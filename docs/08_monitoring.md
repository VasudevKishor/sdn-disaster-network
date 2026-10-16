# Document 8 — Traffic Monitoring

## 8.1 Why Monitor Traffic?

Having flow rules and QoS queues in place is not enough — you need **evidence**
that they are actually working. The monitoring component answers:

- Are the OVS queues actually being used?
- Is the high-priority flow getting more bandwidth?
- Is the low-priority flow actually being rate-limited?
- What does the packet-level data look like?

Two files handle this: `monitoring/capture_traffic.sh` and `monitoring/stats_reader.py`.

---

## 8.2 `capture_traffic.sh` — The Complete Monitoring Script

This script orchestrates the entire monitoring demonstration:

### Step 1: Take a "Before" Snapshot

```bash
ovs-ofctl -O OpenFlow13 dump-queues s1 2>&1 | tee -a "${LOG_FILE}"
ovs-ofctl -O OpenFlow13 queue-stats s1  2>&1 | tee -a "${LOG_FILE}"
```

- `dump-queues s1` — Lists queue configurations (min-rate, max-rate) on s1.
- `queue-stats s1` — Shows the current byte/packet counts per queue.
  At this point (before traffic), counters should be zero or very low.
- `tee -a "${LOG_FILE}"` — Writes output to both the terminal AND a log file.

### Step 2: Start `tcpdump` in the Background

```bash
tcpdump -i "${CAP_PORT}" -w "${PCAP_FILE}" -s 65535 &
TCPDUMP_PID=$!
```

- `-i s1-eth1` — Capture packets on the bottleneck interface.
- `-w qos_demo_TIMESTAMP.pcap` — Write raw packets to a `.pcap` file.
- `-s 65535` — Capture the full packet (not truncated).
- `&` — Run in the background so the script continues.
- `TCPDUMP_PID=$!` — Save the background process ID so we can kill it later.

The `.pcap` file can later be opened in **Wireshark** for visual analysis.

### Step 3: Launch Competing Traffic Flows

```bash
if ip netns list 2>/dev/null | grep -q "h2"; then
    # Mininet host namespaces are available
    ip netns exec h4 iperf -s -p 5001 -D   # Start iperf server on Ambulance
    ip netns exec h1 iperf -s -p 8080 -D   # Start iperf server on CommandCenter
    sleep 1
    ip netns exec h2 iperf -c 10.0.0.4 -p 5001 -t 20 -b 30M &   # High-priority flow
    ip netns exec h7 iperf -c 10.0.0.1 -p 8080 -t 20 -b 30M &   # Low-priority flow
    wait
else
    # Print manual commands for Mininet CLI
    log "mininet> h4 cmd iperf -s -p 5001 &"
    log "mininet> h7 iperf -c 10.0.0.1 -p 8080 -t 20 -b 30M &"
    sleep "${DURATION}"
fi
```

**What `ip netns exec h2` does:**
Mininet creates each host as a Linux network namespace named after the host. This
command executes `iperf -c ...` inside the `h2` namespace — exactly as if you were
typing it into the Mininet CLI with `h2 iperf -c ...`.

**The competing flows:**
- `h2 → h4` (Hospital → Ambulance): 30 Mbps requested on TCP port 5001.
  → This matches the proactive flow rule + Queue 0 (high priority, up to 50 Mbps).
- `h7 → h1` (PublicHelpline → CommandCenter): 30 Mbps requested on TCP port 8080.
  → This matches the low-priority rule + Queue 1 (capped at 5 Mbps).

Both ask for 30 Mbps, but the shared link is 50 Mbps. Without QoS, both would
get ~25 Mbps. With QoS, h2 gets near 30 Mbps and h7 gets at most 5 Mbps.

### Step 4: Take an "After" Snapshot

```bash
kill "${TCPDUMP_PID}"   # Stop packet capture
ovs-ofctl -O OpenFlow13 queue-stats s1 | tee -a "${LOG_FILE}"
ovs-ofctl -O OpenFlow13 dump-flows s1  | tee -a "${LOG_FILE}"
```

The `queue-stats` now shows significantly higher byte counts for Queue 0 than
Queue 1 — **proof that QoS is working**.

---

## 8.3 `stats_reader.py` — Collecting Structured Evidence

This Python script provides a more structured way to collect and compare statistics.

### The `collect_all()` Function

```python
def collect_all():
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ryu_rest": collect_ryu_stats(),
        "ovs": collect_ovs_stats(),
    }
```

It combines two sources of data into a single timestamped JSON object.

### `collect_ryu_stats()` — From the REST API

```python
def collect_ryu_stats():
    data = {"flows": {}, "ports": {}}
    for dpid in [1, 2, 3]:
        data["flows"][str(dpid)] = fetch_json(f"{RYU_REST}/stats/flow/{dpid}")
        data["ports"][str(dpid)] = fetch_json(f"{RYU_REST}/stats/port/{dpid}")
    return data
```

Queries the Ryu REST API (`ofctl_rest` app) for:
- `/stats/flow/{dpid}` — All flow table entries with their byte/packet hit counters.
- `/stats/port/{dpid}` — Per-port byte/packet statistics from OpenFlow.

This gives controller-side visibility into what's happening.

### `collect_ovs_stats()` — From OVS Directly

```python
def collect_ovs_stats():
    data = {}
    for sw in ["s1", "s2", "s3"]:
        data[sw] = {
            "dump_flows":  run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", sw]),
            "dump_queues": run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-queues", sw]),
            "queue_stats": run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "queue-stats", sw]),
            "port_stats":  run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-ports", sw]),
        }
    return data
```

Directly queries OVS using `ovs-ofctl` for:
- `dump-flows` — Current flow table (verifies rules are installed).
- `dump-queues` — Queue configuration (verifies HTB queues are set up).
- `queue-stats` — Per-queue traffic counters (the proof QoS is working).
- `dump-ports` — Per-port traffic counters (independent confirmation).

### The `--compare` Feature

```bash
python3 monitoring/stats_reader.py --save monitoring/evidence/before.json
# (run iperf traffic)
python3 monitoring/stats_reader.py --save monitoring/evidence/after.json
python3 monitoring/stats_reader.py --compare monitoring/evidence/before.json monitoring/evidence/after.json
```

The comparison function reads both JSON files and prints the queue-stats side
by side. If Queue 0 has significantly more bytes in the "after" snapshot, and
Queue 1 is low (rate-limited), the evidence is conclusive.

---

## 8.4 What Good Evidence Looks Like

### `dump-flows s1` showing QoS rules are installed:
```
priority=500,ip,nw_src=10.0.0.2,nw_dst=10.0.0.4 actions=set_queue:0,output:2
priority=400,tcp,nw_src=10.0.0.7,nw_dst=10.0.0.1,tp_src=8080 actions=set_queue:1,output:3
```

### `queue-stats s1` before traffic:
```
port=s1-eth1 queue=0 tx_bytes=0   tx_packets=0
port=s1-eth1 queue=1 tx_bytes=0   tx_packets=0
```

### `queue-stats s1` after 20 seconds of competing flows:
```
port=s1-eth1 queue=0 tx_bytes=75000000  tx_packets=50000  ← Hospital flow: ~30 Mbps
port=s1-eth1 queue=1 tx_bytes=12500000  tx_packets=8000   ← Helpline: ~5 Mbps (capped)
```

The ratio of `queue=0` to `queue=1` bytes directly proves the policy is enforced.
