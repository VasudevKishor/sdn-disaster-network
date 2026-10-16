"""Real Ryu parser/packet tests, without root or a fake Ryu implementation."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
try:
    from ryu.ofproto import ofproto_v1_3 as ofp, ofproto_v1_3_parser as parser
    from ryu.lib.packet import packet, ethernet, ipv4, arp, tcp, udp, icmp
except ImportError as exc:
    raise unittest.SkipTest("Use the pinned Python 3.9 Ryu environment: " + str(exc))
from controller.flow_manager_app import FlowManagerApp, FLOW_COOKIE
from controller.base_switch_app import BaseLearningSwitch
from config.lab import AGENCY_HOSTS as HOSTS, egress, SWITCHES
from tests.test_matches import wire_fields


class Datapath:
    ofproto, ofproto_parser = ofp, parser
    def __init__(self, dpid=1):
        self.id, self.messages = dpid, []
    def send_msg(self, message):
        # Pack real OpenFlow bytes to detect bad matches/actions/buffer values.
        message.serialize()
        if isinstance(message, parser.OFPFlowMod) and "ip_frag" in message.match:
            # Ryu serialization alone accepts a field before its prerequisite;
            # OVS rejects it. Check all generated Phase 1 and Phase 2 rules.
            fields = wire_fields(message.match)
            assert fields.index((0x8000, 5)) < fields.index((0x0001, 26)), \
                "EtherType must precede the Nicira fragment field on the wire"
        self.messages.append(message)


def frame(source="h7", target="h4", protocol=6, src_port=8080, dst_port=5001,
          source_ip=None, source_mac=None, dest_mac=None, offset=0, flags=0):
    src, dst = HOSTS[source], HOSTS[target]
    p = packet.Packet()
    p.add_protocol(ethernet.ethernet(src=source_mac or src["mac"], dst=dest_mac or dst["mac"], ethertype=0x0800))
    p.add_protocol(ipv4.ipv4(src=source_ip or src["ip"], dst=dst["ip"], proto=protocol, offset=offset, flags=flags))
    if protocol == 6:
        p.add_protocol(tcp.tcp(src_port=src_port, dst_port=dst_port))
    elif protocol == 17:
        p.add_protocol(udp.udp(src_port=src_port, dst_port=dst_port))
    elif protocol == 1:
        p.add_protocol(icmp.icmp())
    p.serialize()
    return p.data


def arp_frame(source="h7", target="h4", ip=None, mac=None):
    src, dst = HOSTS[source], HOSTS[target]
    p = packet.Packet()
    p.add_protocol(ethernet.ethernet(src=src["mac"], dst="ff:ff:ff:ff:ff:ff", ethertype=0x0806))
    p.add_protocol(arp.arp(opcode=arp.ARP_REQUEST, src_mac=mac or src["mac"],
                          src_ip=ip or src["ip"], dst_ip=dst["ip"]))
    p.serialize()
    return p.data


class ControllerTests(unittest.TestCase):
    def setUp(self):
        with patch("controller.flow_manager_app.hub.spawn"):
            self.app = FlowManagerApp()
        self.dp = Datapath()

    def inject(self, data, ingress=4, buffer=ofp.OFP_NO_BUFFER, dp=None):
        dp = dp or self.dp
        self.app.packet_in_handler(SimpleNamespace(msg=SimpleNamespace(
            datapath=dp, data=data, match={"in_port": ingress}, buffer_id=buffer)))
        return dp.messages

    def flows(self):
        return [m for m in self.dp.messages if isinstance(m, parser.OFPFlowMod)]

    def test_proxy_arp_is_reply_only_no_learned_rule_or_flood(self):
        messages = self.inject(arp_frame())
        self.assertEqual(len(messages), 1)
        self.assertEqual(len(self.flows()), 0)
        out = messages[0]
        reply = packet.Packet(out.data).get_protocol(arp.arp)
        self.assertEqual((reply.opcode, reply.src_ip, reply.src_mac),
                         (arp.ARP_REPLY, HOSTS["h4"]["ip"], HOSTS["h4"]["mac"]))
        self.assertEqual(out.actions[0].port, 4)
        self.assertEqual(out.in_port, ofp.OFPP_CONTROLLER)
        self.assertEqual(out.buffer_id, ofp.OFP_NO_BUFFER)

    def test_buffered_arp_reply_has_own_data_and_no_buffer(self):
        self.inject(arp_frame(), buffer=0)
        self.assertEqual(self.dp.messages[0].buffer_id, ofp.OFP_NO_BUFFER)
        self.assertTrue(self.dp.messages[0].data)

    def test_proxy_arp_validates_binding_and_local_attachment(self):
        for data, port in ((arp_frame(ip=HOSTS["h2"]["ip"]), 4),
                           (arp_frame(mac=HOSTS["h2"]["mac"]), 4), (arp_frame(), 3)):
            self.inject(data, ingress=port)
        self.inject(arp_frame(), ingress=1, dp=Datapath(2))
        self.assertFalse(self.dp.messages)

    def test_arp_cannot_poison_later_ip_queue(self):
        self.inject(arp_frame())
        self.inject(frame())
        flow = self.flows()[0]
        match = dict(flow.match.items())
        self.assertEqual(match, dict(in_port=4, eth_src=HOSTS["h7"]["mac"], eth_dst=HOSTS["h4"]["mac"],
                                     eth_type=2048, ipv4_src=HOSTS["h7"]["ip"], ipv4_dst=HOSTS["h4"]["ip"],
                                     ip_proto=6, tcp_src=8080, tcp_dst=5001, ip_frag=0))
        self.assertEqual(flow.instructions[0].actions[0].queue_id, 0)  # protected dst service wins
        self.inject(frame(dst_port=8080))
        self.assertEqual(self.flows()[-1].instructions[0].actions[0].queue_id, 1)
        self.assertNotEqual(dict(self.flows()[-1].match.items()), match)

    def test_tcp_source_classification_is_in_flow_match(self):
        self.inject(frame(source="h1", src_port=8080, dst_port=9999), ingress=2)
        self.inject(frame(source="h1", src_port=1234, dst_port=9999), ingress=2)
        flows = self.flows()
        self.assertEqual([f.instructions[0].actions[0].queue_id for f in flows], [1, 0])
        self.assertEqual([f.match["tcp_src"] for f in flows], [8080, 1234])

    def test_udp_classification_and_fields(self):
        self.inject(frame(source="h5", protocol=17, src_port=6001), ingress=2, dp=Datapath(3))
        dp = Datapath(3)
        self.inject(frame(source="h5", protocol=17, src_port=6001), ingress=2, dp=dp)
        flow = next(m for m in dp.messages if isinstance(m, parser.OFPFlowMod))
        self.assertEqual(flow.match["udp_src"], 6001)
        self.assertEqual(flow.instructions[0].actions[-1].port, 1)
        self.assertNotEqual(flow.instructions[0].actions[-1].port, ofp.OFPP_FLOOD)

    def test_all_registered_directions_at_every_switch(self):
        for switch, dpid in SWITCHES.items():
            for src, sm in HOSTS.items():
                for dst, dm in HOSTS.items():
                    ingress, output = egress(switch, sm), egress(switch, dm)
                    dp = Datapath(dpid)
                    self.inject(frame(source=src, target=dst, protocol=1), ingress, dp=dp)
                    if ingress == output:
                        self.assertFalse(dp.messages)
                    else:
                        self.assertEqual(dp.messages[-1].actions[-1].port, output)

    def test_spoofed_ip_mac_port_and_destination_dropped(self):
        for data, ingress in ((frame(source_ip=HOSTS["h2"]["ip"]), 4),
                              (frame(source_mac=HOSTS["h2"]["mac"], source_ip=HOSTS["h2"]["ip"]), 4),
                              (frame(), 3), (frame(dest_mac=HOSTS["h1"]["mac"]), 4),
                              (frame(source_mac="00:00:00:00:00:ff"), 4)):
            self.inject(data, ingress)
        self.assertFalse(self.dp.messages)

    def test_fragmented_and_malformed_frames_dropped(self):
        for data in (frame(flags=1), frame(offset=1), b"", b"bad"):
            self.inject(data)
        self.assertFalse(self.dp.messages)

    def test_warmed_non_transport_rules_exclude_fragments(self):
        self.inject(frame(protocol=1))
        self.assertEqual(self.flows()[0].match["ip_frag"], 0)

    def test_ipv6_lldp_and_unknown_broadcast_are_dropped(self):
        for etype in (0x86dd, 0x88cc, 0x1234):
            p = packet.Packet()
            p.add_protocol(ethernet.ethernet(src=HOSTS["h7"]["mac"], dst="ff:ff:ff:ff:ff:ff", ethertype=etype))
            p.serialize()
            self.inject(p.data)
        self.assertFalse(self.dp.messages)

    def test_zero_buffer_id_installs_once_without_packet_out(self):
        self.inject(frame(), buffer=0)
        self.assertEqual(len(self.dp.messages), 1)
        self.assertEqual(self.flows()[0].buffer_id, 0)

    def test_qos_disabled_has_output_only(self):
        self.app.qos_enabled = False
        self.inject(frame())
        self.assertEqual(len(self.flows()[0].instructions[0].actions), 1)

    def test_reconnect_deletes_only_owned_rules_before_table_miss(self):
        self.app.switch_features_handler(SimpleNamespace(msg=SimpleNamespace(datapath=self.dp)))
        delete, fragments, miss = self.dp.messages
        self.assertEqual(delete.command, ofp.OFPFC_DELETE)
        self.assertEqual(delete.cookie, FLOW_COOKIE)
        self.assertEqual(delete.cookie_mask, 0xffffffffffffffff)
        self.assertEqual(fragments.priority, 65000)
        self.assertEqual(fragments.match["ip_frag"], (1, 1))
        self.assertEqual(fragments.instructions[0].actions, [])
        self.assertEqual(miss.priority, 0)

    def test_manual_identity_fields_survive_actual_rest_conversion(self):
        from ryu.lib.ofctl_v1_3 import to_match
        from config.lab import manual_rule
        for ambulance in (False, True):
            fields = manual_rule(ambulance)["match"]
            self.assertEqual(dict(to_match(self.dp, fields).items()), fields)

    def test_unknown_datapath_is_not_programmed(self):
        dp = Datapath(99)
        self.app.switch_features_handler(SimpleNamespace(msg=SimpleNamespace(datapath=dp)))
        self.inject(frame(), dp=dp)
        self.assertFalse(dp.messages)

    def test_base_switch_recovers_from_move_and_preserves_zero_buffer(self):
        base = BaseLearningSwitch()
        base.mac_to_port[1] = {HOSTS["h7"]["mac"]: 2, HOSTS["h4"]["mac"]: 1}
        base.packet_in_handler(SimpleNamespace(msg=SimpleNamespace(
            datapath=self.dp, data=frame(), match={"in_port": 4}, buffer_id=0)))
        self.assertEqual(base.mac_to_port[1][HOSTS["h7"]["mac"]], 4)
        deletes = [m for m in self.dp.messages if isinstance(m, parser.OFPFlowMod) and m.command == ofp.OFPFC_DELETE]
        self.assertEqual(len(deletes), 2)
        learned = self.dp.messages[-1]
        self.assertEqual(learned.buffer_id, 0)
        self.assertEqual(learned.idle_timeout, 30)


if __name__ == "__main__":
    unittest.main()
