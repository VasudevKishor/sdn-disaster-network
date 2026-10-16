"""Probe wire validation and failover decisions, without root/network mutation."""
import json
import errno
from pathlib import Path
import socket
import struct
import tempfile
import unittest
import threading
import time
from dataclasses import replace
from unittest.mock import patch

from config.phase2 import ENDPOINTS, SITES, Attachment, HEALTH_PORT, health_source_port
from monitoring import path_monitor as monitor
from tests.test_phase2_model import ready_state
from tests import phase2_harness as harness


def reply(payload, attachment=None):
    attachment = attachment or ENDPOINTS["p2h1"].attachments[0]
    src, dst = ENDPOINTS["p2h2"], ENDPOINTS["p2h1"]
    udp = struct.pack("!HHHH", HEALTH_PORT, health_source_port(attachment), 8 + len(payload), 0) + payload
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 63, 17, 0,
                         socket.inet_aton(src.ip), socket.inet_aton(dst.ip))
    header = header[:10] + struct.pack("!H", monitor.checksum(header)) + header[12:]
    return monitor.mac_bytes(dst.mac) + monitor.mac_bytes(SITES[dst.site].gateway_mac) + b"\x08\x00" + header + udp


class ProbeSocket:
    def __init__(self, attachment, send_error=None, receive_error=None):
        self.attachment, self.send_error, self.receive_error = attachment, send_error, receive_error
        self.payload, self.announcements = b"", 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def bind(self, *args):
        pass

    def setsockopt(self, *args):
        pass

    def setblocking(self, *args):
        pass

    def send(self, packet):
        if self.send_error is not None:
            raise OSError(self.send_error, "injected device failure")
        if packet[12:14] == b"\x08\x06":
            self.announcements += 1
        else:
            self.payload = packet[42:]
        return len(packet)

    def recvmsg(self, *args):
        if self.receive_error is not None:
            raise OSError(self.receive_error, "injected device failure")
        return reply(self.payload, self.attachment), [], 0, None


