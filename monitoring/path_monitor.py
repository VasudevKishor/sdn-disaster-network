"""Remote UDP round-trip probes per bond slave; no gateway-local health claims.

Run inside a configured Mininet host namespace. Only configured health peers and
reserved ports are supported. Raw packet sockets require CAP_NET_RAW; changing
the active slave requires CAP_NET_ADMIN. No changes occur on import.
"""
import argparse
from contextlib import ExitStack
import errno
import json
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import struct
import subprocess
import time

from config.phase2 import (ENDPOINTS, SITES, HEALTH_TARGETS, HEALTH_PORT,
                           health_source_port, load_inventory)

MAGIC = b"SDNP2HP1"
PAYLOAD_SIZE = len(MAGIC) + 16
INTERVAL, TIMEOUT, THRESHOLD, HOLD_DOWN = .25, .15, 3, 1.0
SOL_PACKET, PACKET_AUXDATA, TP_STATUS_CSUMNOTREADY = 263, 8, 1 << 3
PATH_ERRORS = frozenset((errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTDOWN,
                         errno.EHOSTUNREACH, errno.ENODEV, errno.ENXIO, errno.ECONNREFUSED))
REFLECTOR_ERRORS = PATH_ERRORS | {errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS, errno.ETIMEDOUT}


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def mac_bytes(mac):
    return bytes.fromhex(mac.replace(":", ""))


def probe_frame(source, target, attachment, payload):
    src, dst = ENDPOINTS[source], ENDPOINTS[target]
    udp = struct.pack("!HHHH", health_source_port(attachment), HEALTH_PORT, 8 + len(payload), 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                     socket.inet_aton(src.ip), socket.inet_aton(dst.ip))
    ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
    ethernet = mac_bytes(SITES[src.site].gateway_mac) + mac_bytes(src.mac) + b"\x08\x00"
    return ethernet + ip + udp


def valid_reply(data, source, target, attachment, payload, checksum_pending=False):
    """Accept only an intact matching response to this cycle's fresh nonce."""
    src, dst = ENDPOINTS[source], ENDPOINTS[target]
    if len(data) < 42 or data[:14] != mac_bytes(src.mac) + mac_bytes(SITES[src.site].gateway_mac) + b"\x08\x00":
        return False
    ip = data[14:]
    ihl = (ip[0] & 15) * 4
    if ip[0] >> 4 != 4 or ihl < 20 or len(ip) < ihl + 8:
        return False
    length = struct.unpack_from("!H", ip, 2)[0]
    if (length > len(ip) or length < ihl + 8 or checksum(ip[:ihl]) != 0
            or struct.unpack_from("!H", ip, 6)[0] & 0x3fff or ip[8] == 0 or ip[9] != 17
            or ip[12:20] != socket.inet_aton(dst.ip) + socket.inet_aton(src.ip)):
        return False
    sport, dport, size, check = struct.unpack_from("!HHHH", ip, ihl)
    if ((sport, dport) != (HEALTH_PORT, health_source_port(attachment))
            or size != 8 + len(payload) or ihl + size != length or ip[ihl + 8:length] != payload):
        return False
    pseudo = ip[12:20] + struct.pack("!BBH", 0, 17, size)
    # Virtual links may expose CHECKSUM_PARTIAL before kernel completion. Only
    # trusted socket ancillary metadata may indicate this; the exact nonce,
    # tuple, sizes, Ethernet identity and IPv4 checksum are still checked.
    return check == 0 or checksum_pending or checksum(pseudo + ip[ihl:length]) == 0


def checksum_pending(ancillary):
    return any(level == SOL_PACKET and kind == PACKET_AUXDATA and len(data) >= 4
               and struct.unpack_from("=I", data)[0] & TP_STATUS_CSUMNOTREADY
               for level, kind, data in ancillary)


