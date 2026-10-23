"""Ordinary attribute customization shares native descriptors and invocation boundaries."""
import unittest
from chaos.language import Evaluator, LanguageError, Native, TailCall, ResourceLimit, ExecutionControl, serializable
from chaos.session import Session


class AttributeProtocolTests(unittest.TestCase):
    def code(self,source,**options):return Evaluator(**options).run(source)

    def test_read_fallback_defaults_and_hasattr(self):
        self.assertEqual(self.code('''var calls=[];class C{var x=41;
        fn __getattribute__(s,n){calls=append(calls,n);if n=="answer"{42}else{default_getattr(s,n)}}
        fn __getattr__(s,n){if n=="missing"{99}else{throw AttributeError(n)}}}
        let c=C();[c.x,c.answer,c.missing,try{getattr(c,"absent")}catch e{12},hasattr(c,"missing"),hasattr(c,"absent"),default_getattr(c,"x"),calls]'''),
        [41,42,99,12,True,False,41,['x','answer','missing','absent','missing','absent']])

    def test_non_attribute_errors_do_not_fallback(self):
        for failure in ['ValueError("bad")','TypeError("bad")','RuntimeError("bad")']:
            with self.subTest(failure=failure):
                self.assertEqual(self.code('var calls=0;class C{fn __getattribute__(s,n){throw '+failure+'}fn __getattr__(s,n){calls=calls+1;42}};try{C().x}catch e{null};calls'),0)

    def test_descriptor_missing_is_fallback_once(self):
        self.assertEqual(self.code('''var calls=0;class D{fn __get__(s,obj,owner){throw AttributeError("descriptor")}}
        class C{let x=D();fn __getattr__(s,n){calls=calls+1;throw AttributeError("fallback")}}
        let c=C();[try{getattr(c,"x")}catch e{42},calls,try{default_getattr(c,"x")}catch e{e.message}]'''),[42,1,'descriptor'])

    def test_ordinary_and_default_read_preserve_unrestricted_alias(self):
        result=self.code('let items=[42];class C{let x=items;fn __getattr__(s,n){items}}let c=C();[items,c.x,c.missing,default_getattr(c,"x")]')
        self.assertEqual(result,[[42]]*4)
        for value in result[1:]:self.assertIs(value,result[0])

    def test_hook_presence_and_raw_class_mro(self):
        self.assertEqual(self.code('''class A{var __getattribute__=fn(n){42}}class B(A){}let b=B();
        default_setattr(b,"__getattribute__",fn(n){99});b.anything'''),42)
        for declaration in ['let __getattribute__=null','let __getattr__=42']:
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError):self.code('class C{'+declaration+'};C().missing')

    def test_c3_and_super_bypass_interception(self):
        self.assertEqual(self.code('''class A{let x=1;fn __getattribute__(s,n){10}}class B(A){let x=2}
        class D(A){fn __getattribute__(s,n){30}}class C(B,D){}let c=C();[c.x,default_getattr(c,"x"),super(B,c).x]'''),[30,2,1])

    def test_write_delete_return_and_default_contracts(self):
        self.assertEqual(self.code('''var log=[];class C{var x:Int=1;
        fn __setattr__(s,n,v){log=append(log,[n,v]);default_setattr(s,n,v+1);null}
        fn __delattr__(s,n){log=append(log,n);default_delattr(s,n);null}}
        let c=C();let assigned=setattr(c,"x",40);let before=c.x;let deleted=delattr(c,"x");
        [assigned,before,deleted,c.x,log,try{default_setattr(c,"x","bad")}catch e{e.code}]'''),[40,41,None,1,[['x',40],'x'],'runtime_error'])

    def test_augmented_assignment_uses_read_and_write_hooks(self):
        self.assertEqual(self.code('''var log=[];class C{var x=40;fn __getattribute__(s,n){log=append(log,"get");default_getattr(s,n)}
        fn __setattr__(s,n,v){log=append(log,"set");default_setattr(s,n,v);null}}let c=C();c.x+=2;[default_getattr(c,"x"),log]'''),[42,['get','set']])

    def test_write_delete_require_sync_null(self):
        for hook,params,operation in [('__setattr__','s,n,v','c.x=42'),('__delattr__','s,n','delattr(c,"x")')]:
            for flavor,body in [('async fn','null'),('fn*','yield 1'),('async fn*','yield 1'),('fn','42'),('fn','(async fn(){null})()')]:
                session=Session();session.evaluate('var calls=0;class C{'+flavor+' '+hook+'('+params+'){calls=calls+1;'+body+'}}let c=C()')
                with self.subTest(hook=hook,flavor=flavor),self.assertRaises(LanguageError):session.evaluate(operation)
                if flavor!='fn':self.assertEqual(session.evaluate('calls'),0)

    def test_callable_async_setter_does_not_start(self):
        session=Session();session.evaluate('var calls=0;class F{async fn __call__(s,*args){calls=calls+1;null}}class C{let __setattr__=F()}let c=C()')
        with self.assertRaises(LanguageError):session.evaluate('c.x=1')
        self.assertEqual(session.evaluate('calls'),0)

    def test_actual_metaclass_all_five_hooks(self):
        self.assertEqual(self.code('''var log=[];class M(Type){
        fn __getattribute__(cls,n){if n=="answer"{42}else{default_getattr(cls,n)}}
        fn __getattr__(cls,n){"fallback"}
        fn __setattr__(cls,n,v){log=append(log,"set");default_setattr(cls,n,v);null}
        fn __delattr__(cls,n){log=append(log,"del");default_delattr(cls,n);null}
        fn* __dir__(cls){yield "z";yield "a"}}
        class C(metaclass=M){var x=0}C.x=41;let first=C.x;delattr(C,"x");
        [C.answer,C.missing,first,dir(C),log,contains(default_dir(C),"answer")]'''),[42,'fallback',41,['a','z'],['set','del'],False])

    def test_read_cold_handles_are_not_implicitly_awaited(self):
        self.assertEqual(self.code('''var calls=0;var fallbacks=0;class C{
        async fn __getattribute__(s,n){calls=calls+1;throw AttributeError("late")}
        fn __getattr__(s,n){fallbacks=fallbacks+1;42}}let t=C().x;let before=calls;
        [before,task_info(t).state,try{await t}catch e{e.code},calls,fallbacks]'''),[0,'created','attribute_error',1,0])
        self.assertEqual(self.code('''class C{fn* __getattr__(s,n){yield n}}collect(C().hello)'''),['hello'])

    def test_directory_factory_and_direct_generator_keep_duplicates(self):
        for declaration in ['fn* __dir__(s){yield "z";yield "a";yield "a"}',
                            'fn __dir__(s){(fn*(){yield "z";yield "a";yield "a"})()}',
                            'fn __dir__(s){["z","a","a"]}']:
            with self.subTest(declaration=declaration):self.assertEqual(self.code('class C{'+declaration+'};dir(C())'),['a','a','z'])

    def test_directory_async_flavors_are_cold_rejected(self):
        for flavor,body in [('async fn','["x"]'),('async fn*','yield "x"')]:
            session=Session();session.evaluate('var calls=0;class C{'+flavor+' __dir__(s){calls=calls+1;'+body+'}}let c=C()')
            with self.subTest(flavor=flavor),self.assertRaises(LanguageError):session.evaluate('dir(c)')
            self.assertEqual(session.evaluate('calls'),0)

    def test_directory_rejects_wrong_items_and_releases_borrowed_iterator(self):
        self.assertEqual(self.code('''let it=iter(["a",1,"z"]);class C{fn __dir__(s){it}};
        [try{dir(C())}catch e{e.code},next(it),next(it)]'''),['argument_error',{'done':False,'value':'z'},{'done':True,'value':None}])

    def test_directory_attempt_and_text_budgets(self):
        for source in ['class C{fn* __dir__(s){while true{yield "x"}}};dir(C())',
                       'class C{fn __dir__(s){[text,text]}};dir(C())']:
            with self.subTest(source=source),self.assertRaises(ResourceLimit):Evaluator(step_limit=1000000).run(source,{'text':'x'*130000})

    def test_pure_reflection_patterns_and_serialization_bypass(self):
        self.assertEqual(serializable(self.code('''var calls=0;class C{var x=42;fn __getattribute__(s,n){calls=calls+1;throw ValueError("unexpected")}
        fn __dir__(s){calls=calls+1;[]}}let c=C();[vars(c),contains(default_dir(c),"x"),c,match c{C::{x:x}=>x;_=>0},calls]''')),[{},True,{'$instance':'C'},42,0])

    def test_special_operator_and_subscription_lookup_bypass_hooks(self):
        self.assertEqual(self.code('''class C{fn __getattribute__(s,n){throw ValueError("ordinary")};fn __add__(s,x){42};fn __getitem__(s,k){41};fn __hash__(s){1}}
        let c=C();[c+1,c[0],hash(c),try{c.__add__}catch e{e.code}]'''),[42,41,1,'value_error'])

    def test_implicit_new_init_and_class_subscription_bypass(self):
        self.assertEqual(self.code('''class M(Type){fn __getattribute__(cls,n){throw ValueError("meta")}}
        class C(metaclass=M){var x=0;fn __getattribute__(s,n){throw ValueError("instance")}
        fn __init__(s,x){default_setattr(s,"x",x);null};@classmethod
        fn __class_getitem__(cls,k){k+1}}
        let c=C(41);[default_getattr(c,"x"),C[41]]'''),[41,42])

    def test_foreign_and_stale_default_results_fail_ownership(self):
        foreign=Evaluator().run('class C{};C()')
        for expression in ['getattr(foreign,"x")','default_getattr(foreign,"x")','dir(foreign)','default_dir(foreign)','setattr(foreign,"x",1)']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:Evaluator().run(expression,{'foreign':foreign})
            self.assertEqual(caught.exception.code,'ownership_error')
        ev=Evaluator();value=ev.run('class C{let x=[(async fn(){42})()]};C()')
        with self.assertRaises(LanguageError) as caught:ev.run('default_getattr(value,"x")',{'value':value})
        self.assertEqual(caught.exception.code,'runtime_error')

    def test_exact_attribute_name_rejects_host_subclass_without_hooks(self):
        class Sneaky(str):
            def __str__(self):raise AssertionError('host hook')
        for expression in ['getattr(value,label)','setattr(value,label,1)','default_getattr(value,label)','delattr(value,label)']:
            ev=Evaluator();value=ev.run('class C{};C()')
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:ev.run(expression,{'value':value,'label':Sneaky('x')})
            self.assertEqual(caught.exception.code,'runtime_error')

    def test_nested_hook_result_keeps_actual_callback_ceiling(self):
        for declaration in ['__getattribute__:class_member(getter,method=true)','x:property(getter)']:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda fn:TailCall(fn,[]),ev,capabilities=frozenset()))
            params='s,n' if '__getattribute__' in declaration else 's'
            source='let f=fn(){tool("secret",42)};let getter=limited(fn(){fn('+params+'){[f]}});let C=new_class("C",[],{'+declaration+'});let got=C().x;try{got[0]()}catch e{e.code}'
            with self.subTest(declaration=declaration):self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_default_nested_result_keeps_caller_ceiling(self):
        ev=Evaluator(tool_handler=lambda *args:42);ev.env.bind('limited',Native('limited',lambda fn:TailCall(fn,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('let f=fn(){tool("secret",42)};class C{let x={f:f}}let c=C();let got=limited(fn(){default_getattr(c,"x")});try{got.f()}catch e{e.code}'),'capability_error')

    def test_intrinsic_classes_cannot_be_modified(self):
        for expression in ['setattr(Type,"x",1)','default_setattr(Type,"x",1)','delattr(Type,"x")','default_delattr(Type,"x")']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:self.code(expression)
            self.assertEqual(caught.exception.code,'attribute_error')

    def test_recursive_hook_budget_and_cleanup(self):
        ev=Evaluator(step_limit=10000)
        with self.assertRaises(ResourceLimit):ev.run('class C{fn __getattribute__(s,n){s.x}};C().x')
        self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)

    def test_fallback_causal_context_is_scoped_not_active_catch(self):
        self.assertEqual(self.code('''class C{fn __getattribute__(s,n){throw AttributeError("first")}
        fn __getattr__(s,n){throw ValueError("second")}};
        let result=try{C().x}catch e{[e.code,current_error().context.code]};
        let unrelated=try{throw ValueError("outside")}catch e{current_error().context};[result,unrelated]'''),[['value_error','attribute_error'],None])
        self.assertEqual(self.code('''class C{fn __getattr__(s,n){current_error()}};C().missing'''),None)

    def test_implicit_prepare_and_mro_entries_use_defaults(self):
        self.assertEqual(self.code('''class MM(Type){fn __getattribute__(s,n){throw ValueError("intercept")}}
        class M(Type,metaclass=MM){@classmethod
        fn __prepare__(cls,n,bases){ {x:class_member(42)} }}class C(metaclass=M){};C().x'''),42)
        self.assertEqual(self.code('''class Base{let x=42}class Adapter{fn __getattribute__(s,n){throw ValueError("intercept")}
        fn __mro_entries__(s,bases){(Base,)}}class C(Adapter()){};C().x'''),42)

    def test_directory_generator_error_restores_reentry_lock(self):
        self.assertEqual(self.code('''fn* names(){yield "a";yield 1;yield "z"}let g=names();
        class C{fn __dir__(s){g}};let failure=try{dir(C())}catch e{e.code};[failure,next(g)]'''),['argument_error',{'done':False,'value':'z'}])

    def test_suspended_attribute_and_directory_values_seal_next_authority(self):
        bodies=['''class C{fn __getattr__(s,n){[remember(g(42))]}}
        async fn outer(){[C().missing,do{await sleep(0.02);0}]}let pending=outer();
        spawn(pending);await sleep(0.002);sink(pending);await pending;42''',
        '''class I{var token=null;var done=false;fn __iter__(s){s}
        fn __next__(s){if s.done{{done:true,value:null}}else{s.done=true;sink(s);{done:false,value:"x"}}}}
        class C{fn __dir__(s){let i=I();i.token=remember(g(42));i}}dir(C());42''']
        for body in bodies:
            calls=[];tokens=[];ev=Evaluator(step_limit=1000000,tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            ev.env.bind('remember',Native('remember',lambda value:tokens.append(value) or value,ev))
            prefix='''let base=generic_method(generic_function("g"),["Any"],fn(x){tool("secret",x)});
            let g=generic_method(base,["Int"],fn(next,x){next},with_next=true);
            let sink=limited(fn(){fn(item){0}});'''
            with self.subTest(body=body):
                self.assertEqual(ev.run(prefix+body),42);self.assertEqual(len(tokens),1)
                with self.assertRaises(LanguageError) as caught:ev.invoke(tokens[0],[])
                self.assertEqual(caught.exception.code,'capability_error');self.assertEqual(calls,[])

    def test_hard_controls_are_not_fallback(self):
        for failure in [ResourceLimit('test'),ExecutionControl('test')]:
            ev=Evaluator();ev.env.bind('halt',Native('halt',lambda:(_ for _ in ()).throw(failure),ev))
            with self.subTest(failure=type(failure).__name__),self.assertRaises(type(failure)):ev.run('class C{fn __getattribute__(s,n){halt()}fn __getattr__(s,n){42}};C().x')
            self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)


if __name__=='__main__':unittest.main()
