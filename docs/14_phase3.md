# Phase 3: incident policy and operator controls

Implementation is available on the existing 20-switch/24-endpoint network.
**The implemented Phase 3 gate passed three full live repetitions**, independently
audited on 2026-10-16. Phase 1 and Phase 2 also retain their audited live results.
The main specification remains [the agreed plan](../PROJECT_DESCRIPTION_AND_PLAN.md).

## Architecture and admission

`controller/policy_app.py` retains Phase 2's trusted attachment checks, proxy ARP,
gateway behavior and path-health traffic. Ordinary IPv4 misses are dropped;
there is no generic routed fallback around service permissions. The controller
publishes fresh port, attachment and connection-incarnation observations.
`policy.operator` runs in system Python and installs explicit OpenFlow 1.3 rules
and OVSDB queues using this graph. The pinned Ryu environment is unchanged.

`config/incident.py` declares a fixed catalogue: dispatch, medical, video,
shelter, bulk and prepared reporting services in each district, plus command
coordination between districts. `catalog` prints endpoint names, protocol and
both transport ports. Rules match trusted ingress, MAC/IP identities and the
exact TCP/UDP tuple, in both directions. Return permissions are stateless tuple
permissions, not TCP connection tracking or cryptographic identity. Registered
endpoints retain separately permitted ICMP diagnostics. Existing district/site
restrictions remain enforced. Prepared field endpoints `af3` and `bf3` start
unregistered; registration and granting their reporting service are separate.
This supports the fixed prepared identities, not arbitrary endpoint enrollment.

Each table entry below is **floor / desired protected allocation / hard maximum**,
in Mbps. Medical rates also apply to an explicitly granted reporting service.

| Class | Normal | Evacuation | Search and rescue | Degraded |
|---|---|---|---|---|
| Dispatch | 1 / 1 / 2 | 1 / 2 / 4 | 1 / 2 / 4 | 0.5 / 1 / 2 |
| Medical | 1 / 2 / 8 | 1 / 4 / 8 | 1 / 2 / 4 | 0.5 / 1 / 2 |
| Video | 1 / 6 / 12 | 0.5 / 2 / 4 | 2 / 12 / 18 | Disabled |
| Shelter | 0.5 / 1 / 4 | 1 / 4 / 8 | 0.5 / 1 / 2 | 0.25 / 0.5 / 1 |
| Bulk | 0.1 / 0.5 / 30 | 0.1 / 0.25 / 1 | 0.1 / 0.25 / 1 | Disabled |

The planner considers up to four deterministic simple paths with reverse paths,
respecting known usable links and attachments. It accounts jointly for both
directions on every switch egress, including access ports, and reserves 0.5 Mbps
per egress for health/diagnostics. Priority is dispatch, medical, shelter, video,
bulk; ties are deterministic by service name. Desired allocations can be reduced
to their floors. Lower-priority requests that do not fit are explicitly rejected.
An infeasible protected dispatch/medical capacity request or explicit constraint
rejects the operator transaction before deployment. A physical partition instead
reports unreachable services and allows surviving local services to continue.
This bounded greedy search is not a globally optimal network allocator.

Queues are per service/direction/version, with consistent minimum/maximum rates
along the route. OpenFlow rules select their declared queue. Health/diagnostics
use queue zero. A desired allocation is a protected admission budget; the maximum
allows opportunistic use. This is static admission and explicit profile selection,
not congestion-feedback adaptation or adaptive video encoding.

## Controlled deployment and recovery

The local journal stores expected rules, queue rates, configurations, versions,
remaining work and per-switch readback. A local file lock serializes operator and
watcher changes. Inspection distinguishes saved state from fresh switch readback.
There is no remote management API; local administrators and the session directory
are trusted. Mutations require root.

Queues for the old and candidate versions are prepared together; their combined
minimum rates must fit. Each port's OVSDB replacement is atomic, but the set of
ports and switches is not. Owned old queue rows remain until session teardown.
Foreign QoS is refused. Rule installation adds desired entries before strictly
deleting obsolete owned entries; identical match replacements are handled without
deleting the new entry. OpenFlow 1.3 is preserved; no OpenFlow 1.4 bundles or
multi-switch atomicity are claimed. Default order is backbone, aggregation, access.

Each completed switch is read back, including previously completed checkpoints
on resume. Failures retain an explicit partial state. Staging records observations;
changed physical attachments, ports or controller incarnations invalidate resume.
Rollback abandons the candidate first, restores the saved version where reachable,
and reports isolated switches as unverified. Restoring management connectivity
does not replay the abandoned candidate. `verify --local-readback` can inspect an
isolated local switch without authorizing an isolated write. Repeating rollback
after connectivity returns can finish recovery. During partial recovery the
watcher freezes until supported recovery conditions hold.

