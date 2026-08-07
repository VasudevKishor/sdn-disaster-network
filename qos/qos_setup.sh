#!/usr/bin/env bash
# qos_setup.sh - Configure OVS HTB QoS queues on disaster-response switches
#
# Run AFTER Mininet network is started (as root):
#   sudo bash qos/qos_setup.sh
#
# Run qos_teardown.sh to remove QoS before baseline (no-QoS) performance tests.
#
# Queue definitions mirror qos/queue_config.json:
#   Queue 0: high priority — min 40 Mbps, max 50 Mbps
#   Queue 1: low priority  — min 1 Mbps,  max 5 Mbps (rate-limited)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../demo_scripts/common_env.sh" 2>/dev/null || true

SWITCH="${QOS_SWITCH:-s1}"
PORT="${QOS_PORT:-${SWITCH}-eth1}"

# Rates from queue_config.json (bits per second)
HIGH_MIN_RATE="${HIGH_MIN_RATE:-40000000}"
HIGH_MAX_RATE="${HIGH_MAX_RATE:-50000000}"
LOW_MIN_RATE="${LOW_MIN_RATE:-1000000}"
LOW_MAX_RATE="${LOW_MAX_RATE:-5000000}"

echo "=== Configuring OVS QoS on ${SWITCH} port ${PORT} ==="

# Remove any existing QoS on this port first (idempotent setup)
ovs-vsctl --if-exists clear port "${PORT}" qos
ovs-vsctl --if-exists destroy QoS "${SWITCH}-qos"

# Create QoS object with two queues (0=high, 1=low)
ovs-vsctl set port "${PORT}" qos=@"newqos" -- \
  --id=@"newqos" create QoS type=linux-htb other-config:max-rate=100000000 queues=0,1 \
  -- set Queue queues=@q0 -- \
  --id=@q0 create Queue other-config:min-rate="${HIGH_MIN_RATE}" other-config:max-rate="${HIGH_MAX_RATE}" \
  -- set Queue queues=@q1 -- \
  --id=@q1 create Queue other-config:min-rate="${LOW_MIN_RATE}" other-config:max-rate="${LOW_MAX_RATE}"

echo ""
echo "=== QoS applied. Verify with: ==="
echo "  ovs-vsctl list Queue"
echo "  ovs-ofctl -O OpenFlow13 queue-stats ${SWITCH}"
echo "  ovs-ofctl -O OpenFlow13 dump-queues ${SWITCH}"
