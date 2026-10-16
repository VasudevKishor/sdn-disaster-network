"""Expanded topology, complete policy matrix, path failures and inventory safety."""
from collections import Counter
from dataclasses import replace
import unittest
from config.expanded import build_inventory
from config.phase2 import ForwardingState


def ready(inventory):
    state = ForwardingState(inventory)
    state.online.update(inventory.SWITCHES)
    state.up.update((s, p) for s in inventory.SWITCHES for p in inventory.expected_ports(s))
    return state


class ExpandedModelTests(unittest.TestCase):
    def setUp(self):
        self.inventory = build_inventory()
        self.state = ready(self.inventory)

    def test_target_size_critical_attachments_and_guest_segments(self):
        i = self.inventory
        self.assertEqual(Counter(i.SWITCH_ROLES.values()), {"backbone": 4, "aggregation": 4, "access": 12})
        self.assertEqual(len(i.ENDPOINTS), 24)
        self.assertEqual(len(i.SITE_ACCESS), 8)
        self.assertEqual(len(i.SITES), 10)
        self.assertEqual(len(i.HEALTH_TARGETS), 12)
        for name, endpoint in i.ENDPOINTS.items():
            critical = i.SITE_ROLES[i.LOCATIONS[name]] in ("command", "hospital")
            self.assertEqual(len(endpoint.attachments), 2 if critical else 1)
            self.assertEqual(len({a.switch for a in endpoint.attachments}), len(endpoint.attachments))
        self.assertEqual(i.ENDPOINTS["as3"].site, "a_guest")
        self.assertNotEqual(i.ENDPOINTS["as3"].site, i.ENDPOINTS["as1"].site)

    def test_mininet_ports_and_links_are_exact_and_management_is_separate(self):
        from topology.phase2_topo import Phase2Topo
        i, topo = self.inventory, Phase2Topo(inventory_name="expanded")
        self.assertEqual(set(topo.switches()), set(i.SWITCHES))
        self.assertEqual(set(topo.hosts()), set(i.ENDPOINTS))
        self.assertEqual(len(topo.links()), len(i.LINKS) + 36)
        for link in i.LINKS:
            self.assertEqual(topo.port(link.a, link.b), (link.pa, link.pb))
            self.assertEqual(topo.linkInfo(link.a, link.b)["bw"], link.rate_mbps)
        for name, endpoint in i.ENDPOINTS.items():
            self.assertIsNone(topo.nodeInfo(name)["ip"])
            for a in endpoint.attachments:
                self.assertEqual(topo.port(name, a.switch), (a.host_port, a.port))
        self.assertTrue(all(topo.nodeInfo(s)["inband"] is False for s in i.SWITCHES))

    def test_complete_matrix_has_loop_free_forward_and_reverse_routes(self):
        allowed = denied = 0
        for source in self.inventory.ENDPOINTS:
            for target in self.inventory.ENDPOINTS:
                if source == target:
                    continue
                route = self.state.route(source, target)
                self.assertEqual(bool(route), self.inventory.permitted(source, target), (source, target))
                if route:
                    allowed += 1
                    self.assertEqual(len(route), len({s for s, _, _ in route}))
                    self.assertTrue(all(a != b for _, a, b in route))
                    self.assertTrue(self.state.route(target, source))
                else:
                    denied += 1
        self.assertEqual((allowed, denied), (414, 138))

    def test_every_single_infrastructure_link_loss_has_a_surviving_path(self):
        for link in self.inventory.LINKS:
            self.state.up.remove((link.a, link.pa))
            self.state.up.remove((link.b, link.pb))
            for source, target in (("ac1", "bc1"), ("ah1", "bf1"), ("as1", "bf2")):
                self.assertTrue(self.state.route(source, target), (link, source, target))
            self.state.up.update(((link.a, link.pa), (link.b, link.pb)))

    def test_each_critical_access_switch_loss_retains_all_its_endpoints(self):
        for site, switches in self.inventory.SITE_ACCESS.items():
            if len(switches) != 2:
                continue
            for switch in switches:
                self.state.online.remove(switch)
                for name, location in self.inventory.LOCATIONS.items():
                    if location == site:
                        self.assertTrue(self.state.route(name, self.inventory.HEALTH_TARGETS[name]))
                self.state.online.add(switch)

    def test_district_partition_preserves_local_routes_and_denies_remote(self):
        cross = [l for l in self.inventory.LINKS if l.a.startswith("bb") and l.b.startswith("bb")
                 and (int(l.a[-1]) <= 2) != (int(l.b[-1]) <= 2)]
        for link in cross:
            self.state.up.difference_update(((link.a, link.pa), (link.b, link.pb)))
        self.assertTrue(self.state.route("ac1", "ah1"))
        self.assertTrue(self.state.route("bc1", "bh1"))
        self.assertFalse(self.state.route("ac1", "bc1"))

    def test_distinct_surviving_route_can_avoid_a_backbone(self):
        route = self.state.route("ac1", "bc1")
        used = next(s for s, _, _ in route if s.startswith("bb"))
        self.state.online.remove(used)
        alternate = self.state.route("ac1", "bc1")
        self.assertTrue(alternate)
        self.assertNotIn(used, [s for s, _, _ in alternate])
        self.assertNotEqual(alternate, route)

    def test_small_configuration_and_invalid_inventory_fail_explicitly(self):
        small = build_inventory(1)
        self.assertEqual((len(small.SWITCHES), len(small.ENDPOINTS)), (10, 12))
        with self.assertRaises(ValueError):
            build_inventory(3)
        i = self.inventory
        i.ENDPOINTS["ac1"] = replace(i.ENDPOINTS["ac1"], ip=i.SITES["a_command"].gateway)
        with self.assertRaisesRegex(ValueError, "identity"):
            i.validate_inventory()


if __name__ == "__main__":
    unittest.main()
