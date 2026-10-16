"""Read classic Ethernet pcap evidence; reject truncated or unsupported captures."""
from collections import Counter
import socket
import struct


def capture_counts(path):
    echoes, arps = Counter(), Counter()
    with path.open("rb") as stream:
        header = stream.read(24)
        formats = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">",
                   b"\x4d\x3c\xb2\xa1": "<", b"\xa1\xb2\x3c\x4d": ">"}
        if len(header) != 24 or header[:4] not in formats:
            raise ValueError("Invalid classic pcap header: " + str(path))
        endian = formats[header[:4]]
        if struct.unpack_from(endian + "I", header, 20)[0] != 1:
            raise ValueError("Expected Ethernet capture: " + str(path))
        while True:
            record = stream.read(16)
            if not record:
                break
            if len(record) != 16:
                raise ValueError("Truncated pcap record")
            _sec, _fraction, size, original = struct.unpack(endian + "IIII", record)
            if size > 65535 or size != original:
                raise ValueError("Truncated/oversized packet capture")
            frame = stream.read(size)
            if len(frame) != size or size < 14:
                raise ValueError("Truncated Ethernet packet")
            if frame[12:14] == b"\x08\x06":
                if len(frame) < 42:
                    raise ValueError("Truncated ARP packet")
                if frame[14:22] == struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1):
                    arps[socket.inet_ntoa(frame[28:32]), socket.inet_ntoa(frame[38:42])] += 1
            elif frame[12:14] == b"\x08\x00":
                ip = frame[14:]
                if len(ip) < 20 or ip[0] >> 4 != 4:
                    raise ValueError("Invalid captured IPv4 packet")
                ihl = (ip[0] & 15) * 4
                total = struct.unpack_from("!H", ip, 2)[0]
                if ihl < 20 or total > len(ip) or total < ihl:
                    raise ValueError("Truncated IPv4 packet")
                if ip[9] == 1 and len(ip) >= ihl + 8 and ip[ihl] == 8:
                    identifier, sequence = struct.unpack_from("!HH", ip, ihl + 4)
                    echoes[socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20]), identifier, sequence] += 1
    return echoes, arps
