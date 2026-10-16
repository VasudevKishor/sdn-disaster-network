#!/usr/bin/env python3
"""Phase 1 inventory-based IPv4 forwarding, proxy ARP and legacy QoS classes.

Run: ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
Only the configured chain is supported. Registered endpoints may communicate;
unknown/spoofed attachments, IPv6, fragments and unknown broadcasts are dropped.
This static binding is not authentication or the Phase 3 service-policy engine.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.lab import AGENCY_HOSTS, HOST_BY_IP, HOST_BY_MAC, egress, switch_name, TRAFFIC_PORTS
from controller.matches import forwarding_match
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, tcp, udp, arp
from ryu.lib import hub

HOSPITAL_IP = AGENCY_HOSTS["h2"]["ip"]
AMBULANCE_IP = AGENCY_HOSTS["h4"]["ip"]
COMMAND_CENTER_IP = AGENCY_HOSTS["h1"]["ip"]
RESCUE_IP = AGENCY_HOSTS["h5"]["ip"]
PUBLIC_HELPLINE_IP = AGENCY_HOSTS["h7"]["ip"]
PORT_AMBULANCE_DATA = TRAFFIC_PORTS["ambulance_data"]
PORT_HOSPITAL_CRITICAL = TRAFFIC_PORTS["hospital_critical"]
PORT_RESCUE_LOCATION = TRAFFIC_PORTS["rescue_location"]
PORT_PUBLIC_HELPLINE = TRAFFIC_PORTS["public_helpline"]
PORT_DRONE_VIDEO = TRAFFIC_PORTS["drone_video"]
QUEUE_HIGH_PRIORITY, QUEUE_LOW_PRIORITY, QUEUE_DEFAULT = 0, 1, 0
FLOW_COOKIE = 0xD150000000000001


def load_queue_config():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "qos", "queue_config.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class FlowManagerApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.qos_enabled = os.environ.get("DISASTER_QOS", "1") == "1"
        self.queue_config = load_queue_config()
        self.stats_thread = hub.spawn(self._request_stats_loop)

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

        if buffer_id is None:
            buffer_id = ofproto.OFP_NO_BUFFER

        mod = parser.OFPFlowMod(
            datapath=datapath,
            cookie=FLOW_COOKIE,
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
            cookie=FLOW_COOKIE, cookie_mask=0xffffffffffffffff,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match if match else parser.OFPMatch(),
        )
        datapath.send_msg(mod)

    def actions_with_queue(self, datapath, port, queue_id=QUEUE_DEFAULT):
        parser = datapath.ofproto_parser
        actions = [parser.OFPActionSetQueue(queue_id)] if self.qos_enabled else []
        return actions + [parser.OFPActionOutput(port)]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        if switch_name(dp.id) is None:
            self.logger.error("Unconfigured datapath %s; refusing forwarding", dp.id)
            return
        self.datapaths[dp.id] = dp
        # Own rules only; FIFO on this connection orders deletion before additions.
        self.delete_flow(dp)
        # Ryu's legacy REST converter cannot express this Nicira field. A higher
        # priority owned drop protects both reactive and manual example rules.
        self.add_flow(dp, 65000, forwarding_match(dp.ofproto_parser,
            eth_type=0x0800, ip_frag=(1, 1)), [])
        self.add_flow(dp, 0, dp.ofproto_parser.OFPMatch(),
                      [dp.ofproto_parser.OFPActionOutput(
                          dp.ofproto.OFPP_CONTROLLER, dp.ofproto.OFPCML_NO_BUFFER)])
        self.logger.info("Switch connected: dpid=%s (QoS=%s)", dp.id, self.qos_enabled)

    def packet_out(self, msg, actions, data=None):
        dp = msg.datapath
        # Generated ARP replies are separate packets, never switch-buffer contents.
        generated = data is not None
        buffer_id = dp.ofproto.OFP_NO_BUFFER if generated else msg.buffer_id
        dp.send_msg(dp.ofproto_parser.OFPPacketOut(
            datapath=dp, buffer_id=buffer_id,
            in_port=dp.ofproto.OFPP_CONTROLLER if generated else msg.match["in_port"],
            actions=actions, data=data if generated else (
                msg.data if buffer_id == dp.ofproto.OFP_NO_BUFFER else None)))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg, dp = ev.msg, ev.msg.datapath
        switch = switch_name(dp.id)
        if switch is None:
            return
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        source = HOST_BY_MAC.get(eth.src)
        ingress = msg.match["in_port"]
        if source is None or ingress != egress(switch, source):
            return
        request = pkt.get_protocol(arp.arp)
        if request is not None:
            # Proxy only at the registered local attachment. Never flood the core.
            if (source["switch"] != switch or request.opcode != arp.ARP_REQUEST
                    or request.src_ip != source["ip"] or request.src_mac != source["mac"]
                    or request.hwtype != 1 or request.proto != 0x0800
                    or request.hlen != 6 or request.plen != 4):
                return
            target = HOST_BY_IP.get(request.dst_ip)
            if target is None:
                return
            reply = packet.Packet()
            reply.add_protocol(ethernet.ethernet(dst=eth.src, src=target["mac"], ethertype=0x0806))
            reply.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=target["mac"],
                                      src_ip=target["ip"], dst_mac=eth.src, dst_ip=source["ip"]))
            reply.serialize()
            self.packet_out(msg, [dp.ofproto_parser.OFPActionOutput(ingress)], reply.data)
            return
        ip = pkt.get_protocol(ipv4.ipv4)
        if (ip is None or ip.src != source["ip"] or ip.offset or ip.flags & 1):
            return
        target = HOST_BY_IP.get(ip.dst)
        if target is None or eth.dst != target["mac"]:
            return
        out_port = egress(switch, target)
        if out_port == ingress:
            return
        fields = dict(in_port=ingress, eth_src=eth.src, eth_dst=eth.dst,
                      eth_type=0x0800, ipv4_src=ip.src, ipv4_dst=ip.dst, ip_proto=ip.proto,
                      ip_frag=0)
        # Every classifier input must be represented in the installed match.
        transport = pkt.get_protocol(tcp.tcp if ip.proto == 6 else udp.udp) if ip.proto in (6, 17) else None
        if ip.proto in (6, 17):
            if transport is None:
                return
            prefix = "tcp" if ip.proto == 6 else "udp"
            fields[prefix + "_src"] = transport.src_port
            fields[prefix + "_dst"] = transport.dst_port
        actions = self.actions_with_queue(dp, out_port, self.classify_queue(pkt))
        self.add_flow(dp, 10, forwarding_match(dp.ofproto_parser, **fields), actions,
                      buffer_id=msg.buffer_id, idle=30)
        if msg.buffer_id == dp.ofproto.OFP_NO_BUFFER:
            self.packet_out(msg, actions)

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
    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error_msg_handler(self, ev):
        msg = ev.msg
        self.logger.error("OPENFLOW_ERROR dpid=%s type=%s code=%s", msg.datapath.id, msg.type, msg.code)

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
