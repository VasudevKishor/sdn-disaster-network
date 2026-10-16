# New-chat handoff: SDN emergency communications project

## Read this first

The agreed project specification is
[PROJECT_DESCRIPTION_AND_PLAN.md](PROJECT_DESCRIPTION_AND_PLAN.md).
Read it together with this handoff, then inspect the actual working tree before
editing. The specification describes the target system, not completed features.

This handoff records the decisions made after the specification was written.
The user accepted the existing plan and wants to proceed without another redesign
because preparation time is limited. The exact deadline has not been provided.

## Agreed objective

Build and rigorously test an academic project demonstrating how SDN translates
emergency incident priorities into coordinated routing, bandwidth, and access
policies across a managed IP network.

The scenario is flooding affecting two neighbouring districts. Some links survive;
others fail. Remaining or temporary backhaul may have lower capacity. Command
centres, hospitals, field bases, and shelters must retain the communication that
their available infrastructure can support.

The real-world scope is the managed IP network connecting sites and communication
gateways. We are not implementing public-safety radio, cellular infrastructure,
satellite equipment, or physical backup power. Do not claim that SDN itself
restores electricity, creates physical connectivity, or controls carrier networks.

SDN is the chosen programmable-control architecture, not the only possible
solution. Conventional routing, QoS, and network automation can implement many of
the individual functions. The project must demonstrate coordinated changes and
their measured benefits and costs.

## User requirements and decisions

- This is a four-member academic project with a presentation and live assessment.
- The professor repeatedly requested a complex topology and project. Complexity
  must have an operational purpose and be demonstrated through integrated
  behaviour, not only a larger diagram or ping tests.
- The professor may request an unknown, manageable network change or problem
  during the presentation. Provide general inspection/change commands and prepare
  the group to reason about them.
- Each member needs a prepared live problem-and-solution demonstration.
- Connect verified ideas from early SDN talks to implemented features.
- Test correctness, negative cases, failures, interactions, and recovery. Explain
  remaining limitations. Passing a finite test suite does not prove no loopholes
  exist.
- Keep the current plan. Treat review remarks as an implementation checklist;
  avoid adding technologies or reopening the overall scenario/design.
- Protect time for integration, comparative measurements, and rehearsal.
- The user asked whether the design is realistic and complex enough. We confirmed
  that the proposed scope is credible and sufficiently complex for an undergraduate
  project, conditional on actually implementing and measuring it. This was not a
  guarantee of the professor's marks or a claim about existing implementation.

## Architecture to build

| Component | Target |
|---|---|
| Districts | Two |
| Backbone switches | Four |
| Aggregation switches | Four, two per district |
| Site access switches | Twelve |
| Total switches | Twenty |
| Application endpoints | Approximately twenty-four |
| Sites | Command centre, hospital, field base, shelter in each district |
| Critical-site access | Two access switches at each command centre and hospital |
| Other site access | One access switch at each field base and shelter |

Each access switch connects to both district aggregation switches. Each aggregation
switch connects to two backbone switches. Use meaningful alternate paths and
unequal path capacities. Keep controller management separate from application
traffic. Build explicit site subnets and gateway/routing behaviour.

The earlier 16-switch layout becomes 20 by adding four critical-site access
switches. Make topology generation configurable so a smaller instance can prove
mechanisms before expansion. Keep 20 as the target; any material scope reduction
should be explained to the user rather than silently redefining completion.

Critical endpoints may connect through both access switches using active-backup
bonding in the same logical site network. Prototype this early. Merely putting
different hosts on two switches or different IP networks does not provide endpoint
failover. Independent power, endpoint power, upstream dependencies, and common
physical routes must be acknowledged in the real-world design.

Use Mininet, Open vSwitch, OpenFlow 1.3, and the existing Ryu environment initially.
Pin the working environment. Do not migrate controller frameworks without a
concrete blocker. No production-grade controller cluster is promised.

## Four demonstrations and shared story

Updated student-led designs are detailed in section 9 of the main specification:

1. Congestion: the student starts competing bulk traffic, diagnoses the shared
   egress bottleneck, applies an incident profile/allocation correction, and
   verifies urgent-message recovery while the offered load stays the same.
2. Constrained routing: the student disables a primary link, inspects automatic
   rerouting, and selects/applies a surviving, permitted, capacity-feasible route
   avoiding a specified backbone switch in both directions. The link stays down
   during verification; require an ordinary backup and a distinct usable option.
3. Endpoint onboarding: the student activates one prepared unregistered endpoint,
   inspects its binding and denied request, registers its authorised attachment,
   then separately grants one exact reporting-service permission. Unrelated
   services and IP/MAC impersonation from a wrong attachment remain blocked.
4. Partial-update recovery (simplified with user approval on 2026-10-16): reuse
   the full incident-profile update, pausing after verified V2 at the first switch;
   isolate a second populated switch before it changes, then resume and inspect
   partial status. Roll reachable switches back to saved V1 while the second stays
   isolated. This replaces the dedicated two-switch, single-class updater. Verify current
   states with clearly labelled read-only local inspection for the isolated
   switch; otherwise mark it unverified. Reconnect only during cleanup.

Connect these through one incident story: normal operation, evacuation declared,
primary link failure and a route constraint, onboarding a reporting endpoint
with restricted access, partial policy deployment during isolation, and recovery. Provide
scoped setup, fault, observation, correction, verification, and reset operations
for each demonstration; each must also be independently repeatable.

Student-led demonstration requirement (confirmed by the user): each student
diagnoses the triggered problem, chooses and applies a justified corrective
action, and verifies the outcome. Scripts support setup, fault injection,
inspection, execution, measurement, and reset; do not hide diagnosis and solution
inside a single automatic demo script. Keep intended controller automation such
as rerouting and access rejection. For security, do not permit an attacker as a
"fix"; use an authorised service-policy change when an operator action is needed.
The four demonstrations are suitable for the agreed undergraduate scope,
conditional on rigorous implementation, repeatable tests, measured evidence,
and student understanding; their quality is not yet established by live results.

Demo review update (2026-10-15): adopted the user's stronger proposed tasks and
aligned student effort to a 3-4 minute target with a prepared network. Use a
feasible profile/allocation, a small set of computed route candidates, one
prepared onboarding endpoint with separate registration/permission operations,
and one saved-snapshot rollback. Each retains separate injection, inspection,
correction and verification; no live coding or manual per-switch rule writing.
Equal student difficulty does not mean equal implementation effort: bounded
partial-update recovery remains the hardest mechanism. Measure policy consistency
there with per-switch evidence; serial 4/8 Mbps caps need not produce any throughput
change on rollback. The timing and difficulty targets still require rehearsal.
This is documentation work only: no controller, topology, script,
or test implementation was changed or live-validated. Existing phase status and
the agreed technology/topology scope remain unchanged. Implement the operator
capabilities in Phase 3 and rehearse/test these sequences in Phase 4; retain
automated end-to-end regression tests alongside student-operated demo tools.

## Implementation checklist from the accepted review

These points refine execution of the existing plan; they do not authorise scope
expansion or require rewriting the specification.

- **Gateways:** Decide exact subnets, gateway addresses/MACs, routing location,
  forward/return handling, and packet transformations. Explain an actual packet
  journey. Where OpenFlow implements routing, account for destination/source MAC
  changes, TTL handling, and ARP resolution.
- **Loops:** Implement controlled ARP/broadcast handling before activating the
  mesh. Proxy ARP for configured endpoints is the preferred starting approach.
  Do not rely on unrestricted flooding or permanently frozen MAC learning.
- **Policy algorithm:** Use a deterministic, explainable procedure: enforce service
  permissions, select candidate paths, account for all admitted protected rates on
  every directed link, then admit, reduce, or reject requests. Checking one request
  independently is insufficient.
- **Adaptation:** Distinguish operator-triggered profile changes, automatic failure
  response, and any optional congestion-triggered adjustments. Do not claim a
  feedback loop that is not implemented. Avoid oscillation after restoration.
- **QoS:** Verify actual output interfaces and queues where flows compete. Check
  interactions between Mininet TCLink shaping and OVS HTB configuration. Distinguish
  minimum allocation, maximum rate, and scheduling priority. Network rate limiting
  alone does not make a video sender adapt its encoding bitrate.
- **Security and failover:** Model the legitimate alternate attachment set for
  critical endpoints. Failover must not be rejected as spoofing, while an unrelated
  port must not be allowed to claim the same identity. Static binding is not
  cryptographic authentication.
- **Controller outage:** Explicitly verify OVS secure fail-mode, critical-rule
  timeouts, and reconnection behaviour. Avoid learning-switch fallback over the
  mesh. Test existing traffic and new traffic separately. Local fast-failover
  protection does not cover all remote failures or recompute capacity policies.
- **Partial updates:** Give policies versions and rules identifiable ownership.
  Validate, apply in a deliberate order, verify, and recover/roll back when needed.
  Do not claim atomic changes across multiple switches and OVSDB configuration.
- **Fair comparison:** Compare best effort, reasonably configured fixed QoS, and
  adaptive policy. Test a capacity-reducing failure and changing incident priorities.
  Fixed QoS may perform equally well in some conditions; report that honestly.
- **Measurement:** Preserve raw data; use concurrent traffic and real application
  measurements. Report on-time delivery, loss, and RTT together. Separate fault
  injection, detection, rule installation, and application recovery times. Three
  repetitions are the minimum gate; use more for central comparisons if feasible.

Keep the profiles and completion gates in the main specification. Reviewers
suggested optional cuts, but the user did not adopt a reduced project scope.

## Current repository state

Phase 1's implemented foundation gate is now satisfied: the user's three live
repetitions passed and their saved evidence was independently inspected on
2026-10-16. See the newest progress entry for exact results and limits. Preserve
the chain, controller, QoS, harness and all existing changes. Phase 2 has three
new saved prototype runs with PASS markers: strengthened identity checks and
usable-path failover passed three inspected repetitions. The small Phase 2
prototype is validated. The 20-switch/24-endpoint expansion now also passes three
full live repetitions, independently audited on 2026-10-16. **Phase 2's implemented
gate is satisfied.** No unchanged rerun is needed. Phase 3's incident-policy and
operator capabilities now also passed three complete live repetitions and an
independent evidence audit on 2026-10-16. **Phase 3's implemented gate is satisfied.**
Phase 4's simplified four student demo helpers are now implemented; their
focused congestion check passed; the full three-repetition rehearsal is next.
See [the Phase 3 guide](docs/14_phase3.md) and newest progress entry. Dated progress entries
below are historical; the newest entry supersedes their pending status.

