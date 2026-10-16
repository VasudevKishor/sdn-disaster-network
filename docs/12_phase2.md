# Phase 2: gateway and usable-path failover prototype

Status: the gateway/usable-path prototype, stronger spoof checks and all three
remote-fault/restoration scenarios passed three inspected live repetitions on
2026-10-16. See the latest handoff and
`monitoring/evidence/phase2_prototype_live_audit_2026-10-16.json`.
This is not the 20-switch Phase 2 completion gate.
The existing Phase 1 topology, controller, QoS scripts and tests are preserved.

## Inventory and packet journey

`config/phase2.py` is the prototype's shared inventory. Three switches use DPIDs
201-203 and names distinct from Phase 1; all are configured for OpenFlow 1.3,
secure fail-mode and out-of-band control over root loopback. No endpoint has a
management interface or an application path through controller connectivity.

| Endpoint | Site/address | Gateway | Attachment |
|---|---|---|---|
| p2h1 | command, 10.20.1.10/24 | 10.20.1.1 | bond0: eth0 to p2s1:1; eth1 to p2s2:1 |
| p2h2 | hospital, 10.20.2.10/24 | 10.20.2.1 | eth0 to p2s3:1 |
| p2h3 | guest, 10.20.3.10/24 | 10.20.3.1 | eth0 to p2s3:2 |

The links are p2s1:10--p2s3:10 at 100 Mbps and
p2s2:10--p2s3:11 at 50 Mbps; host links are 50 Mbps. TCLink supplies these
prototype limits. The new controller does not configure OVS QoS or assign queues.

For a command-to-hospital request:

1. The command host routes the off-subnet destination through 10.20.1.1 on bond0.
2. The controller validates the ARP sender's IP, MAC and permitted local
   attachment, then answers only that host with the command gateway MAC.
3. The source access switch matches ingress, Ethernet addresses, IPv4 source and
   destination, protocol, and the OVS non-fragment field. It decrements TTL once,
   sets source Ethernet to the hospital gateway MAC and destination Ethernet to
   the hospital host MAC, then outputs on the topology-derived path.
4. Other switches match the expected ingress and transformed addresses. They
   preserve TTL and deliver one copy to the hospital. The mesh of switches acts
   as transport for one logical routed hop, not a router hop at each switch.
5. The reply uses the hospital gateway and a separately generated reverse route,
   rewriting to the command gateway/host Ethernet pair and decrementing once.

Gateways answer ARP; they are not host processes running ICMP or other services.
The implementation supports IPv4 ICMP/TCP/UDP between the command and hospital
endpoints. Guest-to-operational traffic and its reverse are denied. This is a
fixed site isolation matrix, not Phase 3 destination-service permissions.

## Loops, identity and state

Unknown broadcast, IPv6, unknown destinations, fragments and invalid attachments
are dropped. There is no FLOOD, NORMAL, or fallback learning action. Proxy ARP
does not extend cross-site address resolution. Configured physical connections
are trusted; local OpenFlow port numbers/names and availability are checked.
This does not discover or detect a cable connected to the wrong peer.

Only p2h1's two configured access attachments can claim its IP/MAC. Bond gratuitous
ARP or a valid source request can update its active attachment; an unrelated host
or a core port cannot. Static binding is not cryptographic authentication.
Rules use a separate Phase 2 ownership cookie namespace. Reconnection clears only
that namespace. Port changes/mobility refresh owned route rules, downstream first,
with brief interruption possible; this is not a verified multi-switch transaction.
OpenFlow errors mark the affected switch unusable rather than announcing success.

## Bond and usable-path monitoring

The host uses active-backup, stable MAC, 100 ms carrier monitoring (`miimon`),
and `primary_reselect=failure` to avoid automatic failback on link restoration.
The kernel handles directly attached carrier loss. **`miimon` does not detect
remote uplink failure or forwarding blackholes while host carrier remains up.**

