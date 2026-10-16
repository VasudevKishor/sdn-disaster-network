"""Inventory, isolation and path checks without starting a Mininet network."""
import unittest
from unittest.mock import patch
from config.phase2 import ENDPOINTS, LINKS, SITES, SWITCHES, ForwardingState, expected_ports, permitted


def ready_state():
    state = ForwardingState()
    state.online.update(SWITCHES)
    state.up.update((switch, port) for switch in SWITCHES for port in expected_ports(switch))
    return state


class Phase2ModelTests(unittest.TestCase):
    def test_topology_has_exact_ports_and_dual_attachment(self):
        from topology.phase2_topo import Phase2Topo
        topo = Phase2Topo()
        self.assertEqual(set(topo.switches()), set(SWITCHES))
        self.assertEqual(set(topo.hosts()), set(ENDPOINTS))
        self.assertEqual(len(topo.links()), 6)
        for name, endpoint in ENDPOINTS.items():
            self.assertIsNone(topo.nodeInfo(name)["ip"])
            for attach in endpoint.attachments:
                self.assertEqual(topo.port(name, attach.switch), (attach.host_port, attach.port))
        for link in LINKS:
            self.assertEqual(topo.port(link.a, link.b), (link.pa, link.pb))
            self.assertEqual(topo.linkInfo(link.a, link.b)["bw"], link.rate_mbps)
        self.assertEqual(len({s.subnet for s in SITES.values()}), 3)

    def test_unknown_or_incomplete_state_never_creates_a_route(self):
        state = ForwardingState()
        self.assertEqual(state.route("p2h1", "p2h2"), ())
        state.online.update(SWITCHES)
        self.assertEqual(state.route("p2h1", "p2h2"), ())
        state.up.update(ready_state().up - {("p2s3", 10)})
        self.assertEqual(state.route("p2h1", "p2h2"), ())

    def test_permitted_matrix_and_paths_in_both_directions(self):
        state = ready_state()
        for source in ENDPOINTS:
            for target in ENDPOINTS:
                route = state.route(source, target)
                self.assertEqual(bool(route), permitted(source, target))
                if route:
                    self.assertEqual(len({step[0] for step in route}), len(route))
                    self.assertTrue(all(a != b for _, a, b in route))
                    self.assertEqual(route[0][1], state.attachment(source).port)
                    self.assertEqual(route[-1][2], state.attachment(target).port)
        self.assertEqual(state.route("p2h1", "p2h2"), (("p2s1", 1, 10), ("p2s3", 10, 1)))

    def test_legal_mobility_changes_both_paths_and_stays_after_restoration(self):
        state = ready_state()
        self.assertTrue(state.observe("p2h1", "p2s1", 1))
        state.up.remove(("p2s1", 1))
        self.assertTrue(state.observe("p2h1", "p2s2", 1))
        self.assertEqual(state.route("p2h1", "p2h2"), (("p2s2", 1, 10), ("p2s3", 11, 1)))
        self.assertEqual(state.route("p2h2", "p2h1"), (("p2s3", 1, 11), ("p2s2", 10, 1)))
        state.up.add(("p2s1", 1))
        self.assertEqual(state.route("p2h1", "p2h2")[0][0], "p2s2")

    def test_wrong_attachment_cannot_move_registered_identity(self):
        state = ready_state()
        for switch, port in (("p2s3", 2), ("p2s1", 10), ("p2s2", 10), ("unknown", 1)):
            self.assertFalse(state.observe("p2h1", switch, port))
        self.assertFalse(state.active)
        state.up.remove(("p2s2", 1))
        self.assertFalse(state.observe("p2h1", "p2s2", 1))

    def test_switch_loss_and_total_isolation_have_distinct_results(self):
        state = ready_state()
        state.online.remove("p2s1")
        self.assertEqual(state.route("p2h2", "p2h1")[-1][0], "p2s2")
        state.online.remove("p2s2")
        self.assertEqual(state.route("p2h2", "p2h1"), ())

    def test_distant_failure_does_not_pretend_to_move_a_miimon_bond(self):
        state = ready_state()
        state.observe("p2h1", "p2s1", 1)
        state.up.remove(("p2s1", 10))
        # Host carrier is intact. A remote failure needs a separate usable-path
        # detection mechanism; the prototype deliberately does not claim one.
        self.assertEqual(state.attachment("p2h1").switch, "p2s1")
        self.assertEqual(state.route("p2h1", "p2h2"), ())


class Phase2HarnessSafetyTests(unittest.TestCase):
    def test_no_root_fails_before_any_ovs_or_interface_operation(self):
        from tests import phase2_harness as harness
        with patch.object(harness.os, "geteuid", return_value=1000), patch.object(harness, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "no network started"):
                harness.preflight(harness.DEFAULT_RYU)
            command.assert_not_called()

    def test_existing_bridge_is_refused_before_any_interface_mutation(self):
        from tests import phase2_harness as harness
        with patch.object(harness.os, "geteuid", return_value=0), \
                patch.object(harness.Path, "is_file", return_value=True), \
                patch.object(harness, "command", return_value="p2s1\n") as command, \
                patch.object(harness, "interfaces") as interfaces:
            with self.assertRaisesRegex(AssertionError, "refusing to disturb"):
                harness.preflight(harness.DEFAULT_RYU)
            command.assert_called_once_with("ovs-vsctl", "--timeout=5", "list-br")
            interfaces.assert_not_called()


if __name__ == "__main__":
    unittest.main()
