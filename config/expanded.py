"""Agreed two-district topology: 4 backbone + 4 aggregation + 12 access.

Eight physical sites, three endpoints per site. Each shelter's third endpoint is
in a separate guest subnet on the shelter access switch. Fixed site isolation is
Phase 2 admission, not the later destination-service policy engine.
"""
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from config.phase2 import Site, Endpoint, Attachment, Link, HEALTH_PORT, health_source_port


@dataclass
class NetworkInventory:
    SWITCHES: dict
    SITES: dict
    ENDPOINTS: dict
    LINKS: tuple
    ALLOWED_SITE_PAIRS: frozenset
    HEALTH_TARGETS: dict
    SWITCH_ROLES: dict
    SITE_ACCESS: dict
    SITE_ROLES: dict
    LOCATIONS: dict
    districts: int

    def expected_ports(self, switch):
        ports = {a.port for e in self.ENDPOINTS.values() for a in e.attachments if a.switch == switch}
        for link in self.LINKS:
            if link.a == switch:
                ports.add(link.pa)
            if link.b == switch:
                ports.add(link.pb)
        return ports

    def permitted(self, source, target):
        if source not in self.ENDPOINTS or target not in self.ENDPOINTS or source == target:
            return False
        src, dst = self.ENDPOINTS[source], self.ENDPOINTS[target]
        return src.site == dst.site or (src.site, dst.site) in self.ALLOWED_SITE_PAIRS

    def validate_inventory(self):
        if len(set(self.SWITCHES.values())) != len(self.SWITCHES):
            raise ValueError("Duplicate datapath ID")
        nets = [IPv4Network(s.subnet) for s in self.SITES.values()]
        if any(a.overlaps(b) for i, a in enumerate(nets) for b in nets[i + 1:]):
            raise ValueError("Overlapping site subnets")
        ips, macs, used = set(), set(), set()
        for site in self.SITES.values():
            if IPv4Address(site.gateway) not in IPv4Network(site.subnet).hosts():
                raise ValueError("Invalid gateway")
            if site.gateway in ips or site.gateway_mac in macs:
                raise ValueError("Duplicate gateway identity")
            ips.add(site.gateway)
            macs.add(site.gateway_mac)
        for link in self.LINKS:
            if link.rate_mbps <= 0 or link.a == link.b:
                raise ValueError("Invalid infrastructure link")
            for switch, port in ((link.a, link.pa), (link.b, link.pb)):
                if switch not in self.SWITCHES or port <= 0 or (switch, port) in used:
                    raise ValueError("Invalid or duplicate infrastructure port")
                used.add((switch, port))
        for name, endpoint in self.ENDPOINTS.items():
            site = self.SITES[endpoint.site]
            if (not endpoint.attachments or endpoint.ip in ips or endpoint.mac in macs
                    or IPv4Address(endpoint.ip) not in IPv4Network(site.subnet).hosts()):
                raise ValueError("Invalid endpoint identity: " + name)
            ips.add(endpoint.ip)
            macs.add(endpoint.mac)
            host_ports = set()
            for a in endpoint.attachments:
                if (a.switch not in self.SWITCHES or a.port <= 0 or (a.switch, a.port) in used
                        or a.host_port < 0 or a.host_port in host_ports):
                    raise ValueError("Invalid endpoint attachment: " + name)
                if len(("%s-eth%d" % (a.switch, a.port)).encode()) > 15 or len(("%s-eth%d" % (name, a.host_port)).encode()) > 15:
                    raise ValueError("Interface name exceeds Linux limit")
                used.add((a.switch, a.port))
                host_ports.add(a.host_port)
        for source, target in self.HEALTH_TARGETS.items():
            ports = [health_source_port(a) for a in self.ENDPOINTS[source].attachments]
            if (not self.permitted(source, target) or len(set(ports)) != len(ports)
                    or any(not 1 <= port <= 65535 or port == HEALTH_PORT for port in ports)):
                raise ValueError("Invalid remote health peer/port")
        if any(a not in self.SITES or b not in self.SITES for a, b in self.ALLOWED_SITE_PAIRS):
            raise ValueError("Unknown site in permission matrix")


def build_inventory(districts=2):
    if districts not in (1, 2):
        raise ValueError("District count must be 1 or 2")
    switches, roles, sites, site_roles, endpoints, locations, access, health = {}, {}, {}, {}, {}, {}, {}, {}
    links, next_port = [], {}
    def switch(name, dpid, role):
        switches[name], roles[name], next_port[name] = dpid, role, 10
    def connect(a, b, rate):
        links.append(Link(a, next_port[a], b, next_port[b], rate))
        next_port[a] += 1
        next_port[b] += 1
    for index in range(districts * 2):
        switch("bb%d" % (index + 1), 301 + index, "backbone")
    for a, b, rate in ((1, 2, 100), (3, 4, 100), (1, 3, 100), (2, 4, 50), (1, 4, 25), (2, 3, 50)):
        if "bb%d" % b in switches:
            connect("bb%d" % a, "bb%d" % b, rate)
    access_index = 0
    for district in range(districts):
        prefix = "ab"[district]
        aggs = [prefix + "ag1", prefix + "ag2"]
        for index, agg in enumerate(aggs):
            switch(agg, 311 + district * 2 + index, "aggregation")
            for core in (district * 2 + 1, district * 2 + 2):
                connect(agg, "bb%d" % core, 100 if index == 0 else 50)
        for index, (role, code) in enumerate((("command", "c"), ("hospital", "h"), ("field", "f"), ("shelter", "s")), 1):
            site = prefix + "_" + role
            subnet = "10.%d.%d" % (30 + district, index)
            sites[site] = Site(subnet + ".0/24", subnet + ".1", "02:30:%02x:%02x:00:01" % (district + 1, index))
            site_roles[site] = role
            critical = role in ("command", "hospital")
            nodes = []
            for number in range(2 if critical else 1):
                node = prefix + code + "c%d" % (number + 1)
                switch(node, 321 + access_index, "access")
                access_index += 1
                nodes.append(node)
                for agg in aggs:
                    connect(node, agg, 100 if agg.endswith("1") else 50)
            access[site] = tuple(nodes)
            for number in range(1, 4):
                name = prefix + code + str(number)
                logical_site, network = site, subnet
                if role == "shelter" and number == 3:
                    logical_site = prefix + "_guest"
                    network = "10.%d.5" % (30 + district)
                    sites[logical_site] = Site(network + ".0/24", network + ".1", "02:30:%02x:05:00:01" % (district + 1))
                    site_roles[logical_site] = "guest"
                endpoints[name] = Endpoint(logical_site, network + ".%d" % (9 + number),
                    "02:31:%02x:%02x:%02x:01" % (district + 1, index, number),
                    tuple(Attachment(node, number, host_port) for host_port, node in enumerate(nodes)))
                locations[name] = site
                if critical:
                    health[name] = prefix + "f1"
    allowed = frozenset((a, b) for a in sites for b in sites if a != b
                        and "guest" not in (site_roles[a], site_roles[b])
                        and {site_roles[a], site_roles[b]} != {"hospital", "shelter"})
    result = NetworkInventory(switches, sites, endpoints, tuple(links), allowed, health,
                              roles, access, site_roles, locations, districts)
    result.validate_inventory()
    return result


if __name__ == "__main__":
    import argparse
    from dataclasses import asdict
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    print(json.dumps(asdict(build_inventory(args.districts)), default=sorted, indent=2))
