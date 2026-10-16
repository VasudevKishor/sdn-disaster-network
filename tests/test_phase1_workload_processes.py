"""Real process integration on loopback; no Mininet, QoS, or topology claim."""
import copy
import shutil
import socket
import subprocess
import unittest
from unittest.mock import patch

from config.lab import AGENCY_HOSTS
from performance import run_tests as perf


class LocalHost:
    def __init__(self):
        self.processes = []

    def popen(self, args, **kwargs):
        process = subprocess.Popen(args, **kwargs)
        self.processes.append(process)
        return process

    def pexec(self, args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return result.stdout, result.stderr, result.returncode


class WorkloadProcessTests(unittest.TestCase):
    def test_real_concurrent_iperf_and_ping_processes_clean_up(self):
        for name in ("iperf", "ss", "ping"):
            if shutil.which(name) is None:
                self.skipTest("Missing workload executable: " + name)
        flows, reservations = copy.deepcopy(perf.FLOWS), []
        try:
            for flow in flows.values():
                reserved = socket.socket()
                reserved.bind(("127.0.0.1", 0))
                reservations.append(reserved)
                flow.update(server_ip="127.0.0.1", port=reserved.getsockname()[1])
        finally:
            for reserved in reservations:
                reserved.close()
        host = LocalHost()
        with patch.object(perf, "FLOWS", flows):
            result = perf.run_all_tests("baseline", {h: host for h in AGENCY_HOSTS}, duration=10)
            perf.validate_results(result)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(host.processes), 6)
        self.assertTrue(all(p.poll() is not None for p in host.processes))
        for key in flows:
            self.assertGreater(result["flows"][key]["throughput_mbps"], 0)
            self.assertEqual(result["flows"][key]["packets_received"], perf.PING_COUNT)
            self.assertIn(key + "_server", result["raw"])


if __name__ == "__main__":
    unittest.main()
