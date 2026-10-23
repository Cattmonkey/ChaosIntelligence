"""Complete dynamic resource phases against all fourteen original algorithms."""
import copy
import threading
import unittest
from contextlib import contextmanager,nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native,Env
from chaos.suspension import Task,Runner,Await,Drain,CHECKPOINT,TERMINAL,TaskCancelled
from chaos.execution_frames import ExecutionCursor,Push,Done,External,InvocationFrame,ProtocolBridge
from chaos.execution_context_protocols import (CONTEXT_PROTOCOL_FRAMES,ContextProtocolRequest,ContextRunFrame,
 ResourceAwaitFrame,ResourceInvokeFrame,GeneratorContextOperationFrame,GeneratorContextExitFrame,
 ResourceAcquireFrame,ResourceExitCallFrame,ResourceExitEntryFrame,StackRequestFrame,StackExecuteFrame,
 StackUnwindFrame,StackFinalizeScopeFrame)
from chaos.contexts import ExitEntry
from chaos.resource_stacks import StackRequest
from chaos.dynamic_contexts import RunRequest

SYNC='''var seen=[];class C{var n=0;fn __init__(self,n=42){self.n=n;null}
fn __enter__(self){seen=append(seen,["enter",self.n]);self.n}
fn __exit__(self,e){seen=append(seen,["exit",self.n]);null}}'''
ASYNC='''var seen=[];class A{async fn __aenter__(self){await sleep(0);seen=append(seen,"enter");42}
async fn __aexit__(self,e){await sleep(0);seen=append(seen,"exit");null}}'''
GEN='''var seen=[];fn* resource(){try{seen=append(seen,"enter");yield 42}finally{seen=append(seen,"exit")}}'''


def snapshot(source,legacy=False,limit=100000,cancel=None,setup=None,seen=None):
    from _resource_reference152 import reference
    effects,trace,debug,checkpoints=[],[],[],[]
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:effects.append((n,copy.deepcopy(v)))or v,trace_handler=trace.append)
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    if setup is not None:setup(e)
    original=ExecutionCursor._advance
    def observed(cursor,value,error):
        request=original(cursor,value,error)
        if request is CHECKPOINT:
            s=e.suspension;t=s.current.task if s.current else None
            checkpoints.append((e.steps,dict(e.current_span),t.id if t else None,s.stream.id if s.stream else None))
        return request
    with patch.object(ExecutionCursor,'_advance',observed),reference(seen)if legacy else nullcontext():
        try:outcome=('value',e.run(source))
        except LanguageError as error:outcome=('error',error.record())
        except ExecutionControl as error:outcome=('control',type(error).__name__,str(error))
    return outcome,e.steps,effects,trace,debug,checkpoints

def recorded(source,legacy=False,seen=None):
    from chaos import Framework,Settings
    from _resource_reference152 import reference
    with reference(seen) if legacy else nullcontext():
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('resource-recording',source=source,capabilities=['text.normalize'])
            return app.start(project['id'],project['version'],None,mode='source').result(10)

@contextmanager
def active(e,caps=None,team=None):
    s=e.suspension;s.begin();task=Task(s,iter(()));runner=Runner(iter(()),task=task)
    runner.capabilities=caps;runner.team_scope=team
    runner.control_state=s.control_state;runner.context_state=s.context_state;runner.condition_state=s.condition_state;runner.decimal_state=s.decimal_state
    s.current=runner
    try:yield runner
    finally:s.current=None;s.finish()

