"""Dynamic stacks use the same captured exits and scheduler as lexical scopes."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit, serializable
from chaos.session import Session
from chaos.suspension import TaskCancelled
from chaos.debugger import preview
from test_computer_values import ComputerFixture


SYNC = '''var seen=[];class C{var label=null;fn __init__(self,label){self.label=label;null}
fn __enter__(self){seen=append(seen,"enter:"+self.label);self.label}
fn __exit__(self,error){seen=append(seen,[self.label,error]);null}}'''


class ResourceStackTests(unittest.TestCase):
    def run_code(self, source, **options): return Evaluator(**options).run(source)

    def test_dynamic_lifo_and_std_factories(self):
        source=SYNC+'''import "std/context" as ctx;
let result=with ctx.ExitStack() as stack{for name in ["a","b"]{stack.enter_context(C(name))};42};
[result,seen,type_of(ctx.AsyncExitStack())]'''
        self.assertEqual(self.run_code(source),[42,['enter:a','enter:b',['b',None],['a',None]],'AsyncExitStack'])

    def test_callback_arguments_identity_snapshot_and_no_suppression(self):
        source='''var seen=[];var values=[1];fn cleanup(value,*,function,manager){seen=append(seen,[value,function,manager]);true}
let error=try{with exit_stack() as s{let same=s.callback(cleanup,values,function=2,manager=3)==cleanup;values=[99];throw same}}catch e{e.value};[error,seen]'''
        self.assertEqual(self.run_code(source),[True,[[[1],2,3]]])

    def test_push_error_evolution_suppression_and_replacement(self):
        source='''var seen=[];let result=with exit_stack() as s{
s.push(fn(e){seen=append(seen,e);null});s.push(fn(e){seen=append(seen,e.value);true});
s.push(fn(e){seen=append(seen,e.value);throw "replacement"});throw "original"};[result,seen]'''
        self.assertEqual(self.run_code(source),[None,['original','replacement',None]])
        source='''with exit_stack() as s{s.push(fn(e){throw "outer"});s.callback(fn(){throw "inner"});throw "body"}'''
        with self.assertRaises(LanguageError) as caught:self.run_code(source)
        self.assertEqual(caught.exception.value,'outer')

    def test_exit_result_contract_and_callbacks_ignore_ordinary_results(self):
        for result in ['1','"yes"','[]','{}','async fn(){true}()']:
            with self.subTest(result=result),self.assertRaises(LanguageError):
                self.run_code('with exit_stack() as s{s.push(fn(e){'+result+'});42}')
        for result in ['true','false','42','"done"','[1]','{}']:
            self.assertEqual(self.run_code('with exit_stack() as s{s.callback(fn(){'+result+'});42}'),42)
        for flavor,body in [('async fn','null'),('fn*','yield 1'),('async fn*','yield 1')]:
            with self.subTest(flavor=flavor),self.assertRaises(LanguageError):
                self.run_code('with exit_stack() as s{s.callback('+flavor+'(){'+body+'});42}')

    def test_push_manager_skips_enter_and_captures_descriptor(self):
        source='''var seen=[];class C{fn __enter__(self){throw "must not enter"}
var __exit__=fn(e){seen=append(seen,"old");null}}let c=C();
with exit_stack() as s{let same=s.push(c)==c;C.__exit__=fn(e){seen=append(seen,"new");null};same};seen'''
        self.assertEqual(self.run_code(source),['old'])

    def test_async_stack_mixes_sync_async_and_awaits_once(self):
        source=SYNC+'''class A{async fn __aenter__(self){seen=append(seen,"async-enter");7}
async fn __aexit__(self,e){seen=append(seen,"async-exit");null}}
async fn cleanup(value){seen=append(seen,value);42}
let result=async with exit_stack(true) as s{s.callback(fn(){seen=append(seen,"first");null});
s.enter_context(C("sync"));let value=await s.enter_async_context(A());s.push_async_callback(cleanup,"last");value};[result,seen]'''
        self.assertEqual(self.run_code(source),[7,['enter:sync','async-enter','last','async-exit',['sync',None],'first']])
        for expression in ['fn(){null}','async fn(){async fn(){null}()}','async fn(){fn*(){yield 1}()}']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):
                self.run_code('async with exit_stack(true) as s{s.push_async_callback('+expression+');42}')

    def test_async_push_manager_and_suppression(self):
        source='''class A{async fn __aexit__(self,e){e.value==42}}async with exit_stack(true) as s{s.push_async_exit(A());throw 42}'''
        self.assertIsNone(self.run_code(source))

    def test_failed_enter_keeps_previous_entries_and_validates_exit_first(self):
        source=SYNC+'''class Bad{fn __enter__(self){seen=append(seen,"bad");null}}
let error=try{with exit_stack() as s{s.enter_context(C("outer"));s.enter_context(Bad())}}catch e{e.code};[seen[0],length(seen),seen[1][0]]'''
        self.assertEqual(self.run_code(source),['enter:outer',2,'outer'])
        source=SYNC+'''class Bad{fn __enter__(self){throw "bad"}fn __exit__(self,e){seen=append(seen,"wrong");null}}
try{with exit_stack() as s{s.enter_context(C("outer"));s.enter_context(Bad())}}catch e{null};length(seen)'''
        self.assertEqual(self.run_code(source),2)

    def test_captured_exit_after_dynamic_acquire(self):
        source='''var seen=[];class C{fn __enter__(self){self}var __exit__=fn(e){seen=append(seen,"old");null}}
with exit_stack() as s{s.enter_context(C());C.__exit__=fn(e){seen=append(seen,"new");null}};seen'''
        self.assertEqual(self.run_code(source),['old'])

    def test_generator_original_error_identity_and_one_shot(self):
        source='''import "std/context" as ctx;var seen=[];
@ctx.contextmanager
fn* resource(){try{yield 42}finally{seen=append(seen,"closed")}}
let manager=resource();let error=try{with exit_stack() as s{s.enter_context(manager);1/0}}catch e{e.code};
let again=try{with exit_stack() as s{s.enter_context(manager)}}catch e{e.code};[error,again,seen]'''
        self.assertEqual(self.run_code(source),['ZeroDivisionError','context_error',['closed']])
        source='''import "std/context" as ctx;@ctx.contextmanager
fn* resource(){try{yield 1}catch e{null}}
with exit_stack() as s{s.enter_context(resource());throw "suppressed"}'''
        self.assertIsNone(self.run_code(source))

    def test_generator_wrong_yield_count_closes_and_outer_can_suppress(self):
        source='''import "std/context" as ctx;var seen=[];
@ctx.contextmanager
fn* twice(){try{yield 1;yield 2}finally{seen=append(seen,"closed")}}
with exit_stack() as s{s.push(fn(e){e.code=="context_error"});s.enter_context(twice())};seen'''
        self.assertEqual(self.run_code(source),['closed'])

    def test_return_loop_and_generator_close_unwind(self):
        source=SYNC+'''fn work(){with exit_stack() as s{s.enter_context(C("return"));return 42}}
let out=work();for i in [1,2]{with exit_stack() as s{s.enter_context(C("loop"));break}};[out,length(seen)]'''
        self.assertEqual(self.run_code(source),[42,4])
        for flavor,step,close in [('fn*','next(g)','close(g)'),('async fn*','await anext(g)','await aclose(g)')]:
            source=SYNC+flavor+' values(){with exit_stack() as s{s.enter_context(C("stream"));yield 42}}let g=values();'+step+';'+close+';length(seen)'
            with self.subTest(flavor=flavor):self.assertEqual(self.run_code(source),2)

    def test_pop_all_transfer_explicit_close_and_closed_rejection(self):
        source='''var seen=[];let a=exit_stack();a.callback(fn(){seen=append(seen,1);null});let b=a.pop_all();
a.callback(fn(){seen=append(seen,2);null});a.close();b.close();b.close();
let error=try{a.callback(fn(){null})}catch e{e.code};[seen,error]'''
        self.assertEqual(self.run_code(source),[[2,1],'context_error'])
        source='''var seen=[];let a=exit_stack(true);a.push_async_callback(async fn(){seen=append(seen,1);null});
let b=a.pop_all();await a.aclose();await b.aclose();seen'''
        self.assertEqual(self.run_code(source),[1])

    def test_close_inside_lexical_scope_and_one_shot_reentry(self):
        source='''var n=0;let s=exit_stack();with s as same{s.callback(fn(){n=n+1;null});s.close();42};
let e=try{with s as _{null}}catch e{e.code};[n,e]'''
        self.assertEqual(self.run_code(source),[1,'context_error'])

    def test_nested_stack_lifo_and_cycle_rejected(self):
        source='''var seen=[];let a=exit_stack();let b=exit_stack();a.callback(fn(){seen=append(seen,"a");null});
a.enter_context(b);b.callback(fn(){seen=append(seen,"b");null});
let error=try{b.enter_context(a)}catch e{e.code};a.close();[seen,error]'''
        self.assertEqual(self.run_code(source),[['b','a'],'context_error'])
        self.assertEqual(self.run_code('let a=exit_stack();try{a.enter_context(a)}catch e{e.code}'),'context_error')

    def test_explicit_close_callback_reentry_is_rejected_and_outer_exit_runs(self):
        source='''var seen=[];let s=exit_stack();s.push(fn(e){seen=append(seen,e.code);true});s.callback(fn(){s.close()});s.close();seen'''
        self.assertEqual(self.run_code(source),['context_busy'])

    def test_async_operation_busy_guard(self):
        source='''class C{async fn __aenter__(self){await async fn(){42}()}async fn __aexit__(self,e){null}}
let s=exit_stack(true);async fn enter(){try{await s.enter_async_context(C())}catch e{e.code}}
let results=await gather([enter(),enter()]);await s.aclose();results'''
        self.assertEqual(self.run_code(source),[42,'context_busy'])

    def test_limits_checked_before_acquisition_and_descriptor_effects(self):
        calls=[]
        source='''class D{fn __get__(self,obj,owner){tool("descriptor",null);fn(){null}}}
class C{let __enter__=D();fn __exit__(self,e){null}}
with exit_stack(max_entries=0) as s{s.enter_context(C())}'''
        with self.assertRaises(ResourceLimit):self.run_code(source,tool_handler=lambda n,v:calls.append(n))
        self.assertEqual(calls,[])
        for params in ['true, true','false, -1','false, 10001','1','false, 1.5']:
            with self.subTest(params=params),self.assertRaises(LanguageError):self.run_code('exit_stack('+params+')')

    def test_task_group_and_native_push_registration_rejected(self):
        for code in ['s.enter_context(task_group())','await s.enter_async_context(task_group())','s.push(task_group())','s.push(exit_stack())']:
            with self.subTest(code=code),self.assertRaises(LanguageError):self.run_code('async with exit_stack(true) as s{'+code+'}')

    def test_mismatched_lexical_kinds_rejected(self):
        for source in ['with exit_stack(true) as s{null}','async with exit_stack() as s{null}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_owner_epoch_and_inert_metadata(self):
        ev=Evaluator();stack=ev.run('exit_stack()')
        self.assertEqual(serializable(stack)['$handle'],'ExitStack')
        self.assertEqual(serializable(stack)['state'],'closed')
        self.assertNotIn('entries',preview(stack))
        with self.assertRaises(LanguageError):Evaluator().run('input.close()',{'input':stack})
        session=Session();session.evaluate('let stack=exit_stack()')
        with self.assertRaises(LanguageError):session.evaluate('stack.close()')
        calls=[]
        self.run_code('let s=exit_stack();s.callback(fn(){null});tool("snapshot",{stack:s});s.close()',tool_handler=lambda n,v:calls.append(v))
        self.assertEqual(calls[0]['stack']['entryCount'],1)
        self.assertNotIn('entries',calls[0]['stack'])

    def test_epoch_finalizes_detached_stacks_after_lexical_generator_owner(self):
        calls=[]
        source='''let s=exit_stack();s.callback(fn(){tool("resource",null);null});
fn* values(){with s as _{try{yield 42}finally{tool("body",null)}}}
let g=values();next(g);null'''
        self.run_code(source,tool_handler=lambda n,v:calls.append(n))
        self.assertEqual(calls,['body','resource'])
        calls.clear()
        self.run_code('let s=exit_stack();s.callback(fn(){tool("detached",null);null});s.pop_all();null',tool_handler=lambda n,v:calls.append(n))
        self.assertEqual(calls,['detached'])

    def test_epoch_cleanup_created_resources_are_also_finalized(self):
        calls=[]
        self.run_code('let s=exit_stack();s.callback(fn(){let later=exit_stack();later.callback(fn(){tool("later",null);null});tool("first",null);null});null',tool_handler=lambda n,v:calls.append(n))
        self.assertEqual(calls,['first','later'])

    def test_hard_controls_skip_every_remaining_cleanup_and_release_flags(self):
        for error in [ResourceLimit('limit'),ExecutionControl('stop'),TaskCancelled('cancel'),KeyboardInterrupt('stop'),SystemExit('stop')]:
            calls=[]
            def effect(name,value):
                calls.append(name)
                if name=='stop':raise error
            for scope in [True,False]:
                calls.clear();ev=Evaluator(tool_handler=effect)
                source='let s=exit_stack();s.callback(fn(){tool("cleanup",null);null});tool("stop",null)'
                if scope:source='with exit_stack() as s{s.callback(fn(){tool("cleanup",null);null});tool("stop",null)}'
                with self.subTest(error=type(error).__name__,scope=scope),self.assertRaises(type(error)) as caught:ev.run(source)
                self.assertIs(caught.exception,error);self.assertEqual(calls,['stop'])
                self.assertEqual(ev.suspension.stack_entries,0)
                self.assertTrue(all(s.state=='closed' and not s.busy for s in ev.suspension.resource_stacks))

    def test_hard_cleanup_error_stops_older_exits(self):
        calls=[];error=TaskCancelled('cleanup cancelled')
        def effect(name,value):
            calls.append(name)
            if name=='stop':raise error
        with self.assertRaises(TaskCancelled) as caught:
            self.run_code('with exit_stack() as s{s.callback(fn(){tool("old",null);null});s.push(fn(e){tool("stop",null);true});throw 42}',tool_handler=effect)
        self.assertIs(caught.exception,error);self.assertEqual(calls,['stop'])


    def test_group_abort_during_inner_cleanup_still_unwinds_outer_entries(self):
        entered=threading.Event();ended=threading.Event();calls=[]
        def effect(kind,payload,cancel):
            if payload['name']=='inner':
                calls.append('inner');entered.set()
                try:self.assertTrue(cancel.wait(3))
                finally:ended.set()
                return None
            if payload['name']=='bad':
                self.assertTrue(entered.wait(3));raise LanguageError('child',code='review_group')
            if payload['name']=='outer':
                self.assertTrue(ended.is_set());calls.append('outer');return None
            self.fail(payload['name'])
        source='''async fn bad(){tool("bad",null)}
          try{async with task_group() as group{group.spawn(bad());
            async with exit_stack(true) as stack{
              stack.callback(fn(){tool("outer",null)});
              stack.push_async_callback(async fn(){tool("inner",null)});42}}}catch e{e.code}'''
        evaluator=Evaluator(effect_handler=effect,tool_handler=lambda n,v:effect('tool',{'name':n,'value':v},threading.Event()))
        self.assertEqual(evaluator.run(source),'review_group')
        self.assertEqual(calls,['inner','outer'])

    def test_append_deep_stack_chain_is_bounded_before_host_recursion(self):
        evaluator=Evaluator(step_limit=100000)
        with self.assertRaises(ResourceLimit):
            evaluator.run('''let owner=exit_stack();var tail=owner;
              for i in range(550){let child=exit_stack();tail.enter_context(child);tail=child};42''')
        self.assertEqual(evaluator.suspension.stack_entries,0)
        self.assertTrue(all(stack.state=='closed' for stack in evaluator.suspension.resource_stacks))


    def test_epoch_cleanup_hard_failure_outranks_original_ordinary_error(self):
        for error in [ResourceLimit('hard'),TaskCancelled('cancel'),KeyboardInterrupt('interrupt'),SystemExit('exit')]:
            def effect(name,value):raise error
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)) as caught:
                self.run_code('let s=exit_stack();s.callback(fn(){tool("stop",null)});throw "ordinary"',tool_handler=effect)
            self.assertIs(caught.exception,error)

    def test_lexical_exit_drains_spawned_stack_acquisition_before_outer_cleanup(self):
        entered=threading.Event();ended=threading.Event();calls=[]
        def effect(kind,payload,cancel):
            name=payload['name']
            if name=='enter':
                entered.set()
                try:self.assertTrue(cancel.wait(3))
                finally:ended.set()
                return 42
            if name=='ready':self.assertTrue(entered.wait(3));return None
            if name=='outer':self.assertTrue(ended.is_set());calls.append('outer');return None
            self.fail(name)
        source='''class C{async fn __aenter__(self){tool("enter",null)}async fn __aexit__(self,e){tool("unexpected",null);null}}
with exit_stack() as outer{outer.callback(fn(){tool("outer",null)});
async with exit_stack(true) as s{spawn(s.enter_async_context(C()));await async fn(){tool("ready",null)}()}};42'''
        evaluator=Evaluator(effect_handler=effect,tool_handler=lambda n,v:effect('tool',{'name':n,'value':v},threading.Event()))
        self.assertEqual(evaluator.run(source),42)
        self.assertEqual(calls,['outer']);self.assertEqual(evaluator.suspension.stack_entries,0)



    def test_lexical_exit_drains_spawned_sync_context_acquisition(self):
        entered=threading.Event();ended=threading.Event()
        def effect(kind,payload,cancel):
            name=payload['name']
            if name=='enter':
                entered.set()
                try:self.assertTrue(cancel.wait(3))
                finally:ended.set()
                return 42
            if name=='ready':self.assertTrue(entered.wait(3));return None
            if name=='done':return ended.is_set()
            self.fail(name)
        source='''class Gate{fn __enter__(self){tool("enter",null)}fn __exit__(self,e){null}}
async fn body(){let result=with exit_stack() as s{async fn acquire(){s.enter_context(Gate())}
spawn(acquire());tool("ready",null);42};[result,tool("done",null)]}await body()'''
        evaluator=Evaluator(effect_handler=effect,tool_handler=lambda n,v:None)
        self.assertEqual(evaluator.run(source),[42,True])
        self.assertEqual(evaluator.suspension.stack_entries,0)

    def test_group_interrupt_at_cleanup_checkpoint_preserves_all_entries(self):
        from chaos.task_groups import GroupAbort
        signal=GroupAbort(object(),LanguageError('group failed'))
        calls=[];evaluator=Evaluator(tool_handler=lambda name,value:calls.append(name))
        original_tick=evaluator.tick;injected=False
        def tick():
            nonlocal injected
            original_tick()
            if not injected and any(stack.state=='closing' for stack in evaluator.suspension.resource_stacks):
                injected=True
                raise signal
        evaluator.tick=tick
        with self.assertRaises(GroupAbort) as caught:
            evaluator.run('with exit_stack() as s{s.callback(fn(){tool("older",null)});s.callback(fn(){tool("newer",null)});42}')
        self.assertIs(caught.exception,signal)
        self.assertEqual(calls,['newer','older'])

    def test_lexical_exit_joins_spawned_sync_close_without_cancelling_cleanup(self):
        entered=threading.Event();ended=threading.Event();calls=[]
        def effect(kind,payload,cancel):
            name=payload['name']
            if name=='cleanup':
                entered.set()
                try:self.assertFalse(cancel.wait(0.1))
                finally:ended.set()
                return None
            if name=='ready':self.assertTrue(entered.wait(3));return None
            if name=='done':return ended.is_set()
            if name=='older':self.assertTrue(ended.is_set());calls.append(name);return None
            self.fail(name)
        source='''async fn body(){let result=with exit_stack() as s{
s.callback(fn(){tool("older",null)});s.callback(fn(){tool("cleanup",null)});
async fn closing(){s.close()}spawn(closing());tool("ready",null);42};[result,tool("done",null)]}await body()'''
        evaluator=Evaluator(effect_handler=effect,tool_handler=lambda n,v:None)
        self.assertEqual(evaluator.run(source),[42,True]);self.assertEqual(calls,['older'])

    def test_group_abort_during_spawned_sync_close_preserves_outer_exits(self):
        entered=threading.Event();ended=threading.Event();calls=[]
        def effect(kind,payload,cancel):
            name=payload['name']
            if name=='cleanup':
                entered.set()
                try:self.assertTrue(cancel.wait(3))
                finally:ended.set()
                return None
            if name=='ready':self.assertTrue(entered.wait(3));return None
            if name=='bad':self.assertTrue(entered.wait(3));raise LanguageError('group failure',code='stack_group')
            if name=='older':self.assertTrue(ended.is_set());calls.append(name);return None
            self.fail(name)
        source='''async fn bad(){tool("bad",null)}try{async with task_group() as group{
group.spawn(bad());with exit_stack() as s{s.callback(fn(){tool("older",null)});s.callback(fn(){tool("cleanup",null)});
async fn closing(){s.close()}spawn(closing());tool("ready",null);42}}}catch e{e.code}'''
        evaluator=Evaluator(effect_handler=effect,tool_handler=lambda n,v:None)
        self.assertEqual(evaluator.run(source),'stack_group');self.assertEqual(calls,['older'])

class ComputerStackTests(unittest.TestCase):
    def setUp(self):self.host=ComputerFixture()

    def test_dynamic_computer_lifetime_and_native_alias_guard(self):
        source='''let c=await computer_open();let result=async with exit_stack(true) as s{
let same=await s.enter_async_context(c);let nested=try{async with c as _{null}}catch e{e.code};
[same==c,nested,computer_info(c).status]};[result,computer_info(c).status]'''
        self.assertEqual(self.host.evaluator().run(source),[[True,'computer_busy','open'],'closed'])
        self.assertEqual([name for name,_ in self.host.calls],['open','close'])

    def test_computer_epoch_cleanup_and_pop_all(self):
        self.host.evaluator().run('let s=exit_stack(true);await s.enter_async_context(await computer_open());s.pop_all();null')
        self.assertEqual([name for name,_ in self.host.calls],['open','close'])

    def test_computer_hard_control_releases_native_flags_without_effect(self):
        ev=self.host.evaluator(tool_handler=lambda *a:(_ for _ in ()).throw(TaskCancelled('stop')))
        with self.assertRaises(TaskCancelled):ev.run('let s=exit_stack(true);await s.enter_async_context(await computer_open());tool("stop",null)')
        self.assertEqual([name for name,_ in self.host.calls],['open'])
        self.assertEqual(ev.suspension.stack_entries,0)
