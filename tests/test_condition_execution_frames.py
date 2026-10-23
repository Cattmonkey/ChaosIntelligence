"""Complete native recovery frames against the exact original algorithms."""
import copy
import math
import threading
import unittest
from contextlib import contextmanager,nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native,TailCall
from chaos.suspension import Runner,GeneratorClose,CHECKPOINT
from chaos.conditions import (Request,HandlerScope,RestartScope,RestartTransfer,DynamicState,
                              create,enter,release,active_state,callback,resolve)
from chaos.condition_values import make
from chaos.execution_frames import ExecutionCursor,InvocationFrame,Push,Done
from chaos.execution_conditions import (RECOVERY_FRAMES,RecoveryCallbackRequest,RecoveryCallbackFrame,
    ConditionRequestFrame,ConditionSignalFrame,ConditionDebuggerFrame,RestartComputeFrame,
    RestartInvokeFrame,RestartCaseFrame)


CASE='restart_case(fn(){invoke_restart("use",[41])},[{name:"use",function:fn(x){x+1}}])'
SIGNAL='var seen=[];with handler_bind([{predicate:fn(c){true},handler:fn(c){seen=append(seen,c.kind)}}]) as h {signal_condition(condition("x"))};seen'
DEBUG='with restart_bind([{name:"use",function:fn(x){x}}]) as r {invoke_debugger(condition("x"))}'


def snapshot(source,legacy=False,limit=100000,cancel=None,debugger=False,pause=False,setup=None):
    from _condition_reference146 import reference
    effects,trace,debug,pauses=[],[],[],[]
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda name,value:effects.append((name,value)) or value,
                trace_handler=trace.append,condition_pause=pause)
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    if debugger:
        def hook(metadata,node,env,frames):
            pauses.append(copy.deepcopy(metadata));offered=metadata['restarts']
            return 0,None if not offered else {'restartId':offered[0]['id'],'args':[42],'kwargs':{}}
        e.condition_debug_hook=hook
    if setup is not None:setup(e)
    with reference() if legacy else nullcontext():
        try:outcome=('value',e.run(source))
        except LanguageError as error:outcome=('error',error.record())
        except ExecutionControl as error:outcome=('control',type(error).__name__,str(error))
    return outcome,e.steps,effects,trace,debug,pauses


@contextmanager
def active(e,caps=None):
    e.suspension.begin()
    runner=Runner(iter(()),task=SimpleNamespace(recording_scope=None,id='fixture',cancel_event=threading.Event(),interrupt=None))
    runner.capabilities=caps;runner.control_state=e.suspension.control_state;runner.context_state=e.suspension.context_state
    runner.condition_state=e.suspension.condition_state;runner.decimal_state=e.suspension.decimal_state
    e.suspension.current=runner
    try:yield runner
    finally:e.suspension.current=None;e.suspension.finish()