class ProbeDeviceFailureTests(unittest.TestCase):
    def setUp(self):
        self.attachments = {"p2h1-eth%d" % a.host_port: a for a in ENDPOINTS["p2h1"].attachments}
        self.sockets = {name: ProbeSocket(a) for name, a in self.attachments.items()}

    def probe(self):
        with patch.object(monitor.select, "select", side_effect=lambda peers, *args: (peers, [], [])):
            return monitor.probe_round("p2h1", "p2h2", self.attachments, self.sockets, lambda: False)

    def test_receive_network_down_fails_only_that_path_and_recovers_next_round(self):
        self.sockets["p2h1-eth0"].receive_error = errno.ENETDOWN
        received, send_errors, receive_errors, _ = self.probe()
        self.assertEqual(received, {"p2h1-eth1"})
        self.assertEqual(send_errors, {})
        self.assertEqual(set(receive_errors), {"p2h1-eth0"})
        self.sockets["p2h1-eth0"].receive_error = None
        self.assertEqual(self.probe()[0], set(self.attachments))

    def test_both_down_sends_do_not_spin_or_report_health(self):
        for peer in self.sockets.values():
            peer.send_error = errno.ENETDOWN
        with patch.object(monitor.select, "select") as selected:
            received, send_errors, receive_errors, _ = monitor.probe_round(
                "p2h1", "p2h2", self.attachments, self.sockets, lambda: False)
        self.assertEqual(received, set())
        self.assertEqual(set(send_errors), set(self.attachments))
        self.assertEqual(receive_errors, {})
        selected.assert_not_called()

    def test_unexpected_permission_or_descriptor_errors_still_fail(self):
        for where, error in (("send_error", errno.EPERM), ("receive_error", errno.EBADF)):
            with self.subTest(where=where):
                peer = self.sockets["p2h1-eth0"]
                setattr(peer, where, error)
                with self.assertRaises(OSError) as raised:
                    self.probe()
                self.assertEqual(raised.exception.errno, error)
                setattr(peer, where, None)

    def test_announcement_network_down_can_be_retried(self):
        peer = self.sockets["p2h1-eth1"]
        peer.send_error = errno.ENETDOWN
        self.assertIsNotNone(monitor.announce_attachment(peer, "p2h1"))
        peer.send_error = None
        self.assertIsNone(monitor.announce_attachment(peer, "p2h1"))
        self.assertEqual(peer.announcements, 1)

    def test_monitor_survives_receive_failure_switches_and_stays_on_backup(self):
        # Exercise the actual monitor loop through the fault seen in live logs,
        # selection/readback, GARP and primary recovery. Only OS I/O is modeled.
        statuses, current, created = [], ["p2h1-eth0"], []
        primary = self.sockets["p2h1-eth0"]
        original_receive = primary.recvmsg
        def receive(*args):
            if 3 <= len(statuses) < 7:
                raise OSError(errno.ENETDOWN, "Network is down")
            return original_receive(*args)
        primary.recvmsg = receive
        class Guard(ProbeSocket):
            def recv(self, *args):
                raise BlockingIOError()
        def socket_factory(family, *args):
            if family == socket.AF_INET:
                return Guard(None)
            name = list(self.attachments)[len(created)]
            created.append(name)
            return self.sockets[name]
        def select_slave(args, **kwargs):
            self.assertEqual(args[:3], ["ip", "link", "set"])
            current[0] = args[-1]
        with patch.object(monitor.socket, "socket", side_effect=socket_factory), \
                patch.object(monitor.select, "select", side_effect=lambda peers, *a: (peers, [], [])), \
                patch.object(monitor, "active_slave", side_effect=lambda: current[0]), \
                patch.object(monitor.subprocess, "run", side_effect=select_slave) as selected, \
                patch.object(monitor, "write_status", side_effect=lambda path, data: statuses.append(json.loads(json.dumps(data)))), \
                patch.object(monitor.time, "sleep"), patch("builtins.print"):
            monitor.monitor("p2h1", Path("unused.json"), lambda: len(statuses) >= 10)
        self.assertEqual(selected.call_count, 1)
        self.assertTrue(any(s["receive_errors"] for s in statuses))
        self.assertEqual(statuses[-1]["active"], "p2h1-eth1")
        self.assertTrue(statuses[-1]["available"])
        self.assertTrue(all(p["healthy"] for p in statuses[-1]["paths"].values()))
        self.assertEqual(self.sockets["p2h1-eth1"].announcements, 1)


class PathDecisionTests(unittest.TestCase):
    def test_requires_consecutive_failure_and_healthy_backup(self):
        state = monitor.PathHealth(("primary", "backup"))
        for _ in range(3):
            state.update({"primary", "backup"})
        for index in range(2):
            state.update({"backup"})
            self.assertIsNone(state.candidate("primary", index))
        state.update({"backup"})
        self.assertEqual(state.candidate("primary", 2), "backup")

    def test_no_failback_after_restoration_or_choice_when_both_fail(self):
        state = monitor.PathHealth(("primary", "backup"))
        for _ in range(3):
            state.update({"primary", "backup"})
        self.assertIsNone(state.candidate("backup", 10))
        for _ in range(3):
            state.update(set())
        self.assertIsNone(state.candidate("primary", 10))

    def test_hold_down_and_current_failed_backup_are_not_selected(self):
        state = monitor.PathHealth(("primary", "backup"))
        for _ in range(3):
            state.update({"backup"})
        state.last_change = 10
        self.assertIsNone(state.candidate("primary", 10.5))
        self.assertEqual(state.candidate("primary", 12), "backup")
        state.update(set())
        self.assertIsNone(state.candidate("primary", 12))

    def test_one_good_probe_does_not_admit_flapping_backup(self):
        state = monitor.PathHealth(("primary", "backup"))
        for _ in range(4):
            state.update(set())
            state.update({"backup"})
            self.assertIsNone(state.candidate("primary", 100))


