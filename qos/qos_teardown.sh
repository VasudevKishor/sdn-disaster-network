#!/usr/bin/env bash
# qos_teardown.sh - Remove OVS QoS queues (for baseline/no-QoS tests)

set -euo pipefail

SWITCH="${QOS_SWITCH:-s1}"
PORT="${QOS_PORT:-${SWITCH}-eth1}"

echo "=== Removing QoS from ${PORT} ==="
ovs-vsctl --if-exists clear port "${PORT}" qos
echo "Done. Port ${PORT} is back to best-effort forwarding."
