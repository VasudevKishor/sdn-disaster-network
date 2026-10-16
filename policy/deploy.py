"""Versioned, checkpointable deployment with honest per-switch status."""
from copy import deepcopy


class Deployment:
    def __init__(self, backend, state=None, save=None, validate=None):
        self.backend = backend
        self.save = save or (lambda:None)
        self.validate = validate or (lambda:None)
        self.state = state or {'status':'empty','current':None,'pending':None,'history':[], 'switches':{}}

    def stage(self, candidate, order=None):
        if self.state['pending'] is not None:
            raise ValueError('Resolve pending deployment before another update')
        current = self.state['current']
        switches = list(candidate['flows'])
        if order is not None and (len(order)!=len(set(order)) or set(order)!=set(switches)):
            raise ValueError('Order must name every affected switch exactly once')
        merged = deepcopy(candidate['queues'])
        if current:
            for port, cfg in current['queues'].items():
                if port.rsplit('-eth',1)[0] not in switches: continue
                target=merged.setdefault(port, deepcopy(cfg))
                for number, queue in cfg['queues'].items(): target['queues'].setdefault(number,deepcopy(queue))
        self.state.update(status='staged', pending=deepcopy(candidate), order=order or switches,
                          remaining=list(order or switches), queue_ready=False,
                          switches={s:{'status':'pending'} for s in switches})
        self.save()
        try:
            self.backend.prepare(merged)
        except Exception as exc:
            self.state.update(status='queue_partial',error=str(exc)); self.save(); raise
        self.state['queue_ready']=True; self.save()

    def resume(self, pause_after=None):
        candidate=self.state['pending']
        if candidate is None: raise ValueError('No staged deployment')
        if not self.state.get('queue_ready'): raise ValueError('Queue preparation did not verify; rollback required')
        self.state['status']='applying'; completed=0
        # A checkpoint's old readback is not proof after an outage/restart.
        for switch in candidate['flows']:
            if switch in self.state['remaining']: continue
            try:
                self.validate(); self.backend.verify(switch,candidate['flows'][switch])
            except Exception as exc:
                self.state['remaining'].insert(0,switch)
                self.state['switches'][switch]={'status':'failed','error':str(exc)}
                self.state['status']='partial'; self.save(); return self.state
        for switch in list(self.state['remaining']):
            self.state['switches'][switch]={'status':'applying'}; self.save()
            try:
                self.validate()
                current=self.state['current']
                if current and current['flows'].get(switch)==candidate['flows'][switch]:
                    try: self.backend.verify(switch,candidate['flows'][switch])
                    except RuntimeError: self.backend.write(switch,candidate['flows'][switch])
                else:
                    self.backend.write(switch,candidate['flows'][switch])
            except Exception as exc:
                self.state['switches'][switch]={'status':'failed','error':str(exc)}
                self.state['status']='partial'
                self.save()
                return self.state
            self.state['switches'][switch]={'status':'verified','version':candidate['version']}
            self.state['remaining'].remove(switch); completed+=1
            self.save()
            if pause_after is not None and completed>=pause_after and self.state['remaining']:
                self.state['status']='paused'; self.save(); return self.state
        if self.state['current'] and self.state['current']['version'] != candidate['version']:
            self.state['history'].append(self.state['current'])
        self.state['current']=candidate; self.state['pending']=None
        self.state['status']='degraded' if candidate.get('unavailable_switches') else 'verified'
        self.save()
        return self.state

    def rollback(self, version=None):
        recovering=self.state['pending'] or self.state['status'] in ('rolling_back','rollback_partial')
        target = self.state['current'] if recovering else next(
            (p for p in reversed(self.state['history']) if version is None or p['version']==version),None)
        if target is None: raise ValueError('No saved rollback target')
        versions=[p for p in (self.state['current'],self.state['pending']) if p]
        merged=deepcopy(target['queues'])
        for snapshot in versions:
            for port,cfg in snapshot['queues'].items():
                q=merged.setdefault(port,deepcopy(cfg))
                for number,value in cfg['queues'].items(): q['queues'].setdefault(number,deepcopy(value))
        # Abandon V2 before writes: reconnection must never resume it.
        self.state['pending']=None; self.state['remaining']=[]; self.state['current']=target
        self.state['status']='rolling_back'; states={}
        self.state['switches']={s:{'status':'unverified','version':target['version']} for s in target['flows']}
        self.save()
        try:
            self.backend.prepare(merged)
        except Exception as exc:
            self.state.update(status='rollback_partial',error='Queue recovery failed: '+str(exc)); self.save(); return self.state
        for switch, flows in target['flows'].items():
            try:
                self.backend.write(switch,flows)
                states[switch]={'status':'verified','version':target['version']}
            except Exception as exc:
                states[switch]={'status':'unverified','version':target['version'],'error':str(exc)}
        self.state['switches']=states
        self.state['status']='rolled_back' if all(v['status']=='verified' for v in states.values()) else 'rollback_partial'
        self.save()
        return self.state
