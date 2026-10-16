# Phase 2: expanded topology and live acceptance

The agreed topology is implemented: two districts, four backbone switches, four
aggregation switches, twelve access switches and twenty-four application hosts.
There are eight physical sites and ten logical subnets. Each shelter's third
host is an isolated guest in its own subnet, on the shelter access switch.
Phase 1 and the three-switch prototype remain available with their original commands.

The expanded offline suite has 143 passing tests, including real Ryu/OpenFlow
serialization and a controller connected to twenty emulated OpenFlow peers.
Three full privileged repetitions also passed and their raw evidence was audited
on 2026-10-16. **The implemented Phase 2 gate is satisfied.**

The inspected runs are `20261016T035837210877Z`, `20261016T040326661411Z` and
`20261016T040818018679Z`. Each passes all 552 directed pairs, with exactly 41,400
permitted echo requests at receivers, zero forbidden requests and zero duplicates.
Ethernet rewriting and TTL were checked in the receiver pcaps. All critical
access, aggregation/backbone and partition checks, spoof rejection, current-row
reconnections, sticky restoration and owned cleanup pass.

All 600 load probes across these repetitions arrived. Per-source p95 RTTs range
from 24.7 to 45.8 ms, versus the 250 ms limit. Access recovery-probe completion
ranges from 0.735 to 1.045 s; aggregation recovery from 1.129 to 1.402 s, and
backbone-link recovery from 0.799 to 0.800 s. Average system CPU ranges from
7.58% to 8.57%; minimum sampled available memory across the runs is 6086 MiB.
These are observed recovery-probe times and a feasible-load smoke gate, not
precise outage durations or the later application/policy comparison.
Audit: [saved results](../monitoring/evidence/phase2_expanded_live_audit_2026-10-16.json).
No unchanged rerun is required; retain these commands for affected regressions.

## Commands

From the repository root:

```bash
python3 scripts/validate_phase2.py
python3 -m config.expanded
sudo python3 -m tests.expanded_harness --repetitions 3
```

The last command is the new full-size gate. Allow several minutes per repetition;
the harness stops at the first failure and prints its evidence directory.
Do not start another lab using these bridge or interface names concurrently.
The harness refuses existing named resources, owns its controller and subprocesses,
and verifies cleanup without global `mn -c`, process-name kills or OVSDB destruction.
It needs the existing Mininet, OVS, tcpdump, iperf, iproute2 and pinned Ryu environment.
System Python runs Mininet; `../ryu/ryu-env/bin/python` runs Ryu.

`--districts 1` selects a ten-switch, twelve-host diagnostic subset. It cannot
certify the twenty-switch gate. `--parallel` adjusts probe concurrency (default
twelve); the manifest records it. Keep the default for comparable acceptance runs.

## Forwarding and isolation

`config/expanded.py` is the shared source for topology, controller, monitors and
tests. Configured port descriptions must match before a switch joins the trusted
graph. Controller management is over host loopback, outside the emulated data paths.
The controller computes deterministic loop-free paths over usable links. Unequal
link capacities are modeled; capacity admission and incident profiles belong to Phase 3.

For a cross-subnet packet the host resolves its site's virtual gateway. Proxy ARP
answers configured identities on permitted attachments; unknown broadcasts are
not flooded. The ingress rule checks attachment, source IP/MAC and gateway MAC,
rewrites Ethernet source/destination and decrements TTL once. Transit switches
forward the rewritten packet; the destination access switch delivers it. Return
traffic has its own computed path. Same-subnet packets retain their TTL.
Rules fail closed for unknown identities, fragments and unsupported traffic.

The initial Phase 2 matrix permits operational sites to communicate except
hospital-to-shelter and shelter-to-hospital, including across districts. Guests
cannot communicate with other subnets. This yields 414 permitted and 138 denied
directed pairs out of 552 distinct-host pairs. This is fixed site admission,
not the later service-specific policy engine. No cryptographic identity is claimed.

All twelve command/hospital hosts have active-backup bonds over two access
switches in their site's subnet. Per-slave remote probes use that district's
field host (`af1` or `bf1`) as reflector. The validated prototype's hysteresis,
hold-down, active-slave readback and sticky restoration are retained. Reflector,
monitor, host power and shared upstream infrastructure remain dependencies;
two access switches alone do not eliminate them.