The preserved foundation topology is a three-switch chain with seven hosts; the
expanded Phase 2 topology is separate. Historical numbered tutorials may still
describe the original triangle.
The repository contains a learning-switch app, a scenario flow-manager app, QoS
scripts, monitoring tools, performance scripts, and two ad hoc network tests.

Original review findings before Phase 1 (the progress record below identifies
implemented fixes and checks; this list preserves the baseline):

- Police-to-Hospital manual rule currently outputs to s1 port 4, but the current
  Hospital port is 3; port 4 is PublicHelpline.
- Learned fallback matches use ingress/MAC fields while queue classification uses
  more specific packet fields. An ARP-derived rule can preserve the wrong queue
  for later IP traffic.
- Existing performance flows do not share the claimed bottleneck; the runner
  executes them sequentially and measures ping afterwards.
- Host execution assumes named `ip netns` entries absent from the ordinary Mininet
  launch. Failed execution can still become saved empty measurements.
- Chart fallback can replace an existing real CSV with sample data when the other
  input is missing; missing measurements are also converted to zero.
- QoS setup configures multiple ports, but teardown clears only one.
- Permanent first-observation MAC binding is a workaround that obstructs recovery.
- The ad hoc tests launch interactive Mininet and include broad cleanup commands.
  Do not use them blindly against an existing running lab.

Checks actually performed previously:

- Python syntax for the nine existing Python files, shell syntax, and queue JSON.
- Topology construction without starting a network, confirming actual port maps.
- Synthetic packets through the controller in the existing Ryu environment,
  reproducing the queue/match inconsistency.
- Isolated checks of missing-namespace measurement handling, `--manual` requiring
  `--mode`, and sample-data overwrite using temporary files.

Full-topology connectivity, comparative throughput, remote-path failure recovery,
and large-topology resource validation remain open. The separate three-switch
Phase 2 local-carrier prototype has the limited live evidence recorded below.

## Environment and workspace cautions

- Workspace: `/home/rithvikmukka/sdn-lab/sdn-project`.
- System Python observed: 3.12.3. Mininet and matplotlib were importable there;
  Ryu and pytest were not.
- An existing Ryu environment was found at `../ryu/ryu-env/`, using Python 3.9.
  Controller imports and synthetic-packet checks worked there. Recheck availability
  before relying on it.
- Mininet, OVS tools, iperf, and tcpdump were present.
- Normal sandbox execution failed with
  `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`.
  Approved escalated commands were used for inspection and checks. Follow current
  tool permissions; earlier approvals are not a reason to bypass a new permission
  requirement. This environment issue is not a project defect.
- Preserve existing user changes. At handoff, modified tracked files included
  `README.md`, both controller apps, `demo_scripts/manual_flow_insertion.sh`,
  `qos/qos_setup.sh`, and `topology/disaster_topo.py`.
- Existing untracked material included `docs/`, `test_loop.py`, `test_ping.py`, and
  the new planning documents. Inspect current status because it can change.
- Existing tracked changes include trailing whitespace. Do not confuse those
  pre-existing findings with new work or perform unrelated cleanup.
- Do not commit, reset, discard user work, kill unrelated controllers, or clean a
  running Mininet environment merely to obtain a clean starting point.

## References still requiring work

The main specification contains real-world sources and the two supplied videos:

- <https://www.youtube.com/watch?v=W734gLC9-dw>
- <https://www.youtube.com/watch?v=YHeyuD89n1Y>

The videos themselves were not retrievable during planning. Specific quotations,
timestamps, and the first video's identity/content remain unverified. Do not
invent them. The main document separately cites ONF architecture material and an
original description of Shenker's talk.

Do not treat broad claims in pasted external reviews, such as universal statements
about production controllers, protocols, or deployments, as verified facts. Check
primary sources when using them in the final presentation.

## Next action and progress record

**Phase 1: Correct and stabilise the foundation** has passed its implemented
three-repetition gate. Phase 2's prototype and full 20-switch expansion also pass
their inspected three-repetition gates. Preserve this baseline. Phase 3 profiles,
capacity checks, service access, queues, deployment/recovery and operator commands
passed their three-repetition live gate and evidence audit. Phase 4's simplified
helpers use these controls; the focused congestion live check passed. Next are
three complete demo/reset repetitions and evidence audit. Do not reopen planning or expand scope.

Exact deadline and the team's prior gateway/bonding experience are unknown. Ask
briefly if needed while continuing the agreed implementation. Do not restart
the project-planning discussion just to obtain those answers.

| Work | Status at handoff |
|---|---|
| Scope and real-world framing | Agreed |
| Main specification | Written |
| Review remarks and execution priorities | Recorded here |
| Phase 1 fixes and harness | Foundation gate passed: 56 offline tests and three inspected live repetitions |
| Phase 2 gateway/bond prototype | Validated: three inspected live repetitions including usable-path faults, restoration and stronger spoof rejection |
| Expanded two-district topology | Implemented: 20 switches/24 endpoints; 143 offline tests and three audited full live repetitions; Phase 2 gate passed |
| Phase 3 incident-policy engine/operator controls | Gate passed: 179-test full offline baseline, latest 27 affected regressions including real OVS/Ryu, and three audited complete live repetitions |
| Phase 4 integrated student demonstrations | Implemented; 198 offline regressions and audited focused congestion/reset passed; full three-repetition rehearsal pending |
| Comparative network measurements | Not started |
| Final presentation and rehearsal | Not started |

After meaningful implementation milestones, update this progress record with
changed files, tests actually run, results, and remaining work. Keep the main
specification as the target and this file as the current handoff state.

### Phase 1 implementation progress (2026-10-15)

- Preserved the existing chain, corrected direct Hospital routing and command
  attachment, retained explicit `OFP_NO_BUFFER` handling and same-port rejection.
- Added `config/lab.py`: explicit shared switch/host/port/rate bindings. Topology,
  scenario forwarding, manual rules, QoS and workloads consume this inventory.
- Scenario controller now uses inventory-derived unicast paths and validated
  proxy ARP; no rescue flood rule or first-observation MAC freeze. Reactive IPv4
  rules include attachment, MAC/IP, protocol and transport classification fields.
  Rules carry an ownership cookie; reconnect clears only its own rules.
- Phase 1 admission permits registered endpoints and rejects inconsistent
  MAC/IP/attachment claims. It is not the Phase 3 service-permission system or
  cryptographic authentication. Base learning app remains tree-only educational
  functionality; the scenario controller is the supported foundation harness app.
- Added scoped QoS ownership and all-11-port setup/teardown. Missing/foreign
  interfaces or infeasible rates fail before setup mutation. Setup is sequential;
  a partial failure requires explicit cleanup and is not an atomic transaction.
- Replaced namespace assumptions and sequential collection with Mininet Host
  objects, owned foreground processes and concurrent traffic to h4. Both streams
  share s2-eth4 (50 Mbps), each offers 35 Mbps. Probes run under load; raw output,
  loss, mean and p95 RTT are retained. These are legacy probes, not the later
  urgent-message application. Failed data cannot be saved as successful CSV.
- Chart generation rejects missing/invalid/mismatched data. Synthetic previews
  live in separate labelled directories and cannot overwrite measured CSVs.
- Added `config/acceptance.json` to freeze starting test budgets and
  `tests/lab_harness.py` to own its network/controller/processes, refuse occupied
  lab resources, and clean only owned state. Root-level ad hoc tests are retained
  but are not part of this harness; do not run them against a live lab.
- Final offline validation: **42 tests passed** (24 system-Python foundation
  checks and 18 pinned-Ryu controller/wire checks). The latter include an actual
  short-lived Ryu controller, real OpenFlow 1.3 handshake/features exchange,
  proxy ARP packet-out and classified flow installation using a local emulated
  peer. This is a protocol smoke test, not OVS dataplane evidence.
- Parsed all 18 Python files without executing the unsafe ad hoc scripts;
  checked all five shell scripts, both configuration JSON files and
  `git diff --check`. All passed. Full raw validation outputs are in
  `monitoring/evidence/phase1_offline_2026-10-15.json`; the environment snapshot
  is `monitoring/evidence/phase1_environment_2026-10-15.json`. Both are explicitly
  exempted from artifact ignores. `requirements-controller.lock` records the
  installed Python 3.9 packages; rebuilding that environment elsewhere remains
  untested. System Mininet and chart dependencies remain separate.
- Regression work found and fixed the synthetic sample label mismatch and an
  IPv4-fragment cache bypass. Cached rules now require OVS `ip_frag=0`; an owned
  priority-65000 fragment drop also covers manual REST rules. The real Ryu REST
  converter silently discards `ip_frag`, so manual JSON deliberately excludes
  that field and relies on the controller's higher-priority fragment drop.
  This OVS/Nicira extension is not generic OpenFlow-switch portability.
- Live blocker confirmed: `sudo -n true` requires a password; OVSDB access as the
  current user is denied. No Mininet topology, throughput/failure experiment,
  gateway/bond prototype or root cleanup has been run in this implementation
  session. Do not mark the Phase 1 gate complete until live repetitions pass.
- Direct invocation of the unprivileged harness was also checked: it exits 1
  with an explicit root requirement, saves failure evidence, and starts no lab.
- QoS/TCLink interaction is explicit: the harness applies OVS egress shaping in
  both comparison modes and saves before/after qdisc and class state. OVS may
  replace switch TCLink netem; do not claim unchanged switch delays. Host TCLink
  shaping remains. Actual rates, queue counters, cleanup and repeated reset
  still require privileged validation before comparison claims.
- Updated `README.md` and added `docs/11_phase1.md` with current commands. Older
  user tutorials and root-level ad hoc tests are retained, not rewritten or run.
  In particular, Ryu defaults to 6653 in the recorded environment while the
  interactive network uses 6633; current launch commands specify 6633 explicitly.
  The harness chooses an unused loopback controller port automatically.

### Phase 2 prototype progress (2026-10-15)

- Added `config/phase2.py`, `topology/phase2_topo.py`, and
  `controller/phase2_app.py` without changing Phase 1 implementation files.
  The three-switch, three-endpoint prototype has distinct names/DPIDs, three
  explicit /24 site networks, virtual gateway IP/MACs, and one dual-attached
  active-backup command endpoint. Command/hospital communication is allowed;
  guest traffic is isolated. This fixed matrix is not Phase 3 service policy.
