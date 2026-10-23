"""Context protocols preserve lexical values, language control and ownership."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.session import Session
from chaos.suspension import TaskCancelled
from test_computer_values import ComputerFixture

SYNC = '''var seen=[];class C{var label="scope";fn __init__(self,label="scope"){self.label=label;null}
fn __enter__(self){seen=append(seen,"enter:"+self.label);self.label}
fn __exit__(self,error){seen=append(seen,[self.label,error]);null}}'''
ASYNC = '''var seen=[];class C{async fn __aenter__(self){seen=append(seen,"enter");42}
async fn __aexit__(self,error){seen=append(seen,error);null}}'''


class ContextProtocolTests(unittest.TestCase):
    def run_code(self, source, **kwargs):
        return Evaluator(**kwargs).run(source)

    def test_body_value_scope_and_manager_once(self):
        source=SYNC+'''var count=0;fn acquire(){count=count+1;C()}
let resource="outer";let result=with acquire() as resource{resource};[result,resource,count,seen]'''
        self.assertEqual(self.run_code(source),['scope','outer',1,['enter:scope',['scope',None]]])

    def test_lifo_and_exit_binding_survives_class_replacement(self):
        source='''var seen=[];class C{fn __enter__(self){self}var __exit__=fn(error){seen=append(seen,"old");null}}
let c=C();with c as a,C() as b{C.__exit__=fn(error){seen=append(seen,"new");null}};seen'''
        self.assertEqual(self.run_code(source),['old','old'])
        self.assertEqual(self.run_code(SYNC+'with C("a") as a,C("b") as b{null};seen'),['enter:a','enter:b',['b',None],['a',None]])

    def test_resolve_validate_both_hooks_before_enter_and_failed_enter_no_exit(self):
        for declaration in ['class Bad{fn __enter__(self){seen=append(seen,"bad");null}}',
                            'class Bad{fn __enter__(self){seen=append(seen,"bad");null}let __exit__=0}',
                            'class Bad{fn __enter__(self){throw "enter"}fn __exit__(self,e){seen=append(seen,"bad");null}}']:
            source=SYNC+declaration+'try{with C("outer") as a,Bad() as b{null}}catch e{null};[seen[0],length(seen),seen[1][0]]'
            with self.subTest(declaration=declaration):self.assertEqual(self.run_code(source),['enter:outer',2,'outer'])

    def test_suppression_is_explicit_and_error_record_is_isolated(self):
        for answer in ['true','false','null']:
            source='''var record=null;class C{fn __enter__(self){null}fn __exit__(self,e){record=e;'''+answer+'''}}
let out=try{with C() as _{throw {reason:"body"}}}catch e{e.value};[out,record.code,record.value]'''
            self.assertEqual(self.run_code(source),[None if answer=='true' else {'reason':'body'},'user_throw',{'reason':'body'}])
        source='''class C{fn __enter__(self){null}fn __exit__(self,e){let changed=set(e.value,"reason","changed");false}}
try{with C() as _{throw {reason:"original"}}}catch e{e.value.reason}'''
        self.assertEqual(self.run_code(source),'original')

    def test_exact_exit_result_and_cleanup_error_precedence(self):
        for result in ['1','"yes"','[]','{}','async fn(){true}()']:
            source='class C{fn __enter__(self){null}fn __exit__(self,e){'+result+'}}with C() as _{42}'
            with self.subTest(result=result),self.assertRaises(LanguageError):self.run_code(source)
        source='class C{fn __enter__(self){null}fn __exit__(self,e){throw "cleanup"}}'
        for body in ['42','throw "body"','return 42']:
            self.assertEqual(self.run_code(source+'fn work(){with C() as _{'+body+'}}try{work()}catch e{e.value}'),'cleanup')

    def test_return_tailcall_and_loop_transfers_exit_without_suppression(self):
        source='''var n=1;class C{fn __enter__(self){null}fn __exit__(self,e){n=2;true}}
fn read(){n}fn work(){with C() as _{return read()}}[work(),n]'''
        self.assertEqual(self.run_code(source),[1,2])
        source=SYNC.replace('null}}','true}}')+'for i in [1,2,3]{with C() as _{if i==1{continue}else{break}}};seen'
        self.assertEqual(self.run_code(source),['enter:scope',['scope',None],'enter:scope',['scope',None]])

    def test_special_method_mro_descriptors_and_instance_shadow_ignored(self):
        source='''var seen=[];class Exit{fn __get__(self,obj,owner){seen=append(seen,"bind");fn(e){seen=append(seen,"exit");null}}}
class A{fn __enter__(self){seen=append(seen,"enter");42}let __exit__=Exit()}class B(A){}let b=B();with b as value{value};seen'''
        self.assertEqual(self.run_code(source),['bind','enter','exit'])
        source='''class C{var __enter__=fn(){42};var __exit__=fn(e){null}}let c=C();c.__enter__=fn(){0};with c as x{x}'''
        self.assertEqual(self.run_code(source),42)

    def test_async_hooks_and_strict_task_results(self):
        self.assertEqual(self.run_code(ASYNC+'let value=async with C() as item{item+1};[value,seen]'),[43,['enter',None]])
        for enter,exit in [('fn __aenter__(self){42}','async fn __aexit__(self,e){null}'),
                           ('async fn __aenter__(self){42}','fn __aexit__(self,e){null}')]:
            with self.subTest(enter=enter,exit=exit),self.assertRaises(LanguageError):self.run_code('class C{'+enter+exit+'}async with C() as _{null}')

    def test_sync_enter_explicit_task_is_not_awaited(self):
        source='''var n=0;async fn acquire(){n=n+1;42}class C{fn __enter__(self){acquire()}fn __exit__(self,e){null}}
with C() as task{[type_of(task),n,await task]}'''
        self.assertEqual(self.run_code(source),['Task',0,42])

    def test_generators_hold_scope_across_yields_and_close_exits(self):
        for prefix,first,close in [('fn*','next(g)','close(g)'),('async fn*','await anext(g)','await aclose(g)')]:
            source=SYNC+prefix+' values(){with C() as resource{yield resource;yield "later"}}let g=values();let first='+first+';let before=length(seen);'+close+';[first,before,seen]'
            with self.subTest(prefix=prefix):self.assertEqual(self.run_code(source),[{'done':False,'value':'scope'},1,['enter:scope',['scope',None]]])
        source=ASYNC+'async fn* values(){async with C() as x{yield x}}let g=values();await anext(g);await aclose(g);seen'
        self.assertEqual(self.run_code(source),['enter',None])

    def test_owner_epoch_and_inert_records(self):
        foreign=Evaluator().run('class C{fn __enter__(self){42}fn __exit__(self,e){null}}C()')
        with self.assertRaises(LanguageError):Evaluator().run('with input as _{null}',{'input':foreign})
        with self.assertRaises(LanguageError):self.run_code('with {__enter__:fn(){42},__exit__:fn(e){null}} as _{null}')
        session=Session();session.evaluate('async fn old(){42}let task=old();class C{fn __aenter__(self){task}async fn __aexit__(self,e){null}}')
        with self.assertRaises(LanguageError):session.evaluate('async with C() as _{null}')

    def test_hard_controls_and_interrupts_never_reach_exit(self):
        for error in [ResourceLimit('limit'),ExecutionControl('cancel'),TaskCancelled('task'),KeyboardInterrupt('stop'),SystemExit('stop')]:
            calls=[]
            def effect(name,value):
                calls.append(name)
                if name=='stop':raise error
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)):
                self.run_code('class C{fn __enter__(self){null}fn __exit__(self,e){tool("cleanup",e);true}}try{with C() as _{tool("stop",null)}}catch e{42}',tool_handler=effect)
            self.assertEqual(calls,['stop'])

    def test_async_hard_controls_and_host_interrupt_identity(self):
        for error in [TaskCancelled('stop'),ResourceLimit('limit'),KeyboardInterrupt('stop'),SystemExit('stop')]:
            calls=[]
            def effect(kind,payload,cancel):
                calls.append(payload['name'])
                raise error
            source='''class C{async fn __aenter__(self){null}async fn __aexit__(self,e){tool("cleanup",e);true}}
async fn exercise(){try{async with C() as _{tool("stop",null)}}catch e{42}}await exercise()'''
            ev=Evaluator(tool_handler=lambda *args:None,effect_handler=effect)
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)) as caught:ev.run(source)
            self.assertIs(caught.exception,error);self.assertEqual(calls,['stop'])
            self.assertEqual(ev.frames,[]);self.assertIsNone(ev.suspension.pool)

    def test_budget_has_no_cleanup_allowance_and_exit_hard_error_wins(self):
        calls=[]
        source='class C{fn __enter__(self){null}fn __exit__(self,e){tool("cleanup",null);true}}try{with C() as _{while true{1}}}catch e{42}'
        with self.assertRaises(ResourceLimit):self.run_code(source,step_limit=120,tool_handler=lambda n,v:calls.append(n))
        self.assertEqual(calls,[])
        error=TaskCancelled('exit cancelled')
        def effect(name,value):raise error
        with self.assertRaises(TaskCancelled) as caught:self.run_code(source.replace('while true{1}','throw "body"'),tool_handler=effect)
        self.assertIs(caught.exception,error)

    def test_async_cancelled_await_cancels_child_and_drains_effect(self):
        started=threading.Event();finished=threading.Event();ev=Evaluator(tool_handler=lambda *a:None)
        calls=[]
        def effect(kind,payload,cancel):
            calls.append(payload['name']);started.set()
            cancel.wait(3);finished.set();return 42
        ev.effect_handler=effect
        def cancel():
            self.assertTrue(started.wait(3))
            for task in ev.suspension.tasks:
                if task.state=='running' and task.runner.waiting is not None:
                    task.cancel_event.set();return
        worker=threading.Thread(target=cancel);worker.start()
        source='''class C{async fn __aenter__(self){tool("acquire",null)}async fn __aexit__(self,e){tool("cleanup",null);null}}
async fn work(){async with C() as _{null}}await work()'''
        try:
            with self.assertRaises(TaskCancelled):ev.run(source)
        finally:worker.join(4)
        self.assertTrue(finished.is_set());self.assertEqual(calls,['acquire']);self.assertIsNone(ev.suspension.pool)
        self.assertTrue(all(t.state in ('completed','cancelled') for t in ev.suspension.tasks));self.assertEqual(ev.frames,[])

    def test_std_context_streams_and_explicit_custom_closers(self):
        source='''import "std/context" as ctx;var seen=[];fn* values(){try{yield 1;yield 2}finally{seen=append(seen,"sync")}}
async fn* asyncValues(){try{yield 3;yield 4}finally{seen=append(seen,"async")}}
let a=with ctx.closing(values()) as stream{for value in stream{break};7};
let b=async with ctx.aclosing(asyncValues()) as stream{async for value in stream{break};8};[a,b,seen]'''
        self.assertEqual(self.run_code(source),[7,8,['sync','async']])
        source='''import "std/context" as ctx;var seen=[];fn dispose(x){seen=append(seen,x);42}async fn adispose(x){dispose(x)}
with ctx.closing("file",dispose) as file{file};async with ctx.aclosing("socket",adispose) as socket{socket};seen'''
        self.assertEqual(self.run_code(source),['file','socket'])


    def test_closing_rejects_direct_and_nested_cold_results(self):
        declarations=[
            'async fn dispose(value){tool("unexpected",value);null}',
            'fn* dispose(value){tool("unexpected",value);yield value}',
            'async fn* dispose(value){tool("unexpected",value);yield value}',
            'async fn pending(value){tool("unexpected",value);null}fn dispose(value){pending(value)}',
        ]
        for declaration in declarations:
            calls=[]
            source='import "std/context" as ctx;'+declaration+'with ctx.closing(42,dispose) as value{value}'
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError) as caught:
                self.run_code(source,tool_handler=lambda *args:calls.append(args))
            self.assertEqual(caught.exception.code,'user_throw')
            self.assertIn('Task/Generator/AsyncGenerator',caught.exception.value)
            self.assertEqual(calls,[])

    def test_aclosing_awaits_once_and_rejects_secondary_cold_results(self):
        for flavor in ['async fn','fn*','async fn*']:
            body='tool("unexpected",value);'+('yield value' if '*' in flavor else 'null')
            source='import "std/context" as ctx;'+flavor+' pending(value){'+body+'}async fn dispose(value){pending(value)};async with ctx.aclosing(42,dispose) as value{value}'
            calls=[]
            with self.subTest(flavor=flavor),self.assertRaises(LanguageError) as caught:
                self.run_code(source,tool_handler=lambda *args:calls.append(args))
            self.assertEqual(caught.exception.code,'user_throw')
            self.assertIn('Task/Generator/AsyncGenerator',caught.exception.value)
            self.assertEqual(calls,[])

    def test_context_adapters_ignore_completed_ordinary_results(self):
        for result in ['null','false','42','"done"','[42]','{closed:true}']:
            for keyword,flavor,helper in [('with','fn','closing'),('async with','async fn','aclosing')]:
                source='import "std/context" as ctx;var cleaned=false;'+flavor+' dispose(value){cleaned=true;'+result+'}let out='+keyword+' ctx.'+helper+'(42,dispose) as value{value};[out,cleaned]'
                with self.subTest(result=result,helper=helper):self.assertEqual(self.run_code(source),[42,True])

    def test_context_adapter_result_checks_never_swallow_hard_control(self):
        for keyword,flavor,helper in [('with','fn','closing'),('async with','async fn','aclosing')]:
            for error in [ResourceLimit('limit'),ExecutionControl('stop'),TaskCancelled('cancel'),KeyboardInterrupt('interrupt'),SystemExit('exit')]:
                seen=[]
                def stop(name,value):
                    seen.append(name);raise error
                source='import "std/context" as ctx;'+flavor+' dispose(value){tool("stop",value)}try{'+keyword+' ctx.'+helper+'(42,dispose) as value{value}}catch e{99}'
                with self.subTest(helper=helper,error=type(error).__name__),self.assertRaises(type(error)) as caught:
                    self.run_code(source,tool_handler=stop)
                self.assertIs(caught.exception,error);self.assertEqual(seen,['stop'])


class ComputerContextTests(unittest.TestCase):
    def setUp(self):self.host=ComputerFixture()

    def test_native_async_context_uses_same_handle_and_close_effect(self):
        source='let c=await computer_open();let result=async with c as alias{[alias==c,computer_info(alias).status]};[result,computer_info(c).status]'
        self.assertEqual(self.host.evaluator().run(source),[[True,'open'],'closed'])
        self.assertEqual([op for op,payload in self.host.calls],['open','close'])
        self.assertTrue(self.host.calls[-1][1]['location']['file'])

    def test_ordinary_failure_closes_and_closed_or_nested_alias_rejected(self):
        source='''let c=await computer_open();let nested=async with c as alias{try{async with alias as _{null}}catch e{e.code}};
let closed=try{async with c as _{null}}catch e{e.code};[nested,closed,computer_info(c).status]'''
        self.assertEqual(self.host.evaluator().run(source),['computer_busy','computer_error','closed'])
        self.host.calls.clear()
        self.assertEqual(self.host.evaluator().run('let c=await computer_open();let error=try{async with c as _{throw 42}}catch e{e.value};[error,computer_info(c).status]'),[42,'closed'])
        self.assertEqual([op for op,payload in self.host.calls],['open','close'])

    def test_explicit_close_within_scope_is_idempotent(self):
        self.assertEqual(self.host.evaluator().run('async with await computer_open() as c{await computer_close(c);42}'),42)
        self.assertEqual([op for op,payload in self.host.calls],['open','close'])

    def test_concurrent_alias_scopes_and_async_generator_close(self):
        source='''let c=await computer_open();async fn use(){try{async with c as _{42}}catch e{e.code}}
[await gather([use(),use()]),computer_info(c).status]'''
        self.assertEqual(self.host.evaluator().run(source),[[42,'computer_busy'],'closed'])
        self.host.calls.clear()
        source='''async fn* values(){async with await computer_open() as c{yield c}}
let stream=values();let c=(await anext(stream)).value;let before=computer_info(c).status;
await aclose(stream);[before,computer_info(c).status]'''
        self.assertEqual(self.host.evaluator().run(source),['open','closed'])
        self.assertEqual([op for op,payload in self.host.calls],['open','close'])

    def test_sync_foreign_and_previous_epoch_computers_rejected(self):
        with self.assertRaises(LanguageError):self.host.evaluator().run('with await computer_open() as c{null}')
        value=self.host.evaluator().run('await computer_open()')
        with self.assertRaises(LanguageError):self.host.evaluator().run('async with input as _{null}',{'input':value})
        ev=self.host.evaluator();value=ev.run('await computer_open()')
        with self.assertRaises(LanguageError):ev.run('async with input as _{null}',{'input':value})

    def test_native_flag_released_on_hard_control_without_close_effect(self):
        ev=self.host.evaluator(tool_handler=lambda *args:(_ for _ in ()).throw(TaskCancelled('stop')))
        held=[]
        def hook(node,env,frames):
            if node[0]=='with' or node[0]=='async_with':return
            try:
                candidate=env.get('c')
                if candidate not in held:held.append(candidate)
            except LanguageError:pass
        ev.debug_hook=hook
        with self.assertRaises(TaskCancelled):ev.run('let c=await computer_open();async with c as _{tool("stop",null)}')
        self.assertEqual([op for op,payload in self.host.calls],['open']);self.assertTrue(held)
        self.assertTrue(all(not c.context_active and not c.busy for c in held))
