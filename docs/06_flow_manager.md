# Document 6 — The Flow Manager (`flow_manager_app.py`)

## 6.1 Why This File Exists

`base_switch_app.py` makes all hosts reachable, but it applies no disaster-specific
policy. `flow_manager_app.py` replaces it with:

1. **All of the base learning-switch behavior** (MAC learning is still needed for
   traffic not covered by proactive rules)
2. **Five proactive flow rules** reflecting the disaster scenario
3. **QoS queue assignment** on a per-packet basis
4. **A periodic stats collection loop** to gather evidence for Review
5. **A `delete_flow` helper** for REST API integration

---

## 6.2 Constants Section — The Shared Source of Truth

```python
HOSPITAL_IP      = "10.0.0.2"
AMBULANCE_IP     = "10.0.0.4"
COMMAND_CENTER_IP = "10.0.0.1"
RESCUE_IP        = "10.0.0.5"
PUBLIC_HELPLINE_IP = "10.0.0.7"

PORT_AMBULANCE_DATA     = 5001
PORT_HOSPITAL_CRITICAL  = 5002
PORT_RESCUE_LOCATION    = 6001
PORT_PUBLIC_HELPLINE    = 8080
PORT_DRONE_VIDEO        = 9000

QUEUE_HIGH_PRIORITY = 0
QUEUE_LOW_PRIORITY  = 1
QUEUE_DEFAULT       = 0

SWITCH_S1 = 1
SWITCH_S2 = 2
SWITCH_S3 = 3

S1_PORT_TO_S3        = 2
S3_PORT_TO_S2        = 1
S2_PORT_H4_AMBULANCE = 4
S1_PORT_H1_COMMAND   = 3
```

**Why hardcode these at the top?**
If a future review changes an IP or port, you change it in ONE place and every
function that uses it automatically updates. Without this, the same value (like
`"10.0.0.2"`) would be scattered across 10+ places in the code.

**The port number constants at the bottom (`S1_PORT_TO_S3 = 2`, etc.):**
These are the **physical switch port numbers** as Mininet assigns them. Mininet
numbers ports in the order links are added in `disaster_topo.py`. The comments
in the file explain the mapping:
```
s1: port 1=to s2, port 2=to s3, port 3=h1, port 4=h2, port 5=h7
s2: port 1=to s1, port 2=to s3, port 3=h3, port 4=h4
s3: port 1=to s2, port 2=to s1, port 3=h5, port 4=h6
```

---

## 6.3 The `__init__` Method — Initialization

```python
def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.mac_to_port = {}
    self.datapaths = {}
    self.qos_enabled = os.environ.get("DISASTER_QOS", "1") == "1"
    self.queue_config = load_queue_config()
    self.stats_thread = hub.spawn(self._request_stats_loop)
```

`self.datapaths = {}`
→ A dictionary of `{ dpid: datapath_object }` — stores references to every connected
  switch. Needed by the stats loop which polls all switches periodically.

`self.qos_enabled = os.environ.get("DISASTER_QOS", "1") == "1"`
→ Reads the `DISASTER_QOS` environment variable. If it's `"1"` (default), QoS
  queue assignments are included in all actions. If `"0"`, queue actions are skipped.
  This allows running a **baseline test** (without QoS) using the same controller:
  ```bash
  DISASTER_QOS=0 ryu-manager controller/flow_manager_app.py
  ```

`self.queue_config = load_queue_config()`
→ Reads `qos/queue_config.json` and stores it. This is documentation/metadata —
  the actual OVS queue setup is done by `qos_setup.sh`, not by the controller.

`self.stats_thread = hub.spawn(self._request_stats_loop)`
→ Ryu uses a cooperative multitasking library called `eventlet`. `hub.spawn()`
  starts a background task (like a thread) that periodically polls stats.
  The stats appear in the Ryu terminal logs every 10 seconds.

---

## 6.4 Flow Installation Helpers

### `actions_with_queue()`
```python
def actions_with_queue(self, datapath, port, queue_id=QUEUE_DEFAULT):
    parser = datapath.ofproto_parser
    actions = []
    if self.qos_enabled:
        actions.append(parser.OFPActionSetQueue(queue_id))
    actions.append(parser.OFPActionOutput(port))
    return actions
```

This is the key function for QoS. It builds an action list:
1. `OFPActionSetQueue(queue_id)` — Assigns the packet to OVS queue 0 or 1.
   **This action must come BEFORE the output action.**
2. `OFPActionOutput(port)` — Then send the packet out the specified port.

If `qos_enabled=False`, only the output action is included — no queue assignment.

### `delete_flow()`
```python
def delete_flow(self, datapath, match=None):
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    mod = parser.OFPFlowMod(
        datapath=datapath,
        command=ofproto.OFPFC_DELETE,
        out_port=ofproto.OFPP_ANY,
        out_group=ofproto.OFPG_ANY,
        match=match if match else parser.OFPMatch(),
    )
    datapath.send_msg(mod)
```

