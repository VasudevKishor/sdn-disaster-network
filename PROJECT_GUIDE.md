# SDN Emergency Communications Project Guide

This is the practical guide for preparing the machine, launching each version of
the lab, operating the four student demonstrations, running the acceptance
checks, and finding the saved results. Run commands from the project root unless
a section says otherwise.

```bash
cd /home/rithvikmukka/sdn-lab/sdn-project
```

## Project status

The project implements and has passed the live gates for Phases 1–4. The main
disaster-response topology has two districts, 20 Open vSwitches and 24 endpoints.
The three full Phase 4 rehearsals ran all four demonstrations and their resets.
Their audit is `monitoring/evidence/phase4_live_audit_2026-10-16.json`.

Phase 5 comparative experiments and Phase 6 presentation preparation/rehearsal
remain. In particular, the older `performance/run_tests.py --mode baseline` and
`--mode qos` commands are foundation measurements; they do not complete the
planned best-effort versus fixed-QoS versus adaptive-policy comparison.

## What runs where

- **System Python** runs Mininet, the harnesses, controllers, and utilities.
- **Pinned Ryu Python** runs the Ryu controller. In the recorded workspace it is
  `../ryu/ryu-env/bin/python` (Python 3.9, Ryu 4.34).
- **Mininet** creates Linux host namespaces and virtual links.
- **Open vSwitch** forwards using OpenFlow 1.3 rules installed by Ryu.
- **HTB queues** shape traffic on selected switch egress ports.

The full topology has backbone switches `bb1`–`bb4`, aggregation switches
`aag1`, `aag2`, `bag1`, `bag2`, and 12 access switches. The hosts represent command
centres, hospitals, field bases, shelters, and isolated shelter guests. The
shared inventory and port map are in `config/expanded.py` and
`docs/13_expanded_topology.md`.

The current normal application runs four incident profiles: normal, evacuation,
search and rescue, and degraded. The operator checks available paths, endpoint
identity, service permissions, switch egress capacity and queue rates before it
applies policy. The controller does not create physical connectivity or restore
power; the topology and link faults are emulated on one Linux machine.

## Prepare and check the machine

