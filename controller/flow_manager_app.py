#!/usr/bin/env python3
"""
flow_manager_app.py - RYU controller with disaster-scenario flow rules + QoS

Extends the learning-switch foundation with:
  - Explicit OpenFlow rules for Hospital priority path (via s1->s3->s2)
  - Port-based matching for Ambulance data (TCP/5001)
  - Queue mapping for high/low priority traffic (OpenFlow set_queue)
  - REST API support via ryu.app.ofctl_rest (run separately)

Run (Review 1 full demo):
  ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py

Assumptions documented for viva:
  - Hospital (10.0.0.2) traffic to Ambulance (10.0.0.4) uses explicit path via s3
  - Ambulance application data uses TCP destination port 5001
  - High-priority queue ID = 0, low-priority queue ID = 1 (OVS HTB queues)
  - QoS queues must be configured on switches BEFORE traffic (see qos/qos_setup.sh)
"""

import json
import os

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, tcp, udp
from ryu.lib import hub

# ---------------------------------------------------------------------------
# Scenario constants (mirrored in topology/disaster_topo.py)
# ---------------------------------------------------------------------------
HOSPITAL_IP = "10.0.0.2"
AMBULANCE_IP = "10.0.0.4"
COMMAND_CENTER_IP = "10.0.0.1"
RESCUE_IP = "10.0.0.5"
PUBLIC_HELPLINE_IP = "10.0.0.7"

PORT_AMBULANCE_DATA = 5001
PORT_HOSPITAL_CRITICAL = 5002
PORT_RESCUE_LOCATION = 6001
PORT_PUBLIC_HELPLINE = 8080
PORT_DRONE_VIDEO = 9000

# OpenFlow queue IDs (must match qos/queue_config.json)
QUEUE_HIGH_PRIORITY = 0
QUEUE_LOW_PRIORITY = 1
QUEUE_DEFAULT = 0

# Datapath IDs assigned by Mininet (s1=1, s2=2, s3=3 typically)
SWITCH_S1 = 1
SWITCH_S2 = 2
SWITCH_S3 = 3

# Port numbers on each switch (Mininet assignment order in disaster_topo.py)
# s1: 1=s2, 2=s3, 3=h1, 4=h2, 5=h7
# s2: 1=s1, 2=s3, 3=h3, 4=h4
# s3: 1=s2, 2=s1, 3=h5, 4=h6
S1_PORT_TO_S3 = 2
S3_PORT_TO_S2 = 1
S2_PORT_H4_AMBULANCE = 4
S1_PORT_H1_COMMAND = 3


def load_queue_config():
    """Load optional queue config JSON for documentation / future extensions."""
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "qos",
        "queue_config.json",
    )
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


