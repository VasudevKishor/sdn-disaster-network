# Document 10 — End-to-End Walkthrough

This document traces exactly what happens, step by step, from the moment you
start the demo until you have your final evidence. Use it as a "simulation of
the simulation."

---

## Phase 1: Starting the Controller (Terminal 1)

```bash
cd /home/rithvikmukka/sdn-lab/sdn-project
../ryu/ryu-env/bin/ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
```

**What happens:**
1. Python starts the Ryu process.
2. `FlowManagerApp.__init__()` runs:
   - `mac_to_port = {}` — empty MAC table
   - `datapaths = {}` — no switches yet
   - `qos_enabled = True` (DISASTER_QOS not set → defaults to "1")
   - `stats_thread` spawned in the background (but immediately sleeps 10 seconds)
3. `ofctl_rest` app starts HTTP server on port 8080.
4. Ryu opens TCP port 6633 and waits for switch connections.

**Terminal output:**
```
loading app ryu.app.ofctl_rest
loading app controller/flow_manager_app.py
instantiating app FlowManagerApp
```

Nothing else happens yet — no switches connected.

---

## Phase 2: Starting Mininet (Terminal 2)

```bash
sudo python3 topology/run_network.py
```

**What happens, second by second:**

**t=0:** Mininet reads `DisasterResponseTopo` and begins creating the virtual network.

**t=1:** Three OVS bridges are created: s1, s2, s3.
Each is configured as `OpenFlow13` and set to connect to `127.0.0.1:6633`.

**t=2:** Mininet creates 7 network namespaces (h1–h7) and assigns IP/MAC addresses.

**t=3:** Mininet creates 10 virtual Ethernet pairs (veth pairs) for all links.
Each pair connects a switch port to either a host or another switch.

**t=4:** OVS switches connect to Ryu over TCP. For each switch:
- OVS sends an `OFPHello` message (protocol negotiation)
- Ryu responds with `OFPHello` confirming OpenFlow 1.3
- OVS sends `OFPFeaturesRequest`
- Ryu receives `EventOFPSwitchFeatures` → `switch_features_handler()` fires

**What `switch_features_handler()` does for s1 (dpid=1):**
1. Logs: `"Switch connected: dpid=1 (QoS=True)"`
2. `datapaths[1] = datapath_s1`
3. Installs table-miss rule (priority 0, match-all, action=controller)
4. Calls `install_scenario_flows(datapath_s1)`:
   - Installs **Rule 1** (Hospital→Ambulance priority path): `priority=500, ip src=10.0.0.2 dst=10.0.0.4, actions=[set_queue:0, output:2]`
   - Installs **Rule 4** (PublicHelpline rate-limit): `priority=400, tcp src=10.0.0.7 dst=10.0.0.1 tcp_src=8080, actions=[set_queue:1, output:3]`

**Same process fires for s2 (dpid=2):**
- Installs table-miss rule
- Installs **Rule 3** (Ambulance TCP/5001): `priority=600, tcp dst=10.0.0.4 tcp_dst=5001, actions=[set_queue:0, output:4]`

**Same process fires for s3 (dpid=3):**
- Installs table-miss rule
- Installs **Rule 2** (Hospital relay): `priority=500, ip src=10.0.0.2 dst=10.0.0.4, actions=[set_queue:0, output:1]`
- Installs **Rule 5** (RescueTeam UDP/6001): `priority=550, udp src=10.0.0.5 udp_src=6001, actions=[set_queue:0, output:FLOOD]`

**Mininet terminal at this point:**
```
*** Starting controller c0
*** Starting 3 switches
s1 s2 s3 ...
*** Waiting for switches to connect
s1 s2 s3
```

**t=6:** Mininet runs `net.pingAll()`. Every host pings every other host.

---

## Phase 3: The `pingAll` Deep Dive

**h1 pings h2 (both on s1):**