The watcher waits for a stable observed topology, then replans within the current
profile/version. It does not automatically change the incident profile. Stale
graph data retains installed state and reports an error, rather than claiming a
fresh verification. Queue IDs are limited to policy versions 1–900 to stay within
the installed Linux HTB queue range; no silent rollover or queue-ID reuse occurs.

OVS Linux HTB replaces the switch interface's previous root qdisc. Phase 3 saves
actual qdisc snapshots; Phase 2 TCLink delay settings must not be assumed to remain
effective on those switch interfaces after queue preparation. Kernel throughput,
recovery timing and packet behavior require the live gate below.

## Commands

From the project root, offline checks and assumed-topology preview are:

```bash
python3 scripts/validate_phase3.py
python3 -m policy.operator catalog
python3 -m policy.operator preview --assume-up --profile evacuation
```

The validation includes isolated real OVSDB, dummy OVS switches and pinned Ryu:
all twenty switches, all profiles, actual OpenFlow/OVSDB readback, checkpoints,
controller isolation and rollback. It never connects to the system OVS database
or creates the Mininet kernel dataplane. Dummy-port checks do not prove HTB rates.
The saved initial 2026-10-16 validation passed all 178 regression tests plus syntax
and whitespace checks: `monitoring/evidence/phase3_offline_2026-10-16.json`.
After the short-capture correction, all 179 tests passed; current evidence is
`monitoring/evidence/phase3_offline_capture_fix_2026-10-16.json`.

Run the live acceptance gate with no other lab active:

```bash
sudo python3 -m tests.phase3_harness --repetitions 3
```

For a quick check of just the current automatic-recovery defect, use:

```bash
sudo python3 -m tests.phase3_harness --recovery-only --repetitions 1
```

This still creates the full topology, health checks and application servers, but
skips the four profile matrices and onboarding tests. It writes `RECOVERY_PASS`,
never the full gate's `PASS`. Run the full three-repetition gate after this focused
check succeeds. Profile progress and the recovery stage now print to the terminal.

To check the checkpoint, isolated-switch rollback and video rate cap without
repeating profiles and link recovery:

```bash
sudo python3 -m tests.phase3_harness --updates-only --repetitions 1
```

It writes only `UPDATES_PASS`; the complete Phase 3 gate remains separate.
Both diagnostic modes reuse the same checks that the full gate runs.

For an interactive session with the normal profile, application servers and the
topology watcher, run:

```bash
sudo python3 -m topology.run_incident
```

Keep that terminal's Mininet CLI open. In another terminal, set `DIR` to the exact
session path printed by the command, then use:

```bash
sudo python3 -m policy.operator --state-dir "$DIR" status
sudo python3 -m policy.operator --state-dir "$DIR" inspect --details
sudo python3 -m policy.operator --state-dir "$DIR" verify
sudo python3 -m policy.operator --state-dir "$DIR" preview --profile evacuation
sudo python3 -m policy.operator --state-dir "$DIR" apply --profile evacuation
sudo python3 -m policy.operator --state-dir "$DIR" candidates --service district_coordination --avoid-switch bb1
sudo python3 -m policy.operator --state-dir "$DIR" constrain --service district_coordination --avoid-switch bb1
sudo python3 -m policy.operator --state-dir "$DIR" constrain --service district_coordination
sudo python3 -m policy.operator --state-dir "$DIR" register --endpoint af3
sudo python3 -m policy.operator --state-dir "$DIR" grant --service a_reporting
sudo python3 -m policy.operator --state-dir "$DIR" deny --service a_reporting
sudo python3 -m policy.operator --state-dir "$DIR" unregister --endpoint af3
sudo python3 -m policy.operator --state-dir "$DIR" rates --service a_video --rates 1000000 2000000 4000000
sudo python3 -m policy.operator --state-dir "$DIR" stage --profile evacuation
sudo python3 -m policy.operator --state-dir "$DIR" resume --pause-after 1
sudo python3 -m policy.operator --state-dir "$DIR" status
sudo python3 -m policy.operator --state-dir "$DIR" rollback
sudo python3 -m policy.operator --state-dir "$DIR" verify --local-readback
```

