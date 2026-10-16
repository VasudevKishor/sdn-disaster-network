# Document 3 — The Network Topology

## 3.1 What Is a Network Topology?

A "topology" is just the layout of the network — which devices exist, how they
are connected, and what the links between them look like (speed, delay).

This project's topology is defined in `topology/disaster_topo.py`.

---

## 3.2 The Physical Layout

```
                    [ s1 ] ──────────── [ s2 ]
                   /      \            /      \
                  /   100M  \        /  100M   \
                 /   10ms    \      /   10ms    \
                /             \ /               \
           [ s3 ]──────────────                 |
             /                                  |
           50M                                 50M
           5ms                                 5ms
            |                                   |
     h1  h2  h7                           h3  h4
  (s1 hosts)                           (s2 hosts)
         |
        h5 h6
     (s3 hosts)
```

**In plain text:**
- Three switches: s1, s2, s3 — connected in a **triangle** (each connected to both others).
- Seven hosts distributed across those switches.
- Two types of links: inter-switch (backbone) and host-to-switch (access).

---

## 3.3 The Switch Triangle — Why a Triangle?

The core switches are connected in a **triangle topology** (also called a "ring" or
"full mesh for 3 nodes"):

```
s1 ── s2
 \   /
  s3
```

**Every switch has TWO paths to every other switch.**
- `s1` can reach `s2` directly, OR via `s1 → s3 → s2`.
- `s2` can reach `s3` directly, OR via `s2 → s1 → s3`.
- `s3` can reach `s1` directly, OR via `s3 → s2 → s1`.

**Why does this matter for a disaster scenario?**
If a cyclone physically destroys the `s1 ↔ s2` link, traffic can still flow through
`s1 → s3 → s2`. The Ryu controller (in later reviews) can detect the link failure and
reroute all traffic automatically. The triangle gives us the **redundant path** we need.

> **Viva point:** A single-line topology (s1 — s2 — s3) has no redundancy. If the
> middle link fails, the network splits in half. The triangle prevents this.

---

## 3.4 The Host Map — Every Host Represents a Real Agency

```python
AGENCY_HOSTS = {
    "h1": {"name": "CommandCenter",   "ip": "10.0.0.1", "switch": "s1", "priority": "normal"},
    "h2": {"name": "Hospital",        "ip": "10.0.0.2", "switch": "s1", "priority": "high"},
    "h3": {"name": "Police",          "ip": "10.0.0.3", "switch": "s2", "priority": "normal"},
    "h4": {"name": "Ambulance",       "ip": "10.0.0.4", "switch": "s2", "priority": "high"},
    "h5": {"name": "RescueTeam",      "ip": "10.0.0.5", "switch": "s3", "priority": "high"},
    "h6": {"name": "Drone",           "ip": "10.0.0.6", "switch": "s3", "priority": "medium"},
    "h7": {"name": "PublicHelpline",  "ip": "10.0.0.7", "switch": "s1", "priority": "low"},
}
```

| Host | Agency | IP | Switch | Priority | Why this switch? |
|------|--------|-----|--------|----------|-----------------|
| h1 | Command Center | 10.0.0.1 | s1 | Normal | Central hub, s1 is the gateway switch |
| h2 | Hospital | 10.0.0.2 | s1 | **High** | Co-located with command for fast coordination |
| h3 | Police | 10.0.0.3 | s2 | Normal | Field operations, separate switch |
| h4 | Ambulance | 10.0.0.4 | s2 | **High** | Field-deployed with police; needs priority routing |
| h5 | Rescue Team | 10.0.0.5 | s3 | **High** | Remote deployment, on third switch |
| h6 | Drone | 10.0.0.6 | s3 | Medium | Co-deployed with rescue teams |
| h7 | Public Helpline | 10.0.0.7 | s1 | **Low** | High-volume but non-critical bulk traffic |

> **Why h7 (PublicHelpline) is on s1:** This is intentional. h7 and h2 (Hospital)
> share the same egress port on s1 toward the core. This creates a **contention point**
> on the `s1-eth1` link — which is exactly what the QoS demo uses to show that the
> hospital's high-priority queue gets more bandwidth even under congestion.

