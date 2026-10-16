"""Phase 3 trusted topology/health controller; services are owned by policy.operator.

No generic TCP/UDP packet-out fallback. The separate local policy process writes
only D153 rules; this app retains validated D152 health and ARP behavior.
"""
import json
import os
from pathlib import Path
import time
import uuid
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, arp, ipv4
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from controller.expanded_app import ExpandedPhase2App
from controller.phase2_app import HEALTH_COOKIE, ROUTE_COOKIE
from config.incident import initial_config
from config.phase2 import Attachment


class IncidentApp(ExpandedPhase2App):
    def __init__(self, *args, **kwargs):
        self.directory = Path(os.environ['SDN_PHASE3_DIR'])
        self.epoch=uuid.uuid4().hex; self.connection_versions={}; self.connection_counter=0
        self.directory.mkdir(parents=True, exist_ok=True)
        super().__init__(*args, **kwargs)
        # Retain only configured, previously observed attachment bindings across
        # a process restart. Port descriptions must still validate usability.
        try:
            previous=json.loads((self.directory/'graph.json').read_text()).get('active',{})
            for name, raw in previous.items():
                attachment=Attachment(*raw)
                if name in self.inventory.ENDPOINTS and attachment in self.inventory.ENDPOINTS[name].attachments:
                    self.state.active[name]=attachment
        except (OSError,ValueError,TypeError):
            pass
        self.publisher = hub.spawn(self.publish_loop)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        if ev.msg.datapath.id in self.names:
            self.connection_counter+=1
            self.connection_versions[self.names[ev.msg.datapath.id]]=self.epoch+'-'+str(self.connection_counter)
        super().features(ev)

    def install_route(self, source, target, route, cookie=ROUTE_COOKIE, sport=None, dport=None):
        if cookie == HEALTH_COOKIE:
            return super().install_route(source,target,route,cookie,sport,dport)

    def publish(self):
        data = {'monotonic':time.monotonic(), 'districts':self.inventory.districts,
                'online':sorted(self.state.online), 'up':sorted(self.state.up),
                'active':{n:[a.switch,a.port,a.host_port] for n,a in self.state.active.items()},
                'connections':self.connection_versions}
        path=self.directory/'graph.json'; temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data)); temporary.replace(path)

    def publish_loop(self):
        while self.is_active:
            self.publish(); hub.sleep(.5)

    def reconcile(self):
        super().reconcile()
        self.publish()

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg=ev.msg; pkt=packet.Packet(msg.data)
        if pkt.get_protocol(ipv4.ipv4) is not None:
            return  # Exact installed service/ICMP rules or fail closed.
        eth=pkt.get_protocol(ethernet.ethernet); request=pkt.get_protocol(arp.arp)
        if eth is None or request is None: return
        try:
            registered=set(json.loads((self.directory/'registry.json').read_text())['registered'])
        except FileNotFoundError:
            registered=set(initial_config(self.inventory)['registered'])
        except (ValueError, KeyError):
            return
        source=self.by_mac.get(eth.src); target=self.by_ip.get(request.dst_ip)
        if source not in registered or (target is not None and target not in registered): return
        super().packet_in(ev)
