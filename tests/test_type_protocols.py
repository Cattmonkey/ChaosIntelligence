"""Native metaclass predicates and one-layer base adaptation share the AST runner."""
import unittest
from chaos.language import Evaluator, LanguageError, ResourceLimit, Native, TailCall


class TypeProtocolTests(unittest.TestCase):
    def code(self, source, **options): return Evaluator(**options).run(source)

    def test_default_and_explicit_nominal_fallback(self):
        self.assertEqual(self.code('class A{}class B(A){}[isinstance(B(),A),issubclass(B,A),Type.__instancecheck__(A,B()),Type.__subclasscheck__(A,B),isinstance(42,A),issubclass(A,B)]'), [True]*4+[False]*2)

    def test_public_hooks_dispatch_even_for_exact_instance_and_class(self):
        self.assertEqual(self.code('var seen=[];class M(Type){fn __instancecheck__(c,v){seen=append(seen,"i");false}fn __subclasscheck__(c,v){seen=append(seen,"s");false}}class C(metaclass=M){}[isinstance(C(),C),issubclass(C,C),seen]'),[False,False,['i','s']])

    def test_selected_metaclass_mro_ignores_target_class_shadow(self):
        self.assertEqual(self.code('class M(Type){fn __instancecheck__(c,v){v==42}fn __subclasscheck__(c,v){v==c}}class N(M){}class C(metaclass=N){fn __instancecheck__(s,v){false}fn __subclasscheck__(s,v){false}}[isinstance(42,C),issubclass(C,C)]'),[True,True])

    def test_property_and_custom_descriptor_receive_actual_class_and_meta(self):
        self.assertEqual(self.code('class D{fn __get__(s,c,m){fn(v){c.marker==42&&class_of(c)==m&&v==42}}}class M(Type){let __instancecheck__=D();@property\nfn __subclasscheck__(c){fn(v){v==c}}}class C(metaclass=M){let marker=42}[isinstance(42,C),issubclass(C,C)]'),[True,True])

    def test_static_classmethod_and_cooperative_super_binding(self):
        self.assertEqual(self.code('class A(Type){fn __instancecheck__(c,v){super(A,c).__instancecheck__(v)||v==42}}class M(A){@classmethod\nfn __subclasscheck__(m,v){m==M}}class C(metaclass=M){}[isinstance(C(),C),isinstance(42,C),issubclass(C,C)]'),[True]*3)
        self.assertTrue(self.code('class M(Type){@staticmethod\nfn __instancecheck__(v){v==42}}class C(metaclass=M){}isinstance(42,C)'))

    def test_virtual_checks_do_not_change_contracts_patterns_or_super(self):
        source='class M(Type){fn __instancecheck__(c,v){true}fn __subclasscheck__(c,v){true}}class C(metaclass=M){}class Other{}let o=Other();[isinstance(o,C),issubclass(Other,C),match o{C::{}=>true,_=>false},try{let x:C=o;0}catch e{e.code},try{super(C,o);0}catch e{e.code},class_of(o)==Other,Type(o)==Other]'
        self.assertEqual(self.code(source),[True,True,False,'runtime_error','argument_error',True,True])

    def test_strict_bool_rejects_truthy_values_and_notimplemented(self):
        for name,args,call in [('__instancecheck__','c,v','isinstance(42,C)'),('__subclasscheck__','c,v','issubclass(C,C)')]:
            for result in ['1','null','[]','{}','NotImplemented']:
                with self.subTest(name=name,result=result),self.assertRaises(LanguageError): self.code('class M(Type){fn '+name+'('+args+'){'+result+'}}class C(metaclass=M){}'+call)

    def test_cold_async_and_generator_hooks_are_never_started(self):
        for name,call in [('__instancecheck__','isinstance(42,C)'),('__subclasscheck__','issubclass(C,C)'),('__mro_entries__','new_class("C",[M()])')]:
            for kind in ['async fn','fn*','async fn*']:
                seen=[];prefix='class M'+('(Type)'if name!='__mro_entries__'else '')
                body=('yield true;'if '*'in kind else '')+'true'
                with self.subTest(name=name,kind=kind),self.assertRaises(LanguageError):self.code(prefix+'{'+kind+' '+name+'(s,v){tool("bad",1);'+body+'}}'+('class C(metaclass=M){}'if name!='__mro_entries__'else '')+call,tool_handler=lambda n,v:seen.append(n))
                self.assertEqual(seen,[])

    def test_sync_function_returning_cold_task_rejected_without_await(self):
        seen=[]
        with self.assertRaises(LanguageError):self.code('async fn work(){tool("bad",1);true}class M(Type){fn __instancecheck__(c,v){work()}}class C(metaclass=M){}isinstance(1,C)',tool_handler=lambda n,v:seen.append(n))
        self.assertEqual(seen,[])

    def test_hook_errors_propagate_and_budget_is_shared(self):
        with self.assertRaises(LanguageError)as error:self.code('class M(Type){fn __instancecheck__(c,v){throw "predicate"}}class C(metaclass=M){}isinstance(1,C)')
        self.assertEqual(error.exception.value,'predicate')
        with self.assertRaises(ResourceLimit):self.code('class M(Type){fn __instancecheck__(c,v){isinstance(v,c)}}class C(metaclass=M){}isinstance(1,C)',step_limit=3000)

    def test_hook_authority_intersects_caller_and_target(self):
        seen=[];ev=Evaluator(tool_handler=lambda n,v:seen.append(n)or True)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        result=ev.run('class M(Type){fn __instancecheck__(c,v){tool("secret",v)}}class C(metaclass=M){}limited(fn(){try{isinstance(42,C)}catch e{e.code}})',{'limited':limited})
        self.assertEqual(result,'capability_error');self.assertEqual(seen,[])

    def test_foreign_class_or_value_is_rejected_before_hook(self):
        foreign=self.code('class C{}C')
        for source in ['isinstance(1,input)','class C{}isinstance(input,C)','class C{}issubclass(input,C)']:
            with self.subTest(source=source),self.assertRaises(LanguageError)as error:Evaluator().run(source,{'input':foreign})
            self.assertEqual(error.exception.code,'ownership_error')

    def test_public_and_default_subclass_require_real_classes(self):
        for call in ['isinstance(1,42)','issubclass(42,C)','issubclass(C,42)','Type.__subclasscheck__(C,42)']:
            with self.subTest(call=call),self.assertRaises(LanguageError):self.code('class C{}'+call)

    def test_base_adapter_source_and_new_class_share_pipeline(self):
        source='class Base{let answer=42}class Adapter{fn __mro_entries__(s,b){(Base,)}}let a=Adapter();class C(a){}let D=new_class("D",[a]);[C.answer,D.answer,C.__orig_bases__==(a,),D.__orig_bases__==(a,),issubclass(C,Base)]'
        self.assertEqual(self.code(source),[42,42,True,True,True])

    def test_adapters_receive_same_original_tuple_once_in_left_order(self):
        source='var seen=[];class A{}class B{}class Proxy{var target=null;fn __init__(s,t){s.target=t;null}fn __mro_entries__(s,original){seen=append(seen,[s.target,original]);(s.target,)}}let a=Proxy(A);let b=Proxy(B);class C(a,b){}[length(seen),seen[0][0]==A,seen[1][0]==B,seen[0][1]==(a,b),seen[1][1]==(a,b),C.__orig_bases__==(a,b)]'
        self.assertEqual(self.code(source),[2,True,True,True,True,True])

    def test_empty_and_multiple_entries_are_supported(self):
        self.assertEqual(self.code('class A{let a=20}class B{let b=22}class Remove{fn __mro_entries__(s,b){()}}class Expand{fn __mro_entries__(s,b){(A,B)}}class C(Remove(),Expand()){}[C.a+C.b,length(C.__orig_bases__)]'),[42,2])

    def test_class_bases_never_invoke_adapter_method_and_have_no_orig_metadata(self):
        seen=[];self.assertEqual(self.code('class A{fn __mro_entries__(s,b){tool("bad",1);()}}class B(A){}[issubclass(B,A),hasattr(B,"__orig_bases__")]',tool_handler=lambda n,v:seen.append(n)),[True,False]);self.assertEqual(seen,[])

    def test_instance_field_and_descriptor_adapter_follow_normal_member_access(self):
        self.assertTrue(self.code('class A{}class Proxy{}let p=Proxy();p.__mro_entries__=fn(b){(A,)};class C(p){}issubclass(C,A)'))
        self.assertTrue(self.code('class A{}class D{fn __get__(s,p,c){fn(b){(A,)}}}class Proxy{let __mro_entries__=D()}class C(Proxy()){}issubclass(C,A)'))

    def test_adapter_result_requires_one_layer_exact_class_tuple(self):
        for result in ['[A]','A','(42,)','(s,)','null']:
            with self.subTest(result=result),self.assertRaises(LanguageError):self.code('class A{}class Proxy{fn __mro_entries__(s,b){'+result+'}}class C(Proxy()){}')

    def test_final_duplicates_c3_conflicts_and_expansion_limit_are_rejected(self):
        for source in ['class A{}class P{fn __mro_entries__(s,b){(A,A)}}class C(P()){}','class A{}class B{}class X(A,B){}class Y(B,A){}class P{fn __mro_entries__(s,b){(X,Y)}}class C(P()){}','class A{}class P{fn __mro_entries__(s,b){tuple([A for n in range(33)])}}class C(P()){}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_original_invalid_and_duplicate_bases_fail_before_options_or_descriptors(self):
        for bases in ['A,A','42','P(),42']:
            seen=[]
            with self.subTest(bases=bases),self.assertRaises(LanguageError):self.code('class A{}class D{fn __get__(s,p,c){tool("descriptor",1);fn(b){()}}}class P{let __mro_entries__=D()}class C('+bases+',tag=tool("options",1)){let x=tool("body",1)}',tool_handler=lambda n,v:seen.append(n))
            self.assertEqual(seen,[])

    def test_adapter_resolution_and_c3_precede_header_options_and_prepare(self):
        seen=[];source='class A{fn __init_subclass__(c,**opts){null}}class P{fn __mro_entries__(s,b){tool("resolve",1);(A,)}}class M(Type){fn __prepare__(m,n,b,**opts){tool("prepare",1);{}}}class C(P(),tag=tool("options",1),metaclass=M){let x=tool("body",1)}C.x'
        self.assertEqual(self.code(source,tool_handler=lambda n,v:seen.append(n)or 42),42);self.assertEqual(seen,['resolve','options','prepare','body'])

    def test_resolved_bases_choose_most_derived_metaclass(self):
        self.assertEqual(self.code('class M(Type){}class N(M){}class A(metaclass=M){}class B(metaclass=N){}class P{fn __mro_entries__(s,b){(A,B)}}class C(P()){}class_of(C)==N'),True)

    def test_orig_bases_metadata_is_postbody_and_visible_to_meta_and_factory(self):
        self.assertEqual(self.code('class A{}class P{fn __mro_entries__(s,b){(A,)}}let p=P();class M(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,set(ns,"seen",ns.__orig_bases__==(p,)))}}class C(p,metaclass=M){let __orig_bases__=42}fn factory(n,b,ns){ns.__orig_bases__==(p,)}let made=new_class("Made",[p],{__orig_bases__:42},{metaclass:factory});[C.seen,C.__orig_bases__==(p,),made]'),[True]*3)

    def test_custom_mapping_receives_orig_metadata_after_declared_members(self):
        source='var writes=[];class A{}class P{fn __mro_entries__(s,b){(A,)}}class NS{var items={};fn keys(s){keys(s.items)}fn __getitem__(s,k){s.items[k]}fn __setitem__(s,k,v){writes=append(writes,k);s.items=set(s.items,k,v);null}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(P(),metaclass=M){let x=42}[writes,C.x,type_of(C.__orig_bases__)]'
        self.assertEqual(self.code(source),[['x','__orig_bases__'],42,'Tuple'])

    def test_lowlevel_type_remains_strict_and_never_resolves(self):
        for call in ['Type("C",[p],{})','Type.__new__(Type,"C",[p],{})']:
            seen=[]
            with self.subTest(call=call),self.assertRaises(LanguageError):self.code('class A{}class P{fn __mro_entries__(s,b){tool("bad",1);(A,)}}let p=P();'+call,tool_handler=lambda n,v:seen.append(n))
            self.assertEqual(seen,[])

    def test_adapter_function_return_cannot_release_wider_class_authority(self):
        seen=[];ev=Evaluator(tool_handler=lambda n,v:seen.append(n)or 42)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        source='class A{fn secret(s){tool("secret",1)}}class P{fn __mro_entries__(s,b){(A,)}}let p=P();limited(fn(){try{new_class("C",[p])}catch e{e.code}})'
        self.assertEqual(ev.run(source,{'limited':limited}),'capability_error');self.assertEqual(seen,[])

    def test_adapted_class_preserves_source_mutable_cell_and_hook_errors(self):
        self.assertEqual(self.code('class A{}class P{fn __mro_entries__(s,b){(A,)}}class C(P()){var x=1;let read=fn(){x}}C.x=42;[C.read(),class_members(C).x.value]'),[42,42])
        with self.assertRaises(LanguageError)as error:self.code('class P{fn __mro_entries__(s,b){throw "adapt"}}class C(P()){}')
        self.assertEqual(error.exception.value,'adapt')

    def test_original_metadata_cannot_export_wider_proxy_from_limited_creation(self):
        for creation in ['new_class("C",[proxy])','do{class C(proxy){};C}']:
            seen=[];ev=Evaluator(tool_handler=lambda n,v:seen.append(n)or 42)
            limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
            source='let Base=limited(fn(){class Safe{};Safe});class Proxy{fn __mro_entries__(s,b){(Base,)}fn secret(s){tool("secret",1)}}let proxy=Proxy();limited(fn(){try{'+creation+'}catch e{e.code}})'
            with self.subTest(creation=creation):self.assertEqual(ev.run(source,{'limited':limited}),'capability_error');self.assertEqual(seen,[])

    def test_original_metadata_rechecks_actual_replacement_class_ceiling(self):
        for creation in ['new_class("C",[proxy],{}, {metaclass:M})','do{class C(proxy,metaclass=M){};C}']:
            ev=Evaluator();limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
            source='let narrow=limited(fn(){class_member(1)});class Base{}class Proxy{fn __mro_entries__(s,b){(Base,)}}let proxy=Proxy();class M(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,set(ns,"narrow",narrow))}}try{'+creation+'}catch e{e.code}'
            with self.subTest(creation=creation):self.assertEqual(ev.run(source,{'limited':limited}),'capability_error')

    def test_synchronous_hooks_cannot_use_explicit_await(self):
        for declaration,tail in [('class M(Type){fn __instancecheck__(c,v){await sleep(0);true}}','class C(metaclass=M){}isinstance(1,C)'),('class P{fn __mro_entries__(s,b){await sleep(0);()}}','class C(P()){}')]:
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError):self.code(declaration+tail)
        self.assertTrue(self.code('async fn produce(){await sleep(0);42}class M(Type){fn __instancecheck__(c,v){v==42}}class C(metaclass=M){}isinstance(await produce(),C)'))

    def test_adapter_rejects_foreign_replacement_and_original_proxy(self):
        foreign=self.code('class F{}F')
        for source in ['class P{fn __mro_entries__(s,b){(input,)}}class C(P()){}','class C(input){}']:
            with self.subTest(source=source),self.assertRaises(LanguageError)as error:Evaluator().run(source,{'input':foreign})
            self.assertEqual(error.exception.code,'ownership_error')

    def test_default_nominal_checks_and_reflection_do_not_execute_custom_hook(self):
        seen=[]
        self.assertEqual(self.code('class M(Type){fn __instancecheck__(c,v){tool("bad",1);false}fn __subclasscheck__(c,v){tool("bad",1);false}}class C(metaclass=M){}let c=C();[Type.__instancecheck__(C,c),Type.__subclasscheck__(C,C),class_of(c)==C,Type(c)==C,match c{C::{}=>true,_=>false}]',tool_handler=lambda n,v:seen.append(n)or True),[True]*5)
        self.assertEqual(seen,[])

    def test_cancellation_of_real_predicate_uses_original_runtime_control(self):
        import threading
        cancelled=threading.Event();ev=Evaluator(cancelled=cancelled.is_set,tool_handler=lambda n,v:cancelled.set()or True)
        with self.assertRaises(ResourceLimit):ev.run('class M(Type){fn __instancecheck__(c,v){tool("cancel",1);true}}class C(metaclass=M){}isinstance(42,C)')
        self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)

    def test_failed_adapter_does_not_publish_source_name(self):
        from chaos.session import Session
        session=Session();session.evaluate('class P{fn __mro_entries__(s,b){throw "fail"}}')
        with self.assertRaises(LanguageError):session.evaluate('class Hidden(P()){}')
        self.assertNotIn('Hidden',session.env.values)

    def test_transformed_original_metadata_saves_narrow_callable_projection(self):
        for creation in ['new_class("C",[alias],{}, {metaclass:Meta})','do{class C(alias,metaclass=Meta){};C}']:
            seen=[];ev=Evaluator(tool_handler=lambda n,v:seen.append(n)or v)
            limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
            source='let narrow=limited(fn(){class_member(1)});fn secret(){tool("secret",42)};class Base{}class Adapter{fn __mro_entries__(s,b){(Base,)}}let alias=Adapter();class Meta(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,set(set(ns,"__orig_bases__",(secret,)),"narrow",narrow))}}let C='+creation+';[type_of(C.__orig_bases__[0]),try{C.__orig_bases__[0]()}catch e{e.code}]'
            with self.subTest(creation=creation):self.assertEqual(ev.run(source,{'limited':limited}),['Function','capability_error']);self.assertEqual(seen,[])

    def test_projected_original_metadata_retains_mutable_cell_contract_and_closure(self):
        seen=[];ev=Evaluator(tool_handler=lambda n,v:seen.append(n)or v)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        source='let narrow=limited(fn(){class_member(1)});fn secret(){tool("secret",42)};class Base{}class Adapter{fn __mro_entries__(s,b){(Base,)}}let alias=Adapter();class Meta(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,set(set(ns,"__orig_bases__",class_member((secret,),mutable=true,contract="Tuple")),"narrow",narrow))}}class C(alias,metaclass=Meta){var __orig_bases__:Tuple=();let read=fn(){__orig_bases__}}[C.read()==C.__orig_bases__,class_members(C).__orig_bases__.mutable,class_members(C).__orig_bases__.contract,try{C.__orig_bases__[0]()}catch e{e.code},try{C.__orig_bases__=42}catch e{e.code}]'
        self.assertEqual(ev.run(source,{'limited':limited}),[True,True,'Tuple','capability_error','runtime_error']);self.assertEqual(seen,[])
