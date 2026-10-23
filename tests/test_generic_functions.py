"""Persistent multi-methods use native objects and the original effect scheduler."""
import unittest

from chaos.language import Evaluator, LanguageError, Native, TailCall, ResourceLimit
from chaos.session import Session


class GenericFunctionTests(unittest.TestCase):
    def code(self, source, **options): return Evaluator(**options).run(source)

    def test_persistent_extension_replacement_removal_and_aliases(self):
        self.assertEqual(self.code('''let empty=generic_function("f");let f=generic_method(empty,["Int"],fn(x){x+1});
        let alias=f;let changed=generic_method(f,["Int"],fn(x){x+2});let removed=generic_remove(changed,["Int"]);
        [f(40),alias(41),changed(40),generic_info(empty).methodCount,generic_info(removed).methodCount,
         try{removed(1)}catch e{e.code},generic_info(generic_remove(f,["String"])).methodCount]'''),
        [41,42,42,0,0,'no_applicable_method',1])

    def test_keyword_helper_api_and_original_extra_arguments(self):
        self.assertEqual(self.code('''let f=generic_method(function=generic_function(name="f",arity=1),
        specializers=["Int"],implementation=fn(x,y=0,*,z=0){x+y+z},qualifier="primary");
        [f(20,20,z=2),generic_methods(function=f)[0].qualifier,generic_info(function=f).name,
         generic_info(generic_remove(function=f,specializers=["Int"],qualifier="primary")).methodCount]'''),
        [42,'primary','f',0])

    def test_builtin_subtypes_bool_and_overlapping_numeric_contracts(self):
        self.assertEqual(self.code('''var f=generic_function("f");f=generic_method(f,["Any"],fn(x){"any"});
        f=generic_method(f,["Number"],fn(x){"number"});f=generic_method(f,["Int"],fn(x){"int"});
        f=generic_method(f,["Bool"],fn(x){"bool"});[f(1),f(1.5),f(true),f("text")]'''),['int','number','bool','any'])
        self.assertEqual(self.code('''var f=generic_function("f");f=generic_method(f,["Number"],fn(x){1});
        f=generic_method(f,["Rational"],fn(x){2});let ambiguous=try{f(1)}catch e{e.code};
        f=generic_method(f,["Int"],fn(x){42});[ambiguous,f(1),f(1.5),f(fraction(1,2))]'''),['ambiguous_method',42,1,2])

    def test_native_class_identity_c3_and_left_to_right_precedence(self):
        self.assertEqual(self.code('''class Base{}class Left(Base){}class Right(Base){}class Leaf(Left,Right){}
        var f=generic_function("f",2);f=generic_method(f,[Base,"Int"],fn(x,y){1});
        f=generic_method(f,[Right,"Any"],fn(x,y){2});f=generic_method(f,[Left,"Any"],fn(x,y){42});
        [f(Leaf(),1),f(Right(),1),f(Base(),1)]'''),[42,2,1])
        self.assertEqual(self.code('''class C{};let Old=C;let f=generic_method(generic_function("f"),[Old],fn(x){42});
        let Other=new_class("C",[],{});[f(Old()),try{f(Other())}catch e{e.code},generic_methods(f)[0].specializers[0]==Old]'''),[42,'no_applicable_method',True])

    def test_class_objects_dispatch_by_actual_metaclass_without_hooks(self):
        self.assertEqual(self.code('''var effects=0;class Meta(Type){fn __instancecheck__(self,value){effects=effects+1;true}}
        class C(metaclass=Meta){};var f=generic_function("f");f=generic_method(f,["Class"],fn(x){1});
        f=generic_method(f,[Meta],fn(x){42});[f(C),f(Meta),effects]'''),[42,1,0])

    def test_data_selectors_reuse_structural_contracts_without_name_lookup(self):
        self.assertEqual(self.code('''data Choice{Yes(value),No};let D=Choice;var f=generic_function("f");
        f=generic_method(f,["Record"],fn(x){1});f=generic_method(f,[D],fn(x){42});
        [f(Yes(1)),f({n:1}),generic_methods(f)[1].specializers[0]==D,
         try{generic_method(f,["Choice"],fn(x){0})}catch e{e.code}]'''),[42,1,True,'argument_error'])
        # Two actual descriptors may describe overlapping structural data values.
        from chaos.language import DataType
        first=DataType('Choice',(('Yes',1),));second=DataType('Choice',(('Yes',1),))
        evaluator=Evaluator();evaluator.env.bind('first',first);evaluator.env.bind('second',second)
        self.assertEqual(evaluator.run('''var f=generic_function("f");f=generic_method(f,[first],fn(x){1});
        f=generic_method(f,[second],fn(x){2});try{f({"$type":"Choice","$tag":"Yes","$values":[1]})}catch e{e.code}'''),'ambiguous_method')

    def test_combination_order_and_repeated_next(self):
        self.assertEqual(self.code('''var seen=[];class Base{}class Child(Base){};var f=generic_function("f");
        f=generic_method(f,[Base],fn(x){seen=append(seen,"primary");42});
        f=generic_method(f,[Base],fn(x){seen=append(seen,"beforeBase");null},"before");
        f=generic_method(f,[Child],fn(x){seen=append(seen,"beforeChild");null},"before");
        f=generic_method(f,[Base],fn(x){seen=append(seen,"afterBase");null},"after");
        f=generic_method(f,[Child],fn(x){seen=append(seen,"afterChild");null},"after");
        f=generic_method(f,[Base],fn(next,x){seen=append(seen,"aroundBase");let r=next();seen=append(seen,"endBase");r},"around");
        f=generic_method(f,[Child],fn(next,x){seen=append(seen,"aroundChild");let r=next();seen=append(seen,"endChild");r},"around");
        [f(Child()),seen]'''),[42,['aroundChild','aroundBase','beforeChild','beforeBase','primary','afterBase','afterChild','endBase','endChild']])
        self.assertEqual(self.code('''var count=0;var f=generic_method(generic_function("f"),["Int"],fn(x){count=count+1;x});
        f=generic_method(f,["Int"],fn(next,x){next()+next()},"around");[f(21),count]'''),[42,2])

    def test_next_captures_exact_version_arguments_and_keywords(self):
        self.assertEqual(self.code('''var f=generic_method(generic_function("f"),["Int"],fn(x,*,add=0){x+add});
        f=generic_method(f,["Int"],fn(next,x,*,add=0){next},"around");let saved=f(40,add=2);
        let changed=generic_method(f,["Int"],fn(x,*,add=0){100});[saved(),changed(1)(),try{saved(1)}catch e{e.code}]'''),[42,100,'argument_error'])

    def test_complete_dispatch_preflight_before_any_method_effects(self):
        for missing,qualifier in [(False,'before'),(False,'after'),(False,'around'),(True,'around')]:
            prefix='var seen=[];var f=generic_function("f");'
            if not missing:prefix+='f=generic_method(f,["Int"],fn(x){seen=append(seen,"primary");42});'
            callback='fn(next,x){seen=append(seen,1);42}' if qualifier=='around' else 'fn(x){seen=append(seen,1);null}'
            source=prefix+'f=generic_method(f,["Number"],'+callback+',"'+qualifier+'");'
            if not missing:source+='f=generic_method(f,["Rational"],'+callback+',"'+qualifier+'");'
            source+='let code=try{f(1)}catch e{e.code};[code,seen]'
            with self.subTest(missing=missing,qualifier=qualifier):
                self.assertEqual(self.code(source),['no_applicable_method' if missing else 'ambiguous_method',[]])

    def test_primary_async_and_generators_stay_cold_and_after_is_call_layer(self):
        self.assertEqual(self.code('''var seen=[];var f=generic_method(generic_function("f"),["Int"],async fn(x){seen=append(seen,"body");x});
        f=generic_method(f,["Int"],fn(x){seen=append(seen,"after");null},"after");let t=f(42);let before=seen;
        [type_of(t),task_info(t).state,before,await t,seen]'''),['Task','created',['after'],42,['after','body']])
        for flavor,consumer in [('fn*','collect'),('async fn*','await acollect')]:
            with self.subTest(flavor=flavor):
                self.assertEqual(self.code('var seen=0;let f=generic_method(generic_function("f"),["Int"],'+flavor+'(x){seen=seen+1;yield x});let g=f(42);let before=seen;[before,'+consumer+'(g),seen]'),[0,[42],1])

    def test_around_async_suspends_in_original_scheduler(self):
        self.assertEqual(self.code('''let p=generic_method(generic_function("f"),["Int"],async fn(x){await sleep(0);x});
        let f=generic_method(p,["Int"],async fn(next,x){await next()},"around");await gather([f(21),f(42)])'''),[21,42])

    def test_known_async_auxiliaries_rejected_and_unknown_cold_result_preserved(self):
        for impl in ['async fn(x){null}','fn*(x){yield null}','async fn*(x){yield null}',
                     'do{class A{async fn __call__(self,x){null}};A()}']:
            with self.subTest(impl=impl):
                self.assertEqual(self.code('let f=generic_function("f");try{generic_method(f,["Int"],'+impl+',"before")}catch e{e.code}'),'argument_error')
        self.assertEqual(self.code('''var seen=0;async fn cold(){seen=seen+1;42};var f=generic_method(generic_function("f"),["Int"],fn(x){seen=100;0});
        f=generic_method(f,["Int"],fn(x){cold()},"before");try{f(0)}catch e{let state=task_info(e.value).state;cancel(e.value);[e.code,state,seen]}'''),['generic_protocol_error','created',0])

    def test_exception_and_resource_cleanup_follow_existing_stack(self):
        self.assertEqual(self.code('''var seen=[];var f=generic_method(generic_function("f"),["Int"],fn(x){throw ValueError("bad")});
        f=generic_method(f,["Int"],fn(x){seen=append(seen,"after");null},"after");
        f=generic_method(f,["Int"],fn(next,x){try{next()}finally{seen=append(seen,"finally")}},"around");
        try{f(1)}catch ValueError::{message:m}{[m,seen]}'''),['bad',['finally']])

    def test_projection_and_introspection_retain_empty_authority(self):
        for expression in ['generic_method(g,["String"],fn(x){0})','generic_remove(g,["String"])','g',
                           'generic_methods(g)[0].implementation']:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            source='fn secret(x){tool("secret",x)};let g=generic_method(generic_function("f"),["Int"],secret);let escaped=limited(fn(){'+expression+'});try{escaped(1)}catch e{e.code}'
            with self.subTest(expression=expression):
                self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_escaped_next_cannot_restore_captured_authority(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''var g=generic_method(generic_function("f"),["Int"],fn(x){tool("secret",x)});
        g=generic_method(g,["Int"],fn(next,x){next},"around");let escaped=limited(fn(){g(1)});try{escaped()}catch e{e.code}'''),'capability_error')
        self.assertEqual(calls,[])

    def test_narrowed_around_cannot_store_a_wider_next_argument(self):
        for implementation in ['limited(fn(){fn(next,x){saved=next;0}})',
            'do{let callback=limited(fn(){fn(self,next,x){saved=next;0}});let C=new_class("C",[],{__call__:class_member(callback,method=true)});C()}',
            'do{let callback=limited(fn(){fn(next,x){saved=next;0}});class D{fn __get__(self,obj,owner){callback}};class C{let __call__=D()};C()}']:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            source='var saved=null;let p=generic_method(generic_function("g"),["Int"],fn(x){tool("secret",x)});let a='+implementation+';let g=generic_method(p,["Int"],a,"around");g(42);try{saved()}catch e{e.code}'
            with self.subTest(implementation=implementation):
                self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_primary_and_around_tail_redispatch_use_existing_trampoline(self):
        for around in ['', 'f=generic_method(f,["Int","Int"],fn(next,n,total){next()},"around");']:
            with self.subTest(around=bool(around)):
                self.assertEqual(self.code('var f=generic_function("count",2);f=generic_method(f,["Int","Int"],fn(n,total){if n==0{total}else{f(n-1,total+1)}});'+around+'f(1500,0)',step_limit=500000),1500)

    def test_opaque_callable_class_under_narrow_invocation_is_not_projected(self):
        ev=Evaluator();ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('class C{var n=0;fn __init__(self,n){self.n=n;null}};let g=generic_method(generic_function("g"),["Int"],C);limited(fn(){g(42).n})'),42)


    def test_nominal_selector_reflection_rejects_wider_class(self):
        ev=Evaluator();ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''class C{};let f=generic_method(generic_function("f"),[C],fn(x){42});
        limited(fn(){try{generic_methods(f)}catch e{e.code}})'''),'capability_error')

    def test_owned_generic_and_selector_and_implementation(self):
        other=Evaluator();foreign=other.run('generic_function("foreign")')
        ev=Evaluator();ev.env.bind('foreign',foreign)
        for expression in ['generic_info(foreign)','generic_methods(foreign)','generic_method(foreign,["Int"],fn(x){x})','foreign(1)']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):ev.run(expression)
        cls=Evaluator().run('class C{};C');impl=Evaluator().run('fn(x){x}')
        for value in (cls,impl):
            ev=Evaluator();ev.env.bind('foreign',value)
            expression='generic_method(generic_function("g"),[foreign],fn(x){x})' if value is cls else 'generic_method(generic_function("g"),["Int"],foreign)'
            with self.assertRaises(LanguageError) as caught:ev.run(expression)
            self.assertEqual(caught.exception.code,'ownership_error')

    def test_mutable_callable_protocols_not_cached_and_introspection_is_pure(self):
        self.assertEqual(self.code('''var seen=0;class D{fn __get__(self,obj,owner){seen=seen+1;fn(x){42}}}
        class Work{var __call__=fn(x){x+1}};let worker=Work();let f=generic_method(generic_function("f"),["Int"],worker);
        let first=f(1);Work.__call__=fn(x){x+40};let second=f(2);let metadata=generic_methods(f);[first,second,seen,metadata[0].implementation==worker]'''),[2,42,0,True])

    def test_session_reuse_and_method_table_limit(self):
        session=Session();session.evaluate('let f=generic_method(generic_function("f"),["Int"],fn(x){x+1})')
        self.assertEqual(session.evaluate('f(41)'),42)
        with self.assertRaises(ResourceLimit):
            self.code('var f=generic_function("f");for n in range(129){let C=new_class("C"+str(n),[],{});f=generic_method(f,[C],fn(x){0})}',step_limit=500000)

    def test_api_validation_cannot_interpret_names_as_source(self):
        for expression in ['generic_function(1)','generic_function("f",0)','generic_function("f",true)',
                           'generic_function("f",33)','generic_method(generic_function("f"),["Missing"],fn(x){x})',
                           'generic_method(generic_function("f"),["Int","Int"],fn(x){x})',
                           'generic_method(generic_function("f"),["Int"],42)',
                           'generic_method(generic_function("f"),["Int"],fn(x){x},"other")',
                           'generic_info(fn(x){x})','generic_methods({name:"f",arity:1,methods:[]})']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):self.code(expression)
        self.assertEqual(self.code('let f=generic_method(generic_function("f"),["Int"],fn(x){x});try{f(x=42)}catch e{e.code}'),'argument_error')

    def test_real_agent_tool_and_team_role_use_generic_callable(self):
        from chaos import Framework, Registry, ProviderSpec, ToolCall, Final, Settings
        registry=Registry();registry.register_provider(ProviderSpec('choose',lambda request,context:
            ToolCall('work',request.input) if not request.history else Final(request.history[-1]['output'])))
        source='''let work=generic_method(generic_function("work"),["Int"],async fn(x){await sleep(0);x+1});
        agent a{provider:"choose",tools:[]};await agent_bind(a,[agent_tool("work",work)])(41)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('generic',source=source)
            self.assertEqual(app.start('generic',1,None,mode='source').result(10),42)
        definition={'kind':'team','name':'dispatch','entry':'worker','roles':[
            {'id':'worker','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'worker','config':{'provider':'local','tools':[]}}}]}
        self.assertEqual(Evaluator().run('''let work=generic_method(generic_function("work"),["Int"],async fn(x){x*2});
        let t=team_bind(team_from(input),{worker:work});await t(21)''',{'input':definition}),42)


if __name__ == '__main__': unittest.main()
