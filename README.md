# SDN emergency communications project

The agreed project is [PROJECT_DESCRIPTION_AND_PLAN.md](PROJECT_DESCRIPTION_AND_PLAN.md).
Read [HANDOFF.md](HANDOFF.md) for current progress, tested results and remaining gates.
**Phases 1–3 have passed their implemented live gates.** The expanded
two-district network has 20 switches and 24 endpoints. Phase 3 passed three full
live repetitions, independently audited on 2026-10-16. The four simplified Phase 4
demo helpers are implemented; their live rehearsal gate is pending. No unchanged
Phase 3 rerun is needed. Commands and student scripts are in the
[Phase 4 runbook](docs/15_phase4_demos.md).
Profiles, service permissions, joint capacity admission, queues, controlled
deployment/rollback and local operator commands are described in the
[Phase 3 guide](docs/14_phase3.md).

Validate the new congestion demo first, then all four demos and resets:

```bash
sudo python3 -m tests.phase4_harness --demo congestion --repetitions 1
sudo python3 -m tests.phase4_harness --repetitions 3
```

For separate student-operated commands on one running lab:

```bash
sudo python3 -m demo_scripts.session
```

Use its printed session directory with `demo_scripts.control` in a second
terminal; follow the runbook's setup, injection, inspection, correction,
verification and reset sequence for each member.

The preserved Phase 1 topology is a three-switch **chain**, `s1--s2--s3`, with seven
hosts in `10.0.0.0/24`. The third core link remains disabled. This is the small
foundation, not the completed disaster-response system.

[Phase 1 guide](docs/11_phase1.md) documents commands, packet journeys, static
admission limits, shaping interactions, evidence and acceptance thresholds.
Older numbered tutorials in `docs/` describe historical Review 1 behavior.

## Environment

Use system Python for Mininet/charts and the existing Python 3.9 Ryu environment:

```bash
python3 --version
../ryu/ryu-env/bin/python --version
mn --version
ovs-vsctl --version
iperf --version
```

The recorded system is Ubuntu 24.04.4, Python 3.12.3, Mininet 2.3.0, OVS 3.3.4
and iperf2 2.1.9. Controller packages are frozen in
[requirements-controller.lock](requirements-controller.lock); the existing
environment uses Ryu 4.34 and eventlet 0.30.2. Do not install that lock into system
Python. No controller framework migration is needed.

## Automated checks

Run from the project root:

```bash
python3 -m unittest -v tests.test_foundation
../ryu/ryu-env/bin/python -m unittest -v tests.test_controller tests.test_protocol
```

The first command covers inventory, measurement validation, chart safety, QoS
ownership and harness guards. The second uses real Ryu packets/OpenFlow messages
and a temporary local controller/peer. These do not start Mininet or alter OVS.

Privileged live checks own their network and controller and refuse occupied lab
resources:

```bash
sudo python3 -m tests.lab_harness --repetitions 3
```

The Phase 1 command above already passed three audited repetitions. For the new
Phase 3 implementation, run:

```bash
python3 scripts/validate_phase3.py
sudo python3 -m tests.phase3_harness --repetitions 3
```

Phase 3's complete live command also passed three audited repetitions; saved audit:
`monitoring/evidence/phase3_live_audit_2026-10-16.json`.
Use these commands for affected regressions after changes. Do not run the old root-level `test_loop.py`
or `test_ping.py` against an active lab; their interactive/global cleanup behavior
is preserved but excluded from the new tests.

## Interactive lab

Terminal 1:

```bash
../ryu/ryu-env/bin/ryu-manager --ofp-tcp-listen-port 6633 \
  --wsapi-host 127.0.0.1 ryu.app.ofctl_rest controller/flow_manager_app.py
```

Terminal 2:

```bash
sudo python3 topology/run_network.py
```

The scenario app uses configured unicast paths, proxy ARP and static endpoint
bindings. Registered endpoint pairs may communicate; inconsistent addresses or
attachments are rejected. This is not cryptographic authentication or the later
service-policy engine. The educational base learning app remains chain-only.

Inspect bindings and manual rules:

```bash
python3 config/lab.py
bash demo_scripts/manual_flow_insertion.sh list
bash demo_scripts/manual_flow_insertion.sh add
bash demo_scripts/manual_flow_insertion.sh delete
```

Police->Hospital exits s1 port **3**. Manual rules derive bindings from inventory.
QoS setup and teardown manage all **11** actual egress ports with owned OVSDB rows:

```bash
sudo bash qos/qos_setup.sh
sudo bash qos/qos_teardown.sh
```

OVS HTB may replace TCLink switch qdiscs. Teardown does not reconstruct TCLink
shaping; restart the owned lab for a fresh TCLink-only configuration.

## Measurement

The performance CLI starts its own scoped lab; close the interactive lab first:

```bash
sudo python3 performance/run_tests.py --mode baseline
sudo python3 performance/run_tests.py --mode qos
python3 performance/run_tests.py --manual
```

Both streams converge on **s2-eth4**, a 50 Mbps egress to h4. Each offers 35 Mbps.
Equivalent commands in an already running Mininet CLI are:

```text
mininet> h4 iperf -s -p 5001 &
mininet> h4 iperf -s -p 8080 &
mininet> h2 iperf -c 10.0.0.4 -p 5001 -t 12 -b 35M &
mininet> h7 iperf -c 10.0.0.4 -p 8080 -t 12 -b 35M &
mininet> h2 ping -c 40 -i .1 10.0.0.4
```

Run probes while both streams are active. The automated runner records full raw
client/probe output, loss, mean/p95 RTT and throughput in timestamped directories.
It verifies queue counter changes on the shared egress. Invalid/missing data fails.
These are foundation measurements, not the final adaptive-policy comparison.

Generate charts with the two actual result paths:

```bash
python3 performance/generate_charts.py --baseline <baseline-run>/perf_baseline.csv \
  --qos <qos-run>/perf_qos.csv --output-dir /tmp/phase1-charts
python3 performance/generate_charts.py --sample --output-dir /tmp/phase1-preview
```

Synthetic previews use separate labelled directories. They never replace measured
CSV files; missing values do not become zeros. Initial acceptance budgets live in
[config/acceptance.json](config/acceptance.json).

## Phase 2 prototype

The separate gateway and usable-path active-backup prototype is described in
[docs/12_phase2.md](docs/12_phase2.md). It preserves the Phase 1 lab above.

```bash
python3 scripts/validate_phase2.py
sudo python3 -m tests.phase2_harness --repetitions 3
```

The strengthened prototype has three inspected live passes, including remote-path
monitoring and uplink/access-switch/blackhole recovery. These commands do
not implement the later four student-led demos.

## Phase 2 expanded topology

The expanded inventory has **20 switches, 24 endpoints and two districts**.
See [the expanded guide and port map](docs/13_expanded_topology.md) for addressing,
permissions, failover dependencies, resource thresholds and evidence.

```bash
python3 scripts/validate_phase2.py
python3 -m config.expanded
sudo python3 -m tests.expanded_harness --repetitions 3
```

The expanded live command checks all 552 directed endpoint pairs with receiver
captures, spoof rejection, critical-site failover, aggregation/backbone faults,
partition recovery, resource headroom and owned cleanup. Three full live passes
were independently audited on 2026-10-16; the Phase 2 gate is satisfied. See the
guide for results and the feasible-load scope. No unchanged rerun is needed.