Use Ubuntu/Linux with Mininet, Open vSwitch, iproute2, iperf2 and tcpdump. The
recorded environment was Ubuntu 24.04, Python 3.12, Mininet 2.3, OVS 3.3 and
iperf2 2.1.9. Install missing system packages with:

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iproute2 iperf tcpdump curl
```

Check the system and pinned controller runtimes:

```bash
python3 --version
../ryu/ryu-env/bin/python --version
mn --version
ovs-vsctl --version
iperf --version
```

The controller lock is `requirements-controller.lock`. It records the known
Python 3.9 Ryu environment; do not install that lock into system Python 3.12.
If Python 3.9 is installed and the sibling Ryu environment is missing, recreate
it from the project root with:

```bash
mkdir -p ../ryu
python3.9 -m venv ../ryu/ryu-env
../ryu/ryu-env/bin/python -m pip install -r requirements-controller.lock
```

The environment and compatibility notes are in
`docs/02_tools_and_environment.md`.

Before any live harness or interactive lab, close other Mininet sessions and
check that no project lab is still running. Each live runner owns its controller,
switches, interfaces and processes. Do not run two project labs at once. Do not
use global `mn -c`, broad process-name kills, or OVS database deletion to recover
a failed run; preserve its evidence and inspect the reported directory first.

## Recommended launch for the four live demonstrations

The Phase 4 presentation session owns the full lab, controller, policy watcher,
health monitors and application servers. Use two terminals.

In **Terminal 1**, start the session and leave it running:

```bash
sudo python3 -m demo_scripts.session
```

It prints `Demo session ready:` and an evidence directory. In **Terminal 2**, set
`DIR` to that exact path (copy it from the first terminal):

```bash
DIR='/home/rithvikmukka/sdn-lab/sdn-project/monitoring/evidence/phase4-sessions/PASTE_SESSION_NAME'
demo() { sudo python3 -m demo_scripts.control --state-dir "$DIR" "$@"; }
demo session status
```

Keep the directory exact. Choose an empty directory if you start the session with
`--state-dir`; existing evidence is never overwritten. If the checkout path is
too long for the local Unix control socket, use a short empty path such as
`/tmp/sdn-demo`:

```bash
sudo python3 -m demo_scripts.session --state-dir /tmp/sdn-demo
```

In Terminal 2, set `DIR=/tmp/sdn-demo`. The local control socket is root-only.
Use one demonstration at a time. Complete its `reset` before starting another.

### Demonstration 1: medical traffic under bulk congestion

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

The medical TCP echo workload sends 8 KiB requests at 250 per second from `ah1`
to `ac2`. Routine bulk traffic runs from `as2` to the same receiver egress and
requests 60 Mbps. The demo measures urgent delivery before, during and after
congestion while the same workloads continue. The correction applies the
evacuation policy and caps bulk traffic. The measured limits are at least 95% of
urgent messages within 250 ms and received p95 no more than 250 ms. Inspect the
reported measurements and queue counters; do not claim recovery if the baseline
already meets the goal or if a run fails verification.

### Demonstration 2: choose a constrained route while a link stays down

```bash
demo routing setup
demo routing inject
demo routing inspect
demo routing fix --candidate 1
demo routing verify
demo routing reset
```

`inspect` lists candidate routes and whether their capacity fits. Use a listed
feasible candidate number; replace `1` if necessary. The demo normally fails
`bb1–bb3` and asks the chosen route to avoid `bb1`, but use the run's printed
topology and options as the authority. Verification checks the route in both
directions while the failed link remains down, plus successful coordination and
a forbidden guest probe.

### Demonstration 3: register and grant one reporting service

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

The prepared endpoint `af3` starts inactive and unregistered. The first measure
must show reporting denied. Registration adds its configured binding but does
not grant access, so the second measure must still be denied. Granting then
permits only the configured `af3 → ac2` TCP reporting service. Verification also
tests unrelated medical traffic and a guest copying the endpoint's IP and MAC.
The identity checks are static binding and attachment checks, not cryptographic
authentication.

### Demonstration 4: recover a partially applied profile

```bash
demo rollback setup
demo rollback inject
demo rollback inspect
demo rollback rollback
demo rollback verify
demo rollback reset
```

The helper checkpoints a whole evacuation profile across the topology, isolates
`bb2`'s controller connection and exposes an explicit partial state. Rollback
restores reachable switches; `bb2` is inspected locally without writing to the
isolated switch. Coordination and dispatch are checked while isolation remains.
Reset restores the controller connection and normal profile. This is the
approved whole-profile rollback demonstration; it does not claim atomic
multi-switch updates.

When all four demos are finished, stop the session from Terminal 2:

```bash
demo session stop
```

Terminal 1 then performs owned cleanup and exits. Keep the session directory for
logs and evidence.

## Phase 3 interactive policy lab

Use this when you want the Mininet CLI and direct operator commands rather than
the four scripted Phase 4 student sequences. Do not run it at the same time as
the Phase 4 session.

In Terminal 1:

```bash
sudo python3 -m topology.run_incident
```

The terminal prints a `Session:` directory and opens the Mininet CLI. In another
terminal, use that exact directory:

```bash
DIR='/home/rithvikmukka/sdn-lab/sdn-project/monitoring/evidence/phase3-sessions/PASTE_SESSION_NAME'
sudo python3 -m policy.operator --state-dir "$DIR" status
sudo python3 -m policy.operator --state-dir "$DIR" inspect --details
sudo python3 -m policy.operator --state-dir "$DIR" verify
sudo python3 -m policy.operator --state-dir "$DIR" preview --profile evacuation
sudo python3 -m policy.operator --state-dir "$DIR" apply --profile evacuation
```

Other operator actions include `candidates`, `constrain`, `register`, `grant`,
`deny`, `unregister`, `rates`, `stage`, `resume` and `rollback`. Read
`docs/14_phase3.md` before changing service permissions, rates, update order or
constraints. Exit the Mininet CLI normally to stop the owned watcher, servers,
queues and network while preserving the evidence.

## Phase 1 foundation lab

The small foundation topology is a separate three-switch chain `s1–s2–s3` with
seven hosts. It is useful for the original flow, QoS and measurement exercises;
it is not the full two-district disaster network.

Open two terminals. In **Terminal 1**, start Ryu:

```bash
DISASTER_QOS=1 ../ryu/ryu-env/bin/ryu-manager \
  --ofp-tcp-listen-port 6633 --wsapi-host 127.0.0.1 \
  ryu.app.ofctl_rest controller/flow_manager_app.py
