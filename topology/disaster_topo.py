#!/usr/bin/env python3
"""
disaster_topo.py - Custom Mininet topology for National Disaster Response Network

Review 1: Foundational SDN infrastructure.
This topology models a disaster-response backbone with redundant switch paths
so later reviews can demonstrate failover and rerouting.

Topology layout (triangle / redundant core):

        s1 -------- s2
         |  \\     / |
         |    \\ /   |
         |     s3    |
         |    / | \\  |
    h1,h2,h7   |   h3,h4
               h5,h6

Agency host mapping (Review 1):
  h1 = CommandCenter      (10.0.0.1/24)  on s1
  h2 = Hospital           (10.0.0.2/24)  on s1  [high-priority traffic source]
  h3 = Police             (10.0.0.3/24)  on s2
  h4 = Ambulance          (10.0.0.4/24)  on s2  [port 5001 demo flows]
  h5 = RescueTeam         (10.0.0.5/24)  on s3  [location updates, port 6001]
  h6 = Drone              (10.0.0.6/24)  on s3  [video stream, port 9000]
  h7 = PublicHelpline     (10.0.0.7/24)  on s1  [low-priority bulk, port 8080]

Assumptions (justify in viva):
  - Core inter-switch links: 100 Mbps, 10 ms delay (regional backbone)
  - Host access links: 50 Mbps, 5 ms delay (last-mile connections)
  - IP subnet: 10.0.0.0/24 (single flat L3 segment for Review 1 simplicity)
"""

from mininet.topo import Topo
from mininet.link import TCLink


# ---------------------------------------------------------------------------
# Agency metadata used by the controller and demo scripts.
# Keeping this in one place makes later extensions (failover, attacks) easier.
# ---------------------------------------------------------------------------
AGENCY_HOSTS = {
    "h1": {"name": "CommandCenter", "ip": "10.0.0.1", "switch": "s1", "priority": "normal"},
    "h2": {"name": "Hospital", "ip": "10.0.0.2", "switch": "s1", "priority": "high"},
    "h3": {"name": "Police", "ip": "10.0.0.3", "switch": "s2", "priority": "normal"},
    "h4": {"name": "Ambulance", "ip": "10.0.0.4", "switch": "s2", "priority": "high"},
    "h5": {"name": "RescueTeam", "ip": "10.0.0.5", "switch": "s3", "priority": "high"},
    "h6": {"name": "Drone", "ip": "10.0.0.6", "switch": "s3", "priority": "medium"},
    "h7": {"name": "PublicHelpline", "ip": "10.0.0.7", "switch": "s1", "priority": "low"},
}

# Simulated application ports (used by flow rules and QoS mapping)
TRAFFIC_PORTS = {
    "ambulance_data": 5001,      # TCP - patient / vitals relay
    "hospital_critical": 5002,   # TCP - life-critical hospital data
    "rescue_location": 6001,     # UDP - small location updates
    "public_helpline": 8080,     # TCP - bulk citizen helpline traffic
    "drone_video": 9000,         # UDP - live aerial video
}


class DisasterResponseTopo(Topo):
    """Custom Mininet topology with redundant switch core."""

    def build(self, n=0, **_kwargs):
        # --- Switches (Open vSwitch, remote RYU controller) ---
        s1 = self.addSwitch("s1", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", protocols="OpenFlow13")
        s3 = self.addSwitch("s3", protocols="OpenFlow13")

        # --- Hosts (agency-labelled) ---
        h1 = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        h2 = self.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
        h3 = self.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")
        h4 = self.addHost("h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04")
        h5 = self.addHost("h5", ip="10.0.0.5/24", mac="00:00:00:00:00:05")
        h6 = self.addHost("h6", ip="10.0.0.6/24", mac="00:00:00:00:00:06")
        h7 = self.addHost("h7", ip="10.0.0.7/24", mac="00:00:00:00:00:07")

        # --- Redundant core links (triangle topology) ---
        # TCLink adds bandwidth caps and propagation delay for realistic measurement.
        self.addLink(
            s1, s2,
            cls=TCLink,
            bw=100, delay="10ms", max_queue_size=1000,
            params1={"port_name": "s1-s2"},
            params2={"port_name": "s2-s1"},
        )
        self.addLink(
            s2, s3,
            cls=TCLink,
            bw=100, delay="10ms", max_queue_size=1000,
            params1={"port_name": "s2-s3"},
            params2={"port_name": "s3-s2"},
        )
        self.addLink(
            s3, s1,
            cls=TCLink,
            bw=100, delay="10ms", max_queue_size=1000,
            params1={"port_name": "s3-s1"},
            params2={"port_name": "s1-s3"},
        )

        # --- Access links (host to switch) ---
        access_kw = dict(cls=TCLink, bw=50, delay="5ms", max_queue_size=500)
        self.addLink(h1, s1, **access_kw)
        self.addLink(h2, s1, **access_kw)
        self.addLink(h7, s1, **access_kw)
        self.addLink(h3, s2, **access_kw)
        self.addLink(h4, s2, **access_kw)
        self.addLink(h5, s3, **access_kw)
        self.addLink(h6, s3, **access_kw)


# Mininet CLI entry point:  sudo mn --custom disaster_topo.py --topo disaster
topos = {"disaster": DisasterResponseTopo}