- Gateway proxy ARP validates configured sender identity and local attachment.
  Routed ingress rewrites source/destination Ethernet and decrements TTL once;
  downstream and independently generated reverse paths use exact expected
  ingress/address/protocol matches. Unknown traffic, IPv6 and fragments drop;
  no FLOOD/NORMAL action or mesh learning fallback is used. Physical peer wiring
  is trusted configuration; local port names/numbers and availability are checked.
- Only configured alternate attachments can move an endpoint identity. Bond
  gratuitous ARP refreshes owned return paths. Separate cookie ownership protects
  Phase 1/foreign rules. Refresh is sequential, downstream first, and not atomic;
  OpenFlow errors withdraw the affected switch from route computation.
- The bond prototype uses `miimon=100`, stable MAC, and
  `primary_reselect=failure`. **Only directly attached carrier loss is covered.**
  A remote uplink failure can leave the host NIC up; tests explicitly show this
  does not imply bond movement. Usable-path monitoring and actual access-switch
  disappearance/recovery still need implementation/validation before claiming
  the agreed resilience gate. Full topology and resource validation are pending.
- Added `tests/phase2_harness.py` with root/occupied-resource preflight and owned
  cleanup. Implemented live checks cover 100-probe allowed/isolated paths,
  receiver pcap with three legitimate controls plus 100 IP/100 MAC-IP spoof
  attempts after warmup, one TTL decrement and Ethernet rewrite, expired-TTL
  drops, local-link bond failover/restoration, sticky backup, errors and cleanup.
  Timing distinguishes observed bond change and successful warmup completion;
  it is not an exact first-recovered-packet measurement. These checks have
  **not run on live Mininet/OVS** in this session.
- Final offline validation: **64 tests passed**: 33 system-Python checks (24
  Phase 1 + 9 Phase 2 model/harness safety) and 31 pinned-Ryu checks (18 Phase 1
  + 12 Phase 2 controller + 1 Phase 2 wire integration). The new wire test runs
  a real Ryu process with three emulated OpenFlow 1.3 peers and verifies port
  descriptions, routing actions, proxy ARP and bond-mobility rule updates.
  This is not OVS dataplane or kernel-bond evidence.
- `scripts/validate_phase2.py` reruns these suites and repository syntax checks,
  saving raw output in `monitoring/evidence/phase2_offline_2026-10-15.json`.
  Python compilation runs in memory because an existing root-owned topology
  bytecode cache cannot be written by this user. No cache ownership was changed.
  Shell/JSON checks and tracked `git diff --check` also passed.
- `sudo -n true` still requires a password. Unprivileged invocation of the new
  live harness exits 1, saves explicit failure evidence under
  `/tmp/sdn-phase2-preflight/`, and starts no controller/network. No root cleanup,
  live fault, or privileged experiment was attempted. See `docs/12_phase2.md`.

### Next concrete work; do not reopen the plan

1. Preserve the three successful Phase 1 runs and their audit report recorded
   below. Its foundation gate is passed; rerun its regression checks when shared
   code changes. These QoS-only runs are not a best-effort/fixed/adaptive comparison.
2. Preserve the three strengthened Phase 2 PASS runs and audited results below.
   The prototype gate has passed; no unchanged rerun is needed. Retest affected
   mechanisms when shared configuration/controller/monitor code changes.
3. Preserve the three audited expanded runs below. The full Phase 2 gate is
   satisfied for its documented workload and fault set. Retest affected behavior
   when changing shared runtime code; an unchanged full rerun is unnecessary.
4. Implement Phase 3 as specified: incident profiles, joint directed-link capacity
   admission, exact service permissions, consistent queues, deliberate versioned
   updates/verification/recovery, and operator inspection/change commands. Include
   profile/version and identity changes in cache invalidation; the current route
   compilation cache assumes static Phase 2 inventory/admission. Then implement
   the four student-led demonstrations in Phase 4. Do not silently claim these
   features already exist or expand the agreed scope.

### Foundation follow-up and inspected live results (2026-10-16)

- Preserved existing tracked and untracked work; no commits, resets, global lab
  cleanup, new topology, incident-policy engine or demo features were introduced.
- Fixed a real OVS match prerequisite-order defect shared by both controllers:
  Ryu sorted `ip_frag` before EtherType. `controller/matches.py` preserves Ryu
  normalization and orders the extension after its prerequisite. Serialized-rule
  regression checks are in `tests/test_matches.py` and `tests/test_controller.py`.
  An isolated OVS 3.3.4 dummy-switch check reproduced both old-order rejections
  and accepted corrected Phase 1/2 messages; this is not a kernel QoS/bond test.
- Inspected the user's three Phase 2 PASS directories:
  `phase2-live/20261015T183723530984Z`, `20261015T183808275503Z`, and
  `20261015T183852976645Z` under `monitoring/evidence/`. Each has empty cleanup
  errors, no controller error markers, 100/100 forward/return probes before,
  during local-link failover and after restoration, and exactly three receiver
  requests at TTL 63. Bond polling observed the backup at about 0.114-0.117 s;
  successful warmup completed at about 0.169-0.173 s. These are polling/warmup
  timestamps, not exact application outage measurements.
- **Evidence caveat:** all three Phase 2 full-MAC/IP spoof outputs include
  `+20 errors`. The old harness accepted these, so its PASS alone does not prove
  that all full-identity ICMP attempts traversed the access link. Keep the raw
  evidence and re-pin the neighbour after the MAC change before retesting that
  claim. Local-link connectivity/TTL/bond results are separate observations.
- Hardened Phase 1 in `performance/run_tests.py` and `tests/lab_harness.py`:
  listener inspection runs `ss` directly; every probe completes before waiting
  for clients, with both clients checked throughout that window; failed commands,
  timeouts and server diagnostics retain raw output in structured errors. All
  owned processes receive a cleanup attempt even if an earlier cleanup step
  fails; cleanup errors prevent a successful measurement result.
- The harness saves `workload_<mode>.json` before queue verification, including
  failed workload evidence. This file is not a live acceptance PASS. Permitted
  probe evidence is saved before assertions and rejects duplicates/inexact counts.
  Spoof tests re-pin the neighbour after MAC changes and reject local delivery
  errors/incomplete attempts. Cleanup now also checks for owned interfaces.
- Added failure/overlap/evidence regressions in `tests/test_phase1_measurements.py`
  and a real ten-second concurrent iperf/ping process test on loopback in
  `tests/test_phase1_workload_processes.py`. The latter uses temporary ports and
  verifies process completion; it does not establish Mininet paths or QoS rates.
- Added `scripts/validate_phase1.py`; it shares validation machinery with
  `scripts/validate_phase2.py`. Both include the new foundation tests and save
  raw test/syntax output with a current-date default. Final validation passed:
  **53 Phase 1 tests** (33 system-Python foundation/process checks and 20 pinned-Ryu
  match/controller/wire checks), plus the broader **75-test regression suite**
  covering the existing Phase 2 prototype. Python compilation, shell/JSON syntax,
  and tracked diff whitespace checks also passed. Raw reports are
  `monitoring/evidence/phase1_offline_2026-10-16.json` and
  `monitoring/evidence/phase2_offline_2026-10-16.json`; historical evidence is
  preserved. The nine new tests include real process integration on loopback,
  not a privileged topology/QoS test.
- `sudo -n python3 -m tests.lab_harness --repetitions 3` exited 1 because sudo
  requires a password. No root lab was started and no Phase 1 gate is claimed.

### Phase 1 live spoof-setup correction (2026-10-16)

- Inspected `monitoring/evidence/phase1/foundation_20261015T184810634942Z` after
  the user's first Phase 1 live attempt. Warmup reached 42/42 directions and
  the saved 100-probe matrix completed, but `forbidden_ip_spoof.txt` contains
  only `ping: bind: Cannot assign requested address`. Controller logs have no
  error markers and cleanup reports no errors. No Phase 1 PASS is claimed.
- Root cause: the harness added 10.0.0.2 as a secondary on h7, then deleted
  its primary 10.0.0.7. Linux with `promote_secondaries=0` can delete both
  same-subnet addresses. This was a test setup defect; the probe never started.
  See the Linux kernel `promote_secondaries` documentation in
  <https://kernel.org/doc/html/v6.12/networking/ip-sysctl.html>.
- `tests/lab_harness.py` now uses `replace_host_ipv4`: flush the test interface's
  IPv4 addresses, add the desired /24, check every command, and read back the
  address before probing. The same checked helper restores the legitimate IP.
  `spoof_address.json` retains the pre-probe address state. Strict rejection of
  incomplete probes/local errors and receiver-side capture remain enabled.
- Added three regressions in `tests/test_phase1_measurements.py`, covering
  same-subnet primary/secondary deletion, restoration, command failure and
  missing address readback. All 11 focused measurement/address tests passed.
- Full Phase 1 validation passed: **56 tests** (36 system-Python tests including
  the real loopback workload, plus 20 pinned-Ryu tests); Python compilation,
  shell/JSON syntax and whitespace checks also passed. Raw output is in
  `monitoring/evidence/phase1_offline_spoof_fix_2026-10-16.json`.
  Privileged rerun still requires the user's sudo password;
  noninteractive sudo is unavailable. Retain the failed live evidence.

### Phase 1 foundation live gate passed (2026-10-16)

- The user ran `sudo python3 -m tests.lab_harness --repetitions 3` successfully.
  Independently inspected all raw evidence under `monitoring/evidence/phase1/`:
  `foundation_20261015T185219812228Z`, `foundation_20261015T185341912180Z`, and
  `foundation_20261015T185500724819Z`. Each has PASS, no failure file, no controller
  traceback/OpenFlow error, secure fail-mode, no FLOOD/NORMAL/ALL flow actions,
  and empty cleanup errors. Historical failed runs remain preserved.
- Each run completed **42 permitted directions x 100/100 probes**, both spoof
  cases with **100 sent / 0 received** and no local delivery errors, and a receiver
  capture containing exactly the three legitimate ICMP requests. The saved capture
  text also includes tcpdump's file-description stderr; this is not a fourth packet.
