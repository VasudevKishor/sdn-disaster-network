"""Real Ryu serialization and packet semantics for the Phase 2 prototype."""
from types import SimpleNamespace as NS
import unittest
try:
    from ryu.ofproto import ofproto_v1_3 as ofp, ofproto_v1_3_parser as parser
    from ryu.lib.packet import packet, ethernet, arp, ipv4, icmp, tcp, udp
except ImportError as exc:
    raise unittest.SkipTest("Use the pinned Ryu Python: " + str(exc))
from config.phase2 import ENDPOINTS as HOSTS, SITES, SWITCHES, expected_ports
from controller.phase2_app import Phase2App, BASE_COOKIE, ROUTE_COOKIE, HEALTH_COOKIE, OWNER_MASK
from tests.test_controller import Datapath
from tests.test_phase2_model import ready_state
from config.phase2 import HEALTH_PORT, health_source_port
from monitoring.path_monitor import probe_frame, MAGIC


def description(switch, port, down=False):
    return NS(port_no=port, name=("%s-eth%d" % (switch, port)).encode(),
              config=0, state=ofp.OFPPS_LINK_DOWN if down else 0)


def arp_request(source="p2h1", destination=None, source_ip=None, source_mac=None, opcode=arp.ARP_REQUEST):
    src = HOSTS[source]
    p = packet.Packet()
    p.add_protocol(ethernet.ethernet(src=source_mac or src.mac, dst="ff:ff:ff:ff:ff:ff", ethertype=0x0806))
    p.add_protocol(arp.arp(opcode=opcode, src_mac=source_mac or src.mac, src_ip=source_ip or src.ip,
                          dst_ip=destination or SITES[src.site].gateway))
    p.serialize()
    return p.data


def frame(source="p2h1", target="p2h2", protocol=1, ttl=64, fragment=0, source_mac=None,
          source_ip=None, destination_mac=None):
    src, dst = HOSTS[source], HOSTS[target]
    p = packet.Packet()
    p.add_protocol(ethernet.ethernet(src=source_mac or src.mac,
                                    dst=destination_mac or SITES[src.site].gateway_mac, ethertype=0x0800))
    p.add_protocol(ipv4.ipv4(src=source_ip or src.ip, dst=dst.ip, proto=protocol, ttl=ttl, flags=fragment))
    p.add_protocol(icmp.icmp() if protocol == 1 else (
        tcp.tcp(src_port=12345, dst_port=8080) if protocol == 6 else udp.udp(src_port=12345, dst_port=8080)))
    p.serialize()
    return p.data


