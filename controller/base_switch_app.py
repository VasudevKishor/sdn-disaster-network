#!/usr/bin/env python3
"""
base_switch_app.py - RYU learning switch (Review 1 starting point)

This is the foundational controller application. It learns MAC addresses from
Packet-In events and installs basic OpenFlow 1.3 flow entries so hosts can
communicate. The flow_manager_app.py extends this with scenario-specific rules.

Run:
  ryu-manager --ofp-tcp-listen-port 6633 controller/base_switch_app.py

For full Review 1 features (custom flows + QoS), use flow_manager_app.py instead.
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet


class BaseLearningSwitch(app_manager.RyuApp):
    """Simple MAC-learning switch — foundation for disaster-response controller."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Per-datapath MAC address table: dpid -> {mac: port}
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install table-miss flow entry sending unmatched packets to controller."""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.logger.info("Switch connected: dpid=%s", datapath.id)

        self.mac_to_port[datapath.id] = {}
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, command=ofproto.OFPFC_DELETE,
            cookie=0xD150000000000003, cookie_mask=0xffffffffffffffff,
            out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY,
            match=parser.OFPMatch()))
        # Table-miss: send everything unknown to controller (priority 0)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle=0, hard=0):
        """Helper to install an OpenFlow flow mod message."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        if buffer_id is None:
            buffer_id = ofproto.OFP_NO_BUFFER

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            cookie=0xD150000000000003,
            buffer_id=buffer_id,
            priority=priority,
            idle_timeout=idle,
            hard_timeout=hard,
            match=match,
            instructions=inst,
        )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Learn source MAC and forward to destination port (or flood)."""
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        if eth.ethertype in (0x86dd, 0x88cc):
            return
        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        self.mac_to_port.setdefault(dpid, {})
        self.logger.debug("packet-in dpid=%s src=%s dst=%s in_port=%s", dpid, src, dst, in_port)

        # This educational app supports only an acyclic topology.
        # A move invalidates cached paths; do not permanently freeze the first port.
        previous = self.mac_to_port[dpid].get(src)
        if previous is not None and previous != in_port:
            for field in ("eth_src", "eth_dst"):
                datapath.send_msg(parser.OFPFlowMod(
                    datapath=datapath, command=ofproto.OFPFC_DELETE,
                    cookie=0xD150000000000003, cookie_mask=0xffffffffffffffff,
                    out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY,
                    match=parser.OFPMatch(**{field: src})))
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install flow so future packets bypass controller (priority 1)
        if out_port != ofproto.OFPP_FLOOD:
            # WORKAROUND: Never install a learned flow if the destination MAC
            # is associated with the same port on which the packet arrived.
            if out_port == in_port:
                self.logger.warning("MAC flapping detected for %s. Dropping packet to prevent loop.", dst)
                return

            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, buffer_id=msg.buffer_id, idle=30)
                return
            self.add_flow(datapath, 1, match, actions, idle=30)

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None,
        )
        datapath.send_msg(out)