Sends a `FlowMod` with `command=OFPFC_DELETE` — telling the switch to remove
matching rules. Used by the REST API demo (`manual_flow_insertion.sh delete`).

---

## 6.5 The Five Disaster-Scenario Flow Rules

These are the heart of `flow_manager_app.py`. They are installed by
`install_scenario_flows()` immediately when each switch connects.

### Rule 1: Hospital Priority Path (s1)

```python
if dpid == SWITCH_S1:
    match = parser.OFPMatch(
        eth_type=0x0800,       # IPv4 packet
        ipv4_src=HOSPITAL_IP,  # From 10.0.0.2 (Hospital)
        ipv4_dst=AMBULANCE_IP, # To 10.0.0.4 (Ambulance)
    )
    actions = self.actions_with_queue(datapath, S1_PORT_TO_S3, QUEUE_HIGH_PRIORITY)
    self.add_flow(datapath, 500, match, actions)
```

**What this does:**
On switch s1, when a packet comes from the Hospital (10.0.0.2) going to the
Ambulance (10.0.0.4), route it toward s3 (port 2 of s1), NOT toward s2 directly.

**Why this path?**
This creates an **explicit, dedicated path** for Hospital→Ambulance traffic:
`s1 → s3 → s2 → h4(Ambulance)`. It avoids the direct `s1 → s2` link, which might
be congested with other traffic. In a real scenario, this path could be a
physically separate, higher-quality cable dedicated to medical communications.

**The QoS part:** `actions_with_queue(datapath, S1_PORT_TO_S3, QUEUE_HIGH_PRIORITY)`
also sets the packet to **Queue 0** (high-priority, 40–50 Mbps guaranteed).

---

### Rule 2: Hospital Relay on s3

```python
if dpid == SWITCH_S3:
    match = parser.OFPMatch(
        eth_type=0x0800,
        ipv4_src=HOSPITAL_IP,
        ipv4_dst=AMBULANCE_IP,
    )
    actions = self.actions_with_queue(datapath, S3_PORT_TO_S2, QUEUE_HIGH_PRIORITY)
    self.add_flow(datapath, 500, match, actions)
```

**What this does:**
On switch s3, the same Hospital→Ambulance traffic (which Rule 1 directed here)
is forwarded toward s2 (port 1 of s3). Together, Rules 1 and 2 create the
complete dedicated path `s1 → s3 → s2`.

**Why a separate rule on s3?**
Without this, when the packet reaches s3, the switch would have no rule for it
and send a PacketIn to the controller. With this rule installed proactively,
s3 handles it instantly.

---

### Rule 3: Ambulance Data TCP/5001 (s2)

```python
if dpid == SWITCH_S2:
    match = parser.OFPMatch(
        eth_type=0x0800,
        ip_proto=6,            # TCP
        ipv4_dst=AMBULANCE_IP, # To 10.0.0.4
        tcp_dst=PORT_AMBULANCE_DATA,  # On port 5001
    )
    actions = self.actions_with_queue(datapath, S2_PORT_H4_AMBULANCE, QUEUE_HIGH_PRIORITY)
    self.add_flow(datapath, 600, match, actions)
```

**What this does:**
On s2, any TCP packet destined for the Ambulance on **port 5001** is routed directly
to h4 (Ambulance) on port 4 of s2, with high-priority queue assignment.

**Why port 5001 specifically?**
Port 5001 represents "ambulance patient data" — the most time-critical flow
(think real-time patient vitals). This rule has **priority 600**, the highest in
the system, meaning it is checked before ANY other rule.

**Why on s2 specifically?**
The Ambulance (h4) is connected to s2. Flow rules for delivering to a host
are installed on the switch that host is directly connected to.

---

### Rule 4: Public Helpline Rate Limiting (s1)

```python
if dpid == SWITCH_S1:
    match = parser.OFPMatch(
        eth_type=0x0800,
        ip_proto=6,
        ipv4_src=PUBLIC_HELPLINE_IP,   # From 10.0.0.7
        ipv4_dst=COMMAND_CENTER_IP,    # To 10.0.0.1
        tcp_src=PORT_PUBLIC_HELPLINE,  # Source port 8080
    )
    actions = self.actions_with_queue(datapath, S1_PORT_H1_COMMAND, QUEUE_LOW_PRIORITY)
    self.add_flow(datapath, 400, match, actions)
```

**What this does:**
On s1, traffic from the Public Helpline going to the Command Center on port 8080
is routed directly to h1 (Command Center) but put into **Queue 1** (max 5 Mbps).

