from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, MagicMock, patch
import json
from config.expanded import build_inventory
from config.incident import initial_config
from policy.model import plan
from policy.deploy import Deployment
from policy.operator import Manager, topology
from tests.test_expanded_model import ready
from tests.phase3_harness import automatic_recovery, isolated_controller


class Backend:
    def __init__(self): self.tables={}; self.queues={}; self.offline=set(); self.writes=[]
    def prepare(self, queues): self.queues=deepcopy(queues)
    def write(self,switch,flows):
        if switch in self.offline: raise RuntimeError('disconnected')
        self.tables[switch]=list(flows); self.writes.append(switch)
    def verify(self,switch,flows,local=False):
        if switch in self.offline and not local: raise RuntimeError('unverified')
        if self.tables.get(switch)!=flows: raise RuntimeError('readback mismatch')
        return {'verified':True,'inspection':'local' if local else 'connected'}


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.i=build_inventory(); self.state=ready(self.i); self.backend=Backend()
        self.v1=plan(initial_config(self.i),self.state,1)
        cfg=initial_config(self.i); cfg['profile']='evacuation'; self.v2=plan(cfg,self.state,2)
        self.d=Deployment(self.backend); self.d.stage(self.v1); self.d.resume()

    def test_invalid_order_cannot_mutate_queues(self):
        before=deepcopy(self.backend.queues)
        with self.assertRaises(ValueError): self.d.stage(self.v2,['bb1'])
        self.assertEqual(self.backend.queues,before)

    def test_checkpoint_is_after_readback_and_before_second_switch(self):
        self.backend.writes=[]; self.d.stage(self.v2); self.d.resume(1)
        self.assertEqual(self.d.state['status'],'paused')
        self.assertEqual(self.backend.writes,['bb1'])
        self.assertEqual(self.backend.tables['bb2'],self.v1['flows']['bb2'])
        self.assertEqual(self.d.state['switches']['bb1']['status'],'verified')

    def test_checkpoint_readback_is_revalidated_before_resume(self):
        self.d.stage(self.v2); self.d.resume(1)
        self.backend.tables['bb1']=[]; self.backend.writes=[]
        self.d.resume()
        self.assertEqual(self.d.state['status'],'partial')
        self.assertEqual(self.d.state['switches']['bb1']['status'],'failed')
        self.assertEqual(self.backend.writes,[])

    def test_partial_update_rollback_with_second_switch_still_isolated(self):
        self.d.stage(self.v2); self.d.resume(1); self.backend.offline.add('bb2')
        self.d.resume(); self.assertEqual(self.d.state['status'],'partial')
        self.assertEqual(self.backend.tables['bb1'],self.v2['flows']['bb1'])
        self.d.rollback()
        self.assertEqual(self.d.state['status'],'rollback_partial'); self.assertIsNone(self.d.state['pending'])
        self.assertEqual(self.backend.tables['bb1'],self.v1['flows']['bb1'])
        self.assertEqual(self.d.state['switches']['bb2']['status'],'unverified')
        self.backend.verify('bb2',self.v1['flows']['bb2'],local=True)
        with self.assertRaises(RuntimeError): self.backend.verify('bb2',self.v1['flows']['bb2'])
        self.backend.offline.clear()
        with self.assertRaises(ValueError): self.d.resume()

    def test_saved_snapshot_rolls_back_full_profile_change(self):
        self.d.stage(self.v2); self.d.resume(); self.d.rollback(1)
        self.assertEqual(self.d.state['current']['version'],1)
        self.assertEqual(self.d.state['status'],'rolled_back')
        self.assertEqual(self.backend.tables,self.v1['flows'])

    def test_queue_failure_cannot_resume_as_success(self):
        def broken(_): raise RuntimeError('OVSDB unavailable')
        self.backend.prepare=broken
        with self.assertRaises(RuntimeError): self.d.stage(self.v2)
        self.assertEqual(self.d.state['status'],'queue_partial')
        with self.assertRaisesRegex(ValueError,'Queue preparation'): self.d.resume()

    def test_journal_survives_checkpoint_and_new_manager(self):
        with tempfile.TemporaryDirectory() as temp:
            manager=Manager(Path(temp),self.i,self.backend)
            manager.stage(initial_config(self.i),self.state); manager.resume()
            cfg=initial_config(self.i); cfg['profile']='evacuation'
            manager.stage(cfg,self.state); manager.resume(1)
            recovered=Manager(Path(temp),self.i,self.backend)
            self.assertEqual(recovered.deployment.state['status'],'paused')
            self.assertEqual(recovered.deployment.state['remaining'][0],'bb2')
            recovered.rollback()
            self.assertIsNone(recovered.deployment.state['pending'])

    def test_stale_topology_is_not_assumed_healthy(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp); (p/'graph.json').write_text(json.dumps({'monotonic':0}))
            with self.assertRaisesRegex(ValueError,'stale'): topology(p,self.i)

    def test_changed_observations_during_update_report_partial(self):
        self.d.stage(self.v2)
        calls=[0]
        def validate():
            calls[0]+=1
            if calls[0]==2: raise RuntimeError('ports changed')
        self.d.validate=validate; self.d.resume()
        self.assertEqual(self.d.state['status'],'partial')
        self.assertEqual(self.d.state['switches']['bb1']['status'],'verified')
        self.assertEqual(self.d.state['switches']['bb2']['status'],'failed')

    def test_same_version_repair_reinstalls_missing_switch_rules(self):
        self.backend.tables['bb1']=[]
        self.d.stage(self.v1); self.d.resume()
        self.assertEqual(self.d.state['status'],'verified')
        self.assertEqual(self.backend.tables['bb1'],self.v1['flows']['bb1'])

    def test_failed_rollback_can_retry_current_recovery_target(self):
        self.d.stage(self.v2); self.d.resume(1); self.backend.offline.add('bb2'); self.d.resume(); self.d.rollback()
        self.backend.offline.clear(); self.d.rollback()
        self.assertEqual(self.d.state['status'],'rolled_back')
        self.assertEqual(self.d.state['current']['version'],1)

    def test_automatic_repair_updates_changed_switches_before_unchanged_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            manager=Manager(Path(temp),self.i,self.backend)
            cfg=initial_config(self.i); cfg['constraints']['district_coordination']={'avoid_switches':['bb1']}
            manager.stage(cfg,self.state); manager.resume()
            current=manager.deployment.state['current']
            route=current['decisions']['district_coordination']['forward']
            names=[s for s,_,_ in route]
            pair=next((a,b) for a,b in zip(names,names[1:]) if a.startswith('bb') and b.startswith('bb'))
            link=next(l for l in self.i.LINKS if {l.a,l.b}==set(pair))
            self.state.up-={(link.a,link.pa),(link.b,link.pb)}
            candidate=manager.candidate(cfg,self.state,False)
            changed={s for s in candidate['flows'] if current['flows'][s]!=candidate['flows'][s]}
            operations=[]; write=self.backend.write; verify=self.backend.verify
            def record_write(s,f): operations.append(('write',s)); return write(s,f)
            def record_verify(s,f,local=False): operations.append(('verify',s)); return verify(s,f,local)
            self.backend.write=record_write; self.backend.verify=record_verify
            manager.repair(self.state)
            self.assertTrue(changed)
            self.assertEqual({s for op,s in operations[:len(changed)] if op=='write'},changed)
            self.assertEqual(manager.deployment.state['status'],'verified')
            self.assertEqual(self.backend.tables,candidate['flows'])
            self.assertEqual({s for op,s in operations if op=='verify'},set(self.i.SWITCHES)-changed)
            self.assertEqual(manager.deployment.state['current']['version'],current['version'])

    def test_automatic_repair_still_reports_isolated_unchanged_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            manager=Manager(Path(temp),self.i,self.backend)
            manager.stage(initial_config(self.i),self.state); manager.resume()
            self.backend.offline.add('bb1')
            result=manager.repair(self.state)
            self.assertEqual(result['status'],'partial')
            self.assertEqual(result['switches']['bb1']['status'],'failed')