class ConditionExecutionFrames(unittest.TestCase):
    def check(self,source,expected,**options):
        modern=snapshot(source,**options);self.assertEqual(modern[0],('value',expected))
        self.assertEqual(modern,snapshot(source,legacy=True,**options));return modern
    def frame(self,e,cls):return next(f for f in reversed(e._execution_cursor.frames) if type(f) is cls)
    def own(self,e,f):f.owner=e;return SimpleNamespace(owner=e)
    def test_compatibility_functions_are_thin_requests(self):
        e=Evaluator();e._execution_cursor=object();r=Request('compute')
        self.assertIs(next(resolve(e,r)),r)
        function=Native('unused',lambda:42,e);request=next(callback(e,function,[42],authority=frozenset()))
        self.assertIs(type(request),RecoveryCallbackRequest);self.assertIs(request.function,function)
        self.assertEqual(request.args,[42]);self.assertEqual(request.authority,frozenset())
    def test_callback_real_invocation_original_flags_and_no_added_tick(self):
        e=Evaluator();f=Native('value',lambda x:x,e)
        with active(e,frozenset({'one','two'})) as runner:
            frame=RecoveryCallbackFrame(f,[42],authority=frozenset({'one'}),all_results=True)
            action=frame.step(self.own(e,frame),None,None)
            self.assertIs(type(action),Push);self.assertIs(type(action.frame),InvocationFrame)
            self.assertIs(action.frame.fn,f);self.assertTrue(action.frame.force);self.assertTrue(action.frame._recovery_callback)
            self.assertTrue(action.frame.all_results);self.assertEqual(runner.capabilities,frozenset({'one'}));self.assertEqual(e.steps,0)
            frame.close(SimpleNamespace(owner=e));self.assertEqual(runner.capabilities,frozenset({'one','two'}))
    def test_callback_restores_exact_saved_runner_on_done_error_and_close(self):
        for operation in ('done','error','close'):
            e=Evaluator();original=frozenset({'outside'})
            with active(e,original) as runner:
                f=RecoveryCallbackFrame(None,[],authority=frozenset());c=self.own(e,f);f.step(c,None,None)
                other=Runner(iter(()));other.capabilities=frozenset({'other'});e.suspension.current=other
                try:
                    if operation=='done':self.assertEqual(f.step(c,42,None),Done(42))
                    elif operation=='error':
                        failure=LanguageError('same')
                        with self.assertRaises(LanguageError) as caught:f.step(c,None,failure)
                        self.assertIs(caught.exception,failure)
                    else:f.close(c)
                    self.assertIs(runner.capabilities,original);self.assertEqual(other.capabilities,frozenset({'other'}))
                finally:e.suspension.current=runner
    def test_signal_group_order_decline_and_full_snapshot(self):self.check(SIGNAL,['x'])
    def test_signal_masking_allows_new_scopes_and_restores(self):
        source='var seen=[];with handler_bind([{predicate:fn(c){true},handler:fn(c){seen=append(seen,c.kind)}}]) as outer {with handler_bind([{predicate:fn(c){true},handler:fn(c){with handler_bind([{predicate:fn(c){true},handler:fn(c){seen=append(seen,"new")}}]) as n {signal_condition(condition("nested"))};seen=append(seen,"inner")}}]) as h {signal_condition(condition("root"))}};seen'
        self.check(source,['new','nested','inner','root'])
    def test_signal_async_callbacks_share_context_and_cold_result_rejected(self):
        self.check('let v=context_var("v",0);with handler_bind([{predicate:async fn(c){await sleep(0);v.set(20);true},handler:async fn(c){await sleep(0);v.set(v.get()+22);null}}]) as h {signal_condition(condition("x"))};v.get()',42)
        source='async fn cold(){42};with handler_bind([{predicate:fn(c){true},handler:fn(c){cold()}}]) as h {signal_condition(condition("x"))}'
        actual=snapshot(source);self.assertEqual(actual,snapshot(source,legacy=True));self.assertEqual(actual[0][1]['code'],'condition_protocol_error')
    def test_strict_predicate_and_visibility_bool_errors(self):
        for source in ['with handler_bind([{predicate:fn(c){1},handler:fn(c){null}}]) as h {signal_condition(condition("x"))}',
                       'with restart_bind([{name:"x",function:fn(){42},test:fn(c){1}}]) as r {compute_restarts()}']:
            modern=snapshot(source);self.assertEqual(modern,snapshot(source,legacy=True));self.assertEqual(modern[0][0],'error')
    def test_required_error_handlers_then_original_error(self):
        self.check('var n=0;with handler_bind([{predicate:fn(c){true},handler:fn(c){n=n+1}}]) as h {try{error_condition(condition("x"))}catch e{[n,e.code]}}',[1,'condition_error'])
    def test_compute_find_order_duplicates_unnamed_and_tests(self):
        self.check('var n=0;with restart_bind([{name:"same",function:fn(){1},test:fn(c){n=n+1;true}},{name:"same",function:fn(){2}},{name:null,function:fn(){3}}]) as r {let a=compute_restarts();[invoke_restart(a[0]),invoke_restart(a[1]),invoke_restart(a[2]),invoke_restart(find_restart("same")),n]}',[1,2,3,1,2])
    def test_inline_restart_args_keywords_and_full_multiple_values(self):
        self.check('with restart_bind([{name:"x",function:fn(a,*,b=2){return_values(a+b,99)}}]) as r {multiple_value_list(fn(){invoke_restart("x",[40],{b:2})})}',[42,99])
    def test_case_release_before_handler_and_cleanup_order(self):
        self.check('var seen=[];let x=restart_case(fn(){try{invoke_restart("use",[41])}finally{seen=append(seen,"cleanup")}},[{name:"use",function:fn(x){seen=append(seen,"restart");[x+1,find_restart("use")]}}]);[x,seen]',[[42,None],['cleanup','restart']])
    def test_case_outer_identity_skips_inner_and_full_results(self):
        self.check('multiple_value_list(fn(){restart_case(fn(){let outer=find_restart("x");restart_case(fn(){invoke_restart(outer,[42])},[{name:"x",function:fn(x){0}}])},[{name:"x",function:fn(x){return_values(x,99)}}])})',[42,99])
    def test_case_cleanup_error_overrides_transfer_and_async_force(self):
        self.check('try{restart_case(async fn(){await sleep(0);try{invoke_restart("x")}finally{throw "cleanup"}},[{name:"x",function:fn(){42}}])}catch e{e.value}','cleanup')
        self.check('restart_case(async fn(){await sleep(0);invoke_restart("x",[41])},[{name:"x",function:async fn(x){await sleep(0);x+1}}])',42)
    def test_cold_task_and_generator_restart_results_preserve_contract(self):
        self.check('async fn cold(){42};with restart_bind([{name:"x",function:fn(){cold()}}]) as r {let t=invoke_restart("x");[type_of(t),await t]}',['Task',42])
        self.check('fn* stream(){yield 42};with restart_bind([{name:"x",function:fn(){stream()}}]) as r {let g=invoke_restart("x");let x=next(g).value;close(g);x}',42)
        direct='with restart_bind([{name:"x",function:fn*(){yield 42}}]) as r {invoke_restart("x")}'
        modern=snapshot(direct);self.assertEqual(modern,snapshot(direct,legacy=True))
        self.assertEqual(modern[0][0],'error');self.assertEqual(modern[0][1]['code'],'condition_protocol_error')
    def test_restart_owner_epoch_and_logical_task_isolation(self):
        self.check('async fn child(){find_restart("x")};with restart_bind([{name:"x",function:fn(){42}}]) as r {await child()}',None)
        self.check('let h=with restart_bind([{name:"x",function:fn(){42}}]) as r {find_restart("x")};try{invoke_restart(h)}catch e{e.code}','restart_error')
    def test_debugger_inline_and_required_pause_full_snapshots(self):
        self.check(DEBUG,42,debugger=True)
        self.check(DEBUG.replace('invoke_debugger','error_condition'),42,debugger=True,pause=True)
    def test_debugger_case_unwind_and_nested_independent_pause(self):
        self.check('restart_case(fn(){invoke_debugger(condition("first"))},[{name:"outer",function:fn(x){with restart_bind([{name:"inner",function:fn(y){y}}]) as r {invoke_debugger(condition("second"))}}}])',42,debugger=True)
    def test_debugger_reentry_during_discovery_is_error_and_depth_restores(self):
        source='with restart_bind([{name:"x",function:fn(x){x},test:fn(c){invoke_debugger(c);true}}]) as r {invoke_debugger(condition("x"))}'
        modern=snapshot(source,debugger=True);self.assertEqual(modern,snapshot(source,legacy=True,debugger=True));self.assertEqual(modern[0][1]['code'],'condition_protocol_error')
    def test_debugger_invalid_elapsed_selection_and_unoffered_id(self):
        for result in [(float('nan'),None),(-1,None),(0,{}),(0,{'restartId':'missing','args':[],'kwargs':{}})]:
            def setup(e,result=result):e.condition_debug_hook=lambda *args:result
            modern=snapshot(DEBUG,setup=setup);self.assertEqual(modern,snapshot(DEBUG,legacy=True,setup=setup));self.assertEqual(modern[0][0],'error')
    def test_debugger_valid_pause_extends_deadline_only_once_and_restores_depth(self):
        e=Evaluator(condition_debug_hook=lambda *args:(.25,None));f=ConditionDebuggerFrame(make('x'))
        with active(e):
            old=e.deadline;c=self.own(e,f);f.step(c,None,None)
            with self.assertRaises(LanguageError):f.step(c,[],None)
            self.assertEqual(e.deadline,old+.25);self.assertEqual(e.condition_debug_depth,0)
    def test_all_small_budget_and_cancel_boundaries_match_old_algorithms(self):
        for source,options in [(SIGNAL,{}),(CASE,{}),(DEBUG,{'debugger':True})]:
            total=snapshot(source,**options)[1]
            for boundary in range(total+2):
                with self.subTest(source=source,boundary=boundary):
                    self.assertEqual(snapshot(source,limit=boundary,**options),snapshot(source,legacy=True,limit=boundary,**options))
                    self.assertEqual(snapshot(source,cancel=boundary,**options),snapshot(source,legacy=True,cancel=boundary,**options))
    def test_paused_handler_field_is_actual_next_execution(self):
        def setup(e):
            def mutate():self.frame(e,ConditionSignalFrame).handler=Native('replacement',lambda c:42,e);return True
            e.env.bind('mutate',Native('mutate',mutate,e))
        self.assertEqual(snapshot('with handler_bind([{predicate:fn(c){mutate()},handler:fn(c){throw "old"}}]) as h {signal_condition(condition("x"))};42',setup=setup)[0],('value',42))
    def test_paused_compute_result_and_restart_args_are_authoritative(self):
        def setup(e):
            def mutate():
                f=self.frame(e,RestartInvokeFrame);f.args=[42];return True
            e.env.bind('mutate',Native('mutate',mutate,e))
        source='with restart_bind([{name:"x",function:fn(x){x},test:fn(c){mutate()}}]) as r {invoke_restart("x",[0])}'
        self.assertEqual(snapshot(source,setup=setup)[0],('value',42))
    def test_signal_forced_close_restores_hidden_and_case_closes_scope(self):
        e=Evaluator()
        with active(e):
            h=create(e,[{'predicate':Native('p',lambda c:True,e),'handler':Native('h',lambda c:None,e)}]);enter(e,h)
            f=ConditionSignalFrame(make('x'));c=self.own(e,f);f.step(c,None,None);self.assertIn(h,active_state(e.suspension).hidden)
            f.close(c);self.assertEqual(active_state(e.suspension).hidden,set())
            case=RestartCaseFrame(Native('body',lambda:None,e),[]);c=self.own(e,case);case.step(c,None,None);self.assertEqual(case.scope.state,'active')
            case.close(c);self.assertEqual(case.scope.state,'closed');release(h)
    def test_debugger_forced_close_releases_depth_without_hook_or_restart(self):
        e=Evaluator(condition_debug_hook=lambda *args:self.fail('forced close invoked hook'))
        with active(e):
            f=ConditionDebuggerFrame(make('x'));c=self.own(e,f);f.step(c,None,None);self.assertEqual(e.condition_debug_depth,1)
            f.close(c);self.assertEqual(e.condition_debug_depth,0);f.close(c);self.assertEqual(e.condition_debug_depth,0)
    def test_every_typed_frame_pending_semantic_root_rejects_foreign(self):
        from chaos.capture_graph import seal
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        cases=[(ConditionRequestFrame(Request('compute')),'result'),(RecoveryCallbackFrame(None,[]),'result'),
               (ConditionSignalFrame(make('x')),'handler'),(ConditionDebuggerFrame(make('x')),'selected'),
               (RestartComputeFrame(),'result'),(RestartInvokeFrame('x',[],{}),'args'),(RestartCaseFrame(None,[]),'transfer')]
        self.assertEqual({type(f) for f,k in cases},set(RECOVERY_FRAMES))
        for f,k in cases:
            with self.subTest(frame=type(f).__name__):
                setattr(f,k,[foreign] if k in ('result','args') else foreign)
                with self.assertRaises(LanguageError) as caught:seal(e,(ExecutionCursor(e,f),),None)
                self.assertEqual(caught.exception.code,'ownership_error')
    def test_request_args_graph_limits_atomic_caps_and_no_hooks(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        for args in [(foreign,),[foreign]]:
            for target in [RecoveryCallbackRequest(None,args),RecoveryCallbackFrame(None,args)]:
                if type(target) is RecoveryCallbackFrame:target.owner=e
                with self.assertRaises(LanguageError):seal(e,(target,),None)
        f=RecoveryCallbackFrame(None,([None]*10000,[None]*10000));f.owner=e
        with self.assertRaises(ResourceLimit):seal(e,(f,),None)
        deep=None
        for i in range(102):deep=[deep]
        f.args=(deep,)
        with self.assertRaises(ResourceLimit):seal(e,(f,),None)
        token=NextDescriptor(e,None);n=Native('next',lambda:None,e,generic=token,captures=())
        f.args=(foreign,n)
        with self.assertRaises(LanguageError):seal(e,(f,),frozenset())
        self.assertIsNone(token.capabilities);self.assertIsNone(n.capabilities)
        class Trap:
            def __iter__(self):raise AssertionError('host hook')
        f.args=Trap();seal(e,(f,),None)
    def test_active_and_saved_authority_roots_use_distinct_edges(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator()
        with active(e,frozenset({'outside'})) as runner:
            inside=NextDescriptor(e,None);outside=NextDescriptor(e,None)
            f=RecoveryCallbackFrame(None,[],authority=frozenset());f.result=Native('inside',lambda:None,e,generic=inside,captures=())
            f.previous_authority=runner.capabilities;f.authority_runner=runner
            runner.value=Native('outside',lambda:None,e,generic=outside,captures=())
            seal(e,(ExecutionCursor(e,f),),frozenset({'outside'}));self.assertEqual(inside.capabilities,frozenset());self.assertEqual(outside.capabilities,frozenset({'outside'}))
            seal(e,(ExecutionCursor(e,f),),frozenset());self.assertEqual(outside.capabilities,frozenset())
        with active(e,frozenset({'outside'})):
            noop=Native('noop',lambda *args:None,e)
            group=create(e,[{'predicate':noop,'handler':noop}]);group.capabilities=frozenset()
            scope=create(e,[{'name':'x','function':noop}],True);scope.capabilities=frozenset();scope.restarts[0].capabilities=frozenset()
            for kind in ('signal','invoke','case','debugger'):
                inside=NextDescriptor(e,None);outside=NextDescriptor(e,None)
                inner=Native('inner',lambda:None,e,generic=inside,captures=())
                outer=Native('outer',lambda:None,e,generic=outside,captures=())
                if kind=='signal':
                    pending=ConditionSignalFrame(make('x'));pending.group=group;pending.handler=inner
                elif kind=='invoke':
                    pending=RestartInvokeFrame('x',[inner],{});pending.restart=scope.restarts[0]
                elif kind=='case':
                    pending=RestartCaseFrame(noop,[]);pending.scope=scope
                    pending.transfer=RestartTransfer(scope.restarts[0],[inner],{},None,{})
                else:
                    pending=ConditionDebuggerFrame(make('x'));pending.selected=scope.restarts[0];pending.args=[inner]
                if 'saved_holder' in pending.ROOTS:pending.saved_holder=outer
                pending.previous_authority=frozenset({'outside'})
                cursor=ExecutionCursor(e,pending);seal(e,(cursor,),frozenset({'outside'}))
                self.assertEqual(inside.capabilities,frozenset(),kind)
                if 'saved_holder' in pending.ROOTS:self.assertEqual(outside.capabilities,frozenset({'outside'}),kind)
                seal(e,(cursor,),frozenset())
                if 'saved_holder' in pending.ROOTS:self.assertEqual(outside.capabilities,frozenset(),kind)
    def test_all64_live_recovery_callback_capture_graph_residues_match_old(self):
        def setup(e):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        for padding in range(64):
            source='''let padding=range(PADDING);let ready=channel(1);let gate=channel();var saved=null;
            let sink=limited(fn(){fn(t){saved=t;null}});
            async fn work(){with restart_bind([{name:"x",function:fn(x){x},test:async fn(c){await channel_send(ready,0);await channel_receive(gate);true}}]) as r {length(compute_restarts(condition("x")))}};
            let pending=work();spawn(pending);await channel_receive(ready);sink(pending);await channel_send(gate,0);await saved'''.replace('PADDING',str(padding))
            with self.subTest(padding=padding):self.check(source,1,setup=setup)
    def test_lexical_handler_restart_stream_and_nested_control_composition(self):
        self.check('let p=prompt_tag("p");fn* g(){with restart_bind([{name:"x",function:fn(){42}}]) as r {yield invoke_restart("x")}};let stream=g();let x=call_prompt(p,fn(){dynamic_wind(fn(){null},fn(){next(stream).value},fn(){null})});close(stream);x',42)


if __name__=='__main__':unittest.main()
