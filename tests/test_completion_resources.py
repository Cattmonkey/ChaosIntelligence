"""Completion outcomes respect asynchronous resources and hard controls."""
import threading
import unittest

from chaos import Framework
from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled, TERMINAL


class CompletionResourceTests(unittest.TestCase):
    def test_loop_break_and_exhaustion_keep_borrowed_cursor_lifecycle(self):
        source='''class Source{var n=0;var closes=0;fn __iter__(s){s}
          fn __next__(s){s.n+=1;{done:s.n>3,value:s.n}}fn __close__(s){s.closes+=1;null}}
          let raw=Source();let cursor=iter(raw);let stopped=for x in cursor{break}else{99};
          let after=next(cursor);let exhausted=for x in cursor{}else{42};
          [stopped,after,exhausted,raw.n,raw.closes,next(cursor)]'''
        self.assertEqual(Evaluator().run(source),[None,{'done':False,'value':2},42,4,0,{'done':True,'value':4}])
        source=source.replace('fn __iter__','fn __aiter__').replace('fn __next__','async fn __anext__').replace('fn __close__','async fn __aclose__').replace('iter(raw)','aiter(raw)').replace('for x in cursor','async for x in cursor').replace('next(cursor)','await anext(cursor)')
        self.assertEqual(Evaluator().run(source),[None,{'done':False,'value':2},42,4,0,{'done':True,'value':4}])

    def test_source_condition_and_pattern_errors_skip_otherwise(self):
        sources=['while (1/0){}else{tool("wrong",null)}','for x in do{throw 7}{}else{tool("wrong",null)}',
                 'for (x,) in [42]{}else{tool("wrong",null)}',
                 'class C{fn __iter__(s){throw 7}};for x in C(){}else{tool("wrong",null)}',
                 'class C{fn __iter__(s){s}fn __next__(s){throw 7}};for x in C(){}else{tool("wrong",null)}',
                 'class C{fn __aiter__(s){s}async fn __anext__(s){throw 7}};async for x in C(){}else{tool("wrong",null)}']
        for source in sources:
            seen=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator(tool_handler=lambda *args:seen.append(args)).run(source)
            self.assertEqual(seen,[])

    def test_generator_close_skips_unentered_else_and_runs_existing_finally(self):
        for flavor,start,close in [('fn*','next(g)','close(g)'),('async fn*','await anext(g)','await aclose(g)')]:
            for protected in ['for x in [1,2]{yield x}else{seen=append(seen,"else")}',
                              'try{yield 1}catch e{seen=append(seen,"catch")}else{seen=append(seen,"else")}']:
                source='var seen=[];'+flavor+' work(){try{'+protected+'}finally{seen=append(seen,"cleanup")}};let g=work();'+start+';'+close+';seen'
                with self.subTest(flavor=flavor,protected=protected):self.assertEqual(Evaluator().run(source),['cleanup'])

    def test_closing_inside_else_does_not_run_remaining_branch(self):
        for flavor,start,close in [('fn*','next(g)','close(g)'),('async fn*','await anext(g)','await aclose(g)')]:
            source='var seen=[];'+flavor+' work(){try{for x in []{}else{seen=append(seen,"entered");yield 42;seen=append(seen,"after")}}finally{seen=append(seen,"cleanup")}};let g=work();'+start+';'+close+';seen'
            with self.subTest(flavor=flavor):self.assertEqual(Evaluator().run(source),['entered','cleanup'])

    def test_generator_handler_and_success_resumes_do_not_share_outcome(self):
        source='fn* work(){try{yield 1}catch e{yield e.value}else{yield 42}};let a=work();let b=work();next(a);next(b);let recovered=throw_into(a,7);[recovered,collect(a),collect(b)]'
        self.assertEqual(Evaluator().run(source),[{'done':False,'value':7},[],[42]])
        source='async fn* work(){try{yield 1}catch e{yield e.value}else{yield 42}};let a=work();let b=work();await anext(a);await anext(b);let recovered=await athrow(a,7);[recovered,await acollect(a),await acollect(b)]'
        self.assertEqual(Evaluator().run(source),[{'done':False,'value':7},[],[42]])

    def test_channel_exhaustion_runs_else_after_all_deliveries(self):
        source='''let messages=channel();var values=[];async fn produce(){
          await channel_send(messages,(20,));await channel_send(messages,(22,));channel_close(messages)};
          let result=async with task_group() as group{group.spawn(produce());
          async for (value,) in messages{values=append(values,value)}else{values}};
          [result,channel_info(messages).waitingReceivers,channel_info(messages).waitingSenders]'''
        self.assertEqual(Evaluator().run(source),[[20,22],0,0])
        source='let messages=channel(1);await channel_send(messages,42);let result=async for x in messages{break}else{99};[result,channel_info(messages).state]'
        self.assertEqual(Evaluator().run(source),[None,'open'])

    def test_local_cancel_retracts_async_loop_receive_and_never_runs_else(self):
        observed=[];captured=[]
        def debug(node,env,frames):
            try:captured[:]=[env.get('messages')]
            except LanguageError:pass
        source='''let messages=channel();var target=null;
          async fn consumer(){async for x in messages{}else{tool("wrong",null)}};
          async fn stop(){while channel_info(messages).waitingReceivers!=1{1};cancel(target)};
          target=consumer();spawn(stop());await target'''
        ev=Evaluator(debug_hook=debug,tool_handler=lambda *args:observed.append(args),step_limit=100000)
        with self.assertRaises(TaskCancelled):ev.run(source)
        self.assertEqual(observed,[]);self.assertEqual(len(captured[0].receivers),0)
        self.assertFalse(ev.suspension.active);self.assertTrue(all(task.state in TERMINAL for task in ev.suspension.tasks))

    def test_group_abort_does_not_become_loop_or_try_success(self):
        source='''let messages=channel();var events=[];
          async fn fail(){while channel_info(messages).waitingReceivers!=1{1};throw "peer"};
          let result=try{async with task_group() as group{group.spawn(fail());
          async for x in messages{}else{events=append(events,"loop else")}}}
          catch error{error.value}else{events=append(events,"try else");42};
          [result,events,channel_info(messages).waitingReceivers]'''
        self.assertEqual(Evaluator(step_limit=100000).run(source),['peer',[],0])

    def test_cancelled_iterator_worker_drains_before_loop_escapes(self):
        entered=threading.Event();finished=threading.Event();seen=[]
        def effect(kind,payload,cancel):
            name=payload['name'];seen.append(name)
            if name=='hold':
                entered.set()
                try:
                    if not cancel.wait(3):raise AssertionError('missing worker cancellation')
                    return {'done':True,'value':None}
                finally:finished.set()
            if name=='ready':self.assertTrue(entered.wait(3))
        source='''class Source{fn __aiter__(s){s}async fn __anext__(s){tool("hold",null)}};
          var target=null;async fn run(){async for x in Source(){}else{tool("wrong",null)}};
          async fn stop(){tool("ready",null);cancel(target)};target=run();spawn(stop());await target'''
        ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect)
        with self.assertRaises(TaskCancelled):ev.run(source)
        self.assertTrue(entered.is_set());self.assertTrue(finished.is_set());self.assertEqual(set(seen),{'hold','ready'})
        self.assertIsNone(ev.suspension.pool);self.assertEqual(ev.frames,[])

    def test_hard_controls_do_not_trigger_catch_else_or_cleanup(self):
        for failure in (ExecutionControl('host'),ResourceLimit('budget'),TaskCancelled('foreign'),KeyboardInterrupt(),SystemExit(7)):
            for protected in ['try{tool("stop",null)}catch e{tool("caught",null)}else{tool("else",null)}',
                              'for x in [1]{tool("stop",null)}else{tool("else",null)}']:
                seen=[]
                def effect(kind,payload,cancel):
                    seen.append(payload['name'])
                    if payload['name']=='stop':raise failure
                ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect)
                with self.subTest(failure=type(failure).__name__,protected=protected),self.assertRaises(type(failure)) as caught:
                    ev.run('async fn run(){try{'+protected+'}finally{tool("cleanup",null)}};await run()')
                self.assertIs(caught.exception,failure);self.assertEqual(seen,['stop']);self.assertIsNone(ev.suspension.pool)

    def test_budget_has_no_reset_and_else_cannot_gain_tool_authority(self):
        for source in ['while true{}else{tool("wrong",null)}',
                       'try{1}catch e{0}else{while true{}}finally{tool("wrong",null)}']:
            seen=[]
            with self.subTest(source=source),self.assertRaises(ResourceLimit):Evaluator(step_limit=150,tool_handler=lambda *args:seen.append(args)).run(source)
            self.assertEqual(seen,[])
        with self.assertRaises(LanguageError):Framework.evaluate('while false{}else{tool("write",null)}')
        with self.assertRaises(LanguageError):Framework.evaluate('try{1}catch e{0}else{tool("write",null)}')

    def test_pending_local_cancel_is_checked_before_else_and_shield_defers_it(self):
        seen=[]
        source='var target=null;async fn work(){while do{cancel(target);false}{}else{tool("else",null)}};target=work();await target'
        with self.assertRaises(TaskCancelled):Evaluator(tool_handler=lambda *a:seen.append(a)).run(source)
        self.assertEqual(seen,[])
        source='var target=null;async fn work(){with cancel_shield() as shield{while do{cancel(target);false}{}else{tool("else",42)}};tool("outside",null)};target=work();await target'
        with self.assertRaises(TaskCancelled):Evaluator(tool_handler=lambda *a:seen.append(a)).run(source)
        self.assertEqual(seen,[('else',42)])

    def test_suppressed_body_error_can_still_complete_normally(self):
        source='class C{fn __enter__(s){null}fn __exit__(s,error){true}};for x in [1]{with C() as value{throw 7}}else{42}'
        self.assertEqual(Evaluator().run(source),42)
        source='class C{fn __enter__(s){null}fn __exit__(s,error){true}};try{with C() as value{throw 7}}catch e{0}else{42}'
        self.assertEqual(Evaluator().run(source),42)

    def test_async_agent_success_and_fallback_stay_on_ast_scheduler(self):
        main=threading.get_ident();asts=set();workers=set();calls=[]
        def handler(name,config,value):workers.add(threading.get_ident());calls.append(value);return value*2
        source='''agent candidate{};async fn search(values){for value in values{
          let result=candidate(value);if result==42{return result}else{continue}}else{"exhausted"}};
          await gather([search([1,21]),search([2,3])])'''
        ev=Evaluator(agent_handler=handler,debug_hook=lambda *a:asts.add(threading.get_ident()))
        self.assertEqual(ev.run(source),[42,'exhausted']);self.assertEqual(sorted(calls),[1,2,3,21]);self.assertEqual(asts,{main});self.assertNotIn(main,workers)