- Both shared egress queue byte counters increased during concurrent traffic.
  Saved HTB class readback shows a 50 Mbps root, queue 0 at 40 Mbps minimum /
  50 Mbps ceiling, and queue 1 at 1 Mbps minimum / 5 Mbps ceiling. Both workloads
  recorded complete measurements and 100% probe delivery. High-priority sender
  throughput was 36.6-36.7 Mbps with probe p95 RTT 30.3-31.4 ms; low-priority
  throughput was 3.73-3.74 Mbps with probe p95 RTT 359-368 ms. These are legacy
  TCP/ping observations, not the future urgent-message application or proof of
  adaptive-policy benefit. No baseline/adaptive comparison was run here.
- Audit summary saved to `monitoring/evidence/phase1_live_audit_2026-10-16.json`.
  Existing 56-test offline results remain valid; this follow-up changed only
  handoff/documentation and saved the read-only evidence audit. The repeated
  Mininet HTB quantum warnings did not fail these named checks.
- **Phase 1's implemented foundation gate is passed.** Retain the agreed limits:
  gateway/mesh expansion, usable-path bond failover, service policy, correlated
  failures, incident profiles and comparative measurements belong to later phases.

### Phase 2 usable-path implementation (2026-10-16)

- Continued the accepted Phase 2 sequence after the user's explicit request to
  proceed. Phase 1 implementation, successful live evidence and thresholds are
  preserved. No policy-engine/demo scope or controller framework changes.
- Strengthened `tests/phase2_harness.py` spoof checks: re-pin the gateway neighbour
  after MAC replacement, require the declared attempt count and reject local
  delivery errors. Older raw negative results remain preserved with their caveat.
- Added attachment-pinned route overrides to `config/phase2.py`, checked against
  configured attachments, port availability and the existing site permission
  matrix. Reserved health ports/peer are in this shared inventory.
- `controller/phase2_app.py` installs separate owned health rules per attachment
  with exact ingress/identity/IP/UDP matches and normal gateway transformations.
  Reserved-port drops outrank generic UDP forwarding. Health misses do not move
  application attachments; an unreachable path cannot be replaced by a gateway
  response or a reply delivered through the other NIC.
- Added `monitoring/path_monitor.py`: actual nonce echo to the remote hospital
  endpoint on each slave using raw packet taps; validate response identity,
  attachment, current nonce and packet integrity. Kernel UDP binds suppress local
  port-unreachable replies. Three successes/failures and a one-second hold-down
  control explicit bond selection; healthy backup remains sticky after recovery.
  Bond selection is read back and GARP updates controller return paths. When no
  probe path is healthy, report unavailable rather than claiming recovery.
- The harness owns monitor/reflector processes and refuses startup without two
  healthy paths. Added uplink-down, access-bridge stop/restart retaining veths, and
  an owned temporary forwarding drop. Each verifies carrier, healthy alternate,
  successful application probe within five seconds, stable forward/return probes,
  guest isolation during fault, restoration without failback and process cleanup.
  Logs/status, carrier, recovery attempts, snapshots and separate timings survive.
- Focused model, packet, source/port rejection, stale-status, controller and real
  three-peer wire tests passed. A real loopback UDP test verifies permitted echo
  and silence for malformed payloads/unconfigured source ports. Isolated OVS 3.3.4
  dummy switches accepted the new controller messages (28/22/30 Phase 2 messages
  across the three switches) without OpenFlow errors. This tests rule acceptance,
  not physical-slave packet taps or Linux bond selection.
- Full regression evidence is saved at
  `monitoring/evidence/phase2_path_monitor_offline_2026-10-16.json`: **96 tests
  passed** (59 system-Python foundation/model/probe/process tests and 37 pinned-Ryu
  controller/match/wire tests), plus Python compilation, shell/JSON syntax and
  tracked whitespace checks. The attempted
  `sudo -n python3 -m tests.phase2_harness --repetitions 3` required a password
  and started no network, so **new live fault tests have not run**. Three privileged repetitions must pass before
  claiming usable-path/access-switch resilience or expanding to 20 switches.
- Limit: this is reachability to one configured reflector, not all destinations
  or services. Reflector failure and monitor failure are dependencies. It is a
  lab mechanism, not cryptographic admission or production HA. See
  `docs/12_phase2.md` for actual commands and packet/monitor behavior.
- Raw-tap checksum handling explicitly supports kernel `PACKET_AUXDATA`
  CHECKSUM_PARTIAL metadata on virtual links. It still checks exact identity,
  header lengths, IPv4 checksum and current nonce, and checks completed UDP
  checksums. Metadata/truncation regressions cover this distinction. No host-wide
  offload/sysctl changes or additional dependencies were introduced.

### Phase 2 monitor device-down correction (2026-10-16)

- Inspected the user's failed live run
  `monitoring/evidence/phase2-live/20261015T191711760847Z`. This was not a startup
  failure: both physical-slave remote probe paths became healthy for 99 cycles.
  Kernel partial-checksum metadata was observed on both. The monitor then exited
  on `recvmsg` with `OSError: [Errno 100] Network is down` during the deliberate
  local-link fault. The harness detected the exited monitor at its next health
  check. Controller logs and network/process cleanup were clean.
- This partial run also records 100/100 forward/return probes before the fault,
  during kernel local-link failover and after restoration. Both strengthened
  spoof cases sent 100 attempts with zero received and no local delivery errors;
  the receiver captured exactly three legitimate controls. This closes the old
  spoof-setup caveat for this attempt, but is not a complete Phase 2 PASS or a
  remote-uplink/access-switch/blackhole result.
- `monitoring/path_monitor.py` now treats explicit network/device errors as a
  failed probe on that interface. It continues with the other socket, avoids
  selecting a persistently errored socket again in that round, and retries the
  device normally in the next round. It also handles expected guard-socket
  network errors and retries GARP after transient link errors. Status includes
  receive/guard/announcement diagnostics. Unexpected permission/descriptor
  errors still propagate; failed paths are never counted healthy.
- Added five regressions in `tests/test_path_monitor.py`: receive ENETDOWN and
  recovery, both-send failure without spinning/false health, unexpected fatal
  errors, retryable GARP, and the actual monitor loop through the recorded error,
  single backup selection, announcement and sticky restoration. All 19 focused
  monitor tests passed, including the existing real UDP reflector test.
- Full validation passed: **101 tests**, plus Python compilation, shell/JSON
  syntax and tracked whitespace checks. Raw regression output is saved at
  `monitoring/evidence/phase2_monitor_linkdown_fix_offline_2026-10-16.json`.
  A noninteractive attempt to rerun the live harness
  still required a sudo password and started no network. Re-run the same
  three-repetition Phase 2 command before claiming this corrected live gate or
  expanding the topology. Phase 1's passed gate remains unchanged.

### Phase 2 access-switch reconnection check correction (2026-10-16)

- Inspected failed run
  `monitoring/evidence/phase2-live/20261015T192302219453Z`. The monitor stayed
  alive; both monitor/reflector exited cleanly on owned teardown, and network
  cleanup reports no errors. The new raw-socket device-down fix is exercised by
  this run; the previous ENETDOWN crash did not recur.
- Remote uplink failure retained carrier, observed a healthy backup at about
  0.721 s, and completed a successful recovery probe at about 0.744 s after fault
  injection. Stable 100/100 forward/return probes passed while the link stayed
  down and after restoration. These are polling/probe timings, not an exact
  application interruption measurement.
- Access-switch removal also switched the bond to the backup, completed 100/100
  forward/return probes and rejected the guest probes while the bridge was down.
  After bridge recreation, the monitor again recorded both paths healthy for
  over 60 consecutive cycles. The harness nevertheless timed out in Mininet's
  controller-readiness check; no complete repetition or blackhole result is claimed.
- Root cause reproduced with the installed Mininet's actual `OVSSwitch.start`,
  `controllerUUIDs` and `connected` methods: bridge restart creates new OVSDB
  Controller rows, but Mininet retains cached old row UUIDs. Its connection
  check queries the deleted row until explicitly refreshed, despite restored
  probe forwarding through the bridge.
- `tests/phase2_harness.py` now refreshes the public controller-UUID cache after
  access-switch restart, then waits and separately verifies current OVSDB
  `is_connected` values. Current rows/targets/readback are saved to
  `p2s1_reconnection.json`. Genuine reconnection failures still fail; the timeout
  has not been increased or bypassed.
- Added `tests/test_phase2_reconnect.py`: four passing regressions use real
  Mininet methods against modeled OVSDB, reproducing the old false negative,
  verifying new UUID/readback, preserving actual-failure rejection, and rejecting
  an optimistic Mininet result when OVSDB reports disconnected. No network starts.
- Full validation passed: **105 tests**, plus Python compilation, shell/JSON
  syntax and tracked whitespace checks. Raw output is saved separately at
  `monitoring/evidence/phase2_reconnect_fix_offline_2026-10-16.json`.
  The attempted live rerun with noninteractive sudo again
  required a password and started no network. Run the same three-repetition
  Phase 2 harness to complete access-switch restoration and blackhole checks.
  Phase 1 remains passed; full Phase 2/20-switch expansion remains pending.

### Phase 2 strengthened prototype live gate passed (2026-10-16)

- Inspected all three successful live directories under
  `monitoring/evidence/phase2-live/`: `20261015T195211407878Z`,
  `20261015T195328826056Z`, and `20261015T195443852481Z`. Each has PASS and no
  failure file, no controller error markers, empty network cleanup errors,
  monitor/reflector exit codes [0, 0] and empty process-cleanup errors.
- Each run has eighteen stable 100/100 forward/return probe sets across normal
  operation, local-link failover, remote uplink loss, access-switch removal,
  forwarding blackhole and their restorations. Both spoof cases sent 100 probes
  with zero received and no local errors. Receiver captures retain exactly three
  legitimate requests at TTL 63. Guest isolation also held during each remote
  fault, endpoint carrier remained up, and restored bond selection stayed sticky.
- Successful recovery-probe completion after fault injection was 0.746-0.848 s
  for uplink loss, 0.904-0.930 s for access-switch removal, and 0.854-0.993 s for
  the blackhole. These are observed probe completion times, not exact outage
  durations; each met the five-second budget. All restarted p2s1 controller
  readbacks show current UUIDs connected, confirming the cache-refresh fix.
