"""Local operator: python3 -m policy.operator --state-dir DIR COMMAND.

Mutations require root. `preview --assume-up` is an explicitly offline model.
No HTTP management API, remote shell, or fabricated live state is provided.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import subprocess
from pathlib import Path
import time
import uuid
from config.expanded import build_inventory
from config.phase2 import ForwardingState, Attachment
from config.incident import initial_config, catalog, PROFILES
from policy.model import plan, candidates, HEADROOM
from policy.backend import OVSBackend, execute
from policy.deploy import Deployment


def atomic(path, data):
    temporary=path.with_suffix('.tmp'); temporary.write_text(json.dumps(data,separators=(',',':'))+'\n'); temporary.replace(path)


def topology(directory, inventory, assume_up=False):
    state=ForwardingState(inventory)
    if assume_up:
        state.online=set(inventory.SWITCHES)
        state.up={(s,p) for s in inventory.SWITCHES for p in inventory.expected_ports(s)}
        return state
    data=json.loads((directory/'graph.json').read_text())
    if not 0 <= time.monotonic()-data['monotonic'] < 2:
        raise ValueError('Topology heartbeat stale; retained rules are not proof of current reachability')
    if data['districts']!=inventory.districts or set(data['online'])-set(inventory.SWITCHES):
        raise ValueError('Graph inventory mismatch')
    state.online=set(data['online']); state.up={tuple(p) for p in data['up']}
    state.connections=data.get('connections',{})
    if any(s not in inventory.SWITCHES or p not in inventory.expected_ports(s) for s,p in state.up):
        raise ValueError('Unknown graph port')
    for name, a in data['active'].items():
        attachment=Attachment(*a)
        if name not in inventory.ENDPOINTS or attachment not in inventory.ENDPOINTS[name].attachments:
            raise ValueError('Untrusted observed attachment')
        state.active[name]=attachment
    return state


def graph_key(state):
    attachments=[(n,state.attachment(n)) for n in sorted(state.inventory.ENDPOINTS)]
    return hashlib.sha256(repr((sorted(state.online),sorted(state.up),attachments,
                              sorted(getattr(state,'connections',{}).items()))).encode()).hexdigest()


def observations(state):
    return {'up':sorted([list(p) for p in state.up]), 'attachments':{
        n:([a.switch,a.port,a.host_port] if (a:=state.attachment(n)) is not None else None)
        for n in sorted(state.inventory.ENDPOINTS)}}


class Manager:
    def __init__(self, directory, inventory, backend=None):
        self.directory, self.inventory = directory, inventory
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/'deployment.json'
        self.saved=json.loads(path.read_text()) if path.exists() else {'owner_id':str(uuid.uuid4()),'next_version':1,'deployment':None}
        self.backend=backend or OVSBackend(inventory,self.saved['owner_id'])
        self.deployment=Deployment(self.backend,self.saved['deployment'],self.save,self.validate_pending)

    def validate_pending(self):
        pending=self.deployment.state['pending']
        if pending and (self.directory/'graph.json').exists():
            state=topology(self.directory,self.inventory)
            if observations(state)!=pending['observations']:
                raise RuntimeError('Physical ports/attachments changed during update; rollback/replan required')
            if getattr(state,'connections',{})!=pending.get('connections',{}):
                raise RuntimeError('Controller/switch restarted during update; rollback/re-stage required')

    def save(self):
        self.saved['deployment']=self.deployment.state
        atomic(self.directory/'deployment.json',self.saved)
        current=self.deployment.state['current']
        atomic(self.directory/'registry.json',{'registered': (current['config'] if current else initial_config(self.inventory))['registered']})

    def config(self):
        current=self.deployment.state['current']
        return json.loads(json.dumps(current['config'] if current else initial_config(self.inventory)))

    def candidate(self, config, state, strict=True):
        current=self.deployment.state['current']
        version=current['version'] if not strict and current else self.saved['next_version']
        if strict or current is None: self.saved['next_version']+=1
        candidate=plan(config,state,version,strict)
        candidate['graph_key']=graph_key(state)
        candidate['observations']=observations(state)
        candidate['connections']=getattr(state,'connections',{})
        if not strict:
            candidate['unavailable_switches']=sorted(set(self.inventory.SWITCHES)-state.online)
            candidate['flows']={s:f for s,f in candidate['flows'].items() if s in state.online}
        return candidate

    def stage(self, config, state, order=None):
        candidate=self.candidate(config,state)
        if order is None:
            order=sorted(candidate['flows'],key=lambda s:({'backbone':0,'aggregation':1,'access':2}[self.inventory.SWITCH_ROLES[s]],s))
        self.deployment.stage(candidate,order)
        self.save(); return self.deployment.state

    def resume(self, pause_after=None):
        result=self.deployment.resume(pause_after); self.save(); return result

    def repair(self, state):
        """Restore changed paths before auditing switches with unchanged rules."""
        current=self.deployment.state['current']
        candidate=self.candidate(self.config(),state,False)
        order=sorted(candidate['flows'],key=lambda s:current['flows'].get(s)==candidate['flows'][s])
        self.deployment.stage(candidate,order)
        return self.resume()

    def rollback(self, version=None):
        result=self.deployment.rollback(version); self.save(); return result


@contextmanager
def locked(directory):
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'operator.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def change(config, args, inventory):
    if args.profile: config['profile']=args.profile
    if args.command in ('register','unregister'):
        if args.endpoint not in inventory.ENDPOINTS: raise ValueError('Select a configured endpoint')
        values=set(config['registered'])
        if args.command=='register': values.add(args.endpoint)
        else:
            if args.endpoint in inventory.HEALTH_TARGETS or args.endpoint in inventory.HEALTH_TARGETS.values():
                raise ValueError('Health-critical endpoints cannot be unregistered in this lab')
            values.discard(args.endpoint)
        config['registered']=sorted(values)
    if args.command in ('grant','deny','rates','constrain'):
        if args.service not in catalog(inventory.districts): raise ValueError('Select a configured exact service')
        if args.command in ('grant','deny'):
            permissions=set(config['permissions'])
            if args.command=='grant': permissions.add(args.service)
            else: permissions.discard(args.service)
            config['permissions']=sorted(permissions)
        if args.command=='rates': config['rates'][args.service]=args.rates
        if args.command=='constrain':
            config['constraints'][args.service]={'avoid_switches':args.avoid_switch,
                'avoid_links':[s.split(',') for s in args.avoid_link], **({'via':args.via.split(',')} if args.via else {})}
            if not args.avoid_switch and not args.avoid_link and not args.via:
                config['constraints'].pop(args.service,None)
    return config


def summary(result):
    if 'flows' in result and 'config' in result:
        return {'version':result['version'],'profile':result['config']['profile'],
                'registered':result['config']['registered'],'permissions':result['config']['permissions'],
                'decisions':result['decisions'],'flow_count':sum(map(len,result['flows'].values())),
                'queue_ports':len(result['queues']), 'unavailable_switches':result.get('unavailable_switches',[]),
                **({'scope':result['scope']} if 'scope' in result else {})}
    if 'current' in result:
        return {**{k:v for k,v in result.items() if k not in ('current','pending','history')},
                'current':summary(result['current']) if result['current'] else None,
                'pending':summary(result['pending']) if result['pending'] else None,
                'saved_versions':[p['version'] for p in result['history']],
                'verification_scope':'saved journal; use verify for current switch/queue readback'}
    if 'flows' in result:
        return {k:v for k,v in result.items() if k not in ('flows','queues')} | {
            'flow_counts':{s:sum('cookie=' in line for line in text.splitlines()) for s,text in result['flows'].items()},
            'queue_database_rows':[len(table) for table in result['queues']], 'detail_hint':'Use --details for actual flows/queue rows'}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path,default=Path('monitoring/phase3-session'))
    parser.add_argument('--districts',type=int,choices=(1,2),default=2)
    parser.add_argument('command',choices=('catalog','status','inspect','preview','apply','stage','resume','rollback','verify','register','unregister','grant','deny','rates','constrain','candidates','watch','teardown'))
    parser.add_argument('--profile'); parser.add_argument('--service'); parser.add_argument('--endpoint')
    parser.add_argument('--rates',type=int,nargs=3); parser.add_argument('--avoid-switch',action='append',default=[])
    parser.add_argument('--avoid-link',action='append',default=[]); parser.add_argument('--via')
    parser.add_argument('--order'); parser.add_argument('--pause-after',type=int); parser.add_argument('--version',type=int)
    parser.add_argument('--assume-up',action='store_true'); parser.add_argument('--local-readback',action='store_true')
    parser.add_argument('--details',action='store_true',help='Include complete rule/queue snapshots')
    args=parser.parse_args(); inv=build_inventory(args.districts)
    if args.assume_up and args.command!='preview': parser.error('--assume-up is restricted to offline preview')
    readonly={'catalog','preview','candidates','status','inspect','verify'}
    if args.command not in readonly and os.geteuid()!=0: parser.error('Policy mutations require root')
    if args.pause_after is not None and args.pause_after<1: parser.error('Positive checkpoint count required')
    try:
        if args.command=='catalog': result={'services':catalog(inv.districts),'profiles':PROFILES}
        elif args.command=='preview' and args.assume_up:
            result=plan(change(initial_config(inv),args,inv),topology(args.state_dir,inv,True))
            result['scope']='offline assumed topology; not installed'
        elif args.command=='watch':
            last_key=None; changed=0
            while True:
                try:
                    with locked(args.state_dir):
                        manager=Manager(args.state_dir,inv); state=topology(args.state_dir,inv)
                        key=graph_key(state)
                        if key!=last_key: last_key=key; changed=time.monotonic()
                        elif time.monotonic()-changed>=1 and manager.deployment.state['pending'] is None:
                            current=manager.deployment.state['current']
                            status=manager.deployment.state['status']
                            supported=status in ('verified','rolled_back','degraded') or (status=='rollback_partial' and current
                                and set(current['flows']) <= state.online)
                            if supported and current and current.get('graph_key')!=key:
                                manager.repair(state)
                except Exception as exc:
                    atomic(args.state_dir/'watch_status.json',{'monotonic':time.monotonic(),'error':str(exc),'retained':'last installed state; not newly verified'})
                time.sleep(.25)
        else:
            with locked(args.state_dir):
                manager=Manager(args.state_dir,inv)
                try:
                    if args.command=='status': result=manager.deployment.state
                    elif args.command=='inspect':
                        state=topology(args.state_dir,inv)
                        result={'online':sorted(state.online),'up_ports':sorted(state.up),
                                'attachments':{n:str(state.attachment(n)) for n in inv.ENDPOINTS},
                                'identities':{n:{'ip':e.ip,'mac':e.mac,'registered':n in manager.config()['registered']} for n,e in inv.ENDPOINTS.items()},
                                'queues':manager.backend.queue_state(), 'flows':{}}
                        for switch in sorted(state.online):
                            result['flows'][switch]=execute(['ovs-ofctl','-O','OpenFlow13','--timeout=5','dump-flows',switch])
                    elif args.command=='resume':
                        topology(args.state_dir,inv)
                        result=manager.resume(args.pause_after)
                    elif args.command=='rollback': result=manager.rollback(args.version)
                    elif args.command=='teardown':
                        manager.deployment.state['status']='tearing_down'; manager.save()
                        try:
                            for s in inv.SWITCHES: manager.backend.write(s,[])
                            manager.backend.cleanup()
                        except Exception:
                            manager.deployment.state['status']='teardown_partial'; raise
                        state=manager.deployment.state
                        if state['current']: state['history'].append(state['current'])
                        state.update(current=None,pending=None,status='removed',remaining=[],switches={})
                        result={'status':'owned state removed'}
                    elif args.command=='verify':
                        current=manager.deployment.state['current']
                        if current is None: raise ValueError('No applied snapshot')
                        result={s:manager.backend.verify(s,f,args.local_readback) for s,f in current['flows'].items()}
                        queues=manager.backend.queue_state()
                        if not all(manager.backend.queue_matches(p,q,queues,True) for p,q in current['queues'].items()):
                            raise RuntimeError('Current queue rates/ownership did not verify')
                        result['queues']={'verified':True,'inspection':'read-only local lab' if args.local_readback else 'OVSDB readback'}
                    else:
                        state=topology(args.state_dir,inv)
                        config=change(manager.config(),args,inv)
                        if args.command=='candidates':
                            service=catalog(inv.districts).get(args.service)
                            if service is None: raise ValueError('Unknown service')
                            paths=candidates(state,service['source'],service['target'],
                                {'avoid_switches':args.avoid_switch,'avoid_links':[s.split(',') for s in args.avoid_link]})
                            base=plan(config,state,manager.saved['next_version'],strict=False)
                            reserved=dict(base['reserved_bps']); own=base['decisions'].get(args.service,{})
                            if own.get('allocation_bps'):
                                for direction in ('forward','reverse'):
                                    for s,_,p in own[direction]: reserved['%s-eth%d'%(s,p)]-=own['allocation_bps']
                            result={'service':args.service,'paths':[{'forward':f,'reverse':r,'residual_bps':min(
                                base['capacities']['%s-eth%d'%(s,p)]-HEADROOM-reserved.get('%s-eth%d'%(s,p),0)
                                for route in (f,r) for s,_,p in route)} for f,r in paths]}
                        elif args.command=='preview': result=plan(config,state,manager.saved['next_version'])
                        else:
                            manager.stage(config,state,args.order.split(',') if args.order else None)
                            result=manager.deployment.state if args.command=='stage' else manager.resume(args.pause_after)
                finally:
                    if args.command not in readonly: manager.save()
        print(json.dumps(result if args.details else summary(result),indent=2))
        return 0 if result.get('status') not in ('partial','rollback_partial') else 1
    except (OSError,ValueError,RuntimeError,KeyError,subprocess.SubprocessError) as exc:
        print(json.dumps({'error':str(exc)})); return 1


if __name__=='__main__': raise SystemExit(main())