1. h1 generates ARP: "Who has 10.0.0.2? Tell 10.0.0.1"
2. Arrives at s1 on port 3 (h1's port).
3. s1 checks flow table:
   - Priority 600: no match (not TCP/5001)
   - Priority 550: no match (not from RescueTeam UDP/6001)
   - Priority 500: no match (not from Hospital IP 10.0.0.2)
   - Priority 400: no match (not from PublicHelpline)
   - Priority 1: none installed yet
   - Priority 0: MATCH (table-miss) → PacketIn to Ryu
4. Ryu receives PacketIn:
   - Learns: MAC(h1)=00:00:00:00:00:01 → port 3 on s1
   - dst MAC = ff:ff:ff:ff:ff:ff (broadcast) → FLOOD
   - PacketOut: flood out all s1 ports
5. ARP reaches h2 (port 4), h7 (port 5), s2 (port 1), s3 (port 2)
6. h2 replies with ARP: "I am 10.0.0.2 / MAC=00:00:00:00:00:02"
7. Reply arrives at s1 port 4:
   - PacketIn to Ryu
   - Ryu learns: MAC(h2)=00:00:00:00:00:02 → port 4 on s1
   - Ryu knows MAC(h1) is at port 3 → installs flow rule `priority=1, eth_dst=00:01, in_port=4 → output:3`
   - PacketOut sends ARP reply to h1 on port 3
8. h1 has h2's MAC → sends ICMP echo request
9. Arrives at s1 port 3, dst MAC=00:02:
   - No priority >1 rule matches (it's ICMP, not the specific IP flows)
   - PacketIn to Ryu again
   - Ryu knows MAC(h2) → port 4, installs flow rule
   - All future h1→h2 packets now go directly port 3→4 with NO controller

> **Note:** Since `autoStaticArp=True` was set in `run_network.py`, ARP is pre-populated.
> The actual first ping skips ARP and goes straight to ICMP. The above is the
> general sequence without `autoStaticArp`.

**h2 pings h4 (Hospital → Ambulance, crosses switches):**

1. h2 generates ARP for 10.0.0.4
2. ARP arrives at s1 port 4.
3. No proactive rule matches ARP (rules match on `eth_type=0x0800` which is IPv4, not ARP).
   ARP goes to Ryu, gets flooded.
4. Eventually h4 replies, MACs are learned, ARP is resolved.
5. h2 sends ICMP echo to 10.0.0.4 (ARP resolved to MAC 00:04).
6. ICMP arrives at s1 port 4:
   - `eth_type=0x0800` (IPv4), `ipv4_src=10.0.0.2`, `ipv4_dst=10.0.0.4`
   - s1 checks flow table:
   - **Priority 500 MATCHES:** Hospital IP → Ambulance IP → `set_queue:0, output:2` (toward s3)
   - Packet goes to s3 without hitting the controller!
7. Packet arrives at s3 on port 2 (s1→s3 link):
   - s3 checks flow table:
   - **Priority 500 MATCHES:** Same Hospital→Ambulance match → `set_queue:0, output:1` (toward s2)
   - Packet forwarded to s2 without hitting controller.
8. Packet arrives at s2:
   - It's an ICMP packet (not TCP), so priority-600 Ambulance rule doesn't match (needs TCP dst=5001).
   - MAC learning takes over → PacketIn to Ryu → Ryu installs `eth_dst=00:04 → output:4`
   - Packet delivered to h4.
9. Future ICMP h2→h4 packets: s1's priority-500 rule handles them. The path is locked in.

---

## Phase 4: QoS Setup (Terminal 2, while Mininet is running)

```bash
sudo bash qos/qos_setup.sh
```

This runs the `ovs-vsctl` commands that create Queue 0 and Queue 1 on `s1-eth1`.
OVS reconfigures the interface's traffic scheduler in the Linux kernel (HTB qdisc).

From this point, when a packet with `set_queue:0` arrives at s1's port 1 egress,
the Linux kernel places it in the high-priority token bucket. When Queue 1 packets
arrive, they go in the rate-limited bucket.

---

## Phase 5: Demonstrating the REST API (Terminal 3)

```bash
bash demo_scripts/manual_flow_insertion.sh demo
```

**Step 1: List flows on s1**
```bash
curl -X GET "http://127.0.0.1:8080/stats/flow/1"
```
Returns JSON with all installed flow entries — you can see the proactive rules
with their priorities and actions.

**Step 2: Add a manual flow (Police → Hospital)**
```bash
curl -X POST "http://127.0.0.1:8080/stats/flowentry/add" -d '{
    "dpid": 1, "priority": 700,
    "match": {"eth_type": 2048, "ipv4_src": "10.0.0.3", "ipv4_dst": "10.0.0.2"},
    "actions": [{"type": "OUTPUT", "port": 4}]
}'
```

Ryu's `ofctl_rest` receives this HTTP POST, constructs an `OFPFlowMod`, and sends
it to s1. The rule is installed immediately. You can verify by listing flows again.

**Step 3: Test the new rule**
In the Mininet CLI: `h3 ping -c 3 h2`
The ping should now use the manually pinned path (out port 4 of s1 = toward h2).

**Step 4: Delete the manual rule**
```bash
curl -X POST "http://127.0.0.1:8080/stats/flowentry/delete" -d '{
    "dpid": 1, "priority": 700,
    "match": {"eth_type": 2048, "ipv4_src": "10.0.0.3", "ipv4_dst": "10.0.0.2"}
}'
```

Rule is deleted from s1's flow table. Future h3→h2 traffic falls back to MAC learning.

---

## Phase 6: Performance Tests

**Baseline run:**
- Set `DISASTER_QOS=0`, restart Ryu, remove QoS with teardown script.
- `sudo python3 performance/run_tests.py --mode baseline` runs iperf+ping for both flows.
- Both flows compete fairly → each gets ~17–20 Mbps, ~25ms latency.
- Results saved to `perf_baseline.csv`.

**QoS run:**
- Set `DISASTER_QOS=1`, restart Ryu, apply QoS with setup script.
- `sudo python3 performance/run_tests.py --mode qos` runs the same tests.
- Hospital flow gets near full requested bandwidth (~22–25 Mbps, ~18ms).
- Helpline flow is capped at 5 Mbps, latency rises due to queuing (~35–50ms).
- Results saved to `perf_qos.csv`.

**Chart generation:**
```bash
python3 performance/generate_charts.py
```
Reads both CSVs and generates two PNG bar charts in `performance/charts/`.

---

## Summary: The Complete Picture

```
You write Python (flow_manager_app.py)
    ↓
Ryu translates it to OpenFlow messages
    ↓
OVS receives and installs the flow rules
    ↓
Mininet hosts generate traffic
    ↓
Traffic hits switch → matches a proactive rule → follows it (no controller)
    ↓
set_queue:0 → Linux HTB gives it guaranteed bandwidth
set_queue:1 → Linux HTB rate-limits it to 5 Mbps
    ↓
iperf measures what actually came through → saved to CSV
    ↓
matplotlib reads CSV → draws bar chart → visual proof
```

**Every component exists for a specific reason:**
- Triangle topology → redundant paths for future failover
- Proactive rules → zero latency for critical first packets
- QoS queues → guaranteed bandwidth separation under contention
- REST API → real-time policy changes without restarting
- Stats collection → evidence that policy is working
- Performance charts → quantified proof for the review panel
