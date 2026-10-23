"""Signature boundaries preserve the existing scheduler and effect ownership."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, Parser, ResourceLimit
from chaos.parameters import validate_parameters
from chaos.suspension import TaskCancelled


class PositionalOnlyControlTests(unittest.TestCase):
    def test_hard_effect_errors_cross_wrappers_without_becoming_binding_errors(self):
        for error in (ExecutionControl('host'),ResourceLimit('budget'),TaskCancelled('foreign'),KeyboardInterrupt(),SystemExit(7)):
            seen=[]
            def effect(kind,payload,cancel):
                seen.append(payload['name'])
                if payload['name']=='stop':raise error
                return None
            source='''fn action(value,/,**rest){tool("stop",[value,rest])}
              async fn work(){try{compose(fn(x,/){x},partial(action,42))(value=9)}catch e{tool("caught",null)}};await work()'''
            ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect)
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)) as caught:ev.run(source)
            self.assertIs(caught.exception,error);self.assertEqual(seen,['stop'])
            self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0);self.assertIsNone(ev.suspension.pool)

    def test_local_cancel_drains_positional_only_method_effect(self):
        entered=threading.Event();finished=threading.Event();seen=[]
        def effect(kind,payload,cancel):
            name=payload['name'];seen.append(name)
            if name=='hold':
                self.assertEqual(payload['value'],[42,{'self':9}])
                entered.set()
                try:
                    if not cancel.wait(3):raise AssertionError('missing local cancellation')
                    return 42
                finally:finished.set()
            if name=='ready':self.assertTrue(entered.wait(3))
            return None
        source='''class C{fn work(self,value,/,**rest){tool("hold",[value,rest]);tool("after",null)}}
          var target=null;async fn work(){C().work(42,self=9)};
          async fn stop(){tool("ready",null);cancel(target)};
          target=work();spawn(stop());await target'''
        ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect)
        with self.assertRaises(TaskCancelled):ev.run(source)
        self.assertTrue(finished.is_set());self.assertEqual(set(seen),{'hold','ready'});self.assertIsNone(ev.suspension.pool)

    def test_agent_calls_keep_ast_thread_and_parallel_effect_workers(self):
        main=threading.get_ident();asts=set();workers=set();barrier=threading.Barrier(2)
        def handler(name,config,value):
            workers.add(threading.get_ident());barrier.wait(timeout=3);return value
        source='''agent echo{};async fn work(value,/,**rest){echo([value,rest])};
          await gather([work(20,value=1),work(22,value=2)])'''
        ev=Evaluator(agent_handler=handler,debug_hook=lambda *args:asts.add(threading.get_ident()))
        self.assertEqual(ev.run(source),[[20,{'value':1}],[22,{'value':2}]])
        self.assertEqual(asts,{main});self.assertEqual(len(workers),2)

    def test_invalid_signature_binding_never_allocates_cold_task_or_stream(self):
        for flavor in ('fn*','async fn','async fn*'):
            for call in ('f(slot=[1])','f([1])','f("wrong")','f([20,22],slot=0)'):
                ev=Evaluator();calls=[];ev.tool_handler=lambda *a:calls.append(a)
                source=flavor+' f(slot as [a,b]:List,/){tool("body",null)};'+call
                with self.subTest(flavor=flavor,call=call),self.assertRaises(LanguageError):ev.run(source)
                self.assertEqual(ev.suspension.tasks,[]);self.assertEqual(ev.suspension.generators,[])
                self.assertEqual(calls,[])

    def test_default_await_retains_enclosing_permission_and_definition_time(self):
        source='''var calls=0;async fn make(){calls+=1;42};fn f(x=await make(),/){x};[f(),f(),calls]'''
        self.assertEqual(Evaluator().run(source),[42,42,1])
        source='''async fn outer(){async fn make(){42};fn f(x=await make(),/){x};f()};await outer()'''
        self.assertEqual(Evaluator().run(source),42)
        for source in ('fn outer(){fn f(x=await make(),/){x}}','fn* outer(){fn f(x=await make(),/){x}}'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()

    def test_validation_limits_run_before_default_effects(self):
        params=[('x'+str(i),'Any','positional_only',None)for i in range(10001)]
        with self.assertRaises(ResourceLimit):validate_parameters(params)
        node=Parser('fn f(x=tool("default",null),/){x}').program()[1][0][2]
        row=node[1][0];node[1].append(('later','Any','positional',None))
        effects=[];ev=Evaluator(tool_handler=lambda *a:effects.append(a));ev.suspension.begin()
        try:
            with self.assertRaises(LanguageError):ev.evaluate(node,ev.env)
        finally:ev.suspension.finish()
        self.assertEqual(effects,[])
        self.assertIsNotNone(row[3])


if __name__=='__main__':unittest.main()
