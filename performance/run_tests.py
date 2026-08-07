#!/usr/bin/env python3
"""
run_tests.py - Performance measurement: throughput + latency with/without QoS

Runs iperf (throughput) and ping (latency) for:
  - High-priority flow: Hospital h2 -> Ambulance h4 (TCP/5001)
  - Low-priority flow:  PublicHelpline h7 -> CommandCenter h1 (TCP/8080)

Two modes:
  --mode baseline   (QoS disabled: DISASTER_QOS=0, qos_teardown.sh)
  --mode qos          (QoS enabled:  DISASTER_QOS=1, qos_setup.sh)

Results saved as CSV in performance/results/

NOTE: This script generates commands and can run them if Mininet host
namespaces (h1, h2, ...) are available. For the review demo, you may also
run the printed Mininet CLI commands manually while the network is up.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Test parameters (consistent across baseline and QoS runs)
IPERF_DURATION = 10          # seconds
IPERF_BANDWIDTH = "25M"      # requested per flow (contention on shared link)
PING_COUNT = 20

FLOWS = {
    "high_priority": {
        "name": "Hospital_to_Ambulance",
        "server_host": "h4",
        "client_host": "h2",
        "server_ip": "10.0.0.4",
        "port": 5001,
        "ping_target": "10.0.0.4",
    },
    "low_priority": {
        "name": "PublicHelpline_to_CommandCenter",
        "server_host": "h1",
        "client_host": "h7",
        "server_ip": "10.0.0.1",
        "port": 8080,
        "ping_target": "10.0.0.1",
    },
}


def run_cmd(cmd, timeout=60):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except FileNotFoundError:
        return f"COMMAND_NOT_FOUND: {cmd[0]}"


def host_exec(host, cmd):
    """Execute command inside Mininet host namespace if available."""
    ns_check = run_cmd(["ip", "netns", "list"])
    if host in ns_check:
        return run_cmd(["ip", "netns", "exec", host, "bash", "-c", cmd], timeout=IPERF_DURATION + 30)
    return f"MANUAL_REQUIRED: mininet> {host} {cmd}"


def parse_iperf_throughput_mbps(output):
    """Parse iperf2 summary line: 'Bandwidth' value in Mbits/sec."""
    match = re.search(r"([\d.]+)\s+Mbits/sec", output)
    if match:
        return float(match.group(1))
    match = re.search(r"([\d.]+)\s+Kbits/sec", output)
    if match:
        return float(match.group(1)) / 1000.0
    return None


def parse_ping_avg_ms(output):
    """Parse ping rtt min/avg/max block."""
    match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", output)
    if match:
        return float(match.group(1))
    match = re.search(r"round-trip min/avg/max = [\d.]+/([\d.]+)/", output)
    if match:
        return float(match.group(1))
    return None


def run_iperf_test(flow_key, flow):
    server = flow["server_host"]
    client = flow["client_host"]
    port = flow["port"]
    ip = flow["server_ip"]

    host_exec(server, f"pkill -f 'iperf -s -p {port}' 2>/dev/null; true")
    host_exec(server, f"iperf -s -p {port} -D")
    time.sleep(1)

    out = host_exec(
        client,
        f"iperf -c {ip} -p {port} -t {IPERF_DURATION} -b {IPERF_BANDWIDTH}",
    )
    mbps = parse_iperf_throughput_mbps(out)
    return {"raw": out, "throughput_mbps": mbps}


def run_ping_test(flow_key, flow):
    out = host_exec(flow["client_host"], f"ping -c {PING_COUNT} {flow['ping_target']}")
    avg_ms = parse_ping_avg_ms(out)
    return {"raw": out, "latency_avg_ms": avg_ms}


def run_all_tests(mode):
    results = {
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iperf_duration_sec": IPERF_DURATION,
        "iperf_bandwidth_requested": IPERF_BANDWIDTH,
        "ping_count": PING_COUNT,
        "flows": {},
    }

    for key, flow in FLOWS.items():
        print(f"  Running iperf: {flow['name']} ({mode})...")
        iperf = run_iperf_test(key, flow)
        print(f"  Running ping:  {flow['name']} ({mode})...")
        ping = run_ping_test(key, flow)
        results["flows"][key] = {
            "label": flow["name"],
            "throughput_mbps": iperf["throughput_mbps"],
            "latency_avg_ms": ping["latency_avg_ms"],
            "iperf_raw": iperf["raw"][-500:],
            "ping_raw": ping["raw"][-500:],
        }
        print(
            f"    -> throughput={iperf['throughput_mbps']} Mbps, "
            f"latency={ping['latency_avg_ms']} ms"
        )

    return results


def save_csv(results, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "mode", "flow_class", "flow_name",
            "throughput_mbps", "latency_avg_ms", "timestamp",
        ])
        for key, data in results["flows"].items():
            writer.writerow([
                results["mode"],
                key,
                data["label"],
                data["throughput_mbps"],
                data["latency_avg_ms"],
                results["timestamp"],
            ])
    print(f"CSV saved -> {path}")


def save_json(results, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"JSON saved -> {path}")


def print_manual_commands():
    print("\n=== Manual Mininet CLI commands (if host namespaces unavailable) ===\n")
    for mode in ("baseline", "qos"):
        print(f"--- {mode.upper()} ---")
        for flow in FLOWS.values():
            print(f"  mininet> {flow['server_host']} cmd iperf -s -p {flow['port']} &")
            print(
                f"  mininet> {flow['client_host']} cmd iperf -c {flow['server_ip']} "
                f"-p {flow['port']} -t {IPERF_DURATION} -b {IPERF_BANDWIDTH}"
            )
            print(
                f"  mininet> {flow['client_host']} ping -c {PING_COUNT} {flow['ping_target']}"
            )
        print()


def main():
    parser = argparse.ArgumentParser(description="Run SDN QoS performance tests")
    parser.add_argument(
        "--mode", choices=["baseline", "qos"], required=True,
        help="baseline=no QoS, qos=with QoS policy",
    )
    parser.add_argument(
        "--output-dir", default=os.path.join(PROJECT_ROOT, "performance", "results"),
        help="Directory for CSV/JSON output",
    )
    parser.add_argument("--manual", action="store_true", help="Print manual Mininet commands")
    args = parser.parse_args()

    if args.manual:
        print_manual_commands()
        return 0

    print(f"\n=== Performance tests ({args.mode}) ===\n")
    results = run_all_tests(args.mode)

    base = os.path.join(args.output_dir, f"perf_{args.mode}")
    save_csv(results, f"{base}.csv")
    save_json(results, f"{base}.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