**Why this is critical to the disaster scenario:**
During a disaster, thousands of citizens call the public helpline simultaneously.
Without this rule, their traffic floods the network and starves hospital/ambulance
communications. Queue 1 caps this traffic at 5 Mbps — still functional for the
helpline, but completely prevented from consuming the full 50 Mbps access link.

---

### Rule 5: Rescue Team Location Updates (s3)

```python
if dpid == SWITCH_S3:
    match = parser.OFPMatch(
        eth_type=0x0800,
        ip_proto=17,               # UDP
        ipv4_src=RESCUE_IP,        # From 10.0.0.5 (RescueTeam)
        udp_src=PORT_RESCUE_LOCATION,  # Source port 6001
    )
    actions = self.actions_with_queue(datapath, datapath.ofproto.OFPP_FLOOD, QUEUE_HIGH_PRIORITY)
    self.add_flow(datapath, 550, match, actions)
```

**What this does:**
On s3, UDP packets from the Rescue Team on port 6001 are flooded to all ports
(since the destination might vary) but assigned to high-priority Queue 0.

**Why UDP for location updates?**
Location updates are small, frequent packets. UDP is used (rather than TCP) because:
- UDP has no connection overhead (no handshake).
- If a single location packet is lost, the next one arrives shortly anyway.
- GPS location updates do not need guaranteed delivery — they need **low latency**.

**Why OFPP_FLOOD?**
Rescue teams send location updates to multiple recipients (Command Center, Police,
Hospital). Using FLOOD lets the packet reach all of them, while still benefiting
from the high-priority queue.

---

## 6.6 The `classify_queue()` Function — Per-Packet QoS in the Learning Path

```python
def classify_queue(self, pkt):
    if not self.qos_enabled:
        return QUEUE_DEFAULT

    ip = pkt.get_protocol(ipv4.ipv4)
    if not ip:
        return QUEUE_DEFAULT

    # High priority: hospital, ambulance, rescue traffic
    if ip.src in (HOSPITAL_IP, AMBULANCE_IP, RESCUE_IP):
        return QUEUE_HIGH_PRIORITY
    if ip.dst in (HOSPITAL_IP, AMBULANCE_IP) and ip.proto in (6, 17):
        tcp_pkt = pkt.get_protocol(tcp.tcp)
        udp_pkt = pkt.get_protocol(udp.udp)
        if tcp_pkt and tcp_pkt.dst_port in (PORT_AMBULANCE_DATA, PORT_HOSPITAL_CRITICAL):
            return QUEUE_HIGH_PRIORITY
        if udp_pkt and udp_pkt.src_port == PORT_RESCUE_LOCATION:
            return QUEUE_HIGH_PRIORITY

    # Low priority: public helpline bulk
    if ip.src == PUBLIC_HELPLINE_IP:
        return QUEUE_LOW_PRIORITY

    return QUEUE_DEFAULT
```

**What this function does:**
This runs during `packet_in_handler` — when the MAC-learning path kicks in for
traffic NOT covered by the proactive rules. It inspects the actual packet layers
to decide which queue the traffic belongs to.

**Why is this needed if we already have proactive rules?**
The proactive rules cover specific well-known flows. But if, for example, the
Hospital sends some other type of traffic (not to the Ambulance, not on port 5002),
that traffic will hit the MAC-learning path. This function ensures it still gets
the correct queue even through the reactive path.

---

## 6.7 The Stats Collection Loop

```python
def _request_stats_loop(self):
    while True:
        for dp in list(self.datapaths.values()):
            self._request_port_stats(dp)
            self._request_flow_stats(dp)
        hub.sleep(10)
```

This background loop runs every 10 seconds. For each connected switch, it sends:
- `OFPPortStatsRequest` — asks for bytes/packets per physical port
- `OFPFlowStatsRequest` — asks for the current flow table with byte/packet counters

The replies are handled by:
```python
@set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
def port_stats_handler(self, ev):
    ...
    self.logger.debug("PORT dpid=%s port=%s rx_bytes=%s tx_bytes=%s", ...)

@set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
def flow_stats_handler(self, ev):
    ...
    self.logger.debug("FLOW dpid=%s priority=%s packets=%s bytes=%s", ...)
```

These log at `DEBUG` level — visible with `ryu-manager --verbose`. This provides
the controller-side evidence for Requirement 4 (traffic monitoring).

---

## 6.8 The REST API Integration — `ryu.app.ofctl_rest`

When you start the controller with:
```bash
ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
```

Ryu loads **two apps simultaneously**: the built-in `ofctl_rest` app (which provides
an HTTP REST API on port 8080) AND your `FlowManagerApp`.

The `ofctl_rest` app automatically exposes endpoints that allow external tools
(like `curl` or any monitoring dashboard) to:
- Query flow tables from any switch
- Add flow rules
- Delete flow rules

Your controller doesn't need any special code for this — the REST API operates
as a separate Ryu application alongside yours.