class ProbePacketTests(unittest.TestCase):
    def setUp(self):
        self.attachment = ENDPOINTS["p2h1"].attachments[0]
        self.payload = monitor.MAGIC + b"0123456789abcdef"

    def accepts(self, packet, payload=None, attachment=None):
        return monitor.valid_reply(packet, "p2h1", "p2h2", attachment or self.attachment, payload or self.payload)

    def test_response_requires_current_nonce_and_correct_physical_attachment(self):
        packet = reply(self.payload)
        self.assertTrue(self.accepts(packet))
        self.assertFalse(self.accepts(packet, payload=monitor.MAGIC + b"fedcba9876543210"))
        self.assertFalse(self.accepts(packet, attachment=ENDPOINTS["p2h1"].attachments[1]))
        self.assertFalse(self.accepts(monitor.probe_frame("p2h1", "p2h2", self.attachment, self.payload)))

    def test_corrupt_truncated_wrong_identity_and_fragmented_frames_are_rejected(self):
        packet = reply(self.payload)
        for end in range(len(packet)):
            self.assertFalse(self.accepts(packet[:end]))
        for index in (0, 6, 12, 14, 16, 20, 23, 26, 30, 34, 36, 38, 42):
            corrupt = bytearray(packet)
            corrupt[index] ^= 1
            self.assertFalse(self.accepts(corrupt), index)
        fragmented = bytearray(packet)
        fragmented[20] = 0x20  # MF, with a valid header checksum.
        fragmented[24:26] = b"\0\0"
        fragmented[24:26] = struct.pack("!H", monitor.checksum(bytes(fragmented[14:34])))
        self.assertFalse(self.accepts(fragmented))

    def test_valid_nonzero_udp_checksum_and_ethernet_padding(self):
        packet = bytearray(reply(self.payload))
        pseudo = bytes(packet[26:34]) + struct.pack("!BBH", 0, 17, len(packet) - 34)
        packet[40:42] = struct.pack("!H", monitor.checksum(pseudo + bytes(packet[34:])))
        self.assertTrue(self.accepts(bytes(packet) + b"\0" * 8))
        packet[-1] ^= 1
        self.assertFalse(self.accepts(bytes(packet)))

    def test_gratuitous_arp_identifies_only_configured_endpoint(self):
        packet = monitor.gratuitous_arp("p2h1")
        self.assertEqual(packet[12:14], b"\x08\x06")
        self.assertEqual(packet[28:32], socket.inet_aton(ENDPOINTS["p2h1"].ip))
        self.assertEqual(packet[38:42], packet[28:32])

    def test_partial_checksums_require_kernel_metadata_and_still_require_nonce(self):
        packet = bytearray(reply(self.payload))
        packet[40:42] = b"\x12\x34"  # Deliberately not a complete checksum.
        self.assertFalse(self.accepts(packet))
        ancillary = [(monitor.SOL_PACKET, monitor.PACKET_AUXDATA,
                      struct.pack("=I", monitor.TP_STATUS_CSUMNOTREADY) + b"\0" * 16)]
        pending = monitor.checksum_pending(ancillary)
        self.assertTrue(pending)
        self.assertTrue(monitor.valid_reply(packet, "p2h1", "p2h2", self.attachment, self.payload, pending))
        packet[-1] ^= 1
        self.assertFalse(monitor.valid_reply(packet, "p2h1", "p2h2", self.attachment, self.payload, pending))
        for invalid in ([], [(0, monitor.PACKET_AUXDATA, ancillary[0][2])],
                        [(monitor.SOL_PACKET, monitor.PACKET_AUXDATA, b"\x08")]):
            self.assertFalse(monitor.checksum_pending(invalid))


