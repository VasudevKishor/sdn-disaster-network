#!/usr/bin/env bash
# capture_traffic.sh - Packet capture + competing iperf flows for QoS demo
#
# Run from project root while Mininet + RYU + QoS are active.
# Requires root for tcpdump on OVS ports.
#
# Usage:
#   sudo bash monitoring/capture_traffic.sh
#
# Output saved to monitoring/evidence/ (pcap + summary log)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
EVIDENCE_DIR="${PROJECT_ROOT}/monitoring/evidence"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
PCAP_FILE="${EVIDENCE_DIR}/qos_demo_${TIMESTAMP}.pcap"
LOG_FILE="${EVIDENCE_DIR}/qos_demo_${TIMESTAMP}.log"

mkdir -p "${EVIDENCE_DIR}"

CAP_PORT="${CAP_PORT:-s1-eth1}"
DURATION="${DURATION:-20}"

log() {
  echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log "=== Disaster Response QoS Traffic Capture ==="
log "Capture port: ${CAP_PORT}, duration: ${DURATION}s"
log "Evidence directory: ${EVIDENCE_DIR}"

# Snapshot queue stats BEFORE traffic
log ""
log "--- Queue stats BEFORE ---"
ovs-ofctl -O OpenFlow13 dump-queues s1 2>&1 | tee -a "${LOG_FILE}" || true
ovs-ofctl -O OpenFlow13 queue-stats s1 2>&1 | tee -a "${LOG_FILE}" || true

# Start tcpdump on the bottleneck port (background)
log ""
log "Starting tcpdump on ${CAP_PORT} -> ${PCAP_FILE}"
if ! command -v tcpdump >/dev/null 2>&1; then
  log "WARNING: tcpdump not found; skipping packet capture."
  TCPDUMP_PID=""
else
  tcpdump -i "${CAP_PORT}" -w "${PCAP_FILE}" -s 65535 &
  TCPDUMP_PID=$!
  sleep 2
fi

log ""
log "--- Starting competing iperf flows (run in Mininet CLI if auto-exec fails) ---"
log "High priority: Hospital h2 -> Ambulance h4 TCP/5001"
log "Low priority:  PublicHelpline h7 -> CommandCenter h1 TCP/8080"

# Execute via Mininet host namespaces when available
if ip netns list 2>/dev/null | grep -q "h2"; then
  ip netns exec h4 iperf -s -p 5001 -D 2>/dev/null || true
  ip netns exec h1 iperf -s -p 8080 -D 2>/dev/null || true
  sleep 1
  ip netns exec h2 iperf -c 10.0.0.4 -p 5001 -t "${DURATION}" -b 30M &
  HPID=$!
  ip netns exec h7 iperf -c 10.0.0.1 -p 8080 -t "${DURATION}" -b 30M &
  LPID=$!
  wait "${HPID}" "${LPID}" 2>/dev/null || true
else
  log "Mininet namespaces not detected. Run manually in Mininet CLI:"
  log "  mininet> h4 cmd iperf -s -p 5001 &"
  log "  mininet> h1 cmd iperf -s -p 8080 &"
  log "  mininet> h2 cmd iperf -c 10.0.0.4 -p 5001 -t ${DURATION} -b 30M &"
  log "  mininet> h7 cmd iperf -c 10.0.0.1 -p 8080 -t ${DURATION} -b 30M &"
  sleep "${DURATION}"
fi

sleep 2
if [[ -n "${TCPDUMP_PID}" ]]; then
  kill "${TCPDUMP_PID}" 2>/dev/null || true
  wait "${TCPDUMP_PID}" 2>/dev/null || true
fi

log ""
log "--- Queue stats AFTER ---"
ovs-ofctl -O OpenFlow13 dump-queues s1 2>&1 | tee -a "${LOG_FILE}" || true
ovs-ofctl -O OpenFlow13 queue-stats s1 2>&1 | tee -a "${LOG_FILE}" || true

log ""
log "--- Flow stats (s1) ---"
ovs-ofctl -O OpenFlow13 dump-flows s1 2>&1 | tee -a "${LOG_FILE}" || true

log ""
log "=== Capture complete ==="
log "PCAP: ${PCAP_FILE}"
log "LOG:  ${LOG_FILE}"
log "Analyze pcap: tcpdump -r ${PCAP_FILE} -nn | head"