class FlowManagerApp(app_manager.RyuApp):
    """Disaster-response SDN controller with custom flows and QoS mapping."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.qos_enabled = os.environ.get("DISASTER_QOS", "1") == "1"
        self.queue_config = load_queue_config()
        self.stats_thread = hub.spawn(self._request_stats_loop)

    # ------------------------------------------------------------------
    # Flow installation helpers
    # ------------------------------------------------------------------
    def add_flow(
        self,
        datapath,
        priority,
        match,
        actions,
        buffer_id=None,
        idle=0,
        hard=0,
        instructions=None,
    ):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        if instructions is None:
            instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            buffer_id=buffer_id,
            priority=priority,
            idle_timeout=idle,
            hard_timeout=hard,
            match=match,
            instructions=instructions,
        )
        datapath.send_msg(mod)

    def delete_flow(self, datapath, match=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match if match else parser.OFPMatch(),
        )
        datapath.send_msg(mod)

    def action_output(self, datapath, port, queue_id=None):
        parser = datapath.ofproto_parser
        if queue_id is not None and self.qos_enabled:
            return parser.OFPActionSetQueue(queue_id)
        return parser.OFPActionOutput(port)

    def actions_with_queue(self, datapath, port, queue_id=QUEUE_DEFAULT):
        """Build action list: optional set_queue then output port."""
        parser = datapath.ofproto_parser
        actions = []
        if self.qos_enabled:
            actions.append(parser.OFPActionSetQueue(queue_id))
        actions.append(parser.OFPActionOutput(port))
        return actions

    # ------------------------------------------------------------------
    # Switch connect: table-miss + scenario-specific proactive rules
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id

        self.datapaths[dpid] = datapath
        self.logger.info("Switch connected: dpid=%s (QoS=%s)", dpid, self.qos_enabled)

        # Table-miss entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

        # Install disaster-scenario rules once switch is known
        self.install_scenario_flows(datapath)

    def install_scenario_flows(self, datapath):
        """Install explicit OpenFlow rules reflecting disaster-response policy."""
        dpid = datapath.id
        parser = datapath.ofproto_parser

        # Rule 1: Hospital -> Ambulance via priority path (s1 -> s3 -> s2)
        # Installed on s1: forward Hospital traffic toward s3 instead of direct s1->s2
        if dpid == SWITCH_S1:
            match = parser.OFPMatch(
                eth_type=0x0800,
                ipv4_src=HOSPITAL_IP,
                ipv4_dst=AMBULANCE_IP,
            )
            actions = self.actions_with_queue(datapath, S1_PORT_TO_S3, QUEUE_HIGH_PRIORITY)
            self.add_flow(datapath, 500, match, actions, idle=0, hard=0)
            self.logger.info("Installed Hospital priority path rule on s1 (dpid=1)")

        # Rule 2: s3 relay — Hospital->Ambulance continues toward s2
        if dpid == SWITCH_S3:
            match = parser.OFPMatch(
                eth_type=0x0800,
                ipv4_src=HOSPITAL_IP,
                ipv4_dst=AMBULANCE_IP,
            )
            actions = self.actions_with_queue(datapath, S3_PORT_TO_S2, QUEUE_HIGH_PRIORITY)
            self.add_flow(datapath, 500, match, actions, idle=0, hard=0)
            self.logger.info("Installed Hospital relay rule on s3 (dpid=3)")

        # Rule 3: Ambulance data — match TCP dst port 5001 (any source)
        if dpid == SWITCH_S2:
            match = parser.OFPMatch(
                eth_type=0x0800,
                ip_proto=6,
                ipv4_dst=AMBULANCE_IP,
                tcp_dst=PORT_AMBULANCE_DATA,
            )
            actions = self.actions_with_queue(datapath, S2_PORT_H4_AMBULANCE, QUEUE_HIGH_PRIORITY)
            self.add_flow(datapath, 600, match, actions, idle=0, hard=0)
            self.logger.info("Installed Ambulance TCP/5001 rule on s2 (dpid=2)")

        # Rule 4: Public helpline -> CommandCenter — low priority queue
        if dpid == SWITCH_S1:
            match = parser.OFPMatch(
                eth_type=0x0800,
                ip_proto=6,
                ipv4_src=PUBLIC_HELPLINE_IP,
                ipv4_dst=COMMAND_CENTER_IP,
                tcp_src=PORT_PUBLIC_HELPLINE,
            )
            actions = self.actions_with_queue(datapath, S1_PORT_H1_COMMAND, QUEUE_LOW_PRIORITY)
            self.add_flow(datapath, 400, match, actions, idle=0, hard=0)
            self.logger.info("Installed PublicHelpline low-priority rule on s1")

        # Rule 5: Rescue team location updates (UDP/6001) — high priority on s3
        if dpid == SWITCH_S3:
            match = parser.OFPMatch(
                eth_type=0x0800,
                ip_proto=17,
                ipv4_src=RESCUE_IP,
                udp_src=PORT_RESCUE_LOCATION,
            )
            actions = self.actions_with_queue(datapath, datapath.ofproto.OFPP_FLOOD, QUEUE_HIGH_PRIORITY)
            self.add_flow(datapath, 550, match, actions, idle=0, hard=0)
            self.logger.info("Installed RescueTeam UDP/6001 rule on s3")

    # ------------------------------------------------------------------
    # MAC learning (fallback for traffic not covered by proactive rules)
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == 0x86DD:
            return  # ignore IPv6 for Review 1

        dst, src = eth.dst, eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Determine output port
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        # Map queue based on L4 match criteria (QoS policy)
        queue_id = self.classify_queue(pkt)

        actions = self.actions_with_queue(datapath, out_port, queue_id)

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, buffer_id=msg.buffer_id)
                return
            self.add_flow(datapath, 1, match, actions)

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data,
        )
        datapath.send_msg(out)

    def classify_queue(self, pkt):
        """
        Map packets to OVS queue IDs based on disaster-scenario traffic class.
        Returns QUEUE_HIGH_PRIORITY, QUEUE_LOW_PRIORITY, or QUEUE_DEFAULT.
        """
        if not self.qos_enabled:
            return QUEUE_DEFAULT

        ip = pkt.get_protocol(ipv4.ipv4)
        if not ip:
            return QUEUE_DEFAULT

        # High priority: hospital, ambulance, rescue traffic
        if ip.src in (HOSPITAL_IP, AMBULANCE_IP, RESCUE_IP):
            return QUEUE_HIGH_PRIORITY
        if ip.dst in (HOSPITAL_IP, AMBULANCE_IP) and ip.proto in (6, 17):
            tcp_pkt = pkt.get_protocol(tcp.tcp)
            udp_pkt = pkt.get_protocol(udp.udp)
            if tcp_pkt and tcp_pkt.dst_port in (PORT_AMBULANCE_DATA, PORT_HOSPITAL_CRITICAL):
                return QUEUE_HIGH_PRIORITY
            if udp_pkt and udp_pkt.src_port == PORT_RESCUE_LOCATION:
                return QUEUE_HIGH_PRIORITY

        # Low priority: public helpline bulk
        if ip.src == PUBLIC_HELPLINE_IP:
            return QUEUE_LOW_PRIORITY
        tcp_pkt = pkt.get_protocol(tcp.tcp)
        if tcp_pkt and tcp_pkt.src_port == PORT_PUBLIC_HELPLINE:
            return QUEUE_LOW_PRIORITY

        return QUEUE_DEFAULT

    # ------------------------------------------------------------------
    # Periodic stats collection (for monitoring / Review 1 evidence)
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif datapath.id in self.datapaths:
            del self.datapaths[datapath.id]

    def _request_stats_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                self._request_port_stats(dp)
                self._request_flow_stats(dp)
            hub.sleep(10)

    def _request_port_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPPortStatsRequest(datapath, 0, datapath.ofproto.OFPP_ANY)
        datapath.send_msg(req)

    def _request_flow_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in ev.msg.body:
            if stat.port_no <= 10:
                self.logger.debug(
                    "PORT dpid=%s port=%s rx_bytes=%s tx_bytes=%s",
                    dpid, stat.port_no, stat.rx_bytes, stat.tx_bytes,
                )

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in ev.msg.body:
            self.logger.debug(
                "FLOW dpid=%s priority=%s packets=%s bytes=%s",
                dpid, stat.priority, stat.packet_count, stat.byte_count,
            )