---

## 3.5 Link Parameters — Making It Realistic

Links are created with two types of parameters using Mininet's `TCLink`:

### Core/Backbone Links (switch-to-switch)
```python
self.addLink(s1, s2, cls=TCLink, bw=100, delay="10ms", max_queue_size=1000)
```
- `bw=100` → 100 Megabits per second (regional fiber backbone speed)
- `delay="10ms"` → 10 milliseconds propagation delay (distance between cities)
- `max_queue_size=1000` → can buffer up to 1000 packets before dropping

### Access Links (host-to-switch)
```python
access_kw = dict(cls=TCLink, bw=50, delay="5ms", max_queue_size=500)
self.addLink(h1, s1, **access_kw)
```
- `bw=50` → 50 Megabits per second (last-mile connection)
- `delay="5ms"` → 5ms delay (local connection is faster than backbone)

> **Why these numbers matter:** The `iperf` tests in Requirement 5 measure actual
> throughput. Without `TCLink` parameters, all links are unlimited and the QoS
> differentiation wouldn't be visible. With 50 Mbps access links and two flows both
> requesting 25 Mbps, the shared bottleneck creates real contention — which QoS then
> resolves.

---

## 3.6 The Traffic Port Map — What Each Port Represents

In the real disaster scenario, each type of data has a well-known port number.
The project uses these port numbers to match flows and apply QoS:

```python
TRAFFIC_PORTS = {
    "ambulance_data":     5001,  # TCP - patient data & vitals relay
    "hospital_critical":  5002,  # TCP - life-critical hospital commands
    "rescue_location":    6001,  # UDP - small GPS location packets
    "public_helpline":    8080,  # TCP - bulk citizen helpline traffic
    "drone_video":        9000,  # UDP - live aerial video stream
}
```

> **Important viva note:** These port numbers are **assumptions** made for this
> simulation. In a real deployment, actual hospital systems use HL7 or FHIR protocols
> on different ports. The numbers here are symbolic — they let us write flow rules
> that differentiate traffic types without needing real medical software.

---

## 3.7 How `disaster_topo.py` Is Structured

The file has three sections:

1. **`AGENCY_HOSTS` dict** — A constant mapping hosts to their names and priority.
   Used by the controller and demo scripts as a shared reference, so IPs and priorities
   are defined in ONE place only.

2. **`TRAFFIC_PORTS` dict** — Another shared constant for port numbers. Referenced
   by both the topology file and the controller's flow rules.

3. **`DisasterResponseTopo(Topo)` class** — The actual Mininet topology class.
   The `build()` method is called by Mininet when the network is created. It:
   - Calls `self.addSwitch()` for s1, s2, s3
   - Calls `self.addHost()` for h1–h7 with fixed IPs and MACs
   - Calls `self.addLink()` for every connection with TCLink parameters

4. **`topos` dict** — A Mininet convention. If you run:
   ```bash
   sudo mn --custom topology/disaster_topo.py --topo disaster
   ```
   Mininet reads this dict to find the `DisasterResponseTopo` class.

---

## 3.8 How `run_network.py` Launches Everything

`run_network.py` is the actual script you run. It:

```python
net = Mininet(
    topo=topo,                                          # Use our custom topology
    controller=RemoteController("c0", ip="127.0.0.1", port=6633),  # Connect to Ryu
    switch=OVSSwitch,                                   # Use Open vSwitch
    link=TCLink,                                        # Use bandwidth-limited links
    autoSetMacs=True,                                   # Auto-assign MAC addresses
    autoStaticArp=True,                                 # Pre-populate ARP tables
)
```

- `RemoteController` tells Mininet: "Don't create a built-in controller. The switches
  should connect to an external controller (Ryu) running at localhost:6633."
- `autoStaticArp=True` pre-fills ARP tables on all hosts, so ARP broadcasts don't
  confuse the flow rules during the demo.

After starting the network, it prints the agency map, calls `net.pingAll()` to verify
0% packet loss, then drops you into the interactive Mininet CLI (`mininet>`).
