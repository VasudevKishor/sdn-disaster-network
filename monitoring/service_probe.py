"""Fixed-size sequenced UDP/TCP echo workloads with raw receiver event evidence."""
import argparse
import json
from pathlib import Path
import secrets
import signal
import socket
import struct
import time

MAGIC=b'SDNP3'; SIZE=25; FIN=b'FINP3'+b'\0'*20


def exact(peer, size):
    data=b''
    while len(data)<size:
        part=peer.recv(size-len(data))
        if not part: return None
        data+=part
    return data


def serve(protocol, port, status):
    stopped=[False]
    signal.signal(signal.SIGTERM,lambda *a:stopped.__setitem__(0,True))
    signal.signal(signal.SIGINT,lambda *a:stopped.__setitem__(0,True))
    def log(event): print(json.dumps(event),flush=True)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM if protocol=='udp' else socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); server.bind(('0.0.0.0',port)); server.settimeout(.2)
        if protocol=='tcp': server.listen(8)
        status.write_text(json.dumps({'ready':True,'port':port,'protocol':protocol}))
        while not stopped[0]:
            try:
                if protocol=='udp':
                    payload,address=server.recvfrom(2048)
                    if len(payload)==SIZE and payload.startswith(MAGIC):
                        log({'peer':address,'request':payload.hex()}); server.sendto(payload,address)
                else:
                    connection,address=server.accept()
                    with connection:
                        connection.settimeout(.5)
                        while not stopped[0]:
                            payload=exact(connection,SIZE)
                            if payload is None or payload==FIN: break
                            if not payload.startswith(MAGIC): break
                            log({'peer':address,'request':payload.hex()}); connection.sendall(payload)
            except (socket.timeout,ConnectionError):
                continue
        status.write_text(json.dumps({'ready':False,'port':port,'protocol':protocol}))


def probe(protocol, source, target, client_port, server_port, count, timeout=.1, interval=.02):
    rows=[]; nonce=secrets.token_bytes(16); latencies=[]
    peer=None
    try:
        for number in range(count):
            payload=MAGIC+struct.pack('!I',number)+nonce
            started=time.monotonic(); row={'sequence':number,'request':payload.hex(),'sent':False,'reply':False}
            try:
                if peer is None:
                    peer=socket.socket(socket.AF_INET,socket.SOCK_DGRAM if protocol=='udp' else socket.SOCK_STREAM)
                    peer.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    peer.bind((source,client_port)); peer.settimeout(timeout)
                    peer.connect((target,server_port))
                if protocol=='udp': peer.send(payload); row['sent']=True; response=peer.recv(SIZE+1)
                else: peer.sendall(payload); row['sent']=True; response=exact(peer,SIZE)
                row['reply']=response==payload
                if row['reply']: latencies.append((time.monotonic()-started)*1000)
            except socket.timeout:
                row['timeout']=True
                if protocol=='tcp': peer.close(); peer=None
            except OSError as exc:
                row['error']={'errno':exc.errno,'message':str(exc)}
                if peer is not None: peer.close(); peer=None
            rows.append(row)
            time.sleep(interval)
        if protocol=='tcp' and peer is not None:
            peer.sendall(FIN)
            try: peer.recv(1)  # Server closes first: client fixed port can be reused.
            except socket.timeout: pass
    finally:
        if peer is not None: peer.close()
    latencies.sort()
    return {'attempts':count,'sent':sum(r['sent'] for r in rows),'received':sum(r['reply'] for r in rows),
            'errors':sum('error' in r for r in rows),'p95_ms':latencies[max(0,(95*len(latencies)+99)//100-1)] if latencies else None,'raw':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('server','client')); parser.add_argument('--protocol',choices=('tcp','udp'),required=True)
    parser.add_argument('--server-port',type=int,required=True); parser.add_argument('--client-port',type=int)
    parser.add_argument('--source'); parser.add_argument('--target'); parser.add_argument('--count',type=int,default=100)
    parser.add_argument('--status',type=Path); parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.mode=='server': serve(args.protocol,args.server_port,args.status)
    else:
        result=probe(args.protocol,args.source,args.target,args.client_port,args.server_port,args.count)
        args.output.write_text(json.dumps(result,indent=2))
        return 0


if __name__=='__main__': raise SystemExit(main())