`monitoring/path_monitor.py` now adds a separate host-side usable-path monitor.
The live harness owns one monitor in p2h1 and one UDP reflector in p2h2, starting
them after controller connection and stopping them before network teardown.
It refuses to proceed unless both attachment paths return valid probes.

- Each physical slave sends a fresh nonce in a small IPv4/UDP frame to the remote
  hospital endpoint. Probe ports are reserved: destination 61999, source 62000
  for eth0 and 62001 for eth1. The reflector echoes only configured source IP/port
  pairs and the expected bounded payload format. It is a lab health service.
- Controller rules match the configured identity, exact ingress and UDP ports,
  forwarding each reply back to the attachment being tested. Backup probes do
  not change the application's active attachment. Reserved-port drops prevent
  generic UDP rules from silently returning probes through another attachment.
  The same gateway Ethernet rewrite/TTL handling applies to health traffic.
- Raw packet taps on each physical slave inspect the reply before inactive-slave
  IP delivery is discarded by bonding. Replies must match Ethernet/IP/UDP
  identity, integrity checks, attachment and this cycle's nonce. Old, unrelated,
  corrupted and cross-interface responses do not prove health. UDP sockets also
  bind the source ports to prevent the local kernel emitting port-unreachable
  responses when a health reply arrives through the active slave.
  Kernel `PACKET_AUXDATA` distinguishes a partial offloaded UDP checksum on
  virtual links from a bad completed checksum. In that case identity, header
  lengths, IPv4 checksum and exact nonce still must match; status records the
  interfaces where partial-checksum metadata was seen.
- Probes run every 250 ms with a 150 ms response window. Three consecutive
  successes qualify a backup; three failures disqualify a path. Selection requires
  a current successful backup probe, with a one-second hold-down after changes.
  If the active path fails, the monitor requests that healthy slave through
  `ip link ... active_slave`, verifies bond readback, and sends gratuitous ARP so
  the controller refreshes ordinary forward/return rules. A healthy current
  slave stays selected after the primary recovers. When both probes fail it
  reports unavailable and does not guess a working alternate.
- Expected per-device network errors, including `ENETDOWN` during an injected
  link fault, count as failed probes on that interface. The other interface
  continues probing, and the failed socket is retried in the next round so link
  restoration can recover. Status records send/receive errors; transient GARP
  announcement failures are retried. Unexpected permission/descriptor errors
  still fail the monitor rather than being reported as ordinary path loss.

This demonstrates reachability to **one configured remote reflector**, not every
destination/service. Reflector or monitor failure is a dependency; it is not
cryptographic identity or a production HA agent. The management path carries no
probes. Carrier-only tests remain separate, and the new kernel packet-tap/bond
interaction must pass live before expansion to the full topology.

