"""Start the actual pinned controller and emulate one OpenFlow 1.3 peer.

No OVS/Mininet/root. This tests launch and wire dispatch, not dataplane forwarding.
"""
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
try:
    import ryu
except ImportError as exc:
    raise unittest.SkipTest("Run with the pinned Ryu Python: " + str(exc))
from tests.test_controller import arp_frame, frame
from controller.flow_manager_app import FLOW_COOKIE


class ProtocolSmokeTests(unittest.TestCase):
    def test_real_controller_handshake_proxy_arp_and_classified_flow(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp) / "controller.log").open("w+") as logfile:
                process = subprocess.Popen(
                    [sys.executable, "-m", "ryu.cmd.manager", "--ofp-listen-host", "127.0.0.1",
                     "--ofp-tcp-listen-port", str(port), str(root / "controller/flow_manager_app.py")],
                    env=dict(os.environ, DISASTER_QOS="1"), stdout=logfile, stderr=subprocess.STDOUT, cwd=root)
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        if process.poll() is not None:
                            logfile.seek(0)
                            self.fail("Controller launch failed: " + logfile.read())
                        try:
                            peer = socket.create_connection(("127.0.0.1", port), timeout=.1)
                            break
                        except OSError:
                            if time.monotonic() > deadline:
                                self.fail("Controller startup timed out")
                            time.sleep(.05)
                    with peer:
                        peer.settimeout(3)
                        def send(kind, body=b"", xid=1):
                            peer.sendall(struct.pack("!BBHI", 4, kind, 8 + len(body), xid) + body)
                        def receive():
                            def exact(size):
                                data = b""
                                while len(data) < size:
                                    part = peer.recv(size - len(data))
                                    if not part:
                                        self.fail("Controller closed connection")
                                    data += part
                                return data
                            header = exact(8)
                            version, kind, size, xid = struct.unpack("!BBHI", header)
                            self.assertEqual(version, 4)
                            body = exact(size - 8)
                            if kind == 2:
                                send(3, body, xid)
                            if kind == 18 and body[:2] == b"\x00\x0d":
                                send(19, struct.pack("!HH4x", 13, 0), xid)
                            return kind, body
                        send(0)
                        for _ in range(30):
                            kind, body = receive()
                            if kind == 5:
                                send(6, struct.pack("!QIBB2xII", 1, 256, 1, 0, 0, 0))
                            if kind == 14 and body[17] == 0 and struct.unpack_from("!H", body, 22)[0] == 0:
                                self.assertEqual(struct.unpack_from("!Q", body)[0], FLOW_COOKIE)
                                break
                        else:
                            self.fail("No table-miss installed after handshake")
                        def packet_in(data):
                            match = struct.pack("!HHII4x", 1, 12, 0x80000004, 4)
                            send(10, struct.pack("!IHBBQ", 0xffffffff, len(data), 0, 0, 0)
                                 + match + b"\x00\x00" + data)
                        packet_in(arp_frame())
                        for _ in range(30):
                            kind, body = receive()
                            if kind == 13:
                                self.assertIn(bytes.fromhex("000000000004"), body)
                                break
                        else:
                            self.fail("No proxy ARP packet-out")
                        packet_in(frame(dst_port=8080))
                        for _ in range(30):
                            kind, body = receive()
                            if kind == 14:
                                self.assertEqual(struct.unpack_from("!Q", body)[0], FLOW_COOKIE)
                                # SET_QUEUE type=21, len=8, queue=1 in APPLY_ACTIONS.
                                self.assertIn(struct.pack("!HHI", 21, 8, 1), body)
                                break
                        else:
                            self.fail("No classified IPv4 flow after packet-in")
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
