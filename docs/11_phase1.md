# Phase 1 foundation

The accepted scope is in [PROJECT_DESCRIPTION_AND_PLAN.md](../PROJECT_DESCRIPTION_AND_PLAN.md).
Current progress and live-validation limits are in [HANDOFF.md](../HANDOFF.md).
The older numbered tutorials describe the original Review 1 and are historical.
This guide is authoritative for the Phase 1 commands and behavior.

## Working environment

Ubuntu 24.04.4; system Python 3.12.3, Mininet 2.3.0, OVS 3.3.4, iperf2 2.1.9,
system matplotlib 3.11.0. Ryu runs with Python 3.9.23 at
`../ryu/ryu-env/bin/python` and Ryu 4.34/eventlet 0.30.2.
`requirements-controller.lock` freezes that installed environment; do not install
it into system Python or upgrade/migrate the controller as part of Phase 1.
`monitoring/evidence/phase1_environment_2026-10-15.json` records actual versions.
Recreation on another machine still needs validation; this is a dependency snapshot.

## Implemented foundation

The topology remains `s1--s2--s3` with seven endpoints in `10.0.0.0/24`.
No redundant mesh or gateway is enabled. Inspect the shared inventory with:

```bash
python3 config/lab.py
```

| Switch | Port bindings |
|---|---|
| s1 | 1=s2, 2=h1 CommandCenter, 3=h2 Hospital, 4=h7 PublicHelpline |
| s2 | 1=s1, 2=s3, 3=h3 Police, 4=h4 Ambulance |
| s3 | 1=s2, 2=h5 RescueTeam, 3=h6 Drone |

The scenario controller answers ARP for configured endpoints at their local
attachment. An h2 request for h4 gets h4's configured MAC. IPv4 frames follow
the inventory path s1 port 1, then s2 port 4. Return frames use s2 port 1 and s1
port 3. There is no TTL decrement or MAC rewriting because this is still one
subnet, not routing. The later gateway prototype must test those transformations.

Unknown identities, inconsistent source IP/MAC/ingress, invalid ARP, unknown
broadcast, IPv6 and fragmented IPv4 are dropped. All registered endpoint pairs
may communicate. This is limited static attachment admission; service permissions,
incident profiles, alternate legitimate attachments and cryptographic identity
are not implemented. The legacy queue classifier still trusts configured endpoint
roles/service fields; it must not be presented as the final application policy.

No ARP/broadcast is flooded by the scenario controller. Learned IPv4 rules include
the inputs to queue classification and expire after 30 idle seconds. They also
match `ip_frag=0` using the OVS/Nicira field to exclude fragments even after a
non-transport flow is cached. An owned priority-65000 fragment drop also protects
manual REST rules, since Ryu's legacy REST converter does not accept `ip_frag`.
This extension requires OVS and is not a claim of
portability to every OpenFlow 1.3 implementation. Ownership
cookies scope reconnect deletion. This is ordered cleanup, not verified atomic
policy application or complete controller-outage reconciliation.
The educational base learning app remains available on the chain, permits flooding,
ages learned entries and invalidates a moved MAC. Use the scenario app for checks.

## Offline checks (no root)

From the project root:

```bash
python3 scripts/validate_phase1.py
python3 -m unittest -v tests.test_foundation
../ryu/ryu-env/bin/python -m unittest -v tests.test_controller
../ryu/ryu-env/bin/python -m unittest -v tests.test_protocol
```

The validator includes failure/overlap/evidence regressions, serialized fragment
match prerequisite checks, controller/wire checks, and a ten-second real iperf/ping
process check on loopback with temporary ports. It saves raw offline evidence;
loopback does not validate topology or QoS. The controller suite serializes real
Ryu packets and OpenFlow messages. None of these starts Mininet or changes the
system OVS dataplane. Do not use root-level `test_ping.py`
or `test_loop.py`: they are preserved user files with interactive/global cleanup
behavior. Discover only inside `tests/` if using unittest discovery.

## Privileged acceptance

Three repetitions passed and their saved evidence was inspected on 2026-10-16.
The implemented Phase 1 foundation gate is satisfied. Details and run directories
are recorded in [HANDOFF.md](../HANDOFF.md); the audit is saved at
`monitoring/evidence/phase1_live_audit_2026-10-16.json`. This validates the named
foundation checks, not later incident-policy or comparative-performance claims.

```bash
sudo python3 -m tests.lab_harness --repetitions 3
```

