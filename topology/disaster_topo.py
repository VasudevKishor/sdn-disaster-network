#!/usr/bin/env python3
"""Phase 1 chain s1--s2--s3; seven endpoints on the legacy flat /24.

Keep the third link disabled until Phase 2 forwarding is validated.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mininet.topo import Topo
from mininet.link import TCLink
from config.lab import (AGENCY_HOSTS, TRAFFIC_PORTS, SWITCHES, CORE_LINKS,
                        CORE_RATE_MBPS, ACCESS_RATE_MBPS)


class DisasterResponseTopo(Topo):
    def build(self, n=0, **_kwargs):
        for name, dpid in SWITCHES.items():
            self.addSwitch(name, dpid="%016x" % dpid, protocols="OpenFlow13",
                           failMode="secure")
        for name, meta in AGENCY_HOSTS.items():
            self.addHost(name, ip=meta["ip"] + "/24", mac=meta["mac"])
        for a, pa, b, pb in CORE_LINKS:
            self.addLink(a, b, port1=pa, port2=pb, cls=TCLink,
                         bw=CORE_RATE_MBPS, delay="10ms", max_queue_size=1000)
        for name, meta in AGENCY_HOSTS.items():
            self.addLink(name, meta["switch"], port1=0, port2=meta["port"],
                         cls=TCLink, bw=ACCESS_RATE_MBPS, delay="5ms", max_queue_size=500)


topos = {"disaster": DisasterResponseTopo}
