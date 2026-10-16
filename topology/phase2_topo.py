"""Three-switch prototype with explicit site routes and one active-backup bond."""
from mininet.topo import Topo
from mininet.node import Host
from mininet.link import TCLink
from config.phase2 import ENDPOINTS, LINKS, SITES, SWITCHES
from config.phase2 import load_inventory


class SiteHost(Host):
    def checked(self, args):
        out, err, code = self.pexec(args)
        if code:
            raise RuntimeError("%s: %s failed: %s%s" % (self.name, args, out, err))
        return out

    def config(self, endpoint_name=None, inventory_name="prototype", districts=2, **params):
        # No IP on a slave; Mininet tracks the physical links for scoped cleanup.
        result = super().config(ip=None, mac=None)
        inventory = load_inventory(inventory_name, districts)
        endpoint = inventory.ENDPOINTS[endpoint_name or self.name]
        interfaces = ["%s-eth%d" % (self.name, a.host_port) for a in endpoint.attachments]
        for interface in interfaces:
            self.checked(["ip", "addr", "flush", "dev", interface])
            self.checked(["ip", "link", "set", interface, "down"])
        interface = interfaces[0]
        if len(interfaces) > 1:
            interface = "bond0"  # Namespace-local, not a host-wide device.
            self.checked(["ip", "link", "add", interface, "type", "bond", "mode", "active-backup",
                          "miimon", "100", "primary_reselect", "failure", "fail_over_mac", "none"])
            self.checked(["ip", "link", "set", interface, "address", endpoint.mac])
            for slave in interfaces:
                self.checked(["ip", "link", "set", slave, "master", interface])
            self.checked(["ip", "link", "set", interface, "type", "bond", "primary", interfaces[0]])
        else:
            self.checked(["ip", "link", "set", interface, "address", endpoint.mac])
        for slave in interfaces:
            self.checked(["ip", "link", "set", slave, "up"])
        self.checked(["ip", "link", "set", interface, "up"])
        site = inventory.SITES[endpoint.site]
        prefix = site.subnet.split("/")[1]
        self.checked(["ip", "addr", "add", endpoint.ip + "/" + prefix, "dev", interface])
        self.checked(["ip", "route", "replace", "default", "via", site.gateway, "dev", interface])
        self.application_interface = interface
        return result


class Phase2Topo(Topo):
    def build(self, inventory_name="prototype", districts=2):
        self.inventory = load_inventory(inventory_name, districts)
        for name, dpid in self.inventory.SWITCHES.items():
            self.addSwitch(name, dpid="%016x" % dpid, protocols="OpenFlow13", failMode="secure",
                           inband=False)
        for name in self.inventory.ENDPOINTS:
            self.addHost(name, cls=SiteHost, endpoint_name=name, inventory_name=inventory_name, districts=districts, ip=None)
        for link in self.inventory.LINKS:
            self.addLink(link.a, link.b, port1=link.pa, port2=link.pb,
                         cls=TCLink, bw=link.rate_mbps, delay="2ms")
        for name, endpoint in self.inventory.ENDPOINTS.items():
            for attach in endpoint.attachments:
                self.addLink(name, attach.switch, port1=attach.host_port, port2=attach.port,
                             cls=TCLink, bw=50, delay="1ms")


topos = {"phase2": Phase2Topo}
