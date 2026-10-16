"""Send ten unknown-target ARP requests from a configured guest namespace."""
import argparse
import socket
import struct
import time
from config.phase2 import load_inventory
from monitoring.path_monitor import mac_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="as3")
    parser.add_argument("--districts", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    inventory = load_inventory("expanded", args.districts)
    endpoint = inventory.ENDPOINTS[args.endpoint]
    if inventory.SITE_ROLES[endpoint.site] != "guest":
        parser.error("Broadcast test requires a configured guest")
    target = endpoint.ip.rsplit(".", 1)[0] + ".250"
    mac = mac_bytes(endpoint.mac)
    frame = (b"\xff" * 6 + mac + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
             + mac + socket.inet_aton(endpoint.ip) + b"\0" * 6 + socket.inet_aton(target))
    interface = "%s-eth%d" % (args.endpoint, endpoint.attachments[0].host_port)
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806)) as peer:
        peer.bind((interface, 0x0806))
        for _ in range(10):
            peer.send(frame)
            time.sleep(.02)
    print("10 unknown-target ARP requests sent to " + target)


if __name__ == "__main__":
    main()
