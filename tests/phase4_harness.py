"""Rehearse the same separate student commands and resets on the real lab."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time
from demo_scripts.model import DEMOS
from demo_scripts.session import demo_network
from policy.operator import atomic
from tests.lab_harness import ROOT,DEFAULT_RYU,require


def sequence(driver, demo):
    started=time.monotonic()
    def step(action,**options):
        print(demo+': '+action,flush=True)
        result=driver.execute({'demo':demo,'action':action,**options})
        return result
    step('setup')
    if demo=='congestion':
        step('measure'); step('inject'); step('measure'); step('inspect'); step('fix'); step('measure')
    elif demo=='routing':
        step('inject'); report=step('inspect')
        choice=next((c['candidate'] for c in report['candidates'] if c['capacity_feasible']),None)
        require(choice is not None,'No feasible route candidate'); step('fix',candidate=choice)
    elif demo=='onboarding':
        step('inject'); step('inspect'); step('measure'); step('register'); step('measure'); step('grant')
    else:
        step('inject'); step('inspect'); step('rollback')
    verification=step('verify'); reset=step('reset')
    require(reset['demo_verified'] and reset['pending'] is None,'Reset did not verify baseline')
    return {'demo':demo,'duration_sec':time.monotonic()-started,'verification':verification,'reset':reset,'passed':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--demo',choices=DEMOS); parser.add_argument('--ryu-python',default=str(DEFAULT_RYU)); args=parser.parse_args()
    if args.repetitions<1: parser.error('Positive repetitions required')
    if os.geteuid()!=0: parser.error('Use sudo python3 -m tests.phase4_harness --repetitions 3')
    selected=[args.demo] if args.demo else list(DEMOS)
    for repetition in range(args.repetitions):
        directory=ROOT/'monitoring/evidence/phase4-live'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        directory.mkdir(parents=True)
        atomic(directory/'manifest.json',{'repetition':repetition+1,'demos':selected,'scope':'focused demo diagnostic' if args.demo else 'four simplified demonstrations and verified resets',
                                         'rollback_scope':'whole-profile checkpoint, user-approved simplification'})
        try:
            results=[]
            with demo_network(directory,args.ryu_python) as driver:
                for demo in selected:
                    results.append(sequence(driver,demo)); atomic(directory/'results.json',results)
            marker='FOCUSED_PASS' if args.demo else 'PASS'
            (directory/marker).write_text('Selected demo sequences and resets passed; see manifest scope\n')
        except Exception as exc:
            (directory/'failure.txt').write_text(str(exc)); print('Failed: '+str(exc)+'; evidence: '+str(directory),file=sys.stderr); return 1
        print('Phase 4 '+('focused demo' if args.demo else 'repetition')+' passed: '+str(directory),flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
