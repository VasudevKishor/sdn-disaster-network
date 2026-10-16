"""Separate local commands: setup, inject, inspect, correct, verify and reset."""
import argparse
import json
from pathlib import Path
import socket
import sys
from demo_scripts.model import DEMOS

ACTIONS=('setup','inject','inspect','measure','fix','register','grant','rollback','verify','reset','status','stop')


def request(directory, command):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
        peer.settimeout(180); peer.connect(str(directory/'demo.sock'))
        peer.sendall(json.dumps(command).encode()+b'\n'); response=bytearray()
        while not response.endswith(b'\n'):
            data=peer.recv(65536)
            if not data: raise RuntimeError('Demo session closed without a complete response; inspect commands.jsonl')
            response.extend(data)
            if len(response)>16*1024*1024: raise RuntimeError('Oversized demo response')
        return json.loads(response)


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('demo',choices=(*DEMOS,'session')); parser.add_argument('action',choices=ACTIONS)
    parser.add_argument('--candidate',type=int); args=parser.parse_args()
    command={'demo':args.demo,'action':args.action}
    if args.candidate is not None: command['candidate']=args.candidate
    try:
        result=request(args.state_dir.resolve(),command); print(json.dumps(result,indent=2)); return 0 if result['ok'] else 1
    except (OSError,ValueError,RuntimeError) as exc: print('Demo command failed: '+str(exc),file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
