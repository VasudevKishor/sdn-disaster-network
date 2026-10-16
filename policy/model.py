"""Pure deterministic admission, directed capacity accounting and flow compilation."""
from collections import defaultdict, deque
from copy import deepcopy
from config.incident import PROFILES, PRIORITY, ESSENTIAL, HEADROOM, catalog

PREFIX, MASK = 0xD153000000000000, 0xffff000000000000


def validate(config, inventory):
    if set(config) != {"profile", "registered", "permissions", "rates", "constraints"}:
        raise ValueError("Unknown/missing policy fields")
    if config["profile"] not in PROFILES:
        raise ValueError("Unsupported profile")
    services = catalog(inventory.districts)
    for key, allowed in (("registered", inventory.ENDPOINTS), ("permissions", services)):
        values = config[key]
        if not isinstance(values, list) or len(values) != len(set(values)) or set(values) - set(allowed):
            raise ValueError("Unknown or duplicate " + key)
    for name, rates in config["rates"].items():
        if name not in services or not isinstance(rates, list) or len(rates) != 3:
            raise ValueError("Unknown service/rate tuple")
        if any(type(n) is not int for n in rates) or not 0 < rates[0] <= rates[1] <= rates[2] <= 50000000:
            raise ValueError("Rates require 0 < floor <= allocation <= maximum <= 50 Mbps")
    edges = {tuple(sorted((l.a, l.b))) for l in inventory.LINKS}
    for name, constraint in config["constraints"].items():
        if name not in services or set(constraint) - {"avoid_switches", "avoid_links", "via"}:
            raise ValueError("Invalid service constraint")
        if set(constraint.get("avoid_switches", [])) - set(inventory.SWITCHES):
            raise ValueError("Unknown avoided switch")
        for link in constraint.get("avoid_links", []):
            if len(link) != 2 or tuple(sorted(link)) not in edges:
                raise ValueError("Unknown avoided link")
        via = constraint.get("via")
        if via is not None and (not isinstance(via, list) or not via or len(via) != len(set(via)) or set(via) - set(inventory.SWITCHES)):
            raise ValueError("Explicit candidate must be a simple known switch path")


def capacities(inventory):
    result = {}
    for link in inventory.LINKS:
        result[link.a, link.pa] = result[link.b, link.pb] = int(link.rate_mbps * 1000000)
    for endpoint in inventory.ENDPOINTS.values():
        for a in endpoint.attachments:
            result[a.switch, a.port] = 50000000
    return result


def candidates(state, source, target, constraint=None, limit=4):
    """Bounded simple candidates; the reverse must be usable and also permitted."""
    constraint = constraint or {}
    inv, src, dst = state.inventory, state.attachment(source), state.attachment(target)
    if src is None or dst is None or not inv.permitted(source, target) or not inv.permitted(target, source):
        return []
    blocked = set(constraint.get("avoid_switches", []))
    forbidden = {tuple(sorted(e)) for e in constraint.get("avoid_links", [])}
    graph = defaultdict(list)
    for link in inv.LINKS:
        if (link.a in state.online and link.b in state.online and (link.a, link.pa) in state.up
                and (link.b, link.pb) in state.up and not {link.a, link.b} & blocked
                and tuple(sorted((link.a, link.b))) not in forbidden):
            graph[link.a].append((link.b, link.pa, link.pb))
            graph[link.b].append((link.a, link.pb, link.pa))
    if src.switch in blocked or dst.switch in blocked:
        return []
    explicit = constraint.get("via")
    if explicit and (explicit[0] != src.switch or explicit[-1] != dst.switch):
        return []
    queue, paths, expanded = deque([(src.switch, (src.switch,), ())]), [], 0
    while queue and len(paths) < limit:
        node, nodes, hops = queue.popleft(); expanded += 1
        if expanded > 10000:
            break
        if node == dst.switch:
            if explicit and list(nodes) != explicit:
                continue
            ingress, forward = src.port, []
            for switch, output, peer, next_in in hops:
                forward.append((switch, ingress, output)); ingress = next_in
            forward.append((dst.switch, ingress, dst.port))
            reverse = [(switch, output, entry) for switch, entry, output in reversed(forward)]
            paths.append((tuple(forward), tuple(reverse)))
            continue
        for peer, output, entry in sorted(graph[node]):
            if peer in nodes or (explicit and list(nodes + (peer,)) != explicit[:len(nodes) + 1]):
                continue
            queue.append((peer, nodes + (peer,), hops + ((node, output, peer, entry),)))
    return paths


