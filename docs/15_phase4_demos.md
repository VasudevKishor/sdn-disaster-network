# Four simplified student demonstrations

Phase 1–3 gates have passed. The Phase 4 helpers are implemented; **their full
three-repetition live gate is pending**. The user approved using Phase 3's tested
whole-profile checkpoint/rollback for demo 4 instead of a separate two-switch,
single-class updater. The other three retain the agreed measured outcomes.

Each student creates a problem, inspects evidence, applies a chosen correction,
verifies the result and resets. The helper commands never combine the whole
assessed demonstration into one action. The automated harness uses the same
individual commands for repeatability testing. The 3–4 minute presentation target
still requires rehearsal; it is not a measured timing claim.

## Fast validation first

From the project root, test the new congestion experiment once:

```bash
python3 scripts/validate_phase4.py
sudo python3 -m tests.phase4_harness --demo congestion --repetitions 1
```

Saved offline validation passed 198 distinct regressions, including the preserved
Phase 1–3 checks, private real OVS/Ryu, 14 demo tests and real loopback workload
processes. Report: `monitoring/evidence/phase4_offline_2026-10-16.json`. It records
retained passing results and the complete pinned-Ryu rerun after test-only fault
synchronization; no production controller or recovery budget was changed.

This is the one new workload behavior that Phase 3 did not validate. If the
normal policy already meets the urgent goals under bulk load, the check fails
with **No urgent degradation observed**; do not describe that as a recovery.
Preserve and inspect its measurements before adjusting the prepared workload.
Neither successful loopback workloads nor model tests certify kernel HTB behavior.

The first focused live run (`20261016T070440726421Z`) passed independent audit:
on-time urgent delivery fell from 100% to 69.30% with bulk load, then returned
to 100% after evacuation using the same workload processes. Bulk queue throughput
fell from 37.99 to 0.985 Mbps. Reset and owned cleanup passed. Audit:
`monitoring/evidence/phase4_congestion_live_audit_2026-10-16.json`.
No unchanged focused rerun is needed; the complete gate below is next.

Once that focused run passes, run all demonstrations and resets:

```bash
sudo python3 -m tests.phase4_harness --repetitions 3
```

The full gate creates one owned topology per repetition and runs all four demos
on it. It saves command order, raw client/server events, receiver pcaps, queue
counters, snapshots, verification and reset results under
`monitoring/evidence/phase4-live/`. A focused `--demo` run writes `FOCUSED_PASS`,
never the full `PASS`. `--demo routing`, `--demo onboarding` and `--demo rollback`
also support targeted debugging. No unchanged Phase 1–3 live rerun is needed.

## One session for the presentation

Terminal 1 owns the network, controller, health monitors and application servers:

```bash
sudo python3 -m demo_scripts.session
```

Leave it running. It prints a session path and commands for terminal 2. Set `DIR`
to that exact printed directory, then define this convenience function in Bash:

```bash
demo() { sudo python3 -m demo_scripts.control --state-dir "$DIR" "$@"; }
demo session status
```

The function sends one action at a time to the local session. The Unix socket is
root-only (mode 0600); no remote management listener or arbitrary shell endpoint
is added. Use a shorter `--state-dir /tmp/sdn-demo` if your checkout path is long;
the directory must be empty so existing evidence is preserved. The default path
works in the current checkout. Do not run another Mininet lab simultaneously.
The normal policy watcher remains enabled for permitted automatic topology
repairs. Partial deployment states retain its existing freeze/recovery behavior.
Reset pauses that owned watcher while restoring the baseline, then restarts it.

Only one demonstration can be active. Finish its `reset` before the next `setup`.
The session records all commands, errors and results in `commands.jsonl`. `inspect`
distinguishes saved policy status from actual rule dumps/queue counters; files
are in the current demo's numbered evidence directory. If an action fails, retain
the terminal output, inspect the session and use that demo's `reset` to recover.
An aborted/failed demo is not a successful rehearsal.

## Student 1: medical messages during bulk congestion

```bash
demo congestion setup
demo congestion measure
demo congestion inject
demo congestion measure
demo congestion inspect
demo congestion fix
demo congestion measure
demo congestion verify
demo congestion reset
```

Explain the baseline measurement first. A medical TCP echo workload runs from
`ah1` to `ac2` on its exact configured tuple, sending 8 KiB messages at 250 per
second (16.384 Mbps payload in each direction for fully echoed messages). Routine
bulk uses `as2` to the same `ac2` receiver, requesting 60 Mbps TCP. Their real
receiver egress is verified to be the same 50 Mbps switch port; the identity of
that switch follows the endpoint's current authorised bond attachment.

The prepared normal policy gives medical traffic 2 Mbps protected allocation
with a 20 Mbps maximum; routine bulk has a legitimate 38 Mbps protected allocation
and 50 Mbps maximum. Other protections and admission remain enabled. Baseline
medical demand fits when bulk is idle. Starting bulk uses its existing reservation,
leaving insufficient room for the full urgent offered workload. This models a
normal allocation that needs an incident-priority change, not invalid admission.

