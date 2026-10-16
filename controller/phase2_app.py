"""Phase 2 prototype: configured routing, site gateways and permitted mobility.

No flooding, NORMAL action, learning fallback, REST mutation or QoS is enabled.
Gateway routing is one logical hop: rewrite Ethernet and decrement TTL at ingress.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.phase2 import ENDPOINTS, SITES, SWITCHES, ForwardingState, expected_ports, permitted
from config.phase2 import HEALTH_TARGETS, HEALTH_PORT, health_source_port
from config.phase2 import load_inventory
from controller.matches import forwarding_match
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp, ipv4, tcp, udp

BASE_COOKIE = 0xD152000000000001
ROUTE_COOKIE = 0xD152000000000002
HEALTH_COOKIE = 0xD152000000000003
OWNER_MASK = 0xffff000000000000
PROTOCOLS = (1, 6, 17)


class Phase2App(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        self.inventory = load_inventory(kwargs.pop("inventory_name", "prototype"), kwargs.pop("districts", 2))
        super().__init__(*args, **kwargs)
        self.state, self.datapaths = ForwardingState(self.inventory), {}
        self.names = {value: key for key, value in self.inventory.SWITCHES.items()}
        self.by_mac = {e.mac: name for name, e in self.inventory.ENDPOINTS.items()}
        self.by_ip = {e.ip: name for name, e in self.inventory.ENDPOINTS.items()}
        self.descriptions = {}

    def delete(self, dp, cookie, mask=0xffffffffffffffff):
        dp.send_msg(dp.ofproto_parser.OFPFlowMod(
            dp, command=dp.ofproto.OFPFC_DELETE, cookie=cookie, cookie_mask=mask,
            table_id=0, out_port=dp.ofproto.OFPP_ANY, out_group=dp.ofproto.OFPG_ANY,
            match=dp.ofproto_parser.OFPMatch()))

    def flow(self, dp, cookie, priority, match, actions):
        p = dp.ofproto_parser
        dp.send_msg(p.OFPFlowMod(dp, cookie=cookie, priority=priority, match=forwarding_match(p, **match),
                                instructions=[p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath
        if dp.id not in self.names:
            self.logger.error("UNCONFIGURED_DATAPATH %s", dp.id)
            return
        self.datapaths[dp.id] = dp
        name = self.names[dp.id]
        self.state.online.discard(name)
        self.state.up = {v for v in self.state.up if v[0] != name}
        self.descriptions[dp.id] = {}
        self.delete(dp, BASE_COOKIE, OWNER_MASK)
        self.flow(dp, BASE_COOKIE, 65000, {"eth_type": 0x0800, "ip_frag": (1, 1)}, [])
        self.flow(dp, BASE_COOKIE, 0, {}, [dp.ofproto_parser.OFPActionOutput(
            dp.ofproto.OFPP_CONTROLLER, dp.ofproto.OFPCML_NO_BUFFER)])
        dp.send_msg(dp.ofproto_parser.OFPPortDescStatsRequest(dp, 0))
        self.reconcile()

    def current(self, dp):
        return self.datapaths.get(dp.id) is dp

    def port_up(self, dp, desc):
        disabled = dp.ofproto.OFPPC_PORT_DOWN | dp.ofproto.OFPPC_NO_RECV | dp.ofproto.OFPPC_NO_FWD
        unavailable = dp.ofproto.OFPPS_LINK_DOWN | dp.ofproto.OFPPS_BLOCKED
        return not (desc.config & disabled or desc.state & unavailable)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def ports(self, ev):
        dp = ev.msg.datapath
        if not self.current(dp):
            return
        name = self.names[dp.id]
        entries = self.descriptions[dp.id]
        for desc in ev.msg.body:
            if desc.port_no < dp.ofproto.OFPP_MAX:
                entries[desc.port_no] = desc
        if ev.msg.flags & dp.ofproto.OFPMPF_REPLY_MORE:
            return
        expected = self.inventory.expected_ports(name)
        valid = set(entries) == expected and all(
            entries[port].name.rstrip(b"\0").decode() == "%s-eth%d" % (name, port) for port in expected)
        if not valid:
            self.logger.error("PORT_INVENTORY_MISMATCH %s", name)
            self.state.online.discard(name)
        else:
            self.state.online.add(name)
            self.state.up = {v for v in self.state.up if v[0] != name}
            self.state.up.update((name, port) for port, desc in entries.items() if self.port_up(dp, desc))
        self.reconcile()

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status(self, ev):
        dp, desc = ev.msg.datapath, ev.msg.desc
        if not self.current(dp):
            return
        name = self.names[dp.id]
        if desc.port_no not in self.inventory.expected_ports(name):
            return
        key = (name, desc.port_no)
        self.state.up.discard(key)
        if ev.msg.reason != dp.ofproto.OFPPR_DELETE and self.port_up(dp, desc):
            self.state.up.add(key)
        self.reconcile()

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def disconnected(self, ev):
        dp = ev.datapath
        if self.current(dp):
            del self.datapaths[dp.id]
            self.state.online.discard(self.names[dp.id])
            self.reconcile()

    def route_fields(self, source, target, step, first, protocol):
        src, dst = self.inventory.ENDPOINTS[source], self.inventory.ENDPOINTS[target]
        routed = src.site != dst.site
        eth_src = src.mac if first or not routed else self.inventory.SITES[dst.site].gateway_mac
        eth_dst = self.inventory.SITES[src.site].gateway_mac if first and routed else dst.mac
        return dict(in_port=step[1], eth_type=0x0800, eth_src=eth_src, eth_dst=eth_dst,
                    ipv4_src=src.ip, ipv4_dst=dst.ip, ip_proto=protocol, ip_frag=0)

    def route_actions(self, dp, source, target, step, first):
        p, src, dst = dp.ofproto_parser, self.inventory.ENDPOINTS[source], self.inventory.ENDPOINTS[target]
        actions = []
        if first and src.site != dst.site:
            actions = [p.OFPActionDecNwTtl(), p.OFPActionSetField(eth_src=self.inventory.SITES[dst.site].gateway_mac),
                       p.OFPActionSetField(eth_dst=dst.mac)]
        return actions + [p.OFPActionOutput(step[2])]

    def install_route(self, source, target, route, cookie=ROUTE_COOKIE, sport=None, dport=None):
        for i in reversed(range(len(route))):
            step = route[i]
            dp = self.datapaths[self.inventory.SWITCHES[step[0]]]
            for protocol in PROTOCOLS if sport is None else (17,):
                fields = self.route_fields(source, target, step, i == 0, protocol)
                if sport is not None:
                    fields.update(udp_src=sport, udp_dst=dport)
                self.flow(dp, cookie, 100 if sport is None else 200, fields,
                          self.route_actions(dp, source, target, step, i == 0))

    def reconcile(self):
        # Sequential, owned-rule refresh, not an atomic network-wide update.
        for dp in self.datapaths.values():
            self.delete(dp, ROUTE_COOKIE)
            self.delete(dp, HEALTH_COOKIE)
            # Reserve the probe ports even when a path is missing. A generic
            # UDP route must never send a backup probe reply to the active NIC.
            for field, port in (("udp_src", HEALTH_PORT), ("udp_dst", HEALTH_PORT)):
                self.flow(dp, HEALTH_COOKIE, 150,
                          {"eth_type": 0x0800, "ip_proto": 17, field: port}, [])
        for source in self.inventory.ENDPOINTS:
            for target in self.inventory.ENDPOINTS:
                route = self.state.route(source, target)
                # Install downstream first. Cross-switch completion still needs
                # live verification; no barrier/readback transaction is claimed.
                self.install_route(source, target, route)
        for source, target in self.inventory.HEALTH_TARGETS.items():
            for attachment in self.inventory.ENDPOINTS[source].attachments:
                port = health_source_port(attachment)
                routes = ((source, target, self.state.route(source, target, source_attachment=attachment), port, HEALTH_PORT),
                          (target, source, self.state.route(target, source, target_attachment=attachment), HEALTH_PORT, port))
                for src, dst, route, sport, dport in routes:
                    self.install_route(src, dst, route, HEALTH_COOKIE, sport, dport)

    def packet_out(self, msg, actions, data=None):
        dp = msg.datapath
        dp.send_msg(dp.ofproto_parser.OFPPacketOut(
            dp, buffer_id=dp.ofproto.OFP_NO_BUFFER, in_port=dp.ofproto.OFPP_CONTROLLER,
            actions=actions, data=msg.data if data is None else data))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg, dp = ev.msg, ev.msg.datapath
        if not self.current(dp) or self.names[dp.id] not in self.state.online:
            return
        if getattr(msg, "reason", dp.ofproto.OFPR_NO_MATCH) != dp.ofproto.OFPR_NO_MATCH:
            return  # Includes expired TTL: never bypass the switch's TTL drop.
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        source = self.by_mac.get(eth.src)
        if source is None:
            return
        src = self.inventory.ENDPOINTS[source]
        switch, port = self.names[dp.id], msg.match["in_port"]
        if not any((a.switch, a.port) == (switch, port) for a in src.attachments):
            return  # Never accept an identity learned on a core or unrelated port.
        request = pkt.get_protocol(arp.arp)
        if eth.ethertype == 0x0806 and request is not None:
            if (request.opcode != arp.ARP_REQUEST or request.src_ip != src.ip or request.src_mac != src.mac
                    or (request.hwtype, request.proto, request.hlen, request.plen) != (1, 0x0800, 6, 4)):
                return
            site = self.inventory.SITES[src.site]
            target = self.by_ip.get(request.dst_ip)
            if request.dst_ip != site.gateway and (target is None or self.inventory.ENDPOINTS[target].site != src.site):
                return
            if not self.observe(source, switch, port):
                return
            if request.dst_ip == src.ip:  # Bond GARP updates location, never floods.
                return
            mac = site.gateway_mac if request.dst_ip == site.gateway else self.inventory.ENDPOINTS[target].mac
            reply = packet.Packet()
            reply.add_protocol(ethernet.ethernet(src=mac, dst=src.mac, ethertype=0x0806))
            reply.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=mac, src_ip=request.dst_ip,
                                      dst_mac=src.mac, dst_ip=src.ip))
            reply.serialize()
            self.packet_out(msg, [dp.ofproto_parser.OFPActionOutput(port)], reply.data)
            return
        ip = pkt.get_protocol(ipv4.ipv4)
        if (eth.ethertype != 0x0800 or ip is None or ip.src != src.ip or ip.proto not in PROTOCOLS
                or ip.offset or ip.flags & 1 or ip.ttl <= 1):
            return
        target = self.by_ip.get(ip.dst)
        if target is None or not self.inventory.permitted(source, target):
            return
        dst = self.inventory.ENDPOINTS[target]
        if eth.dst != (self.inventory.SITES[src.site].gateway_mac if src.site != dst.site else dst.mac):
            return
        if ip.proto in (6, 17) and pkt.get_protocol(tcp.tcp if ip.proto == 6 else udp.udp) is None:
            return
        transport = pkt.get_protocol(udp.udp)
        if transport is not None and HEALTH_PORT in (transport.src_port, transport.dst_port):
            return  # Probe misses never learn/move an application attachment.
        if not self.observe(source, switch, port):
            return
        route = self.state.route(source, target)
        if route and route[0][:2] == (switch, port):
            self.packet_out(msg, self.route_actions(dp, source, target, route[0], True))

    def observe(self, source, switch, port):
        previous = self.state.attachment(source)
        if not self.state.observe(source, switch, port):
            return False
        if previous != self.state.active[source]:
            self.logger.info("ATTACHMENT_CHANGE %s %s:%s", source, switch, port)
            self.reconcile()
        return True

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error(self, ev):
        dp = ev.msg.datapath
        self.logger.error("OPENFLOW_ERROR dpid=%s type=%s code=%s", dp.id, ev.msg.type, ev.msg.code)
        if self.current(dp):
            self.state.online.discard(self.names[dp.id])
            self.reconcile()
