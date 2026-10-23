"""Persistent host identity never replaces the evaluator-owned Computer lease."""
import copy
import threading
import unittest

from chaos.language import Evaluator, LanguageError, serializable
from chaos.suspension import TaskCancelled
from test_computer_values import ComputerFixture


class PersistentFixture(ComputerFixture):
    def __init__(self):
        super().__init__();self.leases={};self.generations={};self.uncertain=False;self.active_drivers=set()

    def handle(self,op,payload):
        if op=='connect':
            self.calls.append((op,copy.deepcopy(payload)));self.ids+=1
            key=payload['key'];self.generations[key]=self.generations.get(key,0)+1
            result={'id':'lease-'+str(self.ids),'backend':'browser','status':'open','sessionKey':key,
                    'stableSessionId':'stable-'+key,'generation':self.generations[key],'requiresReconcile':self.uncertain,'continued':key in self.active_drivers}
            self.active_drivers.add(key)
            self.leases[result['id']]=result
            return copy.deepcopy(result)
        if op in ('close','release','reconcile') and payload['id'] in self.leases:
            self.calls.append((op,copy.deepcopy(payload)));result=self.leases[payload['id']]
            if op=='reconcile':result['requiresReconcile']=False;self.uncertain=False
            else:
                result['status']='closed'
                if op=='close':
                    self.active_drivers.discard(result['sessionKey'])
            return copy.deepcopy(result)
        result=super().handle(op,payload)
        if op in ('observe','act') and payload['id'] in self.leases:
            lease=self.leases[payload['id']];lease['requiresReconcile']=self.uncertain
            result.update({key:lease[key] for key in ('sessionKey','stableSessionId','generation','requiresReconcile','continued')})
        return result


