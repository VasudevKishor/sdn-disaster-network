"""Actual isolated OVSDB + userspace dummy switch, without host network changes.

Verifies OF1.3 replacement/readback and OVSDB ownership. Dummy ports do not
validate kernel HTB rate enforcement or the Mininet dataplane.
"""
import os
from pathlib import Path
import shutil
import json
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from config.expanded import build_inventory
from policy import backend
from policy.model import flow
from performance.run_tests import stop_process
from tests.lab_harness import ROOT, DEFAULT_RYU
from policy.operator import Manager, topology
from config.incident import initial_config, PROFILES
from tests.phase3_harness import isolated_controller


class RealOVSTests(unittest.TestCase):
    def test_actual_of13_apply_verify_change_rollback_and_owned_queue_cleanup(self):
        for tool in ('ovsdb-tool','ovsdb-server','ovs-vswitchd','ovs-vsctl','ovs-ofctl'):
            if not shutil.which(tool): self.skipTest('Missing '+tool)
        with tempfile.TemporaryDirectory(prefix='p3-ovs-') as directory:
            p=Path(directory); env=dict(os.environ,OVS_RUNDIR=directory)
            processes=[]
            def run(args):
                r=subprocess.run(args,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(r.returncode,0,r.stderr); return r.stdout.strip()
            db=['ovs-vsctl','--db=unix:'+str(p/'db.sock'),'--timeout=5']
            run(['ovsdb-tool','create',str(p/'conf.db'),'/usr/share/openvswitch/vswitch.ovsschema'])
            with (p/'daemon.log').open('w') as log:
                try:
                    processes.append(subprocess.Popen(['ovsdb-server','--remote=punix:'+str(p/'db.sock'),
                        '--pidfile='+str(p/'db.pid'),'--unixctl='+str(p/'db.ctl'),str(p/'conf.db')],env=env,stdout=log,stderr=log))
                    deadline=time.monotonic()+5
                    while not (p/'db.sock').exists():
                        self.assertIsNone(processes[-1].poll()); self.assertLess(time.monotonic(),deadline); time.sleep(.02)
                    run(db+['--no-wait','init'])
                    processes.append(subprocess.Popen(['ovs-vswitchd','unix:'+str(p/'db.sock'),'--enable-dummy',
                        '--pidfile='+str(p/'switch.pid'),'--unixctl='+str(p/'switch.ctl')],env=env,stdout=log,stderr=log))
                    run(db+['add-br','acc1','--','set','Bridge','acc1','datapath_type=dummy','protocols=OpenFlow13',
                            '--','add-port','acc1','acc1-eth1','--','set','Interface','acc1-eth1','type=dummy','ofport_request=1',
                            '--','add-port','acc1','acc1-eth10','--','set','Interface','acc1-eth10','type=dummy','ofport_request=10'])
                    original=backend.execute
                    def execute(args,input=None):
                        args=list(args)
                        if args[0]=='ovs-vsctl': args.insert(1,'--db=unix:'+str(p/'db.sock'))
                        if args[0]=='ovs-ofctl' and 'acc1' in args:
                            args[args.index('acc1')]='unix:'+str(p/'acc1.mgmt')
                        result=subprocess.run(args,input=input,env=env,text=True,capture_output=True,timeout=10)
                        if result.returncode: raise RuntimeError(result.stderr or result.stdout)
                        return result.stdout.strip()
                    inv=build_inventory(); b=backend.OVSBackend(inv,'isolated-ovs-test')
                    first=flow(inv,'ac1','bc1',1,10,True,'udp',19020,9020,0xd153000001001400,104)
                    second=first.replace('000001001400','000002001400').replace('set_queue:104','set_queue:168')
                    with patch('policy.backend.execute',side_effect=execute),patch.object(b,'connected',return_value=True):
                        q={'acc1-eth10':{'capacity':100000000,'queues':{'0':{'min':500000,'max':100000000,'priority':0},
                                                                           '104':{'min':1000000,'max':4000000,'priority':0},
                                                                           '168':{'min':1000000,'max':8000000,'priority':0}}}}
                        b.prepare(q); self.assertTrue(b.queue_matches('acc1-eth10',q['acc1-eth10']))
                        b.write('acc1',[first]); b.verify('acc1',[first])
                        b.write('acc1',[first]); b.verify('acc1',[first])
                        changed=first.replace('set_queue:104','set_queue:168')
                        b.write('acc1',[changed]); b.verify('acc1',[changed])
                        extra=first.replace('in_port=1,','in_port=10,')
                        b.write('acc1',[first,extra]); b.verify('acc1',[first,extra])
                        b.write('acc1',[second]); b.verify('acc1',[second])
                        with self.assertRaises(RuntimeError): b.verify('acc1',[first])
                        b.write('acc1',[first]); b.verify('acc1',[first])
                        b.write('acc1',[]); b.cleanup()
                        self.assertFalse([r for r in b.table('QoS') if r['external_ids'].get('run')=='isolated-ovs-test'])
                        self.assertFalse([r for r in b.table('Queue') if r['external_ids'].get('run')=='isolated-ovs-test'])
                    # Actual Ryu topology publisher + twenty actual OVS dummy
                    # bridges. Only kernel queues/traffic remain a live-lab gate.
                    args=[]
                    for switch, dpid in inv.SWITCHES.items():
                        if switch!='acc1': args += ['--','add-br',switch]
                        args += ['--','set','Bridge',switch,'datapath_type=dummy','protocols=OpenFlow13','other-config:datapath-id=%016x'%dpid]
                        for number in sorted(inv.expected_ports(switch)):
                            name='%s-eth%d'%(switch,number)
                            if switch=='acc1' and number in (1,10): continue
                            args += ['--','add-port',switch,name,'--','set','Interface',name,'type=dummy','ofport_request=%d'%number]
                    run(db+args)
                    with socket.socket() as reserved:
                        reserved.bind(('127.0.0.1',0)); controller_port=reserved.getsockname()[1]
                    session=p/'session'; session.mkdir()
                    processes.append(subprocess.Popen([str(DEFAULT_RYU),'-m','ryu.cmd.manager','--ofp-listen-host','127.0.0.1',
                        '--ofp-tcp-listen-port',str(controller_port),str(ROOT/'controller/policy_app.py')],cwd=ROOT,
                        env=dict(env,SDN_PHASE3_DIR=str(session)),stdout=log,stderr=log))
                    args=[]
                    for switch in inv.SWITCHES: args += ['--','set-controller',switch,'tcp:127.0.0.1:%d'%controller_port]
                    run(db+args)
                    deadline=time.monotonic()+20
                    while True:
                        self.assertIsNone(processes[-1].poll(),'Ryu exited')
                        self.assertLess(time.monotonic(),deadline,'Twenty-bridge policy graph startup timed out')
                        if (session/'graph.json').exists():
                            state=topology(session,inv)
                            if len(state.online)==20: break
                        time.sleep(.1)
                    def private_execute(args,input=None):
                        args=list(args)
                        if args[0]=='ovs-vsctl': args.insert(1,'--db=unix:'+str(p/'db.sock'))
                        elif args[0]=='ovs-ofctl':
                            args=[('unix:'+str(p/(a+'.mgmt'))) if a in inv.SWITCHES else a for a in args]
                        result=subprocess.run(args,input=input,env=env,text=True,capture_output=True,timeout=15)
                        if result.returncode: raise RuntimeError(result.stderr or result.stdout)
                        return result.stdout.strip()
                    with patch('policy.backend.execute',side_effect=private_execute):
                        manager=Manager(session,inv)
                        deadline=time.monotonic()+15
                        while not all(manager.backend.connected(s) for s in inv.SWITCHES):
                            self.assertLess(time.monotonic(),deadline,'OVSDB controller connection readback timed out')
                            time.sleep(.1)
                        for profile in PROFILES:
                            cfg=initial_config(inv); cfg['profile']=profile
                            manager.stage(cfg,topology(session,inv)); result=manager.resume()
                            self.assertEqual(result['status'],'verified',str(result.get('switches')))
                            self.assertEqual(result['current']['config']['profile'],profile)
                        manager.rollback()
                        self.assertEqual(manager.deployment.state['status'],'rolled_back')
                        # The isolated switch must carry real service rules;
                        # an empty table cannot detect a destructive fault setup.
                        cfg=manager.config(); cfg['constraints']['district_coordination']={'avoid_switches':['bb1']}
                        manager.stage(cfg,topology(session,inv)); manager.resume()
                        saved=manager.deployment.state['current']
                        self.assertTrue(saved['flows']['bb2'])
                        cfg=manager.config(); cfg['profile']='evacuation'
                        manager.stage(cfg,topology(session,inv)); manager.resume(1)
                        self.assertEqual(manager.deployment.state['status'],'paused')
                        old_refs=manager.backend.ovs('get','Bridge','bb2','controller')
                        with isolated_controller(manager.backend,'bb2'):
                            manager.backend.verify('bb2',saved['flows']['bb2'],local=True)
                            manager.resume(); self.assertEqual(manager.deployment.state['status'],'partial')
                            manager.rollback(); self.assertEqual(manager.deployment.state['status'],'rollback_partial')
                            self.assertEqual(manager.deployment.state['switches']['bb2']['status'],'unverified')
                            manager.backend.verify('bb2',saved['flows']['bb2'],local=True)
                            self.assertIsNone(manager.deployment.state['pending'])
                        self.assertEqual(manager.backend.ovs('get','Bridge','bb2','controller'),old_refs)
                        manager.backend.verify('bb2',saved['flows']['bb2'])
                        for switch in inv.SWITCHES: manager.backend.write(switch,[])
                        manager.backend.cleanup()
                finally:
                    for process in reversed(processes): stop_process(process)
                    self.assertTrue(all(process.poll() is not None for process in processes))


if __name__=='__main__': unittest.main()
