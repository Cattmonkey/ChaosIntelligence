"""Whole suspension-expression algorithms against exact prior native routes.
All tests are private source proposals until a fresh bounded run executes them.
"""
import copy,json
from contextlib import contextmanager,nullcontext
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError,ResourceLimit,ExecutionControl,serializable
from chaos.suspension import Task,Effect,Await,Yield,CHECKPOINT
from chaos.execution_suspension import SuspensionSyntaxFrame,suspension_cursor
from chaos.execution_frames import ensure_cursor
from chaos.capture_graph import seal
from _suspension_reference201 import reference,KINDS
from _effect_schedule158 import effect_schedule
from test_operator_execution_frames import active
from test_callable_execution_frames import owned_frame,pending

WITNESS='async fn add(x){await sleep(0);x+1};fn* stream(){let value=yield 1;yield from [value,3];return 4};let g=stream();[await add(41),next(g),send(g,2),next(g),next(g),try{throw {answer:42}}catch e{try{throw}catch same{same.value.answer}}]'
EXPECTED=[42,{'done':False,'value':1},{'done':False,'value':2},{'done':False,'value':3},{'done':True,'value':4},42]

@contextmanager
def observe():
    rows={'steps':{},'frames':[]};original=SuspensionSyntaxFrame.step
    def step(frame,cursor,value,error):
        kind=frame.kind or frame.node[0];rows['steps'][kind]=rows['steps'].get(kind,0)+1
        rows['frames'].append((frame,cursor));return original(frame,cursor,value,error)
    with patch.object(SuspensionSyntaxFrame,'step',step):yield rows

def snapshot(source,legacy=False,limit=30000,cancel=None,setup=None,policy='already_done'):
    effects=[];trace=[];debug=[]
    def effect(kind,payload,event):effects.append((kind,copy.deepcopy(payload)));return payload['value']if kind=='tool'else payload.get('input')
    e=Evaluator(tool_handler=lambda name,value:value,effect_handler=effect,trace_handler=trace.append,step_limit=limit,timeout=30)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup:setup(e)
    with effect_schedule(policy)as schedule,reference()if legacy else nullcontext():
        try:result=('value',serializable(e.run(source)))
        except LanguageError as error:result=('error',error.record())
        except ExecutionControl as error:result=('control',type(error).__name__,str(error))
    return result,e.steps,effects,trace,debug,e.depth,len(e.frames),dict(schedule)

