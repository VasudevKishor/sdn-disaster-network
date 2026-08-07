#!/usr/bin/env python3
"""
stats_reader.py - Collect OpenFlow / OVS statistics for QoS monitoring evidence

Reads:
  - RYU REST API (flow stats, port stats)
  - ovs-ofctl dump-flows / dump-queues / queue-stats

Usage:
  python3 monitoring/stats_reader.py --save monitoring/evidence/stats_snapshot.json
  python3 monitoring/stats_reader.py --compare before.json after.json
"""

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone


RYU_REST = "http://127.0.0.1:8080"
SWITCHES = ["s1", "s2", "s3"]
DPIDS = [1, 2, 3]


def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc), "url": url}


def run_cmd(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return out.strip()
    except subprocess.CalledProcessError as exc:
        return exc.output.strip() if exc.output else str(exc)


def collect_ryu_stats():
    data = {"flows": {}, "ports": {}}
    for dpid in DPIDS:
        data["flows"][str(dpid)] = fetch_json(f"{RYU_REST}/stats/flow/{dpid}")
        data["ports"][str(dpid)] = fetch_json(f"{RYU_REST}/stats/port/{dpid}")
    return data


def collect_ovs_stats():
    data = {}
    for sw in SWITCHES:
        data[sw] = {
            "dump_flows": run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", sw]),
            "dump_queues": run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-queues", sw]),
            "queue_stats": run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "queue-stats", sw]),
            "port_stats": run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-ports", sw]),
        }
    return data


def collect_all():
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ryu_rest": collect_ryu_stats(),
        "ovs": collect_ovs_stats(),
    }


def print_summary(snapshot):
    print(f"Snapshot time: {snapshot.get('timestamp', 'unknown')}")
    print("\n--- OVS queue-stats (s1) ---")
    qs = snapshot.get("ovs", {}).get("s1", {}).get("queue_stats", "")
    print(qs[:2000] if qs else "(no data)")
    print("\n--- OVS port-stats excerpt (s1) ---")
    ps = snapshot.get("ovs", {}).get("s1", {}).get("port_stats", "")
    print(ps[:2000] if ps else "(no data)")


def compare_snapshots(before_path, after_path):
    with open(before_path, encoding="utf-8") as fh:
        before = json.load(fh)
    with open(after_path, encoding="utf-8") as fh:
        after = json.load(fh)

    print("=== QoS Stats Comparison ===")
    print(f"Before: {before.get('timestamp')}")
    print(f"After:  {after.get('timestamp')}")
    print("\n--- Queue stats delta (s1) ---")
    bq = before.get("ovs", {}).get("s1", {}).get("queue_stats", "")
    aq = after.get("ovs", {}).get("s1", {}).get("queue_stats", "")
    print("BEFORE:\n", bq)
    print("\nAFTER:\n", aq)


def main():
    parser = argparse.ArgumentParser(description="Read RYU/OVS stats for QoS monitoring")
    parser.add_argument("--save", help="Save snapshot JSON to this path")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="Compare two snapshots")
    parser.add_argument("--print", action="store_true", help="Print summary to stdout")
    args = parser.parse_args()

    if args.compare:
        compare_snapshots(args.compare[0], args.compare[1])
        return 0

    snapshot = collect_all()

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2)
        print(f"Saved stats snapshot -> {args.save}")

    if args.print or not args.save:
        print_summary(snapshot)

    return 0


if __name__ == "__main__":
    sys.exit(main())
