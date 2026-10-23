"""Augmented operator effects retain scheduler ownership and hard controls."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled


class AugmentedControlTests(unittest.TestCase):
    def test_hard_control_identity_no_fallback_no_write(self):
        for error in (ExecutionControl('host'),ResourceLimit('budget'),TaskCancelled('unattributed'),KeyboardInterrupt(),SystemExit(7)):
            seen=[]
            def effect(kind,payload,cancel):
                seen.append(payload['name'])
                if payload['name']=='stop': raise error
                return 42
            evaluator=Evaluator(tool_handler=lambda n,v:None,effect_handler=effect)
            source='''class N{fn __iadd__(s,x){tool("stop",x)}fn __add__(s,x){tool("fallback",x)}}
class C{fn __getitem__(s,k){N()}fn __setitem__(s,k,v){tool("write",v);null}}
async fn work(){try{C()[0]+=1}catch e{tool("catch",0)}};await work()'''
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)) as caught:evaluator.run(source)
            self.assertIs(caught.exception,error);self.assertEqual(seen,['stop'])
            self.assertEqual(evaluator.frames,[]);self.assertEqual(evaluator.depth,0);self.assertIsNone(evaluator.suspension.pool)

    def test_local_cancel_drains_inplace_worker_without_write(self):
        entered=threading.Event();finished=threading.Event();seen=[]
        def effect(kind,payload,cancel):
            name=payload['name'];seen.append(name)
            if name=='hold':
                entered.set()
                try:
                    if not cancel.wait(3):raise AssertionError('worker cancellation not delivered')
                    raise TaskCancelled('worker acknowledged')
                finally:finished.set()
            if name=='ready':self.assertTrue(entered.wait(3))
            return None
        evaluator=Evaluator(tool_handler=lambda n,v:None,effect_handler=effect)
        source='''class N{fn __iadd__(s,x){tool("hold",x)}fn __add__(s,x){tool("fallback",x)}}
class C{fn __getitem__(s,k){N()}fn __setitem__(s,k,v){tool("write",v);null}}
var target=null;async fn work(){C()[0]+=1};async fn stop(){tool("ready",0);cancel(target)};
target=work();spawn(stop());await target'''
        with self.assertRaises(TaskCancelled):evaluator.run(source)
        self.assertTrue(finished.is_set());self.assertEqual(set(seen),{'hold','ready'});self.assertIsNone(evaluator.suspension.pool)

    def test_shield_delays_local_cancel_through_complete_write(self):
        seen=[]
        source='''var target=null;class N{fn __iadd__(s,x){cancel(target);tool("operation",x);40+x}}
class C{fn __getitem__(s,k){N()}fn __setitem__(s,k,v){tool("write",v);null}}
async fn work(){with cancel_shield() as shield{C()[0]+=2;tool("inside",0)};tool("outside",0)};
target=work();await target'''
        evaluator=Evaluator(tool_handler=lambda n,v:seen.append((n,v)))
        with self.assertRaises(TaskCancelled):evaluator.run(source)
        self.assertEqual(seen,[('operation',2),('write',42),('inside',0)])

    def test_loop_budget_and_shift_resource_failure_never_catchable(self):
        for source in ('var n=1;try{n<<=14000}catch e{42}',
                       'class C{fn __iadd__(s,x){while true{1}}}var c=C();try{c+=1}catch e{42}',
                       'class C{fn __iadd__(s,x){var c=s;c+=x;c}}var c=C();try{c+=1}catch e{42}'):
            with self.subTest(source=source),self.assertRaises(ResourceLimit):Evaluator(step_limit=1000).run(source)

    def test_cooperative_protocols_keep_ast_single_thread_and_real_parallel_effects(self):
        main=threading.get_ident();asts=set();workers=set();barrier=threading.Barrier(2)
        def handler(name,config,value):
            workers.add(threading.get_ident());barrier.wait(timeout=3);return value
        evaluator=Evaluator(agent_handler=handler,debug_hook=lambda *args:asts.add(threading.get_ident()))
        source='agent a{};class C{fn __iadd__(s,x){a(x+1)}}async fn work(n){var c=C();c+=n;c};await gather([work(40),work(41)])'
        self.assertEqual(evaluator.run(source),[41,42]);self.assertEqual(asts,{main});self.assertEqual(len(workers),2)

    def test_yield_and_await_keep_original_receiver_and_key(self):
        source='''class C{var n=1;fn __getitem__(s,k){s.n}fn __setitem__(s,k,v){s.n=v;null}}
var target=C();let original=target;var key=0;
async fn rhs(){target=C();target.n=99;key=1;await sleep(0);2};target[key]+=await rhs();[original.n,target.n,key]'''
        self.assertEqual(Evaluator().run(source),[3,99,1])
        self.assertEqual(Evaluator().run('var x=1;fn* gen(){x+=yield 2;yield x};let g=gen();let first=next(g);send(g,4);x'),5)

    def test_foreign_protocol_operand_and_target_rejected(self):
        foreign=Evaluator().run('class C{fn __iadd__(s,x){42}fn __rand__(s,x){42}fn __pos__(s){42}}C()')
        for source in ('var x=foreign;x+=1','1&foreign','+foreign','class C{fn __iadd__(s,x){42}}var c=C();c+=foreign'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source,{'foreign':foreign})


if __name__=='__main__':unittest.main()
