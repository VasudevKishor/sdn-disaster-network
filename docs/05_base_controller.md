# Document 5 — The Base Controller (`base_switch_app.py`)

## 5.1 Purpose of This File

`base_switch_app.py` is the **starting-point controller** — a classic MAC-learning
switch. It makes the network functional (all hosts can ping each other), but it
applies no QoS and no scenario-specific routing.

Think of it as the "factory default" SDN controller. The more advanced
`flow_manager_app.py` extends and replaces it for the full demo.

---

## 5.2 The Full Annotated Code

```python
#!/usr/bin/env python3
```
This "shebang" line tells Linux to run this file using the Python 3 interpreter.

---

```python
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
```

**What these imports do:**

| Import | What it provides |
|--------|-----------------|
| `app_manager` | The base class `RyuApp` that all Ryu controllers inherit from |
| `ofp_event` | All OpenFlow event types (PacketIn, SwitchFeatures, etc.) |
| `CONFIG_DISPATCHER` | State flag: switch just connected, sending its config |
| `MAIN_DISPATCHER` | State flag: switch is fully connected and operating normally |
| `set_ev_cls` | Decorator to register a function as an event handler |
| `ofproto_v1_3` | Constants and class definitions for OpenFlow 1.3 |
| `packet, ethernet` | Packet parsers to decode raw bytes into structured objects |

---

```python
class BaseLearningSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
```

- `BaseLearningSwitch` inherits from `RyuApp` — this registers it as a Ryu application.
- `OFP_VERSIONS` tells Ryu: "Only accept switches speaking OpenFlow 1.3."
  If a switch tries to connect using a different version, Ryu will reject it.

---

```python
def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.mac_to_port = {}
```

- `mac_to_port` is the **MAC address table** — the core of the learning switch.
- It is structured as: `{ dpid: { mac_address: port_number } }`
- Example after a few packets:
  ```
  {
    1: { "00:00:00:00:00:01": 3, "00:00:00:00:00:02": 4 },
    2: { "00:00:00:00:00:03": 3, "00:00:00:00:00:04": 4 }
  }
  ```
  This means: on switch 1 (dpid=1), host with MAC `00:01` is reachable through port 3.

---

## 5.3 The switch_features_handler — First Contact

```python
@set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
def switch_features_handler(self, ev):
    datapath = ev.msg.datapath
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser

    self.logger.info("Switch connected: dpid=%s", datapath.id)

    match = parser.OFPMatch()
    actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
    self.add_flow(datapath, 0, match, actions)
```

**Line by line:**

`@set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)`
→ Register this function. It fires when a switch sends its "Features Reply" message —
  which happens immediately after the TCP connection is established.
  `CONFIG_DISPATCHER` means: only fire this during the initial config phase.

`datapath = ev.msg.datapath`
→ The `datapath` object represents the switch itself. It has methods to send
  messages back to the switch.

`ofproto = datapath.ofproto`
→ Contains OpenFlow constants like `OFPP_CONTROLLER` (the virtual port number
  that means "send to the controller"), `OFPCML_NO_BUFFER`, etc.

`parser = datapath.ofproto_parser`
→ Factory object for creating OpenFlow messages (`OFPMatch`, `OFPFlowMod`, etc.)

`match = parser.OFPMatch()`
→ An **empty match** — matches every packet. This is what makes this the
  "table-miss" (catch-all) rule.

`actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]`
→ The action is: "Send this packet to the controller."
  `OFPCML_NO_BUFFER` means: "Send the full packet content, not just a reference."

`self.add_flow(datapath, 0, match, actions)`
→ Install this rule with **priority 0** (lowest possible).

**Result:** Every switch gets a rule that says "if no other rule matches, ask me."

---

## 5.4 The add_flow Helper

```python
def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle=0, hard=0):
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser

    inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
    mod = parser.OFPFlowMod(
        datapath=datapath,
        buffer_id=buffer_id,
        priority=priority,
        idle_timeout=idle,
        hard_timeout=hard,
        match=match,
        instructions=inst,
    )
    datapath.send_msg(mod)
```

This is a reusable helper. You call it with a match + actions, and it builds and
sends the correct OpenFlow `FlowMod` message to the switch.

`OFPInstructionActions(OFPIT_APPLY_ACTIONS, actions)`
→ In OpenFlow 1.3, actions must be wrapped in an "Instruction" object.
  `OFPIT_APPLY_ACTIONS` means "apply these actions immediately" (as opposed to
  writing them to a metadata register for the next table).

`OFPFlowMod(...)`
→ The actual OpenFlow message that installs a flow rule.
  - `buffer_id` — If the switch buffered the triggering packet, reference it here
    so the switch forwards it without us having to re-send it.
  - `idle_timeout=0, hard_timeout=0` — Never expire.

