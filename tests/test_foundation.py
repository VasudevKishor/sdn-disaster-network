import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("MPLBACKEND", "Agg")
from config.lab import AGENCY_HOSTS, CORE_LINKS, SWITCHES, egress, managed_ports, manual_rule
from performance import run_tests as perf
from performance import generate_charts as charts
from qos import manage


class InventoryTests(unittest.TestCase):
    def test_topology_uses_exact_inventory_ports(self):
        from topology.disaster_topo import DisasterResponseTopo
        topo = DisasterResponseTopo()
        self.assertEqual(set(topo.switches()), set(SWITCHES))
        self.assertEqual(set(topo.hosts()), set(AGENCY_HOSTS))
        self.assertEqual(len(topo.links()), 9)
        for host, meta in AGENCY_HOSTS.items():
            self.assertEqual(topo.port(host, meta["switch"]), (0, meta["port"]))
            self.assertEqual(topo.nodeInfo(host)["ip"], meta["ip"] + "/24")
        for a, pa, b, pb in CORE_LINKS:
            self.assertEqual(topo.port(a, b), (pa, pb))
        self.assertEqual(len(managed_ports()), 11)

    def test_every_path_terminates_without_revisiting(self):
        for switch in SWITCHES:
            for host in AGENCY_HOSTS.values():
                node, seen = switch, set()
                while node != host["switch"]:
                    self.assertNotIn(node, seen)
                    seen.add(node)
                    port = egress(node, host)
                    hops = [b for a, pa, b, pb in CORE_LINKS if a == node and pa == port]
                    hops += [a for a, pa, b, pb in CORE_LINKS if b == node and pb == port]
                    self.assertEqual(len(hops), 1)
                    node = hops[0]
                self.assertEqual(egress(node, host), host["port"])

    def test_manual_rule_port_and_binding(self):
        rule = manual_rule()
        self.assertEqual(rule["actions"][-1]["port"], 3)
        self.assertEqual(rule["match"]["in_port"], 1)
        self.assertEqual(manual_rule(True)["actions"][-1]["port"], 4)
        output = subprocess.check_output([sys.executable, "config/lab.py", "manual-delete"], text=True)
        self.assertEqual(json.loads(output)["cookie"], rule["cookie"])
        self.assertEqual(json.loads(output)["cookie_mask"], 0xffffffffffffffff)

    def test_shell_helpers_load_the_shared_inventory(self):
        result = subprocess.check_output(["bash", "-c",
            "source demo_scripts/common_env.sh\nprintf '%s %s %s' \"$IP_HOSPITAL\" \"$QOS_PORT\" \"$DPID_S1\""], text=True)
        self.assertEqual(result, "10.0.0.2 s2-eth4 1")

    def test_unconfigured_switch_rejected(self):
        with self.assertRaises(ValueError):
            egress("s9", AGENCY_HOSTS["h1"])


