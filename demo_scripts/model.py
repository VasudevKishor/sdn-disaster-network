"""Pure demo configuration, candidate preparation and measured acceptance."""
from copy import deepcopy
from config.incident import initial_config, catalog, HEADROOM
from policy.model import candidates, plan

DEMOS=('congestion','routing','onboarding','rollback')
URGENT_SIZE, URGENT_RATE, DEADLINE_MS = 8192, 250, 250
MEASURE_SECONDS=5


def congestion_config(inv):
    config=initial_config(inv)
    # Normal urgent traffic can borrow up to 20 Mbps. Routine bulk has a
    # legitimate 38 Mbps reservation: demand changes, not admission violations.
    config['rates']={'a_medical':[1000000,2000000,20000000],
                     'a_bulk':[38000000,38000000,50000000]}
    return config


def corrected_congestion(config):
    result=deepcopy(config); result['profile']='evacuation'
    result['rates'].pop('a_bulk',None)
    return result


def urgent_metrics(events, start, end, deadline_ms=DEADLINE_MS):
    if end<=start: raise ValueError('Measurement window must be positive')
    offers={}; replies={}
    for row in events:
        if row['event']=='offer' and start<=row['monotonic']<end:
            if row['sequence'] in offers: raise ValueError('Duplicate offered sequence')
            offers[row['sequence']]=row['monotonic']
        elif row['event']=='reply':
            replies.setdefault(row['sequence'],[]).append(row['monotonic'])
    rtts=[]; on_time=0; duplicates=0
    for sequence, offered in offers.items():
        times=replies.get(sequence,[]); duplicates+=max(0,len(times)-1)
        if times:
            elapsed=(min(times)-offered)*1000
            if elapsed<0: raise ValueError('Reply predates offer')
            rtts.append(elapsed); on_time+=elapsed<=deadline_ms
    rtts.sort(); offered=len(offers); duration=end-start
    p95=rtts[max(0,(95*len(rtts)+99)//100-1)] if rtts else None
    return {'offered':offered,'received':len(rtts),'on_time':on_time,
            'on_time_fraction':on_time/offered if offered else 0,
            'delivery_fraction':len(rtts)/offered if offered else 0,'p95_ms':p95,
            'duplicates':duplicates,'window_seconds':duration,'offered_per_second':offered/duration,
            'deadline_ms':deadline_ms,'goals_met':bool(offered and on_time/offered>=.95 and p95 is not None
                and p95<=deadline_ms and duplicates==0)}


def prepare_route_problem(config, state):
    """Find a real primary fault, ordinary backup, and distinct avoid option."""
    service=catalog(state.inventory.districts)['district_coordination']
    before=plan(config,state,1,strict=False)['decisions']['district_coordination']
    if not before.get('forward'): raise ValueError('No usable coordination route for the prepared fault')
    nodes=[s for s,_,_ in before['forward']]
    for a,b in zip(nodes,nodes[1:]):
        if not (a.startswith('bb') and b.startswith('bb')): continue
        link=next(l for l in state.inventory.LINKS if {l.a,l.b}=={a,b})
        failed=deepcopy(state); failed.up-={(link.a,link.pa),(link.b,link.pb)}
        backup=plan(config,failed,1,strict=False)['decisions']['district_coordination']
        for avoided in [s for s,_,_ in backup.get('forward',[]) if s.startswith('bb')]:
            paths=candidates(failed,service['source'],service['target'],{'avoid_switches':[avoided]})
            if paths:
                cfg=deepcopy(config); cfg['constraints']['district_coordination']={'avoid_switches':[avoided]}
                try: selected=plan(cfg,failed,1)['decisions']['district_coordination']
                except ValueError: continue
                if selected['status'] in ('admitted','reduced'):
                    return {'failed_link':[a,b],'avoid_switch':avoided,'ordinary_backup':backup['forward']}
    raise ValueError('No meaningful primary-fault/ordinary-backup/avoid-switch task exists')


def route_options(config, state, avoided):
    service=catalog(state.inventory.districts)['district_coordination']
    base=plan(config,state,1,strict=False); reserved=dict(base['reserved_bps'])
    own=base['decisions'].get('district_coordination',{})
    if own.get('allocation_bps'):
        for direction in ('forward','reverse'):
            for switch,_,port in own[direction]: reserved['%s-eth%d'%(switch,port)]-=own['allocation_bps']
    options=[]
    for index,(forward,reverse) in enumerate(candidates(state,service['source'],service['target'],
                                                      {'avoid_switches':[avoided]}),1):
        residual=min(base['capacities']['%s-eth%d'%(s,p)]-HEADROOM-reserved.get('%s-eth%d'%(s,p),0)
                     for route in (forward,reverse) for s,_,p in route)
        options.append({'candidate':index,'switches':[s for s,_,_ in forward],
                        'forward':forward,'reverse':reverse,'residual_bps':residual,
                        'capacity_feasible':residual>=1000000})
    return options
