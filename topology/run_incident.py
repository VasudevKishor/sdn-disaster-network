"""Interactive Phase 3 session with a normal baseline and local operator watcher."""
import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from pathlib import Path
from config.expanded import build_inventory
from config.incident import initial_config
from policy.operator import Manager, topology
from policy.processes import stop_group
from tests.lab_harness import ROOT, DEFAULT_RYU
from tests.expanded_harness import lab, health_processes
from tests.phase3_harness import wait, servers


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path); parser.add_argument('--ryu-python',default=str(DEFAULT_RYU))
    args=parser.parse_args()
    if os.geteuid()!=0: parser.error('Use sudo python3 -m topology.run_incident')
    directory=(args.state_dir or ROOT/'monitoring/evidence/phase3-sessions'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    if directory.exists() and any(directory.iterdir()): parser.error('Choose an empty session directory; existing work is preserved')
    directory.mkdir(parents=True,exist_ok=True); inv=build_inventory(); watcher=None; manager=None
    from mininet.cli import CLI
    try:
        with lab(inv,args.ryu_python,directory,controller_app='policy_app.py',environment={'SDN_PHASE3_DIR':str(directory)}) as (net,controller):
            wait(lambda:(directory/'graph.json').exists() and len(topology(directory,inv).online)==20)
            manager=Manager(directory,inv)
            try:
                with health_processes(net,inv,directory), servers(net,inv,directory):
                    manager.stage(initial_config(inv),topology(directory,inv)); result=manager.resume()
                    if result['status']!='verified': raise RuntimeError('Initial normal profile did not verify')
                    with (directory/'watch.log').open('w') as log:
                        watcher=subprocess.Popen([sys.executable,'-m','policy.operator','--state-dir',str(directory),'watch'],cwd=ROOT,
                                                 stdout=log,stderr=log,start_new_session=True)
                        print('\nSession: '+str(directory))
                        print('Operator: sudo python3 -m policy.operator --state-dir '+str(directory)+' status\n')
                        try: CLI(net)
                        finally: stop_group(watcher); watcher=None
            finally:
                if watcher is not None: stop_group(watcher)
                manager.backend.cleanup()  # Owned queues only; net.stop removes all owned bridges/flows next.
    except Exception as exc:
        (directory/'failure.txt').write_text(str(exc)); print('Session failed: '+str(exc),file=sys.stderr); return 1
    print('Session stopped; evidence: '+str(directory)); return 0


if __name__=='__main__': raise SystemExit(main())