`datapath.send_msg(mod)`
→ Serializes the `OFPFlowMod` to raw bytes and sends it over the TCP connection
  to the switch.

---

## 5.5 The packet_in_handler — Where Learning Happens

```python
@set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
def packet_in_handler(self, ev):
    msg = ev.msg
    datapath = msg.datapath
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    in_port = msg.match["in_port"]

    pkt = packet.Packet(msg.data)
    eth = pkt.get_protocols(ethernet.ethernet)[0]
    dst = eth.dst
    src = eth.src
    dpid = datapath.id
```

`@set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)`
→ Fire this function whenever a PacketIn message arrives (switch asking "what do I
  do with this packet?"). Only active after the switch finishes initial config.

`in_port = msg.match["in_port"]`
→ Which physical switch port did this packet arrive on?

`pkt = packet.Packet(msg.data)`
→ Parse the raw packet bytes into a structured Ryu `Packet` object.

`eth = pkt.get_protocols(ethernet.ethernet)[0]`
→ Get the Ethernet layer. Every packet on this network has an Ethernet header.

`dst = eth.dst, src = eth.src`
→ Extract the 6-byte destination and source MAC addresses.

---

```python
    self.mac_to_port.setdefault(dpid, {})
    self.mac_to_port[dpid][src] = in_port
```

**This is the learning step.**
- `setdefault(dpid, {})` — Initialize an empty dict for this switch if it's new.
- `mac_to_port[dpid][src] = in_port` — Record: "The source MAC of this packet
  arrived on port `in_port`, so that MAC is reachable through that port."

Example: If h1 (MAC=00:01) sends a packet in on port 3 of s1:
→ `mac_to_port[1]["00:00:00:00:00:01"] = 3`

Now the switch knows that to reach h1, it should use port 3.

---

```python
    if dst in self.mac_to_port[dpid]:
        out_port = self.mac_to_port[dpid][dst]
    else:
        out_port = ofproto.OFPP_FLOOD
```

- If we know the destination MAC → use the known port (unicast).
- If we don't know the destination → `OFPP_FLOOD` (send to all ports except the one
  it arrived on). The host will reply, and we'll learn its location then.

---

```python
    actions = [parser.OFPActionOutput(out_port)]

    if out_port != ofproto.OFPP_FLOOD:
        match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
        if msg.buffer_id != ofproto.OFP_NO_BUFFER:
            self.add_flow(datapath, 1, match, actions, buffer_id=msg.buffer_id)
            return
        self.add_flow(datapath, 1, match, actions)
```

- If we know the destination port (not flooding), **install a flow rule** so future
  packets to this same destination don't need to come to the controller.
- The match is `(in_port, eth_dst, eth_src)` — specific to this exact conversation.
- Priority 1 (above the table-miss rule at priority 0, below scenario rules).
- `buffer_id` optimization: if the switch buffered the packet, include the buffer ID
  in the FlowMod so the switch releases it immediately. Then `return` — we don't
  need to also send a PacketOut.

---

```python
    out = parser.OFPPacketOut(
        datapath=datapath,
        buffer_id=msg.buffer_id,
        in_port=in_port,
        actions=actions,
        data=msg.data,
    )
    datapath.send_msg(out)
```

For the **first packet** (before a flow rule exists), we send a `PacketOut` to tell
the switch exactly how to forward this one packet right now. The `FlowMod` above
handles all future packets of this flow.

---

## 5.6 Complete Sequence: First Ping from h1 to h2

```
1. h1 sends ARP "Who is 10.0.0.2?" → arrives at s1 port 3
2. s1 has no matching rule → PacketIn to Ryu
3. Ryu learns: MAC(h1) → port 3 on s1
4. Ryu doesn't know h2's MAC yet → actions = FLOOD
5. Ryu sends PacketOut(FLOOD) → ARP broadcast goes to all ports
6. h2 receives ARP, replies with "I am 10.0.0.2 / MAC=00:02"
7. h2's ARP reply arrives at s1 port 4 → PacketIn to Ryu
8. Ryu learns: MAC(h2) → port 4 on s1
9. Ryu knows MAC(h1) is at port 3 → installs flow rule
10. PacketOut sends ARP reply to h1 at port 3
11. h1 now has h2's MAC → sends ICMP ping (echo request)
12. ICMP arrives at s1 → Ryu now knows BOTH MACs → installs flow rule
13. All subsequent pings go directly s1-port3→s1-port4 with NO controller involvement
```

After step 12, the controller is entirely out of the loop for h1↔h2 traffic.