Stop your own interactive lab normally before running this. The harness refuses
existing named lab bridges/interfaces. It starts its own controller on an unused
loopback port, uses real Host objects rather than `ip netns` names, warms ARP,
checks 100 probes for each registered direction, injects 100 spoofed-IP and 100
spoofed-MAC/IP probes, captures at the receiver with three allowed control packets
to distinguish actual rejection from misrouted replies or duplicate delivery,
checks legitimate recovery and secure fail-mode, runs concurrent traffic, saves
evidence and cleans its network, controller, queues and QoS rows. A failure gives
a nonzero exit status and a `failure.txt`; a run has `PASS` only after cleanup.
It never calls `mn -c`, `killall`, `pkill` or a global OVS destroy operation.
OpenFlow error reports or controller tracebacks fail the harness. Both managed
queue byte counters must increase during the QoS workload at the shared egress.
Permitted probes reject duplicates and use the frozen probe count. Spoof probes
re-pin their permanent target neighbour after the MAC change and require all
attempts without local delivery errors, so failed ARP is not sufficient evidence.
The spoof address replaces the original by flushing IPv4 first, then adding and
reading back the new /24. Adding a secondary and deleting the primary can remove
both addresses on Linux, causing `ping: bind: Cannot assign requested address`.
`spoof_address.json` records the verified sender address before the probes.

Raw snapshots, controller logs, traffic results and cleanup reports are saved
under `monitoring/evidence/phase1/` in distinct timestamped directories. Finite
tests establish only their named cases. Live duplication/broadcast counters,
actual queue usage/rate caps, reconnect behavior, and clean repetitions remain
separate checks when those mechanisms change. Broader failure/service-policy
matrices belong to later phases.

## Measurements and shaping

The performance CLI owns its lab; do not also start the interactive topology:

```bash
sudo python3 performance/run_tests.py --mode baseline
sudo python3 performance/run_tests.py --mode qos
python3 performance/run_tests.py --manual
```

h2->h4 TCP/5001 and h7->h4 TCP/8080 start concurrently, each offering 35 Mbps.
Their shared bottleneck is **s2-eth4, 50 Mbps**. Probes run while both streams are
active. Results preserve sender-reported TCP throughput, actual ping reply counts,
mean and p95 RTT, and full client/probe output. These are preliminary mechanism
measurements, not the later urgent-message/comparative experiment.
Missing commands, failed processes and empty measurements are explicit errors.
The runner verifies both competing clients remain alive through every probe's
completion. `workload_<mode>.json` retains raw client, probe, listener and server
output on success or failure, before queue verification. A completed workload
file is not evidence that the enclosing live harness passed; check its PASS
marker and cleanup report. Failed workloads cannot become successful CSV data.

`qos/manage.py setup` configures both queues on all 11 actual egress ports.
Queue 0 has a 40 Mbps minimum and 50 Mbps maximum; queue 1 has 1 Mbps minimum and
5 Mbps maximum. Minimum is a contention guarantee, maximum is a cap. No explicit
HTB scheduling priority is configured. Root rates follow inventory capacity.
`qos/manage.py baseline` uses uncapped classes under the same OVS root rates,
with controller queue actions disabled by the harness. `qos_teardown.sh` detaches
and destroys owned state on all ports, including orphaned rows after bridge loss.
Foreign attached QoS is refused; setup failures may leave partial owned changes.

OVS HTB can replace TCLink switch qdiscs. The harness configures OVS in both
measurement modes, retains host-side TCLink shaping and records switch qdisc/class
state before/after. Do not assume switch netem delays survive. A standalone QoS
teardown does **not** restore an overwritten TCLink root qdisc; restart your owned
lab for a fresh TCLink-only baseline. The harness always builds a fresh topology.
Actual scheduler/rate/delay behavior remains subject to privileged checks.

Charts require both real files explicitly (each run has its own directory):

```bash
python3 performance/generate_charts.py --baseline <baseline-run>/perf_baseline.csv \
  --qos <qos-run>/perf_qos.csv --output-dir /tmp/phase1-charts
python3 performance/generate_charts.py --sample --output-dir /tmp/phase1-preview
```

`--sample` creates a separate labelled synthetic preview. Missing real files never
trigger synthetic fallback, and invalid/missing values never become zeros.

`config/acceptance.json` freezes initial budgets, including 100 permitted probes,
100 forbidden attempts, three repetitions, 99% urgent-message delivery, 250 ms
message p95 RTT, five-second backbone recovery and 30-second controller isolation.
The latter application/fault budgets are untested targets for later phases.

## Interactive learning/demo compatibility

```bash
../ryu/ryu-env/bin/ryu-manager --ofp-tcp-listen-port 6633 \
  --wsapi-host 127.0.0.1 ryu.app.ofctl_rest controller/flow_manager_app.py
sudo python3 topology/run_network.py
```

The controller port must be explicit: the recorded Ryu defaults to 6653, while
the interactive launcher uses 6633. Manual insertion derives its output and
identity match from inventory (Police->Hospital exits s1 port 3); deletion uses
an exact ownership cookie. REST access is local to the lab in this command.
Restart the owned topology when changing between the educational base app and
the scenario app; foreign cached rules are deliberately not erased on reconnect.
For verified measurements use the owned harness, not the legacy capture helper's
namespace/traffic automation. That helper remains historical and unvalidated.