[Linux bonding monitoring documentation](https://docs.kernel.org/6.17/networking/bonding.html)
describes the differences between carrier and ARP monitoring.
[Linux packet socket documentation](https://man7.org/linux/man-pages/man7/packet.7.html)
describes raw link-layer packet taps and their capability requirement.
[Ryu OpenFlow 1.3 reference](https://ryu.readthedocs.io/en/latest/ofproto_v1_3_ref.html)
documents the port events, match structures and actions used by the app.

## Offline checks

From the repository root:

```bash
python3 scripts/validate_phase2.py
```

The script uses system Python for Mininet topology construction without network
startup, and `../ryu/ryu-env/bin/python` for real Ryu packet/parser checks and
actual controller processes with loopback OpenFlow peers. It also runs Phase 1
regressions, compiles repository Python in memory without executing ad hoc tests,
checks shell/JSON syntax, and saves raw evidence. Pass `--ryu-python` and `--output`
to override the interpreter and evidence destination.

The suite also covers nonce/attachment/integrity checks, hysteresis, no failback,
unavailable paths, reserved-port isolation and a real loopback UDP reflector.
The three-peer protocol check is real controller/wire evidence. It is not an
OVS dataplane, kernel-bond, throughput, outage, or full-topology resource test.

## Privileged live checks

With no existing p2s1/p2s2/p2s3 bridges or prototype interfaces:

```bash
sudo python3 -m tests.phase2_harness --repetitions 3
```

The harness refuses occupied resources, owns its controller/network/processes,
and avoids global Mininet cleanup or killing unrelated processes. Non-root
invocation saves explicit failure evidence and starts no network.

If warmup fails, inspect that run's `controller.log`. In particular,
`OPENFLOW_ERROR ... type=4 code=9` means `OFPBMC_BAD_PREREQ`: a flow match
was rejected. Ryu's default field sorting puts Nicira `ip_frag` before its
EtherType prerequisite. Both controllers use `controller/matches.py` to put
the fragment field after the standard fields, preserving fragment rejection.
The offline checks verify this order in the serialized controller rules.
Mininet's `sch_htb ... quantum ... is big` messages are separate traffic-shaper
warnings; they do not explain these OpenFlow match rejections.
HTB derives a scheduling batch size (quantum) from rate divided by `r2q`; its
automatic value at these rates triggers a tuning warning. Installed Mininet
prefixes all nonempty `tc` output with `*** Error:`, including warning text.
The three inspected live passes retained these warnings. See the
[HTB manual](https://www.man7.org/linux/man-pages/man8/tc-htb.8.html).

Implemented checks include forward/return 100-probe connectivity, site isolation,
receiver-side capture of legitimate controls and 100 IP/100 MAC-IP spoof attempts
after warmup, gateway Ethernet/TTL transformation, expired-TTL rejection, local
primary-link bond failover and restoration, stable backup selection, controller
errors and owned-resource cleanup. Raw pcap, flows, ports, addresses, bond state,
probe output and approximate event timing are retained per run. A failed ping
alone is not treated as proof that a spoofed request never reached its receiver.
The spoof test reinstalls the gateway neighbour after changing the MAC and
requires all attempts without local delivery errors. The older three prototype
PASS runs had local errors in this case and do not certify this strengthened check.

Additional live scenarios remove the currently active remote uplink, remove its
OVS access bridge while retaining endpoint interfaces, and install one temporary
owned IPv4 drop while leaving port state up. Every scenario verifies retained
host carrier, a healthy alternate bond slave and a successful recovery probe
within five seconds, then stable 100-probe forward/return connectivity and guest
isolation while the fault remains injected. Restoration must retain the healthy
backup and recover both health paths. Fault state is restored in `finally`;
no system-wide OVS/Mininet cleanup is used.

After recreating an access bridge, the harness refreshes Mininet's cached
controller UUIDs before waiting for connection. OVSDB controller rows can be
recreated with new UUIDs; checking a deleted cached row gives a false timeout.
`p2s1_reconnection.json` retains the current rows, targets and authoritative
`is_connected` readback. A real failed connection still fails the harness.

`path_monitor.jsonl`, `path_status.json`, reflector logs, process cleanup state,
per-fault carrier readback, recovery probes, flow/bond snapshots and
`remote_path_timing.json` are retained. Status is written atomically and must be
fresh; stale or failed monitor processes cannot establish recovery.

The harness records bond detection and successful warmup completion separately;
these are not exact first-recovered-packet timestamps. It does not measure the
later urgent-message application. Live failures must be fixed before reporting
this prototype validated or using it as proof for a larger topology.

## Next work in the agreed phases

The strengthened gateway/usable-path harness has passed three repetitions. Grow
configuration toward both districts and the 20-switch target, preserving these checks.
Provide the multiple surviving paths needed for the constrained-routing demo.
The full connectivity/isolation matrices, loop evidence and presentation-machine
load gate remain open. Operator profiles, capacity accounting, constrained-route
commands, endpoint registration, and partial-update rollback belong to Phase 3;
the four student-led demonstrations are integrated and rehearsed in Phase 4.
