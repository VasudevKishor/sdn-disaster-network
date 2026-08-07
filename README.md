# National Disaster Response Network — SDN Simulation (Review 1)

Mininet + RYU + Open vSwitch project for a **National Disaster Management Authority**
network backbone. Review 1 builds the foundational SDN infrastructure: topology,
custom flow rules, QoS queues, traffic monitoring, and performance comparison.

**Out of scope for Review 1:** link failures, rerouting, attack detection (later reviews).

---

## Prerequisites (Ubuntu/Linux)

```bash
# Mininet + OVS
sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch iperf tcpdump curl

# RYU SDN controller
sudo apt-get install -y python3-pip
pip3 install ryu

# Performance charts
pip3 install matplotlib
```

Verify:

```bash
mn --version
ryu-manager --version
ovs-vsctl --version
```

---

## Project Structure

```
sdn-disaster-network/
├── topology/
│   ├── disaster_topo.py       # Custom Mininet topology (3 switches, 7 hosts)
│   └── run_network.py         # Launch script with pingall
├── controller/
│   ├── base_switch_app.py     # Learning switch (Req 1 starting point)
│   └── flow_manager_app.py    # Custom flows + QoS mapping (Req 2–3)
├── qos/
│   ├── queue_config.json      # Queue definitions (review artifact)
│   ├── qos_setup.sh           # Apply OVS HTB queues
│   └── qos_teardown.sh        # Remove QoS for baseline tests
├── monitoring/
│   ├── capture_traffic.sh     # tcpdump + competing iperf flows
│   ├── stats_reader.py        # RYU REST + ovs-ofctl stats
│   └── evidence/              # Saved pcaps, logs, JSON snapshots
├── performance/
│   ├── run_tests.py           # iperf/ping tests → CSV/JSON
│   ├── generate_charts.py     # matplotlib charts from CSV
│   ├── results/               # Raw test output
│   └── charts/                # Generated PNG charts
├── demo_scripts/
│   ├── common_env.sh
│   └── manual_flow_insertion.sh  # curl REST API demo (Req 2)
└── README.md
```

---

## Agency Host Map

| Host | Agency            | IP        | Switch | Priority |
|------|-------------------|-----------|--------|----------|
| h1   | CommandCenter     | 10.0.0.1  | s1     | normal   |
| h2   | Hospital          | 10.0.0.2  | s1     | high     |
| h3   | Police            | 10.0.0.3  | s2     | normal   |
| h4   | Ambulance         | 10.0.0.4  | s2     | high     |
| h5   | RescueTeam        | 10.0.0.5  | s3     | high     |
| h6   | Drone             | 10.0.0.6  | s3     | medium   |
| h7   | PublicHelpline    | 10.0.0.7  | s1     | low      |

**Simulated traffic ports (assumptions for viva):**

| Port | Traffic type              |
|------|---------------------------|
| 5001 | Ambulance patient data    |
| 5002 | Hospital critical data    |
| 6001 | Rescue location updates   |
| 8080 | Public helpline bulk      |
| 9000 | Drone live video          |

---

## Quick Start (Full Review Demo)

Open **3 terminals** on your Linux VM:

### Terminal 1 — RYU Controller

```bash
cd sdn-disaster-network
ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
```

Expected log lines:

```
Switch connected: dpid=1 (QoS=True)
Installed Hospital priority path rule on s1 (dpid=1)
Installed Ambulance TCP/5001 rule on s2 (dpid=2)
...
```

### Terminal 2 — Mininet Network

```bash
cd sdn-disaster-network
sudo python3 topology/run_network.py
```

Expected: agency map printed, then `pingall` with **0% dropped**.

### Terminal 3 — QoS + Monitoring + Tests

```bash
cd sdn-disaster-network
sudo bash qos/qos_setup.sh
bash demo_scripts/manual_flow_insertion.sh list
```

---

# Requirement-by-Requirement Guide

## Requirement 1: Controller Setup & Topology (3 marks)

### (a) Files

- `topology/disaster_topo.py` — custom topology (triangle core, 3 switches, 7 hosts, TCLink)
- `topology/run_network.py` — network launcher
- `controller/base_switch_app.py` — learning switch base

### (b) How to run

```bash
# Terminal 1
ryu-manager controller/base_switch_app.py

# Terminal 2
sudo python3 topology/run_network.py
```

