"""Real resource frame lifetime, historical semantics and protocol boundaries."""
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from chaos.language import (Evaluator,Env,Parser,Native,LanguageError,ResourceLimit,
                            ExecutionControl,TailCall)
from chaos.execution_frames import (ExecutionCursor,ContextFrame,ProtectedValueFrame,
                                    ProtocolBridge,frame_roots)
from chaos.suspension import Await,TaskCancelled
from _context_reference142 import reference


SYNC='''var events=[];class C{var value=null;var suppress=false;
fn __init__(s,value=42,suppress=false){s.value=value;s.suppress=suppress;null}
fn __enter__(s){events=append(events,"enter");s.value}
fn __exit__(s,error){events=append(events,if error==null{null}else{error.code});s.suppress}}
'''
ASYNC='''class A{async fn __aenter__(s){await sleep(0);events=append(events,"enter");[20,22]}
async fn __aexit__(s,error){await sleep(0);events=append(events,if error==null{null}else{error.code});false}}
'''


class ContextExecutionFrames(unittest.TestCase):
    def observe(self,source,*,old=False,limit=100000,cancel_at=None):
        e=Evaluator(step_limit=limit,timeout=30);effects=[];trace=[];debug=[];tick=e.tick
        def checkpoint():
            if cancel_at is not None and e.steps==cancel_at:raise TaskCancelled('fixture cancel')
            tick()
        e.tick=checkpoint
        e.tool_handler=lambda name,value:effects.append((name,value)) or value
        e.trace_handler=lambda event:trace.append(dict(event)) if event.get('event')=='expression' else None
        e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
        def run():
            try:return ('value',e.run(source))
            except BaseException as error:return ('error',type(error).__name__,getattr(error,'code',None),str(error),getattr(error,'location',None))
        if old:
            with reference(e):result=run()
        else:result=run()
        self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
        self.assertIsNone(e.handled_error);self.assertIsNone(e.error_context)
        self.assertIsNone(getattr(e,'_execution_cursor',None))
        return result,e.steps,effects,trace,debug

    def raw(self,source):
        e=Evaluator(timeout=30);e.suspension.begin();env=Env(e.env)
        return e,e._s_evaluate(Parser(source).program(),env),env

    def pause(self,cursor):
        while True:
            request=next(cursor)
            if type(request) is Await:return request

    def finish(self,cursor):
        with self.assertRaises(StopIteration) as caught:
            while True:cursor.send(None)
        return caught.exception.value

    def test_expected_semantics_match_legacy_values_steps_effects_debug_and_trace(self):
        cases=[
            (SYNC+'let result=with C(42) as x{x};[result,events]',[42,['enter',None]]),
            (SYNC+'let outer=99;let result=with C([20,22]) as [outer,y]{outer+y};[result,outer,events]',[42,99,['enter',None]]),
            (SYNC+'let result=with C([1],true) as [x,y]{tool("wrong",0)};[result,events]',[None,['enter','match_error']]),
            (SYNC+'try{with C() as x{throw 7}}catch e{[e.value,events]}',[7,['enter','user_throw']]),
            (SYNC+'fn work(){with C() as x{return x}};[work(),events]',[42,['enter',None]]),
            (SYNC+'var n=0;for x in [1,2]{with C() as r{n+=1;if x==1{continue}else{break}}};[n,events]',[2,['enter',None,'enter',None]]),
            (SYNC+'let r=with C() as a,C(a) as b{a+b};[r,events]',[84,['enter','enter',None,None]]),
            ('var events=[];'+ASYNC+'let result=async with A() as [x,y]{tool("body",x+y)};[result,events]',[42,['enter',None]]),
            ('var events=[];fn* resource(){tool("enter",0);yield ([20,22]);tool("exit",0)};with generator_context(resource()) as [x,y]{x+y}',42),
        ]
        for source,expected in cases:
            with self.subTest(source=source):
                actual=self.observe(source)
                self.assertEqual(actual,self.observe(source,old=True))
                self.assertEqual(actual[0],('value',expected))

    def test_budget_and_local_cancellation_at_every_step_match_original(self):
        source='class C{fn __enter__(s){1}fn __exit__(s,e){tool("exit",e);true}};with C() as x{tool("body",x);throw 7}'
        total=self.observe(source)[1]
        for threshold in range(total+2):
            with self.subTest(budget=threshold):self.assertEqual(self.observe(source,limit=threshold),self.observe(source,old=True,limit=threshold))
            with self.subTest(cancel=threshold):self.assertEqual(self.observe(source,cancel_at=threshold),self.observe(source,old=True,cancel_at=threshold))

    def test_all_four_syntax_kinds_bypass_legacy_execute(self):
        e=Evaluator();original=e._s_execute;seen=[]
        def execute(node,*args,**options):
            self.assertNotIn(node[0],{'with','async_with','with_pattern','async_with_pattern'})
            return original(node,*args,**options)
        def probe():
            frame=next(f for f in reversed(e._execution_cursor.frames) if type(f)is ContextFrame)
            seen.append(frame.node[0]);self.assertEqual(frame.phase,'body')
            self.assertTrue(any(type(f)is ProtectedValueFrame for f in e._execution_cursor.frames))
            return 42
        e._s_execute=execute;e.env.bind('probe',Native('probe',probe,e))
        source=SYNC+ASYNC+'[with C() as x{probe()},with C([42]) as [x]{probe()},async with A() as x{probe()},async with A() as [x,y]{probe()}]'
        self.assertEqual(e.run(source),[42]*4)
        self.assertEqual(seen,['with','with_pattern','async_with','async_with_pattern'])

    def test_manager_expression_is_one_live_boundary_across_suspension(self):
        source=SYNC+'var calls=0;with do{calls+=1;await sleep(0);C()} as x{await sleep(0);x}'
        e,c,env=self.raw(source)
        try:
            self.pause(c);frame=next(f for f in c.frames if type(f)is ContextFrame);env=frame.env
            self.assertEqual(frame.phase,'manager');self.assertIsNone(frame.entry)
            self.assertEqual(env.get('calls'),1)
            while type(c.send(None)) is not Await:pass
            self.assertEqual(frame.phase,'body');self.assertEqual(env.get('calls'),1)
            self.assertEqual(self.finish(c),42);self.assertEqual(env.get('calls'),1)
        finally:c.close();e.suspension.finish()

    def test_paused_body_scope_entry_and_resume_read_the_authoritative_state(self):
        e,c,env=self.raw(SYNC+'with C(42) as x{await sleep(0);x}')
        try:
            self.pause(c);frame=next(f for f in c.frames if type(f)is ContextFrame);env=frame.env
            self.assertEqual(frame.phase,'body');self.assertEqual(frame.acquired_value,42)
            self.assertIs(frame.scope.get('x'),frame.acquired_value)
            for value in (frame.env,frame.scope,frame.manager,frame.entry):self.assertIn(value,frame_roots(frame))
            self.assertFalse(frame.entry.released);frame.scope.values['x']=99
            self.assertEqual(self.finish(c),99);self.assertEqual(env.get('events'),['enter',None])
            self.assertIsNone(frame.entry);self.assertIsNone(frame.scope)
        finally:c.close();e.suspension.finish()

    def test_paused_exit_pending_result_changes_the_actual_completion(self):
        source='class C{async fn __aenter__(s){0}async fn __aexit__(s,e){await sleep(0);null}};async fn work(){async with C() as x{42}};await work()'
        e=Evaluator();observed=[]
        def debug(node,env,frames):
            if node[0]!='await':return
            for task in e.suspension.tasks:
                iterator=getattr(task.runner,'iterator',None)
                if type(iterator)is ExecutionCursor:
                    for frame in iterator.frames:
                        if type(frame)is ContextFrame and frame.phase=='exit':
                            self.assertEqual(frame.result,42);self.assertIn(frame.result,frame_roots(frame))
                            frame.result=99;observed.append(frame.entry)
        e.debug_hook=debug
        self.assertEqual(e.run(source),99);self.assertEqual(len(observed),1);self.assertTrue(observed[0].released)

    def test_exit_replacement_and_error_suppression_do_not_rewrite_original_error(self):
        original=LanguageError('body',value=42,code='fixture')
        for suppressed in (False,True):
            e=Evaluator(tool_handler=lambda name,value:(_ for _ in ()).throw(original));e.env.bind('original',original)
            source='class C{fn __enter__(s){0}fn __exit__(s,e){'+('true' if suppressed else 'false')+'}};with C() as x{tool("body",null)}'
            if suppressed:self.assertIsNone(e.run(source))
            else:
                with self.assertRaises(LanguageError) as caught:e.run(source)
                self.assertIs(caught.exception,original)
        source='class C{fn __enter__(s){0}fn __exit__(s,e){throw 99}};fn f(){with C() as x{return 42}};try{f()}catch e{e.value}'
        self.assertEqual(Evaluator().run(source),99)

    def test_forced_close_releases_lease_without_executing_user_exit(self):
        e,c,env=self.raw(SYNC+'with C() as x{await sleep(0);events=append(events,"body")}')
        try:
            self.pause(c);frame=next(f for f in c.frames if type(f)is ContextFrame);env=frame.env;entry=frame.entry
            self.assertFalse(entry.released);c.close();c.close()
            self.assertTrue(entry.released);self.assertEqual(env.get('events'),['enter'])
            self.assertEqual(c.frames,[]);self.assertIsNone(frame.manager);self.assertIsNone(frame.scope)
        finally:c.close();e.suspension.finish()

    def test_forced_close_binding_context_restores_dynamic_holder(self):
        source='let key=context_var("key",1);with key.set(42) as token{await sleep(0);key.get()}'
        e,c,env=self.raw(source)
        try:
            self.pause(c);frame=next(f for f in c.frames if type(f)is ContextFrame);env=frame.env;entry=frame.entry
            self.assertEqual(entry.kind,'binding');self.assertFalse(entry.released)
            from chaos.dynamic_contexts import get
            self.assertEqual(get(e,env.get('key')),42)
            c.close();self.assertTrue(entry.released)
            # Native access uses the same root holder as this manually driven
            # cursor; no child task or copied context can conceal a live lease.
            self.assertEqual(get(e,env.get('key')),1)
            self.assertIsNone(frame.entry)
        finally:c.close();e.suspension.finish()

    def test_acquire_bridge_is_only_a_native_leaf_before_the_real_body_frame(self):
        source='class C{async fn __aenter__(s){42}async fn __aexit__(s,e){null}};async with C() as x{99}'
        e,c,env=self.raw(source)
        try:
            request=self.pause(c);frame=next(f for f in c.frames if type(f)is ContextFrame)
            self.assertEqual(frame.phase,'acquire');self.assertIsNone(frame.entry)
            self.assertEqual(request.task.state,'created')
            self.assertFalse(any(type(f)is ProtectedValueFrame for f in c.frames))
            self.assertIs(type(c.frames[-1]),ProtocolBridge)
            self.assertIn(frame.manager,frame_roots(frame));c.close()
            self.assertIsNone(frame.manager);self.assertEqual(c.frames,[])
        finally:c.close();e.suspension.finish()

    def test_group_abort_runs_exit_but_true_cannot_suppress_its_identity(self):
        from chaos.task_groups import GroupAbort
        e,c,env=self.raw(SYNC+'with C(42,true) as x{await sleep(0);x}')
        failure=LanguageError('child',value=7);signal=GroupAbort(None,failure)
        try:
            self.pause(c);frame=next(f for f in c.frames if type(f)is ContextFrame);env=frame.env;entry=frame.entry
            with self.assertRaises(GroupAbort) as caught:
                c.throw(signal)
                while True:c.send(None)
            self.assertIs(caught.exception,signal);self.assertTrue(entry.released)
            self.assertEqual(env.get('events'),['enter','runtime_error'])
        finally:c.close();e.suspension.finish()

    def test_native_hard_teardown_error_does_not_replace_execution_control(self):
        original=TaskCancelled('body');seen=[]
        def body(name,value):raise original
        def native_exit(e,entry):seen.append(entry);raise RuntimeError('native stop failed')
        e=Evaluator(tool_handler=body)
        with patch('chaos.contexts.hard_exit_entry',native_exit):
            with self.assertRaises(TaskCancelled) as caught:
                e.run('class C{fn __enter__(s){0}fn __exit__(s,e){throw 99}};with C() as x{tool("stop",null)}')
        self.assertIs(caught.exception,original);self.assertEqual(len(seen),1);self.assertTrue(seen[0].released)

    def test_every_resource_frame_root_participates_in_authority_sealing(self):
        from chaos.capture_graph import seal
        from chaos.contexts import ExitEntry
        from chaos.generic_functions import NextDescriptor
        for field in ('env','manager','acquired_value','scope','result','pending_error','entry'):
            with self.subTest(field=field):
                e=Evaluator();capsule=NextDescriptor(e,None)
                token=Native('next',lambda:42,e,generic=capsule,captures=())
                boundary=SimpleNamespace(node=('with','x',('literal',None),('literal',None)),env=Env(),capture=SimpleNamespace(capabilities=frozenset()))
                frame=ContextFrame(boundary);frame.owner=e
                if field in ('env','scope'):
                    value=Env();value.bind('captured',token)
                elif field=='entry':value=ExitEntry('function',function=token)
                elif field=='pending_error':value=LanguageError('pending',value=token)
                else:value=token
                setattr(frame,field,value);seal(e,[frame],None)
                self.assertEqual(capsule.capabilities,frozenset())

    def test_frame_roots_reject_foreign_pending_completion(self):
        from chaos.capture_graph import seal
        e=Evaluator();other=Evaluator()
        boundary=SimpleNamespace(node=('with','x',None,None),env=Env(),capture=None)
        frame=ContextFrame(boundary);frame.owner=e;frame.result=Native('foreign',lambda:42,other)
        with self.assertRaises(LanguageError) as caught:seal(e,[frame],None)
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_protected_tail_and_all_multiple_values_survive_cleanup(self):
        sources=[
            SYNC+'var n=1;fn read(){n};class D(C){fn __exit__(s,e){n=2;null}};fn f(){with D() as x{return read()}};[f(),n]',
            SYNC+'fn recur(n,*,value:Int)->Int{if n==0{value}else{recur(n-1,value=value)}};with C() as x{recur(600,value=42)}',
            SYNC+'multiple_value_list(fn(){with C() as x{return_values(20,22)}})',
            SYNC+'multiple_value_list(fn(){with C() as x{return_values()}})',
        ]
        for source,expected in zip(sources,([1,2],42,[20,22],[])):
            with self.subTest(source=source):
                actual=self.observe(source);self.assertEqual(actual,self.observe(source,old=True))
                self.assertEqual(actual[0],('value',expected))

    def test_secondary_foreign_values_are_validated_before_resource_exit(self):
        for old in (False,True):
            e=Evaluator();foreign=Native('foreign',lambda:42,Evaluator())
            e.env.bind('foreign',foreign)
            source=SYNC+'let result=try{with C() as x{return_values(42,foreign)}}catch e{e.code};[result,events]'
            if old:
                with reference(e):result=e.run(source)
            else:result=e.run(source)
            self.assertEqual(result,['ownership_error',['enter','ownership_error']])

    def test_hard_controls_preserve_identity_skip_user_exit_and_retire_lease(self):
        for error in (ResourceLimit('limit'),ExecutionControl('stop'),TaskCancelled('cancel'),KeyboardInterrupt('interrupt'),SystemExit('exit')):
            e=Evaluator();held=[];calls=[]
            def effect(name,value):
                calls.append(name)
                frame=next(f for f in reversed(e._execution_cursor.frames) if type(f)is ContextFrame)
                held.append(frame.entry);raise error
            e.tool_handler=effect
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)) as caught:
                e.run('class C{fn __enter__(s){0}fn __exit__(s,e){tool("exit",null);true}};with C() as x{tool("stop",null)}')
            self.assertIs(caught.exception,error);self.assertEqual(calls,['stop']);self.assertTrue(held[0].released)

    def test_generator_contexts_and_outer_generator_close_keep_native_leaf_contract(self):
        for flavor,start,close in (('fn*','next(stream)','close(stream)'),('async fn*','await anext(stream)','await aclose(stream)')):
            source='var events=[];fn* resource(){events=append(events,"enter");yield 42;events=append(events,"exit")};'+flavor+' outer(){with generator_context(resource()) as x{yield x}};let stream=outer();let first='+start+';let before=events;'+close+';[first.value,before,events]'
            with self.subTest(flavor=flavor):self.assertEqual(Evaluator().run(source),[42,['enter'],['enter','exit']])
        source='var events=[];async fn* resource(){await sleep(0);yield ([20,22]);await sleep(0);events=append(events,"exit")};let answer=async with generator_context(resource(),true) as [x,y]{x+y};[answer,events]'
        self.assertEqual(Evaluator().run(source),[42,['exit']])

    def test_exit_stack_lifo_and_pattern_failure_use_original_native_exit_entries(self):
        source=SYNC+'let stack=exit_stack();stack.enter_context(C(20));stack.enter_context(C(22));let out=try{with stack as []{99}}catch e{e.code};[out,events]'
        actual=self.observe(source);self.assertEqual(actual,self.observe(source,old=True))
        self.assertEqual(actual[0],('value',['match_error',['enter','enter','match_error','match_error']]))
        source='var events=[];'+ASYNC+'let stack=exit_stack(true);await stack.enter_async_context(A());let out=async with stack as s{42};[out,events]'
        self.assertEqual(Evaluator().run(source),[42,['enter',None]])

    def test_device_computer_leaf_lifecycle_and_no_hidden_context_body_bridge(self):
        from test_devices import host,run
        from test_computer_values import ComputerFixture
        log=[]
        with host(log) as app:
            source='async with await device_open("desk") as d{let command=device_action(d,{action:"release"});let receipt=await d(command);[type_of(d),device_receipt_info(receipt).status]}'
            self.assertEqual(run(app,source),['Device','acknowledged'])
        self.assertEqual([row[0] for row in log],['open','act','close'])
        fixture=ComputerFixture();e=fixture.evaluator();states=[]
        def probe():
            frame=next(f for f in reversed(e._execution_cursor.frames) if type(f)is ContextFrame)
            states.append((frame.phase,frame.entry.kind,frame.entry.released));return 42
        e.env.bind('probe',Native('probe',probe,e))
        self.assertEqual(e.run('async with await computer_open() as c{probe()}'),42)
        self.assertEqual(states,[('body','computer',False)]);self.assertEqual([op for op,p in fixture.calls],['open','close'])

    def test_async_protocol_is_awaited_once_and_sync_task_value_remains_cold(self):
        source='var count=0;async fn value(){count+=1;42};class C{fn __enter__(s){value()}fn __exit__(s,e){null}};with C() as task{[type_of(task),count,await task,count]}'
        self.assertEqual(Evaluator().run(source),['Task',0,42,1])
        source='var calls=[];class C{async fn __aenter__(s){calls=append(calls,"enter");42}async fn __aexit__(s,e){calls=append(calls,"exit");null}};let n=async with C() as x{x};[n,calls]'
        self.assertEqual(Evaluator().run(source),[42,['enter','exit']])


if __name__=='__main__':unittest.main()
