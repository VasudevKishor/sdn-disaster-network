import unittest
from unittest.mock import patch
from config.expanded import build_inventory
from policy.backend import OVSBackend, OWNER, execute as real_execute


class BackendTests(unittest.TestCase):
    def setUp(self): self.b=OVSBackend(build_inventory(),'test-run')

    def test_isolated_switch_cannot_be_written_through_local_socket(self):
        with patch.object(self.b,'connected',return_value=False), patch('policy.backend.execute') as call:
            with self.assertRaisesRegex(RuntimeError,'disconnected'): self.b.write('bb1',[])
            call.assert_not_called()

    def test_of13_add_before_exact_old_cookie_delete_and_readback(self):
        old=' cookie=0xd153000001000100,table=0,priority=2000,tcp,in_port=1,nw_src=10.30.1.10 actions=output:10'
        calls=[]
        def execute(args, input=None):
            calls.append((args,input))
            if 'parse-flows' in args: return real_execute(args)
            return old if 'dump-flows' in args else ''
        with patch.object(self.b,'connected',return_value=True), patch('policy.backend.execute',side_effect=execute), \
                patch.object(self.b,'verify') as verify:
            self.b.write('bb1',['cookie=0xd153000002000100,priority=2000,tcp,in_port=1,nw_src=10.30.1.11,actions=output:11'])
            cmds=[c[0] for c in calls]
            self.assertLess(next(i for i,c in enumerate(cmds) if 'add-flows' in c),next(i for i,c in enumerate(cmds) if 'del-flows' in c))
            self.assertTrue(all('OpenFlow13' in c for c in cmds))
            self.assertTrue(any('--strict' in c for c in cmds)); verify.assert_called_once()

    def queue_fixture(self, foreign=False):
        expected={'capacity':50000000,'queues':{'0':{'min':500000,'max':50000000,'priority':0}}}
        tags={'owner':'foreign' if foreign else OWNER,'run':'test-run'}
        return expected, ({'acc1-eth1':{'qos':['qos-id']}},
            {'qos-id':{'type':'linux-htb','external_ids':tags,'other_config':{'max-rate':'50000000'},'queues':{0:'queue-id'}}},
            {'queue-id':{'other_config':{'min-rate':'500000','max-rate':'50000000','priority':'0'}}})

    def test_queue_readback_rejects_foreign_owner_and_wrong_rates(self):
        expected, state=self.queue_fixture()
        self.assertTrue(self.b.queue_matches('acc1-eth1',expected,state))
        state[2]['queue-id']['other_config']['min-rate']='1'
        self.assertFalse(self.b.queue_matches('acc1-eth1',expected,state))
        expected, state=self.queue_fixture(True)
        self.assertFalse(self.b.queue_matches('acc1-eth1',expected,state))

    def test_foreign_qos_and_joint_minima_fail_before_mutation(self):
        expected,state=self.queue_fixture(True)
        with patch.object(self.b,'queue_state',return_value=state), patch.object(self.b,'ovs',side_effect=['acc1','1']) as ovs:
            with self.assertRaisesRegex(ValueError,'Foreign QoS'): self.b.prepare({'acc1-eth1':expected})
            self.assertEqual(ovs.call_count,2)
        expected['queues']['99']={'min':50000000,'max':50000000,'priority':1}
        with patch.object(self.b,'queue_state',return_value=state), patch.object(self.b,'ovs') as ovs:
            with self.assertRaisesRegex(ValueError,'guarantees exceed'): self.b.prepare({'acc1-eth1':expected})
            ovs.assert_not_called()

    def test_already_prepared_queues_on_isolated_switch_need_no_write(self):
        expected,state=self.queue_fixture()
        with patch.object(self.b,'queue_state',return_value=state), \
                patch.object(self.b,'ovs',side_effect=['acc1','1']) as ovs, patch.object(self.b,'connected',return_value=False):
            self.b.prepare({'acc1-eth1':expected})
            self.assertEqual(ovs.call_count,2)


if __name__=='__main__': unittest.main()
