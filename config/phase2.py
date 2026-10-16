"""Small Phase 2 routing/bond prototype; independent of the Phase 1 inventory.

The physical links are trusted configuration, not dynamically discovered cables.
No service-level policy, QoS allocation, or 20-switch completion is claimed here.
"""
from collections import deque
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
import sys


@dataclass(frozen=True)
class Site:
    subnet: str
    gateway: str
    gateway_mac: str


@dataclass(frozen=True, order=True)
class Attachment:
    switch: str
    port: int
    host_port: int = 0


@dataclass(frozen=True)
class Endpoint:
    site: str
    ip: str
    mac: str
    attachments: tuple


@dataclass(frozen=True)
class Link:
    a: str
    pa: int
    b: str
    pb: int
    rate_mbps: int


SWITCHES = {"p2s1": 201, "p2s2": 202, "p2s3": 203}
SITES = {
    "command": Site("10.20.1.0/24", "10.20.1.1", "02:20:00:01:00:01"),
    "hospital": Site("10.20.2.0/24", "10.20.2.1", "02:20:00:02:00:01"),
    "guest": Site("10.20.3.0/24", "10.20.3.1", "02:20:00:03:00:01"),
}
ENDPOINTS = {
    "p2h1": Endpoint("command", "10.20.1.10", "02:20:00:00:00:11",
                      (Attachment("p2s1", 1, 0), Attachment("p2s2", 1, 1))),
    "p2h2": Endpoint("hospital", "10.20.2.10", "02:20:00:00:00:12",
                      (Attachment("p2s3", 1),)),
    "p2h3": Endpoint("guest", "10.20.3.10", "02:20:00:00:00:13",
                      (Attachment("p2s3", 2),)),
}
LINKS = (Link("p2s1", 10, "p2s3", 10, 100),
         Link("p2s2", 10, "p2s3", 11, 50))
# Foundation isolation only, not Phase 3 service permissions.
ALLOWED_SITE_PAIRS = frozenset({("command", "hospital"), ("hospital", "command")})
# Reserved, attachment-specific UDP health paths to a real remote endpoint.
HEALTH_TARGETS = {"p2h1": "p2h2"}
HEALTH_PORT = 61999
HEALTH_SOURCE_BASE = 62000


def health_source_port(attachment):
    return HEALTH_SOURCE_BASE + attachment.host_port


def validate_inventory():
    used, ips, macs = set(), set(), set()
    nets = [IPv4Network(s.subnet) for s in SITES.values()]
    if any(a.overlaps(b) for i, a in enumerate(nets) for b in nets[i + 1:]):
        raise ValueError("Overlapping site subnets")
    for site in SITES.values():
        if IPv4Address(site.gateway) not in IPv4Network(site.subnet):
            raise ValueError("Gateway outside site subnet")
    for link in LINKS:
        if link.rate_mbps <= 0 or link.a == link.b:
            raise ValueError("Invalid physical link")
        for port in ((link.a, link.pa), (link.b, link.pb)):
            if port[0] not in SWITCHES or port[1] <= 0 or port in used:
                raise ValueError("Invalid or duplicate switch port")
            used.add(port)
    for endpoint in ENDPOINTS.values():
        if endpoint.ip in ips or endpoint.mac in macs or not endpoint.attachments:
            raise ValueError("Duplicate or unattached endpoint")
        ips.add(endpoint.ip)
        macs.add(endpoint.mac)
        site = SITES[endpoint.site]
        if IPv4Address(endpoint.ip) not in IPv4Network(site.subnet) or endpoint.ip == site.gateway:
            raise ValueError("Invalid endpoint subnet")
        for attach in endpoint.attachments:
            port = (attach.switch, attach.port)
            if attach.switch not in SWITCHES or attach.port <= 0 or port in used:
                raise ValueError("Invalid or duplicate endpoint port")
            used.add(port)


def expected_ports(switch):
    ports = {a.port for e in ENDPOINTS.values() for a in e.attachments if a.switch == switch}
    for link in LINKS:
        if link.a == switch:
            ports.add(link.pa)
        if link.b == switch:
            ports.add(link.pb)
    return ports


def permitted(source, target):
    if source == target:
        return False
    src, dst = ENDPOINTS[source], ENDPOINTS[target]
    return src.site == dst.site or (src.site, dst.site) in ALLOWED_SITE_PAIRS


class ForwardingState:
    """Fail closed on unknown ports; observed mobility stays inside allowed sets."""
    def __init__(self, inventory=None):
        self.inventory = inventory or sys.modules[__name__]
        self.inventory.validate_inventory()
        self.online = set()
        self.up = set()
        self.active = {}
        self._topology_key, self._paths, self._adjacency = None, {}, {}

    def usable(self, attachment):
        return attachment.switch in self.online and (attachment.switch, attachment.port) in self.up

    def attachment(self, name):
        previous = self.active.get(name)
        if previous is not None and self.usable(previous):
            return previous
        return next((a for a in self.inventory.ENDPOINTS[name].attachments if self.usable(a)), None)

    def observe(self, name, switch, port):
        attach = next((a for a in self.inventory.ENDPOINTS[name].attachments
                       if (a.switch, a.port) == (switch, port)), None)
        if attach is None or not self.usable(attach):
            return False
        self.active[name] = attach
        return True

    def path(self, start, end):
        if start not in self.online or end not in self.online:
            return None
        key = (frozenset(self.online), frozenset(self.up))
        if key != self._topology_key:
            self._topology_key, self._paths = key, {}
            self._adjacency = {node: [] for node in self.online}
            for link in self.inventory.LINKS:
                if (link.a not in self.online or link.b not in self.online
                        or (link.a, link.pa) not in self.up or (link.b, link.pb) not in self.up):
                    continue
                self._adjacency[link.a].append((link.b, link.pa, link.pb))
                self._adjacency[link.b].append((link.a, link.pb, link.pa))
        if (start, end) in self._paths:
            return self._paths[start, end]
        pending = deque([(start, ())])
        seen = {start}
        while pending:
            node, hops = pending.popleft()
            if node == end:
                self._paths[start, end] = hops
                return hops
            for peer, output, ingress in sorted(self._adjacency[node]):
                if peer in self.online and peer not in seen:
                    seen.add(peer)
                    pending.append((peer, hops + ((node, output, peer, ingress),)))
        self._paths[start, end] = None
        return None

    def route(self, source, target, source_attachment=None, target_attachment=None):
        """Return switch/ingress/egress steps, with no flood or revisited switch."""
        if not self.inventory.permitted(source, target):
            return ()
        src = source_attachment if source_attachment is not None else self.attachment(source)
        dst = target_attachment if target_attachment is not None else self.attachment(target)
        if src is None or dst is None:
            return ()
        if (src not in self.inventory.ENDPOINTS[source].attachments or dst not in self.inventory.ENDPOINTS[target].attachments
                or not self.usable(src) or not self.usable(dst)):
            return ()
        path = self.path(src.switch, dst.switch)
        if path is None:
            return ()
        result, ingress = [], src.port
        for node, output, _peer, next_ingress in path:
            result.append((node, ingress, output))
            ingress = next_ingress
        result.append((dst.switch, ingress, dst.port))
        return tuple(result)


validate_inventory()


def load_inventory(name="prototype", districts=2):
    if name == "prototype":
        return sys.modules[__name__]
    if name == "expanded":
        from config.expanded import build_inventory
        return build_inventory(districts)
    raise ValueError("Unknown Phase 2 inventory: " + name)
