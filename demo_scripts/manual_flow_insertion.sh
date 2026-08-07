#!/usr/bin/env bash
# manual_flow_insertion.sh - Demo manual OpenFlow rule management via RYU REST API
#
# Prerequisites:
#   1. ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
#   2. Mininet network running
#
# Usage:
#   bash demo_scripts/manual_flow_insertion.sh list
#   bash demo_scripts/manual_flow_insertion.sh add
#   bash demo_scripts/manual_flow_insertion.sh delete
#   bash demo_scripts/manual_flow_insertion.sh demo   # full live demo sequence

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=demo_scripts/common_env.sh
source "${SCRIPT_DIR}/common_env.sh"

cmd="${1:-demo}"

list_flows() {
  local dpid="${1:-$DPID_S1}"
  echo "=== Flow rules on switch dpid=${dpid} ==="
  curl -s -X GET "${RYU_REST_BASE}/stats/flow/${dpid}" | python3 -m json.tool
}

add_manual_flow() {
  echo "=== Adding manual flow: Police (h3) -> Hospital (h2) pinned to port 2 on s1 ==="
  curl -s -X POST "${RYU_REST_BASE}/stats/flowentry/add" \
    -H "Content-Type: application/json" \
    -d "{
      \"dpid\": ${DPID_S1},
      \"table_id\": 0,
      \"priority\": 700,
      \"match\": {
        \"eth_type\": 2048,
        \"ipv4_src\": \"${IP_POLICE}\",
        \"ipv4_dst\": \"${IP_HOSPITAL}\"
      },
      \"actions\": [
        {\"type\": \"OUTPUT\", \"port\": 2}
      ]
    }"
  echo ""
  echo "Flow added. List with: $0 list ${DPID_S1}"
}

delete_manual_flow() {
  echo "=== Deleting manual flow (Police -> Hospital on s1) ==="
  curl -s -X POST "${RYU_REST_BASE}/stats/flowentry/delete" \
    -H "Content-Type: application/json" \
    -d "{
      \"dpid\": ${DPID_S1},
      \"table_id\": 0,
      \"priority\": 700,
      \"match\": {
        \"eth_type\": 2048,
        \"ipv4_src\": \"${IP_POLICE}\",
        \"ipv4_dst\": \"${IP_HOSPITAL}\"
      }
    }"
  echo ""
  echo "Flow deleted."
}

add_ambulance_port_flow() {
  echo "=== Adding manual port-match flow: TCP dst ${PORT_AMBULANCE} -> Ambulance ==="
  curl -s -X POST "${RYU_REST_BASE}/stats/flowentry/add" \
    -H "Content-Type: application/json" \
    -d "{
      \"dpid\": ${DPID_S2},
      \"table_id\": 0,
      \"priority\": 800,
      \"match\": {
        \"eth_type\": 2048,
        \"ip_proto\": 6,
        \"ipv4_dst\": \"${IP_AMBULANCE}\",
        \"tcp_dst\": ${PORT_AMBULANCE}
      },
      \"actions\": [
        {\"type\": \"SET_QUEUE\", \"queue_id\": 0},
        {\"type\": \"OUTPUT\", \"port\": 4}
      ]
    }"
  echo ""
}

run_demo() {
  echo "=========================================="
  echo " Manual Flow Rule REST API Demo"
  echo " REST endpoint: ${RYU_REST_BASE}"
  echo "=========================================="
  echo ""
  echo "[Step 1] List existing flows on s1 (dpid=${DPID_S1})"
  list_flows "${DPID_S1}" | head -80
  echo ""
  echo "[Step 2] Insert manual flow via curl POST"
  add_manual_flow
  echo ""
  echo "[Step 3] Verify flow appears in switch table"
  list_flows "${DPID_S1}" | head -80
  echo ""
  echo "[Step 4] Test traffic (run in Mininet CLI): h3 ping -c 3 h2"
  echo ""
  read -r -p "Press Enter after testing, to delete the manual flow..."
  delete_manual_flow
  echo ""
  echo "[Step 5] Confirm deletion"
  list_flows "${DPID_S1}" | head -80
  echo ""
  echo "Demo complete. Capture terminal output as review evidence."
}

case "${cmd}" in
  list)   list_flows "${2:-$DPID_S1}" ;;
  add)    add_manual_flow ;;
  delete) delete_manual_flow ;;
  add_ambulance) add_ambulance_port_flow ;;
  demo)   run_demo ;;
  *)
    echo "Usage: $0 {list|add|delete|add_ambulance|demo} [dpid]"
    exit 1
    ;;
esac