Reflectors use nonblocking UDP replies: transient send/receive errors drop that
echo and are recorded in periodic status counters rather than terminating the
shared process. `replied` counts socket-accepted sends, not remote delivery.
Only a matching fresh response at the physical-slave monitor earns path health.
Unknown socket/permission errors remain fatal. A health failure also preserves
switch/bond snapshots and reflector route/neighbor diagnostics before teardown.

The expanded controller reuses the prototype's gateway and identity checks but
reconciles only changed owned rules, installing downstream rules first and
strictly deleting obsolete matches. Reconnection invalidates its installed-rule
cache. This reduces full-network churn; it is not an atomic multi-switch update.
Unchanged routes also reuse their compiled matches/actions instead of rebuilding
and JSON-encoding every rule on every topology event. Cache signatures include
the entire route and current datapath connection identities; features/reset events
invalidate the cache. Both attachment-specific health routes remain separate.
This reduces repeated controller work during restoration bursts. The three
inspected full Mininet runs include restoration and subsequent gateway/load checks.
All switches use secure fail mode, with no NORMAL/FLOOD fallback over the mesh.

## Frozen live checks and evidence

Thresholds are in `config/expanded_acceptance.json`, copied into each manifest.
Warmup is separately labeled and cannot satisfy the measured matrix.

- Every directed pair sends 100 measured probes: permitted pairs need 100 replies,
  denied pairs zero, with complete attempts and no local errors or duplicates.
- All twenty-four receiving application interfaces are captured simultaneously.
  The audit requires 100 delivered requests per permitted pair, zero per denied
  pair, and no duplicate requests. Ten unknown ARP requests must appear at their
  origin and at no other host. Kernel capture-drop counters must be zero.
- A guest impersonates a registered command host's IP, then MAC and IP. Each case
  sends 100 attempts without local errors. A hospital receiver capture must retain
  only three legitimate positive-control requests.
- Each critical site's primary access bridge is removed while endpoint carrier
  stays up. All three affected hosts must select their backup and complete a
  recovery probe within five seconds. Stable forward/return probes and isolation
  checks follow while the fault remains. Restoration must reconnect the current
  OVSDB controller rows and retain the sticky backup selection.
- Aggregation loss and backbone-link loss require recovery within five seconds
  plus stable bidirectional probes. A complete district partition must preserve
  local communication, block cross-district traffic and recover after restoration.
