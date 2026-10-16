"""Owned live demo actions; the student chooses inspection and correction separately."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from config.expanded import build_inventory
from config.incident import initial_config, catalog
from demo_scripts.model import (DEMOS, URGENT_RATE, DEADLINE_MS, MEASURE_SECONDS,
                               congestion_config, corrected_congestion, urgent_metrics,
                               prepare_route_problem, route_options)
from policy.operator import Manager, topology, locked, atomic, summary
from policy.backend import execute
from policy.model import PREFIX, MASK
from policy.processes import stop_group
from performance.run_tests import stop_process
from tests.lab_harness import ROOT, require
from tests.expanded_harness import host_command
from tests.phase3_harness import check, wait, isolated_controller


def json_events(path):
    # Writers flush each complete line. Ignore only the final line still being
    # written; a malformed complete record must remain an error.
    data=path.read_bytes(); lines=data.split(b'\n')
    return [json.loads(line) for line in lines[:-1] if line]


class DemoLab:
    def __init__(self, net, inv, directory, responders):
        self.net,self.inv,self.directory,self.responders=net,inv,Path(directory),responders
        self.session=self.directory/'state'; self.services=catalog(inv.districts)
        self.active=None; self.folder=None; self.state={}; self.processes={}; self.watch=None
        self.isolation=None; self.failed_link=None; self.saved=None; self.serial=0
        self.cleanup=[]
        self.replaced_echo=set()
        self.automatic=False

    def manager(self): return Manager(self.session,self.inv)

    def start_watch(self):
        self.automatic=True
        if self.watch is not None:
            require(self.watch.poll() is None,'Policy watcher exited; inspect policy_watch.log')
            return
        with (self.directory/'policy_watch.log').open('a') as log:
            self.watch=subprocess.Popen([sys.executable,'-m','policy.operator','--state-dir',str(self.session),'watch'],
                                        cwd=ROOT,stdout=log,stderr=log,start_new_session=True)

    def save(self):
        atomic(self.directory/'demo_state.json',{'active':self.active,'folder':str(self.folder) if self.folder else None,
                                                'state':self.state})

    def spawn(self, name, host, args, log=None):
        require(name not in self.processes,'Owned process already exists: '+name)
        path=log or self.folder/(name+'.log'); handle=path.open('w')
        try: process=self.net[host].popen(args,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT)
        except Exception: handle.close(); raise
        self.processes[name]=(process,handle)
        return process

    def stop(self, name):
        owned=self.processes.pop(name,None)
        if owned:
            process,handle=owned
            try: stop_process(process)
            finally: handle.close()
            self.cleanup.append({'name':name,'pid':process.pid,'returncode':process.poll()})

    def alive(self, *names):
        for name in names:
            require(name in self.processes and self.processes[name][0].poll() is None,'Owned workload exited: '+name)

    def apply(self, config):
        with locked(self.session):
            manager=self.manager(); manager.stage(config,topology(self.session,self.inv)); result=manager.resume()
            require(result['status']=='verified','Policy did not fully verify: '+str(summary(result)))
            return result['current']

    def probe(self, label, service, allowed, inventory=None):
        self.serial+=1; label='%03d_%s'%(self.serial,label)
        check(self.net,inventory or self.inv,self.folder,label,service,allowed)
        return {'label':label,'allowed':allowed,
                'client':{k:v for k,v in json.loads((self.folder/(label+'.json')).read_text()).items() if k!='raw'},
                'receiver':json.loads((self.folder/(label+'_receiver.json')).read_text())}

    def verify_policy(self, local_switch=None, snapshot=None):
        manager=self.manager(); snapshot=snapshot or manager.deployment.state['current']
        result={s:manager.backend.verify(s,flows,local=s==local_switch) for s,flows in snapshot['flows'].items()}
        queues=manager.backend.queue_state()
        require(all(manager.backend.queue_matches(p,q,queues,True) for p,q in snapshot['queues'].items()),'Queue rate/ownership readback mismatch')
        return result

    def listener(self, host, port):
        return bool(host_command(self.net[host],['ss','-H','-ltn','sport','=',':%d'%port]).strip())

    def stop_echo(self, name):
        if 'echo_'+name in self.processes: self.stop('echo_'+name)
        elif self.responders[name].poll() is None: stop_process(self.responders[name])

    def restart_echo(self, name):
        service=self.services[name]; ready=self.folder/(name+'_reset_ready.json')
        if ready.exists(): ready.unlink()
        process=self.spawn('echo_'+name,service['target'],[sys.executable,'-m','monitoring.service_probe','server',
            '--protocol',service['protocol'],'--server-port',str(service['server_port']),'--status',str(ready.resolve())])
        self.responders[name]=process
        wait(lambda:ready.exists() and process.poll() is None,label='Reset service listener: '+name)

    def setup(self, demo):
        require(self.active is None,'Reset the current demonstration before starting another')
        self.active=demo; self.serial=0; self.state={}; self.saved=None
        index=len(list(self.directory.glob('demo_*')))+1
        self.folder=self.directory/('demo_%03d_%s'%(index,demo)); self.folder.mkdir()
        if demo=='congestion':
            snapshot=self.apply(congestion_config(self.inv))
            first=snapshot['decisions']['a_medical']['forward'][-1]
            second=snapshot['decisions']['a_bulk']['forward'][-1]
            require(first[0]==second[0] and first[2]==second[2],'Workloads do not share the receiver egress')
            port='%s-eth%d'%(first[0],first[2]); require(snapshot['capacities'][port]==50000000,'Expected 50 Mbps receiver egress')
            self.state.update(shared_egress=port,capacity_mbps=50,urgent_requests_per_second=URGENT_RATE,
                              urgent_payload_bytes=8192,urgent_offered_mbps=8192*8*URGENT_RATE/1000000,
                              bulk_offered_mbps=60,deadline_ms=DEADLINE_MS,measurements={},phase='baseline')
            for name in ('a_medical','a_bulk'):
                self.stop_echo(name); self.replaced_echo.add(name)
            medical=self.services['a_medical']; bulk=self.services['a_bulk']
            host_command(self.net[medical['source']],['ping','-n','-c','1','-W','1',self.inv.ENDPOINTS[medical['target']].ip])
            ready=self.folder/'urgent_ready.json'
            server=self.spawn('urgent_server',medical['target'],[sys.executable,'-m','monitoring.urgent_stream','server',
                '--server-port',str(medical['server_port']),'--status',str(ready.resolve())])
            wait(lambda:ready.exists() and server.poll() is None,label='Urgent echo listener')
            self.spawn('bulk_server',bulk['target'],['iperf','-s','-p',str(bulk['server_port']),'-i','1','-f','m'])
            wait(lambda:self.listener(bulk['target'],bulk['server_port']),label='Routine bulk listener')
            self.spawn('urgent_client',medical['source'],[sys.executable,'-m','monitoring.urgent_stream','client',
                '--source',self.inv.ENDPOINTS[medical['source']].ip,'--target',self.inv.ENDPOINTS[medical['target']].ip,
                '--client-port',str(medical['client_port']),'--server-port',str(medical['server_port']),'--rate',str(URGENT_RATE)])
            wait(lambda:(self.folder/'urgent_client.log').stat().st_size>0,label='Paced urgent workload')
            time.sleep(.5)
        elif demo=='routing':
            self.apply(initial_config(self.inv))
            self.state=prepare_route_problem(initial_config(self.inv),topology(self.session,self.inv))
            self.probe('routing_baseline',self.services['district_coordination'],True)
        elif demo=='onboarding':
            self.apply(initial_config(self.inv))
            host_command(self.net['af3'],['ip','link','set','dev',self.net['af3'].application_interface,'down'])
            self.state={'endpoint':'af3','service':'a_reporting','activated':False,'registered':False,'granted':False}
        else:
            cfg=initial_config(self.inv); cfg['constraints']['district_coordination']={'avoid_switches':['bb1']}
            self.saved=deepcopy(self.apply(cfg))
            require(bool(self.saved['flows']['bb2']),'Rollback needs nonempty saved bb2 rules')
            self.state={'saved_version':self.saved['version'],'update_scope':'whole incident profile across twenty switches'}
            self.probe('rollback_baseline',self.services['district_coordination'],True)
        return {'status':'prepared','demo':demo,'state':self.state,'evidence':str(self.folder)}

    def queue_bytes(self, name):
        snapshot=self.manager().deployment.state['current']; switch,_,port=snapshot['decisions'][name]['forward'][-1]
        number=snapshot['version']*64+self.services[name]['queue_index']*2
        raw=execute(['ovs-ofctl','-O','OpenFlow13','queue-stats',switch,str(port),str(number)])
        value=re.search(r'queue\s+%d:\s+bytes=(\d+)'%number,raw)
        require(value is not None,'Missing actual queue counters')
        return {'switch':switch,'port':port,'queue':number,'bytes':int(value[1]),'raw':raw,'monotonic':time.monotonic()}

    def measure(self):
        if self.active=='onboarding':
            require(self.state['activated'],'Activate the prepared endpoint first')
            result=self.probe('reporting_'+('granted' if self.state['granted'] else 'denied'),self.services['a_reporting'],self.state['granted'])
            if not self.state['granted']:
                self.state['denied_after_registration' if self.state['registered'] else 'denied_before_registration']=True
            return result
        require(self.active=='congestion','measure is supported for congestion and onboarding')
        self.alive('urgent_client','urgent_server'); phase=self.state['phase']
        if phase!='baseline': self.alive('bulk_client','bulk_server')
        queues_before={n:self.queue_bytes(n) for n in ('a_medical','a_bulk')}
        start=time.monotonic(); time.sleep(MEASURE_SECONDS); end=time.monotonic(); time.sleep(DEADLINE_MS/1000+.05)
        self.alive('urgent_client','urgent_server')
        if phase!='baseline': self.alive('bulk_client','bulk_server')
        events=json_events(self.folder/'urgent_client.log'); metrics=urgent_metrics(events,start,end)
        require(URGENT_RATE*.9<=metrics['offered_per_second']<=URGENT_RATE*1.1,'Urgent offered rate did not remain fixed')
        offers={e['sequence'] for e in events if e['event']=='offer' and start<=e['monotonic']<end}
        nonce=next(e['nonce'] for e in events if e['event']=='started')
        seen={e['sequence'] for e in json_events(self.folder/'urgent_server.log') if e['event']=='request' and e['nonce']==nonce}
        replies={e['sequence'] for e in events if e['event']=='reply' and e['sequence'] in offers}
        require(replies<=seen,'Echo replies lack receiver-side request evidence')
        queues_after={n:self.queue_bytes(n) for n in ('a_medical','a_bulk')}
        require(all((queues_before[n]['switch'],queues_before[n]['port'],queues_before[n]['queue'])==
                    (queues_after[n]['switch'],queues_after[n]['port'],queues_after[n]['queue']) for n in queues_before),
                'Queue path changed during the window; repeat a stable measurement')
        rates={n:8*(queues_after[n]['bytes']-queues_before[n]['bytes'])/
               (queues_after[n]['monotonic']-queues_before[n]['monotonic'])/1000000 for n in queues_before}
        require(queues_after['a_medical']['bytes']>queues_before['a_medical']['bytes'],'Urgent queue did not carry traffic')
        if phase!='baseline': require(rates['a_bulk']>0,'Competing bulk workload carried no receiver traffic')
        result={**metrics,'phase':phase,'start':start,'end':end,'receiver_unique_requests':len(offers&seen),
                'queues_before':queues_before,'queues_after':queues_after,'queue_throughput_mbps':rates,
                'urgent_pid':self.processes['urgent_client'][0].pid,
                'bulk_pid':self.processes['bulk_client'][0].pid if phase!='baseline' else None}
        self.state['measurements'][phase]=result; atomic(self.folder/(phase+'_measurement.json'),result)
        return result

    def inject(self):
        if self.active=='congestion':
            measurements=self.state['measurements']
            require('baseline' in measurements and measurements['baseline']['goals_met'],'Measure a passing urgent baseline first')
            require(self.state['phase']=='baseline','Bulk workload already injected')
            service=self.services['a_bulk']
            self.spawn('bulk_client',service['source'],['iperf','-c',self.inv.ENDPOINTS[service['target']].ip,
                '-p',str(service['server_port']),'-B',self.inv.ENDPOINTS[service['source']].ip+':'+str(service['client_port']),
                '-b','60m','-t','3600','-i','1','-f','m'])
            time.sleep(2); self.alive('bulk_client'); self.state['phase']='congested'
            return {'status':'bulk running','offered_mbps':60,'next':'measure and inspect before applying a correction'}
        if self.active=='routing':
            require(self.failed_link is None,'A link fault is already active')
            self.start_watch()
            self.failed_link=tuple(self.state['failed_link']); self.net.configLinkStatus(*self.failed_link,'down')
            def rerouted():
                require(self.watch.poll() is None,'Policy watcher exited')
                current=self.manager().deployment.state
                if current['status']!='verified' or current['pending']: return False
                nodes=[s for s,_,_ in current['current']['decisions']['district_coordination']['forward']]
                return not any({a,b}==set(self.failed_link) for a,b in zip(nodes,nodes[1:]))
            wait(rerouted,60,'Ordinary automatic backup reconciliation')
            route=self.manager().deployment.state['current']['decisions']['district_coordination']['forward']
            require(self.state['avoid_switch'] in [s for s,_,_ in route],'Ordinary backup already meets the constraint; task is not meaningful')
            self.state['ordinary_actual']=route
            return {'status':'ordinary backup active','automatic_path':route,'additional_requirement':'avoid '+self.state['avoid_switch'],
                    'failed_link_stays_down':self.failed_link}
        if self.active=='onboarding':
            require(not self.state['activated'],'Endpoint already activated')
            host_command(self.net['af3'],['ip','link','set','dev',self.net['af3'].application_interface,'up'])
            attachment=self.inv.ENDPOINTS['af3'].attachments[0]
            wait(lambda:(attachment.switch,attachment.port) in topology(self.session,self.inv).up,label='Prepared endpoint attachment')
            self.state['activated']=True
            return {'status':'endpoint active, unregistered','endpoint':'af3','next':'measure denied access, then register'}
        require(self.isolation is None,'Controller isolation is already active')
        with locked(self.session):
            manager=self.manager(); cfg=manager.config(); cfg['profile']='evacuation'
            manager.stage(cfg,topology(self.session,self.inv)); result=manager.resume(1)
            require(result['status']=='paused','Deterministic checkpoint failed')
            atomic(self.folder/'checkpoint.json',result)
            self.state['candidate_version']=result['pending']['version']
        self.isolation=isolated_controller(self.manager().backend,'bb2')
        isolation=self.isolation.__enter__(); atomic(self.folder/'controller_isolation.json',isolation)
        self.manager().backend.verify('bb2',self.saved['flows']['bb2'],local=True)
        with locked(self.session):
            manager=self.manager(); result=manager.resume(); require(result['status']=='partial','Isolated update incorrectly succeeded')
            atomic(self.folder/'partial_update.json',result)
        return {'status':'partial','saved_version':self.state['saved_version'],'candidate_version':self.state['candidate_version'],
                'isolated_switch':'bb2','next':'inspect and choose rollback; keep isolation in place'}

    def inspect(self):
        manager=self.manager(); snapshot=manager.deployment.state['current']; graph=topology(self.session,self.inv)
        result={'demo':self.active,'state':self.state,'policy':summary(manager.deployment.state),
                'online_switches':sorted(graph.online),'registered':manager.config()['registered']}
        if self.active=='routing':
            result['ordinary_backup']=self.state.get('ordinary_actual',self.state['ordinary_backup'])
            result['candidates']=route_options(manager.config(),graph,self.state['avoid_switch'])
        if self.active=='onboarding':
            endpoint=self.inv.ENDPOINTS['af3']; result['identity']={'ip':endpoint.ip,'mac':endpoint.mac,
                'authorised_attachment':[list(a) if hasattr(a,'__iter__') else [a.switch,a.port,a.host_port] for a in endpoint.attachments],
                'registered':'af3' in manager.config()['registered'],'permission':'a_reporting' in manager.config()['permissions']}
        switches=('bb1','bb2') if self.active=='rollback' else sorted({snapshot['decisions'][n]['forward'][-1][0]
            for n in ('a_medical','a_bulk')}) if self.active=='congestion' else ()
        result['actual_rule_snapshots']={}
        for switch in switches:
            raw=execute(['ovs-ofctl','-O','OpenFlow13','dump-flows',switch,'cookie=0x%x/0x%x'%(PREFIX,MASK)])
            self.serial+=1; file=self.folder/('%03d_inspect_%s_flows.txt'%(self.serial,switch)); file.write_text(raw+'\n')
            versions=sorted({(int(c,16)>>24)&0xffffff for c in re.findall(r'cookie=(0x[0-9a-f]+)',raw)})
            result['actual_rule_snapshots'][switch]={'versions':versions,'file':str(file),'read_only_local':not manager.backend.connected(switch)}
        if self.active=='congestion': result['actual_queue_counters']={n:self.queue_bytes(n) for n in ('a_medical','a_bulk')}
        return result

    def fix(self, candidate=None):
        if self.active=='congestion':
            require(self.state['phase']=='congested','Inject and measure congestion before correction')
            require('congested' in self.state['measurements'],'Measure the congested case first')
            self.alive('urgent_client','bulk_client')
            self.apply(corrected_congestion(self.manager().config())); self.state['phase']='corrected'; time.sleep(2)
            return {'status':'evacuation applied','workloads':'same running processes and offered rates','next':'measure, then verify'}
        if self.active=='routing':
            require(self.failed_link is not None,'Inject the link failure first')
            require(type(candidate) is int and candidate>0,'Select a listed --candidate number')
            options=route_options(self.manager().config(),topology(self.session,self.inv),self.state['avoid_switch'])
            selected=next((c for c in options if c['candidate']==candidate),None)
            require(selected is not None and selected['capacity_feasible'],'Select a listed capacity-feasible --candidate number')
            cfg=self.manager().config(); cfg['constraints']['district_coordination']={
                'avoid_switches':[self.state['avoid_switch']],'via':selected['switches']}
            snapshot=self.apply(cfg); self.state['selected_candidate']=selected
            return {'status':'constrained route applied','route':snapshot['decisions']['district_coordination'],
                    'failed_link_still_down':self.failed_link}
        if self.active=='rollback':
            require(self.isolation is not None,'Inject a partial update first')
            with locked(self.session):
                manager=self.manager(); result=manager.rollback(); require(result['status']=='rollback_partial','Isolated switch must remain unverified')
                atomic(self.folder/'rollback_status.json',result)
            self.state['rolled_back']=True
            return {'status':'rollback_partial','meaning':'reachable switches restored; bb2 remains isolated and unverified until read-only inspection'}
        raise ValueError('Onboarding uses separate register and grant commands')

    def register(self):
        require(self.active=='onboarding' and self.state['activated'],'Activate the prepared endpoint first')
        require(not self.state['registered'],'Endpoint already registered')
        require(self.state.get('denied_before_registration'),'Measure denied reporting before registration')
        cfg=self.manager().config(); cfg['registered'].append('af3'); self.apply(cfg); self.state['registered']=True
        return {'status':'binding registered','permission_granted':False,'next':'measure denied access again, then grant'}

    def grant(self):
        require(self.active=='onboarding' and self.state['registered'],'Register the endpoint first')
        require(not self.state['granted'],'Reporting permission already granted')
        require(self.state.get('denied_after_registration'),'Measure denied reporting after registration, before grant')
        cfg=self.manager().config(); cfg['permissions'].append('a_reporting'); self.apply(cfg); self.state['granted']=True
        return {'status':'exact reporting service granted','source':'af3','target':self.services['a_reporting']['target'],
                'protocol':'tcp','client_port':19006,'server_port':9006}

    def spoof_check(self):
        attacker=self.net['as3']; interface=attacker.application_interface; original=self.inv.ENDPOINTS['as3']; victim=self.inv.ENDPOINTS['af3']
        gateway=self.inv.SITES[victim.site]; fake=build_inventory(self.inv.districts)
        fake.ENDPOINTS['as3']=replace(original,ip=victim.ip,mac=victim.mac,site=victim.site)
        try:
            host_command(attacker,['ip','-4','addr','flush','dev',interface])
            host_command(attacker,['ip','link','set','dev',interface,'address',victim.mac])
            host_command(attacker,['ip','addr','add',victim.ip+'/24','dev',interface])
            host_command(attacker,['ip','route','replace','default','via',gateway.gateway,'dev',interface])
            return self.probe('spoofed_reporting',{**self.services['a_reporting'],'source':'as3'},False,fake)
        finally:
            host_command(attacker,['ip','link','set','dev',interface,'address',original.mac])
            host_command(attacker,['ip','-4','addr','flush','dev',interface])
            host_command(attacker,['ip','addr','add',original.ip+'/24','dev',interface])
            host_command(attacker,['ip','route','replace','default','via',self.inv.SITES[original.site].gateway,'dev',interface])
            host_command(attacker,['ip','neigh','flush','dev',interface])

    def verify(self):
        if self.active=='congestion':
            measurements=self.state['measurements']
            require(set(measurements)=={'baseline','congested','corrected'},'Measure baseline, congestion and correction separately')
            baseline,before,after=(measurements[k] for k in ('baseline','congested','corrected'))
            require(baseline['goals_met'],'Urgent baseline did not meet the declared goals')
            require(not before['goals_met'],'No urgent degradation observed; cannot claim congestion recovery')
            require(after['goals_met'],'Correction did not meet urgent application goals')
            require(before['urgent_pid']==after['urgent_pid']==baseline['urgent_pid'] and before['bulk_pid']==after['bulk_pid'],
                    'Workload processes changed between measurements')
            require(after['queue_throughput_mbps']['a_bulk']<before['queue_throughput_mbps']['a_bulk'],
                    'No measured cost to competing bulk throughput')
            self.verify_policy(); result={'status':'verified','measurements':measurements,'same_offered_load':True}
        elif self.active=='routing':
            require(self.failed_link is not None and self.state.get('selected_candidate'),'Select and apply a candidate first')
            graph=topology(self.session,self.inv); link=next(l for l in self.inv.LINKS if {l.a,l.b}==set(self.failed_link))
            require((link.a,link.pa) not in graph.up and (link.b,link.pb) not in graph.up,'Failed link was re-enabled')
            decision=self.manager().deployment.state['current']['decisions']['district_coordination']
            for direction in ('forward','reverse'):
                require(self.state['avoid_switch'] not in [s for s,_,_ in decision[direction]],'Avoided switch remains on path')
            self.verify_policy()
            result={'status':'verified','positive':self.probe('selected_route',self.services['district_coordination'],True),
                    'negative':self.probe('forbidden_identity',{**self.services['district_coordination'],'source':'as3'},False),
                    'route':decision,'failed_link_still_down':self.failed_link}
        elif self.active=='onboarding':
            require(self.state['granted'],'Grant the reporting service first'); self.verify_policy()
            result={'status':'verified','reporting':self.probe('authorised_reporting',self.services['a_reporting'],True),
                    'unrelated':self.probe('unrelated_medical',{**self.services['a_medical'],'source':'af3'},False),
                    'spoofed':self.spoof_check()}
        else:
            require(self.state.get('rolled_back') and self.isolation is not None,'Roll back while bb2 remains isolated first')
            manager=self.manager(); require(manager.deployment.state['pending'] is None,'Abandoned candidate is still pending')
            tables=self.verify_policy('bb2',self.saved)
            result={'status':'verified with explicit isolated read-only inspection','tables':tables,
                    'coordination':self.probe('isolated_coordination',self.services['district_coordination'],True),
                    'dispatch':self.probe('isolated_dispatch',self.services['a_dispatch'],True)}
        self.state['verified']=True; atomic(self.folder/'verification.json',result); return result

    def reset(self):
        require(self.active is not None,'No active demonstration')
        demo=self.active
        if self.watch: stop_group(self.watch); self.watch=None
        if self.isolation:
            cm=self.isolation; self.isolation=None; cm.__exit__(None,None,None)
            wait(lambda:len(topology(self.session,self.inv).online)==len(self.inv.SWITCHES),30,'Controller restoration')
        if self.failed_link:
            pair=self.failed_link; self.net.configLinkStatus(*pair,'up'); self.failed_link=None
            link=next(l for l in self.inv.LINKS if {l.a,l.b}==set(pair))
            wait(lambda:all(p in topology(self.session,self.inv).up for p in ((link.a,link.pa),(link.b,link.pb))),label='Routing reset')
        if demo=='congestion':
            # Servers initiate close so fixed client ports are reusable.
            for name in ('urgent_server','urgent_client','bulk_server','bulk_client'): self.stop(name)
            for name in sorted(self.replaced_echo): self.restart_echo(name)
            self.replaced_echo.clear()
        if demo=='onboarding':
            host_command(self.net['af3'],['ip','link','set','dev',self.net['af3'].application_interface,'up'])
            attachment=self.inv.ENDPOINTS['af3'].attachments[0]
            wait(lambda:(attachment.switch,attachment.port) in topology(self.session,self.inv).up,label='Endpoint reset')
        with locked(self.session):
            manager=self.manager()
            if manager.deployment.state['pending'] is not None or manager.deployment.state['status'] in ('rollback_partial','rolling_back'):
                manager.rollback()
        self.apply(initial_config(self.inv)); self.verify_policy()
        baseline=self.probe('reset_dispatch',self.services['a_dispatch'],True)
        denied=self.probe('reset_unregistered_reporting',self.services['a_reporting'],False)
        result={'status':'reset verified','demo':demo,'baseline':baseline,'unregistered_reporting':denied,
                'demo_verified':self.state.get('verified',False),'pending':self.manager().deployment.state['pending']}
        atomic(self.folder/'reset.json',result); self.active=None; self.state={}; self.save()
        if self.automatic: self.start_watch()
        return result

    def execute(self, request):
        if set(request)-{'demo','action','candidate'}: raise ValueError('Unknown command field')
        demo,action=request.get('demo'),request.get('action')
        if demo not in DEMOS: raise ValueError('Unknown demonstration')
        started=time.monotonic(); result=None; error=None
        try:
            if action=='setup': result=self.setup(demo)
            else:
                require(self.active==demo,'Prepare this demonstration first; reset any other active demo')
                require(action!='rollback' or demo=='rollback','rollback is specific to the rollback demonstration')
                operations={'inject':self.inject,'inspect':self.inspect,'measure':self.measure,'fix':lambda:self.fix(request.get('candidate')),
                            'rollback':lambda:self.fix(),'register':self.register,'grant':self.grant,'verify':self.verify,'reset':self.reset}
                if action not in operations: raise ValueError('Unsupported demo action')
                result=operations[action]()
            return result
        except Exception as exc:
            error=str(exc); raise
        finally:
            self.save()
            with (self.directory/'commands.jsonl').open('a') as handle:
                handle.write(json.dumps({'monotonic':started,'duration_sec':time.monotonic()-started,'request':request,
                                         'ok':error is None,'error':error,'result':result})+'\n')

    def close(self):
        self.automatic=False
        errors=[]
        if self.active:
            try: self.reset()
            except Exception as exc: errors.append('reset: '+str(exc))
        if self.watch:
            try: stop_group(self.watch)
            except Exception as exc: errors.append('watch: '+str(exc))
        if self.isolation:
            try: self.isolation.__exit__(None,None,None)
            except Exception as exc: errors.append('isolation: '+str(exc))
        for name in list(self.processes):
            try: self.stop(name)
            except Exception as exc: errors.append(name+': '+str(exc))
        atomic(self.directory/'demo_cleanup.json',{'processes':self.cleanup,'errors':errors})
        return errors