class HealthRouteModelTests(unittest.TestCase):
    def test_probe_routes_pin_backup_without_moving_application(self):
        state = ready_state()
        state.observe("p2h1", "p2s1", 1)
        backup = ENDPOINTS["p2h1"].attachments[1]
        self.assertEqual(state.route("p2h1", "p2h2", source_attachment=backup)[0][0], "p2s2")
        self.assertEqual(state.route("p2h2", "p2h1", target_attachment=backup)[-1][0], "p2s2")
        self.assertEqual(state.attachment("p2h1").switch, "p2s1")
        self.assertFalse(state.route("p2h1", "p2h2", source_attachment=Attachment("p2s3", 2)))

    def test_missing_remote_path_cannot_fall_back_to_local_gateway(self):
        state = ready_state()
        primary = ENDPOINTS["p2h1"].attachments[0]
        state.up.remove(("p2s1", 10))
        self.assertFalse(state.route("p2h1", "p2h2", source_attachment=primary))
        self.assertFalse(state.route("p2h2", "p2h1", target_attachment=primary))


class ProbeHarnessTests(unittest.TestCase):
    def test_local_errors_or_incomplete_negative_attempts_do_not_pass(self):
        for text in ("100 packets transmitted, 0 received, +20 errors", "ping: bind: Cannot assign requested address",
                     "1 packets transmitted, 0 received"):
            host = type("Host", (), {"pexec": lambda self, args: (text, "", 1)})()
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temp, self.assertRaises(AssertionError):
                harness.ping({"p2h3": host}, Path(temp), "negative", "p2h3", "p2h2", allowed=False)

    def test_stale_or_unhealthy_status_cannot_report_recovery(self):
        process = type("Process", (), {"poll": lambda self: None})()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "status.json"
            for stamp, available in ((0, True), (100, False)):
                path.write_text(json.dumps({"monotonic": stamp, "available": available}))
                with patch.object(harness.time, "monotonic", side_effect=[100, 100, 106]):
                    with self.assertRaisesRegex(AssertionError, "timed out"):
                        harness.wait_health(path, [process])


