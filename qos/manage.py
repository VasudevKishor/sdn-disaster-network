#!/usr/bin/env python3
"""Scoped OVSDB QoS ownership. Never destroy another lab's rows."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.lab import managed_ports

OWNER = "sdn-disaster-phase1"


def ovs(*args):
    return subprocess.check_output(["ovs-vsctl", "--timeout=5", *args], text=True).strip()


def owned(table, port):
    return ovs("--data=bare", "--no-heading", "--columns=_uuid", "find", table,
               "external_ids:owner=" + OWNER, "external_ids:port=" + port).split()


def clear(port):
    qoses, queues = owned("QoS", port), owned("Queue", port)
    # Do not detach a foreign QoS that replaced ours in the meantime.
    try:
        current = ovs("get", "Port", port, "qos").strip("[] ")
    except subprocess.CalledProcessError:
        current = None  # bridge may already have been removed by net.stop()
    if current in qoses:
        ovs("clear", "Port", port, "qos")
    for table, rows in (("QoS", qoses), ("Queue", queues)):
        for uuid in rows:
            ovs("--if-exists", "destroy", table, uuid)


def apply(action):
    ports = managed_ports()
    if action in ("setup", "baseline"):
        # Validate every interface/rate before the first mutation; missing is an error.
        cfg = json.loads(Path(__file__).with_name("queue_config.json").read_text())
        rates = []
        for key, prefix in (("0", "HIGH"), ("1", "LOW")):
            q = cfg["queues"][key]
            lo = int(os.environ.get(prefix + "_MIN_RATE", q["min_rate_bps"]))
            hi = int(os.environ.get(prefix + "_MAX_RATE", q["max_rate_bps"]))
            if not 0 < lo <= hi <= min(ports.values()) * 1000000:
                raise ValueError("Queue rates exceed the smallest configured egress")
            rates.append((lo, hi))
        if sum(lo for lo, hi in rates) > min(ports.values()) * 1000000:
            raise ValueError("Sum of guaranteed rates exceeds egress capacity")
        for port in ports:
            bridge = ovs("port-to-br", port)
            if bridge != port.split("-")[0]:
                raise ValueError("Unexpected bridge for " + port)
            actual = int(ovs("get", "Interface", port, "ofport"))
            if actual != int(port.split("eth")[1]):
                raise ValueError("Unexpected OpenFlow port for " + port)
            current = ovs("get", "Port", port, "qos").strip("[] ")
            if current and current not in owned("QoS", port):
                raise ValueError("Foreign QoS attached to " + port)
    for port, mbps in ports.items():
        clear(port)
        if action in ("setup", "baseline"):
            tags = ["external_ids:owner=" + OWNER, "external_ids:port=" + port]
            active = rates if action == "setup" else [(0, mbps * 1000000)] * 2
            ovs("set", "Port", port, "qos=@qos", "--", "--id=@qos", "create", "QoS",
                "type=linux-htb", "other-config:max-rate=%d" % (mbps * 1000000),
                "queues:0=@high", "queues:1=@low", *tags, "--",
                "--id=@high", "create", "Queue", *tags,
                "other-config:min-rate=%d" % active[0][0],
                "other-config:max-rate=%d" % active[0][1], "--",
                "--id=@low", "create", "Queue", *tags,
                "other-config:min-rate=%d" % active[1][0],
                "other-config:max-rate=%d" % active[1][1])
        print(action + ": " + port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "baseline", "teardown"])
    args = parser.parse_args()
    try:
        apply(args.action)
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        print("QoS failed (partial changes may require teardown): " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