class Phase2ControllerTests(unittest.TestCase):
    def setUp(self):
        self.app = Phase2App()
        self.app.state = ready_state()
        self.app.datapaths = {dpid: Datapath(dpid) for dpid in SWITCHES.values()}
        self.dp = self.app.datapaths[201]

    def inject(self, data, dpid=201, port=1, reason=ofp.OFPR_NO_MATCH):
        dp = self.app.datapaths[dpid]
        self.app.packet_in(NS(msg=NS(datapath=dp, data=data, match={"in_port": port},
                                    reason=reason, buffer_id=ofp.OFP_NO_BUFFER)))
        return dp.messages

    def test_gateway_proxy_arp_and_no_cross_subnet_arp(self):
        self.inject(arp_request())
        out = self.dp.messages[-1]
        self.assertIsInstance(out, parser.OFPPacketOut)
        reply = packet.Packet(out.data).get_protocol(arp.arp)
        self.assertEqual((reply.src_ip, reply.src_mac), (SITES["command"].gateway, SITES["command"].gateway_mac))
        self.assertEqual(out.in_port, ofp.OFPP_CONTROLLER)
        self.assertEqual(out.buffer_id, ofp.OFP_NO_BUFFER)
        self.dp.messages.clear()
        self.inject(arp_request(destination=HOSTS["p2h2"].ip))
        self.assertFalse(self.dp.messages)

    def test_bad_arp_unknown_broadcast_and_wrong_attachment_are_silent(self):
        for data in (arp_request(source_ip=HOSTS["p2h2"].ip),
                     arp_request(opcode=arp.ARP_REPLY), arp_request(destination="10.20.1.99"), b"bad"):
            self.inject(data)
        self.inject(arp_request(), dpid=203, port=2)
        self.assertTrue(all(not dp.messages for dp in self.app.datapaths.values()))

    def test_route_rules_rewrite_and_decrement_once_in_both_directions(self):
        self.app.reconcile()
        for source, target in (("p2h1", "p2h2"), ("p2h2", "p2h1")):
            for proto in (1, 6, 17):
                src, dst = HOSTS[source], HOSTS[target]
                eth_src, eth_dst, ttl = src.mac, SITES[src.site].gateway_mac, 64
                route = self.app.state.route(source, target)
                for index, (switch, ingress, output) in enumerate(route):
                    flows = [m for m in self.app.datapaths[SWITCHES[switch]].messages
                             if isinstance(m, parser.OFPFlowMod) and m.command == ofp.OFPFC_ADD
                             and m.cookie == ROUTE_COOKIE and m.match["ipv4_src"] == src.ip
                             and m.match["ip_proto"] == proto]
                    self.assertEqual(len(flows), 1)
                    flow = flows[0]
                    self.assertEqual((flow.match["in_port"], flow.match["eth_src"], flow.match["eth_dst"]),
                                     (ingress, eth_src, eth_dst))
                    self.assertEqual(flow.match["ip_frag"], 0)
                    actions = flow.instructions[0].actions
                    for action in actions:
                        if isinstance(action, parser.OFPActionDecNwTtl):
                            ttl -= 1
                        elif isinstance(action, parser.OFPActionSetField):
                            if action.key == "eth_src":
                                eth_src = action.value
                            else:
                                eth_dst = action.value
                    self.assertEqual(actions[-1].port, output)
                    self.assertNotIn(output, (ofp.OFPP_FLOOD, ofp.OFPP_NORMAL))
                    self.assertEqual(ttl, 63)
                self.assertEqual((eth_src, eth_dst), (SITES[dst.site].gateway_mac, dst.mac))

    def test_guest_and_spoofed_identity_have_no_permitted_cached_rule(self):
        self.app.reconcile()
        for dp in self.app.datapaths.values():
            for flow in dp.messages:
                if isinstance(flow, parser.OFPFlowMod) and flow.command == ofp.OFPFC_ADD:
                    self.assertNotEqual(flow.match.get("ipv4_src"), HOSTS["p2h3"].ip)
        for dp in self.app.datapaths.values():
            dp.messages.clear()
        self.inject(frame(source="p2h3"), dpid=203, port=2)
        self.inject(frame(source_mac=HOSTS["p2h1"].mac, source_ip=HOSTS["p2h1"].ip), dpid=203, port=2)
        self.inject(frame(source_ip=HOSTS["p2h2"].ip))
        self.assertTrue(all(not dp.messages for dp in self.app.datapaths.values()))

    def test_fragment_low_ttl_invalid_ttl_reason_and_wrong_gateway_are_dropped(self):
        for data in (frame(fragment=1), frame(ttl=1), frame(ttl=0), frame(destination_mac=HOSTS["p2h2"].mac)):
            self.inject(data)
        self.inject(frame(), reason=ofp.OFPR_INVALID_TTL)
        self.assertFalse(self.dp.messages)

    def test_first_packet_uses_same_routing_actions_as_installed_rules(self):
        self.inject(frame())
        out = self.dp.messages[-1]
        self.assertIsInstance(out, parser.OFPPacketOut)
        self.assertIsInstance(out.actions[0], parser.OFPActionDecNwTtl)
        self.assertEqual(out.actions[-1].port, 10)
        self.assertEqual(out.buffer_id, ofp.OFP_NO_BUFFER)

    def test_bond_garp_updates_return_path_without_flood(self):
        self.inject(arp_request(destination=HOSTS["p2h1"].ip), dpid=202)
        self.assertEqual(self.app.state.active["p2h1"].switch, "p2s2")
        self.assertFalse(any(isinstance(m, parser.OFPPacketOut)
                             for dp in self.app.datapaths.values() for m in dp.messages))
        remote = self.app.datapaths[203]
        outputs = [m.instructions[0].actions[-1].port for m in remote.messages
                   if isinstance(m, parser.OFPFlowMod) and m.command == ofp.OFPFC_ADD
                   and m.cookie == ROUTE_COOKIE
                   and m.match["ipv4_dst"] == HOSTS["p2h1"].ip]
        self.assertEqual(outputs, [11] * 3)

    def test_port_down_withdraws_primary_path_and_all_writes_are_owned(self):
        self.app.port_status(NS(msg=NS(datapath=self.dp, desc=description("p2s1", 1, True), reason=ofp.OFPPR_MODIFY)))
        self.assertEqual(self.app.state.route("p2h2", "p2h1")[-1][0], "p2s2")
        for dp in self.app.datapaths.values():
            for m in dp.messages:
                self.assertEqual(m.cookie & OWNER_MASK, BASE_COOKIE & OWNER_MASK)
                if m.command == ofp.OFPFC_DELETE:
                    self.assertEqual(m.cookie_mask, 0xffffffffffffffff)

    def test_features_wait_for_complete_validated_port_descriptions(self):
        self.app.features(NS(msg=NS(datapath=self.dp)))
        self.assertNotIn("p2s1", self.app.state.online)
        self.app.ports(NS(msg=NS(datapath=self.dp, body=[description("p2s1", 1)], flags=ofp.OFPMPF_REPLY_MORE)))
        self.assertNotIn("p2s1", self.app.state.online)
        self.app.ports(NS(msg=NS(datapath=self.dp, body=[description("p2s1", 10)], flags=0)))
        self.assertIn("p2s1", self.app.state.online)
        self.assertEqual(self.dp.messages[0].cookie_mask, OWNER_MASK)

    def test_blocked_or_forwarding_disabled_ports_are_not_usable(self):
        for config, state in ((ofp.OFPPC_NO_FWD, 0), (ofp.OFPPC_NO_RECV, 0), (0, ofp.OFPPS_BLOCKED)):
            desc = description("p2s1", 10)
            desc.config, desc.state = config, state
            self.app.port_status(NS(msg=NS(datapath=self.dp, desc=desc, reason=ofp.OFPPR_MODIFY)))
            self.assertNotIn(("p2s1", 10), self.app.state.up)
            self.assertEqual(self.app.state.route("p2h1", "p2h2"), ())

    def test_bad_inventory_and_stale_connection_events_fail_closed(self):
        self.app.features(NS(msg=NS(datapath=self.dp)))
        self.app.ports(NS(msg=NS(datapath=self.dp, body=[description("wrong", 1), description("p2s1", 10)], flags=0)))
        self.assertNotIn("p2s1", self.app.state.online)
        old_dp = Datapath(201)
        self.app.disconnected(NS(datapath=old_dp))
        self.assertIs(self.app.datapaths[201], self.dp)
        self.app.ports(NS(msg=NS(datapath=old_dp, body=[], flags=0)))
        self.assertNotIn("p2s1", self.app.state.online)

    def test_disconnect_and_openflow_error_withdraw_routes(self):
        self.app.disconnected(NS(datapath=self.dp))
        self.assertNotIn("p2s1", self.app.state.online)
        self.assertEqual(self.app.state.route("p2h1", "p2h2")[0][0], "p2s2")
        remote = self.app.datapaths[203]
        self.app.error(NS(msg=NS(datapath=remote, type=3, code=1)))
        self.assertEqual(self.app.state.route("p2h1", "p2h2"), ())

    def test_health_rules_reach_each_attachment_and_do_not_move_application(self):
        self.app.reconcile()
        self.assertEqual(self.app.state.active, {})
        for attachment in HOSTS["p2h1"].attachments:
            for source, target, forward in (("p2h1", "p2h2", True), ("p2h2", "p2h1", False)):
                port = health_source_port(attachment)
                route = self.app.state.route(source, target, **{
                    "source_attachment" if forward else "target_attachment": attachment})
                for index, step in enumerate(route):
                    dp = self.app.datapaths[SWITCHES[step[0]]]
                    flows = [m for m in dp.messages if isinstance(m, parser.OFPFlowMod)
                             and m.command == ofp.OFPFC_ADD and m.cookie == HEALTH_COOKIE and m.priority == 200
                             and m.match["ipv4_src"] == HOSTS[source].ip
                             and m.match["udp_src" if forward else "udp_dst"] == port]
                    self.assertEqual(len(flows), 1)
                    flow = flows[0]
                    self.assertEqual(flow.match["in_port"], step[1])
                    self.assertEqual(flow.instructions[0].actions[-1].port, step[2])
                    decrements = sum(isinstance(a, parser.OFPActionDecNwTtl) for a in flow.instructions[0].actions)
                    self.assertEqual(decrements, int(index == 0))

    def test_health_packet_in_miss_never_moves_or_bypasses_missing_route(self):
        attachment = HOSTS["p2h1"].attachments[1]
        self.inject(probe_frame("p2h1", "p2h2", attachment, MAGIC + b"0" * 16), dpid=202, port=1)
        self.assertEqual(self.app.state.active, {})
        self.assertTrue(all(not dp.messages for dp in self.app.datapaths.values()))

    def test_unavailable_health_path_is_withdrawn_and_reserved_ports_fail_closed(self):
        self.app.state.up.remove(("p2s2", 10))
        self.app.reconcile()
        for dp in self.app.datapaths.values():
            health = [m for m in dp.messages if isinstance(m, parser.OFPFlowMod)
                      and m.command == ofp.OFPFC_ADD and m.cookie == HEALTH_COOKIE]
            reserved = [m for m in health if m.priority == 150]
            self.assertEqual(len(reserved), 2)
            self.assertTrue(all(not m.instructions[0].actions for m in reserved))
            for flow in health:
                if flow.priority == 200:
                    self.assertNotIn(62001, (flow.match["udp_src"], flow.match["udp_dst"]))

    def test_health_reply_rules_keep_guest_access_closed(self):
        self.app.reconcile()
        for dp in self.app.datapaths.values():
            for flow in dp.messages:
                if isinstance(flow, parser.OFPFlowMod) and flow.command == ofp.OFPFC_ADD and flow.cookie == HEALTH_COOKIE:
                    self.assertNotEqual(flow.match.get("in_port"), 2)
                    self.assertNotEqual(flow.match.get("ipv4_src"), HOSTS["p2h3"].ip)


if __name__ == "__main__":
    unittest.main()
