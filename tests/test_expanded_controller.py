"""Expanded controller rules and update behavior, serialized with real Ryu."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
try:
    from ryu.ofproto import ofproto_v1_3 as ofp, ofproto_v1_3_parser as parser
    from ryu.lib.packet import packet, ethernet, arp
except ImportError as exc:
    raise unittest.SkipTest("Use pinned Ryu Python: " + str(exc))
from controller.expanded_app import ExpandedPhase2App
from controller.phase2_app import ROUTE_COOKIE, HEALTH_COOKIE, BASE_COOKIE
from tests.test_controller import Datapath
from tests.test_expanded_model import ready


class ExpandedControllerTests(unittest.TestCase):
    def setUp(self):
        self.app = ExpandedPhase2App()
        self.i = self.app.inventory
        self.app.state = ready(self.i)
        self.app.datapaths = {dpid: Datapath(dpid) for dpid in self.i.SWITCHES.values()}

    def test_full_rules_are_specific_and_no_change_emits_no_update(self):
        self.app.reconcile()
        rules = [m for dp in self.app.datapaths.values() for m in dp.messages if isinstance(m, parser.OFPFlowMod)]
        self.assertGreater(len(rules), 3000)
        for rule in rules:
            self.assertEqual(rule.command, ofp.OFPFC_ADD)
            if rule.cookie == ROUTE_COOKIE:
                self.assertTrue(all(field in rule.match for field in ("in_port", "eth_src", "eth_dst", "ipv4_src", "ipv4_dst", "ip_proto", "ip_frag")))
                self.assertNotIn(rule.match["ipv4_src"], {self.i.ENDPOINTS["as3"].ip, self.i.ENDPOINTS["bs3"].ip})
        for dp in self.app.datapaths.values():
            dp.messages.clear()
        self.app.reconcile()
        self.assertTrue(all(not dp.messages for dp in self.app.datapaths.values()))

    def test_unchanged_and_unused_link_changes_reuse_compiled_routes(self):
        self.app.reconcile()
        with patch.object(self.app, "route_actions", wraps=self.app.route_actions) as compiled:
            self.app.reconcile()
            compiled.assert_not_called()
            # Removing one end of an unused 25 Mbps diagonal changes the graph
            # but none of this deterministic inventory's shortest paths.
            link = next(l for l in self.i.LINKS if (l.a, l.b) == ("bb1", "bb4"))
            self.app.state.up.remove((link.a, link.pa))
            self.app.reconcile()
            compiled.assert_not_called()

    def test_cached_routes_follow_bond_mobility_and_retain_pinned_health_paths(self):
        self.app.reconcile()
        self.app.state.observe("ah1", "ahc2", 1)
        self.app.reconcile()
        ip = self.i.ENDPOINTS["ah1"].ip
        ordinary = [(dpid, key, value) for dpid, rules in self.app.installed.items() for key, value in rules.items()
                    if key[0] == ROUTE_COOKIE and dict(key[2]).get("ipv4_dst") == ip]
        self.assertTrue(ordinary)
        self.assertFalse(any(dpid == self.i.SWITCHES["ahc1"] and value[2][-1].port == 1
                             for dpid, _, value in ordinary))
        self.assertTrue(any(dpid == self.i.SWITCHES["ahc2"] and value[2][-1].port == 1
                            for dpid, _, value in ordinary))
        for switch, port in (("ahc1", 62000), ("ahc2", 62001)):
            self.assertTrue(any(key[0] == HEALTH_COOKIE and dict(key[2]).get("ipv4_dst") == ip
                                and dict(key[2]).get("udp_dst") == port and value[2][-1].port == 1
                                for key, value in self.app.installed[self.i.SWITCHES[switch]].items()))

    def test_features_on_same_connection_forces_cached_route_reinstallation(self):
        from tests.test_phase2_controller import description
        self.app.reconcile()
        dp = self.app.datapaths[self.i.SWITCHES["acc1"]]
        self.app.features(NS(msg=NS(datapath=dp)))
        dp.messages.clear()
        self.app.ports(NS(msg=NS(datapath=dp, flags=0,
                                body=[description("acc1", p) for p in self.i.expected_ports("acc1")])))
        self.assertTrue(any(isinstance(m, parser.OFPFlowMod) and m.command == ofp.OFPFC_ADD
                            and m.cookie == ROUTE_COOKIE for m in dp.messages))

    def test_cached_plan_matches_full_rebuild_through_partition_and_restoration(self):
        from controller.phase2_app import Phase2App
        reference = ExpandedPhase2App()
        reference.state = ready(reference.inventory)
        reference.datapaths = {dpid: Datapath(dpid) for dpid in self.i.SWITCHES.values()}
        # Disable the optimization for this reference; it compiles every route
        # and serializes actual OF1.3 updates just as the original app did.
        reference.install_route = lambda *args, **kwargs: Phase2App.install_route(reference, *args, **kwargs)
        def plan(app):
            return {(dpid, key): value[0] for dpid, rules in app.installed.items() for key, value in rules.items()}
        links = [l for l in self.i.LINKS if l.a.startswith("bb") and l.b.startswith("bb")
                 and (int(l.a[-1]) <= 2) != (int(l.b[-1]) <= 2)]
        for app in (self.app, reference):
            for link in links:
                app.state.up.difference_update(((link.a, link.pa), (link.b, link.pb)))
            app.reconcile()
        self.assertEqual(plan(self.app), plan(reference))
        for link in links:
            for endpoint in ((link.a, link.pa), (link.b, link.pb)):
                for app in (self.app, reference):
                    app.state.up.add(endpoint)
                    app.reconcile()
                self.assertEqual(plan(self.app), plan(reference))

    def test_cross_district_packet_rewrite_and_ttl_once(self):
        self.app.reconcile()
        route = self.app.state.route("ac1", "bh1")
        decrements = 0
        for switch, ingress, output in route:
            flows = [m for m in self.app.datapaths[self.i.SWITCHES[switch]].messages
                     if m.cookie == ROUTE_COOKIE and m.match["ipv4_src"] == self.i.ENDPOINTS["ac1"].ip
                     and m.match["ipv4_dst"] == self.i.ENDPOINTS["bh1"].ip and m.match["ip_proto"] == 1]
            self.assertEqual(len(flows), 1)
            self.assertEqual(flows[0].match["in_port"], ingress)
            actions = flows[0].instructions[0].actions
            self.assertEqual(actions[-1].port, output)
            decrements += sum(isinstance(a, parser.OFPActionDecNwTtl) for a in actions)
        self.assertEqual(decrements, 1)

    def test_topology_change_withdraws_only_obsolete_owned_rules(self):
        self.app.reconcile()
        before = sum(len(v) for v in self.app.installed.values())
        for dp in self.app.datapaths.values():
            dp.messages.clear()
        self.app.state.online.remove("bb1")
        self.app.reconcile()
        updates = [m for dp in self.app.datapaths.values() for m in dp.messages]
        self.assertTrue(any(m.command == ofp.OFPFC_DELETE_STRICT for m in updates))
        self.assertTrue(all(m.command in (ofp.OFPFC_ADD, ofp.OFPFC_DELETE_STRICT) for m in updates))
        self.assertLess(len(updates), before * 2)
        for m in updates:
            self.assertIn(m.cookie, (ROUTE_COOKIE, HEALTH_COOKIE))

    def test_reconnect_clears_cache_and_installs_base_rules(self):
        self.app.reconcile()
        dp = self.app.datapaths[self.i.SWITCHES["acc1"]]
        dp.messages.clear()
        self.app.features(NS(msg=NS(datapath=dp)))
        self.assertTrue(any(isinstance(m, parser.OFPFlowMod) and m.cookie == BASE_COOKIE and m.priority == 65000 for m in dp.messages))
        self.assertNotIn("acc1", self.app.state.online)

    def test_unknown_broadcast_and_spoofed_guest_are_silent(self):
        source = self.i.ENDPOINTS["as3"]
        frame = packet.Packet()
        frame.add_protocol(ethernet.ethernet(src=source.mac, dst="ff:ff:ff:ff:ff:ff", ethertype=0x0806))
        frame.add_protocol(arp.arp(opcode=1, src_mac=source.mac, src_ip=source.ip, dst_ip="10.30.5.250"))
        frame.serialize()
        a = source.attachments[0]
        dp = self.app.datapaths[self.i.SWITCHES[a.switch]]
        self.app.packet_in(NS(msg=NS(datapath=dp, data=frame.data, match={"in_port": a.port}, reason=ofp.OFPR_NO_MATCH)))
        self.assertTrue(all(not d.messages for d in self.app.datapaths.values()))


if __name__ == "__main__":
    unittest.main()