`constrain` without constraint flags clears that service's constraint. `--via`
accepts a comma-separated explicit switch sequence; `--avoid-link` accepts a
comma-separated switch pair. `--order` must name every affected switch exactly
once. `resume` without `--pause-after` continues the remaining switches.
`rollback --version N` selects a saved version when no incomplete deployment is
being recovered; recovery of a pending update targets the last committed version.
`--details` exposes complete snapshots. Exit from the Mininet CLI stops owned
processes and removes the session's owned queues/network while retaining evidence.

## Live gate and remaining phases

Each repetition checks all four profiles against actual rules and queue rates,
100 application attempts for every configured permitted/disabled service,
receiver pcaps with no duplicates or capture drops, blocked TCP SYNs, and selected
queue counter increases. It separately checks prepared registration/grant,
unrelated-service denial, wrong tuples and spoofed identities. An oversubscribed
protected request must fail without advancing the committed version.

Short service captures use tcpdump immediate mode as well as `-U`: `-U` flushes
the output file but does not disable packet batching before tcpdump receives
packets. Positive checks allow up to two seconds for the expected capture records
to drain before stopping the writer. The final stopped capture still must contain
exactly 100 requests and zero duplicates, with zero reported kernel capture drops;
a drain timeout cannot turn missing evidence into a PASS. The first Phase 3 live
attempt delivered and echoed all 100 bulk requests, but stopped its buffered
capture at request 79. See `monitoring/evidence/phase3_capture_audit_2026-10-16.json`.
That run remains failed evidence and does not satisfy the live gate.

It also checks a constrained inter-district route, link failure and an actual echo
recovery within five seconds while the link stays down; a paused deployment,
management-isolated switch, partial failure and rollback with a read-only local
check; successful service during isolation; and restoration without abandoned
candidate replay. A 20 Mbps offered UDP video stream must be capped under the
evacuation profile: receiver throughput between 1 and 5 Mbps for the declared
4 Mbps maximum (25% upper tolerance and a liveness lower bound). This is a cap
smoke test, not comparative urgent-application performance evidence.

Automatic repair updates changed switches first and still reads back unchanged
switches afterward. Recovery probes run immediately after fault injection, rather
than waiting for all twenty management readbacks before sending the first probe.
The application echo must still complete within five seconds; full reconciliation
has a separate 60-second management timeout and is required before the check can
pass. Raw attempts and both timings are saved in `automatic_recovery.json`.
The earlier `20261016T052936295267Z` run passed all 44 profile service checks and
onboarding/spoof checks, then timed out during reconciliation before measuring
application recovery. Its evidence is preserved; see
`monitoring/evidence/phase3_recovery_audit_2026-10-16.json`.

Management isolation preserves the bridge's primary-controller rows and changes
their targets temporarily to a reserved local TCP port with no listener. The
original targets are restored on every exit path, without replacing controller
UUIDs. Do not use `del-controller` for this scenario: OVS clears its flow tables
when the last primary controller is removed, even with secure fail-mode. The
test requires real saved service rules on the isolated switch and verifies them
locally immediately after disconnection and again after partial rollback. It
also sends 100 coordination echoes through that isolated switch, alongside local
dispatch checks. It never writes through the local socket while the controller
is disconnected. The 27 affected regressions, including actual private OVS/Ryu
isolation with nonempty rules, passed; report:
`monitoring/evidence/phase3_isolation_fix_tests_2026-10-16.json`.
Run `20261016T054432958250Z` recovered its application in 4.78 seconds and completed
full readback in 6.19 seconds, then exposed this destructive isolation setup.
That failed run is preserved; diagnosis is in
`monitoring/evidence/phase3_isolation_audit_2026-10-16.json`.

Raw evidence is under `monitoring/evidence/phase3-live/`; PASS requires process,
controller, queue and network cleanup. Complete runs `20261016T055931961676Z`,
`20261016T060538530066Z` and `20261016T061145167359Z` passed independent inspection:
54 service checks each, 4,500 successful echoes and 900 denied attempts per run,
zero captured duplicates/leaks and zero kernel capture drops; 40 selected-queue
counter checks per run; application recovery in 4.649–4.791 seconds; populated
isolated-switch rollback readback and live coordination; receiver video 3.89 Mbps
under its 4 Mbps maximum; and clean teardown. Audit:
`monitoring/evidence/phase3_live_audit_2026-10-16.json`.
No unchanged live rerun is needed. Phase 4 still must build/rehearse the four complete
student demonstrations, including the bounded two-switch/one-class rollback
exercise; this harness's full-profile checkpoint is supporting infrastructure.
Phase 5 comparative workloads/charts and Phase 6 presentation/rehearsal remain.
