# Document 2 — Tools and Environment

Before reading any code, you need to understand what every tool does and why
it was chosen.

---

## 2.1 Mininet — The Network Simulator

**What it is:** Mininet creates a complete virtual network on a single Linux machine.
It creates real Linux processes acting as "hosts" and connects them through virtual
network switches, all using your machine's actual kernel networking stack.

**Why it matters:** You don't need physical routers or switches. The `ping` commands,
`iperf` traffic tests, and `tcpdump` captures you run inside Mininet behave exactly
like they would on real hardware. This is called an **emulator**, not just a simulator.

**How it creates hosts:** Each virtual host (h1, h2, etc.) is a Linux **network
namespace** — a completely isolated network environment within the same machine.
When you type `h1 ping h2` in the Mininet CLI, it runs a real `ping` inside h1's
isolated environment.

**Key Mininet concepts used in this project:**

| Concept | What it does in this project |
|---------|------------------------------|
| `Topo` class | Defines the layout — how many switches, hosts, and links |
| `TCLink` | Adds bandwidth limits and delay to links (makes it realistic) |
| `OVSSwitch` | Creates Open vSwitch instances as the network switches |
| `RemoteController` | Tells Mininet to connect all switches to an external Ryu controller |
| `CLI(net)` | Opens an interactive command prompt where you can run commands on any host |

---

## 2.2 Open vSwitch (OVS) — The Smart Switch

**What it is:** Open vSwitch (OVS) is a software implementation of a network switch.
When Mininet creates virtual switches (s1, s2, s3), they are actually OVS instances
running on your Linux machine.

**Why OVS specifically?**
- OVS supports **OpenFlow** — the standard protocol for SDN switch control.
- OVS supports **QoS queues** — the traffic prioritization system this project uses.
  Not all virtual switches support this; OVS has the best Linux HTB queue support.
- OVS is production-grade software used in real cloud data centers.

**What OVS does:**
When a packet arrives at an OVS switch, the switch checks its **flow table** (a list
of rules the controller installed). If the packet matches a rule, it follows that
rule's action (forward out port 3, drop, queue it, etc.). If no rule matches, it
sends the packet to the Ryu controller asking "what do I do with this?"

---

## 2.3 Ryu — The SDN Controller

**What it is:** Ryu is an open-source SDN controller framework written in Python.
You write a Python class that inherits from `RyuApp`, define event handlers (like
`@set_ev_cls`), and Ryu handles all the low-level networking — connecting to switches,
parsing OpenFlow messages, and sending commands back.

**Why Python?**
- Easy to read, modify, and extend.
- Has rich libraries (the Ryu project itself includes full OpenFlow 1.3 parsers).
- Your traffic classification logic, queue assignment, and stats polling are all
  regular Python functions — no complex networking code needed.

**How Ryu runs:**
```
$ ryu-manager controller/flow_manager_app.py
```
This starts the Ryu process, which opens a TCP server socket (port 6633 or 6653).
OVS switches connect to it and from that point, Ryu controls them.

---

## 2.4 OpenFlow — The Language Between Ryu and OVS

**What it is:** OpenFlow is the **protocol** (the language) that the Ryu controller
uses to talk to OVS switches. It defines:
- What messages a switch sends to the controller ("I don't know what to do with this packet")
- What commands the controller sends to switches ("Install this flow rule", "Forward this packet")

**Why version 1.3?**
OpenFlow 1.3 is the stable, widely-supported version that adds:
- Multiple flow tables
- Group tables (for multicast/multipath — useful in later reviews)
- Better QoS actions including `set_queue`

**The key OpenFlow messages used in this project:**

| Message | Direction | What it means |
|---------|-----------|---------------|
| `OFPSwitchFeatures` | Switch → Controller | "I just connected, here is my DPID" |
| `OFPFlowMod` | Controller → Switch | "Install/delete this flow rule" |
| `OFPPacketIn` | Switch → Controller | "A packet arrived I don't have a rule for" |
| `OFPPacketOut` | Controller → Switch | "Send this packet out this port" |
| `OFPFlowStatsRequest` | Controller → Switch | "Tell me all your current flow table entries" |
| `OFPPortStatsRequest` | Controller → Switch | "Tell me bytes/packets per port" |

---

## 2.5 How All Four Tools Work Together

Here is the **exact sequence** when you start the project:

```
1. You start Ryu:
   $ ./ryu/ryu-env/bin/ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
   → Ryu opens TCP port 6633 and waits.

2. You start Mininet:
   $ sudo python3 topology/run_network.py
   → Mininet creates 3 OVS switches (s1, s2, s3) and 7 host namespaces.
   → OVS switches connect to Ryu at 127.0.0.1:6633.

3. Ryu receives the switch connection:
   → switch_features_handler() fires for s1, then s2, then s3.
   → Ryu installs a "table-miss" rule on each switch (send unknown packets to controller).
   → Ryu installs custom disaster-scenario flow rules on each switch.

4. A host pings another host:
   → The packet arrives at the switch.
   → The switch checks its flow table.
   → If a rule matches → follows the rule immediately (no controller involved).
   → If no rule matches → sends PacketIn to Ryu → Ryu decides → installs new rule.

5. All subsequent packets of that flow:
   → Handled entirely by the switch (no Ryu involved), using the installed rule.
```

This is the **fundamental efficiency of SDN**: the controller only gets involved
for the first packet of each flow. After that, the switch handles everything at
hardware/kernel speed.