`inspect` shows the shared capacity, allocations, raw installed rule snapshots and
actual selected-queue counters. `fix` applies evacuation, removes the special bulk
reservation and caps routine bulk at 1 Mbps. It retains the explicit 20 Mbps medical
ceiling. The combined old/new queue minima are checked to fit before deployment.
The same two client processes keep running with the same requested offered rates.
TCP backpressure reduces actual transmitted bulk throughput; the 60 Mbps is its
requested demand, not a claim that 60 Mbps crosses the link after shaping.

Measurements use five-second offer windows. An urgent request must receive its
exact echoed payload within **250 ms** of its scheduled offer. At least **95%**
must meet that deadline, and received RTT p95 must be at most 250 ms. Sender
backpressure and missing/late replies count against delivery; duplicates fail.
Client and receiver raw events identify the same per-session nonce and sequence.
Queue throughput uses actual byte-counter sampling intervals.

`verify` requires a passing baseline, visible under-load degradation, a passing
corrected window, unchanged workload PIDs/rates and a measured reduction in bulk
throughput. It cannot claim a recovery if normal operation was already adequate.
`reset` stops only owned workloads, restores the original echo servers and normal
policy, then checks dispatch and denied unregistered reporting. This is one
bounded congestion demonstration; Phase 5 comparative policy experiments remain.

## Student 2: constrained routing with a link still down

```bash
demo routing setup
demo routing inject
demo routing inspect
demo routing fix --candidate 1
demo routing verify
demo routing reset
```

Setup computes a real coordination route and selects a backbone-link fault for
which an ordinary backup and a distinct capacity-feasible avoid-switch route
exist. On this inventory it normally fails `bb1–bb3` and asks to avoid `bb1`.
Names come from the current topology; inspect the output rather than assuming
the example. Injection leaves the link down and credits the controller's normal
automatic backup. That ordinary backup must still include the avoided component,
otherwise setup is not an appropriate problem and the helper refuses the claim.

Inspect the current route and numbered candidates with residual capacity. Choose
a listed feasible candidate (replace `1` as needed). The correction revalidates
that route against fresh observations and joint capacity, installing both
directions. Verification checks actual policy readback, exclusion of the avoided
switch, both failed-link ports still down, 100 successful coordination requests
and rejection of a guest identity. Reset stops the owned watcher, restores the
link and normal policy, and verifies the baseline.

## Student 3: one prepared endpoint, one reporting permission

```bash
demo onboarding setup
demo onboarding inject
demo onboarding inspect
demo onboarding measure
demo onboarding register
demo onboarding measure
demo onboarding grant
demo onboarding verify
demo onboarding reset
```

Setup leaves prepared `af3` unregistered and its interface inactive. Injection
activates it. Inspect its configured IP, MAC, attachment and permission. The first
measurement must show denied reporting. Registering the exact known binding is
a separate command; the second measurement must still show denied reporting.
Only then grant its exact `af3 → ac2`, TCP `19006 → 9006` reporting permission.

Verification requires 100 reporting echoes, receiver-side rejection of unrelated
medical access, and rejection when a guest copies the reporting endpoint's IP/MAC
on an unauthorised attachment. The guest's original identity is restored in
`finally`, including when a check fails. These are static identity/attachment
checks, not cryptographic authentication. Reset removes registration and the
permission via the normal baseline, restores the prepared interface and verifies
that reporting is denied again. Ping alone never establishes reporting access.

## Student 4: recover a partial incident-profile update

```bash
demo rollback setup
demo rollback inject
demo rollback inspect
demo rollback rollback
demo rollback verify
demo rollback reset
```

Setup saves the normal V1 profile with actual coordination rules on `bb2`.
Injection prepares V2 evacuation queues, pauses after `bb1` is installed/read back,
isolates `bb2`'s control connection and resumes to obtain explicit partial status.
This is the user-approved whole-profile version, not a dedicated one-class update.

Inspect per-switch V1/V2 status, the isolated connection and actual flow snapshots.
Choose saved V1 via the explicit `rollback` action. Reachable switches are restored;
the isolated switch remains unverified in management status. `verify` separately
labels local read-only inspection of its retained V1 and checks all saved owned
rules/queue rates, absence of pending V2, 100 coordination echoes through the
isolated switch and 100 local dispatch echoes.

Isolation retargets the existing controller rows to a reserved non-listening
loopback port, retaining UUIDs and rules. It never removes the last controller
or writes to an isolated datapath. The connection stays isolated throughout
rollback and verification. Only `reset` restores the original controller targets,
verifies recovery, clears any incomplete update and applies the normal baseline.
Old unused V2 queues can remain owned until session teardown. No multi-switch
atomicity, throughput increase from rollback or general controller cluster is
claimed.

## End the session

```bash
demo session stop
```

Terminal 1 performs owned process, queue and network cleanup, preserving evidence.
Ctrl+C there also requests cleanup. Do not replace cleanup with global process
kills or `mn -c` while a session is active. A successful full gate requires all
four demonstrations and resets, clean controller/process/network logs, and three
inspected repetitions. Supplementary access-switch/partition behavior retains its
Phase 2 evidence; no newly combined correlated-fault rehearsal is claimed here.
