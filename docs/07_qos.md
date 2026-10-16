# Document 7 — QoS: Queues and Traffic Prioritization

## 7.1 What Is QoS?

**Quality of Service (QoS)** is the ability to treat different types of traffic
differently — giving some traffic guaranteed bandwidth and low latency, while
restricting other traffic.

**The core problem it solves:**
A network link is shared. If two flows compete for the same link, and the link
is saturated, packets from BOTH flows get dropped randomly (tail-drop). With QoS,
you can ensure that critical traffic (hospital data) always gets through, while
non-critical traffic (helpline calls) is gracefully limited.

```
WITHOUT QoS (Best-Effort):              WITH QoS (HTB Queues):

  Hospital  ──────┐                       Hospital ──── [Queue 0: 40-50 Mbps] ──┐
  Helpline ───────┤──────── 50 Mbps         ^guaranteed bandwidth                 │──── 50 Mbps link
                  │link                   Helpline ──── [Queue 1: max 5 Mbps] ───┘
  → Both compete equally                     ^rate-capped
  → Both get dropped under load            → Hospital always wins
```

---

## 7.2 How OVS HTB Queues Work

OVS uses the **Hierarchical Token Bucket (HTB)** scheduler — a Linux kernel traffic
shaper. Here's how to think about it:

**Imagine a water tank (the link) being filled from two taps:**
- **Tap 0 (Queue 0 — High Priority):** Always allowed 40 Mbps. Can burst up to 50 Mbps
  if the link has spare capacity.
- **Tap 1 (Queue 1 — Low Priority):** Gets at least 1 Mbps (guaranteed not to starve),
  but capped at 5 Mbps maximum no matter what.

Even if Tap 1 tries to send 50 Mbps, the HTB scheduler will throttle it to 5 Mbps.
Meanwhile, Tap 0 can use everything else.

---

## 7.3 The Queue Configuration (`qos/queue_config.json`)

```json
{
  "queues": {
    "0": {
      "name": "high_priority_critical",
      "min_rate_bps": 40000000,
      "max_rate_bps": 50000000,
      "traffic_classes": ["Hospital critical TCP/5002", "Ambulance data TCP/5001", "Rescue location UDP/6001"],
      "openflow_queue_id": 0
    },
    "1": {
      "name": "low_priority_best_effort",
      "min_rate_bps": 1000000,
      "max_rate_bps": 5000000,
      "traffic_classes": ["Public helpline bulk TCP/8080"],
      "openflow_queue_id": 1
    }
  },
  "qos_port": {
    "switch": "s1",
    "port_name": "s1-eth1",
    "comment": "Egress from s1 toward s2 — shared bottleneck for demo"
  }
}
```

**What this file is:**
This is a **documentation artifact** — it describes the intended configuration in
structured JSON format. It is read by the controller at startup for reference, but
the actual OVS queues are created by `qos_setup.sh`, not by this file directly.

**The `qos_port` field:**
`s1-eth1` is the interface on s1 that faces s2. Both the Hospital (h2, on s1) and
the Public Helpline (h7, on s1) must route through this interface to reach the rest
of the network. This is the **shared bottleneck** — the point where contention occurs.
By applying queues here, we demonstrate QoS at the most meaningful point.

---

## 7.4 `qos_setup.sh` — The OVS Queue Commands

```bash
SWITCH="s1"
PORT="s1-eth1"
HIGH_MIN_RATE=40000000
HIGH_MAX_RATE=50000000
LOW_MIN_RATE=1000000
LOW_MAX_RATE=5000000
```

**Step 1: Clear any existing QoS (idempotent)**
```bash
ovs-vsctl --if-exists clear port "${PORT}" qos
ovs-vsctl --if-exists destroy QoS "${SWITCH}-qos"
```
`--if-exists` means: "don't error if it doesn't exist." This makes the script safe
to run multiple times without breaking things.

**Step 2: Create the QoS policy and queues**
```bash
ovs-vsctl set port "${PORT}" qos=@newqos -- \
  --id=@newqos create QoS type=linux-htb other-config:max-rate=100000000 queues=0,1 \
  -- set Queue queues=@q0 -- \
  --id=@q0 create Queue other-config:min-rate=40000000 other-config:max-rate=50000000 \
  -- set Queue queues=@q1 -- \
  --id=@q1 create Queue other-config:min-rate=1000000 other-config:max-rate=5000000
```

**Breaking this down:**

`set port "s1-eth1" qos=@newqos`
→ Attach a QoS policy to the `s1-eth1` port. `@newqos` is a local reference
  name (like a variable) used within this single `ovs-vsctl` command.

`create QoS type=linux-htb other-config:max-rate=100000000 queues=0,1`
→ Create an HTB QoS object with:
  - Maximum total bandwidth: 100 Mbps (matches the link capacity)
  - Two queues indexed 0 and 1

`create Queue other-config:min-rate=40000000 other-config:max-rate=50000000`
→ Queue 0: guaranteed 40 Mbps minimum, capped at 50 Mbps maximum.

`create Queue other-config:min-rate=1000000 other-config:max-rate=5000000`
→ Queue 1: guaranteed 1 Mbps minimum (so it never completely starves), capped
  at 5 Mbps maximum.

**Why are `min-rate` and `max-rate` both in bits/sec?**
Linux HTB and OVS work in bits per second. So 40 Mbps = 40,000,000 bps.

---

## 7.5 How the Controller Assigns Packets to Queues

The OpenFlow `set_queue` action is what actually assigns a packet to a queue.
In `flow_manager_app.py`, `actions_with_queue()` builds:

```python
actions = [
    OFPActionSetQueue(0),   # ← Assign to Queue 0 (high priority)
    OFPActionOutput(port),  # ← Then forward out this port
]
```

**Critical detail:** `OFPActionSetQueue` must come **before** `OFPActionOutput`.
OpenFlow actions in a list are executed in order. The queue assignment must happen
before the packet is handed to the port scheduler.

**What happens inside OVS when `set_queue(0)` is received:**
1. The packet is placed in the Queue 0 bucket for the `s1-eth1` interface.
2. The HTB scheduler determines when to actually transmit the packet.
3. If Queue 0 has traffic, it gets transmitted first (up to 50 Mbps).
4. Queue 1 only gets bandwidth after Queue 0's demand is met.

---

## 7.6 `qos_teardown.sh` — Removing QoS for Baseline Tests

```bash
ovs-vsctl --if-exists clear port "${PORT}" qos
```

This single command removes the QoS policy from the port. After this, all traffic
on `s1-eth1` reverts to **best-effort** (no differentiation). This is used before
running baseline performance tests (Requirement 5) to get an unmodified comparison.

---

## 7.7 Verifying QoS Is Active

After running `qos_setup.sh`, verify with:

```bash
# See all queue configurations in the OVS database
ovs-vsctl list Queue

# See queue statistics (bytes/packets per queue)
ovs-ofctl -O OpenFlow13 queue-stats s1

# See current flow table entries (look for set_queue actions)
ovs-ofctl -O OpenFlow13 dump-flows s1
```

In `dump-flows s1` output, correctly configured rules will show:
```
actions=set_queue:0,output:2
actions=set_queue:1,output:3
```

The `set_queue:0` confirms that packet is being routed to the high-priority bucket
before being sent out the port.
