#!/usr/bin/env python3
"""
run_network.py - Launch the disaster-response Mininet network.

Usage (from project root, as root):
  sudo python3 topology/run_network.py

Connects all OVS switches to RYU at 127.0.0.1:6633 (OpenFlow 1.3).
Start the controller first in a separate terminal:
  ryu-manager controller/base_switch_app.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

from disaster_topo import DisasterResponseTopo, AGENCY_HOSTS


def print_agency_map():
    info("\n=== Disaster Response Agency Host Map ===\n")
    for host, meta in sorted(AGENCY_HOSTS.items()):
        info(
            "  %-4s  %-18s  IP=%-12s  switch=%-3s  priority=%s\n"
            % (host, meta["name"], meta["ip"], meta["switch"], meta["priority"])
        )
    info("=========================================\n")


def run():
    setLogLevel("info")
    topo = DisasterResponseTopo()

    net = Mininet(
        topo=topo,
        controller=RemoteController("c0", ip="127.0.0.1", port=6633),
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True,
    )

    print_agency_map()
    info("*** Starting network\n")
    net.start()

    info("\n*** Controller check (run in another shell while network is up):\n")
    info("    ovs-vsctl show\n")
    info("    ovs-ofctl -O OpenFlow13 show s1\n\n")

    info("*** Running pingall for baseline connectivity evidence\n")
    net.pingAll()

    info("\n*** Mininet CLI ready. Type 'exit' to stop.\n")
    CLI(net)
    net.stop()


if __name__ == "__main__":
    run()
