"""Phase 3 policy gate: sudo python3 -m tests.phase3_harness --repetitions 3."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from config.expanded import build_inventory
from config.incident import catalog, PROFILES, initial_config
from policy.operator import Manager, topology
from monitoring.service_capture import requests, wait_for_requests
from performance.run_tests import stop_process, parse_iperf_throughput_mbps
from policy.processes import stop_group
from tests.lab_harness import ROOT, DEFAULT_RYU, require, command
from tests.expanded_harness import lab, health_processes, host_command, interface_names


def wait(predicate, timeout=20, label='Phase 3 readiness'):
    end=time.monotonic()+timeout
    while not predicate():
        require(time.monotonic()<end,label+' timed out'); time.sleep(.1)


@contextmanager
def isolated_controller(backend, switch):
    """Interrupt a connection without removing its primary-controller row.

    Removing the last primary controller clears OVS flow tables even in secure
    mode. Retarget the existing rows to a reserved, non-listening local socket
    instead, then restore those same rows on every exit path.
    """
    refs=backend.ovs('get','Bridge',switch,'controller')
    ids=re.findall(r'[0-9a-f]{8}-[0-9a-f-]{27}',refs)
    rows={row['_uuid']:row['target'] for row in backend.table('Controller') if row['_uuid'] in ids}
    require(bool(ids) and len(rows)==len(ids),'Missing controller rows for isolation: '+switch)
    with socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1',0))  # Reserve the port without listening.
        target='tcp:127.0.0.1:%d'%unavailable.getsockname()[1]
        try:
            for row in rows: backend.ovs('set','Controller',row,'target='+json.dumps(target))
            wait(lambda:not backend.connected(switch),label='Controller isolation: '+switch)
            yield {'controller_rows':rows,'isolated_target':target}
        finally:
            for row, original in rows.items(): backend.ovs('set','Controller',row,'target='+json.dumps(original))
            wait(lambda:backend.connected(switch),30,'Controller restoration: '+switch)


@contextmanager
def servers(net, inv, directory):
    owned=[]; logs=[]
    try:
        for name, service in catalog(inv.districts).items():
            status=directory/(name+'_server.json'); log=(directory/(name+'_server.log')).open('w'); logs.append(log)
            process=net[service['target']].popen([sys.executable,'-m','monitoring.service_probe','server',
                '--protocol',service['protocol'],'--server-port',str(service['server_port']),'--status',str(status.resolve())],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            owned.append((name,process))
            wait(lambda: status.exists() and process.poll() is None)
        yield dict(owned)
    finally:
        for _,process in reversed(owned): stop_process(process)
        for log in logs: log.close()
        result={name:p.poll() for name,p in owned}
        (directory/'service_cleanup.json').write_text(json.dumps(result,indent=2))
        require(all(code==0 for code in result.values()),'Service process cleanup failure')


def check(net, inv, directory, label, service, allowed, queue_location=None):
    source=service['source']; target=service['target']; src=inv.ENDPOINTS[source]; dst=inv.ENDPOINTS[target]
    if allowed:
        out,err,code=net[source].pexec(['ping','-n','-q','-c','1','-W','1',dst.ip])
        (directory/(label+'_arp_warmup.txt')).write_text(out+err)
        require(code==0,'Permitted diagnostic/address warmup failed: '+label)
    # Force forbidden IP attempts onto the network even when registration would
    # otherwise deny ARP. Never accept a local delivery error as policy denial.
    if not allowed:
        gateway=inv.SITES[src.site]
        host_command(net[source],['ip','neigh','replace',gateway.gateway,'lladdr',gateway.gateway_mac,'nud','permanent','dev',net[source].application_interface])
    path=directory/(label+'.pcap'); result_path=directory/(label+'.json')
    def queue_count():
        switch,port,number=queue_location
        output=command('ovs-ofctl','-O','OpenFlow13','queue-stats',switch,str(port),str(number))
        value=re.search(r'queue\s+%d:\s+bytes=(\d+)'%number,output)
        require(value is not None,'Missing selected queue counters')
        return int(value[1]),output
    before=queue_count() if queue_location is not None else None
    # -U flushes the output file, but does not disable libpcap's kernel-side
    # batching. Immediate mode is also needed before short captures are stopped.
    capture=net[target].popen(['tcpdump','--immediate-mode','-U','-n','-i',net[target].application_interface,'-w',str(path.resolve()),
        'ip and src host %s and dst host %s and dst port %d'%(src.ip,dst.ip,service['server_port'])],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    try:
        wait(lambda:path.exists() and path.stat().st_size>=24 and capture.poll() is None,8)
        host_command(net[source],[sys.executable,'-m','monitoring.service_probe','client','--protocol',service['protocol'],
            '--source',src.ip,'--target',dst.ip,'--client-port',str(service['client_port']),'--server-port',str(service['server_port']),
            '--count','100','--output',str(result_path.resolve())])
        if allowed:
            wait_for_requests(path,src.ip,dst.ip,service['protocol'],service['client_port'],service['server_port'],100)
        time.sleep(.2)
    finally:
        stop_process(capture); _,err=capture.communicate(timeout=3)
        (directory/(label+'_capture.log')).write_text(err)
    require(re.search(r'\b0 packets dropped by kernel\b',err),'Receiver capture loss/unverified')
    result=json.loads(result_path.read_text()); evidence=requests(path,src.ip,dst.ip,service['protocol'],service['client_port'],service['server_port'])
    (directory/(label+'_receiver.json')).write_text(json.dumps(evidence,indent=2))
    require(result['attempts']==100 and result['errors']==0,'Incomplete/local-error application attempts: '+label)
    require(result['received']==(100 if allowed else 0),'Application result mismatch: '+label)
    require(evidence['requests']==(100 if allowed else 0) and evidence['duplicates']==0,
            'Receiver request leak/missing/duplicate: %s (expected=%d, captured=%d, duplicates=%d)'%
            (label,100 if allowed else 0,evidence['requests'],evidence['duplicates']))
    if not allowed and service['protocol']=='tcp': require(evidence['tcp_syns']==0,'Forbidden SYN reached receiver: '+label)
    if before is not None:
        after=queue_count()
        (directory/(label+'_queue.json')).write_text(json.dumps({'location':queue_location,'before':before,'after':after},indent=2))
        require(after[0]>before[0],'Application did not use selected queue: '+label)


def automatic_recovery(net, inv, directory, session, manager):
    services=catalog(inv.districts)
    decision=manager.deployment.state['current']['decisions']['district_coordination']
    route=decision['forward']
    edges=list(zip([s for s,_,_ in route],[s for s,_,_ in route][1:]))
    failed=next((a,b) for a,b in edges if a.startswith('bb') and b.startswith('bb'))
    watch_log=(directory/'watch.log').open('w')
    watcher=subprocess.Popen([sys.executable,'-m','policy.operator','--state-dir',str(session),'watch'],cwd=ROOT,
                             stdout=watch_log,stderr=watch_log,start_new_session=True)
    print('Checking actual application recovery (five-second limit)...',flush=True)
    try:
        started=time.monotonic(); net.configLinkStatus(*failed,'down')
        recovery=directory/'automatic_probe.json'; coord=services['district_coordination']; attempts=[]
        received=False; elapsed=time.monotonic()-started
        while time.monotonic()-started<5:
            require(watcher.poll() is None,'Automatic policy watcher exited; inspect watch.log')
            host_command(net[coord['source']],[sys.executable,'-m','monitoring.service_probe','client','--protocol','udp',
                '--source',inv.ENDPOINTS[coord['source']].ip,'--target',inv.ENDPOINTS[coord['target']].ip,
                '--client-port',str(coord['client_port']),'--server-port',str(coord['server_port']),'--count','1','--output',str(recovery.resolve())])
            result=json.loads(recovery.read_text()); elapsed=time.monotonic()-started
            attempts.append({'completion_sec':elapsed,'probe':result})
            if result['received']==1 and result['errors']==0:
                received=True; break
        (directory/'automatic_recovery.json').write_text(json.dumps({'recovery_probe_completion_sec':elapsed,
            'failed_link':failed,'attempts':attempts},indent=2))
        require(received and elapsed<=5,'Automatic application recovery exceeded five seconds; inspect automatic_recovery.json')
        def rerouted():
            data=json.loads((session/'deployment.json').read_text())['deployment']; current=data['current']
            if data['status'] in ('partial','queue_partial'):
                raise AssertionError('Automatic policy reconciliation failed: '+str(data.get('switches')))
            if data['status']!='verified' or current is None: return False
            nodes=[s for s,_,_ in current['decisions']['district_coordination'].get('forward',[])]
            return bool(nodes) and 'bb1' not in nodes and not any((a,b)==failed or (b,a)==failed for a,b in zip(nodes,nodes[1:]))
        # The application recovery budget is above; full twenty-switch readback
        # is a separate management check, not the first application probe.
        wait(rerouted,60,'Automatic policy reconciliation/readback')
        finished=time.monotonic()-started
        data=json.loads((directory/'automatic_recovery.json').read_text()); data['full_readback_sec']=finished
        (directory/'automatic_recovery.json').write_text(json.dumps(data,indent=2))
        check(net,inv,directory,'automatic_link_down_coordination',services['district_coordination'],True)
    finally:
        stop_group(watcher)
        watch_log.close(); net.configLinkStatus(*failed,'up')
    restored=next(l for l in inv.LINKS if {l.a,l.b}==set(failed))
    wait(lambda:all(p in topology(session,inv).up for p in ((restored.a,restored.pa),(restored.b,restored.pb))),
         label='Failed link restoration')
    return Manager(session,inv)


def exercise(net, inv, directory, session, manager, recovery_only=False, updates_only=False):
    services=catalog(inv.districts); reports=[]
    with servers(net,inv,directory) as responders:
        if updates_only:
            cfg=initial_config(inv); cfg['constraints']['district_coordination']={'avoid_switches':['bb1']}
            manager.stage(cfg,topology(session,inv)); require(manager.resume()['status']=='verified','Update baseline failed')
            check(net,inv,directory,'constrained_coordination',services['district_coordination'],True)
            update_checks(net,inv,directory,session,manager,responders)
            return
        if recovery_only:
            cfg=initial_config(inv); cfg['constraints']['district_coordination']={'avoid_switches':['bb1']}
            manager.stage(cfg,topology(session,inv)); require(manager.resume()['status']=='verified','Recovery baseline failed')
            check(net,inv,directory,'constrained_coordination',services['district_coordination'],True)
            automatic_recovery(net,inv,directory,session,manager)
            return
        for profile in PROFILES:
            print('Checking profile: '+profile,flush=True)
            cfg=manager.config(); cfg['profile']=profile
            started=time.monotonic(); manager.stage(cfg,topology(session,inv)); status=manager.resume()
            require(status['status']=='verified','Profile deployment did not fully verify')
            for switch, flows in status['current']['flows'].items(): manager.backend.verify(switch,flows)
            qstate=manager.backend.queue_state()
            require(all(manager.backend.queue_matches(p,q,qstate,True) for p,q in status['current']['queues'].items()),'Profile queue readback mismatch')
            reports.append({'profile':profile,'version':status['current']['version'],'deployment_sec':time.monotonic()-started,
                            'decisions':status['current']['decisions']})
            (directory/'profiles.json').write_text(json.dumps(reports,indent=2))
            qdiscs=json.loads(command('tc','-s','-j','qdisc','show'))
            owned=interface_names(inv)
            (directory/(profile+'_qdiscs.json')).write_text(json.dumps([q for q in qdiscs if q.get('dev') in owned],indent=2))
            for name in cfg['permissions']:
                decision=status['current']['decisions'][name]
                allowed=decision['status'] in ('admitted','reduced')
                location=None
                if allowed:
                    s,_,port=decision['forward'][-1]
                    location=(s,port,status['current']['version']*64+services[name]['queue_index']*2)
                check(net,inv,directory,profile+'_'+name,services[name],allowed,location)
        # Prepared endpoint: registration and permission are independently tested.
        service=services['a_reporting']
        check(net,inv,directory,'report_unregistered',service,False)
        cfg=manager.config(); cfg['registered'].append('af3'); manager.stage(cfg,topology(session,inv)); require(manager.resume()['status']=='verified','Registration deployment failed')
        check(net,inv,directory,'report_registered_without_permission',service,False)
        cfg=manager.config(); cfg['permissions'].append('a_reporting'); manager.stage(cfg,topology(session,inv)); require(manager.resume()['status']=='verified','Permission deployment failed')
        check(net,inv,directory,'report_authorized',service,True)
        wrong=dict(services['a_medical'],source='af3')
        check(net,inv,directory,'report_unrelated_service',wrong,False)
        invalid=manager.config(); invalid['rates'].update({'a_medical':[40000000]*3,'a_reporting':[40000000]*3})
        before_version=manager.deployment.state['current']['version']
        try:
            manager.stage(invalid,topology(session,inv))
        except ValueError as exc:
            (directory/'rejected_capacity.json').write_text(json.dumps({'error':str(exc),'version_unchanged':before_version},indent=2))
        else: raise AssertionError('Infeasible joint medical allocations were accepted')
        require(manager.deployment.state['current']['version']==before_version,'Invalid request changed active version')
        # A claimed trusted UDP port from a different legitimate identity is denied.
        wrong=dict(services['a_dispatch'],source='as2')
        check(net,inv,directory,'wrong_identity_same_service_port',wrong,False)
        # Both address fields copied from an unrelated attachment remain denied.
        attacker=net['as3']; interface=attacker.application_interface
        original=inv.ENDPOINTS['as3']; victim=inv.ENDPOINTS['af1']; gateway=inv.SITES[victim.site]
        from dataclasses import replace
        fake=build_inventory(inv.districts); fake.ENDPOINTS['as3']=replace(original,ip=victim.ip,mac=victim.mac,site=victim.site)
        try:
            host_command(attacker,['ip','-4','addr','flush','dev',interface])
            host_command(attacker,['ip','link','set','dev',interface,'address',victim.mac])
            host_command(attacker,['ip','addr','add',victim.ip+'/24','dev',interface])
            host_command(attacker,['ip','route','replace','default','via',gateway.gateway,'dev',interface])
            check(net,fake,directory,'wrong_attachment_mac_ip',dict(services['a_dispatch'],source='as3'),False)
        finally:
            host_command(attacker,['ip','link','set','dev',interface,'address',original.mac])
            host_command(attacker,['ip','-4','addr','flush','dev',interface])
            host_command(attacker,['ip','addr','add',original.ip+'/24','dev',interface])
            host_command(attacker,['ip','route','replace','default','via',inv.SITES[original.site].gateway,'dev',interface])
            host_command(attacker,['ip','neigh','flush','dev',interface])
        # Explicit bidirectional constraint is validated against live topology.
        cfg=manager.config(); cfg['constraints']['district_coordination']={'avoid_switches':['bb1']}
        manager.stage(cfg,topology(session,inv)); require(manager.resume()['status']=='verified','Constrained update failed')
        decision=manager.deployment.state['current']['decisions']['district_coordination']
        require(all('bb1' not in {s for s,_,_ in decision[k]} for k in ('forward','reverse')),'Avoided switch used')
        check(net,inv,directory,'constrained_coordination',services['district_coordination'],True)
        manager=automatic_recovery(net,inv,directory,session,manager)
        update_checks(net,inv,directory,session,manager,responders)


def update_checks(net, inv, directory, session, manager, responders):
    services=catalog(inv.districts)
    print("Checking checkpoint, isolated rollback and video cap...",flush=True)
    # Checkpoint + management-only isolation + rollback while isolated.
    saved=manager.deployment.state['current']; cfg=manager.config(); cfg['profile']='evacuation'
    manager.stage(cfg,topology(session,inv)); manager.resume(1)
    require(manager.deployment.state['status']=='paused','Checkpoint did not pause')
    require(bool(saved['flows']['bb2']),'Rollback control requires populated bb2 service rules')
    with isolated_controller(manager.backend,'bb2') as isolation:
        (directory/'controller_isolation.json').write_text(json.dumps(isolation,indent=2))
        # Prove that management isolation itself preserved the saved rules.
        manager.backend.verify('bb2',saved['flows']['bb2'],local=True)
        manager.resume(); require(manager.deployment.state['status']=='partial','Isolated update claimed success')
        (directory/'partial_update.json').write_text(json.dumps(manager.deployment.state,indent=2))
        manager.rollback(); require(manager.deployment.state['status']=='rollback_partial','Isolated state claimed verified')
        local={s:manager.backend.verify(s,f,local=(s=='bb2')) for s,f in saved['flows'].items()}
        (directory/'rollback_readback.json').write_text(json.dumps(local,indent=2))
        check(net,inv,directory,'service_during_management_isolation',services['a_dispatch'],True)
        check(net,inv,directory,'coordination_during_management_isolation',services['district_coordination'],True)
    require(net.waitConnected(timeout=30),'Policy control connection did not restore')
    wait(lambda:len(topology(session,inv).online)==20)
    require(manager.deployment.state['pending'] is None,'Abandoned update replay possible')
    # Verify an actual kernel cap, not just OVSDB intent. Offered video
    # stays above its declared 4 Mbps evacuation ceiling.
    cfg=manager.config(); cfg['profile']='evacuation'
    manager.stage(cfg,topology(session,inv)); require(manager.resume()['status']=='verified','Rate-test profile failed')
    video=services['a_video']; stop_process(responders['a_video'])
    server=net[video['target']].popen(['iperf','-s','-u','-p',str(video['server_port']),'-f','m'],
                                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    out=err=''; maximum=manager.deployment.state['current']['decisions']['a_video']['maximum_bps']/1000000
    try:
        wait(lambda:bool(host_command(net[video['target']],['ss','-H','-lun','sport','=',':%d'%video['server_port']]).strip()))
        out,err,code=net[video['source']].pexec(['iperf','-u','-c',inv.ENDPOINTS[video['target']].ip,
            '-p',str(video['server_port']),'-B',inv.ENDPOINTS[video['source']].ip+':'+str(video['client_port']),
            '-b','20m','-t','5','-f','m'])
        require(code==0,'Capped video generator failed')
    finally:
        stop_process(server); sout,serr=server.communicate(timeout=3)
        rate=parse_iperf_throughput_mbps(sout)
        (directory/'video_rate_cap.json').write_text(json.dumps({'client':out+err,'server':sout+serr,
            'receiver_mbps':rate,'maximum_mbps':maximum,'offered_mbps':20,'tolerance':1.25},indent=2))
    require(rate is not None and maximum*.25 <= rate <= maximum*1.25,'Actual video rate cap failed')


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--ryu-python',default=str(DEFAULT_RYU))
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--recovery-only',action='store_true',help='Focused diagnostic; does not satisfy the full Phase 3 gate')
    selection.add_argument('--updates-only',action='store_true',help='Checkpoint/isolated rollback/video cap diagnostic; not the full gate')
    args=parser.parse_args()
    if args.repetitions<1: parser.error('Positive repetitions required')
    if os.geteuid()!=0:
        print('Root required; no lab started. Use sudo python3 -m tests.phase3_harness --repetitions 3',file=sys.stderr)
        return 1
    inv=build_inventory()
    for repetition in range(args.repetitions):
        directory=ROOT/'monitoring/evidence/phase3-live'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        directory.mkdir(parents=True); session=directory/'state'; session.mkdir()
        (directory/'manifest.json').write_text(json.dumps({'repetition':repetition+1,'switches':20,'endpoints':24,'profiles':list(PROFILES),
            'application_attempts':100,'scope':('Focused updates/rollback/cap only; not the full Phase 3 gate' if args.updates_only else
            'Focused automatic recovery only; not the full Phase 3 gate' if args.recovery_only else
            'Phase 3 service/queue/update correctness; no Phase 4 demos or adaptive comparison')},indent=2))
        try:
            with lab(inv,args.ryu_python,directory,controller_app='policy_app.py',environment={'SDN_PHASE3_DIR':str(session)}) as (net,controller):
                wait(lambda:(session/'graph.json').exists() and len(topology(session,inv).online)==20)
                manager=Manager(session,inv)
                try:
                    with health_processes(net,inv,directory): exercise(net,inv,directory,session,manager,args.recovery_only,args.updates_only)
                finally:
                    errors=[]
                    for switch in inv.SWITCHES:
                        try: manager.backend.write(switch,[])
                        except Exception as exc: errors.append(switch+': '+str(exc))
                    manager.backend.cleanup()
                    (directory/'policy_cleanup.json').write_text(json.dumps({'errors':errors},indent=2))
                    require(not errors,'Policy cleanup readback failed')
            log=(directory/'controller.log').read_text()
            require(not any(s in log for s in ('Traceback','OPENFLOW_ERROR','PORT_INVENTORY_MISMATCH')),'Policy controller error')
            marker='UPDATES_PASS' if args.updates_only else 'RECOVERY_PASS' if args.recovery_only else 'PASS'
            (directory/marker).write_text('Focused updates/rollback/cap passed; full gate pending\n' if args.updates_only else
                                          'Focused automatic recovery passed; full gate pending\n' if args.recovery_only else
                                          'Phase 3 implemented service/queue/update checks passed\n')
        except Exception as exc:
            (directory/'failure.txt').write_text(str(exc)); print('Failed: %s; evidence: %s'%(exc,directory),file=sys.stderr); return 1
        print(('Phase 3 update check passed: ' if args.updates_only else 'Phase 3 recovery check passed: ' if args.recovery_only else 'Phase 3 repetition passed: ')+str(directory),flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
