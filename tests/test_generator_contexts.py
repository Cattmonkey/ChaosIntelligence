import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, serializable
from chaos.suspension import TaskCancelled
from chaos.session import Session
from chaos.debugger import preview


class GeneratorContextTests(unittest.TestCase):
    def run_code(self, source, **kwargs):
        return Evaluator(**kwargs).run(source)

    def test_native_cold_exactly_one_yield_and_body_result(self):
        self.assertEqual(self.run_code('''var events=[];
          fn* resource(){events=append(events,"enter");yield 40;events=append(events,"exit");return 99}
          let manager:ContextManager=generator_context(resource());let before=events;
          let result=with manager as n{n+2};[before,result,events,type_of(manager)]'''),
          [[],42,['enter','exit'],'ContextManager'])

    def test_async_cold_cleanup_await_and_nested_managers(self):
        self.assertEqual(self.run_code('''import "std/context" as C;var events=[];
          async fn mark(n){events=append(events,n)}
          @C.asynccontextmanager
          async fn* resource(n){await mark(n);yield n;await mark(-n)}
          let a:AsyncContextManager=resource(1);let before=events;
          let value=async with a as first,resource(2) as second{first+second};
          [before,value,events,type_of(a)]'''),[[],3,[1,2,-2,-1],'AsyncContextManager'])

    def test_decorator_methods_patterns_defaults_keywords_and_class_access(self):
        self.assertEqual(self.run_code('''import "std/context" as C;
          class Resources {
            @C.contextmanager
            fn* open(self,pair as [left,right],extra=2,*,scale=1,**kwargs){yield (left+right+extra)*scale+kwargs.bonus}
          }
          let obj=Resources();
          [with obj.open([10,10],scale=2,bonus=0) as n{n},
           with Resources.open(obj,[19,20],bonus=1) as n{n}]'''),[44,42])

    def test_static_and_classmethod_wrappers(self):
        self.assertEqual(self.run_code('''import "std/context" as C;
          class Resources {
            @staticmethod
            @C.contextmanager
            fn* plain(n=42){yield n}
            @classmethod
            @C.contextmanager
            fn* bound(cls){yield cls}
          }
          [with Resources().plain() as n{n},with Resources.bound() as cls{cls==Resources}]'''),[42,True])

    def test_decorator_captures_factory_immutably(self):
        self.assertEqual(self.run_code('''import "std/context" as C;
          @C.contextmanager
          fn* original(n=42){yield n}
          original.factory=fn*(){yield 0};original.asynchronous=true;
          with original() as n{n}'''),42)

    def test_ordinary_error_injected_without_record_rewrapping(self):
        self.assertEqual(self.run_code('''import "std/context" as C;var seen=null;
          @C.contextmanager
          fn* recover(){try{yield 1}catch e{seen=e}}
          let value=with recover() as n{1/0};[value,seen.code,seen.value]'''),[None,'ZeroDivisionError',None])

    def test_original_error_identity_and_location_survive_finally(self):
        original=LanguageError('fixture',file='origin.chaos',line=7,column=3,code='fixture_error')
        events=[]
        def effect(name,value):
            if name=='body':raise original
            events.append(name)
        source='''fn* resource(){try{yield 1}finally{tool("cleanup",null)}}
          with generator_context(resource()) as n{tool("body",null)}'''
        with self.assertRaises(LanguageError) as caught:self.run_code(source,tool_handler=effect)
        self.assertIs(caught.exception,original)
        self.assertEqual(caught.exception.record()['location'],{'file':'origin.chaos','line':7,'column':3})
        self.assertEqual(events,['cleanup'])

    def test_cleanup_replacement_and_outer_suppression(self):
        self.assertEqual(self.run_code('''import "std/context" as C;var seen=[];
          @C.contextmanager
          fn* outer(){try{yield 1}catch e{seen=append(seen,e.value)}}
          @C.contextmanager
          fn* inner(){try{yield 2}finally{throw "cleanup"}}
          [with outer() as a,inner() as b{throw "body"},seen]'''),[None,['cleanup']])

    def test_zero_two_yields_and_illegal_close_yield(self):
        for declaration in ['fn* resource(){return 1}', 'fn* resource(){yield 1;yield 2}',
                            'fn* resource(){try{yield 1;yield 2}finally{yield 3}}']:
            ev=Evaluator()
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError):
                ev.run(declaration+'with generator_context(resource()) as n{n}')
            self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)
            self.assertTrue(all(g.state in ('closed','failed','completed') and not g.busy for g in ev.suspension.generators))

    def test_extra_yield_is_closed_once_with_cleanup(self):
        self.assertEqual(self.run_code('''var closed=0;
          fn* resource(){try{yield 1;yield 2}finally{closed=closed+1}}
          let error=try{with generator_context(resource()) as n{n}}catch e{e.code};[error,closed]'''),['context_error',1])

    def test_error_handler_cannot_yield_again(self):
        self.assertEqual(self.run_code('''fn* resource(){try{yield 1}catch e{yield 2}}
          try{with generator_context(resource()) as n{throw "body"}}catch e{e.code}'''),'context_error')

    def test_factory_return_contract_remains_on_generator_completion(self):
        with self.assertRaises(LanguageError):self.run_code('''import "std/context" as C;
          @C.contextmanager
          fn* resource()->Int{yield 42;return "bad"}with resource() as n{n}''')

    def test_return_break_continue_preserve_control_and_run_post_yield(self):
        self.assertEqual(self.run_code('''import "std/context" as C;var closed=0;
          @C.contextmanager
          fn* resource(){yield 1;closed=closed+1}
          fn answer(){with resource() as n{return 42}}
          var n=0;for value in [1,2,3]{with resource() as r{if value==1{continue}else{null};n=n+value;break}};
          [answer(),n,closed]'''),[42,2,3])

    def test_closing_outer_generator_runs_manager_post_yield(self):
        for flavor,first,close in [('fn*','next(stream)','close(stream)'),('async fn*','await anext(stream)','await aclose(stream)')]:
            source='''var events=[];fn* resource(){yield 42;events=append(events,"exit")}
              '''+flavor+''' outer(){with generator_context(resource()) as n{yield n}}
              let stream=outer();'''+first+';'+close+';events'
            with self.subTest(flavor=flavor):self.assertEqual(self.run_code(source),['exit'])

    def test_epoch_finalization_obeys_lexical_owner_not_creation_order(self):
        events=[]
        self.assertEqual(self.run_code('''fn* resource(){yield 42;tool("cleanup",null)}
          let manager=generator_context(resource());fn* outer(){with manager as n{yield n}}
          let stream=outer();next(stream);7''',tool_handler=lambda n,v:events.append(n)),7)
        self.assertEqual(events,['cleanup'])

    def test_reentry_and_wrong_flavor_leave_first_entry_usable(self):
        self.assertEqual(self.run_code('''fn* resource(){yield 42}let manager=generator_context(resource());
          let wrong=try{async with manager as n{n}}catch e{e.code};
          let first=with manager as n{[n,try{with manager as x{x}}catch e{e.code}]};
          [wrong,first,try{with manager as n{n}}catch e{e.code}]'''),
          ['argument_error',[42,'context_error'],'context_error'])

    def test_constructor_requires_exact_mode_and_unstarted_generator(self):
        sources=['generator_context(42)','fn* g(){yield 1}generator_context(g(),asynchronous=1)',
          'async fn* g(){yield 1}generator_context(g())','fn* g(){yield 1}generator_context(g(),true)',
          'fn* g(){yield 1}let s=g();next(s);generator_context(s)',
          'fn* g(){yield 1}let s=g();generator_context(s);generator_context(s)']
        for source in sources:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_reserved_aliases_cannot_be_consumed_closed_or_acquired(self):
        for operation in ['next(s)','send(s,null)','throw_into(s,42)','close(s)','collect(s)','iter(s)']:
            source='fn* g(){yield 42}let s=g();let m=generator_context(s);let before=try{'+operation+'}catch e{e.code};[before,with m as n{n}]'
            with self.subTest(operation=operation):self.assertEqual(self.run_code(source),['context_error',42])
        for operation in ['anext(s)','asend(s,null)','athrow(s,42)','aclose(s)']:
            source='async fn* g(){yield 42}let s=g();let t='+operation+';let m=generator_context(s,true);[try{await t}catch e{e.code},async with m as n{n}]'
            with self.subTest(operation=operation):self.assertEqual(self.run_code(source),['context_error',42])

    def test_alias_returned_from_yield_remains_reserved(self):
        self.assertEqual(self.run_code('''var s=null;fn* g(){yield s}s=g();
          with generator_context(s) as alias{try{next(alias)}catch e{e.code}}'''),'context_error')

    def test_foreign_and_previous_epoch_managers_are_rejected(self):
        value=Evaluator().run('fn* g(){yield 1}generator_context(g())')
        with self.assertRaises(LanguageError):Evaluator().run('with input as n{n}',{'input':value})
        session=Session();session.evaluate('fn* g(){yield 1}let manager=generator_context(g())')
        with self.assertRaises(LanguageError):session.evaluate('with manager as n{n}')

    def test_native_contract_copy_and_recording_ownership(self):
        from chaos.contexts import generator_context, context_owned
        from chaos.language import check_type
        ev=Evaluator();scheduler=ev.suspension;scheduler.begin()
        try:
            stream=scheduler.generator((x for x in ()));manager=generator_context(ev,stream)
            self.assertIs(check_type(manager,'ContextManager'),manager)
            self.assertIs(ev.copy_value({'manager':manager})['manager'],manager)
            with self.assertRaises(LanguageError):check_type(manager,'AsyncContextManager')
            other=scheduler.task((x for x in ()));other.recording_scope='another-recording';scheduler.current=other.runner
            with self.assertRaises(LanguageError) as caught:context_owned(manager,ev)
            self.assertEqual(caught.exception.code,'mode_error')
        finally:
            scheduler.current=None;scheduler.finish()

    def test_descriptions_and_host_projection_are_inert(self):
        seen=[];ev=Evaluator(tool_handler=lambda n,v:seen.append(v))
        value=ev.run('''fn* g(){tool("start",null);yield 1}
          let manager=generator_context(g());tool("inspect",[manager]);manager''')
        self.assertEqual(seen,[[{'$handle':'ContextManager','id':value.id,'state':'created'}]])
        self.assertEqual(serializable(value),{'$handle':'ContextManager','id':value.id,'state':'closed'})
        self.assertEqual(preview(value),{'type':'ContextManager','id':value.id,'state':'closed'})

    def test_hard_controls_never_run_cleanup_later(self):
        for flavor,keyword,flag in [('fn*','with','false'),('async fn*','async with','true')]:
            for error in [ResourceLimit('limit'),ExecutionControl('stop'),TaskCancelled('cancel'),KeyboardInterrupt('interrupt'),SystemExit('exit')]:
                seen=[]
                def effect(name,value):
                    if name=='stop':raise error
                    seen.append(name)
                ev=Evaluator(tool_handler=effect)
                source=flavor+' g(){try{yield 1}finally{tool("cleanup",null)}}'+keyword+' generator_context(g(),'+flag+') as n{tool("stop",null)}'
                with self.subTest(flavor=flavor,error=type(error).__name__),self.assertRaises(type(error)) as caught:ev.run(source)
                self.assertIs(caught.exception,error);self.assertEqual(seen,[])
                self.assertTrue(all(g.context_manager.state=='closed' and not g.busy for g in ev.suspension.generators))

    def test_group_abort_runs_finally_but_cannot_be_caught_or_suppressed(self):
        self.assertEqual(self.run_code('''var seen=[];async fn bad(){throw "original"}
          fn* resource(){try{yield 1}catch e{seen=append(seen,"caught")}finally{seen=append(seen,"cleanup");return null}}
          let error=try{async with task_group() as g{with generator_context(resource()) as n{g.spawn(bad());while true{1}}}}catch e{e.value};
          [error,seen]''',step_limit=3000),['original',['cleanup']])

    def test_inner_task_group_cannot_span_context_yield(self):
        with self.assertRaises(LanguageError) as caught:self.run_code('''async fn* resource(){async with task_group() as g{yield 42}}
          async with generator_context(resource(),true) as n{n}''')
        self.assertEqual(caught.exception.code,'task_group_error')

    def test_macro_generated_decorated_factory(self):
        self.assertEqual(self.run_code('''import "std/context" as C;
          macro make(decorator,value){quote{
            @unquote(decorator)
            fn* resource(){yield unquote(value)};resource
          }}
          let factory=make!(C.contextmanager,42);with factory() as n{n}'''),42)

    def test_async_error_protocol_and_repeat_entry(self):
        self.assertEqual(self.run_code('''var seen=null;
          async fn* resource(){try{yield 1}catch e{seen=e.code}}
          let manager=generator_context(resource(),true);
          [async with manager as n{1/0},seen,try{async with manager as n{n}}catch e{e.code}]'''),
          [None,'ZeroDivisionError','context_error'])
        for body in ['return 1','yield 1;yield 2','try{yield 1}catch e{yield 2}']:
            source='async fn* g(){'+body+'}async with generator_context(g(),true) as n{throw "body"}'
            with self.subTest(body=body),self.assertRaises(LanguageError):self.run_code(source)

    def test_async_group_abort_during_exit_drains_and_late_hard_wins(self):
        for late in [None,ResourceLimit('late hard')]:
            started=threading.Event();finished=threading.Event();seen=[]
            def effect(kind,payload,cancel):
                if payload['name']=='exit':
                    started.set();self.assertTrue(cancel.wait(2));finished.set()
                    if late is not None:raise late
                    return None
                if payload['name']=='bad':
                    self.assertTrue(started.wait(2));raise LanguageError('original')
                seen.append(payload['name'])
            ev=Evaluator(tool_handler=lambda *args:None,effect_handler=effect)
            source='''async fn bad(){tool("bad",null)}
              async fn* resource(){try{yield 42;tool("exit",null)}finally{tool("after",null)}}
              try{async with task_group() as g{g.spawn(bad());async with generator_context(resource(),true) as n{n}}}catch e{e.message}'''
            with self.subTest(late=late):
                if late is None:self.assertEqual(ev.run(source),'original')
                else:
                    with self.assertRaises(ResourceLimit) as caught:ev.run(source)
                    self.assertIs(caught.exception,late)
                self.assertTrue(finished.is_set());self.assertEqual(seen,[])
                self.assertIsNone(ev.suspension.pool);self.assertEqual(ev.frames,[])

    def test_async_group_abort_drains_private_entry_before_scope_finishes(self):
        started=threading.Event();finished=threading.Event();seen=[]
        def effect(kind,payload,cancel):
            if payload['name']=='acquire':
                started.set();self.assertTrue(cancel.wait(2));finished.set();return 42
            if payload['name']=='bad':
                self.assertTrue(started.wait(2));raise LanguageError('original')
            seen.append(payload['name'])
        ev=Evaluator(tool_handler=lambda *args:None,effect_handler=effect)
        result=ev.run('''async fn bad(){tool("bad",null)}
          async fn* resource(){try{yield tool("acquire",null)}finally{tool("cleanup",null)}}
          try{async with task_group() as g{g.spawn(bad());async with generator_context(resource(),true) as n{n}}}catch e{e.message}''')
        self.assertEqual(result,'original');self.assertTrue(finished.is_set());self.assertEqual(seen,[])
        self.assertIsNone(ev.suspension.pool);self.assertFalse(ev.suspension.active)


if __name__=='__main__':unittest.main()