class ComputerContinuityValuesTests(unittest.TestCase):
    def setUp(self):self.host=PersistentFixture()
    def run_code(self,source,bindings=None,**kw):return self.host.evaluator(**kw).run(source,bindings)
    def operations(self):return [op for op,p in self.host.calls]

    def test_connect_is_cold_and_validates_key_before_effect(self):
        self.assertEqual(self.run_code('type_of(computer_connect("desk"))'),'Task');self.assertEqual(self.host.calls,[])
        for key in ('', '../secret','space key','x'*129,42,None):
            with self.subTest(key=key),self.assertRaises(LanguageError):self.run_code('computer_connect(input)',bindings={'input':key})
        self.assertEqual(self.host.calls,[])
        with self.assertRaisesRegex(LanguageError,'does not allow'):Evaluator().run('await computer_connect("desk")')

    def test_release_reconnect_has_new_lease_and_fencing_generation(self):
        result=self.run_code('''let c=await computer_connect("desk");let first=computer_info(c);
          let cold=computer_release(c);let before=computer_info(c).status;let released=await cold;
          let d=await computer_connect("desk");[before,released,first,computer_info(d),c==d]''')
        self.assertEqual(result[0],'open');self.assertEqual(result[1]['status'],'closed')
        self.assertNotEqual(result[2]['id'],result[3]['id']);self.assertFalse(result[4])
        self.assertFalse(result[2]['continued']);self.assertTrue(result[3]['continued'])
        for key in ('sessionKey','stableSessionId'):self.assertEqual(result[2][key],result[3][key])
        self.assertGreater(result[3]['generation'],result[2]['generation'])
        self.assertEqual(self.operations(),['connect','release','connect'])

    def test_explicit_close_and_ephemeral_release(self):
        result=self.run_code('''let c=await computer_connect("desk");let old=computer_info(c);
          await computer_close(c);await computer_release(c);let d=await computer_connect("desk");
          let temporary=await computer_open();let closed=await computer_release(temporary);
          [old.generation,computer_info(d).generation,closed,computer_info(c).status]''')
        self.assertEqual(result,[1,2,{'id':'computer-3','backend':'browser','status':'closed'},'closed'])
        self.assertEqual(self.operations(),['connect','close','connect','open','close'])

    def test_async_context_release_return_error_and_generator_close(self):
        for body,expected in [('42',42),('throw 42','caught')]:
            host=PersistentFixture();source='try{async with await computer_connect("desk") as c{'+body+'}}catch e{"caught"}'
            self.assertEqual(host.evaluator().run(source),expected);self.assertEqual([op for op,p in host.calls],['connect','release'])
        result=self.run_code('''async fn* stream(){async with await computer_connect("desk") as c{yield c}}
          let s=stream();let c=(await anext(s)).value;await aclose(s);computer_info(c).status''')
        self.assertEqual(result,'closed');self.assertEqual(self.operations(),['connect','release'])

    def test_async_context_return_and_explicit_close_do_not_double_release(self):
        result=self.run_code('''async fn use(){async with await computer_connect("desk") as c{return 42}}
          let answer=await use();async with await computer_connect("desk") as c{await computer_close(c)};answer''')
        self.assertEqual(result,42);self.assertEqual(self.operations(),['connect','release','connect','close'])

    def test_reconcile_cold_latest_observation_and_snapshot_copy(self):
        self.host.uncertain=True
        result=self.run_code('''let c=await computer_connect("desk");let o=await computer_observe(c,false);
          let t=computer_reconcile(c,o,"verified current form");let before=computer_info(c);
          let confirmed=await t;[before.requiresReconcile,confirmed,computer_info(c),observation_info(o).requiresReconcile]''')
        self.assertTrue(result[0]);self.assertFalse(result[1]['requiresReconcile']);self.assertEqual(result[1],result[2]);self.assertTrue(result[3])
        self.assertEqual(self.operations(),['connect','observe','reconcile'])
        payload=self.host.calls[-1][1];self.assertEqual(set(payload),{'id','observationId','evidence','location'})
        self.assertEqual(payload['evidence'],'verified current form');self.assertIsInstance(payload['location']['line'],int)

    def test_reconcile_stale_wrong_lease_and_bad_evidence_never_reach_host(self):
        for tail in ['let task=computer_reconcile(c,o,"checked");await computer_observe(c,false);await task',
                     'let d=await computer_connect("other");await computer_reconcile(d,o,"checked")',
                     'await computer_release(c);await computer_reconcile(c,o,"checked")']:
            host=PersistentFixture()
            with self.subTest(tail=tail),self.assertRaises(LanguageError):host.evaluator().run('let c=await computer_connect("desk");let o=await computer_observe(c,false);'+tail)
            self.assertNotIn('reconcile',[op for op,p in host.calls])
        for evidence in ('',' \n ',42,None,'x'*1001):
            with self.subTest(evidence=str(evidence)[:20]),self.assertRaises(LanguageError):
                self.run_code('let c=await computer_connect("desk");let o=await computer_observe(c,false);computer_reconcile(c,o,input)',bindings={'input':evidence})
        with self.assertRaisesRegex(LanguageError,'persistent'):self.run_code('let c=await computer_open();computer_reconcile(c,await computer_observe(c,false),"checked")')
        self.assertNotIn('reconcile',self.operations())

    def test_owned_epoch_and_json_cannot_restore_lease_or_observation(self):
        evaluator=self.host.evaluator();c=evaluator.run('await computer_connect("desk")')
        with self.assertRaisesRegex(LanguageError,'epoch'):evaluator.run('await computer_release(input)',{'input':c})
        with self.assertRaisesRegex(LanguageError,'ownership'):self.host.evaluator().run('computer_release(input)',{'input':c})
        for value in (serializable(c),copy.deepcopy(c.snapshot)):
            with self.assertRaises(LanguageError):self.run_code('computer_release(input)',bindings={'input':value})
        self.assertEqual(set(serializable(c)),{'$computer','backend','status'})

    def test_successful_observation_refreshes_uncertainty_but_not_driver_identity(self):
        original=self.host.handle
        def handle(op,payload):
            if op=='act':self.host.uncertain=True;raise LanguageError('uncertain action')
            return original(op,payload)
        evaluator=Evaluator(computer_handler=handle)
        result=evaluator.run('''let c=await computer_connect("desk");let o=await computer_observe(c,false);
          let a=computer_action(o,{action:"click",ref:"e1"});try{await c(a)}catch e{null};
          let cached=computer_info(c).requiresReconcile;await computer_observe(c,false);
          [cached,computer_info(c).requiresReconcile,action_info(a).used]''')
        self.assertEqual(result,[False,True,True])

    def test_host_metadata_checks_identity_and_scalar_types(self):
        cases=[{'generation':True},{'generation':0},{'requiresReconcile':0},{'sessionKey':'different'},{'stableSessionId':''},{'status':'closed'}]
        for changes in cases:
            host=PersistentFixture()
            def handle(op,payload):
                result=host.handle(op,payload)
                if op=='connect':result.update(changes)
                return result
            with self.subTest(changes=changes),self.assertRaises(LanguageError):Evaluator(computer_handler=handle).run('await computer_connect("desk")')
        host=PersistentFixture()
        def mismatch(op,payload):
            result=host.handle(op,payload)
            if op=='observe':result['generation']+=1
            return result
        with self.assertRaisesRegex(LanguageError,'identity'):Evaluator(computer_handler=mismatch).run('let c=await computer_connect("desk");await computer_observe(c,false)')

    def test_release_obeys_busy_and_cancellation_drains_worker(self):
        started=threading.Event();drained=threading.Event();host=self.host
        def dispatch(kind,payload,cancel):
            if kind=='tool':self.assertTrue(started.wait(2));return None
            if payload['operation']=='release':
                started.set()
                try:
                    cancel.wait(2)
                    raise TaskCancelled('cancelled release')
                finally:drained.set()
            return host.handle(payload['operation'],payload['payload'])
        evaluator=host.evaluator(effect_handler=dispatch,tool_handler=lambda *a:None)
        with self.assertRaises(TaskCancelled):
            evaluator.run('let c=await computer_connect("desk");let t=spawn(computer_release(c));tool("wait",null);cancel(t);await t')
        self.assertTrue(drained.is_set());self.assertIsNone(evaluator.suspension.pool);self.assertEqual(evaluator.frames,[])

    def test_release_excludes_other_operations_until_worker_drains(self):
        started=threading.Event();drained=threading.Event();host=self.host
        def dispatch(kind,payload,cancel):
            if kind=='tool':self.assertTrue(started.wait(2));return None
            if payload['operation']=='release':
                started.set()
                try:
                    cancel.wait(2)
                    raise TaskCancelled('cancelled release')
                finally:drained.set()
            return host.handle(payload['operation'],payload['payload'])
        evaluator=host.evaluator(effect_handler=dispatch,tool_handler=lambda *a:None)
        with self.assertRaisesRegex(LanguageError,'already executing'):
            evaluator.run('let c=await computer_connect("desk");let t=spawn(computer_release(c));tool("wait",null);await gather([t,computer_observe(c,false)])')
        self.assertTrue(drained.is_set());self.assertNotIn('observe',self.operations());self.assertIsNone(evaluator.suspension.pool)


if __name__=='__main__':unittest.main()
