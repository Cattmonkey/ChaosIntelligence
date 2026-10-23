import threading
import unittest

from chaos.language import Evaluator, Parser, LanguageError, ResourceLimit, Env, serializable
from chaos.session import Session
from chaos.objects import ChaosClass, ChaosInstance, BoundMethod, SuperProxy, c3_merge


class ObjectTests(unittest.TestCase):
    def run_code(self, source, **kwargs):return Evaluator(**kwargs).run(source)

    def test_construct_fields_detached_method_and_dynamic_attribute(self):
        self.assertEqual(self.run_code('class C{var x:Int=1;fn __init__(self,x){self.x=x;null}fn get(self){self.x}}let a=C(42);let f=a.get;a["extra"]=7;[f(),a.extra,class_of(a)==C,type_of(C),type_of(a),type_of(f)]'),[42,7,True,'Class','Instance','Function'])

    def test_defaults_once_and_shared_mutable_instances(self):
        self.assertEqual(self.run_code('var hits=0;fn initial(){hits=hits+1;7}class Box{var x=0}class C{var n:Int=initial();let box=Box()}let a=C();let b=C();a.n=42;a.box.x=9;[hits,a.n,b.n,b.box.x]'),[1,42,7,9])

    def test_class_namespace_and_public_mutable_member_share_cell(self):
        self.assertEqual(self.run_code('class C{var n=1;let x=do{n=2;n};let read=fn(){n};fn f(self,x=n){x}}C.n=3;[C.n,C.x,C().n,C.read(),C().f()]'),[3,2,3,3,2])

    def test_object_descriptors_count_against_serialization_budget(self):
        name='x'*3000
        with self.assertRaises(ResourceLimit):self.run_code('class '+name+'{}let c='+name+'();map(fn(x){c},range(100))')

    def test_inherited_cell_override_does_not_mutate_base(self):
        self.assertEqual(self.run_code('class A{var n:Int=1}class B(A){}let a=A();let b=B();B.n=2;b.n=3;[A.n,B.n,a.n,b.n,B().n]'),[1,2,1,3,2])
        with self.assertRaises(LanguageError):self.run_code('class A{var n:Int=1}class B(A){}B().n="bad"')

    def test_readonly_members_and_records_are_not_mutated(self):
        for source in ['class C{let x=1}C().x=2','class C{fn f(self){1}}C().f=2','class C{let x=1}C.x=2','let x={a:1};x.a=2','let xs=[1];xs[0]=2']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_c3_diamond_and_cooperative_super(self):
        self.assertEqual(self.run_code('class A{fn f(self){["A"]}}class B(A){fn f(self){["B"]+super(B,self).f()}}class C(A){fn f(self){["C"]+super(C,self).f()}}class D(B,C){fn f(self){["D"]+super(D,self).f()}}D().f()'),['D','B','C','A'])

    def test_bad_mro_rejected_before_member_effects(self):
        for bases in ['A,A','A,1']:
            seen=[]
            with self.assertRaises(LanguageError):self.run_code('class A{}class B('+bases+'){let x=tool("bad",1)}',tool_handler=lambda *args:seen.append(1))
            self.assertEqual(seen,[])
        seen=[]
        with self.assertRaises(LanguageError):self.run_code('class X{}class Y{}class A(X,Y){}class B(Y,X){}class C(A,B){let x=tool("bad",1)}',tool_handler=lambda *args:seen.append(1))
        self.assertEqual(seen,[])

    def test_nominal_classes_and_subclass_contracts(self):
        self.assertEqual(self.run_code('fn factory(){class C{};C}let A=factory();let B=factory();class D(A){}let d:A=D();[A==B,isinstance(d,A),isinstance(d,B),issubclass(D,A),issubclass(A,D)]'),[False,True,False,True,False])
        with self.assertRaises(LanguageError):self.run_code('fn factory(){class C{};C}let A=factory();let B=factory();let x:A=B()')

    def test_self_contract_and_method_closure(self):
        self.assertEqual(self.run_code('let external=40;class C{let external=1;fn f(self:C,x:Int=external)->Int{external+x}}C().f()'),41)
        with self.assertRaises(LanguageError):self.run_code('class C{let x=42;fn f(self){x}}C().f()')
        with self.assertRaises(LanguageError):self.run_code('class C{fn f(self:C){1}}C.f(42)')

    def test_inherited_initializer_and_null_contract(self):
        self.assertEqual(self.run_code('class A{var x=0;fn __init__(self,x){self.x=x;null}}class B(A){}B(x=42).x'),42)
        for source in ['class C{}C(1)','class C{fn __init__(self){1}}C()','class C{async fn __init__(self){null}}C()','class C{fn* __init__(self){yield 1}}C()']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_all_method_flavors_and_bound_calls(self):
        self.assertEqual(self.run_code('class C{var x=40;async fn a(self,n=2){self.x+n}fn* g(self){yield self.x}async fn* ag(self){yield self.x+2}}let c=C();[await c.a(),collect(c.g()),await acollect(c.ag())]'),[42,[40],[42]])
        self.assertEqual(self.run_code('class C{fn add(self,a,b=2){a+b}fn __call__(self,x){self.add(x)}}let c:Function=C();let f:Function=c.add;[apply(f,[40]),partial(f,b=1)(41),compose(fn(x){x+1},c)(39)]'),[42,42,42])

    def test_declared_methods_bind_but_callable_fields_do_not(self):
        self.assertEqual(self.run_code('let f=fn(x){x+1};agent a{prompt:"x"}class C{let callback=f;var actor=a;fn call(self,x){self.actor(x)}}let c=C();[c.callback(41),c.call(42)]',agent_handler=lambda name,config,value:value),[42,42])
        self.assertEqual(self.run_code('class C{let __call__=fn(x){x+1}}let c:Function=C();c(41)'),42)
        with self.assertRaises(LanguageError):self.run_code('class C{let __call__=1}let c:Function=C()')

    def test_callable_special_lookup_is_mro_only(self):
        self.assertEqual(self.run_code('class C{var __call__=fn(x){x+1}}let c=C();c.__call__=fn(x){999};c(41)'),42)
        with self.assertRaises(LanguageError):self.run_code('class C{}let c=C();c.__call__=fn(x){x};c(42)')

    def test_tail_bound_calls_keep_bool_contract(self):
        self.assertEqual(self.run_code('class C{fn f(self,n){if n==0{42}else{self.f(n-1)}}}C().f(700)',step_limit=50000),42)
        with self.assertRaises(LanguageError):self.run_code('class C{fn f(self,n){if n==0{1}else{true && self.f(n-1)}}}C().f(2)')

    def test_introspection_and_explicit_super(self):
        self.assertEqual(self.run_code('class A{let a=1}class B(A){var b=2}let x=B();setattr(x,"extra",3);[getattr(x,"b"),hasattr(x,"missing"),dir(x),getattr(super(B,x),"a")]'),[2,False,['a','b','extra'],1])
        with self.assertRaises(LanguageError):self.run_code('class A{}class B{}super(A,B())')

    def test_vars_projects_own_state_without_aliasing_field_dictionary(self):
        self.assertEqual(self.run_code('class A{var inherited=1}class B(A){var own=2;fn f(self){1}}let b=B();b.own=42;b.extra=7;let copy=set(vars(b),"own",99);[vars(b),copy,b.own,keys(vars(B))]'),[{'own':42,'extra':7},{'own':99,'extra':7},42,['own','f']])
        self.assertEqual(self.run_code('class Box{var x=1}class C{}let c=C();c.box=Box();let v=vars(c);v.box.x=42;c.box.x'),42)
        with self.assertRaises(LanguageError):self.run_code('class C{}vars(super(C,C()))')

    def test_cross_owner_all_object_operations(self):
        other=Evaluator();klass=other.run('class C{var x=1;fn f(self){1}}C');obj=other.invoke(klass,[])
        for source,bindings in [('foreign.x',{'foreign':obj}),('foreign.x=2',{'foreign':obj}),('foreign.f()',{'foreign':obj}),('foreign()',{'foreign':klass}),('class D(foreign){}',{'foreign':klass}),('let x:foreign=null',{'foreign':klass}),('getattr(foreign,"x")',{'foreign':obj}),('isinstance(foreign,foreignClass)',{'foreign':obj,'foreignClass':klass})]:
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source,bindings)
        other.suspension.finish()

    def test_new_object_host_descriptors_sync_and_async(self):
        seen=[]
        def tool(name,value):seen.append(value);return 1
        def agent(name,config,value):seen.append(value);return 1
        def workflow(graph,value):seen.append(value);return 1
        source='agent a{prompt:"x"}class C{fn f(self){1}}let c=C();let payload=[C,c,c.f,super(C,c)];tool("t",payload);a(payload);workflow({},payload);async fn run(){tool("t",payload);a(payload);workflow({},payload)}await run()'
        self.run_code(source,tool_handler=tool,agent_handler=agent,workflow_handler=workflow)
        self.assertEqual(len(seen),6)
        for payload in seen:
            self.assertEqual(payload,[{'$class':'C'},{'$instance':'C'},{'$boundMethod':'f','$class':'C'},{'$super':'C','$instance':'C'}])

    def test_legacy_callable_sync_host_behavior_is_preserved(self):
        from chaos.language import Function
        seen=[]
        self.run_code('fn f(x){x}tool("t",f)',tool_handler=lambda name,value:seen.append(value))
        self.assertIsInstance(seen[0],Function)

    def test_agent_async_methods_overlap_on_same_ast_thread(self):
        barrier=threading.Barrier(2);workers=set();asts=set();main=threading.get_ident()
        def agent(name,config,value):workers.add(threading.get_ident());barrier.wait(timeout=2);return value
        self.assertEqual(self.run_code('agent a{prompt:"x"}class C{let actor=a;async fn f(self,x){self.actor(x)}}let c=C();await gather([c.f(41),c.f(42)])',agent_handler=agent,debug_hook=lambda *args:asts.add(threading.get_ident())),[41,42])
        self.assertEqual(asts,{main});self.assertEqual(len(workers),2)

    def test_session_objects_persist_but_method_tasks_do_not(self):
        session=Session();session.evaluate('class C{var x=1;async fn f(self){self.x}}let c=C();let t=c.f()')
        self.assertEqual(session.evaluate('c.x=42;await c.f()'),42)
        with self.assertRaises(LanguageError):session.evaluate('await t')

    def test_building_class_not_constructible_and_not_published(self):
        session=Session()
        with self.assertRaises(LanguageError):session.evaluate('class C{let instance=C()}')
        self.assertNotIn('C',session.env.values)
        session.evaluate('class C{}');self.assertEqual(session.evaluate('type_of(C())'),'Instance')

    def test_class_control_context_does_not_escape_outer_function(self):
        for source in ['fn f(){class C{let x=do{return 1}}}','while true{class C{let x=do{break}}}','fn* f(){class C{let x=yield 1}}','fn f(){class C{let x=await (async fn(){1})()}}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()
        self.assertEqual(self.run_code('async fn f(){class C{let x=await (async fn(){42})()};C.x}await f()'),42)

    def test_contextual_class_variable_and_c3_identity(self):
        self.assertEqual(self.run_code('let class=fn(x){x};class(42)'),42)
        self.assertEqual(self.run_code('let class=1;let other=42;class other'),42)
        self.assertEqual(self.run_code('let class=1;fn other(){42}class other()'),42)
        a,b=object(),object();self.assertEqual(c3_merge([[a,b],[b],[a,b]]),[a,b])


if __name__=='__main__':unittest.main()