class SuspensionSyntaxFrames201(unittest.TestCase):
    def check(self,source,expected=None,**options):
        old=snapshot(source,legacy=True,**options);modern=snapshot(source,**options);self.assertEqual(modern,old)
        if expected is not None:self.assertEqual(modern[0],('value',expected))
        return modern

    def test_all_five_kinds_have_true_old_firstadvance_and_real_new_steps(self):
        for legacy in (True,False):
            held=[]
            with reference(held)if legacy else nullcontext()as reached,observe()as seen:
                self.assertEqual(Evaluator().run(WITNESS),EXPECTED)
                if legacy:
                    self.assertEqual(seen['steps'],{});self.assertTrue(all(reached.values()),reached)
                    self.assertTrue(held);self.assertTrue(all(iterator.gi_code is code for _,iterator,code in held))
                else:self.assertEqual(set(seen['steps']),set(KINDS));self.assertTrue(all(seen['steps'].values()))

    def test_entire_witness_preserves_result_steps_trace_debug_and_frames(self):self.check(WITNESS,EXPECTED)

    def test_sync_async_delegation_send_return_and_finally_order(self):
        sources=[('var events=[];fn* a(){try{yield 1;return 9}finally{events=append(events,"inner")}};fn* b(){try{let n=yield from a();yield n}finally{events=append(events,"outer")}};let g=b();let one=next(g);let two=send(g,null);close(g);[one,two,events]',[{'done':False,'value':1},{'done':False,'value':9},['inner','outer']]),('async fn* a(){yield 1;yield 2;return 3};async fn* b(){yield from a()};await acollect(b())',[1,2])]
        for source,expected in sources:
            with self.subTest(source=source):self.check(source,expected)

    def test_await_argument_self_deadlock_and_foreign_epoch_errors(self):
        for source in ['try{await 42}catch e{e.code}','var a=null;async fn f(){try{await a}catch e{e.message}};a=f();await a','var a=null;var b=null;async fn fa(){await b};async fn fb(){await a};a=fa();b=fb();try{await a}catch e{e.code}']:
            with self.subTest(source=source):self.check(source)
        def setup(e):e.env.bind('foreign',Evaluator().run('async fn old(){42};old()'))
        self.check('try{await foreign}catch e{e.code}',setup=setup)

    def test_actual_owned_agent_operation_uses_existing_resource_await(self):
        def setup(e):
            def make():
                def action():return (yield Effect('tool',{'name':'owned_operation','value':42}))
                task=e.suspension.task(action(),capture_roots=());task.agent_operation=True;return task
            e.env.bind('operation',Native('operation',make,e,captures=()))
        records=[]
        for legacy in (True,False):
            from chaos import contexts
            original=contexts._await;seen=[]
            def await_(e,target,protocol_owned=False,**options):
                self.assertIs(type(target),Task);self.assertTrue(target.agent_operation);self.assertEqual(target.state,'created')
                self.assertEqual(target.parent_task_id,e.suspension.current.task.id);self.assertIs(protocol_owned,True);self.assertIs(options['all_results'],True)
                seen.append(target);return original(e,target,protocol_owned,**options)
            with patch.object(contexts,'_await',await_):result=snapshot('async fn work(){await operation()};await work()',legacy=legacy,setup=setup,policy='first_wait')
            self.assertEqual(result[0],('value',42));self.assertEqual(len(seen),1);self.assertEqual(seen[0].state,'completed');records.append(result)
        self.assertEqual(records[0],records[1])

    def test_borrowed_ordinary_await_does_not_acquire_new_cancel_ownership(self):
        source='async fn work(){42};let borrowed=work();async fn waiter(){await borrowed};let owner=waiter();cancel(owner);[task_info(borrowed).state,task_info(borrowed).cancellationRequested,await borrowed]'
        self.check(source,['created',False,42])

    def test_started_sibling_survives_other_cancellation(self):
        self.check('async fn sibling(){await sleep(0);42};async fn consumer(t){await t};let t=spawn(sibling());let w=consumer(t);cancel(w);await t',42)

    def test_yield_outside_stream_and_group_or_shield_fail_through_original_boundaries(self):
        self.check('try{await 0}catch e{e.code}','argument_error')
        self.check('fn* g(){try{yield 1}finally{yield 2}};let x=g();next(x);let code=try{close(x)}catch e{e.code};[code,next(x)]',['runtime_error',{'done':True,'value':None}])

    def test_error_values_rethrow_identity_and_explicit_cause_preserved(self):
        from chaos.error_values import capture
        for kind in ('throw','rethrow'):
            for legacy in (True,False):
                e=Evaluator();cause=LanguageError('cause',code='fixture_cause');failure=LanguageError('same',code='fixture_error');failure.__cause__=cause
                wrapped=capture(e,failure);held=[]
                node=('throw',('literal',wrapped))if kind=='throw'else('rethrow',)
                with active(e,None),reference(held)if legacy else nullcontext():
                    e.handled_error=failure;iterator=e._s_execute(node,e.env)
                    with self.assertRaises(LanguageError)as caught:pending(ensure_cursor(e,iterator))
                    self.assertIs(caught.exception,failure);self.assertIs(caught.exception.__cause__,cause)
                    self.assertEqual(e.depth,0)

    def test_escaping_host_stopiteration_only_converts_at_native_boundary(self):
        for kind in KINDS:
            results=[]
            for legacy in (True,False):
                e=Evaluator();fault=StopIteration({'sentinel':kind});events=[]
                with active(e,None),reference()if legacy else nullcontext(),patch.object(e,'tick',side_effect=fault):
                    node=('rethrow',)if kind=='rethrow'else(kind,('literal',42));iterator=e._s_execute(node,e.env)
                    try:iterator.send(None)
                    except BaseException as error:events.append((type(error).__name__,str(error),error.__cause__ is fault))
                    else:self.fail('host StopIteration must not become driver completion')
                results.append(events)
            self.assertEqual(results[0],results[1]);self.assertEqual(results[0],[('RuntimeError','generator raised StopIteration',True)])

    def test_cold_throw_and_close_do_not_enter_capture_or_tick(self):
        from chaos import capture_frames
        for legacy in (True,False):
            for operation in ('close','throw'):
                e=Evaluator();events=[];failure=LanguageError('cold',code='fixture_cold')
                with reference()if legacy else nullcontext(),patch.object(e,'tick',side_effect=lambda:events.append('tick')),patch.object(capture_frames,'active',side_effect=lambda _:events.append('capture')):
                    iterator=e._s_execute(('yield',('literal',42)),e.env)
                    if operation=='close':self.assertIsNone(iterator.close())
                    else:
                        with self.assertRaises(LanguageError)as caught:iterator.throw(failure)
                        self.assertIs(caught.exception,failure)
                    self.assertEqual(events,[])

    def test_entry_capture_precedes_single_tick_and_normal_completion_is_not_pep479(self):
        from chaos import capture_frames
        for legacy in (True,False):
            e=Evaluator();events=[]
            with active(e,None),reference()if legacy else nullcontext(),patch.object(capture_frames,'active',side_effect=lambda _:events.append('capture')),patch.object(e,'tick',side_effect=lambda:events.append('tick')):
                iterator=e._s_execute(('rethrow',),e.env)
                with self.assertRaises(LanguageError)as caught:iterator.send(None)
                self.assertEqual(caught.exception.code,'rethrow_error');self.assertEqual(events,['capture','tick'])
        self.check('async fn f(){42};await f()',42)

    def test_compiled_fallback_and_multiple_value_projection_keep_same_behavior(self):
        for target in ('interpreter','auto','auto-control','auto-flow'):
            source='fn f(x){if x>0{throw x}else{x}};let compiled=compile_function(f,TARGET);try{compiled(42)}catch e{e.value}'.replace('TARGET',json.dumps(target))
            with self.subTest(target=target):self.check(source,42)
        self.check('async fn f(){return_values(1,2)};await f()',1)

    def test_shared_budget_and_cancel_prefixes_keep_original_steps(self):
        normal=self.check(WITNESS,EXPECTED)
        for limit in (1,2,5,17,normal[1]-1,normal[1]):
            with self.subTest(limit=limit):self.assertEqual(snapshot(WITNESS,limit=limit),snapshot(WITNESS,legacy=True,limit=limit))
        for cancel in (1,7,normal[1]-1):self.assertEqual(snapshot(WITNESS,cancel=cancel),snapshot(WITNESS,legacy=True,cancel=cancel))

    def test_pending_metadata_owner_epoch_and_foreign_roots_rejected_atomically(self):
        foreign=Evaluator().run('fn foreign(){1}')
        e=Evaluator()
        with active(e,None):
            cursor=ensure_cursor(e,e._s_execute(('yield',('literal',42)),e.env));self.assertIsInstance(pending(cursor),Yield)
            frame=owned_frame(self,cursor,SuspensionSyntaxFrame)
            for field,bad in [('phase',foreign),('kind',foreign),('epoch',True),('native_completed',foreign),('asynchronous',foreign),('env',foreign),('operand',foreign),('capabilities',foreign),('dependencies',foreign)]:
                old=getattr(frame,field);setattr(frame,field,bad)
                try:
                    with self.subTest(field=field),self.assertRaises(LanguageError)as caught:seal(e,(cursor,),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
                finally:setattr(frame,field,old)
            local=e.env.get('tool');before=local.capabilities;old=frame.operand;frame.operand=foreign
            try:
                with self.assertRaises(LanguageError):seal(e,(cursor,local),frozenset())
                self.assertIs(local.capabilities,before)
            finally:frame.operand=old;cursor.close()

    def test_pending_phase_is_authoritative_and_does_not_replay_operand(self):
        e=Evaluator();effects=[]
        with active(e,None):
            cursor=ensure_cursor(e,e._s_execute(('yield',('literal',42)),e.env));self.assertIsInstance(pending(cursor),Yield)
            frame=owned_frame(self,cursor,SuspensionSyntaxFrame);self.assertEqual(frame.phase,'yield_return')
            frame.phase='operand';frame.kind='throw'
            with self.assertRaises(LanguageError)as caught:cursor.send({'answer':42})
            self.assertEqual(caught.exception.value,{'answer':42});self.assertTrue(cursor.closed)

    def test_borrowed_target_already_pending_is_not_cancelled_with_its_waiter(self):
        results=[]
        from chaos import suspension
        for legacy in (True,False):
            records=[];effects=[];seen=[]
            e=Evaluator(tool_handler=lambda name,value:value,effect_handler=lambda kind,payload,event:effects.append(payload['name'])or payload['value'],timeout=30)
            e.env.bind('remember',Native('remember',lambda target,waiter:records.append((target,waiter))or None,e,captures=()))
            with effect_schedule('first_wait')as schedule,reference()if legacy else nullcontext():
                original=suspension.wait
                def wait(futures,*args,**kwargs):
                    futures=tuple(futures)
                    try:
                        if records and not seen:
                            target,waiter=records[0];future=target.runner.future
                            if future is not None and any(future is item for item in futures):
                                self.assertFalse(future.done());self.assertIs(waiter.runner.waiting,target);self.assertEqual(waiter.runner.state,'waiting')
                                e.suspension.cancel(waiter);self.assertFalse(target.cancel_event.is_set());seen.append(True)
                    finally:completed=original(futures,*args,**kwargs)
                    return completed
                with patch.object(suspension,'wait',wait):
                    value=e.run('async fn target(){tool("borrowed_hold",42)};async fn waiter(t){await t};let borrowed=spawn(target());let w=spawn(waiter(borrowed));remember(borrowed,w);await borrowed')
            self.assertEqual(value,42);self.assertEqual(seen,[True]);self.assertEqual(effects,['borrowed_hold'])
            target,waiter=records[0];self.assertEqual(target.state,'completed');self.assertEqual(waiter.state,'cancelled');self.assertFalse(target.cancel_event.is_set())
            self.assertEqual(schedule['waitBoundaryReleases'],1);self.assertEqual(schedule['shutdownReleases'],0)
            results.append((value,e.steps,effects,target.state,waiter.state,dict(schedule)))
        self.assertEqual(results[0],results[1])

    def test_robot_marker_preserves_ordinary_await_instead_of_resource_await_expansion(self):
        from chaos import contexts
        def setup(e):
            def make():
                def action():return(yield Effect('tool',{'name':'robot_marker_contract','value':42}))
                task=e.suspension.task(action(),capture_roots=());task.robot_operation=True;return task
            e.env.bind('operation',Native('operation',make,e,captures=()))
        with patch.object(contexts,'_await',side_effect=AssertionError('robot ordinary syntax must not gain special ResourceAwait routing')):
            self.check('async fn work(){await operation()};await work()',42,setup=setup,policy='first_wait')

    def test_await_and_yield_import_aliases_bind_at_first_advance_not_resume(self):
        import chaos.suspension as module
        for kind in ('await','yield'):
            for legacy in (True,False):
                e=Evaluator();held=[]
                with active(e,None),reference(held)if legacy else nullcontext():
                    def cold():
                        if False:yield None
                        return 42
                    value=e.suspension.task(cold(),capture_roots=())if kind=='await'else 42
                    def operand():
                        yield Effect('tool',{'name':'alias-hold','value':None})
                        return value
                    child=('literal',None);original=e._s_evaluate
                    def evaluate(node,env,*args,**kwargs):return operand()if node is child else original(node,env,*args,**kwargs)
                    saved=module.Await if kind=='await'else module.Yield
                    with patch.object(e,'_s_evaluate',evaluate):
                        iterator=e._s_execute((kind,child),e.env);self.assertIs(type(iterator.send(None)),Effect)
                        with patch.object(module,'Await'if kind=='await'else'Yield',side_effect=AssertionError('late alias incorrectly reimported')):
                            request=iterator.send(None);self.assertIs(type(request),saved)
                            if kind=='await':self.assertIs(request.task,value)
                            else:self.assertEqual(request.value,42)
                        iterator.close()
