"""Concurrent-load readiness regressions and real four-stream loopback I/O.

Loopback validates workload orchestration, not Mininet shaping or its RTT gate.
"""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from config.expanded import build_inventory
from tests import expanded_harness as harness
from tests.test_phase1_workload_processes import LocalHost


class ExpandedLoadTests(unittest.TestCase):
    def test_startup_failure_retains_source_and_receiver_network_snapshots(self):
        inventory = build_inventory()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(harness, "load_network_snapshot", side_effect=[{"phase": "before"}, {"phase": "failure"}]) as snapshot, \
                patch.object(harness, "wait_health", side_effect=AssertionError("paths not ready")):
            with self.assertRaisesRegex(AssertionError, "paths not ready"):
                harness.load_check({}, inventory, Path(temp), [])
            data = json.loads((Path(temp) / "load_network.json").read_text())
            self.assertEqual(data, [{"phase": "before"}, {"phase": "failure"}])
            self.assertEqual(snapshot.call_args.args[2], ("ac1", "ah1", "bc1", "bh1", "bf2"))

    def test_recorded_high_rtt_still_fails_unchanged_gate(self):
        measured = {s: {"packets_sent": 50, "packets_received": 50, "delivery_fraction": 1.0,
                        "latency_p95_ms": value} for s, value in
                    (("ac1", 1609), ("ah1", 201), ("bc1", 162), ("bh1", 1584))}
        with self.assertRaisesRegex(AssertionError, "ac1: 50/50 replies, p95 1609.*bh1: 50/50 replies, p95 1584"):
            harness.require_load_delivery(measured)
        measured["ac1"]["latency_p95_ms"] = measured["bh1"]["latency_p95_ms"] = 250
        harness.require_load_delivery(measured)

    def test_loss_or_incomplete_probe_sets_cannot_pass(self):
        for sent, received in ((49, 49), (50, 49)):
            with self.subTest(sent=sent, received=received):
                with self.assertRaisesRegex(AssertionError, "resource gate failed"):
                    harness.require_load_delivery({"ac1": {"packets_sent": sent, "packets_received": received,
                        "delivery_fraction": received / sent, "latency_p95_ms": 10}})

    def test_live_clients_without_received_payload_are_not_ready(self):
        clients = [(s, Mock()) for s in ("ac1", "ah1", "bc1", "bh1")]
        for _, process in clients:
            process.poll.return_value = None
        empty = {s: {"connections": 0, "bytes_received": 0, "raw": ""} for s, _ in clients}
        full = {s: {"connections": 1, "bytes_received": 65536, "raw": "socket data"} for s, _ in clients}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(harness, "receiver_progress", side_effect=[empty, full]), \
                patch.object(harness.time, "sleep") as slept:
            result = harness.wait_load_ready({}, "bf2", clients, Path(temp))
            self.assertEqual(result, full)
            attempts = json.loads((Path(temp) / "load_readiness.json").read_text())
            self.assertEqual([a["streams"] for a in attempts], [empty, full])
            slept.assert_called_once()

    def test_unready_stream_times_out_with_raw_evidence(self):
        client = Mock()
        client.poll.return_value = None
        progress = {"ac1": {"connections": 1, "bytes_received": 24, "raw": "header only"}}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(harness, "receiver_progress", return_value=progress), \
                patch.object(harness.time, "monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(AssertionError, "did not become ready"):
                harness.wait_load_ready({}, "bf2", [("ac1", client)], Path(temp))
            self.assertEqual(json.loads((Path(temp) / "load_readiness.json").read_text())[0]["streams"], progress)

    def test_receiver_progress_requires_actual_established_socket_byte_counters(self):
        outputs = ["", "0 0 local peer\n bytes_received:24", "0 0 local peer\n bytes_received:131072"]
        with patch.object(harness, "host_command", side_effect=outputs) as called:
            result = harness.receiver_progress({}, "bf2", ())
            self.assertEqual(result, {})
        net = {"bf2": object()}
        with patch.object(harness, "host_command", side_effect=outputs) as called:
            result = harness.receiver_progress(net, "bf2", ("ac1", "ah1", "bc1"))
            self.assertEqual([p["bytes_received"] for p in result.values()], [0, 24, 131072])
            self.assertEqual([p["connections"] for p in result.values()], [0, 1, 1])
            self.assertIn("established", called.call_args.args[1])

    def test_real_four_streams_ready_before_all_fifty_probes_and_owned_cleanup(self):
        for name in ("iperf", "ss", "ping"):
            if shutil.which(name) is None:
                self.skipTest("Missing workload executable: " + name)
        reservations = []
        for base in range(40000, 40100, 4):
            try:
                for port in range(base, base + 4):
                    peer = socket.socket()
                    reservations.append(peer)
                    peer.bind(("127.0.0.1", port))
                break
            except OSError:
                for peer in reservations:
                    peer.close()
                reservations = []
        else:
            self.fail("No free four-port loopback range")
        for peer in reservations:
            peer.close()
        inventory = build_inventory()
        inventory.ENDPOINTS = {n: replace(e, ip="127.0.0.1") for n, e in inventory.ENDPOINTS.items()}
        host = LocalHost()
        host.name = "loopback"
        names = ("ac1", "ah1", "bc1", "bh1", "bf2")
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(harness, "LOAD_PORT_BASE", base), \
                patch.object(harness, "load_network_snapshot", return_value={"scope": "loopback workload test"}):
            try:
                harness.load_check({n: host for n in names}, inventory, Path(temp), [])
            finally:
                self.assertTrue(all(p.poll() is not None for p in host.processes))
            result = json.loads((Path(temp) / "resource_load.json").read_text())
            progress = json.loads((Path(temp) / "load_progress.json").read_text())
            raw = json.loads((Path(temp) / "load_raw.json").read_text())
            self.assertEqual(len(host.processes), 12)
            for name in names[:-1]:
                self.assertEqual(result["flows"][name]["packets_received"], 50)
                self.assertGreater(result["flows"][name]["throughput_mbps"], 0)
                self.assertGreater(progress["after_probes"][name]["bytes_received"],
                                   progress["before_probes"][name]["bytes_received"])
                self.assertIn(name + "_client", raw)
                self.assertIn(name + "_server", raw)


if __name__ == "__main__":
    unittest.main()
