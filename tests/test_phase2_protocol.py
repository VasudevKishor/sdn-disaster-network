"""Real Ryu process and three emulated OF1.3 peers; no OVS dataplane claim."""
import os
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
    from ryu.ofproto import ofproto_v1_3 as ofp
except ImportError as exc:
    raise unittest.SkipTest("Use the pinned Ryu Python: " + str(exc))
from config.phase2 import ENDPOINTS, SWITCHES, expected_ports
from controller.phase2_app import BASE_COOKIE, ROUTE_COOKIE
from tests.test_phase2_controller import arp_request


class Phase2WireTests(unittest.TestCase):
    def test_real_three_peer_port_validation_routing_arp_and_bond_mobility(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp) / "controller.log").open("w+") as logfile:
                process = subprocess.Popen(
                    [sys.executable, "-m", "ryu.cmd.manager", "--ofp-listen-host", "127.0.0.1",
                     "--ofp-tcp-listen-port", str(port), str(root / "controller/phase2_app.py")],
                    cwd=root, stdout=logfile, stderr=subprocess.STDOUT, env=os.environ.copy())
                peers, messages, described = {}, {name: [] for name in SWITCHES}, set()
                try:
                    deadline = time.monotonic() + 10
                    for name in SWITCHES:
                        while True:
                            self.assertIsNone(process.poll(), "Controller exited during startup")
                            try:
                                peer = socket.create_connection(("127.0.0.1", port), timeout=.1)
                                break
                            except OSError:
                                if time.monotonic() > deadline:
                                    self.fail("Controller startup timed out")
                                time.sleep(.05)
                        peer.settimeout(2)
                        peers[name] = peer

                    def send(name, kind, body=b"", xid=1):
                        peers[name].sendall(struct.pack("!BBHI", 4, kind, 8 + len(body), xid) + body)

                    def receive(name):
                        def exact(size):
                            result = b""
                            while len(result) < size:
                                chunk = peers[name].recv(size - len(result))
                                self.assertTrue(chunk, "Controller unexpectedly closed connection")
                                result += chunk
                            return result
                        version, kind, length, xid = struct.unpack("!BBHI", exact(8))
                        self.assertEqual(version, 4)
                        self.assertGreaterEqual(length, 8)
                        body = exact(length - 8)
                        messages[name].append((kind, body))
                        if kind == 2:
                            send(name, 3, body, xid)
                        elif kind == 5:
                            send(name, 6, struct.pack("!QIBB2xII", SWITCHES[name], 256, 1, 0, 0, 0), xid)
                        elif kind == 18 and body[:2] == b"\x00\x0d":
                            descriptions = b"".join(struct.pack(
                                ofp.OFP_PORT_PACK_STR, p, b"\x02\x00\x00\x00\x00\x01",
                                ("%s-eth%d" % (name, p)).encode(), 0, 0, 0, 0, 0, 0, 100000, 100000)
                                for p in sorted(expected_ports(name)))
                            send(name, 19, struct.pack("!HH4x", 13, 0) + descriptions, xid)
                            described.add(name)

                    def pump(predicate):
                        limit = time.monotonic() + 5
                        while not predicate():
                            self.assertIsNone(process.poll(), "Controller exited during wire check")
                            if time.monotonic() > limit:
                                self.fail("Timed out waiting for Phase 2 wire response")
                            readable, _, _ = select.select(list(peers.values()), [], [], .1)
                            for sock in readable:
                                name = next(n for n, p in peers.items() if p is sock)
                                receive(name)

                    def route_flows(name):
                        return [body for kind, body in messages[name] if kind == 14
                                and struct.unpack_from("!Q", body)[0] == ROUTE_COOKIE
                                and body[17] == ofp.OFPFC_ADD]

                    for name in peers:
                        send(name, 0)
                    pump(lambda: described == set(SWITCHES)
                         and all(route_flows(name) for name in ("p2s1", "p2s3")))
                    for name in peers:
                        self.assertTrue(any(kind == 14 and struct.unpack_from("!Q", body)[0] == BASE_COOKIE
                                            for kind, body in messages[name]))
                    decrement = struct.pack("!HH4x", ofp.OFPAT_DEC_NW_TTL, 8)
                    self.assertTrue(any(decrement in body for body in route_flows("p2s1")))

                    def packet_in(name, data):
                        match = struct.pack("!HHII4x", 1, 12, 0x80000004, 1)
                        send(name, 10, struct.pack("!IHBBQ", 0xffffffff, len(data), 0, 0, 0)
                             + match + b"\0\0" + data)

                    packet_in("p2s1", arp_request())
                    pump(lambda: any(kind == 13 for kind, _ in messages["p2s1"]))
                    reply = next(body for kind, body in messages["p2s1"] if kind == 13)
                    self.assertIn(socket.inet_aton("10.20.1.1"), reply)
                    for name in messages:
                        messages[name].clear()
                    packet_in("p2s2", arp_request(destination=ENDPOINTS["p2h1"].ip))
                    output_backup = struct.pack("!HHIH6x", 0, 16, 11, ofp.OFPCML_MAX)
                    dst_oxm = struct.pack("!HBB4s", 0x8000, 24, 4, socket.inet_aton(ENDPOINTS["p2h1"].ip))
                    pump(lambda: any(dst_oxm in body and output_backup in body for body in route_flows("p2s3")))
                finally:
                    for peer in peers.values():
                        peer.close()
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                logfile.flush()
                logfile.seek(0)
                logs = logfile.read()
                self.assertNotIn("Traceback", logs, logs)
                self.assertNotIn("OPENFLOW_ERROR", logs, logs)
                self.assertNotIn("PORT_INVENTORY_MISMATCH", logs, logs)


if __name__ == "__main__":
    unittest.main()