- Saved audit: `monitoring/evidence/phase2_prototype_live_audit_2026-10-16.json`.
  **The small Phase 2 gateway/usable-path prototype gate is passed.** No runtime
  code or shaping parameters changed during this audit. The full 20-switch
  topology, connectivity/isolation matrices and resource gate remain pending;
  expansion is the next agreed implementation step.
- The user's HTB warning comes from Mininet TCLink's default `r2q`/automatic
  quantum at these link rates. Installed Mininet prints `*** Error:` for every
  nonempty `tc` output, including this tuning warning. It did not invalidate the
  named tests. Quantum is bytes served per scheduling round; `r2q` divides the
  configured byte rate to derive it. See <https://www.man7.org/linux/man-pages/man8/tc-htb.8.html>.
  No suppression, system-package patch or scheduler change was made to hide it.

### Phase 2 twenty-switch expansion implemented; live gate pending (2026-10-16)

- Continued the agreed expansion after the three inspected prototype passes;
  preserved existing Phase 1/prototype commands, environment and working changes.
  No Phase 3 policies, demos or framework migration were added.
- `config/expanded.py` generates the shared two-district inventory: 4 backbone,
  4 aggregation and 12 access switches, 24 hosts, 38 infrastructure links and
  36 host attachments. Eight physical sites use ten logical subnets, with one
  guest host at each shelter. All twelve command/hospital hosts have two access
  attachments. Each access switch reaches both district aggregations, each
  aggregation reaches both local backbone switches, and the four backbone
  switches have six unequal-capacity links. `--districts 1` is the ten-switch
  diagnostic subset, never a substitute for the full gate.
- Initial fixed Phase 2 admission permits operational sites except hospital ↔
  shelter and isolates both guests. The full directed matrix has 414 permitted
  and 138 denied pairs (552 total). This is explicit site admission for topology
  acceptance, not a service-policy engine. Each district's six bonded hosts use
  its field reflector (`af1`/`bf1`); reflector/monitor/common-path dependencies
  remain. Gateway rewrite, once-only routed TTL decrement, proxy ARP, attachment
  checks, secure fail-mode and loop-free routing reuse the validated prototype.
- Shared model/topology/monitor code now accepts a selected inventory, retaining
  prototype defaults. Path computations cache by usable graph state.
  `controller/expanded_app.py` installs only changed owned rules in global
  downstream-first order and strictly deletes obsolete matches. Reconnection
  clears the corresponding rule cache. This avoids whole-network delete/reinstall
  churn; it does not promise atomic multi-switch transitions.
- `tests/expanded_harness.py` owns its resources and refuses occupied names.
  It retains raw results and pcaps for all 552 pairs with 100 probes each,
  audits actual receiver delivery (including zero forbidden requests), rejects
  duplicates, and verifies a ten-request unknown-ARP origin control with no
  remote copies. Both IP-only and MAC/IP spoof attempts have receiver controls.
  It removes each critical site's primary access bridge with endpoint carrier
  retained, checks all three affected hosts' backup selection and recovery probes
  within five seconds, stable forward/return probes and sticky restoration.
  It also tests aggregation loss, backbone-link loss, district partition and
  restoration. Current OVSDB UUIDs are refreshed after bridge recreation.
- Frozen acceptance in `config/expanded_acceptance.json`: four concurrent paced
  10 Mbps TCP streams for 15 seconds, 50 ICMP probes per source during load,
  100% probe delivery, p95 RTT ≤250 ms, average system CPU ≤85%, sampled available
  memory ≥512 MiB. Nonzero throughput and real load/probe overlap are required.
  This is a feasible 40 Mbps resource smoke workload through a 50 Mbps receiver,
  not urgent-message application measurements or an adaptive-QoS comparison.
  Full intended application-load assessment remains dependent on later phases.
- Added expanded inventory, controller, evidence and real twenty-peer OpenFlow
  tests to `scripts/validate_phase2.py`. Final offline result: **127 passing
  tests**, Python compilation, shell/JSON syntax and tracked whitespace checks.
  Evidence: `monitoring/evidence/phase2_expanded_offline_2026-10-16.json`.
  Regressions include all directed model paths, every single infrastructure-link
  loss, all critical access failures, constrained alternatives, partitioning,
  real Ryu registration/rules, capture corruption/leaks/duplicates and probe
  failure cleanup. These tests do not execute the expanded OVS dataplane.
- Direct unprivileged expanded invocation failed at root preflight before any
  lab operation, as intended. The actual three-repetition live attempt using
  noninteractive sudo returned `sudo: a password is required`; no live network
  started. **Expanded Phase 2 is not yet live-certified.** Next user command:
  `sudo python3 -m tests.expanded_harness --repetitions 3`.
  Inspect PASS markers, all matrix/receiver audits, fault timing, resource limits,
  controller logs and cleanup for three full two-district repetitions. Fix any
  demonstrated defects before moving to Phase 3; do not rerun the small prototype
  as a substitute or claim completion from the offline checks.
- Updated README and `docs/13_expanded_topology.md`, including an exact generated
  switch/DPID, subnet/gateway, host identity/attachment and infrastructure-port map.
  Regenerate those tables with `python3 scripts/export_expanded_topology.py`.
  The original specification is unchanged. HTB tuning warnings are not suppressed.

### Expanded reflector send-timeout correction (2026-10-16)

- Inspected failed run `monitoring/evidence/phase2-expanded/20261016T013805638963Z`.
  `af1_health.log` shows `TimeoutError: timed out` at the reflector's UDP
  `sendto`. This happened in initial health readiness, before warmup or any
  measured matrix/fault/load tests. `af1` exited 1; the other thirteen health
  processes exited 0 on teardown. Controller logs contain no error markers,
  network cleanup errors are empty, and health cleanup errors are empty.
  The HTB tuning warnings were not the terminating exception.
- The shared UDP socket previously applied its receive timeout to sends as
  well, so a backpressured reply could block all peers and kill reflection.
  `monitoring/path_monitor.py` now uses nonblocking I/O with bounded read waits,
  drops transiently unsendable echoes, and continues serving other peers.
  Explicit network/device errors, would-block/buffer errors and timeouts are
  recorded as counters and a last-error diagnostic. Unexpected permission or
  descriptor errors still terminate. There are no stale-response retries and
  no relaxed health thresholds: only fresh validated monitor responses count.
  Reflector status now has periodic monotonic heartbeat, receive/reply/reject/error
  counters, and an explicit not-ready status at clean shutdown. A socket-accepted
  send is not proof of remote delivery.
- Added four reflector regressions: reproduce the recorded timeout with all
  twelve district source/port tuples and recover on the next probe; transient
  send/receive errors never become replies; unexpected errors remain fatal;
  and expanded identity/port/payload rejection remains enforced. The existing
  real UDP echo/negative-identity test also passes. All 23 monitor tests passed.
- The expanded harness now retains switch/bond flows and reflector route/neighbor
  diagnostics before teardown on health failure. A regression verifies diagnostic
  readback failure preserves the original exception and stops all fourteen owned
  processes. No traffic-policy, topology, shaping or acceptance limits changed.
- Full offline validation: **132 tests passed**, plus compilation, shell/JSON
  syntax and whitespace checks; evidence is
  `monitoring/evidence/phase2_reflector_timeout_fix_offline_2026-10-16.json`.
  The attempted corrected live run with noninteractive sudo required a password
  and started no network. The precise kernel/network source of backpressure
  is not established by this failed run; startup readiness and the full dataplane
  still require validation. Run `sudo python3 -m tests.expanded_harness --repetitions 3`
  again and inspect its saved evidence. Do not claim the full Phase 2 gate passed
  or advance to Phase 3 yet.

### Expanded load measurement readiness correction (2026-10-16)

- Inspected run `monitoring/evidence/phase2-expanded/20261016T023034457285Z`.
  All 552 measured pair records passed (414 allowed, 138 denied). Receiver audit
  matches all expected delivered counts, zero duplicates, ten unknown-ARP origin
  controls and zero remote copies. Both 100-attempt spoof cases were denied with
  three legitimate receiver controls. All four critical primary-access failures
  exercised all twelve affected hosts: recovery-probe completion ranged from
  0.885 to 1.017 s. Aggregation and backbone-link recovery completed in 1.396 and
  1.562 s. Stable fault/restoration probes and district-partition isolation passed.
  These are one partial repetition's results, not the three-run full-size gate.
- The terminating check was load-time RTT, not delivery or machine resource
  exhaustion: all four sources received 50/50 probes; p95 RTTs were ac1 1609 ms,
  ah1 201 ms, bc1 162 ms, bh1 1584 ms against the 250 ms limit. Average system CPU
  was 7.284%; minimum sampled available memory was 6211.8 MiB. Each TCP stream
  reported 10.5 Mbps. Controller logs were clean, all fourteen health processes
  exited 0, and process/network cleanup error lists were empty. The reflector
  timeout crash did not recur in this run.
- Found a concrete workload-orchestration defect: pings began one second after
  client process launch, without verifying TCP payload reception. The bh1 TCP
  connection reports a 2.796-second initial RTT. Early ac1/bh1 ping delays fell
  from about 1.8 s across their first ten probes. Therefore the measurement
  started before all four offered streams were ready. This does **not** establish
  the underlying cause of network delay or prove it is fixed.
- `tests/expanded_harness.py` now waits for all remote health paths, retains
  bounded forward/return startup attempts for each source/receiver pair, and
  requires each TCP receiver socket to be established with at least 64 KiB
  received within five seconds before launching measured pings. It verifies
  receiver byte counters advance for every stream over the measured window and
  clients remain alive throughout. CPU sampling starts at payload readiness.
  All fifty measured probes remain included; slow probes are not trimmed.
- Added periodic owned switch/host qdisc backlog/drop counters and host neighbor
  snapshots during load (`load_network.json`), per-socket startup/readiness
  (`load_readiness.json`), and before/after byte counters (`load_progress.json`).
  Failure messages now name offending sources, reply counts and p95 RTT.
  Keep these diagnostics if a fully established load still exceeds the gate.
- Acceptance metadata version is `phase2-expanded-readiness-2026-10-16`.
  Delivery, 250 ms RTT, CPU/memory, stream count/duration and requested `10M`
  rates are unchanged. Corrected their description: installed iperf2 interprets
  uppercase M in binary units, so the actual request is approximately 41.94 Mbps
  total, still below the receiver's 50 Mbps. This corrects the prior "40 Mbps"
  shorthand; the offered rate was not reduced. No routing, HTB/qdisc, offload,
  priority or topology settings were changed to obtain a pass.
