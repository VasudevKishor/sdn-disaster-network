"""Phase 4 pure admission/measurement checks and real unprivileged workloads."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,MagicMock,patch
from contextlib import contextmanager,nullcontext
from config.expanded import build_inventory
from config.incident import initial_config
from demo_scripts.model import (congestion_config,corrected_congestion,prepare_route_problem,
                               route_options,urgent_metrics,URGENT_RATE)
from demo_scripts.engine import DemoLab,json_events
from demo_scripts.control import request
from demo_scripts.session import Handler,demo_network
from monitoring.urgent_stream import SIZE,message,MAGIC
from performance.run_tests import stop_process,parse_iperf_throughput_mbps
from policy.model import plan
from policy.operator import Manager
from tests.test_expanded_model import ready
from tests.test_incident_deploy import Backend
import socketserver


class DemoModelTests(unittest.TestCase):
    def setUp(self): self.inv=build_inventory(); self.state=ready(self.inv)

    def test_congestion_is_jointly_admitted_on_same_egress_and_transition_fits(self):
        before=plan(congestion_config(self.inv),self.state,1)
        after=plan(corrected_congestion(before['config']),self.state,2)
        medical=before['decisions']['a_medical']['forward'][-1]; bulk=before['decisions']['a_bulk']['forward'][-1]
        self.assertEqual((medical[0],medical[2]),(bulk[0],bulk[2]))
        self.assertEqual(before['capacities']['%s-eth%d'%(medical[0],medical[2])],50000000)
        self.assertEqual(after['config']['profile'],'evacuation'); self.assertNotIn('a_bulk',after['config']['rates'])
        self.assertEqual(after['decisions']['a_bulk']['maximum_bps'],1000000)
        for port in set(before['queues'])|set(after['queues']):
            queues={**before['queues'].get(port,{}).get('queues',{}),**after['queues'].get(port,{}).get('queues',{})}
            capacity=(after['queues'].get(port) or before['queues'][port])['capacity']
            self.assertLessEqual(sum(q['min'] for q in queues.values()),capacity,port)

    def test_route_problem_has_distinct_ordinary_and_constrained_survivors(self):
        config=initial_config(self.inv); task=prepare_route_problem(config,self.state)
        self.assertIn(task['avoid_switch'],[s for s,_,_ in task['ordinary_backup']])
        link=next(l for l in self.inv.LINKS if {l.a,l.b}==set(task['failed_link']))
        self.state.up-={(link.a,link.pa),(link.b,link.pb)}
        options=route_options(config,self.state,task['avoid_switch']); self.assertTrue(options)
        for option in options:
            self.assertNotIn(task['avoid_switch'],option['switches'])
            self.assertTrue(option['capacity_feasible'])
            self.assertFalse(any({a,b}==set(task['failed_link']) for a,b in zip(option['switches'],option['switches'][1:])))

    def test_no_route_problem_is_fabricated_in_partition(self):
        self.state.up=set()
        with self.assertRaises((ValueError,KeyError)): prepare_route_problem(initial_config(self.inv),self.state)

    def test_urgent_acceptance_counts_missing_late_and_duplicate_replies(self):
        offers=[{'event':'offer','sequence':i,'monotonic':1+i*.01} for i in range(100)]
        replies=[{'event':'reply','sequence':i,'monotonic':1+i*.01+.01} for i in range(100)]
        baseline=urgent_metrics(offers+replies,1,2); self.assertTrue(baseline['goals_met'])
        lost=urgent_metrics(offers+replies[:70],1,2); self.assertFalse(lost['goals_met']); self.assertEqual(lost['on_time'],70)
        late=urgent_metrics(offers+[{**r,'monotonic':r['monotonic']+.5} for r in replies],1,2)
        self.assertFalse(late['goals_met']); self.assertEqual(late['on_time'],0)
        dup=urgent_metrics(offers+replies+[replies[0]],1,2); self.assertFalse(dup['goals_met']); self.assertEqual(dup['duplicates'],1)
        with self.assertRaises(ValueError): urgent_metrics(offers+offers+replies,1,2)

    def test_urgent_windows_do_not_use_other_period_replies_as_delivery(self):
        rows=[{'event':'offer','sequence':1,'monotonic':.9},{'event':'reply','sequence':1,'monotonic':1.1},
              {'event':'offer','sequence':2,'monotonic':1.2},{'event':'reply','sequence':2,'monotonic':1.3}]
        r=urgent_metrics(rows,1,2); self.assertEqual((r['offered'],r['received']),(1,1))
        self.assertEqual(urgent_metrics([],1,2)['goals_met'],False)

    def test_registration_and_permission_commands_cannot_skip_negative_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            driver=DemoLab(Mock(),self.inv,Path(temp),{})
            driver.active='onboarding'; driver.state={'activated':True,'registered':False,'granted':False}
            with self.assertRaisesRegex(AssertionError,'before registration'): driver.register()
            driver.state['registered']=True
            with self.assertRaisesRegex(AssertionError,'after registration'): driver.grant()

    def test_congestion_verification_cannot_claim_recovery_without_a_problem(self):
        with tempfile.TemporaryDirectory() as temp:
            driver=DemoLab(Mock(),self.inv,Path(temp),{}); driver.active='congestion'
            driver.state={'measurements':{s:{'goals_met':True} for s in ('baseline','congested','corrected')}}
            with self.assertRaisesRegex(AssertionError,'No urgent degradation'): driver.verify()

    def test_log_reader_only_ignores_incomplete_last_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'events'; path.write_text('{"event":"offer"}\n{"event":')
            self.assertEqual(json_events(path),[{'event':'offer'}])
            path.write_text('broken\n')
            with self.assertRaises(ValueError): json_events(path)


class WorkloadTests(unittest.TestCase):
    def test_real_paced_medical_echo_and_source_port_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            with socket.socket() as reserved: reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
            with socket.socket() as reserved: reserved.bind(('127.0.0.1',0)); source_port=reserved.getsockname()[1]
            ready=directory/'ready'; processes=[]
            with (directory/'server.log').open('w') as server_log,(directory/'client.log').open('w') as client_log:
                try:
                    server=subprocess.Popen([sys.executable,'-m','monitoring.urgent_stream','server','--server-port',str(port),'--status',str(ready)],stdout=server_log,stderr=server_log)
                    processes.append(server); end=time.monotonic()+3
                    while not ready.exists():
                        self.assertIsNone(server.poll()); self.assertLess(time.monotonic(),end); time.sleep(.01)
                    client=subprocess.Popen([sys.executable,'-m','monitoring.urgent_stream','client','--source','127.0.0.1','--target','127.0.0.1',
                        '--client-port',str(source_port),'--server-port',str(port),'--rate',str(URGENT_RATE)],stdout=client_log,stderr=client_log)
                    processes.append(client); time.sleep(.3); start=time.monotonic(); time.sleep(1); finish=time.monotonic(); time.sleep(.3)
                    self.assertIsNone(client.poll()); self.assertIsNone(server.poll())
                finally:
                    for process in processes: stop_process(process)  # Server first.
            self.assertTrue(all(p.returncode==0 for p in processes))
            rows=json_events(directory/'client.log'); result=urgent_metrics(rows,start,finish)
            self.assertTrue(result['goals_met'],result)
            self.assertLess(abs(result['offered_per_second']-URGENT_RATE),URGENT_RATE*.1)
            self.assertGreater(len([e for e in json_events(directory/'server.log') if e['event']=='request']),100)
            with socket.socket() as reused: reused.bind(('127.0.0.1',source_port))
            self.assertEqual(len(message(5,b'x'*16)),SIZE); self.assertEqual(message(5,b'x'*16)[:8],MAGIC)

    def test_real_tcp_bulk_supports_fixed_source_and_pacing(self):
        if not shutil.which('iperf'): self.skipTest('iperf missing')
        with socket.socket() as reserved: reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
        with socket.socket() as reserved: reserved.bind(('127.0.0.1',0)); source=reserved.getsockname()[1]
        server=subprocess.Popen(['iperf','-s','-p',str(port),'-f','m'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            end=time.monotonic()+3
            while not subprocess.check_output(['ss','-H','-ltn','sport','=',':%d'%port],text=True).strip():
                self.assertIsNone(server.poll()); self.assertLess(time.monotonic(),end); time.sleep(.02)
            r=subprocess.run(['iperf','-c','127.0.0.1','-p',str(port),'-B','127.0.0.1:'+str(source),'-b','8m','-t','1','-f','m'],capture_output=True,text=True,timeout=6)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        finally: stop_process(server)
        out,err=server.communicate(timeout=3)
        self.assertIn('port '+str(source),out); self.assertGreater(parse_iperf_throughput_mbps(out) or 0,0)


class DemoBackend(Backend):
    def queue_state(self): return self.queues
    def queue_matches(self,port,expected,state,allow_extra):
        return all(state[port]['queues'].get(n)==v for n,v in expected['queues'].items())
    def connected(self,switch): return switch not in self.offline


class DemoStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name); self.inv=build_inventory(); self.graph=ready(self.inv); self.backend=DemoBackend()
        self.net=MagicMock(); self.net.waitConnected.return_value=True
        self.driver=DemoLab(self.net,self.inv,self.path,{})
        self.driver.manager=lambda:Manager(self.path/'state',self.inv,self.backend)
        self.probes=[]
        def probe(label,service,allowed,inventory=None):
            self.probes.append((label,service,allowed))
            return {'label':label,'allowed':allowed,'client':{'received':100 if allowed else 0}}
        self.driver.probe=probe
        for target,kwargs in (
                ('demo_scripts.engine.topology',{'return_value':self.graph}),
                ('demo_scripts.engine.execute',{'return_value':''})):
            mocked=patch(target,**kwargs); mocked.start(); self.addCleanup(mocked.stop)
        self.host_command_mock=patch('demo_scripts.engine.host_command',return_value='')
        self.host_commands=self.host_command_mock.start(); self.addCleanup(self.host_command_mock.stop)
        self.driver.apply(initial_config(self.inv))

    def call(self,demo,action): return self.driver.execute({'demo':demo,'action':action})

    def test_onboarding_commands_and_reset_restore_actual_policy_model(self):
        # Packet delivery is stubbed here; policy snapshots and flow compilation
        # are real. Actual packets are checked separately in the privileged gate.
        self.call('onboarding','setup'); self.call('onboarding','inject')
        site=self.inv.SITES[self.inv.ENDPOINTS['af3'].site]
        self.host_commands.assert_any_call(self.net['af3'],[
            'ip','route','replace','default','via',site.gateway,'dev',self.net['af3'].application_interface])
        self.host_commands.assert_any_call(self.net['af3'],[
            'ip','neigh','replace',site.gateway,'lladdr',site.gateway_mac,'nud','permanent',
            'dev',self.net['af3'].application_interface])
        self.call('onboarding','measure'); self.call('onboarding','register'); self.call('onboarding','measure')
        self.call('onboarding','grant')
        with patch.object(self.driver,'spoof_check',return_value={'denied':True}): self.call('onboarding','verify')
        result=self.call('onboarding','reset'); self.assertTrue(result['demo_verified']); self.assertIsNone(self.driver.active)
        config=self.driver.manager().config(); self.assertNotIn('af3',config['registered']); self.assertNotIn('a_reporting',config['permissions'])
        reporting=[p for p in self.probes if p[1]==self.driver.services['a_reporting']]
        self.assertEqual([p[2] for p in reporting],[False,False,True,False])
        expected=plan(initial_config(self.inv),self.graph,self.driver.manager().deployment.state['current']['version'])
        self.assertEqual(self.backend.tables,expected['flows'])

    def test_profile_rollback_keeps_isolation_until_reset_and_abandons_candidate(self):
        @contextmanager
        def isolated(backend,switch):
            backend.offline.add(switch)
            try: yield {'controller_rows':{'known':'original'}}
            finally: backend.offline.remove(switch)
        with patch('demo_scripts.engine.isolated_controller',side_effect=isolated):
            self.call('rollback','setup'); self.call('rollback','inject')
            state=self.driver.manager().deployment.state; self.assertEqual(state['status'],'partial')
            self.assertTrue(state['current']['flows']['bb2']); self.assertIn('bb2',self.backend.offline)
            self.call('rollback','inspect'); self.call('rollback','rollback'); self.call('rollback','verify')
            self.assertIn('bb2',self.backend.offline); self.assertIsNone(self.driver.manager().deployment.state['pending'])
            result=self.call('rollback','reset'); self.assertTrue(result['demo_verified']); self.assertFalse(self.backend.offline)
            self.assertIsNone(self.driver.manager().deployment.state['pending'])
        self.assertTrue((self.path/'commands.jsonl').exists())


class LocalCommandTests(unittest.TestCase):
    def test_session_setup_and_final_cleanup_share_persisted_queue_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp); inv=build_inventory(); graph=ready(inv); owners=set(); identities={}
            class OwnedBackend(DemoBackend):
                def prepare(self,queues):
                    super().prepare(queues); owners.add(self.owner)
                    identities['prepare']=self.owner
                def cleanup(self):
                    identities['cleanup']=self.owner; owners.discard(self.owner)
            def manager(path,inventory):
                backend=OwnedBackend(); m=Manager(path,inventory,backend)
                backend.owner=m.saved['owner_id']; return m
            @contextmanager
            def network(*args,**kwargs):
                (directory/'state/graph.json').write_text('{}'); (directory/'controller.log').write_text('')
                yield MagicMock(),Mock()
            with patch('demo_scripts.session.lab',side_effect=network), \
                 patch('demo_scripts.session.health_processes',return_value=nullcontext()), \
                 patch('demo_scripts.session.servers',return_value=nullcontext({})), \
                 patch('demo_scripts.session.topology',return_value=graph), \
                 patch('demo_scripts.engine.topology',return_value=graph), \
                 patch('policy.operator.topology',return_value=graph), \
                 patch('demo_scripts.session.Manager',side_effect=manager), \
                 patch('demo_scripts.engine.Manager',side_effect=manager), \
                 patch.object(DemoLab,'start_watch'):
                with demo_network(directory): pass
            self.assertEqual(identities['prepare'],identities['cleanup'])
            self.assertEqual(owners,set(),'Setup queues would otherwise become foreign to cleanup')

    def test_local_request_uses_separate_actions_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'demo.sock'; driver=Mock(); driver.execute.return_value={'status':'prepared'}
            with socketserver.UnixStreamServer(str(path),Handler) as server:
                server.driver=driver
                thread=threading.Thread(target=server.handle_request); thread.start()
                try: reply=request(Path(temp),{'demo':'routing','action':'inspect'})
                finally: thread.join(timeout=3)
                self.assertTrue(reply['ok']); driver.execute.assert_called_once_with({'demo':'routing','action':'inspect'})
                thread=threading.Thread(target=server.handle_request); thread.start()
                try:
                    with socket.socket(socket.AF_UNIX) as peer:
                        peer.connect(str(path)); peer.sendall(b'[]\n'); result=json.loads(peer.recv(4096))
                    self.assertFalse(result['ok'])
                finally: thread.join(timeout=3)


if __name__=='__main__': unittest.main()