def probe_round(endpoint, target, attachments, sockets, stopped):
    """One bounded probe window; a failed interface must not kill monitoring."""
    started = time.monotonic()
    payloads, received, send_errors, receive_errors, pending_checksums = {}, set(), {}, {}, set()
    waiting = dict(sockets)
    for name, peer in sockets.items():
        payloads[name] = MAGIC + secrets.token_bytes(16)
        try:
            peer.send(probe_frame(endpoint, target, attachments[name], payloads[name]))
        except OSError as exc:
            if exc.errno not in PATH_ERRORS:
                raise
            send_errors[name] = str(exc)
            waiting.pop(name)
    deadline = started + TIMEOUT
    while not stopped() and waiting and time.monotonic() < deadline:
        readable, _, _ = select.select(list(waiting.values()), [], [], max(0, deadline - time.monotonic()))
        for peer in readable:
            name = next(n for n, sock in waiting.items() if sock is peer)
            try:
                data, ancillary, flags, _address = peer.recvmsg(65535, socket.CMSG_SPACE(20))
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno not in PATH_ERRORS:
                    raise
                receive_errors[name] = str(exc)
                # Avoid spinning on an errored socket for the rest of this
                # window. Retry it normally in the next round after recovery.
                waiting.pop(name)
                continue
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                continue
            pending = checksum_pending(ancillary)
            if valid_reply(data, endpoint, target, attachments[name], payloads[name], pending):
                received.add(name)
                waiting.pop(name)
                if pending:
                    pending_checksums.add(name)
    return received, send_errors, receive_errors, pending_checksums


def announce_attachment(peer, endpoint):
    try:
        peer.send(gratuitous_arp(endpoint))
    except OSError as exc:
        if exc.errno not in PATH_ERRORS:
            raise
        return str(exc)
    return None


def gratuitous_arp(endpoint):
    identity = ENDPOINTS[endpoint]
    mac, ip = mac_bytes(identity.mac), socket.inet_aton(identity.ip)
    return (b"\xff" * 6 + mac + b"\x08\x06"
            + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + mac + ip + b"\0" * 6 + ip)


class PathHealth:
    """Consecutive outcomes, no automatic failback, no guessing when all fail."""
    def __init__(self, names):
        self.paths = {name: {"successes": 0, "failures": 0, "healthy": False} for name in names}
        self.last_change = float("-inf")

    def update(self, received):
        for name, state in self.paths.items():
            if name in received:
                state["successes"] += 1
                state["failures"] = 0
                if state["successes"] >= THRESHOLD:
                    state["healthy"] = True
            else:
                state["failures"] += 1
                state["successes"] = 0
                if state["failures"] >= THRESHOLD:
                    state["healthy"] = False

    def candidate(self, active, now):
        if now - self.last_change < HOLD_DOWN:
            return None
        if active in self.paths and self.paths[active]["failures"] < THRESHOLD:
            return None
        return next((name for name, state in self.paths.items()
                     if name != active and state["healthy"] and state["failures"] == 0), None)


def write_status(path, status):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, indent=2) + "\n")
    temporary.replace(path)


def active_slave():
    report = Path("/proc/net/bonding/bond0").read_text()
    found = re.search(r"^Currently Active Slave: (\S+)", report, re.M)
    return found[1] if found and found[1] != "None" else None


def reflect(endpoint, status_path, stopped):
    allowed = {(ENDPOINTS[name].ip, health_source_port(a)) for name, target in HEALTH_TARGETS.items()
               if target == endpoint for a in ENDPOINTS[name].attachments}
    if not allowed:
        raise ValueError("Endpoint is not a configured health reflector")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind((ENDPOINTS[endpoint].ip, HEALTH_PORT))
        # One shared reflector serves every critical endpoint in its district.
        # A congested/unresolved return path must not block all other peers or
        # kill reflection. Drop that echo; monitors must earn health with a
        # later fresh nonce, never a queued/retried stale response.
        peer.setblocking(False)
        status = {"ready": True, "endpoint": endpoint, "received": 0,
                  "replied": 0, "rejected": 0, "send_errors": 0, "receive_errors": 0,
                  "last_error": None}
        updated = float("-inf")
        while not stopped():
            now = time.monotonic()
            if now - updated >= INTERVAL:
                write_status(status_path, dict(status, monotonic=now))
                updated = now
            readable, _, _ = select.select([peer], [], [], TIMEOUT)
            if not readable or stopped():
                continue
            try:
                data, address = peer.recvfrom(2048)
            except OSError as exc:
                if not isinstance(exc, socket.timeout) and exc.errno not in REFLECTOR_ERRORS:
                    raise
                status["receive_errors"] += 1
                status["last_error"] = {"operation": "receive", "error": str(exc)}
                continue
            status["received"] += 1
            if address not in allowed or len(data) != PAYLOAD_SIZE or not data.startswith(MAGIC):
                status["rejected"] += 1
                continue
            try:
                peer.sendto(data, address)
            except OSError as exc:
                if not isinstance(exc, socket.timeout) and exc.errno not in REFLECTOR_ERRORS:
                    raise
                status["send_errors"] += 1
                status["last_error"] = {"operation": "send", "peer": list(address), "error": str(exc)}
            else:
                status["replied"] += 1
        write_status(status_path, dict(status, ready=False, monotonic=time.monotonic()))


