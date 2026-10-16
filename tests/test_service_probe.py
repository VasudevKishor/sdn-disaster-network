import json
import shutil
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from monitoring.service_probe import probe, MAGIC
from monitoring.service_capture import requests, wait_for_requests
from performance.run_tests import stop_process, parse_iperf_throughput_mbps
from tests.test_expanded_evidence import pcap


class ServiceProbeTests(unittest.TestCase):
    def test_real_udp_iperf_supports_fixed_source_port_and_receiver_rate(self):
        if not shutil.which('iperf') or not shutil.which('ss'): self.skipTest('iperf/ss missing')
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as reserved:
            reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
        server=subprocess.Popen(['iperf','-s','-u','-p',str(port),'-f','m'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+3
            while not subprocess.check_output(['ss','-H','-lun','sport','=',':%d'%port],text=True).strip():
                self.assertIsNone(server.poll()); self.assertLess(time.monotonic(),deadline); time.sleep(.02)
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as reserved:
                reserved.bind(('127.0.0.1',0)); client=reserved.getsockname()[1]
            result=subprocess.run(['iperf','-u','-c','127.0.0.1','-p',str(port),'-B','127.0.0.1:'+str(client),
                '-b','10m','-t','2','-f','m'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
        finally: stop_process(server)
        out,err=server.communicate(timeout=3)
        self.assertIn('port '+str(client),out)
        self.assertGreater(parse_iperf_throughput_mbps(out) or 0,0)

    def test_real_tcp_and_udp_echo_and_repeat_fixed_client_port(self):
        for protocol in ('tcp','udp'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as temp:
                with socket.socket(socket.AF_INET,socket.SOCK_STREAM if protocol=='tcp' else socket.SOCK_DGRAM) as reserved:
                    reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
                with socket.socket() as reserved:
                    reserved.bind(('127.0.0.1',0)); client=reserved.getsockname()[1]
                status=Path(temp)/'ready.json'
                with (Path(temp)/'server.log').open('w') as log:
                    process=subprocess.Popen([sys.executable,'-m','monitoring.service_probe','server','--protocol',protocol,
                        '--server-port',str(port),'--status',str(status)],stdout=log,stderr=log)
                    try:
                        deadline=time.monotonic()+3
                        while not status.exists():
                            self.assertIsNone(process.poll()); self.assertLess(time.monotonic(),deadline); time.sleep(.01)
                        for _ in range(2):
                            result=probe(protocol,'127.0.0.1','127.0.0.1',client,port,10,interval=.005)
                            self.assertEqual((result['sent'],result['received'],result['errors']),(10,10,0))
                    finally: stop_process(process)
                self.assertEqual(process.returncode,0)
                events=[json.loads(l) for l in (Path(temp)/'server.log').read_text().splitlines()]
                self.assertEqual(len(events),20)
                self.assertEqual(len({e['request'] for e in events}),20)

    def test_closed_udp_port_is_local_error_not_successful_policy_denial(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as reserved:
            reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
        result=probe('udp','127.0.0.1','127.0.0.1',0,port,2,interval=0)
        self.assertEqual(result['received'],0); self.assertGreater(result['errors'],0)

    def test_receiver_capture_detects_payload_duplicates_and_forbidden_syn(self):
        source,target='10.30.3.10','10.30.1.10'
        def frame(protocol,segment):
            ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(segment),0,0,63,protocol,0,socket.inet_aton(source),socket.inet_aton(target))
            return b'\0'*12+b'\x08\x00'+ip+segment
        payload=MAGIC+struct.pack('!I',1)+b'x'*16
        udp=struct.pack('!HHHH',19001,9001,8+len(payload),0)+payload
        tcp=struct.pack('!HHIIBBHHH',19001,9001,0,0,5<<4,2,1000,0,0)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'data.pcap'; pcap(path,[frame(17,udp)]*2)
            result=requests(path,source,target,'udp',19001,9001)
            self.assertEqual((result['requests'],result['duplicates']),(2,1))
            pcap(path,[frame(6,tcp)])
            self.assertEqual(requests(path,source,target,'tcp',19001,9001)['tcp_syns'],1)
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError): requests(path,source,target,'tcp',19001,9001)

    def test_receiver_drain_waits_for_buffered_tcp_tail_and_partial_record(self):
        source,target='10.30.3.10','10.30.1.10'
        frames=[]
        for sequence in range(100):
            payload=MAGIC+struct.pack('!I',sequence)+b'x'*16
            segment=struct.pack('!HHIIBBHHH',19005,9005,sequence*25,0,5<<4,24,1000,0,0)+payload
            ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(segment),0,0,63,6,0,
                           socket.inet_aton(source),socket.inet_aton(target))
            frames.append(b'\0'*12+b'\x08\x00'+ip+segment)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'buffered.pcap'; pcap(path,frames[:80])
            self.assertEqual(requests(path,source,target,'tcp',19005,9005)['requests'],80)
            tail=b''.join(struct.pack('<IIII',0,0,len(f),len(f))+f for f in frames[80:])
            def flush():
                time.sleep(.05)
                with path.open('ab',buffering=0) as stream:
                    stream.write(tail[:10])  # A record header being written.
                    time.sleep(.05); stream.write(tail[10:])
            writer=threading.Thread(target=flush); writer.start()
            try:
                self.assertTrue(wait_for_requests(path,source,target,'tcp',19005,9005,100,timeout=1))
            finally: writer.join()
            result=requests(path,source,target,'tcp',19005,9005)
            self.assertEqual((result['requests'],result['duplicates']),(100,0))
            # Waiting must not synthesize missing packets or accept duplicates.
            pcap(path,frames[:80])
            self.assertFalse(wait_for_requests(path,source,target,'tcp',19005,9005,100,timeout=.05))
            self.assertEqual(requests(path,source,target,'tcp',19005,9005)['requests'],80)
            pcap(path,frames+[frames[-1]])
            self.assertTrue(wait_for_requests(path,source,target,'tcp',19005,9005,100,timeout=.05))
            self.assertEqual(requests(path,source,target,'tcp',19005,9005)['duplicates'],1)


if __name__=='__main__': unittest.main()