```

In **Terminal 2**, start Mininet:

```bash
sudo python3 topology/run_network.py
```

The runner waits for the controller, performs `pingall`, then opens the Mininet
CLI. Exit with `exit` to stop that lab. The manual flow script can list/add/delete
its example rule while the controller REST API is running:

```bash
bash demo_scripts/manual_flow_insertion.sh list
bash demo_scripts/manual_flow_insertion.sh add
bash demo_scripts/manual_flow_insertion.sh delete
```

Apply or remove the foundation QoS setup only while that lab is active:

```bash
sudo bash qos/qos_setup.sh
sudo bash qos/qos_teardown.sh
```

QoS teardown removes the project-owned HTB configuration; it does not rebuild
the original TCLink qdiscs. Restart the owned Mininet lab for a fresh link-only
configuration.

The foundation baseline and QoS measurements each start their own scoped lab.
Close the interactive lab first:

```bash
sudo python3 performance/run_tests.py --mode baseline
sudo python3 performance/run_tests.py --mode qos
python3 performance/run_tests.py --manual
```

The two modes record the Phase 1 workload to timestamped output directories.
They are not the planned Phase 5 adaptive-policy comparison.

## Validation commands

Offline validation does not need `sudo` and does not start a Mininet kernel
dataplane. It runs the phase's applicable Python tests, pinned-Ryu checks, syntax
and file checks, then saves a JSON report:

```bash
python3 scripts/validate_phase1.py
python3 scripts/validate_phase2.py
python3 scripts/validate_phase3.py
python3 scripts/validate_phase4.py
```

The corresponding live gates require `sudo`, system tools, the pinned Ryu
environment and an otherwise idle lab machine:

```bash
sudo python3 -m tests.lab_harness --repetitions 3
sudo python3 -m tests.phase2_harness --repetitions 3
sudo python3 -m tests.expanded_harness --repetitions 3
sudo python3 -m tests.phase3_harness --repetitions 3
sudo python3 -m tests.phase4_harness --repetitions 3
```

The Phase 2 prototype gate and the 20-switch expanded gate are separate checks.
The Phase 4 command runs all four student demos and verified resets three times.
It can take several minutes and stops at the first failure. To diagnose one
Phase 4 sequence, run a focused check:

```bash
sudo python3 -m tests.phase4_harness --demo congestion --repetitions 1
sudo python3 -m tests.phase4_harness --demo routing --repetitions 1
sudo python3 -m tests.phase4_harness --demo onboarding --repetitions 1
sudo python3 -m tests.phase4_harness --demo rollback --repetitions 1
```

A focused run writes `FOCUSED_PASS`, not the complete gate's `PASS`. The Phase 3
diagnostics `--recovery-only` and `--updates-only` also produce their own scoped
markers and cannot stand in for a full Phase 3 run. Do not repeat an unchanged
live gate merely to get a newer timestamp; rerun after a relevant code or
environment change, or when the run's evidence is incomplete.

## Evidence and reading results

Harness output gives a timestamped directory under `monitoring/evidence/`.
Keep that entire directory when reporting a failure. Useful files include:

- `manifest.json`: repetition number, scope and the acceptance configuration.
- `results.json`: measured outcomes and phase-level status.
- `failure.txt`: first failure reported by the harness, if any.
- `commands.jsonl`: separate Phase 4 student actions and their results.
- `*.pcap`, `*_receiver.json`, and `*_capture.log`: receiver-side evidence and
  tcpdump completion/drop counts.
- `controller.log`, policy state and cleanup JSON: controller health, actual
  policy state, and owned process/network cleanup.

Only a full-gate `PASS` marker means the selected full harness scope passed.
`FOCUSED_PASS`, `RECOVERY_PASS` and `UPDATES_PASS` mean only the named subset
passed. A printed demo success without a full `PASS` does not certify all
repetitions. The `.gitignore` excludes most generated live evidence to keep large
run artifacts out of source control; preserve local evidence directories for
the assessment and handoff.

The warning
`sch_htb: quantum of class ... is big. Consider r2q change` is emitted by Linux
HTB when a configured class rate implies a large scheduling quantum. It is a
tuning warning and does not by itself mean a run failed. It can matter to packet
scheduling fairness, so use measured throughput, latency, receiver captures and
the harness's final result rather than ignoring failed checks.

## If something fails

1. Save the full terminal output and the evidence path.
2. Read `failure.txt`, the relevant client/receiver JSON and capture log.
3. Check whether the client reports local errors. A denied probe with local
   delivery errors does not prove that the network policy blocked it.
4. Confirm that the harness cleaned up the resources it owns. Do not launch a
   second lab on the same switches while the first is active.
5. Fix the reported cause and rerun the focused check first. Rerun the full gate
   after the focused check passes.

For topology, addressing and exact ports, see `docs/13_expanded_topology.md`.
For incident policy and operator details, see `docs/14_phase3.md`. The four
student scripts and explanations are in `docs/15_phase4_demos.md`. The project
scope, limitations and remaining work are in `PROJECT_DESCRIPTION_AND_PLAN.md`
and `HANDOFF.md`.

## Remaining work

Phase 5 must compare best-effort, fixed QoS and adaptive policy with identical
workloads, save raw configurations and measurements, and generate reproducible
charts. Phase 6 must assemble the presentation, real-world comparison,
source-checked references and failure/dependency explanation, then rehearse the
four demonstrations and unexpected changes. Neither phase is represented as
complete by the existing Phase 1 foundation performance commands.

## Push committed work to GitHub

After committing the changes you want to publish, run:

```bash
git status --short --branch
git push origin main
```

For the configured HTTPS remote, GitHub requires a personal access token at the
password prompt; the website account password does not work. This repository is
public and owned by a different account, so a classic token with `public_repo`
scope is the narrow token scope for public-repository access. The GitHub account
used for the token must already have write access to the repository. Type the
token into the terminal prompt only; never put it in a command, source file or
chat message.
