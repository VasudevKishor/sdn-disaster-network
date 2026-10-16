"""Owned local OVS operations; management isolation is never bypassed for writes."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from policy.model import PREFIX, MASK

OWNER = 'sdn-phase3'


def execute(args, input=None):
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('%s: %s' % (' '.join(args[:4]), result.stderr.strip() or result.stdout.strip()))
    return result.stdout.strip()


def decode(value):
    if isinstance(value, list) and len(value) == 2 and value[0] in ('map', 'set', 'uuid', 'named-uuid'):
        kind, data = value
        if kind == 'map': return {decode(k): decode(v) for k,v in data}
        if kind == 'set': return [decode(v) for v in data]
        return data
    return value


class OVSBackend:
    def __init__(self, inventory, owner_id):
        self.inventory, self.owner_id = inventory, owner_id

    def ovs(self, *args):
        return execute(['ovs-vsctl', '--timeout=5', *args])

    def table(self, table):
        data = json.loads(self.ovs('--format=json', 'list', table))
        return [dict(zip(data['headings'], [decode(v) for v in row])) for row in data['data']]

    def connected(self, switch):
        if switch not in self.inventory.SWITCHES: raise ValueError('Unknown switch')
        # The local management socket exists even during controller isolation.
        # Do not use it as permission to modify an isolated switch.
        rows = self.ovs('get', 'Bridge', switch, 'controller')
        ids = re.findall(r'[0-9a-f]{8}-[0-9a-f-]{27}', rows)
        return bool(ids) and any(self.ovs('get', 'Controller', row, 'is_connected') == 'true' for row in ids)

    def queue_state(self):
        qoses = {r['_uuid']: r for r in self.table('QoS')}
        queues = {r['_uuid']: r for r in self.table('Queue')}
        ports = {r['name']: r for r in self.table('Port')}
        return ports, qoses, queues

    def queue_matches(self, port, expected, state=None, allow_extra=False):
        ports, qoses, queues = state or self.queue_state()
        refs = ports.get(port, {}).get('qos', [])
        if isinstance(refs, str): refs = [refs]
        if len(refs) != 1: return False
        q = qoses.get(refs[0], {})
        tags = q.get('external_ids', {})
        if tags.get('owner') != OWNER or tags.get('run') != self.owner_id or q.get('type') != 'linux-htb': return False
        if q.get('other_config', {}).get('max-rate') != str(expected['capacity']): return False
        actual = q.get('queues', {})
        if (not set(expected['queues']) <= {str(k) for k in actual} if allow_extra else {str(k) for k in actual} != set(expected['queues'])): return False
        for number, values in expected['queues'].items():
            row = queues.get(actual.get(int(number)), {})
            cfg = row.get('other_config', {})
            if cfg != {'min-rate':str(values['min']), 'max-rate':str(values['max']), 'priority':str(values['priority'])}: return False
        return True

    def prepare(self, queues):
        state = self.queue_state(); ports, qoses, _ = state
        # Validate the complete request before the first mutation.
        for port, expected in queues.items():
            switch, number = port.rsplit('-eth', 1)
            if switch not in self.inventory.SWITCHES or int(number) not in self.inventory.expected_ports(switch):
                raise ValueError('Unknown QoS egress')
            if sum(q['min'] for q in expected['queues'].values()) > expected['capacity']:
                raise ValueError('Staged queue guarantees exceed ' + port)
            if any(not 0 <= q['min'] <= q['max'] <= expected['capacity'] for q in expected['queues'].values()):
                raise ValueError('Invalid queue rate')
            if port not in ports or self.ovs('port-to-br', port) != switch or int(self.ovs('get','Interface',port,'ofport')) != int(number):
                raise ValueError('Missing/mismatched port ' + port)
            refs = ports[port]['qos']; refs = [refs] if isinstance(refs,str) else refs
            for ref in refs:
                tags = qoses.get(ref,{}).get('external_ids',{})
                if tags.get('owner') != OWNER or tags.get('run') != self.owner_id:
                    raise ValueError('Foreign QoS on ' + port)
            if not self.queue_matches(port,expected,state) and not self.connected(switch):
                raise RuntimeError('Controller disconnected: ' + switch)
        for port, expected in queues.items():
            if self.queue_matches(port,expected,state): continue
            if not self.connected(port.rsplit('-eth',1)[0]): raise RuntimeError('Controller disconnected before QoS write')
            tags = ['external_ids:owner='+OWNER, 'external_ids:run='+self.owner_id, 'external_ids:port='+port]
            args = ['set','Port',port,'qos=@qos','--','--id=@qos','create','QoS','type=linux-htb',
                    'other-config:max-rate=%d'%expected['capacity'], *tags]
            for number in expected['queues']: args += ['queues:%s=@q%s'%(number,number)]
            for number, q in expected['queues'].items():
                args += ['--','--id=@q'+number,'create','Queue', *tags,
                         'other-config:min-rate=%d'%q['min'],'other-config:max-rate=%d'%q['max'],
                         'other-config:priority=%d'%q['priority']]
            self.ovs(*args)
        actual = self.queue_state()
        if not all(self.queue_matches(p,q,actual) for p,q in queues.items()):
            raise RuntimeError('Queue readback mismatch')

    def write(self, switch, flows):
        if not self.connected(switch): raise RuntimeError('Controller disconnected: '+switch)
        # OF1.3 is retained. Standard bundles require OF1.4: use explicit
        # add-before-delete and readback instead of claiming atomicity.
        old=execute(['ovs-ofctl','-O','OpenFlow13','--timeout=5','--no-stats','dump-flows',switch,
                     'cookie=0x%x/0x%x'%(PREFIX,MASK)])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'flows.txt'; path.write_text('\n'.join(flows)+'\n')
            compiled=execute(['ovs-ofctl','-O','OpenFlow13','parse-flows',str(path)])
            old_lines=[line.strip() for line in old.splitlines() if 'cookie=' in line]
            old_path=Path(directory)/'old.txt'; old_path.write_text('\n'.join(old_lines)+'\n')
            old_compiled=execute(['ovs-ofctl','-O','OpenFlow13','parse-flows',str(old_path)])
            def identities(output):
                # OVS normalizes match spelling/order for us. Cookie/actions
                # are not part of the OpenFlow ADD identity (table,priority,
                # match). Never delete a same-version rule just re-added.
                return [re.sub(r'\s+cookie:0x[0-9a-f]+','',line.split(': ADD ',1)[1].split(' actions=',1)[0]).strip()
                        for line in output.splitlines() if ': ADD ' in line]
            desired=set(identities(compiled)); previous=identities(old_compiled)
            if len(previous)!=len(old_lines): raise RuntimeError('Cannot normalize previous owned flow identities')
            if not self.connected(switch): raise RuntimeError('Controller disconnected before flow add')
            execute(['ovs-ofctl','-O','OpenFlow13','--timeout=5','add-flows',switch,str(path)])
            obsolete=[]
            for line, identity in zip(old_lines,previous):
                if identity in desired: continue
                match=line.split('actions=')[0].strip().rstrip(',')
                match=re.sub(r'cookie=(0x[0-9a-f]+)',r'cookie=\1/0xffffffffffffffff',match)
                obsolete.append(match)
            if obsolete:
                if not self.connected(switch): raise RuntimeError('Controller disconnected before obsolete-rule removal')
                execute(['ovs-ofctl','-O','OpenFlow13','--timeout=5','--strict','del-flows',switch,'-'],
                        input='\n'.join(obsolete)+'\n')
        self.verify(switch, flows)

    def verify(self, switch, flows, local=False):
        if not local and not self.connected(switch): raise RuntimeError('Controller disconnected; state unverified: '+switch)
        actual=execute(['ovs-ofctl','-O','OpenFlow13','--timeout=5','dump-flows',switch,'cookie=0x%x/0x%x'%(PREFIX,MASK)])
        with tempfile.TemporaryDirectory() as directory:
            expected=Path(directory)/'expected'; observed=Path(directory)/'observed'
            expected.write_text('\n'.join(flows)+'\n')
            observed.write_text('\n'.join(l.strip() for l in actual.splitlines() if 'cookie=' in l)+'\n')
            execute(['ovs-ofctl','-O','OpenFlow13','diff-flows',str(expected),str(observed)])
        return {'verified':True, 'inspection':'read-only local lab' if local else 'connected readback', 'flows':len(flows)}

    def cleanup(self):
        ports,qoses,queues=self.queue_state()
        owned=lambda row: row.get('external_ids',{}).get('owner')==OWNER and row.get('external_ids',{}).get('run')==self.owner_id
        for port,row in ports.items():
            refs=row['qos']; refs=[refs] if isinstance(refs,str) else refs
            if any(owned(qoses.get(ref,{})) for ref in refs): self.ovs('clear','Port',port,'qos')
        for table, rows in (('QoS',qoses),('Queue',queues)):
            for uuid,row in rows.items():
                if owned(row): self.ovs('--if-exists','destroy',table,uuid)
        _,qoses,queues=self.queue_state()
        if any(owned(row) for row in list(qoses.values())+list(queues.values())):
            raise RuntimeError('Owned QoS/Queue rows remain after cleanup')