class RecoveryHarnessTests(unittest.TestCase):
    def test_isolation_retains_controller_row_and_restores_target_on_error(self):
        row='11111111-1111-1111-1111-111111111111'; original='tcp:127.0.0.1:16333'
        target=[original]; backend=Mock(); backend.table.return_value=[{'_uuid':row,'target':original}]
        def ovs(*args):
            if args==('get','Bridge','bb2','controller'): return '['+row+']'
            self.assertEqual(args[:3],('set','Controller',row))
            target[0]=json.loads(args[3].removeprefix('target='))
        backend.ovs.side_effect=ovs; backend.connected.side_effect=lambda switch:target[0]==original
        with self.assertRaisesRegex(RuntimeError,'injected failure'):
            with isolated_controller(backend,'bb2') as fault:
                self.assertNotEqual(target[0],original)
                self.assertEqual(fault['controller_rows'],{row:original})
                self.assertFalse(backend.connected('bb2'))
                raise RuntimeError('injected failure')
        self.assertEqual(target[0],original)
        self.assertTrue(backend.connected('bb2'))

    def exercise(self, late=False):
        inv=build_inventory(); state=ready(inv); cfg=initial_config(inv)
        cfg['constraints']['district_coordination']={'avoid_switches':['bb1']}
        before=plan(cfg,state,1); nodes=[s for s,_,_ in before['decisions']['district_coordination']['forward']]
        pair=next((a,b) for a,b in zip(nodes,nodes[1:]) if a.startswith('bb') and b.startswith('bb'))
        link=next(l for l in inv.LINKS if {l.a,l.b}==set(pair))
        state.up-={(link.a,link.pa),(link.b,link.pb)}; after=plan(cfg,state,1)
        state.up|={(link.a,link.pa),(link.b,link.pb)}
        clock=[0.]; events=[]; manager=Mock(); manager.deployment.state={'current':before}
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp); session=directory/'state'; session.mkdir()
            def probe(_host,args):
                events.append('probe'); clock[0]=6. if late else .3
                Path(args[args.index('--output')+1]).write_text(json.dumps({'received':1,'errors':0}))
            def wait(predicate,timeout=20,label=''):
                if timeout==60:
                    self.assertIn('probe',events); events.append('readback'); clock[0]=8.
                    (session/'deployment.json').write_text(json.dumps({'deployment':{'current':after,'status':'verified'}}))
                self.assertTrue(predicate())
            with patch('tests.phase3_harness.subprocess.Popen',return_value=Mock(poll=Mock(return_value=None))), \
                 patch('tests.phase3_harness.stop_group'), patch('tests.phase3_harness.host_command',side_effect=probe), \
                 patch('tests.phase3_harness.wait',side_effect=wait), patch('tests.phase3_harness.check'), \
                 patch('tests.phase3_harness.topology',return_value=state), patch('tests.phase3_harness.Manager'), \
                 patch('tests.phase3_harness.time.monotonic',side_effect=lambda:clock[0]):
                if late:
                    with self.assertRaisesRegex(AssertionError,'application recovery exceeded five seconds'):
                        automatic_recovery(MagicMock(),inv,directory,session,manager)
                    self.assertNotIn('readback',events)
                else:
                    automatic_recovery(MagicMock(),inv,directory,session,manager)
                    evidence=json.loads((directory/'automatic_recovery.json').read_text())
                    self.assertEqual(evidence['recovery_probe_completion_sec'],.3)
                    self.assertEqual(evidence['full_readback_sec'],8.)

    def test_echo_recovery_is_measured_before_full_readback(self): self.exercise()
    def test_late_echo_still_fails_unchanged_five_second_budget(self): self.exercise(late=True)


if __name__=='__main__': unittest.main()
