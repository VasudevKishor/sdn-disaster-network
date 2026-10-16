"""One owned lab for the four student-operated demos; local root-only commands."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socketserver
import sys
from config.expanded import build_inventory
from config.incident import initial_config
from demo_scripts.engine import DemoLab
from policy.operator import Manager, topology, atomic
from tests.lab_harness import ROOT, DEFAULT_RYU, require
from tests.expanded_harness import lab, health_processes
from tests.phase3_harness import servers, wait

MAX_REQUEST=8192


@contextmanager
def demo_network(directory, ryu_python=str(DEFAULT_RYU)):
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
    state=directory/'state'; state.mkdir()
    inv=build_inventory(); driver=None
    with lab(inv,ryu_python,directory,controller_app='policy_app.py',environment={'SDN_PHASE3_DIR':str(state)}) as (net,controller):
        wait(lambda:(state/'graph.json').exists() and len(topology(state,inv).online)==20,label='Demo controller topology')
        manager=Manager(state,inv)
        # Persist the ownership token before action handlers construct another
        # Manager, so setup and final queue cleanup share exactly one owner.
        manager.save()
        try:
            with health_processes(net,inv,directory),servers(net,inv,directory) as responders:
                driver=DemoLab(net,inv,directory,responders); driver.apply(initial_config(inv)); driver.start_watch()
                try: yield driver
                finally:
                    errors=driver.close(); require(not errors,'Demo reset/process cleanup failed: '+str(errors))
        finally:
            errors=[]
            for switch in inv.SWITCHES:
                try: manager.backend.write(switch,[])
                except Exception as exc: errors.append(switch+': '+str(exc))
            manager.backend.cleanup(); atomic(directory/'policy_cleanup.json',{'errors':errors})
            require(not errors,'Demo policy cleanup failed')
    log=(directory/'controller.log').read_text()
    require(not any(s in log for s in ('Traceback','OPENFLOW_ERROR','PORT_INVENTORY_MISMATCH')),'Demo controller error')


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            raw=self.rfile.readline(MAX_REQUEST+1)
            if len(raw)>MAX_REQUEST or not raw.endswith(b'\n'): raise ValueError('Invalid/oversized command')
            request=json.loads(raw)
            if not isinstance(request,dict): raise ValueError('Command must be an object')
            if request=={'demo':'session','action':'stop'}:
                self.server.stopped=True; result={'status':'session stopping'}
            elif request=={'demo':'session','action':'status'}:
                result={'active':self.server.driver.active,'state':self.server.driver.state,'directory':str(self.server.driver.directory)}
            else: result=self.server.driver.execute(request)
            response={'ok':True,'result':result}
        except Exception as exc: response={'ok':False,'error':str(exc)}
        try: self.wfile.write(json.dumps(response).encode()+b'\n')
        except (BrokenPipeError,ConnectionResetError): pass


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--ryu-python',default=str(DEFAULT_RYU)); args=parser.parse_args()
    if os.geteuid()!=0: parser.error('Use sudo python3 -m demo_scripts.session')
    directory=(args.state_dir or ROOT/'monitoring/evidence/phase4-sessions'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    if directory.exists() and any(directory.iterdir()): parser.error('Choose an empty session directory; prior evidence is preserved')
    directory.mkdir(parents=True,exist_ok=True); stopped=[False]
    for signum in (signal.SIGINT,signal.SIGTERM): signal.signal(signum,lambda *_:stopped.__setitem__(0,True))
    path=directory/'demo.sock'
    if len(os.fsencode(path))>107: parser.error('Session socket path is too long; choose a shorter --state-dir such as /tmp/sdn-demo')
    try:
        with demo_network(directory,args.ryu_python) as driver:
            with socketserver.UnixStreamServer(str(path),Handler) as server:
                os.chmod(path,0o600); server.timeout=.5; server.driver=driver; server.stopped=False
                atomic(directory/'READY.json',{'directory':str(directory),'socket':str(path),'scope':'Phase 4 manual demos; not a live validation result'})
                print('\nDemo session ready: '+str(directory),flush=True)
                print('In another terminal: export DIR='+str(directory),flush=True)
                print('Then: sudo python3 -m demo_scripts.control --state-dir "$DIR" congestion setup',flush=True)
                print('Stop: sudo python3 -m demo_scripts.control --state-dir "$DIR" session stop',flush=True)
                while not stopped[0] and not server.stopped: server.handle_request()
        (directory/'STOPPED').write_text('Owned demo session cleaned up\n')
        return 0
    except Exception as exc:
        (directory/'failure.txt').write_text(str(exc)); print('Session failed: '+str(exc)+'; evidence: '+str(directory),file=sys.stderr); return 1
    finally:
        if path.exists(): path.unlink()


if __name__=='__main__': raise SystemExit(main())
