#!/usr/bin/env python3
"""Own one Mininet network and controller; never use global kill/cleanup.

sudo python3 -m tests.lab_harness --repetitions 3 --output-dir /tmp/phase1-live
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

from config.lab import AGENCY_HOSTS, SWITCHES, managed_ports
from performance.run_tests import (ACCEPTANCE, MeasurementError, run_all_tests,
                                   save_csv, save_json, stop_process)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RYU = ROOT.parent / "ryu/ryu-env/bin/python"


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=15)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def owned_interfaces():
    return set(managed_ports()) | {h + "-eth0" for h in AGENCY_HOSTS}


def interfaces():
    return {i["ifname"].split("@")[0] for i in json.loads(command("ip", "-j", "link", "show"))}


def preflight(ryu_python):
    if os.geteuid() != 0:
        raise RuntimeError("Live checks require root. Run sudo python3 -m tests.lab_harness; no network was started")
    if not Path(ryu_python).is_file():
        raise RuntimeError("Ryu interpreter missing: " + str(ryu_python))
    bridges = set(command("ovs-vsctl", "--timeout=5", "list-br").split())
    if bridges.intersection(SWITCHES):
        raise RuntimeError("Lab bridges already exist; refusing to disturb an existing network")
    if owned_interfaces().intersection(interfaces()):
        raise RuntimeError("Lab interfaces already exist; inspect them before retrying")


def qos(action):
    return command(sys.executable, str(ROOT / "qos/manage.py"), action)


def snapshot(directory, label):
    data = {}
    for switch in SWITCHES:
        for what in ("dump-flows", "queue-stats"):
            data[switch + "_" + what] = command("ovs-ofctl", "-O", "OpenFlow13", what, switch)
        data[switch + "_fail_mode"] = command("ovs-vsctl", "get-fail-mode", switch)
    for port in managed_ports():
        data[port + "_qdisc"] = command("tc", "-s", "qdisc", "show", "dev", port)
        data[port + "_classes"] = command("tc", "-s", "class", "show", "dev", port)
    (directory / (label + ".json")).write_text(json.dumps(data, indent=2))
    return data


def queue_bytes(output):
    """Require actual byte counters, including both managed classes."""
    values = {int(q): int(n) for q, n in re.findall(r"queue\s+(\d+):\s+bytes=(\d+)", output)}
    require({0, 1}.issubset(values), "Missing queue counters on shared egress")
    return values


def measured_workload(net, mode, directory):
    shared = AGENCY_HOSTS["h4"]
    before = command("ovs-ofctl", "-O", "OpenFlow13", "queue-stats", shared["switch"], str(shared["port"]))
    (directory / "shared_queues_before.txt").write_text(before)
    try:
        result = run_all_tests(mode, {h.name: h for h in net.hosts})
    except MeasurementError as exc:
        if exc.result is not None:
            save_json(exc.result, directory / ("workload_" + mode + ".json"))
        raise
    # Preserve completed measurements before queue readback, which can fail.
    # This is raw workload evidence; only the enclosing harness grants PASS.
    save_json(result, directory / ("workload_" + mode + ".json"))
    after = command("ovs-ofctl", "-O", "OpenFlow13", "queue-stats", shared["switch"], str(shared["port"]))
    (directory / "shared_queues_after.txt").write_text(after)
    first, last = queue_bytes(before), queue_bytes(after)
    for key in ((0, 1) if mode == "qos" else (0,)):
        require(last[key] > first[key], "No measured traffic on shared queue %d" % key)
    return result


@contextmanager
def lab(ryu_python, directory, mode="baseline"):
    preflight(ryu_python)
    from mininet.net import Mininet
    from mininet.node import RemoteController, OVSSwitch
    from topology.disaster_topo import DisasterResponseTopo
    # Reserve a controller port; no existing controller is killed or reused.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    net, ctrl, qos_attempted = None, None, False
    with (directory / "controller.log").open("w") as logfile:
        try:
            ctrl = subprocess.Popen(
                [str(ryu_python), "-m", "ryu.cmd.manager", "--ofp-listen-host", "127.0.0.1",
                 "--ofp-tcp-listen-port", str(port), str(ROOT / "controller/flow_manager_app.py")],
                env=dict(os.environ, DISASTER_QOS="1" if mode == "qos" else "0"),
                cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 15
            while True:
                if ctrl.poll() is not None:
                    raise RuntimeError("Controller exited; see controller.log")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Controller startup timed out")
                    time.sleep(.1)
            # build=False lets finally stop a partially constructed owned network.
            net = Mininet(topo=None, build=False, controller=None, switch=OVSSwitch,
                          autoStaticArp=False)
            net.topo = DisasterResponseTopo()
            net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=port)
            net.build()
            net.start()
            require(net.waitConnected(timeout=15), "Switch controller connection timed out")
            snapshot(directory, "tc_before_ovs_qos")
            # Give OVS exclusive switch-egress shaping in BOTH comparison modes.
            # TCLink host-egress netem persists; switch root netem may be replaced.
            qos_attempted = True
            (directory / "qos_setup.log").write_text(qos("setup" if mode == "qos" else "baseline"))
            time.sleep(1)
            snapshot(directory, "started")
            yield net
            require(ctrl.poll() is None, "Controller exited during checks")
            logfile.flush()
            logs = (directory / "controller.log").read_text()
            require("OPENFLOW_ERROR" not in logs and "Traceback" not in logs,
                    "Controller reported an OpenFlow/application error; see controller.log")
        finally:
            errors = []
            if qos_attempted:
                try:
                    (directory / "qos_teardown.log").write_text(qos("teardown"))
                except Exception as exc:
                    errors.append("QoS teardown: " + str(exc))
            if net is not None:
                try:
                    net.stop()
                except Exception as exc:
                    errors.append("net.stop: " + str(exc))
            if ctrl is not None:
                stop_process(ctrl)
            bridges = set(command("ovs-vsctl", "--timeout=5", "list-br").split())
            if bridges.intersection(SWITCHES):
                errors.append("Owned bridges remain after cleanup")
            if interfaces().intersection(owned_interfaces()):
                errors.append("Owned interfaces remain after cleanup")
            for table in ("QoS", "Queue"):
                rows = command("ovs-vsctl", "--timeout=5", "--columns=_uuid", "find", table,
                               "external_ids:owner=sdn-disaster-phase1")
                if rows.strip():
                    errors.append("Owned " + table + " rows remain")
            (directory / "cleanup.json").write_text(json.dumps({"errors": errors}, indent=2))
            if errors:
                raise RuntimeError("; ".join(errors))


def permitted_checks(hosts, directory):
    raw = {}
    count = ACCEPTANCE["permitted_probes"]
    for src in AGENCY_HOSTS:
        for dst in AGENCY_HOSTS:
            if src == dst:
                continue
            target = AGENCY_HOSTS[dst]["ip"]
            out, err, code = hosts[src].pexec(
                ["ping", "-n", "-q", "-c", str(count),
                 "-i", ".01", "-W", "1", target])
            raw[src + "_" + dst] = out + err
            # Persist before the assertion so a failed matrix is inspectable.
            (directory / "permitted_probes.json").write_text(json.dumps(raw, indent=2))
            summary = r"\b%d packets transmitted, %d (?:packets )?received\b" % (count, count)
            require(code == 0 and re.search(summary, out) and "dup" not in out.lower(),
                    "Permitted probe failure or duplicate: " + src + "->" + dst)


def require_denied_probe(output, code):
    count = ACCEPTANCE["forbidden_attempts"]
    summary = r"\b%d packets transmitted, 0 (?:packets )?received\b" % count
    require(code == 1 and re.search(summary, output) and "errors" not in output.lower(),
            "Forbidden probe must complete without replies or local delivery errors")


def replace_host_ipv4(host, interface, address):
    # Adding a secondary and then deleting its primary can delete BOTH on Linux
    # (promote_secondaries=0). Remove the old address before adding the new one.
    commands = (["ip", "-4", "addr", "flush", "dev", interface],
                ["ip", "addr", "add", address + "/24", "dev", interface],
                ["ip", "-j", "-4", "addr", "show", "dev", interface])
    for args in commands:
        out, err, code = host.pexec(args)
        require(code == 0, "%s: %s failed: %s%s" % (host.name, args, out, err))
    observed = json.loads(out)
    addresses = [(a.get("local"), a.get("prefixlen")) for link in observed for a in link.get("addr_info", [])]
    require(addresses == [(address, 24)],
            "%s: requested IPv4 address %s/24 missing or unexpected addresses present: %s" %
            (host.name, address, out))
    return observed


def live_checks(net, directory):
    hosts = {h.name: h for h in net.hosts}
    # Warm actual proxy ARP in both directions; autoStaticArp must remain off.
    require(net.pingAll(timeout="2") == 0, "Warm-up connectivity failed")
    permitted_checks(hosts, directory)
    # Capture at the receiver. A spoofed ping can fail just because replies go
    # to the real identity, even if a bad forwarding rule delivered the request.
    capture_path = directory.resolve() / "admission.pcap"
    capture = hosts["h4"].popen(
        ["tcpdump", "-U", "-i", "h4-eth0", "-w", str(capture_path),
         "icmp and src %s and dst %s and icmp[0] = 8" % (AGENCY_HOSTS["h2"]["ip"], AGENCY_HOSTS["h4"]["ip"])],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    # Change h7's source IP, then its full MAC/IP identity, after rule warm-up.
    host = hosts["h7"]
    original, spoof = AGENCY_HOSTS["h7"]["ip"], AGENCY_HOSTS["h2"]["ip"]
    target = AGENCY_HOSTS["h4"]
    try:
        deadline = time.monotonic() + 5
        while not capture_path.exists() or capture_path.stat().st_size < 24:
            require(capture.poll() is None, "Receiver capture failed to start")
            require(time.monotonic() < deadline, "Receiver capture startup timed out")
            time.sleep(.05)
        out, err, code = hosts["h2"].pexec(["ping", "-n", "-c", "3", "-W", "1", target["ip"]])
        require(code == 0 and "3 received" in out, "Receiver capture positive control failed")
        addresses = replace_host_ipv4(host, "h7-eth0", spoof)
        (directory / "spoof_address.json").write_text(json.dumps(addresses, indent=2))
        # Avoid counting a local ARP failure alone as proof that IP was rejected.
        out, err, code = host.pexec(["ip", "neigh", "replace", target["ip"], "lladdr", target["mac"],
                                   "nud", "permanent", "dev", "h7-eth0"])
        require(code == 0, "Cannot install spoof test neighbour")
        out, err, code = host.pexec(["ping", "-n", "-I", spoof, "-c", str(ACCEPTANCE["forbidden_attempts"]),
                                   "-i", ".01", "-W", "1", target["ip"]])
        (directory / "forbidden_ip_spoof.txt").write_text(out + err)
        require_denied_probe(out, code)
        out, err, code = host.pexec(["ip", "link", "set", "dev", "h7-eth0", "address", AGENCY_HOSTS["h2"]["mac"]])
        require(code == 0, "Cannot set full identity for negative test")
        # Changing a MAC can invalidate neighbours. Re-pin after the change so
        # failure to resolve ARP cannot masquerade as IPv4 admission rejection.
        out, err, code = host.pexec(["ip", "neigh", "replace", target["ip"], "lladdr", target["mac"],
                                   "nud", "permanent", "dev", "h7-eth0"])
        require(code == 0, "Cannot install full-identity spoof test neighbour")
        out, err, code = host.pexec(["ping", "-n", "-I", spoof, "-c", str(ACCEPTANCE["forbidden_attempts"]),
                                   "-i", ".01", "-W", "1", target["ip"]])
        (directory / "forbidden_mac_ip_spoof.txt").write_text(out + err)
        require_denied_probe(out, code)
    finally:
        stop_process(capture)
        host.pexec(["ip", "link", "set", "dev", "h7-eth0", "address", AGENCY_HOSTS["h7"]["mac"]])
        replace_host_ipv4(host, "h7-eth0", original)
        host.pexec(["ip", "neigh", "flush", "dev", "h7-eth0"])
    packets, err, code = hosts["h4"].pexec(["tcpdump", "-nn", "-r", str(capture_path)])
    (directory / "admission_capture.txt").write_text(packets + err)
    require(code == 0 and len(packets.strip().splitlines()) == 3,
            "Receiver capture must contain exactly the three allowed control requests, with no spoof or duplicate delivery")
    require(net.ping([hosts["h7"], hosts["h4"]], timeout="2") == 0, "Legitimate recovery failed")
    for switch in SWITCHES:
        require(command("ovs-vsctl", "get-fail-mode", switch).strip() == "secure", "Fail-mode mismatch")
    snapshot(directory, "checked")


def new_run(output_dir, label):
    directory = Path(output_dir) / (label + "_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "manifest.json").write_text(json.dumps(
        {"started": datetime.now(timezone.utc).isoformat(), "acceptance": ACCEPTANCE,
         "scope": "Phase 1 legacy chain; static endpoint admission; no adaptive profile",
         "shaping": "OVS switch egress in both modes; TCLink host egress; raw qdisc/class snapshots retained"}, indent=2))
    return directory


def collect_performance(mode, ryu_python, output_dir):
    directory = new_run(output_dir, mode)
    try:
        with lab(ryu_python, directory, mode) as net:
            require(net.pingAll(timeout="2") == 0, "Measurement warm-up failed")
            result = measured_workload(net, mode, directory)
            snapshot(directory, "after_traffic")
            save_json(result, directory / ("perf_" + mode + ".json"))
            save_csv(result, directory / ("perf_" + mode + ".csv"))
    except Exception as exc:
        (directory / "failure.txt").write_text(str(exc))
        raise
    print("Measured evidence: " + str(directory))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ryu-python", default=str(DEFAULT_RYU))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "monitoring/evidence/phase1")
    parser.add_argument("--repetitions", type=int, default=ACCEPTANCE["repetitions"])
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    for i in range(args.repetitions):
        directory = new_run(args.output_dir, "foundation")
        try:
            with lab(args.ryu_python, directory, "qos") as net:
                live_checks(net, directory)
                result = measured_workload(net, "qos", directory)
                save_json(result, directory / "perf_qos.json")
                save_csv(result, directory / "perf_qos.csv")
                snapshot(directory, "after_traffic")
            (directory / "PASS").write_text("All implemented Phase 1 live checks passed\n")
        except Exception as exc:
            (directory / "failure.txt").write_text(str(exc))
            raise
        print("Passed repetition %d: %s" % (i + 1, directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