class MeasurementTests(unittest.TestCase):
    def test_iperf_units_and_final_summary(self):
        self.assertEqual(perf.parse_iperf_throughput_mbps("2 Mbits/sec\n9 Mbits/sec"), 9)
        self.assertEqual(perf.parse_iperf_throughput_mbps("200 Kbits/sec"), .2)
        self.assertEqual(perf.parse_iperf_throughput_mbps("1 Gbits/sec"), 1000)
        self.assertIsNone(perf.parse_iperf_throughput_mbps("connection failed"))

    def test_missing_host_objects_are_explicit_failure(self):
        with self.assertRaisesRegex(perf.MeasurementError, "Host objects"):
            perf.run_all_tests("qos")

    def test_ping_loss_and_p95_are_preserved(self):
        data = perf.parse_ping("64 bytes time=10 ms\n64 bytes time=30 ms\n3 packets transmitted, 2 received")
        self.assertEqual(data["latency_avg_ms"], 20)
        self.assertEqual(data["latency_p95_ms"], 30)
        self.assertAlmostEqual(data["delivery_fraction"], 2 / 3)
        for output in ("TIMEOUT", "100 packets transmitted, 0 received", "2 packets transmitted, 2 received\ntime=1 ms"):
            with self.assertRaises(perf.MeasurementError):
                perf.parse_ping(output)

    def test_failed_measurement_cannot_be_saved_as_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "failed.csv"
            with self.assertRaises(perf.MeasurementError):
                perf.save_csv({"status": "failed"}, path)
            self.assertFalse(path.exists())

    def test_manual_mode_needs_no_mode_or_privileges(self):
        result = subprocess.run([sys.executable, "performance/run_tests.py", "--manual"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("s2-eth4", result.stdout)
        self.assertNotIn("cmd iperf", result.stdout)

    def test_concurrent_start_and_scoped_process_cleanup(self):
        events, processes = [], []
        class Process:
            def __init__(self, args):
                self.args, self.returncode = args, None
            def poll(self):
                return self.returncode
            def communicate(self, timeout):
                self.returncode = 0
                if self.args[0] == "ping":
                    return "\n".join(["64 bytes time=20 ms"] * 40) + "\n40 packets transmitted, 40 received", ""
                return "[ 3] 0.0-12.0 sec 30 MBytes 20 Mbits/sec\n", ""
            def terminate(self):
                self.returncode = -15
            def kill(self):
                self.returncode = -9
        class Host:
            def popen(self, args, **kwargs):
                events.append(args)
                p = Process(args)
                processes.append(p)
                return p
            def pexec(self, args):
                return "LISTEN", "", 0
        with patch.object(perf.time, "sleep"):
            result = perf.run_all_tests("qos", {h: Host() for h in AGENCY_HOSTS})
        self.assertEqual(result["status"], "complete")
        self.assertEqual([a[0] for a in events], ["iperf"] * 4 + ["ping"] * 2)
        self.assertTrue(all(p.poll() is not None for p in processes))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "perf.csv"
            perf.save_csv(result, path)
            with path.open() as fh:
                self.assertEqual(len(list(csv.DictReader(fh))), 2)
            with self.assertRaises(FileExistsError):
                perf.save_csv(result, path)

    def test_failed_client_cleans_up_owned_servers(self):
        class Process:
            returncode = 1
            def poll(self): return 1
        class Host:
            def popen(self, *args, **kwargs): return Process()
        with self.assertRaisesRegex(perf.MeasurementError, "server exited"):
            perf.run_all_tests("qos", {h: Host() for h in AGENCY_HOSTS})


class ChartTests(unittest.TestCase):
    def test_invalid_numbers_never_become_zero(self):
        for value in (None, "", "None", "nan", "inf", "broken", "-1"):
            with self.assertRaises(ValueError):
                charts.to_float(value)
        self.assertEqual(charts.to_float("0"), 0)

    def test_missing_duplicate_and_mismatched_rows_rejected(self):
        row = {"flow_class": "critical", "flow_name": "same", "throughput_mbps": "1", "latency_avg_ms": "2"}
        for a, b in (([], []), ([row], []), ([row, row], [row]),
                     ([row], [dict(row, flow_name="other")]), ([row], [dict(row, throughput_mbps="")])):
            with self.assertRaises(ValueError):
                charts.build_chart_data(a, b)

    def test_missing_input_preserves_existing_real_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            baseline = Path(temp) / "real.csv"
            baseline.write_text("real evidence sentinel")
            result = subprocess.run([sys.executable, "performance/generate_charts.py", "--baseline", str(baseline),
                                     "--qos", str(Path(temp) / "missing.csv"), "--output-dir", temp],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(baseline.read_text(), "real evidence sentinel")
            self.assertFalse((Path(temp) / "perf_qos.csv").exists())

    def test_sample_only_creates_separate_labelled_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            baseline = Path(temp) / "real.csv"
            baseline.write_text("real evidence sentinel")
            result = subprocess.run([sys.executable, "performance/generate_charts.py", "--sample",
                                     "--baseline", str(baseline), "--output-dir", temp],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SYNTHETIC PREVIEW", result.stdout)
            self.assertEqual(baseline.read_text(), "real evidence sentinel")
            previews = list(Path(temp).glob("synthetic_preview_*"))
            self.assertEqual(len(previews), 1)
            self.assertTrue((previews[0] / "throughput_comparison.png").exists())


class QoSTests(unittest.TestCase):
    def test_missing_port_aborts_before_any_mutation(self):
        calls = []
        def ovs(*args):
            calls.append(args)
            if args[0] == "port-to-br":
                if args[1] == "s3-eth3":
                    raise subprocess.CalledProcessError(1, args)
                return args[1].split("-")[0]
            if args[:2] == ("get", "Interface"):
                return args[2].split("eth")[1]
            return "[]" if args[0] == "get" else ""
        with patch.object(manage, "ovs", side_effect=ovs):
            with self.assertRaises(subprocess.CalledProcessError):
                manage.apply("setup")
        self.assertFalse(any(c[0] in ("set", "clear", "destroy") for c in calls))

    def test_foreign_qos_is_not_detached(self):
        calls = []
        def ovs(*args):
            calls.append(args)
            if args[0] == "port-to-br": return args[1].split("-")[0]
            if args[:2] == ("get", "Interface"): return args[2].split("eth")[1]
            if args[:2] == ("get", "Port"): return "foreign-uuid"
            return ""
        with patch.object(manage, "ovs", side_effect=ovs):
            with self.assertRaisesRegex(ValueError, "Foreign QoS"):
                manage.apply("setup")
        self.assertFalse(any(c[0] == "clear" for c in calls))

    def test_teardown_visits_every_managed_port_without_global_destroy(self):
        visited = []
        with patch.object(manage, "clear", side_effect=lambda port: visited.append(port)):
            manage.apply("teardown")
        self.assertEqual(set(visited), set(managed_ports()))

    def test_invalid_rates_abort_before_ovs(self):
        with patch.dict(os.environ, HIGH_MIN_RATE="51000000"), patch.object(manage, "ovs") as ovs:
            with self.assertRaises(ValueError):
                manage.apply("setup")
            ovs.assert_not_called()

    def test_owned_setup_repeated_then_teardown_does_not_leak_rows(self):
        # Model OVSDB references/ownership, exercising real management sequencing.
        rows, attached, calls = {}, {}, []
        foreign = {"table": "QoS", "port": "unrelated-eth1", "owner": "someone-else"}
        rows["foreign"] = foreign
        def ovs(*args):
            calls.append(args)
            if args[0] == "port-to-br": return args[1].split("-")[0]
            if args[:2] == ("get", "Interface"): return args[2].split("eth")[1]
            if args[:2] == ("get", "Port"): return attached.get(args[2], "[]")
            if args[0] == "--data=bare":
                table, port = args[4], args[6].split("=", 1)[1]
                return "\n".join(k for k, row in rows.items() if row["table"] == table
                                 and row["port"] == port and row["owner"] == manage.OWNER)
            if args[0] == "clear":
                attached.pop(args[2], None)
                return ""
            if args[:2] == ("--if-exists", "destroy"):
                self.assertEqual(rows[args[3]]["owner"], manage.OWNER)
                rows.pop(args[3])
                return ""
            if args[0] == "set":
                port = args[2]
                for table, suffix in (("QoS", "qos"), ("Queue", "high"), ("Queue", "low")):
                    uuid = port + "-" + suffix
                    self.assertNotIn(uuid, rows)
                    rows[uuid] = {"table": table, "port": port, "owner": manage.OWNER}
                attached[port] = port + "-qos"
                self.assertIn("queues:0=@high", args)
                self.assertIn("queues:1=@low", args)
                return ""
            self.fail("Unexpected OVS invocation: " + repr(args))
        with patch.object(manage, "ovs", side_effect=ovs), patch("builtins.print"):
            manage.apply("setup")
            self.assertEqual(len(rows), 3 * len(managed_ports()) + 1)
            manage.apply("setup")
            self.assertEqual(len(rows), 3 * len(managed_ports()) + 1)
            manage.apply("teardown")
            manage.apply("teardown")
            self.assertEqual(rows, {"foreign": foreign})
            self.assertEqual(attached, {})
        self.assertFalse(any("--all" in call for call in calls))


class HarnessTests(unittest.TestCase):
    def test_queue_counters_require_both_real_classes(self):
        from tests.lab_harness import queue_bytes
        self.assertEqual(queue_bytes("port 4 queue 0: bytes=10, pkts=1\nport 4 queue 1: bytes=20, pkts=2"),
                         {0: 10, 1: 20})
        with self.assertRaises(AssertionError):
            queue_bytes("port 4 queue 0: bytes=?")

    def test_unprivileged_preflight_starts_nothing(self):
        from tests.lab_harness import preflight
        with patch("os.geteuid", return_value=1000), patch("subprocess.check_output") as cmd:
            with self.assertRaisesRegex(RuntimeError, "require root"):
                preflight(sys.executable)
            cmd.assert_not_called()

    def test_existing_lab_is_refused(self):
        from tests import lab_harness
        with patch("os.geteuid", return_value=0), patch.object(lab_harness, "command", return_value="s1\n"):
            with self.assertRaisesRegex(RuntimeError, "already exist"):
                lab_harness.preflight(sys.executable)


if __name__ == "__main__":
    unittest.main()
