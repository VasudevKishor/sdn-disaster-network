import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
try:
    from ryu.lib.packet import packet, ethernet, arp, ipv4, udp
    from ryu.ofproto import ofproto_v1_3 as ofp, ofproto_v1_3_parser as parser
except ImportError as exc:
    raise unittest.SkipTest('Use pinned Ryu Python: '+str(exc))
from controller.policy_app import IncidentApp
from controller.phase2_app import ROUTE_COOKIE, HEALTH_COOKIE
from tests.test_controller import Datapath
from tests.test_expanded_model import ready


class IncidentControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ,SDN_PHASE3_DIR=self.temp.name), patch('controller.policy_app.hub.spawn'):
            self.app=IncidentApp()
        self.i=self.app.inventory; self.app.state=ready(self.i)
        self.app.datapaths={d:Datapath(d) for d in self.i.SWITCHES.values()}

    def inject(self, source='af1', ip=False, wrong_port=None):
        e=self.i.ENDPOINTS[source]; a=e.attachments[0]; frame=packet.Packet()
        frame.add_protocol(ethernet.ethernet(src=e.mac,dst=self.i.SITES[e.site].gateway_mac,ethertype=0x0800 if ip else 0x0806))
        if ip:
            frame.add_protocol(ipv4.ipv4(src=e.ip,dst=self.i.ENDPOINTS['ac1'].ip,proto=17)); frame.add_protocol(udp.udp(src_port=19001,dst_port=9001))
        else:
            frame.add_protocol(arp.arp(opcode=1,src_ip=e.ip,src_mac=e.mac,dst_ip=self.i.SITES[e.site].gateway))
        frame.serialize()
        dp=self.app.datapaths[self.i.SWITCHES[a.switch]]
        self.app.packet_in(NS(msg=NS(datapath=dp,data=frame.data,match={'in_port':wrong_port or a.port},reason=ofp.OFPR_NO_MATCH)))
        return dp.messages

    def test_no_generic_application_rules_and_no_packet_out_bypass(self):
        self.app.reconcile()
        rules=[m for d in self.app.datapaths.values() for m in d.messages]
        self.assertFalse(any(isinstance(m,parser.OFPFlowMod) and m.cookie==ROUTE_COOKIE for m in rules))
        self.assertTrue(any(isinstance(m,parser.OFPFlowMod) and m.cookie==HEALTH_COOKIE and m.priority==200 for m in rules))
        for d in self.app.datapaths.values(): d.messages.clear()
        self.assertFalse(self.inject(ip=True))

    def test_registration_is_required_for_gateway_arp_and_binding_stays_exact(self):
        self.assertFalse(self.inject('af3'))
        self.assertTrue(self.inject('af1'))
        dp=self.app.datapaths[self.i.SWITCHES['afc1']]; dp.messages.clear()
        self.assertFalse(self.inject('af1',wrong_port=3))
        (Path(self.temp.name)/'registry.json').write_text(json.dumps({'registered':['af3']}))
        self.assertTrue(self.inject('af3'))

    def test_corrupt_registry_fails_closed_and_graph_has_only_inventory_ports(self):
        (Path(self.temp.name)/'registry.json').write_text('invalid')
        self.assertFalse(self.inject('af1'))
        self.app.publish(); data=json.loads((Path(self.temp.name)/'graph.json').read_text())
        self.assertEqual(set(data['online']),set(self.i.SWITCHES))
        self.assertEqual(len(data['up']),sum(len(self.i.expected_ports(s)) for s in self.i.SWITCHES))

    def test_restart_restores_only_configured_previous_attachments(self):
        (Path(self.temp.name)/'graph.json').write_text(json.dumps({'active':{'ac1':['acc2',1,1],'ah1':['asc1',3,0]}}))
        with patch.dict(os.environ,SDN_PHASE3_DIR=self.temp.name),patch('controller.policy_app.hub.spawn'):
            restored=IncidentApp()
        self.assertEqual(restored.state.active['ac1'].switch,'acc2')
        self.assertNotIn('ah1',restored.state.active)
        self.assertIsNone(restored.state.attachment('ac1'))  # No fresh validated ports yet.

    def test_same_datapath_reconnect_changes_published_incarnation(self):
        dp=self.app.datapaths[self.i.SWITCHES['bb1']]
        self.app.features(NS(msg=NS(datapath=dp)))
        first=json.loads((Path(self.temp.name)/'graph.json').read_text())['connections']['bb1']
        self.app.features(NS(msg=NS(datapath=dp)))
        second=json.loads((Path(self.temp.name)/'graph.json').read_text())['connections']['bb1']
        self.assertNotEqual(first,second)


if __name__=='__main__': unittest.main()