- Six new regressions cover missing payload despite live clients, readiness
  timeout with raw evidence, socket-byte parsing, exact recorded high-RTT failure,
  loss/incomplete-probe rejection, and real four-stream/200-ping workload I/O with
  owned cleanup on loopback. Focused tests passed. Full offline validation:
  **138 tests passed**, with compilation, shell/JSON syntax and whitespace checks;
  evidence `monitoring/evidence/phase2_load_readiness_fix_offline_2026-10-16.json`.
  Loopback validates orchestration, not Mininet shaping or live RTTs.
- Noninteractive sudo remains unavailable. The next privileged command remains
  `sudo python3 -m tests.expanded_harness --repetitions 3`. Inspect corrected
  readiness and queue evidence alongside the full matrix/fault/load/cleanup
  checks. The expanded Phase 2 gate remains open; do not start Phase 3 yet.

### First expanded pass audited; restoration compilation bottleneck corrected (2026-10-16)

- Inspected `phase2-expanded/20261016T025758330490Z`: one full PASS, all 552
  matrix results pass, receiver pcaps independently recount to exactly the
  expected allowed/denied requests, zero duplicates, ten origin-only unknown ARP
  controls, clean controller logs and cleanup, and all fourteen health processes
  exit 0. All four TCP receiver byte counters advance through the measured
  window. All 200 load probes arrive; p95 RTTs are 54.6, 52.6, 47.7 and 46.7 ms.
  Each TCP stream reports 10.5 Mbps, system CPU averages 7.012%, and sampled
  available memory stays at least 6221.3 MiB. This is one validated repetition,
  not the required three consecutive full passes.
- The next repetition `20261016T030259812954Z` passes the full matrix/receiver
  audit and preceding fault checks but fails `load_startup_ah1` before any TCP
  workload. Twenty-one one-packet attempts receive no reply, including one local
  delivery error. `ah1` remains on its healthy backup bond attachment; the B
  reflector's gateway neighbor is incomplete at failure. No process crash or
  controller error marker occurs, and teardown is clean. Exact source/receiver
  neighbor state was missing because the first load snapshot followed startup;
  snapshots now also run before startup and on exit, including startup failure.
- Reproduced excessive synchronous controller work with real Ryu serialization:
  an unchanged reconcile compiles roughly thousands of rules again despite
  emitting no update (0.495 s in the diagnostic), and eight partition-restoration
  port events take 4.723 s. This can leave controller-handled gateway ARP waiting
  behind topology work, close to the five-second application budget. The saved
  run's late-installed flow durations support investigating this bottleneck;
  they do not establish a complete packet-by-packet causal trace of the failure.
- Extracted the existing route-install loop into `Phase2App.install_route`, with
  the same protocol matches, ownership, priorities, rewrite/TTL actions and global
  downstream-first order. The expanded subclass caches compiled entries per
  source/target and health-port tuple. It reuses them only when the complete
  switch/port route and datapath connection identities match. Attachment moves,
  changed paths, withdrawals, and same-object features resets invalidate/rebuild
  affected rules. The configured Phase 2 inventory remains static; this is not
  runtime policy-update support. No asynchronous worker or relaxed admission was
  introduced. Strict obsolete-rule deletion and installed-rule diffing remain.
- On this machine, diagnostic unchanged reconcile drops to 0.0145 s and the
  eight-event restoration burst to 2.005 s, with initial full compilation
  essentially unchanged. These are single diagnostic executions, not a repeated
  dataplane performance study. Saved results:
  `monitoring/evidence/phase2_reconcile_cache_benchmark_2026-10-16.json`.
- Added regressions for unchanged/unused-link reuse without action compilation,
  cached bond mobility with both pinned health paths, same-object connection
  reset, and equality to a full recompilation after every one of eight restoration
  events. Strengthened the actual twenty-peer Ryu wire test: withdraw cross-site
  rules during a partition, restore all four cross-links through eight port
  events, then require restored routes and the queued hospital gateway ARP reply
  within five seconds (focused run: 4.162 s). This uses emulated OpenFlow peers;
  it does not execute OVS/Mininet or prove exact application interruption.
- Added a startup-failure regression requiring both before/failure source and
  receiver queue/neighbor snapshots. Full offline validation: **143 tests pass**,
  plus compilation, shell/JSON syntax and whitespace checks; evidence is
  `monitoring/evidence/phase2_reconcile_cache_fix_offline_2026-10-16.json`.
  Saved live audit:
  `monitoring/evidence/phase2_expanded_first_pass_audit_2026-10-16.json`.
- The five-second recovery budget, load/RTT/loss thresholds, offered rates,
  shaping and topology are unchanged. Corrected runtime still needs three fresh
  full repetitions: `sudo python3 -m tests.expanded_harness --repetitions 3`.
  Noninteractive sudo requires the user's password. Do not close Phase 2 or start
  Phase 3 based on the single earlier PASS or these offline/controller tests.

### Expanded Phase 2 live gate passed and audited (2026-10-16)

- Audited all three complete two-district repetitions:
  `monitoring/evidence/phase2-expanded/20261016T035837210877Z`,
  `20261016T040326661411Z` and `20261016T040818018679Z`. Each has PASS, no failure
  file, matching 20-switch/24-endpoint inventory and unchanged acceptance limits.
- Independently checked raw ping records and recounted receiver pcaps: each run
  passes all 552 distinct directed pairs, with 414 allowed and 138 denied.
  Exactly 41,400 allowed echo requests are delivered per repetition, with zero
  forbidden deliveries or duplicate requests. Every captured request has the
  expected Ethernet identities and TTL (63 routed, 64 same subnet). Ten unknown
  ARP requests appear only at their origin; all 25 capture logs per run report
  zero kernel drops. Spoof captures retain exactly three legitimate controls and
  no requests from either 100-attempt spoof scenario.
- All 39 stable fault/partition/isolation/control probe files per repetition
  have complete expected counts, no duplicates or local errors. All twelve
  critical endpoints per repetition retain carrier during primary-access bridge
  removal, select a healthy backup, and complete recovery probes in 0.735-1.045 s.
  Aggregation recovery completes in 1.129-1.402 s; backbone-link recovery in
  0.799-0.800 s. These are first successful recovery-probe completion times, not
  exact outage durations. Both district partition/restoration checks pass.
  All five recreated bridges per run have connected current OVSDB rows; final
  bond health is good on both paths and application attachment stays on backup.
- Under the unchanged four-stream TCP workload, all 600 measured probes across
  three repetitions arrive. Raw output agrees with saved metrics. Per-source
  p95 RTTs are 24.7-45.8 ms, below 250 ms; each TCP stream reports 10.5 Mbps.
  Payload readiness completes in 0.166-0.180 s, and receiver counters advance
  for every stream through measured probes. CPU averages 7.58-8.57%; available
  memory is at least 6086 MiB at sampled points. Periodic queue/neighbor evidence
  exists. This validates the Phase 2 feasible-load smoke gate, not later urgent
  application workloads, adaptive policy benefits or a saturation benchmark.
- Each run has secure fail-mode on all twenty switches and no NORMAL/FLOOD/ALL
  fallback actions, no controller error markers, fourteen clean health-process
  exits, and empty process/network cleanup error lists. Earlier reflector,
  startup and restoration failures did not recur in these repetitions.
- Saved audit: `monitoring/evidence/phase2_expanded_live_audit_2026-10-16.json`.
  **The implemented full-size Phase 2 gate is passed.** No runtime, shaping or
  acceptance settings changed during this audit. The existing 143-test offline
  validation remains current; no unnecessary runtime test rerun was performed.
  Updated README, expanded guide, current progress and next-action sections.
- Next is the already agreed Phase 3 policy/operator implementation. No Phase 3
  service engine, incident profiles, capacity admission, adaptive QoS, partial
  deployment rollback or student demo tools are claimed implemented. Preserve
  all earlier code, evidence and working-tree changes. No unchanged Phase 2
  rerun is needed; run affected regressions as Phase 3 changes shared behavior.

### Phase 3 implementation progress (2026-10-16)

- Added fixed incident/service configuration, bounded deterministic bidirectional
  planning with joint directed-link capacity admission, priority reductions and
  explicit rejections. Four profiles cover normal, evacuation, search-and-rescue
  and degraded operation. Prepared `af3`/`bf3` identities require separate
  registration and reporting-service grants. Exact transport/MAC/IP/ingress
  rules retain the Phase 2 site matrix; ordinary IPv4 packet-in misses fail closed.
- New `policy/` modules implement owned OVSDB queues, actual OpenFlow 1.3 rule
  normalization/readback, serialized local commands, atomic journal files,
  per-switch checkpoints and partial deployment/rollback status. Add-before-delete
  replacement preserves identical match keys. Queue preparation includes both
  retained/candidate minima; no multi-switch or OVSDB/OpenFlow atomicity claim.
  Versions stop at 900 to respect the installed HTB queue range.
- `controller/policy_app.py` retains Phase 2 health/ARP and trusted identities,
  publishes fresh topology and controller incarnation observations. The optional
  watcher repairs routes within the current profile/version after stable graph
  changes, freezes unsupported partial states, and never replays abandoned V2.
  `topology/run_incident.py` owns a manual normal-profile session and service
  responders. Local operator commands support inspect, preview, apply, constraints,
  onboarding, rates, staged resume, rollback and fresh verification.
- Added exact TCP/UDP service probes and receiver pcap analysis, a full-size
  `tests.phase3_harness`, isolated real OVSDB/dummy-OVS/pinned-Ryu integration,
  planner/deployment/backend/controller tests and `scripts/validate_phase3.py`.
  The live harness covers profile behavior and selected queue counters, negative
  service/spoof cases, admission conflicts, constrained route/five-second echo
  recovery, isolated-switch rollback and a receiver-measured video rate cap.
  It preserves logs and actual qdisc snapshots, checks owned cleanup and claims
  no live PASS until those assertions run. The existing expanded harness accepts
  an optional controller/environment for reuse; its Phase 2 defaults are preserved.
- The full regression run initially exposed a timing-sensitive existing Phase 2
  emulated-peer reader: reading one OpenFlow message per socket turn put its
  restoration check narrowly above five seconds. The reader now drains buffered
  batches with TCP_NODELAY; the five-second assertion remains. Focused rerun
  passed with restoration/ARP at 2.059 seconds. No production recovery budget
  or controller policy was relaxed. Focused Phase 3 tests also passed (29 tests).
