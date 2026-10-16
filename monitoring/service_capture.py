"""Read receiver-side IPv4 request evidence from classic Ethernet pcap."""
from collections import Counter
import socket
import struct
import time
from monitoring.service_probe import MAGIC, SIZE


def requests(path, source, target, protocol, client_port, server_port):
    payloads=Counter(); syns=0
    with path.open('rb') as stream:
        header=stream.read(24)
        formats={b'\xd4\xc3\xb2\xa1':'<',b'\xa1\xb2\xc3\xd4':'>',b'\x4d\x3c\xb2\xa1':'<',b'\xa1\xb2\x3c\x4d':'>'}
        if len(header)!=24 or header[:4] not in formats: raise ValueError('Invalid pcap header')
        endian=formats[header[:4]]
        if struct.unpack_from(endian+'I',header,20)[0]!=1: raise ValueError('Expected Ethernet pcap')
        while True:
            record=stream.read(16)
            if not record: break
            if len(record)!=16: raise ValueError('Truncated capture record')
            _,_,size,original=struct.unpack(endian+'IIII',record)
            if size!=original or size>65535: raise ValueError('Truncated/oversized captured packet')
            frame=stream.read(size)
            if len(frame)!=size or size<14: raise ValueError('Truncated frame')
            if frame[12:14]!=b'\x08\x00': continue
            ip=frame[14:]
            if len(ip)<20: raise ValueError('Short IPv4 packet')
            ihl=(ip[0]&15)*4
            if ihl<20 or len(ip)<ihl+8: raise ValueError('Short IPv4 packet')
            total=struct.unpack_from('!H',ip,2)[0]
            if total>len(ip) or total<ihl+8: raise ValueError('Truncated IP payload')
            if socket.inet_ntoa(ip[12:16])!=source or socket.inet_ntoa(ip[16:20])!=target: continue
            segment=ip[ihl:total]
            if ip[9]!=(17 if protocol=='udp' else 6): continue
            if struct.unpack_from('!HH',segment)!=(client_port,server_port): continue
            if protocol=='udp': data=segment[8:]
            else:
                if len(segment)<20: raise ValueError('Short TCP header')
                offset=(segment[12]>>4)*4
                if offset<20 or offset>len(segment): raise ValueError('Invalid TCP data offset')
                syns+=bool(segment[13]&2); data=segment[offset:]
            if data.startswith(MAGIC):
                if len(data)!=SIZE: raise ValueError('Unexpected echo message framing')
                payloads[data.hex()]+=1
    return {'requests':sum(payloads.values()),'duplicates':sum(max(0,n-1) for n in payloads.values()),'tcp_syns':syns}


def wait_for_requests(path, source, target, protocol, client_port, server_port, expected, timeout=2):
    """Allow a running packet-buffered writer to flush delivered requests.

    This is a bounded drain, not an acceptance check. The caller must stop the
    writer and parse the final file strictly, including duplicates and loss.
    A record can be temporarily incomplete while tcpdump is writing it.
    """
    deadline=time.monotonic()+timeout
    while True:
        try:
            if requests(path,source,target,protocol,client_port,server_port)['requests']>=expected:
                return True
        except (OSError,ValueError):
            pass
        remaining=deadline-time.monotonic()
        if remaining<=0: return False
        time.sleep(min(.02,remaining))