Alternative Mininet CLI launch:

```bash
sudo mn --custom topology/disaster_topo.py --topo disaster \
  --controller=remote,ip=127.0.0.1,port=6633 --switch ovsk,protocols=OpenFlow13 \
  --link tc
```

### (c) Evidence to capture

1. **Controller logs** showing `Switch connected: dpid=1/2/3`
2. **OVS controller connection:**
   ```bash
   ovs-vsctl show
   ```
   Look for `Controller "tcp:127.0.0.1:6633"` on each bridge (s1, s2, s3).

3. **OpenFlow handshake:**
   ```bash
   ovs-ofctl -O OpenFlow13 show s1
   ```

4. **pingall 0% loss** output from Mininet

### (d) Viva talking points

- Why triangle topology? Redundant paths for later failover (s1↔s2, s2↔s3, s3↔s1).
- TCLink bandwidth/delay model real backbone vs last-mile links.
- SDN separates control plane (RYU) from data plane (OVS switches).

---

## Requirement 2: Flow Rule Implementation (3 marks)

### (a) Files

- `controller/flow_manager_app.py` — proactive OpenFlow rules
- `demo_scripts/manual_flow_insertion.sh` — REST API curl demo

### (b) How to run

```bash
ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
# (Mininet running in another terminal)

bash demo_scripts/manual_flow_insertion.sh demo
# or individual commands:
bash demo_scripts/manual_flow_insertion.sh list
bash demo_scripts/manual_flow_insertion.sh add
bash demo_scripts/manual_flow_insertion.sh delete
```

### (c) Evidence to capture

1. **Proactive rules in controller logs** (Hospital path, Ambulance port 5001)
2. **Flow table dump:**
   ```bash
   ovs-ofctl -O OpenFlow13 dump-flows s1
   ovs-ofctl -O OpenFlow13 dump-flows s2
   ```
3. **REST API list output** from `manual_flow_insertion.sh list`
4. **Before/after** adding and deleting manual Police→Hospital rule

Example manual curl (Police → Hospital pinned on s1 port 2):

```bash
curl -X POST http://127.0.0.1:8080/stats/flowentry/add \
  -H "Content-Type: application/json" \
  -d '{
    "dpid": 1, "priority": 700,
    "match": {"eth_type": 2048, "ipv4_src": "10.0.0.3", "ipv4_dst": "10.0.0.2"},
    "actions": [{"type": "OUTPUT", "port": 2}]
  }'
```

### (d) Viva talking points

- Priority field in flow entries (higher = matched first).
- Explicit IP/port matching vs MAC learning.
- REST API allows runtime policy changes without restarting controller.

---

## Requirement 3: QoS Policy Configuration (3 marks)

### (a) Files

- `qos/queue_config.json` — queue rate definitions (separate artifact)
- `qos/qos_setup.sh` — ovs-vsctl HTB queue creation
- `qos/qos_teardown.sh` — remove QoS for baseline
- `controller/flow_manager_app.py` — `OFPActionSetQueue` mapping

### (b) How to run

```bash
# With Mininet running:
sudo bash qos/qos_setup.sh

# Verify queues:
ovs-vsctl list Queue
ovs-ofctl -O OpenFlow13 dump-queues s1
```

Disable QoS for baseline tests:

```bash
DISASTER_QOS=0 ryu-manager controller/flow_manager_app.py
sudo bash qos/qos_teardown.sh
```

### (c) Evidence to capture

1. Output of `qos_setup.sh`
2. `ovs-vsctl list Queue` showing min-rate/max-rate for queues 0 and 1
3. Flow entries with `set_queue:0` or `set_queue:1` in dump-flows
4. `queue_config.json` explaining rate assumptions

**Queue assumptions:**

| Queue | Class    | min-rate | max-rate | Purpose                    |
|-------|----------|----------|----------|----------------------------|
| 0     | High     | 40 Mbps  | 50 Mbps  | Hospital/Ambulance/Rescue  |
| 1     | Low      | 1 Mbps   | 5 Mbps   | Public helpline (capped)   |

### (d) Viva talking points

- OVS HTB queuing vs OpenFlow `set_queue` action.
- Why attach QoS to s1-eth1 (shared egress toward core).
- `DISASTER_QOS=0` env var disables queue actions for fair baseline comparison.