- Final complete regression validation **passed: 178 tests** (126 system-Python,
  52 pinned-Ryu), with no skipped checks, plus Python compile, shell syntax,
  configuration JSON and tracked whitespace checks. Evidence:
  `monitoring/evidence/phase3_offline_2026-10-16.json`. Catalogue and assumed-topology
  evacuation preview smoke checks passed (13 catalogue services, four profiles;
  initial evacuation has 11 enabled services, 1,532 flows and 38 queued ports).
  Dummy switches prove real OpenFlow/OVSDB behavior, not kernel HTB shaping or
  Mininet application performance. Attempted the live command with `sudo -n`;
  it stopped with `sudo: a password is required` before starting a lab. The
  privileged three-repetition gate remains pending.
- Next command: `sudo python3 -m tests.phase3_harness --repetitions 3`. Inspect all
  saved repetitions under `monitoring/evidence/phase3-live/`, fix demonstrated
  defects and audit the gate before Phase 4. Do not rerun unchanged Phase 1/2 live
  gates. Existing changes and pinned environments are preserved; no migration or
  dependency installation was performed. Phase 4's four student sequences,
  especially its bounded two-switch/one-class rollback exercise, Phase 5's fair
  comparative measurements and Phase 6 rehearsal remain outstanding.

### Phase 3 short-capture correction (2026-10-16)

- Inspected user run `monitoring/evidence/phase3-live/20261016T052111516618Z`,
  which failed at `normal_a_bulk`. All 100 client attempts were sent and echoed,
  with zero errors and p95 3.05 ms. The receiver server logged the exact same
  100 unique request payloads. The pcap contains only sequences 0–79; tcpdump
  reports 83 captured packets versus 105 received by filter, with zero kernel
  drops. All saved policy/network/health/service cleanup results were clean.
  This was incomplete capture evidence, not evidence of 20 lost application
  requests. The old failure and its files remain unchanged.
- Saved a reproducible audit with input hashes and client/server/capture
  comparisons: `monitoring/evidence/phase3_capture_audit_2026-10-16.json`.
- `tests/phase3_harness.py` now uses tcpdump `--immediate-mode` in addition to
  `-U`. The latter flushes output records but does not disable libpcap/kernel
  packet batching. `monitoring/service_capture.py` provides a bounded two-second
  positive-capture drain before shutdown, tolerating records still being written.
  The final stopped file remains strictly parsed; the required 100 requests,
  zero duplicates, negative-access checks and zero kernel drops are unchanged.
  Failure output now includes expected/captured/duplicate counts. No HTB/r2q,
  production flow, admission or recovery thresholds changed.
- Added regression coverage for the observed 80+20 delayed TCP capture tail,
  a partially written record, permanently missing requests and duplicate evidence.
  All five focused service-probe tests passed, including real TCP/UDP loopback
  echoes and fixed-source-port iperf. Complete post-fix validation **passed all
  179 tests** (127 system-Python and 52 pinned-Ryu), including the isolated real
  OVS integration, plus compile/shell/JSON/whitespace checks, with no skips.
  Separate evidence: `monitoring/evidence/phase3_offline_capture_fix_2026-10-16.json`.
- Phase 3 remains live-pending. Sudo still requires the user's password, so the
  corrected Mininet gate cannot be executed by this session. Next run is
  `sudo python3 -m tests.phase3_harness --repetitions 3`, followed by inspection
  of all three saved repetitions. Do not advance to Phase 4 yet.

### Phase 3 recovery scheduling and focused check (2026-10-16)

- User reported timeout in run `phase3-live/20261016T052936295267Z` and limited
  remaining time. Audited all 44 saved profile service checks: exact expected
  delivery, zero duplicates and zero client errors. Onboarding, admission rejection,
  wrong identity/attachment and constrained coordination also completed before
  the timeout. The prior packet-capture correction worked in this run.
- The timeout was the automatic link-recovery check, which waited for all twenty
  switches to reach committed `verified` before sending its first application
  echo. The interrupted journal was still applying version 8 to version 8; only
  `bb2`, `bb3`, `bb4`, `bag1` needed changed rules, but unchanged switch checks
  delayed `bag1`. No application recovery time was measured, so no five-second
  success or actual application outage length is claimed from this failed run.
  Audit: `monitoring/evidence/phase3_recovery_audit_2026-10-16.json`.
- Added `Manager.repair()` to update changed switches first while retaining all
  unchanged-switch readbacks and partial-failure safeguards. Watcher calls it;
  manual profile/checkpoint deployment order is unchanged. Atomic JSON journals
  now use compact serialization to reduce repeated history writes; operator output
  remains readable. No admission, rate or application recovery threshold relaxed.
- `automatic_recovery()` in the harness now sends real echo probes immediately
  after the injected link failure. The first successful echo must complete within
  the unchanged five-second budget. Full journal/readback completion is checked
  separately (60-second management timeout) before the test can pass. Both times
  and raw attempts are saved. Timeout labels identify recovery versus link
  restoration; profile/recovery progress prints to the terminal.
- Added `--recovery-only`: full network and real services, but only constrained
  baseline/automatic-recovery checks. It writes `RECOVERY_PASS`, never a full
  `PASS`, and is explicitly labelled diagnostic-only in its manifest. This avoids
  repeating the several-minute matrices while debugging the current fault.
- **All 25 targeted tests passed** in 4.12 seconds, plus syntax/whitespace checks.
  Results are saved separately in
  `monitoring/evidence/phase3_recovery_fix_tests_2026-10-16.json`.
  Tests cover changed-first ordering, unchanged-switch isolation
  still becoming partial, echo-before-readback measurement and rejection of an
  echo after five seconds. Previous 179-test full-suite result remains historical;
  this time only affected regressions were rerun to respect the user's time.
- Next command: `sudo python3 -m tests.phase3_harness --recovery-only --repetitions 1`.
  Inspect its evidence first, then run the complete three-repetition gate. Sudo
  requires the user's password; no new live PASS claimed. Phase 4 remains gated.

### Phase 3 controller isolation correction (2026-10-16)

- Inspected full run `phase3-live/20261016T054432958250Z`. Profiles and automatic
  recovery completed: first echo at 4.781 seconds, full journal verification at
  6.187 seconds. The later failure was read-only validation of the two saved
  coordination rules on isolated `bb2` during checkpoint rollback. Rollback
  correctly reported `rollback_partial`, with `bb2` unverified and pending V2
  abandoned; all other switches were verified. Audit:
  `monitoring/evidence/phase3_isolation_audit_2026-10-16.json`.
- Root cause was the harness fault setup: `ovs-vsctl del-controller bb2` removes
  its last primary controller, which **clears OpenFlow tables**. This behavior is
  explicitly documented in installed `ovs-vswitchd.conf.db(5)`, Bridge.controller,
  and applies even with secure fail-mode. A destructive configuration change was
  being used to simulate a connection-only outage. The earlier dummy integration
  had an empty saved `bb2` table, so it failed to expose this mistake.
- New `isolated_controller()` retains primary-controller UUIDs and retargets
  their existing rows to a bound but non-listening loopback TCP port. It restores
  exact original targets in `finally`, then checks reconnection. Targets use OVSDB
  JSON string quoting. No global firewall, controller framework or production
  backend changes. The live harness now requires populated `bb2` service rules
  and verifies their preservation immediately after isolation and after rollback.
- Strengthened the actual private OVS/Ryu integration to route coordination via
  `bb2` before the checkpoint, require nonempty rules and verify both preserved
  rules and unchanged controller UUIDs across isolation/restore. Added a regression
  for restoring targets when the isolated block raises an exception. Targeted
  tests plus the real integration **all passed: 27 tests in 75.37 seconds**,
  plus syntax/whitespace checks, saved in
  `monitoring/evidence/phase3_isolation_fix_tests_2026-10-16.json`.
  The live check also sends 100 coordination echoes through the isolated switch,
  alongside local dispatch, rather than relying only on saved-flow readback.
  The first integration attempt caught missing OVSDB
  quoting in the new setup; that was fixed before requesting any user live rerun.
- Added `--updates-only` to run the same checkpoint/isolation/rollback/video-cap
  code on a constrained normal baseline without repeating profiles/onboarding/
  automatic recovery. Its marker is `UPDATES_PASS`, never the full `PASS`.
  Next short check:
  `sudo python3 -m tests.phase3_harness --updates-only --repetitions 1`.
  Full three-repetition live validation and evidence audit still gate Phase 4.
  Sudo requires the user's password; no new kernel/live PASS claimed here.

### Focused Phase 3 update gate inspected (2026-10-16)

- User's `--updates-only --repetitions 1` run
  `phase3-live/20261016T055625706698Z` passed and was independently audited.
  Constrained coordination, local dispatch during controller isolation and
  coordination through isolated `bb2` each delivered 100/100 echoes with no
  local errors. Reparsed receiver pcaps each contain exactly 100 requests,
  zero duplicates and zero reported kernel capture drops. p95 RTTs are
  3.13–3.19 ms. Saved rollback readback verifies all twenty saved switch tables;
  isolated `bb2` is explicitly read-only local inspection with two preserved rules.
- The checkpoint records V2 only on `bb1`; attempting isolated `bb2` reports
  partial. After rollback/reconnection, the next evacuation deployment reaches
  verified V3 on all twenty switches with no pending candidate. Offered video
  is 20 Mbps and receiver throughput is 3.89 Mbps under the declared 4 Mbps cap,
  within the existing smoke-gate tolerance. This is cap evidence, not a video
  quality or comparative application performance claim.
- All fourteen health processes and thirteen service responders exit cleanly;
  policy/network cleanup error lists are empty and no controller error markers
  appear. The marker is `UPDATES_PASS`, correctly not full `PASS`. Audit:
  `monitoring/evidence/phase3_updates_live_audit_2026-10-16.json`.
- No runtime changed during this audit. Next is the complete gate:
  `sudo python3 -m tests.phase3_harness --repetitions 3`. Inspect all three complete
  runs before marking Phase 3 passed or advancing to Phase 4. Do not repeat the
  focused diagnostics unless a new failure warrants it.

### Complete Phase 3 live gate audited (2026-10-16)

