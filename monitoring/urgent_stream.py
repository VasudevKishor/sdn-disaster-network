"""Paced fixed-size medical echoes; raw offer/receiver/reply evidence, no QoS claims."""
import argparse
from collections import deque
import json
from pathlib import Path
import secrets
import select
import signal
import socket
import struct
import time

MAGIC=b'SDNURG04'; SIZE=8192


def message(sequence, nonce):
    return struct.pack('!8sQ16s',MAGIC,sequence,nonce)+b'\0'*(SIZE-32)


def event(kind, **fields):
    print(json.dumps({'event':kind,'monotonic':time.monotonic(),**fields}),flush=True)


def server(port, status, stopped):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('0.0.0.0',port)); listener.listen(2); listener.setblocking(False)
        status.write_text(json.dumps({'ready':True,'protocol':'tcp','port':port}))
        peer=None; incoming=bytearray(); outgoing=bytearray()
        try:
            while not stopped[0]:
                readable,writable,_=select.select([listener]+([peer] if peer else []),
                                                  [peer] if peer and outgoing else [],[],.02)
                if listener in readable:
                    new,address=listener.accept(); new.setblocking(False); new.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    if peer: peer.close()
                    peer=new; incoming.clear(); outgoing.clear()
                if peer in readable:
                    data=peer.recv(65536)
                    if not data: peer.close(); peer=None; continue
                    incoming.extend(data)
                    while len(incoming)>=SIZE:
                        payload=bytes(incoming[:SIZE]); del incoming[:SIZE]
                        magic,seq,nonce=struct.unpack_from('!8sQ16s',payload)
                        if magic!=MAGIC or payload!=message(seq,nonce): raise ValueError('Corrupt urgent request')
                        event('request',sequence=seq,nonce=nonce.hex()); outgoing.extend(payload)
                if peer in writable and outgoing:
                    try: sent=peer.send(outgoing); del outgoing[:sent]
                    except BlockingIOError: pass
        finally:
            if peer: peer.close()  # Server closes first: fixed client port is reusable.
            status.write_text(json.dumps({'ready':False,'port':port}))


def client(source,target,client_port,server_port,rate,stopped):
    if not 1<=rate<=1000: raise ValueError('Rate must be 1..1000 requests/sec')
    nonce=secrets.token_bytes(16)
    with socket.socket() as peer:
        peer.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        peer.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        peer.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,32768)
        peer.bind((source,client_port)); peer.settimeout(3); peer.connect((target,server_port)); peer.setblocking(False)
        pending=deque(); incoming=bytearray(); sequence=0; due=time.monotonic()
        event('started',source=source,target=target,client_port=client_port,server_port=server_port,rate=rate,
              payload_bytes=SIZE,nonce=nonce.hex())
        while not stopped[0]:
            now=time.monotonic()
            while now>=due:
                event('offer',sequence=sequence,scheduled=due,monotonic=due)
                if len(pending)<32: pending.append((message(sequence,nonce),0))
                else: event('backpressure',sequence=sequence)
                sequence+=1; due+=1/rate
            readable,writable,_=select.select([peer],[peer] if pending else [],[],max(0,min(.01,due-time.monotonic())))
            if peer in writable:
                data,offset=pending[0]
                try:
                    count=peer.send(data[offset:]); offset+=count
                    if offset==SIZE: pending.popleft()
                    else: pending[0]=(data,offset)
                except BlockingIOError: pass
            if peer in readable:
                data=peer.recv(65536)
                if not data: return
                incoming.extend(data)
                while len(incoming)>=SIZE:
                    payload=bytes(incoming[:SIZE]); del incoming[:SIZE]
                    magic,seq,token=struct.unpack_from('!8sQ16s',payload)
                    if magic!=MAGIC or token!=nonce or payload!=message(seq,nonce): raise ValueError('Corrupt urgent reply')
                    event('reply',sequence=seq)


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('mode',choices=('server','client'))
    parser.add_argument('--source'); parser.add_argument('--target'); parser.add_argument('--client-port',type=int)
    parser.add_argument('--server-port',type=int,required=True); parser.add_argument('--rate',type=int,default=250)
    parser.add_argument('--status',type=Path); args=parser.parse_args(); stopped=[False]
    for signum in (signal.SIGTERM,signal.SIGINT): signal.signal(signum,lambda *_:stopped.__setitem__(0,True))
    if args.mode=='server': server(args.server_port,args.status,stopped)
    else: client(args.source,args.target,args.client_port,args.server_port,args.rate,stopped)


if __name__=='__main__': main()
