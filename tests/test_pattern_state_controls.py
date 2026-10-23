"""Structural state updates share task cancellation and resource boundaries."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled


class PatternStateControlTests(unittest.TestCase):
    def test_hard_rhs_controls_do_not_commit_or_invoke_language_cleanup(self):
        for error in (ExecutionControl('host'),ResourceLimit('budget'),TaskCancelled('foreign'),KeyboardInterrupt(),SystemExit(7)):
            cells=[];seen=[]
            def debug(node,env,frames):
                if node[0]=='assign_pattern':cells[:]=[env.resolve_cell('a'),env.resolve_cell('b')]
            def effect(kind,payload,cancel):
                seen.append(payload['name'])
                if payload['name']=='stop':raise error
                return None
            source='''var a:Int=1;var b:Int=2;
              class C{fn __enter__(s){[42]}fn __exit__(s,e){tool("exit",null);null}}
              async fn work(){try{with C() as [value]{[a,b]=tool("stop",value)}}catch e{tool("caught",null)}};await work()'''
            ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect,debug_hook=debug)
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)) as caught:ev.run(source)
            self.assertIs(caught.exception,error);self.assertEqual([c.value for c in cells],[1,2])
            self.assertEqual(seen,['stop']);self.assertEqual(ev.frames,[]);self.assertIsNone(ev.suspension.pool)

    def test_local_rhs_worker_cancel_is_drained_before_failure(self):
        entered=threading.Event();finished=threading.Event();cells=[];seen=[]
        def debug(node,env,frames):
            if node[0]=='assign_pattern':cells[:]=[env.resolve_cell('a'),env.resolve_cell('b')]
        def effect(kind,payload,cancel):
            name=payload['name'];seen.append(name)
            if name=='hold':
                entered.set()
                try:
                    if not cancel.wait(3):raise AssertionError('missing local cancellation')
                    return [20,22]
                finally:finished.set()
            if name=='ready':self.assertTrue(entered.wait(3))
            return None
        source='''var a:Int=1;var b:Int=2;var target=null;
          async fn work(){[a,b]=tool("hold",null);tool("after",null)};
          async fn stop(){tool("ready",null);cancel(target)};
          target=work();spawn(stop());await target'''
        ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect,debug_hook=debug)
        with self.assertRaises(TaskCancelled):ev.run(source)
        self.assertTrue(finished.is_set());self.assertEqual([c.value for c in cells],[1,2])
        self.assertEqual(set(seen),{'hold','ready'});self.assertIsNone(ev.suspension.pool)

    def test_shield_keeps_complete_update_before_delivering_local_cancel(self):
        seen=[]
        source='''var a:Int=1;var b:Int=2;var target=null;
          async fn work(){with cancel_shield() as shield{
            [a,b]=do{cancel(target);tool("rhs",null);[20,22]};tool("inside",[a,b])};tool("outside",null)};
          target=work();await target'''
        with self.assertRaises(TaskCancelled):Evaluator(tool_handler=lambda n,v:seen.append((n,v))).run(source)
        self.assertEqual(seen,[('rhs',None),('inside',[20,22])])

    def test_rhs_suspension_permits_other_updates_but_commit_has_no_torn_observation(self):
        observed=[];threads=set();main=threading.get_ident()
        def debug(node,env,frames):
            threads.add(threading.get_ident())
            try:values=[env.get('a'),env.get('b')]
            except LanguageError:return
            if all(type(v)is int for v in values):observed.append(values)
        source='''var a:Int=1;var b:Int=2;let ready=channel();let release=channel();
          async fn update(){[a,b]=do{await channel_send(ready,true);await channel_receive(release);[20,22]};[a,b]};
          async fn rival(){await channel_receive(ready);[a,b]=[9,10];let observed=[a,b];await channel_send(release,true);observed};
          let result=await gather([update(),rival()]);[result,a,b]'''
        result=Evaluator(debug_hook=debug).run(source)
        self.assertEqual(result,[[[20,22],[9,10]],20,22]);self.assertEqual(threads,{main})
        self.assertTrue(observed);self.assertTrue(all(row in ([1,2],[9,10],[20,22])for row in observed))

    def test_generator_close_during_assignment_rhs_preserves_cells(self):
        source='''var a:Int=1;var b:Int=2;var closed=0;
          fn* work(){try{[a,b]=yield ([20,22])}finally{closed+=1}};
          let g=work();next(g);close(g);[a,b,closed]'''
        self.assertEqual(Evaluator().run(source),[1,2,1])
        source=source.replace('fn* work','async fn* work').replace('next(g);close(g)','await anext(g);await aclose(g)')
        self.assertEqual(Evaluator().run(source),[1,2,1])

    def test_parallel_agent_results_remain_scheduler_owned_structural_values(self):
        workers=set();barrier=threading.Barrier(2);asts=set()
        def handler(name,config,value):
            workers.add(threading.get_ident());barrier.wait(timeout=3);return {'left':value,'right':value+1}
        source='''agent pair{};async fn work(n){var a:Int=0;var b:Int=0;
          {left:a,right:b}=pair(n);a+b};await gather([work(20),work(21)])'''
        ev=Evaluator(agent_handler=handler,debug_hook=lambda *a:asts.add(threading.get_ident()))
        self.assertEqual(ev.run(source),[41,43]);self.assertEqual(len(workers),2)
        self.assertEqual(asts,{threading.get_ident()})


if __name__=='__main__':unittest.main()
