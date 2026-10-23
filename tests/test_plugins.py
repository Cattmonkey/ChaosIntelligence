import unittest
from chaos.plugins import PluginHost, PluginSpec

class PluginContracts(unittest.TestCase):
    def test_waiting_dependency_activates_late_then_unloads_consumer_first(self):
        log=[];host=PluginHost()
        def consumer(ctx):
            dependency=ctx.get('resource');self.assertIsNotNone(dependency)
            ctx.provide('consumer',object());ctx.effect(lambda:log.append('consumer'))
        def provider(ctx):
            ctx.provide('resource',object());ctx.effect(lambda:log.append('provider'))
        host.add(PluginSpec('consumer',consumer,requires=('resource',)))
        self.assertEqual(host.inventory()[0]['state'],'pending')
        self.assertEqual(host.inventory()[0]['missingServices'],['resource'])
        host.add(PluginSpec('provider',provider))
        self.assertTrue(all(row['state']=='active' for row in host.inventory()))
        host.refresh([{'id':'provider','enabled':False}])
        self.assertEqual(log,['consumer','provider'])
        self.assertIsNone(host.get('resource'));self.assertIsNone(host.get('consumer'))
        host.refresh([])
        self.assertTrue(all(row['state']=='active' for row in host.inventory()))
        host.close();host.close()
        self.assertEqual(log,['consumer','provider','consumer','provider'])
    def test_partial_start_failure_disposes_effects_and_services(self):
        log=[];host=PluginHost()
        def broken(ctx):
            ctx.provide('leaking-resource',object());ctx.effect(lambda:log.append('cleanup'))
            raise ValueError('raw sensitive failure details')
        host.add(PluginSpec('broken',broken))
        self.assertIsNone(host.get('leaking-resource'))
        self.assertEqual(log,['cleanup'])
        self.assertEqual(host.inventory()[0]['state'],'failed')
        self.assertNotIn('sensitive',str(host.inventory()))
        host.refresh([]);self.assertEqual(log,['cleanup'])
        host.close();self.assertEqual(log,['cleanup'])
    def test_effect_order_and_failure_are_isolated(self):
        log=[];host=PluginHost()
        def dispose_bad():log.append(2);raise RuntimeError('fixture')
        def plugin(ctx):
            ctx.effect(lambda:log.append(1));ctx.effect(dispose_bad);ctx.effect(lambda:log.append(3))
        host.add(PluginSpec('effects',plugin));host.close()
        self.assertEqual(log,[3,2,1]);self.assertEqual(host.inventory()[0]['state'],'disposed')
    def test_invalid_refresh_is_atomic(self):
        host=PluginHost();host.add(PluginSpec('one',lambda ctx:None))
        with self.assertRaises(ValueError):host.refresh([{'id':'one','enabled':False},{'id':'unknown','enabled':False}])
        self.assertTrue(host.inventory()[0]['enabled']);self.assertEqual(host.inventory()[0]['state'],'active')
        host.close()
    def test_old_context_cannot_republish_after_stop(self):
        contexts=[];host=PluginHost();host.add(PluginSpec('one',lambda ctx:contexts.append(ctx)))
        host.refresh([{'id':'one','enabled':False}])
        with self.assertRaises(RuntimeError):contexts[0].provide('x',1)
        with self.assertRaises(RuntimeError):contexts[0].effect(lambda:None)
        host.close()

if __name__=='__main__':unittest.main()