- Four concurrent paced TCP streams request the unchanged iperf rate `10M` each
  for fifteen seconds to a field host with a shared 50 Mbps access link. In the
  installed iperf2, uppercase `M` uses binary units: about 10.486 Mbps per stream,
  41.94 Mbps total, as the saved output shows (see the
  [iperf2 manual](https://iperf2.sourceforge.io/iperf-manpage.html)). Each stream must have nonzero
  measured throughput. Before starting clients, all health paths and bounded
  forward/return startup probes must recover after fault restoration. Before
  starting measured probes, each receiver must have one established connection
  with at least 64 KiB received, within five seconds; a live client process alone
  is insufficient. Socket counters must advance for every stream during the
  measured probe window, with clients alive throughout. Fifty concurrent ICMP probes per source need 100% delivery
  and p95 RTT at most 250 ms. Average system CPU must be at most 85%, with at least
  512 MiB MemAvailable at sampled points. Background host activity contributes
  to these system readings. CPU sampling starts after payload readiness.
  No slow samples are removed from the fifty measured probes. Startup attempts
  are separately retained, alongside periodic host/switch qdisc backlog/drop
  counters and host neighbor state during load. These diagnose remaining delays;
  later failures can be diagnosed with the retained live snapshots. This is a feasible-load resource smoke check, not
  urgent-message delivery, adaptive policy evaluation or a saturation benchmark.
- Owned network/process cleanup and clean controller logs are required for PASS.

Artifacts under `monitoring/evidence/phase2-expanded/<UTC timestamp>/` include
manifest, raw ping results, receiver pcaps and audit, capture logs, controller log,
switch flows/ports/fail modes, bonds, health status/logs, fault carrier and timing,
reconnection readbacks, resource samples, raw load output and cleanup results.
`load_startup*_recovery.json` retains network startup attempts; `load_readiness.json`
retains per-socket readiness, `load_progress.json` the before/after receiver byte
counters, and `load_network.json` the periodic queue/neighbor snapshots.
Failures preserve available artifacts and write `failure.txt`; PASS is written
only after implemented checks and cleanup complete. Fault timings measure first
successful recovery-probe completion, not exact interruption duration.

The existing HTB quantum tuning warnings may still appear because Mininet labels
nonempty `tc` output as an error. Shaping defaults have not been changed to hide
them. Judge acceptance by actual checks and artifacts.

## Exact topology map

The following inventory tables are generated by
`python3 scripts/export_expanded_topology.py`. Regenerate after inventory edits.
Infrastructure switch interfaces are `<switch>-eth<port>`. Host attachments list
host interface to switch port. Critical applications use `bond0`; other hosts use
their single `eth0`. Host access links are 50 Mbps. Infrastructure rates follow.

<!-- generated-inventory -->

### Switches

| Switch | DPID (decimal) | Role |
|---|---:|---|
| bb1 | 301 | backbone |
| bb2 | 302 | backbone |
| bb3 | 303 | backbone |
| bb4 | 304 | backbone |
| aag1 | 311 | aggregation |
| aag2 | 312 | aggregation |
| acc1 | 321 | access |
| acc2 | 322 | access |
| ahc1 | 323 | access |
| ahc2 | 324 | access |
| afc1 | 325 | access |
| asc1 | 326 | access |
| bag1 | 313 | aggregation |
| bag2 | 314 | aggregation |
| bcc1 | 327 | access |
| bcc2 | 328 | access |
| bhc1 | 329 | access |
| bhc2 | 330 | access |
| bfc1 | 331 | access |
| bsc1 | 332 | access |

### Subnets and virtual gateways

| Logical site | Subnet | Gateway | Gateway MAC |
|---|---|---|---|
| a_command | 10.30.1.0/24 | 10.30.1.1 | 02:30:01:01:00:01 |
| a_hospital | 10.30.2.0/24 | 10.30.2.1 | 02:30:01:02:00:01 |
| a_field | 10.30.3.0/24 | 10.30.3.1 | 02:30:01:03:00:01 |
| a_shelter | 10.30.4.0/24 | 10.30.4.1 | 02:30:01:04:00:01 |
| a_guest | 10.30.5.0/24 | 10.30.5.1 | 02:30:01:05:00:01 |
| b_command | 10.31.1.0/24 | 10.31.1.1 | 02:30:02:01:00:01 |
| b_hospital | 10.31.2.0/24 | 10.31.2.1 | 02:30:02:02:00:01 |
| b_field | 10.31.3.0/24 | 10.31.3.1 | 02:30:02:03:00:01 |
| b_shelter | 10.31.4.0/24 | 10.31.4.1 | 02:30:02:04:00:01 |
| b_guest | 10.31.5.0/24 | 10.31.5.1 | 02:30:02:05:00:01 |

### Hosts and attachment ports

| Host | Logical site | IP | MAC | Host interface → switch port |
|---|---|---|---|---|
| ac1 | a_command | 10.30.1.10 | 02:31:01:01:01:01 | ac1-eth0 → acc1:1; ac1-eth1 → acc2:1 |
| ac2 | a_command | 10.30.1.11 | 02:31:01:01:02:01 | ac2-eth0 → acc1:2; ac2-eth1 → acc2:2 |
| ac3 | a_command | 10.30.1.12 | 02:31:01:01:03:01 | ac3-eth0 → acc1:3; ac3-eth1 → acc2:3 |
| ah1 | a_hospital | 10.30.2.10 | 02:31:01:02:01:01 | ah1-eth0 → ahc1:1; ah1-eth1 → ahc2:1 |
| ah2 | a_hospital | 10.30.2.11 | 02:31:01:02:02:01 | ah2-eth0 → ahc1:2; ah2-eth1 → ahc2:2 |
| ah3 | a_hospital | 10.30.2.12 | 02:31:01:02:03:01 | ah3-eth0 → ahc1:3; ah3-eth1 → ahc2:3 |
| af1 | a_field | 10.30.3.10 | 02:31:01:03:01:01 | af1-eth0 → afc1:1 |
| af2 | a_field | 10.30.3.11 | 02:31:01:03:02:01 | af2-eth0 → afc1:2 |
| af3 | a_field | 10.30.3.12 | 02:31:01:03:03:01 | af3-eth0 → afc1:3 |
| as1 | a_shelter | 10.30.4.10 | 02:31:01:04:01:01 | as1-eth0 → asc1:1 |
| as2 | a_shelter | 10.30.4.11 | 02:31:01:04:02:01 | as2-eth0 → asc1:2 |
| as3 | a_guest | 10.30.5.12 | 02:31:01:04:03:01 | as3-eth0 → asc1:3 |
| bc1 | b_command | 10.31.1.10 | 02:31:02:01:01:01 | bc1-eth0 → bcc1:1; bc1-eth1 → bcc2:1 |
| bc2 | b_command | 10.31.1.11 | 02:31:02:01:02:01 | bc2-eth0 → bcc1:2; bc2-eth1 → bcc2:2 |
| bc3 | b_command | 10.31.1.12 | 02:31:02:01:03:01 | bc3-eth0 → bcc1:3; bc3-eth1 → bcc2:3 |
| bh1 | b_hospital | 10.31.2.10 | 02:31:02:02:01:01 | bh1-eth0 → bhc1:1; bh1-eth1 → bhc2:1 |
| bh2 | b_hospital | 10.31.2.11 | 02:31:02:02:02:01 | bh2-eth0 → bhc1:2; bh2-eth1 → bhc2:2 |
| bh3 | b_hospital | 10.31.2.12 | 02:31:02:02:03:01 | bh3-eth0 → bhc1:3; bh3-eth1 → bhc2:3 |
| bf1 | b_field | 10.31.3.10 | 02:31:02:03:01:01 | bf1-eth0 → bfc1:1 |
| bf2 | b_field | 10.31.3.11 | 02:31:02:03:02:01 | bf2-eth0 → bfc1:2 |
| bf3 | b_field | 10.31.3.12 | 02:31:02:03:03:01 | bf3-eth0 → bfc1:3 |
| bs1 | b_shelter | 10.31.4.10 | 02:31:02:04:01:01 | bs1-eth0 → bsc1:1 |
| bs2 | b_shelter | 10.31.4.11 | 02:31:02:04:02:01 | bs2-eth0 → bsc1:2 |
| bs3 | b_guest | 10.31.5.12 | 02:31:02:04:03:01 | bs3-eth0 → bsc1:3 |

### Infrastructure links

| Switch A:port | Switch B:port | Mbps |
|---|---|---:|
| bb1:10 | bb2:10 | 100 |
| bb3:10 | bb4:10 | 100 |
| bb1:11 | bb3:11 | 100 |
| bb2:11 | bb4:11 | 50 |
| bb1:12 | bb4:12 | 25 |
| bb2:12 | bb3:12 | 50 |
| aag1:10 | bb1:13 | 100 |
| aag1:11 | bb2:13 | 100 |
| aag2:10 | bb1:14 | 50 |
| aag2:11 | bb2:14 | 50 |
| acc1:10 | aag1:12 | 100 |
| acc1:11 | aag2:12 | 50 |
| acc2:10 | aag1:13 | 100 |
| acc2:11 | aag2:13 | 50 |
| ahc1:10 | aag1:14 | 100 |
| ahc1:11 | aag2:14 | 50 |
| ahc2:10 | aag1:15 | 100 |
| ahc2:11 | aag2:15 | 50 |
| afc1:10 | aag1:16 | 100 |
| afc1:11 | aag2:16 | 50 |
| asc1:10 | aag1:17 | 100 |
| asc1:11 | aag2:17 | 50 |
| bag1:10 | bb3:13 | 100 |
| bag1:11 | bb4:13 | 100 |
| bag2:10 | bb3:14 | 50 |
| bag2:11 | bb4:14 | 50 |
| bcc1:10 | bag1:12 | 100 |
| bcc1:11 | bag2:12 | 50 |
| bcc2:10 | bag1:13 | 100 |
| bcc2:11 | bag2:13 | 50 |
| bhc1:10 | bag1:14 | 100 |
| bhc1:11 | bag2:14 | 50 |
| bhc2:10 | bag1:15 | 100 |
| bhc2:11 | bag2:15 | 50 |
| bfc1:10 | bag1:16 | 100 |
| bfc1:11 | bag2:16 | 50 |
| bsc1:10 | bag1:17 | 100 |
| bsc1:11 | bag2:17 | 50 |
