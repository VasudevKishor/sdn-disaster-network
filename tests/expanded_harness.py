"""Owned 20-switch Phase 2 acceptance; root required, no global cleanup.

sudo python3 -m tests.expanded_harness --repetitions 3
Use --districts 1 for the 10-switch mechanism subset; it cannot certify full size.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

from config.phase2 import load_inventory
from monitoring.pcap_checks import capture_counts
from performance.run_tests import stop_process, parse_iperf_throughput_mbps, parse_ping
from tests.lab_harness import ROOT, DEFAULT_RYU, command, require

ACCEPTANCE = json.loads((ROOT / "config/expanded_acceptance.json").read_text())


def interface_names(inventory):
    return {"%s-eth%d" % (s, p) for s in inventory.SWITCHES for p in inventory.expected_ports(s)} | {
        "%s-eth%d" % (h, a.host_port) for h, e in inventory.ENDPOINTS.items() for a in e.attachments}


def live_interfaces():
    return {i["ifname"].split("@")[0] for i in json.loads(command("ip", "-j", "link", "show"))}


def preflight(inventory, ryu_python):
    require(os.geteuid() == 0, "Root required; no lab started. Use sudo python3 -m tests.expanded_harness --repetitions 3")
    require(Path(ryu_python).is_file(), "Pinned Ryu interpreter missing")
    inventory.validate_inventory()
    require(not set(command("ovs-vsctl", "--timeout=5", "list-br").split()).intersection(inventory.SWITCHES),
            "Expanded bridge already exists; refusing to disturb it")
    require(not live_interfaces().intersection(interface_names(inventory)), "Expanded interface already exists")


def host_command(host, args):
    out, err, code = host.pexec(args)
    require(code == 0, "%s %s failed: %s%s" % (host.name, args, out, err))
    return out


@contextmanager
def lab(inventory, ryu_python, directory, controller_app="expanded_app.py", environment=None):
    preflight(inventory, ryu_python)
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController
    from topology.phase2_topo import Phase2Topo
    net, controller = None, None
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    with (directory / "controller.log").open("w") as log:
        try:
            controller = subprocess.Popen(
                [str(ryu_python), "-m", "ryu.cmd.manager", "--ofp-listen-host", "127.0.0.1",
                 "--ofp-tcp-listen-port", str(port), str(ROOT / "controller" / controller_app)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, SDN_PHASE2_DISTRICTS=str(inventory.districts), **(environment or {})))
            deadline = time.monotonic() + 15
            while True:
                require(controller.poll() is None, "Expanded controller exited; see controller.log")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    require(time.monotonic() < deadline, "Expanded controller startup timed out")
                    time.sleep(.1)
            net = Mininet(topo=None, build=False, switch=OVSSwitch, controller=None, autoStaticArp=False)
            net.topo = Phase2Topo(inventory_name="expanded", districts=inventory.districts)
            net.addController("ec0", controller=RemoteController, ip="127.0.0.1", port=port)
            net.build()
            net.start()
            require(net.waitConnected(timeout=ACCEPTANCE["controller_startup_sec"]), "Expanded switch connection timeout")
            yield net, controller
            require(controller.poll() is None, "Expanded controller exited during checks")
        finally:
            errors = []
            if net is not None:
                try:
                    net.stop()
                except Exception as exc:
                    errors.append("net.stop: " + str(exc))
            if controller is not None:
                stop_process(controller)
            try:
                if set(command("ovs-vsctl", "--timeout=5", "list-br").split()).intersection(inventory.SWITCHES):
                    errors.append("Owned bridges remain")
                if live_interfaces().intersection(interface_names(inventory)):
                    errors.append("Owned interfaces remain")
            except Exception as exc:
                errors.append("cleanup readback: " + str(exc))
            (directory / "cleanup.json").write_text(json.dumps({"errors": errors}, indent=2))
            require(not errors, "; ".join(errors))


def read_health(path, process):
    require(process.poll() is None, "Expanded health process exited: " + str(path))
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if "monotonic" in data and not 0 <= time.monotonic() - data["monotonic"] < 1:
        return None
    return data


def wait_health(processes, timeout=15, expected=None):
    deadline = time.monotonic() + timeout
    while True:
        ready = True
        for name, process, status in processes:
            data = read_health(status, process)
            if data is None:
                ready = False
            elif "paths" in data:
                ready &= data["available"] and all(p["healthy"] for p in data["paths"].values())
                if expected and name in expected:
                    ready &= data["active"] == expected[name]
            else:
                ready &= data.get("ready", False)
        if ready:
            return
        require(time.monotonic() < deadline, "Expanded health paths not ready; inspect health logs")
        time.sleep(.05)


@contextmanager
def health_processes(net, inventory, directory):
    processes, logs = [], []
    completed = False
    try:
        roles = [("reflect", name) for name in sorted(set(inventory.HEALTH_TARGETS.values()))]
        roles += [("monitor", name) for name in sorted(inventory.HEALTH_TARGETS)]
        for mode, name in roles:
            status = directory.resolve() / (name + "_health.json")
            log = (directory / (name + "_health.log")).open("w")
            logs.append(log)
            process = net[name].popen([sys.executable, "-m", "monitoring.path_monitor", mode,
                                     "--inventory", "expanded", "--districts", str(inventory.districts),
                                     "--endpoint", name, "--status-file", str(status)],
                                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            processes.append((name, process, status))
        wait_health(processes)
        yield processes
        require(all(p.poll() is None for _, p, _ in processes), "Expanded health process exited during checks")
        completed = True
    except Exception:
        # Preserve live dataplane/ARP evidence before owned teardown. A health
        # startup failure used to retain only process logs, obscuring why
        # reflector replies were backpressured or not reaching the slaves.
        diagnostics = {}
        try:
            snapshot(net, inventory, directory, "health_failure")
        except Exception as exc:
            diagnostics["snapshot_error"] = str(exc)
        for name in sorted(set(inventory.HEALTH_TARGETS.values())):
            try:
                diagnostics[name] = {"neighbors": host_command(net[name], ["ip", "-j", "neigh", "show"]),
                                     "routes": host_command(net[name], ["ip", "-j", "route", "show"])}
            except Exception as exc:
                diagnostics[name] = {"error": str(exc)}
        (directory / "health_failure_diagnostics.json").write_text(json.dumps(diagnostics, indent=2))
        raise
    finally:
        errors = []
        for name, process, _ in reversed(processes):
            try:
                stop_process(process)
            except Exception as exc:
                errors.append(name + ": " + str(exc))
        for log in logs:
            log.close()
        (directory / "health_cleanup.json").write_text(json.dumps({"errors": errors,
            "returncodes": {name: p.poll() for name, p, _ in processes}}, indent=2))
        require(not errors and all(p.poll() is not None for _, p, _ in processes), "Health process cleanup failed")
        if completed:
            require(all(p.returncode == 0 for _, p, _ in processes), "Health process did not exit cleanly")


def matrix(net, inventory, directory, label, count=100, parallel=12, strict=True):
    pairs = [(a, b) for a in sorted(inventory.ENDPOINTS) for b in sorted(inventory.ENDPOINTS) if a != b]
    results = []
    for start in range(0, len(pairs), parallel):
        owned = []
        try:
            for source, target in pairs[start:start + parallel]:
                process = net[source].popen(["ping", "-n", "-q", "-c", str(count), "-i", ".02", "-W", ".4",
                                            inventory.ENDPOINTS[target].ip],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                owned.append((source, target, process))
            for source, target, process in owned:
                out, err = process.communicate(timeout=count * .02 + 8)
                allowed = inventory.permitted(source, target)
                expected = count if allowed else 0
                valid = (process.returncode == (0 if allowed else 1)
                         and re.search(r"\b%d packets transmitted, %d (?:packets )?received\b" % (count, expected), out)
                         and not re.search(r"dup|errors", out + err, re.I))
                results.append({"source": source, "target": target, "allowed": allowed,
                                "code": process.returncode, "output": out + err, "passed": bool(valid)})
                (directory / (label + ".json")).write_text(json.dumps(results, indent=2))
                if strict:
                    require(valid, "%s matrix failed: %s -> %s; inspect %s.json" % (label, source, target, label))
        finally:
            for _, _, process in reversed(owned):
                stop_process(process)
    return results


@contextmanager
def captures(net, inventory, directory, names=None, prefix=""):
    processes = []
    try:
        for name in names or inventory.ENDPOINTS:
            endpoint = inventory.ENDPOINTS[name]
            path = directory.resolve() / (prefix + name + ".pcap")
            process = net[name].popen(["tcpdump", "-U", "-n", "-i", net[name].application_interface,
                                      "-w", str(path), "(icmp and icmp[0] = 8 and dst host %s) or arp" % endpoint.ip],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            processes.append((name, path, process))
        deadline = time.monotonic() + 8
        while not all(path.exists() and path.stat().st_size >= 24 for _, path, _ in processes):
            require(all(p.poll() is None for _, _, p in processes), "Matrix receiver capture exited")
            require(time.monotonic() < deadline, "Matrix captures did not start")
            time.sleep(.05)
        yield
    finally:
        errors = []
        for name, _, process in reversed(processes):
            stop_process(process)
            _out, err = process.communicate(timeout=3)
            (directory / (prefix + name + "_capture.log")).write_text(err or "")
            if not re.search(r"\b0 packets dropped by kernel\b", err or ""):
                errors.append("Capture loss/unverified counters: " + name)
        require(not errors, "; ".join(errors))


def audit_captures(inventory, directory, count):
    by_ip = {e.ip: name for name, e in inventory.ENDPOINTS.items()}
    pairs, duplicate = Counter(), 0
    guest = inventory.ENDPOINTS["as3"]
    unknown = guest.ip.rsplit(".", 1)[0] + ".250"
    broadcasts = {}
    for name in inventory.ENDPOINTS:
        echoes, arps = capture_counts(directory / (name + ".pcap"))
        for (source, target, _identifier, _seq), n in echoes.items():
            require(source in by_ip and target in by_ip and by_ip[target] == name, "Unknown or misdelivered captured request")
            pairs[by_ip[source], name] += n
            duplicate += max(0, n - 1)
        broadcasts[name] = arps[guest.ip, unknown]
    report = {"duplicates": duplicate, "unknown_arp_observed": broadcasts, "pairs": []}
    for source in inventory.ENDPOINTS:
        for target in inventory.ENDPOINTS:
            if source == target:
                continue
            expected = count if inventory.permitted(source, target) else 0
            observed = pairs[source, target]
            report["pairs"].append({"source": source, "target": target, "expected": expected, "observed": observed})
    (directory / "receiver_audit.json").write_text(json.dumps(report, indent=2))
    require(duplicate == 0 and all(p["expected"] == p["observed"] for p in report["pairs"]),
            "Receiver matrix mismatch, forbidden delivery or duplicate; inspect receiver_audit.json")
    require(broadcasts["as3"] == 10 and all(n == 0 for name, n in broadcasts.items() if name != "as3"),
            "Unknown ARP did not have exactly ten origin controls and zero remote deliveries")


def snapshot(net, inventory, directory, label):
    data = {"switches": {}, "bonds": {}}
    for name in inventory.SWITCHES:
        flows = command("ovs-ofctl", "-O", "OpenFlow13", "dump-flows", name)
        mode = command("ovs-vsctl", "get-fail-mode", name).strip()
        data["switches"][name] = {"flows": flows, "ports": command("ovs-ofctl", "-O", "OpenFlow13", "dump-ports", name), "fail_mode": mode}
        require(mode == "secure" and not re.search(r"\b(?:FLOOD|NORMAL|ALL)\b", flows), "Unsafe forwarding: " + name)
    for name in inventory.HEALTH_TARGETS:
        data["bonds"][name] = host_command(net[name], ["cat", "/proc/net/bonding/bond0"])
    (directory / (label + ".json")).write_text(json.dumps(data, indent=2))


def spoof_checks(net, inventory, directory):
    identity, original = inventory.ENDPOINTS["ac1"], inventory.ENDPOINTS["as3"]
    host, interface = net["as3"], "as3-eth0"
    gateway = inventory.SITES[identity.site]
    with captures(net, inventory, directory, names=("ah1",), prefix="spoof_"):
        try:
            out, err, code = net["ac1"].pexec(["ping", "-n", "-q", "-c", "3", "-i", ".02", "-W", "1", inventory.ENDPOINTS["ah1"].ip])
            (directory / "spoof_positive_control.txt").write_text(out + err)
            require(code == 0 and "3 packets transmitted, 3 received" in out, "Spoof capture positive control failed")
            host_command(host, ["ip", "-4", "addr", "flush", "dev", interface])
            host_command(host, ["ip", "addr", "add", identity.ip + "/24", "dev", interface])
            host_command(host, ["ip", "route", "replace", "default", "via", gateway.gateway, "dev", interface])
            for label, mac in (("spoof_ip", original.mac), ("spoof_mac_ip", identity.mac)):
                host_command(host, ["ip", "link", "set", "dev", interface, "address", mac])
                host_command(host, ["ip", "neigh", "replace", gateway.gateway, "lladdr", gateway.gateway_mac,
                                    "nud", "permanent", "dev", interface])
                probe_pair(net, inventory, directory, label, "as3", "ah1", False)
        finally:
            host_command(host, ["ip", "link", "set", "dev", interface, "address", original.mac])
            host_command(host, ["ip", "-4", "addr", "flush", "dev", interface])
            host_command(host, ["ip", "addr", "add", original.ip + "/24", "dev", interface])
            host_command(host, ["ip", "route", "replace", "default", "via", inventory.SITES[original.site].gateway, "dev", interface])
            host_command(host, ["ip", "neigh", "flush", "dev", interface])
    echoes, _ = capture_counts(directory / "spoof_ah1.pcap")
    require(sum(echoes.values()) == 3 and all(k[:2] == (identity.ip, inventory.ENDPOINTS["ah1"].ip) and n == 1 for k, n in echoes.items()),
            "Spoof capture includes missing controls, duplicates or leaked requests")


def probe_pair(net, inventory, directory, label, source, target, allowed=True):
    out, err, code = net[source].pexec(["ping", "-n", "-q", "-c", "100", "-i", ".02", "-W", ".4", inventory.ENDPOINTS[target].ip])
    (directory / (label + ".txt")).write_text(out + err)
    require(code == (0 if allowed else 1) and re.search(r"\b100 packets transmitted, %d received\b" % (100 if allowed else 0), out)
            and not re.search(r"dup|errors", out + err, re.I), "Probe failed: " + label)


def await_pair(net, inventory, directory, label, source, target, deadline):
    attempts = []
    while time.monotonic() < deadline:
        out, err, code = net[source].pexec(["ping", "-n", "-q", "-c", "1", "-W", ".2", inventory.ENDPOINTS[target].ip])
        now = time.monotonic()
        attempts.append({"monotonic": now, "code": code, "output": out + err})
        (directory / (label + "_recovery.json")).write_text(json.dumps(attempts, indent=2))
        if code == 0 and "1 received" in out and "dup" not in out.lower() and now <= deadline:
            return now
        time.sleep(.05)
    raise AssertionError("Expanded application recovery exceeded five seconds: " + label)


def restore_switch(net, switch, directory):
    net[switch].start([net["ec0"]])
    uuids = net[switch].controllerUUIDs(update=True)
    connected = net.waitConnected(timeout=30)
    state = {uuid: command("ovs-vsctl", "--timeout=5", "get", "Controller", uuid, "is_connected").strip() for uuid in uuids}
    (directory / (switch + "_reconnection.json")).write_text(json.dumps(state, indent=2))
    require(connected and "true" in state.values(), "Expanded switch failed to reconnect: " + switch)


def failure_checks(net, inventory, directory, processes):
    for site, switches in inventory.SITE_ACCESS.items():
        if len(switches) != 2:
            continue
        switch = switches[0]
        affected = [name for name, location in inventory.LOCATIONS.items() if location == site]
        start = time.monotonic()
        try:
            net[switch].stop(deleteIntfs=False)
            deadline = start + 5
            for name in affected:
                state = json.loads(host_command(net[name], ["ip", "-j", "link", "show", "dev", name + "-eth0"]))
                (directory / (name + "_fault_carrier.json")).write_text(json.dumps(state, indent=2))
                require("LOWER_UP" in state[0]["flags"], "Access fault did not retain carrier")
            while True:
                chosen = [read_health(path, p) for name, p, path in processes if name in affected]
                if len(chosen) == len(affected) and all(d and d["available"] and d["active"].endswith("eth1") for d in chosen):
                    break
                require(time.monotonic() < deadline, "Expanded critical endpoint failover exceeded 5 seconds")
                time.sleep(.05)
            times = {}
            for name in affected:
                target = inventory.HEALTH_TARGETS[name]
                times[name] = await_pair(net, inventory, directory, "access_" + name, name, target, deadline) - start
            (directory / (switch + "_failover.json")).write_text(json.dumps({"recovery_probe_sec": times, "endpoints": chosen}, indent=2))
            for name in affected:
                target = inventory.HEALTH_TARGETS[name]
                probe_pair(net, inventory, directory, "access_down_" + name, name, target)
                probe_pair(net, inventory, directory, "access_down_return_" + name, target, name)
            probe_pair(net, inventory, directory, switch + "_down_guest", "as3", "ah1", False)
        finally:
            restore_switch(net, switch, directory)
        wait_health(processes, timeout=20, expected={name: name + "-eth1" for name in affected})
    start = time.monotonic()
    try:
        net["aag1"].stop(deleteIntfs=False)
        target = "bc1" if inventory.districts == 2 else "af1"
        recovered = await_pair(net, inventory, directory, "aggregation", "ac1", target, start + 5)
        (directory / "aggregation_timing.json").write_text(json.dumps({"recovery_probe_sec": recovered - start}))
        probe_pair(net, inventory, directory, "aggregation_down", "ac1", target)
        probe_pair(net, inventory, directory, "aggregation_down_return", target, "ac1")
    finally:
        restore_switch(net, "aag1", directory)
    wait_health(processes)
    if inventory.districts == 2:
        link = next(l for l in inventory.LINKS if (l.a, l.b) == ("bb1", "bb3"))
        started = time.monotonic()
        try:
            net.configLinkStatus(link.a, link.b, "down")
            recovered = await_pair(net, inventory, directory, "backbone_link", "ac1", "bc1", started + 5)
            (directory / "backbone_timing.json").write_text(json.dumps({"recovery_probe_sec": recovered - started}))
            probe_pair(net, inventory, directory, "backbone_down", "ac1", "bc1")
            probe_pair(net, inventory, directory, "backbone_down_return", "bc1", "ac1")
        finally:
            net.configLinkStatus(link.a, link.b, "up")
        await_pair(net, inventory, directory, "backbone_restored", "ac1", "bc1", time.monotonic() + 5)
        cross = [l for l in inventory.LINKS if l.a.startswith("bb") and l.b.startswith("bb")
                 and (int(l.a[-1]) <= 2) != (int(l.b[-1]) <= 2)]
        try:
            for link in cross:
                net.configLinkStatus(link.a, link.b, "down")
            time.sleep(1)
            probe_pair(net, inventory, directory, "partition_local_a", "ac1", "ah1")
            probe_pair(net, inventory, directory, "partition_local_b", "bc1", "bh1")
            probe_pair(net, inventory, directory, "partition_remote", "ac1", "bc1", False)
        finally:
            for link in cross:
                net.configLinkStatus(link.a, link.b, "up")
        await_pair(net, inventory, directory, "partition_restored", "ac1", "bc1", time.monotonic() + 5)
        probe_pair(net, inventory, directory, "partition_restored", "ac1", "bc1")
    snapshot(net, inventory, directory, "after_faults")


def resource_snapshot():
    fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:9]
    cpu = [int(n) for n in fields]
    memory = re.search(r"^MemAvailable:\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M)
    return {"cpu_total": sum(cpu), "cpu_idle": cpu[3] + cpu[4], "available_memory_mib": int(memory[1]) / 1024}


LOAD_PORT_BASE = 5100


def receiver_progress(net, target, sources):
    progress = {}
    for index, source in enumerate(sources):
        output = host_command(net[target], ["ss", "-H", "-tin", "state", "established",
                                            "sport", "=", ":%d" % (LOAD_PORT_BASE + index)])
        counts = re.findall(r"\bbytes_received:(\d+)\b", output)
        progress[source] = {"bytes_received": sum(map(int, counts)), "connections": len(counts), "raw": output}
    return progress


def wait_load_ready(net, target, clients, directory):
    started, attempts = time.monotonic(), []
    while True:
        require(all(p.poll() is None for _, p in clients), "Load client exited before sending measured traffic")
        progress = receiver_progress(net, target, [name for name, _ in clients])
        elapsed = time.monotonic() - started
        attempts.append({"elapsed_sec": elapsed, "streams": progress})
        (directory / "load_readiness.json").write_text(json.dumps(attempts, indent=2))
        ready = all(p["connections"] == 1 and p["bytes_received"] >= ACCEPTANCE["load_ready_bytes_per_stream"]
                    for p in progress.values())
        require(elapsed <= ACCEPTANCE["load_readiness_sec"], "Load streams did not become ready within five seconds")
        if ready:
            return progress
        time.sleep(.05)


def load_network_snapshot(net, inventory, names):
    owned_interfaces = interface_names(inventory)
    root_qdiscs = json.loads(command("tc", "-s", "-j", "qdisc", "show"))
    data = {"monotonic": time.monotonic(), "switch_qdiscs": [q for q in root_qdiscs if q.get("dev") in owned_interfaces],
            "hosts": {}}
    for name in names:
        data["hosts"][name] = {"qdiscs": json.loads(host_command(net[name], ["tc", "-s", "-j", "qdisc", "show"])),
                               "neighbors": json.loads(host_command(net[name], ["ip", "-j", "neigh", "show"]))}
    return data


def require_load_delivery(measured):
    failed = []
    for source, flow in measured.items():
        if (flow["packets_sent"] != ACCEPTANCE["load_probe_count"]
                or flow["delivery_fraction"] < ACCEPTANCE["load_probe_delivery"]
                or flow["latency_p95_ms"] > ACCEPTANCE["load_probe_p95_ms"]):
            failed.append("%s: %d/%d replies, p95 %.3f ms" %
                          (source, flow["packets_received"], flow["packets_sent"], flow["latency_p95_ms"]))
    require(not failed, "Under-load delivery/RTT resource gate failed: " + "; ".join(failed))


def load_check(net, inventory, directory, processes):
    target = "bf2" if inventory.districts == 2 else "af2"
    sources = ("ac1", "ah1", "bc1", "bh1") if inventory.districts == 2 else ("ac1", "ah1", "ac2", "ah2")
    owned, clients, probes, raw, samples, network = [], [], [], {}, [], []
    try:
        network.append(load_network_snapshot(net, inventory, sources + (target,)))
        wait_health(processes)
        # Fault restoration and neighbor discovery are separate from the
        # concurrent-load measurement. Retain every readiness attempt.
        for source in sources:
            await_pair(net, inventory, directory, "load_startup_" + source, source, target, time.monotonic() + 5)
            await_pair(net, inventory, directory, "load_startup_return_" + source, target, source, time.monotonic() + 5)
        network.append(load_network_snapshot(net, inventory, sources + (target,)))
        for index, source in enumerate(sources):
            port = LOAD_PORT_BASE + index
            server = net[target].popen(["iperf", "-s", "-p", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            owned.append((source + "_server", server))
            deadline = time.monotonic() + 5
            while not host_command(net[target], ["ss", "-H", "-ltn", "sport", "=", ":%d" % port]).strip():
                require(server.poll() is None and time.monotonic() < deadline, "Load server failed")
                time.sleep(.05)
        for index, source in enumerate(sources):
            client = net[source].popen(["iperf", "-c", inventory.ENDPOINTS[target].ip, "-p", str(LOAD_PORT_BASE + index),
                                        "-t", str(ACCEPTANCE["load_duration_sec"]), "-b", ACCEPTANCE["load_rate_per_stream"], "-f", "m"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            clients.append((source, client))
            owned.append((source + "_client", client))
        initial_progress = wait_load_ready(net, target, clients, directory)
        samples.append(resource_snapshot())
        for source in sources:
            probe = net[source].popen(["ping", "-n", "-c", str(ACCEPTANCE["load_probe_count"]), "-i", ".1", "-W", "1",
                                       inventory.ENDPOINTS[target].ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            probes.append((source, probe))
            owned.append((source + "_probe", probe))
        deadline, next_snapshot = time.monotonic() + 12, time.monotonic()
        while any(p.poll() is None for _, p in probes):
            require(all(p.poll() is None for _, p in clients), "Load client stopped during measured probes")
            require(time.monotonic() < deadline, "Under-load probes timed out")
            if time.monotonic() >= next_snapshot:
                samples.append(resource_snapshot())
                network.append(load_network_snapshot(net, inventory, sources + (target,)))
                next_snapshot = time.monotonic() + 1
            time.sleep(.05)
        final_progress = receiver_progress(net, target, sources)
        progress_report = {"before_probes": initial_progress, "after_probes": final_progress}
        (directory / "load_progress.json").write_text(json.dumps(progress_report, indent=2))
        require(all(final_progress[s]["connections"] == 1 and
                    final_progress[s]["bytes_received"] > initial_progress[s]["bytes_received"] for s in sources),
                "Load streams did not deliver data during measured probes")
        measured = {}
        for source, probe in probes:
            out, err = probe.communicate(timeout=12)
            raw[source + "_probe"] = out + err
            require(probe.returncode == 0 and all(p.poll() is None for _, p in clients), "Load/probe overlap failed")
            measured[source] = parse_ping(out)
            samples.append(resource_snapshot())
        for source, client in clients:
            out, err = client.communicate(timeout=25)
            raw[source + "_client"] = out + err
            rate = parse_iperf_throughput_mbps(out)
            require(client.returncode == 0 and rate and rate > 0, "Missing load throughput")
            measured[source]["throughput_mbps"] = rate
        samples.append(resource_snapshot())
        cpu_total = samples[-1]["cpu_total"] - samples[0]["cpu_total"]
        cpu_idle = samples[-1]["cpu_idle"] - samples[0]["cpu_idle"]
        cpu = 100 * (1 - cpu_idle / cpu_total) if cpu_total else 100
        result = {"flows": measured, "resource_samples": samples, "average_system_cpu_percent": cpu,
                  "scope": "Four feasible TCP streams and ICMP resource smoke test; not urgent-message/adaptive measurements"}
        (directory / "resource_load.json").write_text(json.dumps(result, indent=2))
        require(cpu <= ACCEPTANCE["maximum_average_cpu_percent"], "CPU headroom gate exceeded")
        require(min(s["available_memory_mib"] for s in samples) >= ACCEPTANCE["minimum_available_memory_mib"], "Memory headroom gate exceeded")
        require_load_delivery(measured)
        wait_health(processes)
    finally:
        for name, process in reversed(owned):
            stop_process(process)
            out, err = process.communicate(timeout=3)
            raw.setdefault(name, (out or "") + (err or ""))
        (directory / "load_raw.json").write_text(json.dumps(raw, indent=2))
        # Keep source/receiver neighbor state even when startup failed before
        # the first stream/probe or the first periodic sample could begin.
        try:
            network.append(load_network_snapshot(net, inventory, sources + (target,)))
        except Exception as exc:
            network.append({"snapshot_error": str(exc), "monotonic": time.monotonic()})
        (directory / "load_network.json").write_text(json.dumps(network, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", type=int, choices=(1, 2), default=2)
    parser.add_argument("--repetitions", type=int, default=ACCEPTANCE["repetitions"])
    parser.add_argument("--parallel", type=int, default=ACCEPTANCE["matrix_parallel"])
    parser.add_argument("--ryu-python", default=str(DEFAULT_RYU))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "monitoring/evidence/phase2-expanded")
    args = parser.parse_args()
    if args.repetitions < 1 or not 1 <= args.parallel <= 24:
        parser.error("Positive repetitions and parallelism 1..24 required")
    inventory = load_inventory("expanded", args.districts)
    for repetition in range(args.repetitions):
        directory = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(json.dumps({"districts": args.districts,
            "switches": inventory.SWITCHES, "endpoints": {n: {"ip": e.ip, "site": e.site} for n, e in inventory.ENDPOINTS.items()},
            "repetition": repetition + 1, "parallel": args.parallel, "acceptance": ACCEPTANCE,
            "limits": "Phase 2 topology/forwarding only; no service-policy, QoS profiles or adaptive comparison"}, indent=2))
        try:
            with lab(inventory, args.ryu_python, directory) as (net, controller):
                with health_processes(net, inventory, directory) as processes:
                    matrix(net, inventory, directory, "warmup", count=2, parallel=args.parallel, strict=False)
                    with captures(net, inventory, directory):
                        matrix(net, inventory, directory, "matrix", count=ACCEPTANCE["matrix_probes"], parallel=args.parallel)
                        host_command(net["as3"], [sys.executable, "-m", "monitoring.broadcast_probe", "--districts", str(args.districts)])
                        time.sleep(.3)
                    audit_captures(inventory, directory, ACCEPTANCE["matrix_probes"])
                    snapshot(net, inventory, directory, "stable")
                    spoof_checks(net, inventory, directory)
                    failure_checks(net, inventory, directory, processes)
                    load_check(net, inventory, directory, processes)
                    require(controller.poll() is None, "Expanded controller exited")
            log = (directory / "controller.log").read_text()
            require(not any(x in log for x in ("Traceback", "OPENFLOW_ERROR", "PORT_INVENTORY_MISMATCH")), "Expanded controller reported errors")
            (directory / "PASS").write_text("Expanded Phase 2 implemented checks passed; inspect district count and repeat gate\n")
        except Exception as exc:
            (directory / "failure.txt").write_text(str(exc))
            print("Failed: %s; evidence: %s" % (exc, directory), file=sys.stderr)
            return 1
        print("Expanded repetition passed: " + str(directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
