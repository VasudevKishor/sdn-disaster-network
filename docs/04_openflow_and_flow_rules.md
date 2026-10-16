# Document 4 — OpenFlow and Flow Rules

## 4.1 What Is a Flow Rule?

A **flow rule** (or flow entry) is the fundamental unit of logic in an SDN switch.
Think of a flow table as a series of "if → then" statements burned into the switch:

```
IF packet destination MAC = 00:00:00:00:00:02
   AND packet came in on port 3
THEN send it out port 1
```

Each rule has three parts:
1. **Match fields** — The conditions the packet must meet
2. **Priority** — Higher number = checked first (like a chain of if-else)
3. **Actions** — What to do when the match is true

---

## 4.2 OpenFlow Match Fields

A match can be based on many packet attributes:

| Match Field | Example Value | What it checks |
|-------------|---------------|----------------|
| `in_port` | `3` | Which switch port the packet arrived on |
| `eth_dst` | `00:00:00:00:00:02` | Destination MAC address |
| `eth_src` | `00:00:00:00:00:01` | Source MAC address |
| `eth_type` | `0x0800` | Ethernet type = IPv4 |
| `ipv4_src` | `10.0.0.2` | Source IP address |
| `ipv4_dst` | `10.0.0.4` | Destination IP address |
| `ip_proto` | `6` | IP protocol: 6=TCP, 17=UDP |
| `tcp_dst` | `5001` | TCP destination port |
| `udp_src` | `6001` | UDP source port |

A rule with **more specific matches** is generally given a **higher priority**,
so it gets checked before the broader/fallback rules.

---

## 4.3 OpenFlow Actions

Actions tell the switch what to do with a matched packet:

| Action | What it does |
|--------|--------------|
| `OFPActionOutput(port)` | Send packet out a specific port number |
| `OFPActionOutput(OFPP_FLOOD)` | Send packet out all ports (broadcast) |
| `OFPActionOutput(OFPP_CONTROLLER)` | Send packet up to the Ryu controller |
| `OFPActionSetQueue(queue_id)` | Assign packet to OVS queue 0 or 1 (QoS) |
| `OFPActionSetField(...)` | Modify a packet header field |

---

## 4.4 The Table-Miss Entry — The Catch-All Rule

Every switch in this project starts with one special rule installed by Ryu:

```
Priority: 0       (lowest possible)
Match:    (nothing — matches every packet)
Action:   Send to controller
```

This is called the **table-miss entry**. It means: "If no other rule matches this
packet, ask the controller what to do."

Without this rule, unmatched packets would simply be **dropped silently** by OVS.
The table-miss entry is how the learning switch works — the first packet of any
new flow always reaches the controller.

---

## 4.5 Priority and Rule Matching — Full Flow Table for This Project

When a packet arrives at a switch, OpenFlow checks rules from **highest priority
to lowest**. Here is the complete picture for s1:

```
Priority 600  → Ambulance TCP/5001 → Queue 0 + Output Port 4    (Req 2 + QoS)
Priority 550  → RescueTeam UDP/6001 → Queue 0 + Flood           (QoS only)
Priority 500  → Hospital→Ambulance → Queue 0 + Output Port 2    (Priority path)
Priority 400  → PublicHelpline TCP/8080 → Queue 1 + Output Port 3 (Rate-limit)
Priority 1    → Learned MAC-based rules (added dynamically)      (MAC learning)
Priority 0    → Table-miss → Send to Controller                  (Default)
```

**Step through an example packet:**

> *Hospital (h2, 10.0.0.2) sends a TCP packet to Ambulance (h4, 10.0.0.4)*

1. Packet arrives at s1.
2. OVS checks priority 600 — no match (this packet isn't TCP/5001 dst port, it's a general packet).
3. OVS checks priority 550 — no match (not from RescueTeam, not UDP/6001).
4. OVS checks priority 500 — **MATCH**: `ipv4_src=10.0.0.2, ipv4_dst=10.0.0.4`.
5. Action: `set_queue(0)` (assign to high-priority queue) + `output(port 2)` (toward s3).
6. Packet is sent out s1's port 2, heading toward s3, bypassing the direct s1→s2 link.
7. **The controller is never involved** — this was handled entirely by the installed rule.

---

## 4.6 Why Flow Rules Are Installed "Proactively" vs "Reactively"

There are two strategies for when to install rules:

### Reactive (MAC Learning — Default Behavior)
1. Packet arrives.
2. Switch has no rule → sends PacketIn to controller.
3. Controller processes it, learns the source MAC, installs a flow rule.
4. All future packets of this flow are handled by the switch.

**Problem:** The *first* packet is always slow (round-trip to controller). For
video streams or critical medical data, this can cause noticeable delay.

### Proactive (Disaster Scenario Rules — What This Project Does)
1. Switch connects to Ryu.
2. Ryu **immediately** installs rules for Hospital, Ambulance, RescueTeam, etc.
3. When the first hospital packet arrives, the rule is **already there** — zero delay.

This is done in `install_scenario_flows()` inside `flow_manager_app.py`.
The proactive rules have priorities 400–600, which are higher than the reactive
MAC-learning rules (priority 1), so the scenario rules always win.

---

## 4.7 Flow Rule Timeouts

Each rule can have two optional timers:

- **`idle_timeout`**: Delete the rule if no matching packet has arrived in N seconds.
- **`hard_timeout`**: Delete the rule unconditionally after N seconds regardless.
- **Both = 0**: The rule is **permanent** until the controller explicitly deletes it.

In this project, all disaster-scenario flow rules use `idle=0, hard=0` (permanent).
This is intentional — during a real disaster, you don't want critical routing rules
disappearing just because traffic was briefly quiet.

---

## 4.8 The DPID — How Ryu Identifies Each Switch

Every OVS switch has a unique **Datapath ID (DPID)**. Mininet assigns DPIDs
sequentially: `s1=1`, `s2=2`, `s3=3`.

The controller uses the DPID to know **which switch it's talking to** and install
the correct rules:

```python
if dpid == SWITCH_S1:
    # Install Hospital priority path rule on s1
if dpid == SWITCH_S2:
    # Install Ambulance TCP/5001 rule on s2
if dpid == SWITCH_S3:
    # Install Hospital relay rule on s3
```

Without the DPID check, the controller would install all rules on all switches —
which would be incorrect (the Ambulance rule must only be on s2, where h4 is connected).