---

## Requirement 4: Traffic Monitoring (3 marks)

### (a) Files

- `monitoring/capture_traffic.sh`
- `monitoring/stats_reader.py`

### (b) How to run

```bash
sudo bash qos/qos_setup.sh   # QoS must be active

# Snapshot BEFORE
python3 monitoring/stats_reader.py --save monitoring/evidence/before.json --print

# In Mininet CLI — competing traffic:
mininet> h4 cmd iperf -s -p 5001 &
mininet> h1 cmd iperf -s -p 8080 &
mininet> h2 cmd iperf -c 10.0.0.4 -p 5001 -t 20 -b 30M &
mininet> h7 cmd iperf -c 10.0.0.1 -p 8080 -t 20 -b 30M &

# Snapshot AFTER
python3 monitoring/stats_reader.py --save monitoring/evidence/after.json --print
python3 monitoring/stats_reader.py --compare monitoring/evidence/before.json monitoring/evidence/after.json

# Optional packet capture
sudo bash monitoring/capture_traffic.sh
```

### (c) Evidence to capture

1. `queue-stats` showing bytes/packets per queue (queue 0 > queue 1 for critical traffic)
2. iperf output: high-priority flow gets more bandwidth than rate-limited helpline
3. Saved pcap in `monitoring/evidence/`
4. JSON stats snapshots before/after

### (d) Viva talking points

- Competing flows on shared bottleneck link (s1→s2).
- Queue statistics prove policy enforcement at data plane.
- tcpdump validates actual traffic on wire.

---

## Requirement 5: Performance Analysis (3 marks)

### (a) Files

- `performance/run_tests.py` — data collection → CSV/JSON
- `performance/generate_charts.py` — chart generation (separate script)

### (b) How to run

**Baseline (no QoS):**

```bash
# Terminal 1
DISASTER_QOS=0 ryu-manager controller/flow_manager_app.py

# Terminal 2: Mininet running
sudo bash qos/qos_teardown.sh

# Terminal 3
python3 performance/run_tests.py --mode baseline
```

**With QoS:**

```bash
# Terminal 1
DISASTER_QOS=1 ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py

# Terminal 2: Mininet running
sudo bash qos/qos_setup.sh

# Terminal 3
python3 performance/run_tests.py --mode qos
```

**Generate charts:**

```bash
python3 performance/generate_charts.py
# Charts saved to performance/charts/
```

If host namespaces aren't available, print manual commands:

```bash
python3 performance/run_tests.py --manual
```

Preview charts with sample data:

```bash
python3 performance/generate_charts.py --sample
```

### (c) Evidence to capture

1. `performance/results/perf_baseline.csv` and `perf_qos.csv`
2. `performance/charts/throughput_comparison.png`
3. `performance/charts/latency_comparison.png`

Expected trend (with QoS under contention):

- High-priority throughput **maintains or improves**
- Low-priority throughput **drops** (rate-limited to ~5 Mbps max)
- High-priority latency **lower** than low-priority under congestion

### (d) Viva talking points

- Same test duration/bandwidth in both conditions for fair comparison.
- Separate collection vs chart scripts (reproducible pipeline).
- Baseline = best-effort; QoS = policy-driven differential service.

---

## Assumptions Summary (for examiner)

1. **Flat /24 subnet** (10.0.0.0/24) — simplicity for Review 1; VLANs later.
2. **DPIDs** s1=1, s2=2, s3=3 — Mininet default assignment order.
3. **Bottleneck demo on s1-eth1** — both Hospital and PublicHelpline egress via s1.
4. **iperf2** used (Mininet default); iperf3 works with minor command changes.
5. **Port numbers** are symbolic application identifiers, not real protocol standards.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Switches not connecting | Check RYU running on port 6633; `ovs-vsctl show` |
| pingall fails | Ensure `flow_manager_app.py` or `base_switch_app.py` is running |
| QoS has no effect | Run `qos_setup.sh` after Mininet starts; verify with `dump-queues s1` |
| REST API connection refused | Include `ryu.app.ofctl_rest` in ryu-manager command |
| Permission denied | Mininet and OVS commands require `sudo` |

---

## License / Submission

College networking project — Review 1 foundational SDN infrastructure.
Extend `flow_manager_app.py` in later reviews for failover, attacks, and anomaly detection.
