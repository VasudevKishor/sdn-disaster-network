"""Phase 3 profiles, exact admission, capacities and real OVS flow parsing."""
from copy import deepcopy
import subprocess
import unittest
from config.expanded import build_inventory
from config.incident import initial_config, PROFILES, catalog, HEADROOM
from policy.model import plan, validate, candidates
from tests.test_expanded_model import ready


class IncidentModelTests(unittest.TestCase):
    def setUp(self):
        self.i=build_inventory(); self.state=ready(self.i); self.config=initial_config(self.i)

    def test_all_profiles_have_explicit_decisions_and_joint_directed_capacity(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.config['profile']=profile; result=plan(self.config,self.state)
                self.assertEqual(set(result['decisions']),set(self.config['permissions']))
                for port, rate in result['reserved_bps'].items():
                    self.assertLessEqual(rate+HEADROOM,result['capacities'][port])
                for q in result['queues'].values():
                    self.assertLessEqual(sum(v['min'] for v in q['queues'].values()),q['capacity'])
                for name,d in result['decisions'].items():
                    if d['status'] in ('admitted','reduced'):
                        self.assertEqual(d['reverse'],tuple((s,o,i) for s,i,o in reversed(d['forward'])))
                        self.assertGreater(d['allocation_bps'],0)

    def test_registration_and_service_permission_are_separate(self):
        self.config['registered'].append('af3')
        first=plan(self.config,self.state)
        self.assertNotIn('a_reporting',first['decisions'])
        self.config['permissions'].append('a_reporting')
        self.assertEqual(plan(self.config,self.state)['decisions']['a_reporting']['status'],'admitted')
        self.config['registered'].remove('af3')
        self.assertEqual(plan(self.config,self.state)['decisions']['a_reporting']['reason'],'endpoint not registered')

    def test_conflicting_protected_requests_are_not_independently_admitted(self):
        self.config['rates']={'a_medical':[40000000]*3,'a_shelter':[40000000]*3}
        result=plan(self.config,self.state)
        self.assertEqual(result['decisions']['a_medical']['allocation_bps'],40000000)
        self.assertEqual(result['decisions']['a_shelter']['status'],'rejected')
        self.config['registered'].append('af3'); self.config['permissions'].append('a_reporting')
        self.config['rates']['a_reporting']=[40000000]*3
        with self.assertRaisesRegex(ValueError,'capacity conflict'):
            plan(self.config,self.state)
        runtime=plan(self.config,self.state,strict=False)
        self.assertEqual(runtime['decisions']['a_reporting']['status'],'rejected')

    def test_shared_capacity_reduces_lower_priority_and_degraded_blocks_bulk(self):
        self.config['rates']={'a_medical':[1000000,48000000,48000000],'a_shelter':[500000,4000000,4000000]}
        result=plan(self.config,self.state)
        self.assertEqual(result['decisions']['a_shelter']['status'],'reduced')
        self.config['profile']='degraded'; self.config['rates']={}
        result=plan(self.config,self.state)
        self.assertEqual(result['decisions']['a_video']['status'],'rejected')
        self.assertEqual(result['decisions']['a_bulk']['reason'],'profile disables class')

    def test_avoidance_and_explicit_candidates_apply_in_both_directions(self):
        constraint={'avoid_switches':['bb1']}
        options=candidates(self.state,'ac1','bc1',constraint)
        self.assertGreaterEqual(len(options),2)
        self.assertTrue(all('bb1' not in {s for s,_,_ in route} for pair in options for route in pair))
        nodes=[s for s,_,_ in options[0][0]]
        self.config['constraints']['district_coordination']={'via':nodes,**constraint}
        result=plan(self.config,self.state)
        self.assertEqual([s for s,_,_ in result['decisions']['district_coordination']['forward']],nodes)
        self.state.online.remove(nodes[1])
        self.assertFalse(candidates(self.state,'ac1','bc1',self.config['constraints']['district_coordination']))

    def test_invalid_policy_input_fails_without_mutating_original(self):
        cases=[('profile','typo'),('registered',['unknown']),('permissions',['anything']),
               ('rates',{'a_dispatch':[True,1,2]}),('rates',{'a_dispatch':[-1,1,2]}),
               ('constraints',{'district_coordination':{'avoid_links':[['bb1','bad']]}})]
        original=deepcopy(self.config)
        for key,value in cases:
            candidate=deepcopy(original); candidate[key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError): validate(candidate,self.i)
        self.assertEqual(self.config,original)
        with self.assertRaisesRegex(ValueError,'queue-ID reuse'): plan(self.config,self.state,version=901)

    def test_rules_are_identity_and_service_specific_with_no_guest_application_rules(self):
        result=plan(self.config,self.state)
        for flows in result['flows'].values():
            for flow in flows:
                for field in ('in_port=','dl_src=','dl_dst=','nw_src=','nw_dst=','nw_frag=no','set_queue:'):
                    self.assertIn(field,flow)
                if 'priority=2000' in flow:
                    self.assertIn('tp_src=',flow); self.assertIn('tp_dst=',flow)
                    self.assertNotIn('10.30.5.12',flow); self.assertNotIn('10.31.5.12',flow)
                self.assertNotIn('NORMAL',flow); self.assertNotIn('FLOOD',flow)

    def test_real_ovs_parser_accepts_routed_forward_reply_and_diagnostic_rules(self):
        result=plan(self.config,self.state,version=17)
        selected=[]
        for name in ('afc1','acc1','ahc1','bb1'):
            for protocol in ('tcp','udp','icmp'):
                matches=[f for f in result['flows'][name] if ','+protocol+',' in f]
                selected += matches[:1]
        self.assertGreater(len(selected),5)
        for flow in selected:
            output=subprocess.run(['ovs-ofctl','-O','OpenFlow13','parse-flow',flow],capture_output=True,text=True,timeout=5)
            self.assertEqual(output.returncode,0,output.stderr)

    def test_partition_reports_remote_unavailable_and_keeps_local_services(self):
        for link in self.i.LINKS:
            if link.a.startswith('bb') and link.b.startswith('bb') and (int(link.a[-1])<=2)!=(int(link.b[-1])<=2):
                self.state.up.discard((link.a,link.pa))
        result=plan(self.config,self.state,strict=False)
        self.assertEqual(result['decisions']['district_coordination']['status'],'rejected')
        self.assertEqual(result['decisions']['a_dispatch']['status'],'admitted')
        self.assertEqual(result['decisions']['b_dispatch']['status'],'admitted')


if __name__=='__main__': unittest.main()
