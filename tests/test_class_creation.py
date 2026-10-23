"""Native creation protocols, not host-language class wrappers."""
from dataclasses import FrozenInstanceError
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, Parser, Native, TailCall, ExecutionControl, Function
from chaos.objects import ClassMember, ChaosInstance
from chaos.session import Session
from chaos.suspension import TaskCancelled


class ClassCreationTests(unittest.TestCase):
    def code(self, source, **options): return Evaluator(**options).run(source)

    def test_legacy_ast_and_keyword_source_order(self):
        self.assertEqual(len(Parser('class C{}').program()[1][0]), 4)
        self.assertEqual(len(Parser('class C(tag=42){}').program()[1][0]), 5)
        source = '''var seen=[];class Base{fn __init_subclass__(cls,*,a,b){seen=append(seen,[a,b]);null}}
        fn value(x){seen=append(seen,x);x}fn base(){seen=append(seen,"base");Base}
        class C(base(),a=value(1),**{b:value(2)}){let n=value(3)}seen'''
        self.assertEqual(self.code(source), ['base', 1, 2, 3, [1,2]])
        for header in ['a=1,Base', '*[Base]', 'a=1,a=2']:
            with self.subTest(header=header), self.assertRaises(LanguageError): Parser('class C('+header+'){}').program()

    def test_header_errors_before_body_and_legacy_base_error_order(self):
        for header in ['1,tag=tool("header",1)', 'Base,Base,tag=tool("header",1)', 'tag=1,**{tag:2}', '**42']:
            seen=[]
            with self.subTest(header=header), self.assertRaises(LanguageError):
                self.code('class Base{}class C('+header+'){let x=tool("body",1)}', tool_handler=lambda name,value:seen.append(name))
            self.assertEqual(seen, [])
        seen=[]
        with self.assertRaises(LanguageError) as caught:
            self.code('class C(metaclass=tool("header",1)){let x=tool("body",1)}', tool_handler=lambda name,value:seen.append(name))
        self.assertEqual(caught.exception.code, 'argument_error'); self.assertEqual(seen,['header'])
        seen=[]
        with self.assertRaises(LanguageError) as caught:
            self.code('class C(**tool("options",1)){}',tool_handler=lambda name,value:seen.append(name) or {'metaclass':1})
        self.assertEqual(caught.exception.code,'argument_error');self.assertEqual(seen,['options'])

    def test_raw_callable_field_and_explicit_dynamic_method(self):
        source='''fn f(self,x){self.n+x};let C=new_class("C",[],{
        n:class_member(40,mutable=true,contract="Int"), f:class_member(f,method=true), callback:fn(x){x+1}});
        let c=C();[c.f(2),c.callback(41),class_members(C).f.value==f,class_members(C).f.method]'''
        self.assertEqual(self.code(source), [42,42,True,True])
        with self.assertRaises(LanguageError):self.code('let C=new_class("C",[],{n:1});C.n=2')

    def test_reflection_fresh_cells_snapshot_and_default_identity(self):
        source='''class Box{var n=0}class A{var n:Int=1;let box=Box();fn f(self){self.n}}
        let members=class_members(A);let B=new_class("B",[],members);let C=new_class("C",[],members);
        B.n=42;B.box.n=7;A.n=2;
        [members.n.value,A.n,B.n,C.n,B().f(),C.box.n,members.box.value==A.box,keys(members)]'''
        self.assertEqual(self.code(source), [1,2,42,1,42,7,True,['n','box','f']])

    def test_class_members_only_own_members_and_descriptor_inert(self):
        self.assertEqual(self.code('''var gets=0;class D{fn __get__(self,obj,owner){gets=gets+1;42}}
        class A{let inherited=1}class B(A){let x=D()};let m=class_members(B);[gets,keys(m),type_of(m.x.value)]'''),[0,['x'],'Instance'])

    def test_member_schema_readonly_and_exact_flags(self):
        self.assertEqual(self.code('let m:ClassMember=class_member(42,contract="Int");[m.value,m.mutable,m.method,m.contract,type_of(m)]'),[42,False,False,'Int','ClassMember'])
        for expr in ['class_member(1,mutable=1)','class_member(1,method=1)','class_member(fn(self){1},mutable=true,method=true)',
                     'class_member(1,method=true)','class_member(fn(*args){1},method=true)','class_member(1,contract="Missing")',
                     'class_member("bad",contract="Int")','let m=class_member(1);m.value=2','let m=class_member(1);delattr(m,"value")']:
            with self.subTest(expr=expr),self.assertRaises(LanguageError):self.code(expr)
        member=self.code('class_member(42)')
        with self.assertRaises(FrozenInstanceError):member.mutable=True

    def test_nominal_and_data_contracts_are_values_not_resolved_names(self):
        source='''class A{};data Choice{Yes(value),No};let a=A();let C=new_class("C",[],{
        a:class_member(a,mutable=true,contract=A),choice:class_member(Yes(42),contract=Choice)});
        [C().a==a,class_members(C).a.contract==A,class_members(C).choice.contract==Choice]'''
        self.assertEqual(self.code(source),[True,True,True])
        with self.assertRaises(LanguageError):self.code('class A{}class_member(A(),contract="A")')
        with self.assertRaises(LanguageError):self.code('class A{}let C=new_class("C",[],{a:class_member(A(),mutable=true,contract=A)});C().a=42')

    def test_all_function_flavors_roundtrip_as_methods(self):
        source='''let C=new_class("C",[],{a:class_member(async fn(self){42},method=true),
        g:class_member(fn*(self){yield 41},method=true),ag:class_member(async fn*(self){yield 40},method=true)});
        let D=new_class("D",[],class_members(C));let d=D();[await d.a(),collect(d.g()),await acollect(d.ag())]'''
        self.assertEqual(self.code(source),[42,[41],[40]])

    def test_descriptor_subclass_decorator_and_publication_order(self):
        source='''var seen=[];class D{fn __set_name__(self,owner,name){seen=append(seen,["name",name,owner().ready]);null}}
        class Base{fn __init_subclass__(cls,*,tag){seen=append(seen,["subclass",tag,cls().ready]);null}}
        fn deco(cls){seen=append(seen,["decorate",cls().ready]);cls}
        @deco
        class C(Base,tag=42){let ready=42;let a=D();let b=D()};seen'''
        self.assertEqual(self.code(source),[['name','a',42],['name','b',42],['subclass',42,42],['decorate',42]])

    def test_own_subclass_hook_not_called_until_next_derivation(self):
        self.assertEqual(self.code('var seen=[];class A{fn __init_subclass__(cls){seen=append(seen,cls);null}}let before=length(seen);class B(A){}[before,length(seen),seen[0]==B]'),[0,1,True])

    def test_diamond_one_hook_and_explicit_cooperative_super(self):
        source='''var seen=[];class A{fn __init_subclass__(cls,**kw){seen=append(seen,"A");super(A,cls).__init_subclass__(**kw);null}}
        class B(A){fn __init_subclass__(cls,*,b=0,**kw){seen=append(seen,["B",b]);super(B,cls).__init_subclass__(**kw);null}}
        class C(A){fn __init_subclass__(cls,*,c=0,**kw){seen=append(seen,["C",c]);super(C,cls).__init_subclass__(**kw);null}}
        seen=[];class D(B,C,b=1,c=2){}seen'''
        self.assertEqual(self.code(source),[['B',1],['C',2],'A'])
        with self.assertRaises(LanguageError):self.code('class A{fn __init_subclass__(cls,**kw){super(A,cls).__init_subclass__(**kw);null}}class B(A,extra=1){}')
        with self.assertRaises(LanguageError):self.code('class A{}super(A,A()).missing')

    def test_dynamic_subclass_hook_and_explicit_classmethod(self):
        source='''var seen=[];fn hook(cls,*,tag){seen=append(seen,[cls,tag]);null}
        let A=new_class("A",[],{__init_subclass__:class_member(hook,method=true)});
        let B=new_class("B",[A],{}, {tag:42});
        class P{@classmethod
        fn __init_subclass__(cls,*,n){cls.n=n;null}}class Q(P,n=7){}
        [seen[0][0]==B,seen[0][1],Q.n,class_members(A).__init_subclass__.value==hook]'''
        self.assertEqual(self.code(source),[True,42,7,True])

    def test_subclass_hook_failure_unpublished_escaped_completed_class(self):
        session=Session();session.evaluate('var escaped=null;class A{fn __init_subclass__(cls){escaped=cls;throw "stop"}}')
        with self.assertRaises(LanguageError):session.evaluate('class Hidden(A){let ready=42}')
        self.assertNotIn('Hidden',session.env.values)
        self.assertEqual(session.evaluate('escaped().ready'),42)

    def test_subclass_hook_sync_and_null_boundaries(self):
        for declaration in ['async fn __init_subclass__(cls){null}','fn* __init_subclass__(cls){yield 1}',
                            'async fn* __init_subclass__(cls){yield 1}','fn __init_subclass__(cls){42}',
                            'fn __init_subclass__(cls){(async fn(){null})()}']:
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError):self.code('class A{'+declaration+'}class B(A){}')

    def test_plain_static_and_classmethod_new_receive_class_once(self):
        for decorator in ['', '@staticmethod\n', '@classmethod\n']:
            source='class C{var n=0;'+decorator+'fn __new__(cls,x){let c=alloc_instance(cls);c.n=x; c}fn __init__(self,x){self.n=self.n+1;null}}let c=C(x=41);[class_of(c)==C,c.n]'
            with self.subTest(decorator=decorator):self.assertEqual(self.code(source),[True,42])
        self.assertEqual(self.code('class C{fn __new__(cls){alloc_instance(cls)}}let c=C();[class_of(C.__new__(C))==C,class_of(c.__new__(C))==C]'),[True,True])

    def test_new_returned_subtype_uses_actual_initializer_once(self):
        source='''var target=null;var seen=[];class A{fn __new__(cls,x){alloc_instance(target)}fn __init__(self,x){throw "wrong"}}
        class B(A){var n=0;fn __init__(self,x){seen=append(seen,x);self.n=x;null}}target=B;
        let b=A(42);[class_of(b)==B,b.n,seen]'''
        self.assertEqual(self.code(source),[True,42,[42]])

    def test_new_descriptor_callable_binding_does_not_drop_requested_class(self):
        source='''class Maker{fn build(self,cls,x){let c=alloc_instance(cls);c.n=x;c}}let maker=Maker();
        class NewHook{fn __get__(self,obj,owner){maker.build}}
        class C{var n=0;let __new__=NewHook();fn __init__(self,x){self.n=self.n+1;null}}C(41).n'''
        self.assertEqual(self.code(source),42)

    def test_new_foreign_result_skips_init_and_does_not_allocate(self):
        ev=Evaluator();self.assertEqual(ev.run('class C{fn __new__(cls,x){x}fn __init__(self,x){throw "wrong"}}[C(42),C({a:1})]'),[42,{'a':1}]);self.assertEqual(ev.object_instance_count,0)
        self.assertEqual(self.code('class Other{let n=42};let other=Other();class C{fn __new__(cls){other}fn __init__(self){throw "wrong"}}C()==other'),True)

    def test_new_sync_handles_and_bad_init(self):
        for body in ['async fn __new__(cls){42}','fn* __new__(cls){yield 1}','async fn* __new__(cls){yield 1}',
                     'fn __new__(cls){(async fn(){42})()}','fn __new__(cls){(fn*(){yield 1})()}']:
            with self.subTest(body=body),self.assertRaises(LanguageError):self.code('class C{'+body+'}C()')
        for init in ['fn __init__(self){42}','async fn __init__(self){null}']:
            with self.subTest(init=init),self.assertRaises(LanguageError):self.code('class C{fn __new__(cls){alloc_instance(cls)}'+init+'}C()')

    def test_dynamic_input_function_closure_not_rewritten(self):
        ev=Evaluator();self.assertEqual(ev.run('let captured=40;fn f(self,x){captured+x}let old=f;let A=new_class("A",[],{f:class_member(f,method=true)});let B=new_class("B",[],class_members(A));[A().f(2),B().f(2),class_members(B).f.value==old]'),[42,42,True])

    def test_allocation_and_class_count_are_actual_resource_limits(self):
        session=Session();session.evaluate('class C{}')
        ev=session.evaluator;ev.object_instance_count=9999
        self.assertIsInstance(session.evaluate('alloc_instance(C)'),ChaosInstance)
        with self.assertRaises(ResourceLimit):session.evaluate('alloc_instance(C)')
        ev.object_class_count=1000
        with self.assertRaises(ResourceLimit):session.evaluate('new_class("D")')
        with self.assertRaises(LanguageError):self.code('class C{let x=alloc_instance(C)}')

    def test_dynamic_schema_c3_failure_before_naming_effect(self):
        for expr in ['new_class("C",[1])','new_class("C",[A,A])','new_class("C",[],[])','new_class("C",[],{},[])','new_class("C",[],{}, {metaclass:A})','new_class("Int")']:
            with self.subTest(expr=expr),self.assertRaises(LanguageError):self.code('class A{};'+expr)
        seen=[]
        with self.assertRaises(LanguageError):self.code('''class D{fn __set_name__(self,owner,name){tool("name",name);null}}
        class X{}class Y{}class A(X,Y){}class B(Y,X){}new_class("C",[A,B],{d:D()})''',tool_handler=lambda *args:seen.append(args))
        self.assertEqual(seen,[])

    def test_nested_cross_owner_values_contract_and_results_rejected(self):
        other=Evaluator();foreign=other.run('class C{};C');foreign_fn=other.run('fn f(self){1};f')
        for code,bindings in [('new_class("D",[foreign])',{'foreign':foreign}),
                              ('class_member({x:[foreign]})',{'foreign':foreign}),
                              ('new_class("D",[],{x:[foreign]})',{'foreign':foreign}),
                              ('class_member(42,contract=foreign)',{'foreign':foreign}),
                              ('class_member(foreign,method=true)',{'foreign':foreign_fn})]:
            with self.subTest(code=code),self.assertRaises(LanguageError):self.code(code) if not bindings else Evaluator().run(code,bindings)
        ev=Evaluator();member=other.run('class_member(1)')
        with self.assertRaises(LanguageError):ev.run('foreign.value',{'foreign':member})
        with self.assertRaises(LanguageError):ev.run('new_class("D",[],{x:foreign})',{'foreign':member})

    def test_narrow_metadata_projection_preserves_function_shape_and_denies_effect(self):
        ev=Evaluator(tool_handler=lambda name,value:42)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        source='''fn f(self){tool("secret",1)};let C=new_class("C",[],{f:class_member(f,method=true)});
        fn reflect(){class_members(C)};let m=limited(reflect);let D=new_class("D",[],m);
        [type_of(m.f.value),try{D().f()}catch e{e.code}]'''
        self.assertEqual(ev.run(source,{'limited':limited}),['Function','capability_error'])
        self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)

    def test_restricted_metadata_cannot_widen_naming_or_inherited_hook_authority(self):
        for kind in ['name','subclass']:
            effects=[];ev=Evaluator(tool_handler=lambda name,value:effects.append(name))
            limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
            if kind=='name':
                source='''class D{fn __set_name__(self,owner,name){tool("secret",1);null}}let d=D();
                fn capture(){class_member(d)}let m=limited(capture);try{new_class("C",[],{d:m})}catch e{e.code}'''
            else:
                source='''fn hook(cls){tool("secret",1);null}fn capture(){class_member(hook,method=true)}
                let m=limited(capture);let A=new_class("A",[],{__init_subclass__:m});try{class B(A){}}catch e{e.code}'''
            with self.subTest(kind=kind):self.assertEqual(ev.run(source,{'limited':limited}),'capability_error')
            self.assertEqual(effects,[])

    def test_vars_reflection_retains_class_and_instance_authority(self):
        for access in ['vars(C).f', 'let c=C();c.f=f;vars(c).f']:
            effects=[];ev=Evaluator(tool_handler=lambda name,value:effects.append(name) or 42)
            limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
            source='''fn f(x){tool("secret",x)}fn capture(){class_member(f)}let m=limited(capture);
            let C=new_class("C",[],{f:m});let extracted=do{ACCESS};
            [type_of(extracted),try{extracted(1)}catch e{e.code}]'''.replace('ACCESS',access)
            if 'c.f=f' in access:
                source=source.replace('{f:m}','{marker:m}')
            with self.subTest(access=access):self.assertEqual(ev.run(source,{'limited':limited}),['Function','capability_error'])
            self.assertEqual(effects,[])

    def test_factory_return_cannot_escape_constructor_authority(self):
        for body,access in [('secret','escaped'),('{nested:[secret]}','escaped.nested[0]')]:
            effects=[];ev=Evaluator(tool_handler=lambda name,value:effects.append(name) or 42)
            limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
            source='''fn secret(x){tool("secret",x)}fn make(){class_member(fn(cls){BODY},method=true)}
            let m=limited(make);let C=new_class("C",[],{__new__:m});let escaped=C();let f=ACCESS;
            [type_of(f),try{f(1)}catch e{e.code}]'''.replace('BODY',body).replace('ACCESS',access)
            with self.subTest(body=body):self.assertEqual(ev.run(source,{'limited':limited}),['Function','capability_error'])
            self.assertEqual(effects,[])

    def test_all_live_values_validate_nested_owner_before_class_hooks(self):
        from chaos.team_values import Team
        from chaos.process_values import Process
        from chaos.session_values import AgentSession
        other=Evaluator()
        values=[other.run('current_program()'),other.run('plan([{id:"x",call:"f"}])'),other.run('channel()'),
                Team('{"name":"foreign"}',other),Process('{"name":"foreign"}',other),
                AgentSession(other,{'key':'foreign','revision':1,'status':'ready'})]
        for value in values:
            for source in ['class_member({nested:[foreign]})','new_class("C",[],{nested:[foreign]})',
                           'class B{fn __init_subclass__(cls,**kw){throw "hook"}}new_class("C",[B],{}, {nested:[foreign]})',
                           'class C{fn __new__(cls){[foreign]}}C()']:
                with self.subTest(type=type(value).__name__,source=source),self.assertRaises(LanguageError) as caught:
                    Evaluator().run(source,{'foreign':value})
                self.assertEqual(caught.exception.code,'ownership_error')

    def test_host_subclasses_rejected_without_running_host_methods(self):
        from chaos.class_creation import member_value, create, ClassRequest
        seen=[]
        class HostMap(dict):
            def items(self):seen.append('items');return super().items()
        class HostList(list):
            def __iter__(self):seen.append('iter');return super().__iter__()
        ev=Evaluator()
        for value in [HostMap(x=1),HostList([1]),{'nested':HostMap(x=1)},[HostList([1])]]:
            with self.subTest(type=type(value).__name__),self.assertRaises(LanguageError):member_value(ev,value)
        self.assertEqual(seen,[])

    def test_header_subclass_and_new_effects_stay_on_ast_thread(self):
        main=threading.get_ident();asts=set();workers=set();seen=[]
        def handler(name,config,value):workers.add(threading.get_ident());seen.append(value);return value
        source='''agent a{};class Base{fn __init_subclass__(cls,*,tag){a(tag);null}}
        async fn work(){class C(Base,tag=40){fn __new__(cls,x){let c=alloc_instance(cls);a(x);c}fn __init__(self,x){a(x+1);null}}C(41)}await work();null'''
        self.assertIsNone(self.code(source,agent_handler=handler,debug_hook=lambda *args:asts.add(threading.get_ident())))
        self.assertEqual(seen,[40,41,42]);self.assertEqual(asts,{main});self.assertTrue(workers);self.assertNotIn(main,workers)

    def test_cancel_and_budget_restore_call_frames(self):
        for source in ['class A{fn __init_subclass__(cls){tool("cancel",1);null}}async fn work(){class B(A){}}await work()',
                       'class C{fn __new__(cls){tool("cancel",1);alloc_instance(cls)}}async fn work(){C()}await work()']:
            ev=Evaluator(tool_handler=lambda *args:None,effect_handler=lambda *args:(_ for _ in ()).throw(TaskCancelled('fixture')))
            with self.subTest(source=source),self.assertRaises(ExecutionControl):ev.run(source)
            self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0);self.assertIsNone(ev.suspension.pool)
        ev=Evaluator(step_limit=250)
        with self.assertRaises(ResourceLimit):ev.run('class A{fn __init_subclass__(cls){while true{1}}}class B(A){}')
        self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)


if __name__ == '__main__': unittest.main()
