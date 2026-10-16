# Document 1 — What Is SDN and Why Do We Need It?

## 1.1 The Problem: Traditional Networks During a Disaster

Imagine a major earthquake has just hit a city. Emergency response agencies —
hospitals, police, ambulance services, rescue teams — all need to communicate
urgently. They share a single underlying network infrastructure.

In a **traditional (non-SDN) network**, every network device (router, switch)
contains its own hardcoded logic. Traffic is forwarded using fixed tables that were
configured before the disaster.

**This causes three critical failures:**

### Failure 1: Static Routing
When a physical cable or switch gets destroyed by the disaster, the network
simply stops working on that path. Rerouting requires a network engineer to
**manually log into every affected device** and change its configuration.
During a live disaster, nobody has time for that.

### Failure 2: No Traffic Priority
Traditional switches treat all traffic equally (called "best-effort"). A
citizen using a public helpline to ask general questions gets the same network
resources as a hospital sending a patient's vital signs. Under congestion,
critical traffic gets dropped randomly.

### Failure 3: Slow Reaction
If an attacker tries to flood the network (DoS attack) or probe critical
infrastructure during the chaos, a traditional switch cannot detect and block
it without manual intervention at each device.

---

## 1.2 The Solution: Software Defined Networking (SDN)

SDN fundamentally changes the architecture by **separating the brain from the body**:

```
Traditional Network                  SDN Network
──────────────────                   ───────────
 Switch A  (has its own brain)        ┌─────────────────────┐
 Switch B  (has its own brain)        │   Ryu Controller    │  ← The ONE brain
 Switch C  (has its own brain)        │   (your Python app) │
                                      └────────┬────────────┘
                                               │  OpenFlow protocol
                                     ┌─────────┼─────────┐
                                  Switch A  Switch B  Switch C
                                  (dumb)    (dumb)    (dumb)
                                  Just follows instructions from controller
```

| Concept | Traditional | SDN |
|---------|-------------|-----|
| Decision making | Inside each switch | Centralized in the Controller |
| Changing behavior | Log into every device | Change one Python file |
| Prioritization | Not possible | Controller installs priority rules |
| Reaction to failure | Manual reconfiguration | Controller reroutes automatically |

---

## 1.3 Why This Project Specifically Needs SDN

This project simulates the **National Disaster Management Authority (NDMA)** network.
Here is why each SDN feature maps to a real requirement:

### Requirement → SDN Feature Mapping

**"Hospitals must always get bandwidth"**
→ The Ryu controller installs OpenFlow rules that **route hospital traffic through a
dedicated path** and assign it to a **high-priority queue with guaranteed bandwidth**.
No traditional switch can do this without manual configuration on every device.

**"Public helpline should not choke the network"**
→ The controller installs a **rate-limiting rule** for public helpline traffic (max
5 Mbps), so even when thousands of citizens call simultaneously, they cannot consume
the bandwidth reserved for ambulances.

**"Network must survive physical link damage"**
→ The topology is built as a **triangle** (3 switches with 3 links forming a loop).
If one link dies, an alternate path exists. The controller can be extended (in later
reviews) to automatically detect the failure and reroute — something impossible in
traditional networks without per-device reconfiguration.

**"Be able to change rules in real time during the crisis"**
→ The Ryu controller exposes a **REST API** (via `ryu.app.ofctl_rest`). A human
operator can issue a `curl` command from any laptop and instantly insert, modify, or
delete flow rules on any switch in the network — in seconds, not hours.

---

## 1.4 A Mental Model to Remember

Think of SDN like an **airport control tower**:
- The **planes** (data packets) just follow instructions.
- The **runways** (switches) direct planes based on what the tower tells them.
- The **control tower** (Ryu controller) has the full picture and makes all decisions.
- If a runway closes (link failure), the control tower immediately reroutes planes
  to alternate runways without the planes needing to know anything changed.

In contrast, a traditional network is like every plane having its own onboard
computer that decides its own route — with no coordination between them.
