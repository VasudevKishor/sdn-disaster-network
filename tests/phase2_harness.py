"""Scoped live gateway and usable-path bond prototype checks (requires root).

sudo python3 -m tests.phase2_harness --repetitions 3
This does not certify the full 20-switch Phase 2 gate.
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

from config.phase2 import ENDPOINTS, SITES, SWITCHES, expected_ports
from performance.run_tests import stop_process
from tests.lab_harness import DEFAULT_RYU, ROOT, command, require


def interfaces():
    return {i["ifname"].split("@")[0] for i in json.loads(command("ip", "-j", "link", "show"))}


def owned_interfaces():
    return {"%s-eth%d" % (s, p) for s in SWITCHES for p in expected_ports(s)} | {
        "%s-eth%d" % (h, a.host_port) for h, e in ENDPOINTS.items() for a in e.attachments}


def preflight(ryu_python):
    if os.geteuid() != 0:
        raise RuntimeError("Root required; no network started. Run sudo python3 -m tests.phase2_harness --repetitions 3")
    require(Path(ryu_python).is_file(), "Pinned Ryu Python missing")
    require(not set(command("ovs-vsctl", "--timeout=5", "list-br").split()).intersection(SWITCHES),
            "Prototype bridge already exists; refusing to disturb it")
    require(not interfaces().intersection(owned_interfaces()), "Prototype interface already exists; inspect it first")


def host_command(host, args):
    out, err, code = host.pexec(args)
    require(code == 0, "%s: %s failed: %s%s" % (host.name, args, out, err))
    return out


@contextmanager
def path_monitoring(net, directory):
    processes = []
    status = directory.resolve() / "path_status.json"
    reflector_status = directory.resolve() / "reflector_status.json"
    with (directory / "path_monitor.jsonl").open("w") as monitor_log, \
            (directory / "reflector.log").open("w") as reflector_log:
        try:
            for mode, host, destination, log in (("reflect", "p2h2", reflector_status, reflector_log),
                                                ("monitor", "p2h1", status, monitor_log)):
                process = net[host].popen([sys.executable, "-m", "monitoring.path_monitor", mode,
                                          "--endpoint", host, "--status-file", str(destination)],
                                         cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                processes.append(process)
            wait_health(status, processes, both=True, timeout=12)
            yield status, processes
            require(all(p.poll() is None for p in processes), "Path monitor/reflector exited; inspect logs")
        finally:
            errors = []
            for process in reversed(processes):
                try:
                    stop_process(process)
                except Exception as exc:
                    errors.append(str(exc))
            (directory / "path_process_cleanup.json").write_text(json.dumps(
                {"returncodes": [p.poll() for p in processes], "errors": errors}, indent=2))
            require(not errors and all(p.poll() is not None for p in processes), "Owned path process cleanup failed")


def wait_health(path, processes, active=None, both=False, timeout=5):
    deadline = time.monotonic() + timeout
    while True:
        require(all(p.poll() is None for p in processes), "Path monitor/reflector exited; inspect logs")
        if path.exists():
            status = json.loads(path.read_text())
            fresh = 0 <= time.monotonic() - status["monotonic"] < 1
            healthy = status.get("available") and (not both or all(p["healthy"] for p in status["paths"].values()))
            if fresh and healthy and (active is None or status["active"] == active):
                return status
        require(time.monotonic() < deadline, "Usable-path health/failover timed out; inspect path_monitor.jsonl")
        time.sleep(.05)


def wait_application_recovery(net, directory, label, deadline):
    attempts = []
    while time.monotonic() < deadline:
        out, err, code = net["p2h1"].pexec(
            ["ping", "-n", "-c", "1", "-W", ".2", ENDPOINTS["p2h2"].ip])
        observed = time.monotonic()
        attempts.append({"monotonic": observed, "code": code, "output": out + err})
        (directory / (label + "_recovery_probes.json")).write_text(json.dumps(attempts, indent=2))
        if code == 0 and "1 received" in out and "DUP" not in out and observed <= deadline:
            return observed
        time.sleep(.05)
    raise AssertionError("Application recovery exceeded 5 seconds: " + label)


def snapshot(net, directory, label):
    result = {"switches": {}, "hosts": {}}
    for switch in SWITCHES:
        result["switches"][switch] = {"flows": command("ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch),
                                       "ports": command("ovs-ofctl", "-O", "OpenFlow13", "dump-ports", switch),
                                       "fail_mode": command("ovs-vsctl", "get-fail-mode", switch)}
    for host in net.hosts:
        result["hosts"][host.name] = {
            "addresses": host_command(host, ["ip", "-j", "addr", "show"]),
            "routes": host_command(host, ["ip", "-j", "route", "show"])}
    result["bond"] = host_command(net["p2h1"], ["cat", "/proc/net/bonding/bond0"])
    (directory / (label + ".json")).write_text(json.dumps(result, indent=2))
    for values in result["switches"].values():
        require(values["fail_mode"].strip() == "secure", "Switch is not in secure fail-mode")
        require(not re.search(r"\b(?:FLOOD|NORMAL|ALL)\b", values["flows"]), "Uncontrolled forwarding action")
    return result


@contextmanager
def lab(ryu_python, directory):
    preflight(ryu_python)  # Before any network/controller mutation.
    from mininet.net import Mininet
    from mininet.node import RemoteController, OVSSwitch
    from topology.phase2_topo import Phase2Topo
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    net, controller = None, None
    with (directory / "controller.log").open("w") as logfile:
        try:
            controller = subprocess.Popen(
                [str(ryu_python), "-m", "ryu.cmd.manager", "--ofp-listen-host", "127.0.0.1",
                 "--ofp-tcp-listen-port", str(port), str(ROOT / "controller/phase2_app.py")],
                cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 15
            while True:
                require(controller.poll() is None, "Controller exited; see controller.log")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    require(time.monotonic() < deadline, "Controller startup timed out")
                    time.sleep(.1)
            net = Mininet(topo=None, build=False, controller=None, switch=OVSSwitch, autoStaticArp=False)
            net.topo = Phase2Topo()
            net.addController("p2c0", controller=RemoteController, ip="127.0.0.1", port=port)
            net.build()
            net.start()
            require(net.waitConnected(timeout=15), "Controller connections timed out")
            time.sleep(1)
            yield net
            require(controller.poll() is None, "Controller exited during checks")
            logfile.flush()
            logs = (directory / "controller.log").read_text()
            require(not any(marker in logs for marker in ("Traceback", "OPENFLOW_ERROR", "PORT_INVENTORY_MISMATCH")),
                    "Controller errors; see controller.log")
        finally:
            errors = []
            if net is not None:
                try:
                    net.stop()
                except Exception as exc:
                    errors.append("net.stop: " + str(exc))
            if controller is not None:
                stop_process(controller)
            if set(command("ovs-vsctl", "--timeout=5", "list-br").split()).intersection(SWITCHES):
                errors.append("Owned bridges remain")
            if interfaces().intersection(owned_interfaces()):
                errors.append("Owned interfaces remain")
            (directory / "cleanup.json").write_text(json.dumps({"errors": errors}, indent=2))
            require(not errors, "; ".join(errors))


def ping(net, directory, label, source, target, count=100, allowed=True, extra=()):
    out, err, code = net[source].pexec(["ping", "-n", "-c", str(count), "-i", ".02", "-W", "1",
                                      *extra, ENDPOINTS[target].ip])
    (directory / (label + ".txt")).write_text(out + err)
    if allowed:
        require(code == 0 and re.search(r"\b%d received\b" % count, out) and "DUP" not in out,
                "Permitted probes failed or duplicated: " + label)
    else:
        require(code == 1 and re.search(r"\b%d packets transmitted, 0 received\b" % count, out)
                and "errors" not in (out + err).lower(),
                "Forbidden probes incomplete, locally failed or not rejected: " + label)


def admission_checks(net, directory):
    target, original, identity = ENDPOINTS["p2h2"], ENDPOINTS["p2h3"], ENDPOINTS["p2h1"]
    guest = net["p2h3"]
    pcap = directory.resolve() / "receiver.pcap"
    capture = net["p2h2"].popen([
        "tcpdump", "-U", "-i", "p2h2-eth0", "-w", str(pcap),
        "icmp and src %s and dst %s and icmp[0] = 8" % (identity.ip, target.ip)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not pcap.exists() or pcap.stat().st_size < 24:
            require(capture.poll() is None, "Receiver capture failed")
            require(time.monotonic() < deadline, "Receiver capture startup timed out")
            time.sleep(.05)
        ping(net, directory, "capture_positive_control", "p2h1", "p2h2", count=3, extra=("-t", "64"))
        ping(net, directory, "ttl_expired", "p2h1", "p2h2", count=2, allowed=False, extra=("-t", "1"))
        host_command(guest, ["ip", "addr", "flush", "dev", "p2h3-eth0"])
        host_command(guest, ["ip", "addr", "add", identity.ip + "/24", "dev", "p2h3-eth0"])
        gateway = SITES[identity.site]
        host_command(guest, ["ip", "route", "replace", "default", "via", gateway.gateway])
        # Force a valid next-hop neighbour: ARP failure alone is not rejection proof.
        host_command(guest, ["ip", "neigh", "replace", gateway.gateway, "lladdr", gateway.gateway_mac,
                             "nud", "permanent", "dev", "p2h3-eth0"])
        ping(net, directory, "ip_spoof", "p2h3", "p2h2", allowed=False)
        host_command(guest, ["ip", "link", "set", "p2h3-eth0", "address", identity.mac])
        host_command(guest, ["ip", "neigh", "replace", gateway.gateway, "lladdr", gateway.gateway_mac,
                             "nud", "permanent", "dev", "p2h3-eth0"])
        ping(net, directory, "mac_ip_spoof", "p2h3", "p2h2", allowed=False)
    finally:
        stop_process(capture)
        host_command(guest, ["ip", "link", "set", "p2h3-eth0", "address", original.mac])
        host_command(guest, ["ip", "addr", "flush", "dev", "p2h3-eth0"])
        host_command(guest, ["ip", "addr", "add", original.ip + "/24", "dev", "p2h3-eth0"])
        host_command(guest, ["ip", "route", "replace", "default", "via", SITES[original.site].gateway])
        host_command(guest, ["ip", "neigh", "flush", "dev", "p2h3-eth0"])
    out = command("tcpdump", "-nn", "-e", "-vv", "-r", str(pcap))
    (directory / "receiver_capture.txt").write_text(out)
    require(out.count("ICMP echo request") == 3 and out.count("ttl 63") == 3,
            "Capture must show exactly three legitimate requests at TTL 63, no spoofed/expired/duplicate requests")
    require(out.count(SITES[target.site].gateway_mac + " > " + target.mac) == 3,
            "Capture does not show the destination site's gateway-to-endpoint Ethernet rewrite")


def gateway_and_local_checks(net, directory):
    # Warm gateway ARP and both directions before applying the frozen 100-probe gate.
    ping(net, directory, "warmup", "p2h1", "p2h2", count=3)
    ping(net, directory, "forward", "p2h1", "p2h2")
    ping(net, directory, "return", "p2h2", "p2h1")
    for source, target in (("p2h3", "p2h1"), ("p2h3", "p2h2"), ("p2h1", "p2h3"), ("p2h2", "p2h3")):
        ping(net, directory, "isolated_%s_%s" % (source, target), source, target, allowed=False)
    snapshot(net, directory, "before_fault")
    admission_checks(net, directory)
    bond = net["p2h1"]
    report = host_command(bond, ["cat", "/proc/net/bonding/bond0"])
    require("Currently Active Slave: p2h1-eth0" in report, "Initial bond primary is unexpected")
    start = time.monotonic()
    net.configLinkStatus("p2h1", "p2s1", "down")
    try:
        deadline = start + 5
        while True:
            report = host_command(bond, ["cat", "/proc/net/bonding/bond0"])
            if "Currently Active Slave: p2h1-eth1" in report:
                break
            require(time.monotonic() < deadline, "Local-link bond failover exceeded prototype 5-second budget")
            time.sleep(.1)
        bond_switch_time = time.monotonic()
        # Warm after the event, then require full stable connectivity. The warmup
        # is recorded separately and must not be presented as zero interruption.
        ping(net, directory, "failover_warmup", "p2h1", "p2h2", count=3)
        first_recovered = time.monotonic()
        ping(net, directory, "failover_forward", "p2h1", "p2h2")
        ping(net, directory, "failover_return", "p2h2", "p2h1")
        snapshot(net, directory, "during_local_link_fault")
        (directory / "failover_timing.json").write_text(json.dumps({
            "fault_monotonic": start, "bond_observed_sec": bond_switch_time - start,
            "successful_warmup_completed_sec": first_recovered - start,
            "note": "Polling plus completed warmup; not an exact first-recovered-packet timestamp"}, indent=2))
    finally:
        net.configLinkStatus("p2h1", "p2s1", "up")
    time.sleep(.5)
    ping(net, directory, "restored_forward", "p2h1", "p2h2")
    ping(net, directory, "restored_return", "p2h2", "p2h1")
    after = snapshot(net, directory, "restored")
    require("Currently Active Slave: p2h1-eth1" in after["bond"], "Unexpected automatic failback")


def restart_access_switch(net, switch, directory):
    device = net[switch]
    device.start([net["p2c0"]])
    # OVS recreates Controller rows when a bridge is recreated. Mininet caches
    # their old UUIDs, so connected() otherwise keeps querying deleted rows.
    device.controllerUUIDs(update=True)
    connected = net.waitConnected(timeout=15)
    current = device.controllerUUIDs(update=True)
    states = {uuid: {"is_connected": command("ovs-vsctl", "--timeout=5", "get", "Controller", uuid, "is_connected").strip(),
                     "target": command("ovs-vsctl", "--timeout=5", "get", "Controller", uuid, "target").strip()}
              for uuid in current}
    (directory / (switch + "_reconnection.json")).write_text(json.dumps(
        {"switch": switch, "mininet_connected": connected, "current_controllers": states}, indent=2))
    require(connected and any(row["is_connected"] == "true" for row in states.values()),
            "Access switch did not reconnect; inspect %s_reconnection.json" % switch)


def remote_path_checks(net, directory, status_path, processes):
    """Keep carrier while losing an uplink, an access bridge, then forwarding."""
    host = net["p2h1"]
    timings = []
    # The existing local-link check leaves eth1 active with eth0 restored.
    scenarios = (("remote_uplink", "p2s2", "p2h1-eth1", "p2h1-eth0"),
                 ("access_switch", "p2s1", "p2h1-eth0", "p2h1-eth1"),
                 ("blackhole", "p2s2", "p2h1-eth1", "p2h1-eth0"))
    for label, switch, old, new in scenarios:
        wait_health(status_path, processes, active=old, both=True)
        start = time.monotonic()
        try:
            if label == "remote_uplink":
                net.configLinkStatus(switch, "p2s3", "down")
            elif label == "access_switch":
                net[switch].stop(deleteIntfs=False)
            else:
                # Own one temporary drop, leaving port state and carrier up.
                command("ovs-ofctl", "-O", "OpenFlow13", "add-flow", switch,
                        "cookie=0xd15200000000ff01,priority=65500,in_port=1,ip,actions=drop")
            link = json.loads(host_command(host, ["ip", "-j", "link", "show", "dev", old]))
            (directory / (label + "_carrier.json")).write_text(json.dumps(link, indent=2))
            require("LOWER_UP" in link[0]["flags"], "Fault did not retain endpoint carrier: " + label)
            health = wait_health(status_path, processes, active=new,
                                 timeout=max(.01, start + 5 - time.monotonic()))
            observed = time.monotonic()
            require(observed - start <= 5, "Usable-path failover exceeded 5 seconds: " + label)
            report = host_command(host, ["cat", "/proc/net/bonding/bond0"])
            require("Currently Active Slave: " + new in report, "Monitor/bond active-slave mismatch")
            recovered = wait_application_recovery(net, directory, label, start + 5)
            ping(net, directory, label + "_forward", "p2h1", "p2h2")
            ping(net, directory, label + "_return", "p2h2", "p2h1")
            # The unrelated guest remains isolated throughout the fault.
            ping(net, directory, label + "_isolation", "p2h3", "p2h2", count=3, allowed=False)
            if label != "access_switch":
                snapshot(net, directory, label + "_during_fault")
            timings.append({"fault": label, "injected_monotonic": start,
                            "healthy_backup_observed_sec": observed - start,
                            "successful_recovery_probe_sec": recovered - start,
                            "stable_probe_completed_sec": time.monotonic() - start,
                            "active": health["active"], "carrier_retained": True})
        finally:
            if label == "remote_uplink":
                net.configLinkStatus(switch, "p2s3", "up")
            elif label == "access_switch":
                restart_access_switch(net, switch, directory)
            else:
                command("ovs-ofctl", "-O", "OpenFlow13", "del-flows", switch,
                        "cookie=0xd15200000000ff01/0xffffffffffffffff")
        wait_health(status_path, processes, active=new, both=True, timeout=10)
        ping(net, directory, label + "_restored_forward", "p2h1", "p2h2")
        ping(net, directory, label + "_restored_return", "p2h2", "p2h1")
        snapshot(net, directory, label + "_restored")
        (directory / "remote_path_timing.json").write_text(json.dumps({
            "events": timings, "note": "Polling and completed stable probes; not exact application outage timestamps"}, indent=2))


def live_checks(net, directory):
    with path_monitoring(net, directory) as (status, processes):
        gateway_and_local_checks(net, directory)
        remote_path_checks(net, directory, status, processes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ryu-python", default=str(DEFAULT_RYU))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "monitoring/evidence/phase2-live")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    for repetition in range(args.repetitions):
        directory = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "manifest.json").write_text(json.dumps({
            "scope": "Phase 2 small prototype: gateways, isolation, local-link and usable-path bond failover",
            "permitted_probes": 100, "forbidden_attempts": 100, "repetition": repetition + 1,
            "inventory": {h: {"ip": e.ip, "site": e.site} for h, e in ENDPOINTS.items()},
            "limits": "No full mesh, full-topology load gate, service policy or QoS allocation; one remote health target"}, indent=2))
        try:
            with lab(args.ryu_python, directory) as net:
                live_checks(net, directory)
            (directory / "PASS").write_text("Implemented prototype live checks passed; full Phase 2 gate remains separate\n")
        except Exception as exc:
            (directory / "failure.txt").write_text(str(exc))
            print("Failed: %s; evidence: %s" % (exc, directory), file=sys.stderr)
            return 1
        print("Prototype repetition passed: " + str(directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
