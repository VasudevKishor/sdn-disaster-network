# SDN Disaster Response Network — Documentation Index

This folder contains a complete, from-scratch explanation of the project.
Read the documents **in order**. Each one builds on the previous one.

---

## Reading Order

| # | File | What it covers |
|---|------|----------------|
| 1 | [01_what_is_sdn.md](./01_what_is_sdn.md) | What SDN is, why normal networks fail in a disaster, and why this project uses SDN |
| 2 | [02_tools_and_environment.md](./02_tools_and_environment.md) | What Mininet, Ryu, Open vSwitch, and OpenFlow actually are and how they relate |
| 3 | [03_topology.md](./03_topology.md) | The network layout — every host, switch, link, and why each choice was made |
| 4 | [04_openflow_and_flow_rules.md](./04_openflow_and_flow_rules.md) | What an OpenFlow flow rule is, how a packet travels through the network |
| 5 | [05_base_controller.md](./05_base_controller.md) | Deep-dive into `base_switch_app.py` — the learning switch, line by line |
| 6 | [06_flow_manager.md](./06_flow_manager.md) | Deep-dive into `flow_manager_app.py` — custom rules, QoS mapping, stats |
| 7 | [07_qos.md](./07_qos.md) | What QoS is, how OVS HTB queues work, and what `qos_setup.sh` does |
| 8 | [08_monitoring.md](./08_monitoring.md) | How traffic is captured and stats are read to prove QoS is working |
| 9 | [09_performance.md](./09_performance.md) | How tests are run, how CSV data is collected, and how charts are generated |
| 10 | [10_end_to_end_walkthrough.md](./10_end_to_end_walkthrough.md) | A full run-through of what happens, second by second, when you start the demo |

---

## Quick Concept Map

```
 REAL WORLD PROBLEM                 HOW WE SOLVE IT
 ─────────────────                  ────────────────
 Disaster destroys links      →     Triangle switch topology (redundant paths)
 Critical traffic gets lost   →     OpenFlow flow rules + QoS queues
 No time to reconfigure       →     Ryu controller reconfigures everything centrally
 Need to prove it works       →     iperf tests + tcpdump + matplotlib charts
```
