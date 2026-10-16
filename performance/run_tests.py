#!/usr/bin/env python3
"""Concurrent Phase 1 measurements through real Mininet Host objects.

CLI owns a scoped lab (requires root); --manual prints commands without a lab.
This verifies the legacy two-class workload, not adaptive-policy performance.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.lab import AGENCY_HOSTS, egress
from config.lab import TRAFFIC_PORTS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = json.loads((PROJECT_ROOT / "config/acceptance.json").read_text())
IPERF_DURATION = ACCEPTANCE["performance_duration_sec"]
IPERF_BANDWIDTH = ACCEPTANCE["offered_rate_per_flow"]
PING_COUNT = ACCEPTANCE["ping_count_under_load"]
FLOWS = {
    "high_priority": {"name": "Hospital_to_Ambulance", "server_host": "h4",
                      "client_host": "h2", "server_ip": AGENCY_HOSTS["h4"]["ip"],
                      "port": TRAFFIC_PORTS["ambulance_data"]},
    "low_priority": {"name": "PublicHelpline_to_Ambulance", "server_host": "h4",
                     "client_host": "h7", "server_ip": AGENCY_HOSTS["h4"]["ip"],
                     "port": TRAFFIC_PORTS["public_helpline"]},
}


class MeasurementError(RuntimeError):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result

    def __str__(self):
        return json.dumps(self.result) if self.result is not None else super().__str__()


def parse_iperf_throughput_mbps(output):
    # Last complete interval, not the first partial interval.
    matches = re.findall(r"([\d.]+)\s+([KMG]?)bits/sec", output)
    if not matches:
        return None
    value, unit = matches[-1]
    result = float(value) * {"": 1e-6, "K": 1e-3, "M": 1, "G": 1000}[unit]
    return result if math.isfinite(result) and result >= 0 else None


def parse_ping(output):
    counts = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", output)
    samples = [float(s) for s in re.findall(r"time[=<]([\d.]+)\s*ms", output)]
    if counts is None or int(counts[1]) == 0 or not samples:
        raise MeasurementError("Missing ping counts or RTT samples")
    sent, received = map(int, counts.groups())
    if received != len(samples) or not 0 < received <= sent:
        raise MeasurementError("Incomplete/inconsistent ping output")
    values = sorted(samples)
    return {"latency_avg_ms": sum(values) / len(values),
            "latency_p95_ms": values[math.ceil(.95 * len(values)) - 1],
            "packets_sent": sent, "packets_received": received,
            "delivery_fraction": received / sent}


def parse_ping_avg_ms(output):
    return parse_ping(output)["latency_avg_ms"]


def finish(process, timeout, evidence, key):
    out, err = process.communicate(timeout=timeout)
    raw = (out or "") + (err or "")
    # Retain output even when the command fails, before raising.
    evidence[key] = raw
    if process.returncode:
        raise MeasurementError("Command failed (%s): %s" % (process.returncode, raw))
    return raw


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=3)


def run_all_tests(mode, hosts=None, duration=IPERF_DURATION):
    if mode not in ("baseline", "qos"):
        raise ValueError("Unsupported measurement mode")
    if hosts is None:
        raise MeasurementError("Real Mininet Host objects required; named namespaces are not assumed")
    if duration < 10:
        raise ValueError("Duration must be at least 10 seconds for concurrent probes")
    # Both flows converge on the 50 Mbps s2->h4 egress. Each offers 35 Mbps.
    shared = ("s2", egress("s2", AGENCY_HOSTS["h4"]))
    for flow in FLOWS.values():
        if flow["server_host"] != "h4" or AGENCY_HOSTS[flow["client_host"]]["switch"] != "s1":
            raise MeasurementError("Workload no longer shares the declared bottleneck")
    processes, servers, clients, probes, raw = [], {}, {}, {}, {}
    result = {"mode": mode, "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "running", "synthetic": False,
              "iperf_duration_sec": duration, "iperf_bandwidth_requested": IPERF_BANDWIDTH,
              "shared_egress": "%s-eth%d" % shared, "ping_count": PING_COUNT, "flows": {}, "raw": raw}
    try:
        for key, f in FLOWS.items():
            # Foreground servers: keep handles; never pkill or daemonise.
            p = hosts[f["server_host"]].popen(
                ["iperf", "-s", "-p", str(f["port"])], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            servers[key] = p
            processes.append((key + "_server", p))
        # Poll actual listening sockets in each host, with a bounded startup.
        deadline = time.monotonic() + 5
        for key, f in FLOWS.items():
            while True:
                if servers[key].poll() is not None:
                    raise MeasurementError("iperf server exited during startup")
                code = hosts[f["server_host"]].pexec(
                    ["ss", "-H", "-ltn", "sport", "=", ":%d" % f["port"]])
                raw[key + "_listener"] = code[0] + code[1]
                if code[2] != 0:
                    raise MeasurementError("Cannot inspect iperf listener: " + code[1])
                if code[0].strip():
                    break
                if time.monotonic() > deadline:
                    raise MeasurementError("iperf listener startup timed out")
                time.sleep(.05)
        for key, f in FLOWS.items():
            p = hosts[f["client_host"]].popen(
                ["iperf", "-c", f["server_ip"], "-p", str(f["port"]),
                 "-t", str(duration), "-b", IPERF_BANDWIDTH, "-f", "m"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            clients[key] = p
            processes.append((key + "_iperf", p))
        time.sleep(1)
        for key, f in FLOWS.items():
            if clients[key].poll() is not None:
                raise MeasurementError("Competing workload exited before measurement")
            p = hosts[f["client_host"]].popen(
                ["ping", "-n", "-c", str(PING_COUNT), "-i", ".1", "-W", "1", f["server_ip"]],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            probes[key] = p
            processes.append((key + "_ping", p))
        # Collect every probe before waiting for any client. Both competing
        # senders must still be running through the entire probe window.
        for key in probes:
            finish(probes[key], duration + 5, raw, key + "_ping")
            if any(p.poll() is not None for p in clients.values()):
                raise MeasurementError("Competing workload exited during probes")
        for key, f in FLOWS.items():
            ping = raw[key + "_ping"]
            perf = finish(clients[key], duration + 10, raw, key + "_iperf")
            throughput = parse_iperf_throughput_mbps(perf)
            if throughput is None or throughput <= 0:
                raise MeasurementError("Missing/zero throughput: " + key)
            result["flows"][key] = dict(label=f["name"], throughput_mbps=throughput,
                                        iperf_raw=perf, ping_raw=ping, **parse_ping(ping))
        result["status"] = "complete"
        return result
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        # Callers preserve this evidence, but cannot save it as successful CSV.
        raise MeasurementError(str(exc), result=result) from exc
    finally:
        cleanup_errors = []
        for key, p in reversed(processes):
            try:
                stop_process(p)
                if key not in raw:
                    out, err = p.communicate(timeout=3)
                    raw[key] = (out or "") + (err or "")
            except Exception as exc:
                cleanup_errors.append(key + ": " + str(exc))
        if cleanup_errors:
            result.update(status="failed", cleanup_errors=cleanup_errors)
            if sys.exc_info()[0] is None:
                raise MeasurementError("Workload cleanup failed", result=result)


def validate_results(results):
    if results.get("status") != "complete" or set(results["flows"]) != set(FLOWS):
        raise MeasurementError("Refusing to save an incomplete measurement")
    for data in results["flows"].values():
        for field in ("throughput_mbps", "latency_avg_ms", "latency_p95_ms", "delivery_fraction"):
            value = data.get(field)
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise MeasurementError("Invalid measurement: " + field)
        if data["throughput_mbps"] <= 0 or not 0 < data["delivery_fraction"] <= 1:
            raise MeasurementError("Invalid throughput/delivery")


def save_csv(results, path):
    validate_results(results)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["mode", "flow_class", "flow_name", "throughput_mbps", "latency_avg_ms",
              "latency_p95_ms", "delivery_fraction", "timestamp"]
    # Refuse to overwrite evidence from a previous run.
    with path.open("x", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for key, data in results["flows"].items():
            writer.writerow(dict(mode=results["mode"], flow_class=key, flow_name=data["label"],
                                 timestamp=results["timestamp"],
                                 **{k: data[k] for k in fields[3:7]}))


def save_json(results, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as fh:
        json.dump(results, fh, indent=2)


def print_manual_commands():
    print("Both streams share s2-eth4 (50 Mbps). Start servers, then clients in background.")
    for f in FLOWS.values():
        print("mininet> %s iperf -s -p %s &" % (f["server_host"], f["port"]))
    for f in FLOWS.values():
        print("mininet> %s iperf -c %s -p %s -t %s -b %s &" %
              (f["client_host"], f["server_ip"], f["port"], IPERF_DURATION, IPERF_BANDWIDTH))
    print("While both clients run: mininet> h2 ping -c 40 -i .1 10.0.0.4")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["baseline", "qos"])
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "performance/results")
    parser.add_argument("--ryu-python", default=str(PROJECT_ROOT.parent / "ryu/ryu-env/bin/python"))
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    if args.manual:
        print_manual_commands()
        return 0
    if args.mode is None:
        parser.error("--mode is required unless --manual is used")
    from tests.lab_harness import collect_performance
    collect_performance(args.mode, args.ryu_python, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