class ResourceProtocolFrames(unittest.TestCase):
    def check(self,source,expected,**kwargs):
        a=snapshot(source,**kwargs);self.assertEqual(a[0],('value',expected));self.assertEqual(a,snapshot(source,legacy=True,**kwargs));return a
    def own(self,e,f,caps=None):f.owner=e;f.capabilities=caps;return SimpleNamespace(owner=e)
    def prototypes(self):
        run=RunRequest(None,None,[],{});stack=StackRequest(None,'close')
        return [ContextRunFrame(run),ResourceAwaitFrame(None),ResourceInvokeFrame(None,[]),GeneratorContextOperationFrame(None),GeneratorContextExitFrame(None),ResourceAcquireFrame(None),ResourceExitCallFrame(None,None,False,None),ResourceExitEntryFrame(ExitEntry('binding')),StackRequestFrame(stack),StackExecuteFrame(stack),StackUnwindFrame(None),StackFinalizeScopeFrame(None)]
    def test_all14_compatibility_boundaries_are_thin_requests(self):
        from chaos import contexts as c,resource_stacks as s,dynamic_contexts as d
        e=Evaluator();e._execution_cursor=object();request=StackRequest(None,'close');run=RunRequest(None,None,[],{})
        values=[c._manager_operation(e,None),c._manager_exit(e,None),c._await(e,None),c._invoke(e,None,[],False),c._exit(e,None,None,False,None),c.acquire(e,None),c.exit_entry(e,None),s.unwind(e,None),s._unwind(e,None),s._execute(e,request),s._execute_scoped(e,request),s.resolve(e,request),s.finalize_scope(e,None),d.resolve(e,run)]
        expected=[ContextProtocolRequest]*11+[StackRequest,ContextProtocolRequest,RunRequest]
        self.assertEqual([type(next(v))for v in values],expected)
        e._execution_cursor=None
        for fn in (lambda:c.acquire(e,None),lambda:c._await(e,None),lambda:s.resolve(e,request),lambda:d.resolve(e,run)):
            cursor=fn();self.assertIs(type(cursor),ExecutionCursor);self.assertIs(type(cursor.root_bridge),ProtocolBridge);self.assertFalse(getattr(cursor.root_bridge,'supplementary',False));cursor.close()
    def test_original_oracle_all14_algorithms_have_reached_witnesses(self):
        from _resource_reference152 import ALGORITHMS
        seen=[]
        source=SYNC+ASYNC.replace('var seen=[];','')+GEN.replace('var seen=[];','')+'''let c=context();c.run(fn(){42});with generator_context(resource()) as v{v};
        async with A() as v{v};with exit_stack() as s{s.enter_context(C());s.callback(fn(){null})};
        42'''
        self.assertEqual(snapshot(source,legacy=True,seen=seen)[0],('value',42))
        self.assertEqual(recorded('fn action(x){let s=exit_stack();s.callback(fn(){null});tool("text.normalize"," resource " );x}let r=await record_skill(action,42);r.output',legacy=True,seen=seen),42)
        self.assertEqual(set(seen),{module.__name__+'.'+name for module,algorithms in ALGORITHMS for name in algorithms})
    def test_context_force_flags_and_resource_ordinary_flags(self):
        e=Evaluator();fn=Native('value',lambda:42,e)
        with active(e):
            from chaos.dynamic_contexts import Context
            c=Context(e.suspension);f=ContextRunFrame(RunRequest(c,fn,[],{}));action=f.step(self.own(e,f),None,None)
            self.assertIs(type(action.frame),InvocationFrame);self.assertTrue(action.frame.force);self.assertTrue(action.frame._context_callback);self.assertTrue(action.frame.all_results);f.close(self.own(e,f))
            f=ResourceInvokeFrame(fn,[]);action=f.step(self.own(e,f),None,None)
            self.assertIs(type(action.frame),InvocationFrame);self.assertFalse(action.frame.force);self.assertFalse(action.frame._context_callback);self.assertFalse(action.frame.all_results)
    def test_context_kwargs_persistent_bindings_and_outer_restore(self):
        self.check('let v=context_var("x",0);let c=context();fn f(x,*,n){v.set(x+n);v.get()}[c.run(f,[40],{n:2}),c.get(v),v.get()]',[42,42,0])
    def test_context_nonreentrance_and_error_restore(self):
        self.check('let v=context_var("x",0);let c=context();let code=c.run(fn(){v.set(42);try{c.run(fn(){0})}catch e{e.code}});try{c.run(fn(){throw 1})}catch e{null};[code,v.get(),c.run(fn(){v.get()})]',['context_busy',0,42])
    def test_context_async_force_and_returned_cold_generator(self):
        self.check('let v=context_var("x",0);let c=context();let n=c.run(async fn(){await sleep(0);v.set(42);v.get()});fn* values(){yield 41}let g=c.run(fn(){values()});let h=c.run(fn*(){yield 42});[n,v.get(),next(g).value,next(h).value]',[42,0,41,42])
    def test_context_full_multiple_results_and_nested_identity(self):
        self.check('let a=context();let b=context();let v=context_var("x",0);multiple_value_list(fn(){a.run(fn(){v.set(40);b.run(fn(){v.set(2)});return_values(v.get()+b.get(v),v.get())})})',[42,40])
    def test_acquire_captures_exit_before_enter_mutation(self):
        self.check('var seen=[];class C{fn __enter__(self){C.__exit__=fn(e){seen=append(seen,"new");null};42}var __exit__=fn(e){seen=append(seen,"old");null}}let n=with C() as n{n};[n,seen]',[42,['old']])
    def test_acquire_validates_both_hooks_before_user_enter(self):
        self.check('var n=0;class Bad{fn __enter__(self){n=n+1;42}let __exit__=0}let code=try{with Bad() as x{x}}catch e{e.code};[code,n]',['argument_error',0])
    def test_descriptor_resolution_order_and_instance_shadow(self):
        self.check('var seen=[];class D{fn __get__(self,obj,owner){seen=append(seen,"bind");fn(e){seen=append(seen,"exit");null}}}class C{fn __enter__(self){seen=append(seen,"enter");42}let __exit__=D()}let n=with C() as n{n};[n,seen]',[42,['bind','enter','exit']])
    def test_sync_enter_returns_cold_task_without_implicit_wait(self):
        self.check('var n=0;async fn acquire(){n=n+1;42}class C{fn __enter__(self){acquire()}fn __exit__(self,e){null}}with C() as t{[type_of(t),n,await t]}',['Task',0,42])
    def test_async_entry_and_exit_await_once(self):
        self.check(ASYNC+'let n=async with A() as n{n};[n,seen]',[42,['enter','exit']])
        for source in ['class C{fn __aenter__(self){42}async fn __aexit__(self,e){null}}','class C{async fn __aenter__(self){42}fn __aexit__(self,e){null}}']:
            a=snapshot(source+'async with C() as n{n}');self.assertEqual(a,snapshot(source+'async with C() as n{n}',legacy=True));self.assertEqual(a[0][0],'error')
    def test_generator_one_shot_identity_and_alias_reservation(self):
        self.check(GEN+'let g=resource();let m=generator_context(g);let code=try{next(g)}catch e{e.code};let n=with m as n{n};[code,n,seen,try{with m as n{n}}catch e{e.code}]',['context_error',42,['enter','exit'],'context_error'])
    def test_async_generator_manager_cleanup_and_shared_context(self):
        self.check('let v=context_var("x",0);var seen=[];async fn* resource(){v.set(41);try{yield v.get()}finally{await sleep(0);seen=append(seen,v.get())}}let n=async with generator_context(resource(),true) as n{v.set(n+1);n};[n,seen,v.get()]',[41,[41],42])
    def test_generator_zero_and_extra_yield_contract_close_once(self):
        self.check('var n=0;fn* resource(){try{yield 1;yield 2}finally{n=n+1}}let code=try{with generator_context(resource()) as x{x}}catch e{e.code};[code,n]',['context_error',1])
        source='fn* resource(){return 1}with generator_context(resource()) as x{x}';a=snapshot(source);self.assertEqual(a,snapshot(source,legacy=True));self.assertEqual(a[0][0],'error')
    def test_generator_original_error_identity_and_cleanup_replacement(self):
        self.check('var seen=[];fn* resource(){try{yield 42}catch e{seen=append(seen,e.value)}}[with generator_context(resource()) as x{throw "body"},seen]',[None,['body']])
        self.check('fn* resource(){try{yield 42}finally{throw "exit"}}try{with generator_context(resource()) as n{throw "body"}}catch e{e.value}','exit')
    def test_exit_bool_null_and_invalid_result_matrix(self):
        for answer,expected in [('true',None),('false','body'),('null','body')]:self.check('class C{fn __enter__(self){42}fn __exit__(self,e){'+answer+'}}try{with C() as n{throw "body"}}catch e{e.value}',expected)
        for answer in ['42','[]','async fn(){true}()']:
            source='class C{fn __enter__(self){42}fn __exit__(self,e){'+answer+'}}with C() as n{n}';a=snapshot(source);self.assertEqual(a,snapshot(source,legacy=True));self.assertEqual(a[0][0],'error')
    def test_soft_return_break_and_escape_cleanup(self):
        self.check('var n=0;class C{fn __enter__(self){42}fn __exit__(self,e){n=n+1;true}}fn work(){with C() as x{return x}}let a=work();for x in [1,2]{with C() as r{break}}let b=call_escape(fn(exit){with C() as r{exit(r)}});[a,b,n]',[42,42,3])
    def test_hard_controls_skip_user_exits_and_restore_frames(self):
        for failure in [ResourceLimit('limit'),ExecutionControl('stop'),TaskCancelled('cancel'),KeyboardInterrupt('interrupt')]:
            for legacy in (False,True):
                from _resource_reference152 import reference
                calls=[];e=Evaluator(tool_handler=lambda n,v:calls.append(n))
                def stop(n,v):calls.append(n);raise failure
                e.tool_handler=stop
                with reference()if legacy else nullcontext():
                    with self.assertRaises(type(failure))as caught:e.run('class C{fn __enter__(self){null}fn __exit__(self,e){tool("exit",null);true}}with C() as x{tool("stop",null)}')
                self.assertIs(caught.exception,failure);self.assertEqual(calls,['stop']);self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
    def test_stack_all_nine_operations_and_transfer(self):
        self.check(SYNC+'''class A{async fn __aenter__(self){7}async fn __aexit__(self,e){seen=append(seen,"async-exit");null}}
        let s=exit_stack(true);s.callback(fn(){seen=append(seen,"callback");null});s.push(fn(e){seen=append(seen,"push");null});
        s.enter_context(C(1));await s.enter_async_context(A());s.push_async_exit(async fn(e){seen=append(seen,"async-push");null});
        s.push_async_callback(async fn(){seen=append(seen,"async-callback");null});let moved=s.pop_all();await s.aclose();await moved.aclose();
        let plain=exit_stack();plain.close();seen''',[["enter",1],'async-callback','async-push','async-exit',['exit',1],'push','callback'])
    def test_stack_push_descriptor_captures_original_exit(self):
        self.check('var seen=[];class C{fn __enter__(self){throw "unused"}var __exit__=fn(e){seen=append(seen,"old");null}}with exit_stack() as s{s.push(C());C.__exit__=fn(e){seen=append(seen,"new");null}};seen',['old'])
    def test_stack_callback_argument_snapshot_and_kwargs(self):
        self.check('var seen=[];var a=[1];fn cleanup(x,*,n){seen=append(seen,[x,n]);42}with exit_stack() as s{s.callback(cleanup,a,n=42);a=[99]};seen',[[[1],42]])
    def test_stack_lifo_error_replacement_and_suppression(self):
        self.check('var seen=[];let n=with exit_stack() as s{s.push(fn(e){seen=append(seen,e);null});s.push(fn(e){seen=append(seen,e.value);true});s.callback(fn(){throw "replacement"});throw "body"};[n,seen]',[None,['replacement',None]])
    def test_stack_nested_cycle_lifo_and_pop_reparent(self):
        self.check('var seen=[];let a=exit_stack();let b=exit_stack();a.callback(fn(){seen=append(seen,"a");null});a.enter_context(b);b.callback(fn(){seen=append(seen,"b");null});let code=try{b.enter_context(a)}catch e{e.code};let moved=a.pop_all();moved.close();[seen,code]',[['b','a'],'context_error'])
    def test_stack_reserves_budget_before_descriptor_effects(self):
        source='class D{fn __get__(self,o,c){tool("unexpected",null);fn(){42}}}class C{let __enter__=D();fn __exit__(self,e){null}}with exit_stack(max_entries=0) as s{s.enter_context(C())}'
        a=snapshot(source);self.assertEqual(a,snapshot(source,legacy=True));self.assertEqual(a[0][0],'error');self.assertEqual(a[2],[])
    def test_stack_explicit_close_busy_and_idempotent(self):
        self.check('var seen=[];let s=exit_stack();s.push(fn(e){seen=append(seen,e.code);true});s.callback(fn(){s.close()});s.close();s.close();[seen,try{s.callback(fn(){null})}catch e{e.code}]',[['context_busy'],'context_error'])
    def test_async_stack_busy_admission_is_channel_deterministic(self):
        self.check('''let ready=channel(1);let gate=channel();class C{async fn __aenter__(self){await channel_send(ready,0);await channel_receive(gate);42}async fn __aexit__(self,e){null}}
        let s=exit_stack(true);let first=spawn(s.enter_async_context(C()));await channel_receive(ready);let code=try{s.callback(fn(){null})}catch e{e.code};await channel_send(gate,0);let n=await first;await s.aclose();[n,code]''',[42,'context_busy'])
    def test_binding_leases_stack_lifo_and_outer_restore(self):
        self.check('let v=context_var("x",0);with exit_stack() as s{s.enter_context(context_bind([{variable:v,value:40}]));s.enter_context(context_bind([{variable:v,value:42}]));v.get()};v.get()',0)
    def test_recorder_finalizes_newly_created_detached_stacks(self):
        source='var seen=[];fn action(x){let s=exit_stack();s.callback(fn(){seen=append(seen,1);let t=exit_stack();t.callback(fn(){seen=append(seen,2);null});null});tool("text.normalize"," resource " );x}let saved=await record_skill(action,42);[seen,saved.output,length(mode_info(saved.skill).actions)]'
        self.assertEqual(recorded(source),[[1,2],42,1]);self.assertEqual(recorded(source),recorded(source,legacy=True))
    def test_owned_and_borrowed_structured_await_cancellation(self):
        e=Evaluator()
        with active(e)as runner:
            runner.task.context_structured=True
            for owned in (False,True):
                t=e.suspension.task(iter(()));f=ResourceAwaitFrame(t,owned);c=self.own(e,f)
                self.assertIs(type(f.step(c,None,None).request),Await);failure=TaskCancelled('original')
                if not owned:
                    with self.assertRaises(TaskCancelled)as caught:f.step(c,None,failure)
                    self.assertIs(caught.exception,failure);self.assertFalse(t.cancel_event.is_set())
                else:
                    action=f.step(c,None,failure);self.assertIs(type(action.request),Drain);self.assertTrue(t.cancel_event.is_set())
                    with self.assertRaises(TaskCancelled)as caught:f.step(c,None,None)
                    self.assertIs(caught.exception,failure)
            from chaos.task_groups import TaskGroup,GroupAbort
            group=TaskGroup(e.suspension);runner.task.scope_group=group
            task=Task(e.suspension,iter(()));task.group=group;f=ResourceAwaitFrame(task,True);c=self.own(e,f);f.step(c,None,None)
            local=TaskCancelled('outer',_cancel_event=runner.task.cancel_event)
            f.step(c,None,local);self.assertIs(f.source,group);self.assertEqual(task.cancel_sources,{group})
            late=ResourceLimit('late')
            with self.assertRaises(ResourceLimit) as caught:f.step(c,None,late)
            self.assertIs(caught.exception,late)
            # Equal-priority hard failures retain the original; genuine local
            # cancellation has lower priority than the late ResourceLimit.
            global_cancel=TaskCancelled('global')
            task=Task(e.suspension,iter(()));task.group=group;f=ResourceAwaitFrame(task,True);c=self.own(e,f);f.step(c,None,None)
            self.assertIs(type(f.step(c,None,global_cancel).request),Drain)
            with self.assertRaises(TaskCancelled) as caught:f.step(c,None,late)
            self.assertIs(caught.exception,global_cancel)
            from chaos import contexts
            from _resource_reference152 import reference
            for original,expected in ((local,late),(global_cancel,global_cancel)):
                task=Task(e.suspension,iter(()));task.group=group
                with reference():
                    iterator=contexts._await(e,task,True)
                    self.assertIs(type(next(iterator)),Await)
                    self.assertIs(type(iterator.throw(original)),Drain)
                    self.assertEqual(task.cancel_sources,{group})
                    with self.assertRaises(type(expected)) as caught:iterator.throw(late)
                    self.assertIs(caught.exception,expected)
            task=Task(e.suspension,iter(()));task.group=group;f=ResourceAwaitFrame(task,True,cleanup_operation=True);c=self.own(e,f);f.step(c,None,None)
            signal=GroupAbort(group,LanguageError('member'))
            with patch.object(e.suspension,'interrupt')as interrupt:
                self.assertIs(type(f.step(c,None,signal).request),Drain);interrupt.assert_called_once_with(task,signal)
            with self.assertRaises(GroupAbort)as caught:f.step(c,None,None)
            self.assertIs(caught.exception,signal);runner.task.scope_group=None
    def test_await_full_results_and_unstarted_throw_identity(self):
        from chaos.multiple_values import make,components
        e=Evaluator()
        with active(e):
            t=e.suspension.task(iter(()));f=ResourceAwaitFrame(t,all_results=True);c=self.own(e,f);f.step(c,None,None)
            packet=make(e,(40,42));self.assertIs(f.step(c,packet,None).value,packet)
            for frame in self.prototypes():
                failure=LanguageError('injected');c=self.own(e,frame)
                with self.assertRaises(LanguageError)as caught:frame.step(c,None,failure)
                self.assertIs(caught.exception,failure)
        from chaos import contexts
        from _resource_reference152 import reference
        for stage in ('unstarted','await','drain'):
            outcomes=[]
            for legacy in (False,True):
                ev=Evaluator()
                with active(ev) as runner,reference() if legacy else nullcontext():
                    runner.task.context_structured=True;t=ev.suspension.task(iter(()));iterator=contexts._await(ev,t,True)
                    if stage!='unstarted':self.assertIs(type(next(iterator)),Await)
                    if stage=='drain':self.assertIs(type(iterator.throw(TaskCancelled('original'))),Drain)
                    try:iterator.close();outcome=('closed',)
                    except BaseException as failure:outcome=(type(failure).__name__,str(failure))
                    outcomes.append((outcome,t.cancel_event.is_set()))
            self.assertEqual(outcomes[0],outcomes[1])
        # Only transport is opaque: one scheduler event, with the actual
        # typed parent's phase/task/result/root fields remaining authoritative.
        ev=Evaluator();foreign=Evaluator().run('fn(){42}')
        with active(ev) as runner:
            task=ev.suspension.task(iter(()));parent=ResourceAwaitFrame(task);cursor=ExecutionCursor(ev,parent)
            count=len(ev.suspension.tasks);request=next(cursor)
            self.assertIs(type(request),Await);self.assertIs(request.task,task)
            self.assertEqual(len(cursor.frames),2);self.assertIs(cursor.frames[-2],parent)
            leaf=cursor.frames[-1];self.assertIs(type(leaf),ProtocolBridge);self.assertTrue(leaf.supplementary)
            self.assertEqual(parent.phase,'await');self.assertIs(parent.task,task);self.assertEqual(task.state,'created')
            from chaos.capture_graph import seal
            parent.result={'pending':[foreign]}
            with self.assertRaises(LanguageError):seal(ev,(cursor,),None)
            parent.result=None
            with self.assertRaises(StopIteration) as caught:cursor.send(42)
            self.assertEqual(caught.exception.value,42);self.assertEqual(parent.result,42)
            self.assertEqual(cursor.frames,[]);self.assertEqual(len(ev.suspension.tasks),count);self.assertEqual(task.state,'created')
            runner.task.context_structured=True;task=ev.suspension.task(iter(()));parent=ResourceAwaitFrame(task,True);cursor=ExecutionCursor(ev,parent)
            self.assertIs(type(next(cursor)),Await);original=TaskCancelled('local',_cancel_event=runner.task.cancel_event)
            request=cursor.throw(original);self.assertIs(type(request),Drain);self.assertIs(request.task,task)
            self.assertEqual(parent.phase,'drain');self.assertIs(parent.original,original)
            self.assertEqual(len(cursor.frames),2);self.assertIs(cursor.frames[-2],parent);self.assertIs(type(cursor.frames[-1]),ProtocolBridge);self.assertTrue(cursor.frames[-1].supplementary)
            with self.assertRaises(TaskCancelled) as caught:cursor.send(None)
            self.assertIs(caught.exception,original);self.assertEqual(cursor.frames,[])
    def test_context_run_forced_close_restores_exact_actual_holder(self):
        from chaos.dynamic_contexts import Context,active_state
        e=Evaluator()
        with active(e):
            state=active_state(e.suspension);previous=state.current;c=Context(e.suspension);f=ContextRunFrame(RunRequest(c,Native('fn',lambda:42,e),[],{}));cursor=self.own(e,f)
            f.step(cursor,None,None);self.assertIs(state.current,c);self.assertTrue(c.active)
            f.close(cursor);self.assertIs(state.current,previous);self.assertFalse(c.active)
    def test_paused_acquire_uses_actual_cached_function_field(self):
        e=Evaluator()
        def resource_probe_acquire_152():
            f=next(f for f in reversed(e._execution_cursor.frames)if type(f)is ResourceAcquireFrame)
            f.enter_function=Native('replacement',lambda:42,e);return None
        e.env.bind('resource_probe_acquire_152',Native('resource_probe_acquire_152',resource_probe_acquire_152,e))
        self.assertEqual(e.run('class D{fn __get__(self,o,c){resource_probe_acquire_152();fn(e){null}}}class C{fn __enter__(self){0}let __exit__=D()}with C() as n{n}'),42)
    def test_paused_unwind_uses_actual_pending_error_field(self):
        e=Evaluator()
        def resource_probe_unwind_152():
            f=next(f for f in reversed(e._execution_cursor.frames)if type(f)is StackUnwindFrame)
            f.pending=LanguageError('changed',code='changed_error');return None
        e.env.bind('resource_probe_unwind_152',Native('resource_probe_unwind_152',resource_probe_unwind_152,e))
        self.assertEqual(e.run('try{with exit_stack() as s{s.callback(fn(){resource_probe_unwind_152();null})}}catch e{e.code}'),'changed_error')
    def test_every_declared_language_root_rejects_foreign_without_hooks(self):
        from chaos.capture_graph import seal
        foreign=Evaluator().run('fn(){42}');e=Evaluator()
        with active(e):
            for prototype in self.prototypes():
                for name in prototype.ROOTS:
                    if name in ('previous_authority','previous_team'):continue
                    f=next(x for x in self.prototypes()if type(x)is type(prototype));self.own(e,f);setattr(f,name,foreign)
                    with self.subTest(frame=type(f).__name__,field=name),self.assertRaises(LanguageError):seal(e,(f,),None)
            for args in ([foreign],(foreign,)):
                with self.assertRaises(LanguageError):seal(e,(ContextProtocolRequest('invoke',(None,args,False)),),None)
            class Bomb:
                def __iter__(self):raise AssertionError('host iterator executed')
                def values(self):raise AssertionError('host values hook executed')
            for request in (RunRequest(None,None,Bomb(),Bomb()),StackRequest(None,'callback',args=Bomb(),kwargs=Bomb()),ContextProtocolRequest('invoke',Bomb())):seal(e,(request,),None)
            f=ResourceInvokeFrame(None,[]);self.own(e,f);f.owner=foreign.owner
            with self.assertRaises(LanguageError):seal(e,(f,),None)
    def test_supplementary_graph_bounds_and_atomic_alias_updates(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        with active(e):
            token=NextDescriptor(e,frozenset({'outside'}),True);native=Native('next',lambda:None,e,capabilities=frozenset({'outside'}),generic=token)
            f=ResourceInvokeFrame(None,[native,foreign]);self.own(e,f,frozenset())
            with self.assertRaises(LanguageError):seal(e,(f,),frozenset())
            self.assertEqual(token.capabilities,frozenset({'outside'}))
            for payload in ([[]for _ in range(20001)],None):
                if payload is None:
                    payload=[]
                    for _ in range(110):payload=[payload]
                f=ResourceInvokeFrame(None,payload);self.own(e,f)
                with self.assertRaises(ResourceLimit):seal(e,(f,),None)
            # Simulate legal allocator ID reuse only after a transient Edge
            # has actually died; retaining identities must prevent that death.
            # This is deterministic on every interpreter, with no timing/load
            # assumption and no impossible collision between live objects.
            import builtins,weakref
            from chaos.capture_frames import Edge
            from chaos.execution_frames import InvocationRequest
            from chaos import execution_frames as frames
            actual_id=builtins.id;original_roots=frames.frame_roots
            for mode in ('charged','supplementary','cross_mode'):
                token=NextDescriptor(e,frozenset({'outside'}),True)
                native=Native('next',lambda:None,e,capabilities=token.capabilities,generic=token)
                first=InvocationFrame(InvocationRequest(None,[]));first.owner=e
                second=InvocationFrame(InvocationRequest(None,[]));second.owner=e
                carrier=ResourceInvokeFrame(None,[]);self.own(e,carrier)
                lifetime={'first':None,'identity':None,'observed':[]}
                def roots(frame):
                    if frame is first:return (Edge(e,None,()),)
                    if frame is second:
                        lifetime['observed'].append(lifetime['first']() is not None)
                        return (Edge(e,None,(foreign,)),)
                    if frame is carrier:return (second,) if mode=='cross_mode' else (second,first,native)
                    return original_roots(frame)
                def reusable_identity(value):
                    if type(value) is Edge:
                        if lifetime['first'] is None:
                            lifetime['first']=weakref.ref(value);lifetime['identity']=actual_id(value)
                        elif lifetime['first']() is None:
                            return lifetime['identity']
                    return actual_id(value)
                roots_to_seal=(second,first,native) if mode=='charged' else (carrier,) if mode=='supplementary' else (carrier,first,native)
                with self.subTest(identity_mode=mode):
                    with patch.object(frames,'frame_roots',roots),patch('chaos.capture_graph.id',reusable_identity,create=True):
                        with self.assertRaises(LanguageError) as caught:seal(e,roots_to_seal,frozenset())
                    self.assertEqual(caught.exception.code,'ownership_error')
                    self.assertEqual(lifetime['observed'],[True])
                    self.assertEqual(token.capabilities,frozenset({'outside'}))
                    self.assertEqual(native.capabilities,frozenset({'outside'}))
    def test_active_callback_and_saved_authority_caller_floor_distinct(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator()
        with active(e,frozenset({'inside','outside'}))as runner:
            def next_value():
                token=NextDescriptor(e,frozenset({'inside','outside'}),True)
                return token,Native('next',lambda:None,e,capabilities=token.capabilities,generic=token)
            inside,a=next_value();saved,b=next_value();fn=Native('limited',lambda:None,e,capabilities=frozenset({'inside'}))
            f=ResourceInvokeFrame(fn,[a]);self.own(e,f,frozenset({'inside','outside'}));f.current=Task(e.suspension,iter(()));f.current.capture_roots=(b,)
            seal(e,(f,),None);self.assertEqual(inside.capabilities,frozenset({'inside'}));self.assertEqual(saved.capabilities,frozenset({'inside','outside'}))
            seal(e,(f,),frozenset());self.assertEqual(saved.capabilities,frozenset())
            result,a=next_value();outside,b=next_value();f=ResourceAcquireFrame(None);self.own(e,f,frozenset({'inside','outside'}));f.phase='invoke';f.enter_function=fn;f.value=a;f.error=b
            seal(e,(f,),None);self.assertEqual(result.capabilities,frozenset({'inside'}));self.assertEqual(outside.capabilities,frozenset({'inside','outside'}))
    def test_every_small_step_budget_matches_exact_old_full_snapshot(self):
        samples=[SYNC+'with C() as n{n}',GEN+'with generator_context(resource()) as n{n}',ASYNC+'async with exit_stack(true) as s{await s.enter_async_context(A());42}','let c=context();c.run(fn(){return_values(40,42)})']
        for source in samples:
            for limit in range(1,snapshot(source)[1]+2):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
    def test_every_small_cancel_threshold_matches_exact_old_full_snapshot(self):
        samples=[SYNC+'with exit_stack() as s{s.enter_context(C());42}',ASYNC+'async with A() as n{n}','let c=context();c.run(async fn(){await sleep(0);42})']
        for source in samples:
            for cancel in range(snapshot(source)[1]+2):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))
    def test_all64_live_capture_charge_alignments_for_context_and_resources(self):
        from chaos.capture_graph import seal
        def setup(e):e.env.bind('sink',Native('sink',lambda value:seal(e,(value,),None)and None,e))
        cases=[
        '''class C{async fn __aenter__(self){await channel_send(ready,0);await channel_receive(gate);42}async fn __aexit__(self,e){null}}async fn work(){async with C() as n{n}}''',
        '''async fn work(){let c=context();c.run(async fn(){await channel_send(ready,0);await channel_receive(gate);42})}''',
        '''class C{async fn __aenter__(self){await channel_send(ready,0);await channel_receive(gate);42}async fn __aexit__(self,e){null}}async fn work(){async with exit_stack(true) as s{await s.enter_async_context(C())}}''']
        for padding in range(64):
            prefix=''.join('let pad'+str(i)+'='+str(i)+';'for i in range(padding))+'let ready=channel(1);let gate=channel();'
            for body in cases:
                source=prefix+body+'let pending=spawn(work());await channel_receive(ready);sink(pending);await channel_send(gate,0);await pending'
                modern=snapshot(source,setup=setup);self.assertEqual(modern[0],('value',42));self.assertEqual(modern,snapshot(source,legacy=True,setup=setup))

if __name__=='__main__':unittest.main()