class ReflectorProcessTests(unittest.TestCase):
    def run_reflector(self, events, inventory=None):
        inventory = inventory or monitor.load_inventory("expanded")
        payload = monitor.MAGIC + b"0123456789abcdef"
        snapshots = []
        class Peer:
            def __init__(self):
                self.index, self.sent, self.closed = 0, [], False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.closed = True
            def bind(self, address):
                self.bound = address
            def setblocking(self, value):
                self.blocking = value
            def recvfrom(self, size):
                self.event = events[self.index]
                self.index += 1
                if self.event.get("receive_error") is not None:
                    raise self.event["receive_error"]
                return self.event.get("payload", payload), self.event["address"]
            def sendto(self, data, address):
                if self.event.get("send_error") is not None:
                    raise self.event["send_error"]
                self.sent.append((data, address))
        peer = Peer()
        try:
            with patch.object(monitor, "ENDPOINTS", inventory.ENDPOINTS), \
                    patch.object(monitor, "HEALTH_TARGETS", inventory.HEALTH_TARGETS), \
                    patch.object(monitor.socket, "socket", return_value=peer), \
                    patch.object(monitor.select, "select", side_effect=lambda peers, *a: (peers, [], [])), \
                    patch.object(monitor, "write_status", side_effect=lambda path, data: snapshots.append(dict(data))):
                monitor.reflect("af1", Path("unused.json"), lambda: peer.index == len(events))
        finally:
            self.assertTrue(peer.closed)
        self.assertFalse(peer.blocking)
        return peer, snapshots[-1]

    def test_shared_expanded_reflector_survives_timeout_and_serves_all_other_peers(self):
        inventory = monitor.load_inventory("expanded")
        addresses = [(inventory.ENDPOINTS[name].ip, health_source_port(a))
                     for name, target in inventory.HEALTH_TARGETS.items() if target == "af1"
                     for a in inventory.ENDPOINTS[name].attachments]
        self.assertEqual(len(addresses), 12)
        events = [{"address": address} for address in addresses]
        events[0]["send_error"] = socket.timeout("timed out")  # Exact recorded crash.
        events.append({"address": addresses[0]})  # Next fresh probe can recover.
        peer, status = self.run_reflector(events, inventory)
        self.assertEqual([address for _, address in peer.sent], addresses[1:] + addresses[:1])
        self.assertEqual((status["received"], status["replied"], status["send_errors"]), (13, 12, 1))
        self.assertEqual(status["last_error"]["peer"], list(addresses[0]))

    def test_reflector_transient_send_and_receive_errors_do_not_count_as_replies(self):
        address = ("10.30.1.10", 62000)
        for error in (errno.EAGAIN, errno.ENOBUFS, errno.ENETDOWN, errno.ENETUNREACH,
                      errno.EHOSTUNREACH, errno.ECONNREFUSED, errno.ETIMEDOUT):
            with self.subTest(error=error):
                peer, status = self.run_reflector([
                    {"receive_error": OSError(error, "receive failure")},
                    {"address": address, "send_error": OSError(error, "send failure")},
                    {"address": address}])
                self.assertEqual(len(peer.sent), 1)
                self.assertEqual((status["received"], status["replied"], status["send_errors"], status["receive_errors"]),
                                 (2, 1, 1, 1))

    def test_reflector_unexpected_socket_errors_still_fail(self):
        for operation, error in (("send_error", errno.EPERM), ("receive_error", errno.EBADF)):
            with self.subTest(operation=operation):
                with self.assertRaises(OSError) as raised:
                    self.run_reflector([{ "address": ("10.30.1.10", 62000),
                                          operation: OSError(error, "fatal failure")}])
                self.assertEqual(raised.exception.errno, error)

    def test_expanded_reflector_rejects_unknown_identity_port_and_bad_payload(self):
        allowed = ("10.30.1.10", 62000)
        peer, status = self.run_reflector([
            {"address": ("10.30.5.12", 62000)},
            {"address": (allowed[0], 62002)},
            {"address": allowed, "payload": b"invalid"},
            {"address": allowed, "payload": monitor.MAGIC + b"short"},
            {"address": allowed}])
        self.assertEqual(len(peer.sent), 1)
        self.assertEqual((status["received"], status["rejected"], status["replied"]), (5, 4, 1))

    def test_real_udp_echo_requires_configured_source_port_and_nonce_format(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger, tempfile.TemporaryDirectory() as temp:
            client.bind(("127.0.0.1", 0))
            stranger.bind(("127.0.0.1", 0))
            client.settimeout(.2)
            stranger.settimeout(.2)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                destination = reservation.getsockname()
            endpoints = {name: replace(e, ip="127.0.0.1") for name, e in ENDPOINTS.items()}
            stop, failures = threading.Event(), []
            status = Path(temp) / "reflector.json"
            def run():
                try:
                    monitor.reflect("p2h2", status, stop.is_set)
                except Exception as exc:
                    failures.append(exc)
            with patch.object(monitor, "ENDPOINTS", endpoints), patch.object(monitor, "HEALTH_PORT", destination[1]), \
                    patch.object(monitor, "health_source_port", return_value=client.getsockname()[1]):
                thread = threading.Thread(target=run, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 2
                    while not status.exists() and not failures and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(failures, [])
                    self.assertTrue(status.exists())
                    payload = monitor.MAGIC + b"0123456789abcdef"
                    client.sendto(payload, destination)
                    self.assertEqual(client.recvfrom(2048)[0], payload)
                    client.sendto(b"invalid", destination)
                    with self.assertRaises(socket.timeout):
                        client.recvfrom(2048)
                    stranger.sendto(payload, destination)
                    with self.assertRaises(socket.timeout):
                        stranger.recvfrom(2048)
                finally:
                    stop.set()
                    thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