def plan(config, state, version=1, strict=True):
    inv = state.inventory; validate(config, inv)
    if type(version) is not int or not 1 <= version <= 900:
        raise ValueError("Version outside 1..900; start a fresh owned session before queue-ID reuse")
    ports = capacities(inv); used = defaultdict(int); decisions, flows, queues = {}, {s: [] for s in inv.SWITCHES}, {}
    services = catalog(inv.districts)
    enabled = sorted(config["permissions"], key=lambda n: (PRIORITY[services[n]["class"]], n))
    for name in enabled:
        service = services[name]; kind = service["class"]
        floor, desired, maximum = config["rates"].get(name, PROFILES[config["profile"]][kind])
        decision = {"service": deepcopy(service), "status": "rejected", "reason": "profile disables class"}
        decisions[name] = decision
        if not desired:
            continue
        if {service['source'], service['target']} - set(config['registered']):
            decision['reason'] = 'endpoint not registered'; continue
        choices = candidates(state, service['source'], service['target'], config['constraints'].get(name))
        admitted = None
        for forward, reverse in choices:
            required = defaultdict(int)
            for route in (forward, reverse):
                for switch, _ingress, output in route:
                    required[switch, output] += 1
            available = min((ports[p] - HEADROOM - used[p]) // count for p, count in required.items())
            allocation = min(desired, available)
            if allocation >= floor:
                admitted = forward, reverse, required, allocation
                break
        if admitted is None:
            decision['reason'] = 'no permitted capacity-feasible bidirectional path'
            if strict and kind in ESSENTIAL and (choices or name in config['constraints']):
                raise ValueError('Protected capacity conflict: ' + name)
            continue
        forward, reverse, required, allocation = admitted
        for p, count in required.items(): used[p] += allocation * count
        decision.update(status='admitted' if allocation == desired else 'reduced', reason='capacity accounted on both directions',
                        allocation_bps=allocation, maximum_bps=maximum, forward=forward, reverse=reverse)
        for direction, route in enumerate((forward, reverse)):
            source, target = (service['source'], service['target']) if direction == 0 else (service['target'], service['source'])
            sport, dport = (service['client_port'], service['server_port']) if direction == 0 else (service['server_port'], service['client_port'])
            queue_id = version * 64 + service['queue_index'] * 2 + direction
            cookie = PREFIX | version << 24 | service['queue_index'] << 8 | direction
            for index in reversed(range(len(route))):
                switch, ingress, output = route[index]
                port = '%s-eth%d' % (switch, output)
                q = queues.setdefault(port, {'capacity': ports[switch, output], 'queues': {'0': {'min': HEADROOM, 'max': ports[switch, output], 'priority': 0}}})
                q['queues'][str(queue_id)] = {'min': allocation, 'max': min(maximum, ports[switch, output]), 'priority': PRIORITY[kind]}
                flows[switch].append(flow(inv, source, target, ingress, output, index == 0,
                                          service['protocol'], sport, dport, cookie, queue_id))
    # ICMP diagnostics are identity-bound and separate from application access.
    for source in sorted(config['registered']):
        for target in sorted(config['registered']):
            route = state.route(source, target)
            for index in reversed(range(len(route))):
                switch, ingress, output = route[index]
                flows[switch].append(flow(inv, source, target, ingress, output, index == 0, 'icmp', None, None, PREFIX | version << 24 | 0xffff, 0))
    return {'version': version, 'config': deepcopy(config), 'decisions': decisions, 'flows': flows, 'queues': queues,
            'reserved_bps': {'%s-eth%d' % p: n for p,n in used.items()}, 'capacities': {'%s-eth%d' % p:c for p,c in ports.items()}}


def flow(inv, source, target, ingress, output, first, protocol, sport, dport, cookie, queue):
    src, dst = inv.ENDPOINTS[source], inv.ENDPOINTS[target]; routed = src.site != dst.site
    fields = ['cookie=0x%x' % cookie, 'table=0', 'priority=%d' % (1000 if protocol == 'icmp' else 2000),
              'idle_timeout=0', 'hard_timeout=0', 'in_port=%d' % ingress, protocol,
              'dl_src=' + (src.mac if first or not routed else inv.SITES[dst.site].gateway_mac),
              'dl_dst=' + (inv.SITES[src.site].gateway_mac if first and routed else dst.mac),
              'nw_src=' + src.ip, 'nw_dst=' + dst.ip, 'nw_frag=no']
    if sport is not None: fields += ['tp_src=%d' % sport, 'tp_dst=%d' % dport]
    actions = []
    if first and routed:
        actions += ['dec_ttl', 'set_field:%s->eth_src' % inv.SITES[dst.site].gateway_mac, 'set_field:%s->eth_dst' % dst.mac]
    actions += ['set_queue:%d' % queue, 'output:%d' % output]
    return ','.join(fields) + ',actions=' + ','.join(actions)