- The latest shared PASS is repetition 3. Located and independently inspected all
  three complete runs: `20261016T055931961676Z`, `20261016T060538530066Z`,
  `20261016T061145167359Z`, under `monitoring/evidence/phase3-live/`. Their manifests
  are repetitions 1/2/3 of the full gate, not focused diagnostics, and each has a
  full `PASS` marker with no failure marker.
- Each run passed 54 service checks, covering all four profiles, registration
  separate from permission, unrelated-service and identity/attachment negatives,
  constrained coordination, actual link recovery, local dispatch and coordination
  through management-isolated `bb2`. Independently reparsed all 162 receiver pcaps:
  each run has 4,500 successful echoes and 900 denied attempts, exact request
  counts, zero duplicates, zero forbidden TCP SYNs and zero kernel capture drops.
  All client attempts complete without local delivery errors. Each run's 40
  admitted profile queue counters advance with zero reported queue errors.
- Joint protected-capacity rejection retains the committed version. Actual echo
  recovery completes in 4.791, 4.679 and 4.649 seconds, below the unchanged five
  seconds. Full readback is recorded separately. The avoided `bb1` remains excluded
  from coordination; the failed backbone link remains down during its stable
  service check.
- The checkpoint records only the first updated switch before isolated `bb2`
  reports partial; rollback readback validates all twenty saved tables, including
  the isolated switch's two retained coordination rules via explicitly read-only
  local inspection. Both local dispatch and coordination deliver 100/100 while
  isolated. Reconnection is followed by explicit evacuation version 10 verified
  on all twenty switches, with no pending candidate. Video receiver throughput
  is 3.89 Mbps in every run for 20 Mbps offered under a declared 4 Mbps cap.
- All three have clean controller logs, fourteen health-process exits and thirteen
  service-responder exits with code zero, and empty policy/network cleanup errors.
  Audit: `monitoring/evidence/phase3_live_audit_2026-10-16.json`.
  **The implemented Phase 3 gate is passed. No unchanged rerun is needed.**
  No runtime or thresholds changed in this audit; no unnecessary tests rerun.
- Updated current status, README and Phase 3 guide. Next is Phase 4: build and
  rehearse the four complete student sequences and resets, especially bounded
  two-switch/one-class rollback, with three complete repetitions each. Phase 3
  provides supporting controls/regressions, not finished student demonstrations.
  Phase 5 fair comparative workloads and Phase 6 presentation remain outstanding.

### Simplified Phase 4 implementation (2026-10-16)

- User approved four simpler student demos, including reuse of the existing
  whole-profile checkpoint/rollback instead of a dedicated two-switch, single-class
  updater. Updated the current main specification section 9.4 and handoff; older
  dated entries preserve the earlier design. Each student still injects, inspects,
  chooses a correction, verifies and resets separately. No framework migration.
- Added `demo_scripts/session.py`, `control.py`, `engine.py`, `model.py` and package
  initializer. One owned 20-switch/24-endpoint session retains Phase 3's watcher,
  health checks and exact application servers. A mode-0600 local Unix socket accepts
  bounded structured commands, not arbitrary shell execution. All actions/results
  are saved in `commands.jsonl`; each demo has its own evidence directory and must
  be reset before another is prepared. Existing baselines/changes are preserved.
- Congestion uses medical `ah1→ac2` and bulk `as2→ac2` on the actual same 50 Mbps
  receiver egress. Prepared normal allocations keep all protection/admission active:
  bulk 38 Mbps protected / 50 Mbps maximum, medical 2 Mbps protected / 20 Mbps
  maximum. Urgent demand is 8 KiB x 250 TCP echoes/sec (16.384 Mbps payload), with
  one persistent 60 Mbps requested TCP bulk workload. Evacuation releases the
  special bulk reservation and caps bulk at 1 Mbps, keeping the medical ceiling.
  Pure admission checks prove old/new queue minima fit together. No protection is
  disabled to manufacture the task; visible degradation still needs a live check.
- Added `monitoring/urgent_stream.py`: paced framed TCP, nonce/sequence, scheduled
  offer times, bounded pending queue and raw client/receiver events. Backpressure,
  missing and late replies count against 95% on-time delivery and a 250 ms deadline/
  p95 goal. Five-second windows require identical workload PIDs/offered rates and
  actual queue-counter intervals. Verification rejects absent initial degradation,
  absent correction or no measured bulk cost. This new kernel experiment is not
  validated by its successful loopback tests.
- Routing computes a primary fault, credits ordinary automatic backup and requires
  a distinct feasible avoid-switch candidate selected by number. It verifies both
  directions, continued link-down state and positive/forbidden traffic. Onboarding
  enforces denied measurements before registration and after registration before
  the separate grant, then reporting/unrelated/spoof checks with guest restoration
  on all exits. Rollback retains isolation through the user's explicit rollback
  and read-only verification, with real coordination through the isolated switch;
  management is restored only during reset.
- Reset restores owned links/controller targets, default echo servers, normal
  configuration and prepared endpoint state, then verifies fresh rules/queues,
  100 dispatch echoes and denied unregistered reporting. Owned process handles/
  groups and queues/network cleanup are retained; no global process kills or
  `mn -c`. Actual student command runbook: `docs/15_phase4_demos.md`.
- Added `tests.phase4_harness`, which rehearses the same separate commands and
  resets. `--demo` is explicitly focused (`FOCUSED_PASS`), not the full gate.
  Added `tests.test_demos` and `scripts/validate_phase4.py`. All 13 focused tests
  passed, including real loopback 250 Hz medical echoes, real paced fixed-port
  TCP bulk, no-problem/late/loss/duplicate rejection, route/admission feasibility,
  local command RPC, registration and rollback/reset state machines. Packet probes
  in model state tests are explicitly mocked, not labelled live evidence.
- Complete regression evidence **passes: 198 distinct tests**, plus source compile,
  shell/JSON and whitespace checks:
  `monitoring/evidence/phase4_offline_2026-10-16.json`. The complete run passed 145
  system tests; the initially failing pinned-Ryu command was rerun in full (52
  passed), retaining the unaffected system result with retry provenance. A final
  14-test demo rerun adds one ownership-bootstrap case (198 unique, not the sum
  of repeated executions). The new session persists its owner token before
  constructing action handlers so queue setup and final cleanup cannot acquire
  different owners. Its full context-manager regression checks this explicitly.
- The initial Phase 2 emulated-peer failure was a test precondition race: timing
  restoration could include remaining DOWN callbacks from the prior injection.
  An observation-only temporary subclass now records completed reconciliation
  and waits for the full initial/partition state before restoration starts.
  Production controller code and the five-second assertion are unchanged.
  Restoration passes at 1.952 seconds in the full pinned-Ryu rerun. The failed
  report is preserved as `phase4_offline_before_wire_sync_2026-10-16.json`, and
  retry history remains in the final report. No kernel/live result is inferred.
- Sudo still requires the user's password (`sudo -n true` checked); no Phase 4
  live network executed in this implementation session. First diagnostic:
  `sudo python3 -m tests.phase4_harness --demo congestion --repetitions 1`.
  Inspect baseline/congested/corrected evidence before the complete
  `sudo python3 -m tests.phase4_harness --repetitions 3`. Live rehearsal is pending;
  the 3–4 minute student target remains unmeasured. Supplementary Phase 2 failure
  evidence remains; no newly combined correlated-fault rehearsal or Phase 5
  comparative measurements are claimed.

### Focused Phase 4 congestion/reset live audit (2026-10-16)

- User run `phase4-live/20261016T070440726421Z` passed the focused congestion
  sequence and reset. Independently recomputed the raw offer/reply events and
  correlated receiver nonce/sequence evidence; no runtime or thresholds changed.
- Baseline: 1,250/1,250 urgent messages on time, p95 3.306 ms. Under competing
  bulk: 867/1,251 on time (69.30%), p95 of received replies 215.10 ms; the 95%
  delivery goal fails visibly. Corrected evacuation: 1,250/1,250 on time, p95
  3.352 ms. No duplicate replies. Scheduled urgent offers remain about 250/sec.
  The same urgent and bulk PIDs persist across before/after correction.
- Selected medical queue throughput changes from 11.98 Mbps under load to
  17.32 Mbps after correction; bulk falls from 37.99 to 0.985 Mbps while its
  requested demand/process continues. Queue throughput includes wire overhead;
  the configured urgent payload demand is 16.384 Mbps. This supports the bounded
  demo's measured recovery/cost, not Phase 5 comparative policy claims.
- Reset readback passes, no pending update remains, 100 dispatch echoes succeed,
  and unregistered reporting is denied again. Reparsed both reset receiver pcaps;
  no duplicates/leaks. All six custom/restarted workload processes exit zero,
  as do fourteen health and thirteen original responder processes. Controller
  logs and demo/policy/network cleanup report no errors. Marker is correctly
  `FOCUSED_PASS`, not full `PASS`.
- Audit: `monitoring/evidence/phase4_congestion_live_audit_2026-10-16.json`.
  **Focused congestion/reset passed.** Next command is
  `sudo python3 -m tests.phase4_harness --repetitions 3` to exercise all four
  demonstrations/resets, followed by saved-evidence audit. Do not rerun the
  unchanged congestion diagnostic or Phase 1–3 live gates. Phase 4 remains open
  until three complete repetitions pass and are inspected.

## Prompt to paste into the new chat

> Continue this project in `/home/rithvikmukka/sdn-lab/sdn-project`. Read
> `HANDOFF.md` and `PROJECT_DESCRIPTION_AND_PLAN.md`, then inspect the current
> working tree. Phase 1 and the full 20-switch/24-endpoint Phase 2 gate passed
> three inspected live repetitions. Phase 3's policy/operator gate also passed
> three complete audited live repetitions; read `docs/14_phase3.md` and the latest
> handoff entry. The user approved simplified Phase 4 helpers, implemented in
> `demo_scripts/` with runbook `docs/15_phase4_demos.md`. Inspect/run the new
> congestion diagnostic and full three-repetition demo/reset live gate, fix
> demonstrated issues and audit evidence. Preserve the Phase 1–3 baselines and
> do not rerun their unchanged live gates.
> Do not claim dummy OVS tests validate kernel shaping or live application recovery.
> Preserve my changes and the validated Phase 2 baseline.
> Follow the implementation checklist in the handoff, test changes rigorously,
> distinguish implemented features from proposals, and update the progress record.
> Do not restart planning or expand the feature scope. Ask for missing information
> only when necessary, and continue useful independent work while waiting.