def monitor(endpoint, status_path, stopped):
    target = HEALTH_TARGETS[endpoint]
    attachments = {"%s-eth%d" % (endpoint, a.host_port): a for a in ENDPOINTS[endpoint].attachments}
    health, previous, pending_announcement = PathHealth(attachments), active_slave(), None
    with ExitStack() as stack:
        sockets, guards = {}, {}
        for name in attachments:
            # Bind the probe's UDP port so a reply arriving on the active bond
            # does not provoke an ICMP port-unreachable from the local kernel.
            guard = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            guard.bind((ENDPOINTS[endpoint].ip, health_source_port(attachments[name])))
            guard.setblocking(False)
            guards[name] = guard
            # ETH_P_ALL receives a tap on the physical slave before bonding's
            # receive handler can discard inactive-slave IP traffic.
            peer = stack.enter_context(socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)))
            peer.setsockopt(SOL_PACKET, PACKET_AUXDATA, 1)
            peer.bind((name, 3))  # Explicit ETH_P_ALL on this physical slave.
            peer.setblocking(False)
            sockets[name] = peer
        while not stopped():
            started = time.monotonic()
            received, send_errors, receive_errors, pending_checksums = probe_round(
                endpoint, target, attachments, sockets, stopped)
            guard_errors = {}
            for name, guard in guards.items():
                try:
                    while True:
                        guard.recv(2048)
                except BlockingIOError:
                    pass
                except OSError as exc:
                    if exc.errno not in PATH_ERRORS:
                        raise
                    guard_errors[name] = str(exc)
            health.update(received)
            active = active_slave()
            event = None
            if active != previous:
                health.last_change = time.monotonic()
                event = {"reason": "kernel_bond_change", "from": previous, "to": active}
            candidate = health.candidate(active, time.monotonic())
            if candidate is not None:
                subprocess.run(["ip", "link", "set", "dev", "bond0", "type", "bond", "active_slave", candidate],
                               check=True, capture_output=True, text=True, timeout=2)
                observed = active_slave()
                if observed != candidate:
                    raise RuntimeError("Bond did not select requested slave: " + str(observed))
                event = {"reason": "remote_path_failure", "from": active, "to": observed}
                active = observed
                health.last_change = time.monotonic()
            if event is not None:
                pending_announcement = active
            announcement_error = None
            if pending_announcement == active and active in sockets:
                announcement_error = announce_attachment(sockets[active], endpoint)
                if announcement_error is None:
                    pending_announcement = None
            previous = active
            status = {"endpoint": endpoint, "reflector": target, "monotonic": time.monotonic(),
                      "active": active, "paths": health.paths, "send_errors": send_errors,
                      "receive_errors": receive_errors, "guard_errors": guard_errors,
                      "announcement_error": announcement_error,
                      "checksum_offload_seen": sorted(pending_checksums),
                      "available": active in health.paths and health.paths[active]["healthy"], "event": event}
            write_status(status_path, status)
            print(json.dumps(status), flush=True)
            remaining = INTERVAL - (time.monotonic() - started)
            if remaining > 0:
                # Small bounded wait; SIGTERM stops by the next cycle.
                time.sleep(remaining)


def main():
    global ENDPOINTS, SITES, HEALTH_TARGETS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("monitor", "reflect"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--inventory", choices=("prototype", "expanded"), default="prototype")
    parser.add_argument("--districts", choices=(1, 2), type=int, default=2)
    parser.add_argument("--status-file", required=True, type=Path)
    args = parser.parse_args()
    inventory = load_inventory(args.inventory, args.districts)
    ENDPOINTS, SITES, HEALTH_TARGETS = inventory.ENDPOINTS, inventory.SITES, inventory.HEALTH_TARGETS
    valid = HEALTH_TARGETS if args.mode == "monitor" else set(HEALTH_TARGETS.values())
    if args.endpoint not in valid:
        parser.error("Endpoint is not configured for this health role")
    state = {"stop": False}
    def stop(_signum, _frame):
        state["stop"] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.mode == "monitor":
        monitor(args.endpoint, args.status_file, lambda: state["stop"])
    else:
        reflect(args.endpoint, args.status_file, lambda: state["stop"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
