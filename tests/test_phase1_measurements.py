"""Failure and overlap regressions for the scoped Phase 1 workload."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from config.lab import AGENCY_HOSTS
from performance import run_tests as perf
from tests import lab_harness


class Process:
    def __init__(self, args, output="", error="", code=0, early=False, timeout=False):
        self.args, self.output, self.error = args, output, error
        self.code, self.timeout = code, timeout
        self.returncode = code if early else None

    def poll(self):
        return self.returncode

    def communicate(self, timeout):
        if self.timeout:
            self.timeout = False
            raise subprocess.TimeoutExpired(self.args, timeout, output=self.output)
        if self.returncode is None:
            self.returncode = self.code
        return self.output, self.error

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class Workload:
    def __init__(self, fault=None):
        self.fault, self.processes = fault, []

    def pexec(self, args):
        if self.fault == "listener" and args[0] == "ss":
            return "", "ss: command not found", 127
        return "LISTEN", "", 0

    def popen(self, args, **kwargs):
        if args[0] == "ping":
            process = Process(args, "\n".join(["64 bytes time=20 ms"] * 40)
                              + "\n40 packets transmitted, 40 received")
            if self.fault == "overlap":
                # Workload disappears after probes start, not before startup's poll.
                original = process.communicate
                def complete(timeout):
                    self.processes[2].returncode = 0
                    return original(timeout)
                process.communicate = complete
            elif self.fault == "probe":
                process.output, process.error, process.code = "partial ping output", "ping failed", 2
        elif "-c" in args:
            process = Process(args, "[ 3] 0.0-12.0 sec 30 MBytes 20 Mbits/sec\n")
            if self.fault == "client":
                process.output, process.error, process.code = "partial client output", "connection reset", 1
            elif self.fault == "timeout":
                process.output, process.timeout = "partial timeout output", True
        else:
            process = Process(args, "server diagnostic")
            if self.fault == "server":
                process.error, process.returncode = "address already in use", 1
        self.processes.append(process)
        return process


class Phase1MeasurementTests(unittest.TestCase):
    def run_workload(self, fault=None):
        workload = Workload(fault)
        self.addCleanup(lambda: self.assertTrue(all(p.poll() is not None for p in workload.processes)))
        with patch.object(perf.time, "sleep"):
            return perf.run_all_tests("qos", {h: workload for h in AGENCY_HOSTS})

    def test_missing_listener_tool_is_not_hidden_by_a_shell_pipeline(self):
        with self.assertRaisesRegex(perf.MeasurementError, "ss: command not found"):
            self.run_workload("listener")

    def test_every_probe_finishes_while_both_competing_clients_are_alive(self):
        with self.assertRaisesRegex(perf.MeasurementError, "during probes"):
            self.run_workload("overlap")

    def test_failure_keeps_raw_output_and_stops_all_owned_processes(self):
        for fault, expected in (("probe", "partial ping output"), ("client", "connection reset"),
                                ("timeout", "partial timeout output"), ("server", "address already in use")):
            with self.subTest(fault=fault):
                with self.assertRaises(perf.MeasurementError) as raised:
                    self.run_workload(fault)
                evidence = raised.exception.result
                self.assertEqual(evidence["status"], "failed")
                self.assertIn(expected, json.dumps(evidence["raw"]))

    def test_queue_verification_failure_preserves_measurement_without_success_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            net = type("Net", (), {"hosts": []})()
            measured = {"status": "complete", "flows": {"sentinel": "measured data"}}
            with patch.object(lab_harness, "command", return_value="port 4 queue 0: bytes=10\nport 4 queue 1: bytes=20"), \
                    patch.object(lab_harness, "run_all_tests", return_value=measured):
                with self.assertRaisesRegex(AssertionError, "No measured traffic"):
                    lab_harness.measured_workload(net, "qos", Path(temp))
            evidence = json.loads((Path(temp) / "workload_qos.json").read_text())
            self.assertEqual(evidence, measured)
            self.assertFalse(list(Path(temp).glob("*.csv")))

    def test_live_workload_failure_saves_structured_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(lab_harness, "command", return_value=""), \
                    patch.object(lab_harness, "run_all_tests", side_effect=lambda *a, **k: self.run_workload("probe")):
                with self.assertRaises(perf.MeasurementError):
                    lab_harness.measured_workload(type("Net", (), {"hosts": []})(), "qos", Path(temp))
            evidence = json.loads((Path(temp) / "workload_qos.json").read_text())
            self.assertEqual(evidence["status"], "failed")
            self.assertIn("partial ping output", json.dumps(evidence["raw"]))

    def test_duplicate_or_incomplete_permitted_probes_fail_with_evidence(self):
        for output in ("100 packets transmitted, 100 received, +1 duplicates",
                       "100 packets transmitted, 1100 received",
                       "100 packets transmitted, 99 received"):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as temp:
                host = type("Host", (), {"pexec": lambda self, args: (output, "", 0)})()
                with self.assertRaisesRegex(AssertionError, "probe failure or duplicate"):
                    lab_harness.permitted_checks({h: host for h in AGENCY_HOSTS}, Path(temp))
                evidence = json.loads((Path(temp) / "permitted_probes.json").read_text())
                self.assertIn(output, evidence.values())

    def test_complete_permitted_matrix_uses_frozen_probe_count(self):
        counts = []
        class Host:
            def pexec(self, args):
                count = int(args[args.index("-c") + 1])
                counts.append(count)
                return "%d packets transmitted, %d received" % (count, count), "", 0
        with tempfile.TemporaryDirectory() as temp, patch.dict(perf.ACCEPTANCE, permitted_probes=7):
            lab_harness.permitted_checks({h: Host() for h in AGENCY_HOSTS}, Path(temp))
            self.assertEqual(counts, [7] * 42)
            self.assertEqual(len(json.loads((Path(temp) / "permitted_probes.json").read_text())), 42)

    def test_forbidden_probe_cannot_pass_on_local_errors_or_incomplete_attempts(self):
        lab_harness.require_denied_probe("100 packets transmitted, 0 received", 1)
        for output, code in (("100 packets transmitted, 0 received, +20 errors", 1),
                             ("1 packets transmitted, 0 received", 1),
                             ("100 packets transmitted, 100 received", 0),
                             ("100 packets transmitted, 0 received", 2)):
            with self.subTest(output=output, code=code), self.assertRaises(AssertionError):
                lab_harness.require_denied_probe(output, code)


class AddressHost:
    """Model Linux same-subnet primary deletion with promote_secondaries=0."""
    name = "h7"

    def __init__(self, fail_add=False, lose_address=False):
        self.addresses = ["10.0.0.7"]
        self.fail_add, self.lose_address = fail_add, lose_address

    def pexec(self, args):
        if args[:4] == ["ip", "-4", "addr", "flush"]:
            self.addresses.clear()
        elif args[:3] in (["ip", "addr", "add"], ["ip", "addr", "replace"]):
            if self.fail_add:
                return "", "RTNETLINK answers: Operation not permitted", 2
            if not self.lose_address:
                self.addresses.append(args[3].split("/")[0])
        elif args[:3] == ["ip", "addr", "del"]:
            address = args[3].split("/")[0]
            if address == self.addresses[0]:
                self.addresses.clear()  # A secondary in the same /24 is removed too.
            else:
                self.addresses.remove(address)
        elif args[:5] == ["ip", "-j", "-4", "addr", "show"]:
            return json.dumps([{"addr_info": [{"local": a, "prefixlen": 24} for a in self.addresses]}]), "", 0
        else:
            raise AssertionError("Unexpected host command: " + repr(args))
        return "", "", 0


class SpoofAddressTests(unittest.TestCase):
    def test_spoof_address_survives_primary_replacement_and_is_restored(self):
        # Failure control: the former setup leaves ping with no bindable IP.
        old = AddressHost()
        old.pexec(["ip", "addr", "replace", "10.0.0.2/24", "dev", "h7-eth0"])
        old.pexec(["ip", "addr", "del", "10.0.0.7/24", "dev", "h7-eth0"])
        self.assertEqual(old.addresses, [])
        host = AddressHost()
        lab_harness.replace_host_ipv4(host, "h7-eth0", "10.0.0.2")
        self.assertEqual(host.addresses, ["10.0.0.2"])
        lab_harness.replace_host_ipv4(host, "h7-eth0", "10.0.0.7")
        self.assertEqual(host.addresses, ["10.0.0.7"])

    def test_address_command_failure_is_explicit(self):
        with self.assertRaisesRegex(AssertionError, "Operation not permitted"):
            lab_harness.replace_host_ipv4(AddressHost(fail_add=True), "h7-eth0", "10.0.0.2")

    def test_missing_address_readback_cannot_reach_spoof_probe(self):
        with self.assertRaisesRegex(AssertionError, "requested IPv4 address.*missing"):
            lab_harness.replace_host_ipv4(AddressHost(lose_address=True), "h7-eth0", "10.0.0.2")


if __name__ == "__main__":
    unittest.main()
