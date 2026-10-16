"""Actual expanded Ryu process with twenty OF1.3 peers, no kernel dataplane."""
import os
import json
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
try:
    from ryu.ofproto import ofproto_v1_3 as ofp, ofproto_v1_3_parser as parser
    from ryu.lib.packet import packet, ethernet, arp
except ImportError as exc:
    raise unittest.SkipTest("Use pinned Ryu Python: " + str(exc))
from config.expanded import build_inventory
from controller.phase2_app import ROUTE_COOKIE, BASE_COOKIE, HEALTH_COOKIE


class ExpandedWireTests(unittest.TestCase):
    def test_real_controller_registers_and_configures_all_twenty_peers(self):
        inventory = build_inventory()
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp, (Path(temp) / "controller.log").open("w+") as logfile:
            # Observe completed callbacks so restoration does not accidentally
            # include DOWN events still queued from the preceding fault burst.
            # The subclass only records state after the real reconcile returns.
            observed=Path(temp)/'observed.json'; instrumented=Path(temp)/'observed_app.py'
            instrumented.write_text(
                'import json\nfrom pathlib import Path\n'
                'from controller.expanded_app import ExpandedPhase2App\n'
                'class ObservedExpandedApp(ExpandedPhase2App):\n'
                '    def reconcile(self):\n'
                '        super().reconcile()\n'
                '        path=Path('+repr(str(observed))+')\n'
                '        temporary=path.with_suffix(".tmp")\n'
                '        temporary.write_text(json.dumps({"up":sorted(self.state.up),"online":sorted(self.state.online)}))\n'
                '        temporary.replace(path)\n')
            process = subprocess.Popen([sys.executable, "-m", "ryu.cmd.manager", "--ofp-listen-host", "127.0.0.1",
                                        "--ofp-tcp-listen-port", str(port), str(instrumented)],
                                       cwd=root, env=dict(os.environ, SDN_PHASE2_DISTRICTS="2"), stdout=logfile, stderr=subprocess.STDOUT)
            peers, described, misses, routing, health = {}, set(), set(), set(), set()
            tables, arp_replies = {name: {} for name in inventory.SWITCHES}, []
            try:
                deadline = time.monotonic() + 10
                for name in inventory.SWITCHES:
                    while True:
                        self.assertIsNone(process.poll(), "Expanded controller exited during startup")
                        try:
                            peer = socket.create_connection(("127.0.0.1", port), timeout=.1)
                            break
                        except OSError:
                            self.assertLess(time.monotonic(), deadline, "Expanded startup timed out")
                            time.sleep(.05)
                    peer.settimeout(5)
                    peer.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    peers[name] = peer
                    peer.sendall(struct.pack("!BBHI", 4, 0, 8, 1))
                def send(name, kind, body, xid):
                    peers[name].sendall(struct.pack("!BBHI", 4, kind, 8 + len(body), xid) + body)
                buffers = {name: bytearray() for name in peers}
                def handle(name, version, kind, length, xid, body):
                    self.assertEqual(version, 4)
                    if kind == ofp.OFPT_ECHO_REQUEST:
                        send(name, ofp.OFPT_ECHO_REPLY, body, xid)
                    elif kind == ofp.OFPT_FEATURES_REQUEST:
                        send(name, ofp.OFPT_FEATURES_REPLY,
                             struct.pack("!QIBB2xII", inventory.SWITCHES[name], 256, 1, 0, 0, 0), xid)
                    elif kind == ofp.OFPT_MULTIPART_REQUEST and body[:2] == b"\x00\x0d":
                        ports = b"".join(struct.pack(ofp.OFP_PORT_PACK_STR, p, b"\x02\x00\x00\x00\x00\x01",
                            ("%s-eth%d" % (name, p)).encode(), 0, 0, 0, 0, 0, 0, 100000, 100000)
                            for p in sorted(inventory.expected_ports(name)))
                        send(name, ofp.OFPT_MULTIPART_REPLY, struct.pack("!HH4x", 13, 0) + ports, xid)
                        described.add(name)
                    elif kind == ofp.OFPT_PACKET_OUT:
                        arp_replies.append((name, body))
                    elif kind == ofp.OFPT_FLOW_MOD:
                        cookie = struct.unpack_from("!Q", body)[0]
                        priority = struct.unpack_from("!H", body, 22)[0]
                        match = parser.OFPMatch.parser(body, 40)
                        key = (priority, tuple(sorted(match.items())))
                        if body[17] == ofp.OFPFC_ADD:
                            tables[name][key] = (cookie, match)
                        elif body[17] == ofp.OFPFC_DELETE_STRICT:
                            tables[name].pop(key, None)
                        if body[17] != ofp.OFPFC_ADD:
                            return
                        if cookie == BASE_COOKIE and priority == 0:
                            misses.add(name)
                        elif cookie == ROUTE_COOKIE:
                            routing.add(name)
                        elif cookie == HEALTH_COOKIE and priority == 200:
                            health.add(name)
                def receive(name):
                    chunk=peers[name].recv(1024*1024)
                    self.assertTrue(chunk,"Expanded controller disconnected")
                    buffer=buffers[name]; buffer.extend(chunk)
                    while len(buffer)>=8:
                        version,kind,length,xid=struct.unpack_from("!BBHI",buffer)
                        self.assertGreaterEqual(length,8)
                        if len(buffer)<length: break
                        body=bytes(buffer[8:length]); del buffer[:length]
                        handle(name,version,kind,length,xid,body)
                def pump(predicate, timeout=30):
                    deadline = time.monotonic() + timeout
                    while not predicate():
                        self.assertIsNone(process.poll(), "Expanded controller exited during wire check")
                        self.assertLess(time.monotonic(), deadline, "Expanded wire configuration/recovery timed out")
                        readable, _, _ = select.select(list(peers.values()), [], [], .1)
                        for peer in readable:
                            name = next(n for n, p in peers.items() if p is peer)
                            receive(name)
                    self.assertLessEqual(time.monotonic(),deadline,"Expanded wire recovery completed after its deadline")
                pump(lambda: described == set(peers) and misses == set(peers)
                     and {"bb1", "bb3"}.issubset(routing) and {"acc2", "bcc2"}.issubset(health))

                def cross_rules():
                    return sum(cookie == ROUTE_COOKIE and match.get("ipv4_src") == inventory.ENDPOINTS["ac1"].ip
                               and match.get("ipv4_dst") == inventory.ENDPOINTS["bf2"].ip and match.get("ip_proto") == 1
                               for table in tables.values() for cookie, match in table.values())
                pump(lambda: cross_rules() >= 6)
                links = [l for l in inventory.LINKS if l.a.startswith("bb") and l.b.startswith("bb")
                         and (int(l.a[-1]) <= 2) != (int(l.b[-1]) <= 2)]
                pump(lambda:observed.exists() and set(json.loads(observed.read_text())['online'])==set(peers))
                def link_events(down):
                    for link in links:
                        for name, number in ((link.a, link.pa), (link.b, link.pb)):
                            desc = struct.pack(ofp.OFP_PORT_PACK_STR, number, b"\x02\x00\x00\x00\x00\x01",
                                               ("%s-eth%d" % (name, number)).encode(), 0,
                                               ofp.OFPPS_LINK_DOWN if down else 0, 0, 0, 0, 0, 100000, 100000)
                            send(name, ofp.OFPT_PORT_STATUS, struct.pack("!B7x", ofp.OFPPR_MODIFY) + desc, 50)
                link_events(True)
                down_ports={(link.a,link.pa) for link in links}|{(link.b,link.pb) for link in links}
                def fault_applied():
                    if not observed.exists(): return False
                    up={tuple(p) for p in json.loads(observed.read_text())['up']}
                    return not (down_ports & up) and cross_rules()==0
                pump(fault_applied)
                started = time.monotonic()
                link_events(False)
                # Let all peer receive loops enqueue the restoration burst
                # before the app's gateway request, while continuing to drain
                # all controller-to-peer flow updates below.
                time.sleep(.05)
                src = inventory.ENDPOINTS["ah1"]
                gateway = inventory.SITES[src.site]
                frame = packet.Packet()
                frame.add_protocol(ethernet.ethernet(src=src.mac, dst="ff:ff:ff:ff:ff:ff", ethertype=0x0806))
                frame.add_protocol(arp.arp(opcode=arp.ARP_REQUEST, src_mac=src.mac, src_ip=src.ip, dst_ip=gateway.gateway))
                frame.serialize()
                match = struct.pack("!HHII4x", 1, 12, 0x80000004, 1)
                send("ahc1", ofp.OFPT_PACKET_IN, struct.pack("!IHBBQ", 0xffffffff, len(frame.data), 0, 0, 0)
                     + match + b"\0\0" + frame.data, 51)
                pump(lambda: cross_rules() >= 6 and any(name == "ahc1" and socket.inet_aton(gateway.gateway) in body
                                                       for name, body in arp_replies), timeout=5 - (time.monotonic() - started))
                print("Expanded restoration burst + gateway ARP: %.3f sec (emulated peers)" % (time.monotonic() - started))
            finally:
                for peer in peers.values():
                    peer.close()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            logfile.flush()
            logfile.seek(0)
            log = logfile.read()
            self.assertNotIn("Traceback", log, log)
            self.assertNotIn("PORT_INVENTORY_MISMATCH", log, log)
            self.assertNotIn("OPENFLOW_ERROR", log, log)


if __name__ == "__main__":
    unittest.main()
