"""Capture corruption, denied delivery and expanded harness safety regressions."""
import json
from pathlib import Path
import socket
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from config.expanded import build_inventory
from monitoring.pcap_checks import capture_counts
from tests import expanded_harness as harness


def pcap(path, frames):
    header = struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
    path.write_bytes(header + b"".join(struct.pack("<IIII", 0, 0, len(f), len(f)) + f for f in frames))


def echo(source="10.30.1.10", target="10.31.1.10", sequence=1):
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28, 0, 0, 63, 1, 0, socket.inet_aton(source), socket.inet_aton(target))
    return b"\0" * 12 + b"\x08\x00" + ip + struct.pack("!BBHHH", 8, 0, 0, 1, sequence)


class ExpandedEvidenceTests(unittest.TestCase):
    def test_health_failure_preserves_diagnostics_and_cleans_all_owned_processes(self):
        inventory, net = build_inventory(), Mock()
        processes = []
        def popen(*args, **kwargs):
            process = Mock(returncode=None)
            process.poll.return_value = None
            processes.append(process)
            return process
        def stop(process):
            process.returncode = 0
            process.poll.return_value = 0
        net.__getitem__ = Mock(return_value=Mock(popen=popen))
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(harness, "wait_health", side_effect=AssertionError("startup failed")), \
                patch.object(harness, "snapshot", side_effect=RuntimeError("readback unavailable")), \
                patch.object(harness, "host_command", return_value="[]"), \
                patch.object(harness, "stop_process", side_effect=stop) as stopped:
            with self.assertRaisesRegex(AssertionError, "startup failed"):
                with harness.health_processes(net, inventory, Path(temp)):
                    self.fail("Failed health startup must not enter lab checks")
            diagnostics = json.loads((Path(temp) / "health_failure_diagnostics.json").read_text())
            self.assertEqual(diagnostics["snapshot_error"], "readback unavailable")
            self.assertEqual(set(diagnostics), {"snapshot_error", "af1", "bf1"})
            self.assertEqual(stopped.call_count, 14)
            self.assertTrue(all(p.poll() == 0 for p in processes))
            cleanup = json.loads((Path(temp) / "health_cleanup.json").read_text())
            self.assertEqual(cleanup["errors"], [])
            self.assertEqual(len(cleanup["returncodes"]), 14)

    def matrix_fixture(self):
        inventory = SimpleNamespace(ENDPOINTS={"a": SimpleNamespace(ip="10.0.0.1"),
                                               "b": SimpleNamespace(ip="10.0.0.2")},
                                    permitted=lambda a, b: a == "a")
        processes = [Mock(returncode=0), Mock(returncode=1)]
        processes[0].communicate.return_value = ("100 packets transmitted, 100 received", "")
        processes[1].communicate.return_value = ("100 packets transmitted, 0 received", "")
        net = {"a": Mock(), "b": Mock()}
        net["a"].popen.return_value, net["b"].popen.return_value = processes
        return inventory, net, processes

    def test_matrix_preserves_successes_and_stops_every_owned_process(self):
        inventory, net, processes = self.matrix_fixture()
        with tempfile.TemporaryDirectory() as temp, patch.object(harness, "stop_process") as stop:
            results = harness.matrix(net, inventory, Path(temp), "matrix", parallel=2)
            self.assertTrue(all(r["passed"] for r in results))
            self.assertEqual(json.loads((Path(temp) / "matrix.json").read_text()), results)
            self.assertEqual({c.args[0] for c in stop.call_args_list}, set(processes))
            self.assertEqual(net["a"].popen.call_args.args[0][4], "100")

    def test_matrix_rejects_local_errors_duplicates_and_incomplete_denials(self):
        invalid = [(1, "100 packets transmitted, 0 received, +3 errors"),
                   (1, "99 packets transmitted, 0 received"),
                   (0, "100 packets transmitted, 0 received"),
                   (1, "100 packets transmitted, 0 received, 1 duplicates")]
        for code, output in invalid:
            with self.subTest(output=output, code=code):
                inventory, net, processes = self.matrix_fixture()
                processes[1].returncode = code
                processes[1].communicate.return_value = (output, "")
                with tempfile.TemporaryDirectory() as temp, patch.object(harness, "stop_process") as stop:
                    with self.assertRaisesRegex(AssertionError, "matrix failed"):
                        harness.matrix(net, inventory, Path(temp), "matrix", parallel=2)
                    self.assertFalse(json.loads((Path(temp) / "matrix.json").read_text())[-1]["passed"])
                    self.assertEqual(stop.call_count, 2)

    def test_matrix_timeout_stops_whole_started_batch(self):
        inventory, net, processes = self.matrix_fixture()
        processes[0].communicate.side_effect = TimeoutError("stalled probe")
        with tempfile.TemporaryDirectory() as temp, patch.object(harness, "stop_process") as stop:
            with self.assertRaisesRegex(TimeoutError, "stalled probe"):
                harness.matrix(net, inventory, Path(temp), "matrix", parallel=2)
            self.assertEqual({c.args[0] for c in stop.call_args_list}, set(processes))

    def test_complete_matrix_and_unknown_broadcast_control(self):
        inventory = build_inventory()
        guest = inventory.ENDPOINTS["as3"]
        target = guest.ip.rsplit(".", 1)[0] + ".250"
        arp = (b"\xff" * 6 + b"\x02" * 6 + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
               + b"\x02" * 6 + socket.inet_aton(guest.ip) + b"\0" * 6 + socket.inet_aton(target))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name, endpoint in inventory.ENDPOINTS.items():
                frames = [echo(e.ip, endpoint.ip, sequence=n) for source, e in inventory.ENDPOINTS.items()
                          if inventory.permitted(source, name) for n in (1, 2)]
                if name == "as3":
                    frames += [arp] * 10
                pcap(directory / (name + ".pcap"), frames)
            harness.audit_captures(inventory, directory, 2)
            data = json.loads((directory / "receiver_audit.json").read_text())
            self.assertEqual(len(data["pairs"]), 552)
            with (directory / "ah1.pcap").open("ab") as fh:
                fh.write(struct.pack("<IIII", 0, 0, len(arp), len(arp)) + arp)
            with self.assertRaisesRegex(AssertionError, "Unknown ARP"):
                harness.audit_captures(inventory, directory, 2)

    def test_pcap_counts_requests_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data.pcap"
            pcap(path, [echo(), echo(), echo(sequence=2)])
            counts, _ = capture_counts(path)
            self.assertEqual(sum(counts.values()), 3)
            self.assertEqual(max(counts.values()), 2)

    def test_capture_truncation_or_wrong_linktype_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data.pcap"
            for data in (b"", b"invalid header", struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 113),
                         struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1) + b"bad"):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    capture_counts(path)

    def test_receiver_audit_rejects_forbidden_delivery_even_if_ping_failed(self):
        inventory = build_inventory()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in inventory.ENDPOINTS:
                pcap(directory / (name + ".pcap"), [])
            pcap(directory / "ah1.pcap", [echo(inventory.ENDPOINTS["as3"].ip, inventory.ENDPOINTS["ah1"].ip)])
            with self.assertRaisesRegex(AssertionError, "forbidden delivery"):
                harness.audit_captures(inventory, directory, 100)
            report = json.loads((directory / "receiver_audit.json").read_text())
            leaked = next(r for r in report["pairs"] if (r["source"], r["target"]) == ("as3", "ah1"))
            self.assertEqual((leaked["expected"], leaked["observed"]), (0, 1))

    def test_unprivileged_preflight_and_occupied_resources_are_refused(self):
        inventory = build_inventory()
        with patch.object(harness.os, "geteuid", return_value=1000), patch.object(harness, "command") as command:
            with self.assertRaisesRegex(AssertionError, "no lab started"):
                harness.preflight(inventory, harness.DEFAULT_RYU)
            command.assert_not_called()
        with patch.object(harness.os, "geteuid", return_value=0), patch.object(harness, "command", return_value="bb1"), \
                patch.object(harness, "live_interfaces") as interfaces:
            with self.assertRaisesRegex(AssertionError, "already exists"):
                harness.preflight(inventory, harness.DEFAULT_RYU)
            interfaces.assert_not_called()


if __name__ == "__main__":
    unittest.main()
