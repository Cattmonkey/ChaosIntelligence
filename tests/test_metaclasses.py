"""Native metaclass protocols, ownership, runtime effects and self-programming."""
import threading
import unittest
from chaos.language import Evaluator, LanguageError, ResourceLimit, Native, TailCall
from chaos.session import Session
from chaos.objects import ChaosClass


class MetaclassTests(unittest.TestCase):
    def code(self,source,**options): return Evaluator(**options).run(source)

    def test_bootstrap_identity_nominal_contracts_and_wire(self):
        self.assertEqual(self.code('class M(Type){}class C(metaclass=M){}let c=C();let meta:Type=M;let value:M=C;[class_of(Type)==Type,class_of(M)==Type,class_of(C)==M,class_of(c)==C,Type(C)==M,Type(c)==C,isinstance(C,Type),isinstance(C,M),isinstance(c,M),issubclass(M,Type),type_of(M)]'),[True]*8+[False,True,'Class'])
        from chaos.language import serializable
        self.assertEqual(serializable(self.code('Type')),{'$class':'Type'})
        for source in ['Type(1)','class_of(1)','class C{}let m:Type=C','class M(Type){}class C{}let x:M=C']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_names_are_validated_before_factory_prepare_and_namespace_effects(self):
        for name in ['42','null','""','"Int"','"Type"']:
            for call in ['new_class(NAME,[],{}, {metaclass:factory})','Type.__new__(Type,NAME,[],NS())']:
                calls=[]
                source='fn factory(n,b,ns){tool("factory",1);42}class NS{fn keys(s){tool("keys",1);[]}fn __getitem__(s,k){1}fn __setitem__(s,k,v){null}}'+call.replace('NAME',name)
                with self.subTest(name=name,call=call),self.assertRaises(LanguageError):self.code(source,tool_handler=lambda n,v:calls.append(n)or [])
                self.assertEqual(calls,[])

    def test_lowlevel_type_selection_can_return_replacement_and_lexical_class_name_is_readonly(self):
        self.assertEqual(self.code('class M(Type){fn __new__(m,n,b,ns){if n=="X"{Type.__new__(m,n,b,ns)}else{42}}}class X(metaclass=M){}[Type("D",[X],{}),Type.__new__(Type,"E",[X],{})]'),[42,42])
        self.assertEqual(self.code('class C{fn replace(s){C=42}fn own(s){C}}let c=C();[try{c.replace()}catch e{e.code},c.own()==C]'),['runtime_error',True])
        with self.assertRaises(LanguageError):self.code('class C{let x=do{C=42;1}}')

    def test_custom_mapping_keys_obey_aggregate_string_budget(self):
        calls=[];ev=Evaluator(tool_handler=lambda n,v:calls.append(n)or 1)
        supply=Native('supply',lambda:['x'*150000,'y'*150000],ev)
        source='class NS{fn keys(s){supply()}fn __getitem__(s,k){tool("read",1);1}fn __setitem__(s,k,v){null}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){}'
        with self.assertRaises(ResourceLimit):ev.run(source,{'supply':supply})
        self.assertEqual(calls,[])

    def test_prepared_seed_is_separate_and_property_updates_require_actual_lineage(self):
        for preparation in ['{}','{x:class_member(property(fn(self){0}))}']:
            prefix='class M(Type){fn __prepare__(m,n,b){'+preparation+'}}'
            for body in ['let x=property(fn(self){1})\n@fn(f){property(f)}\nfn x(self){2}', 'let x=property(fn(self){1})\n@fn(f){x}\nfn x(self){2}']:
                with self.subTest(preparation=preparation,body=body),self.assertRaises(LanguageError):self.code(prefix+'class C(metaclass=M){'+body+'}')
            valid='class C(metaclass=M){var saved=40;@property\nfn x(self){self.saved}\n@x.setter\nfn x(self,v){self.saved=v;null}}let c=C();c.x=42;c.x'
            self.assertEqual(self.code(prefix+valid),42)

    def test_instance_namespace_snapshot_obeys_same_aggregate_values_budget_as_record(self):
        ev=Evaluator();supply=Native('supply',lambda:'x'*150000,ev)
        source='class NS{fn keys(s){["a","b"]}fn __getitem__(s,k){supply()}fn __setitem__(s,k,v){null}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){}'
        with self.assertRaises(ResourceLimit):ev.run(source,{'supply':supply})
        with self.assertRaises(ResourceLimit):Evaluator().run('Type("C",[],{a:supply(),b:supply()})',{'supply':Native('supply',lambda:'x'*150000)})

    def test_most_derived_direct_highlevel_and_explicit_selection(self):
        source='''class A(Type){}class B(A){}class X(metaclass=A){}class Y(metaclass=B){}
        class Z(X,Y,metaclass=A){}let D=Type("D",[X,Y],{});let E=new_class("E",[X,Y]);
        [class_of(Z)==B,class_of(D)==B,class_of(E)==B]'''
        self.assertEqual(self.code(source),[True]*3)
        calls=[]
        with self.assertRaises(LanguageError) as caught:self.code('class A(Type){fn __prepare__(m,n,b){tool("seen",n);{}}}class B(Type){}class X(metaclass=A){}class Y(metaclass=B){}class Z(X,Y){let x=tool("body",1)}',tool_handler=lambda n,x:calls.append(x))
        self.assertEqual(caught.exception.code,'metaclass_conflict');self.assertEqual(calls,['X'])

    def test_explicit_null_and_unknown_metaclass_fail_after_header_before_body(self):
        for value in ['null','42','{}','[]']:
            calls=[]
            with self.subTest(value=value),self.assertRaises(LanguageError):self.code('class C(metaclass='+value+'){let x=tool("body",1)}',tool_handler=lambda n,x:calls.append(n))
            self.assertEqual(calls,[])
        calls=[]
        with self.assertRaises(LanguageError):self.code('class C(**tool("header",1)){let x=tool("body",1)}',tool_handler=lambda n,x:calls.append(n)or {'metaclass':None})
        self.assertEqual(calls,['header'])

    def test_prepare_record_copy_preseed_and_dynamic_creation_share_pipeline(self):
        self.assertEqual(self.code('''let seed={a:class_member(40)};var seen=[];
        class M(Type){fn __prepare__(m,n,b){seen=append(seen,n);seed}fn __new__(m,n,b,ns){Type.__new__(m,n,b,ns)}}
        class C(metaclass=M){let answer=a+2}let D=new_class("D",[],{answer:class_member(42)}, {metaclass:M});
        [C.answer,D.answer,keys(seed),seen,keys(class_members(C))]'''),[42,42,['a'],['C','D'],['a','answer']])

    def test_mapping_receives_metadata_incrementally_and_reads_transformed_value(self):
        self.assertEqual(self.code('''var seen=[];class NS{var values={};var labels=[];
        fn keys(s){s.labels}fn __getitem__(s,k){s.values[k]}fn __setitem__(s,k,v){seen=append(seen,[k,v.method,v.mutable]);let actual=if k=="a"{class_member(v.value+1)}else{v};s.values=set(s.values,k,actual);if !contains(s.labels,k){s.labels=append(s.labels,k)}else{null};null}}
        class M(Type){fn __prepare__(m,n,b){NS()}}
        class C(metaclass=M){let a=40;var b:Int=a+1;fn get(self){self.b}}
        [C().get(),seen,class_members(C).b.contract]'''),[42,[['a',False,False],['b',False,True],['get',True,False]],'Int'])

    def test_mapping_final_order_and_namespace_transformation(self):
        self.assertEqual(self.code('''class M(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,{z:class_member(42),a:ns.a})}}
        class C(metaclass=M){let a=40;let dropped=1}[keys(class_members(C)),C.a,C.z,hasattr(C,"dropped")]'''),[['z','a'],40,42,False])

    def test_prepare_mapping_validation_and_hook_error_not_swallowed(self):
        for body in ['42','[]','{seed:class_member(1)}']:
            if body.startswith('{'):continue
            with self.subTest(body=body),self.assertRaises(LanguageError):self.code('class M(Type){fn __prepare__(m,n,b){'+body+'}}class C(metaclass=M){}')
        for result in ['["a","a"]','[1]','42']:
            with self.subTest(result=result),self.assertRaises(LanguageError):self.code('class NS{fn keys(s){'+result+'}fn __getitem__(s,k){1}fn __setitem__(s,k,v){null}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){}')
        with self.assertRaises(LanguageError)as caught:self.code('class NS{fn keys(s){throw "mapping"}fn __getitem__(s,k){1}fn __setitem__(s,k,v){null}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){}')
        self.assertEqual(caught.exception.value,'mapping')

    def test_mapping_reads_observe_cross_key_write_and_do_not_swallow_user_failure(self):
        source='class NS{var values={x:class_member(1)};fn keys(s){keys(s.values)}fn __getitem__(s,k){s.values[k]}fn __setitem__(s,k,v){s.values=set(s.values,k,v);if k=="y"{s.values=set(s.values,"x",class_member(2))}else{null};null}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){let y=x;let z=x}[C.x,C.y,C.z]'
        self.assertEqual(self.code(source),[2,1,2])
        for update in ['x=2','x+=1']:
            self.assertEqual(self.code('class C{var x=1;let y=do{'+update+';x};let z=x}[C.x,C.y,C.z]'),[2,2,2])
        with self.assertRaises(LanguageError) as caught:
            self.code('class NS{fn keys(s){[]}fn __setitem__(s,k,v){null}fn __getitem__(s,k){throw "read"}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){let v=Type}')
        self.assertEqual(caught.exception.value,'read')

    def test_creation_order_options_consumption_and_exactly_once(self):
        source='''var events=[];class D{fn __set_name__(s,c,n){events=append(events,"set_name");null}}
        class Base{fn __init_subclass__(c,*,tag){events=append(events,["subclass",tag]);null}}
        class M(Type){fn __prepare__(m,n,b,*,tag){events=append(events,"prepare");{}}
        fn __new__(m,n,b,ns,*,tag){events=append(events,"new");Type.__new__(m,n,b,ns,tag=tag)}
        fn __init__(c,n,b,ns,*,tag){events=append(events,"init");null}}
        fn deco(c){events=append(events,"decorator");c}
        @deco
        class C(Base,metaclass=M,tag=42){let d=D()}events'''
        self.assertEqual(self.code(source),['prepare','new','set_name',['subclass',42],'init','decorator'])
        self.assertEqual(self.code('class M(Type){fn __new__(m,n,b,ns,*,consume){Type.__new__(m,n,b,ns)}fn __init__(c,n,b,ns,**options){null}}class C(metaclass=M,consume=42){}class_of(C)==M'),True)

    def test_direct_meta_and_type_new_skip_prepare(self):
        self.assertEqual(self.code('var seen=0;class M(Type){fn __prepare__(m,n,b){seen=seen+1;{}}}let C=M("C",[],{x:class_member(42)});let D=Type.__new__(M,"D",[],{x:class_member(41)});[seen,class_of(C)==M,D.x]'),[0,True,41])

    def test_new_nonclass_result_skips_init_and_actual_replacement_self_reference(self):
        self.assertEqual(self.code('var seen=0;class M(Type){fn __new__(m,n,b,ns){42}fn __init__(c,n,b,ns){seen=seen+1;null}}class C(metaclass=M){}[C,seen]'),[42,0])
        self.assertEqual(self.code('fn factory(n,b,ns){Type(n+"Final",b,ns)}class C(metaclass=factory){fn own(self){C}}let c=C();c.own()==C'),True)

    def test_factory_function_instance_class_static_and_legal_results(self):
        self.assertEqual(self.code('fn factory(n,b,ns){fn(x){x+1}}class C(metaclass=factory){}C(41)'),42)
        self.assertEqual(self.code('class F{fn __prepare__(s,n,b){ {seed:class_member(40)} }fn __call__(s,n,b,ns){Type(n,b,ns)}}let f=F();class C(metaclass=f){let a=seed+2}C.a'),42)
        self.assertEqual(self.code('class F{fn __new__(m,n,b,ns){Type(n,b,ns)}}class C(metaclass=F){let n=42}C.n'),42)
        self.assertEqual(self.code('fn factory(n,b,ns){42}let f=staticmethod(factory);class C(metaclass=f){}C'),42)
        self.assertEqual(self.code('fn factory(n,b,ns){ {name:n} }new_class("C",[],{}, {metaclass:factory})'),{'name':'C'})

    def test_metaclass_call_can_return_cold_task_and_generator(self):
        self.assertEqual(self.code('class M(Type){async fn __call__(c,x){x+1}}class C(metaclass=M){}let t=C(41);[type_of(t),await t]'),['Task',42])
        self.assertEqual(self.code('class M(Type){fn* __call__(c){yield 42}}class C(metaclass=M){}collect(C())'),[42])

    def test_cooperative_new_init_call_and_super_layers(self):
        source='''var seen=[];class A(Type){fn __new__(m,n,b,ns){seen=append(seen,"Anew");super(A,m).__new__(m,n,b,ns)}fn __init__(c,n,b,ns){seen=append(seen,"Ainit");super(A,c).__init__(n,b,ns);null}}
        class B(A){fn __new__(m,n,b,ns){seen=append(seen,"Bnew");super(B,m).__new__(m,n,b,ns)}fn __init__(c,n,b,ns){seen=append(seen,"Binit");super(B,c).__init__(n,b,ns);null}
        fn __call__(c,x){let v=super(B,c).__call__(x);v.n=v.n+1;v}}
        class C(metaclass=B){var n=0;fn __init__(s,x){s.n=x;null}}[C(41).n,seen]'''
        self.assertEqual(self.code(source),[42,['Bnew','Anew','Binit','Ainit']])

    def test_meta_property_and_descriptors_receive_class_not_null(self):
        self.assertEqual(self.code('''class M(Type){@property
        fn x(c){c.saved+1}
        @x.setter
        fn x(c,v){c.saved=v;null}
        @x.deleter
        fn x(c){c.saved=0;null}}
        class C(metaclass=M){var saved=40;let x=99}C.x=41;let got=C.x;delattr(C,"x");[got,C.saved,vars(C).x]'''),[42,0,99])
        self.assertEqual(self.code('class D{fn __get__(s,c,m){[c,m]}}class M(Type){let x=D();fn me(c){c};@classmethod\nfn meta(m){m};@staticmethod\nfn static(x){x}}class C(metaclass=M){let x=42}class E(metaclass=M){}[C.x,E.x[0]==E,E.x[1]==M,C.me()==C,C.meta()==M,C.static(42)]'),[42,True,True,True,True,42])

    def test_builtin_type_cannot_be_mutated_or_allocated_as_instance(self):
        for code in ['Type.x=1','Type.__call__=42','delattr(Type,"__call__")','alloc_instance(Type)','class M(Type){}alloc_instance(M)']:
            with self.subTest(code=code),self.assertRaises(LanguageError):self.code(code)

    def test_creation_hooks_reject_async_cold_result_and_bad_init(self):
        for method,params,body in [('__prepare__','m,n,b','{}'),('__new__','m,n,b,ns','42'),('__init__','c,n,b,ns','null')]:
            for prefix in ['async fn','fn*','async fn*']:
                calls=[]
                source='class M(Type){'+prefix+' '+method+'('+params+'){tool("bad",1);'+('yield 1;'if '*' in prefix else '')+body+'}}class C(metaclass=M){}'
                with self.subTest(method=method,prefix=prefix),self.assertRaises(LanguageError):self.code(source,tool_handler=lambda n,v:calls.append(n))
                self.assertEqual(calls,[])
        for source in ['class M(Type){fn __new__(m,n,b,ns){(async fn(){42})()}}class C(metaclass=M){}','class M(Type){fn __init__(c,n,b,ns){42}}class C(metaclass=M){}','async fn factory(n,b,ns){42}class C(metaclass=factory){}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_foreign_nested_values_and_type_identity_are_owned(self):
        foreign=self.code('class M(Type){}M')
        for source in ['class C(metaclass=input){}','new_class("C",[],{}, {metaclass:input})','Type("C",[],{nested:[input]})','Type(input)']:
            with self.subTest(source=source),self.assertRaises(LanguageError)as caught:Evaluator().run(source,{'input':foreign})
            self.assertEqual(caught.exception.code,'ownership_error')
        self.assertIsNot(Evaluator().native_type,Evaluator().native_type)

    def test_function_factory_and_namespace_cannot_widen_authority(self):
        calls=[];ev=Evaluator(tool_handler=lambda n,v:calls.append(n)or 42)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        self.assertEqual(ev.run('fn factory(n,b,ns){fn(x){tool("secret",x)}}fn capture(){factory}let m=limited(capture);class C(metaclass=m){}[try{C(1)}catch e{e.code},limited(fn(){Type})==Type]',{'limited':limited}),['capability_error',True]);self.assertEqual(calls,[])

    def test_final_mutable_cell_keeps_closure_alias_after_metaclass_transformation(self):
        source='class M(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,set(ns,"n",class_member("ready",mutable=true,contract="String")))}}class C(metaclass=M){var n:Int=1;let read=fn(){n}}let before=C.read();C.n="updated";[before,C.read(),class_members(C).n.contract,try{C.n=2}catch e{e.code}]'
        self.assertEqual(self.code(source),['ready','updated','String','runtime_error'])
        source='class C{var n=1;let read=fn(){n}}let D=new_class("D",[],class_members(C));D.n=9;C.n=2;[D.n,C.n,D.read(),C.read()]'
        self.assertEqual(self.code(source),[9,2,2,2])
        source='class M(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,set(ns,"n",class_member(42)))}}class C(metaclass=M){var n=1;let read=fn(){n}}[C.n,C.read(),try{C.n=2}catch e{e.code}]'
        self.assertEqual(self.code(source),[42,1,'attribute_error'])

    def test_return_projection_preserves_nominal_program_and_nested_function_authority(self):
        from chaos.program_values import Program
        calls=[];ev=Evaluator(tool_handler=lambda n,v:calls.append(n)or 42)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        source='let p=program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"fn add(x){x+1};add(input)"},languageVersions:{"main.chaos":2}});let values=limited(fn(){[p,{program:p}]});[values[0],values[1].program,program_info(program_rename(values[0],"main.chaos",3,"increase")).files["main.chaos"]]'
        values=ev.run(source,{'limited':limited})
        self.assertIs(type(values[0]),Program);self.assertIs(values[0],values[1])
        self.assertEqual(values[2],'fn increase(x){x+1};increase(input)')
        source='fn secret(x){tool("secret",x)}let values=limited(fn(){[secret,{callback:secret,native:tool}]});[type_of(values[0]),try{values[0](1)}catch e{e.code},try{values[1].callback(1)}catch e{e.code},try{values[1].native("secret",1)}catch e{e.code}]'
        self.assertEqual(ev.run(source,{'limited':limited}),['Function','capability_error','capability_error','capability_error'])
        self.assertEqual(calls,[])

    def test_real_agent_tool_program_metadata_and_ordinary_member_keep_nominal_identity(self):
        from chaos import Framework, Settings, Registry, ProviderSpec, ToolCall, Final
        requests=[];registry=Registry()
        def provider(request,context):
            requests.append(request)
            return Final(request.history[-1]['output']) if request.history else ToolCall('reflect',None)
        registry.register_provider(ProviderSpec('test.meta-program',provider))
        source='''let p=current_program();agent a{provider:"test.meta-program"};
        let inspect=agent_tool("reflect",fn(_){
         let direct=class_member(p).value;let C=new_class("Candidate",[],{p:class_member(p)});
         let reflected=class_members(C).p.value;let member=C.p;
         let renamed=program_rename(reflected,"main.chaos",4,"snapshot");
         [type_of(direct),type_of(reflected),type_of(member),direct==p,reflected==p,member==p,
          program_info(direct).entry,program_info(member).entry,
          program_info(renamed).files["main.chaos"]!=program_info(p).files["main.chaos"]]
        },output_type="List");await agent_bind(a,[inspect])(null)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('metaclass-program-reflection',source=source,capabilities=[])
            self.assertEqual(app.start('metaclass-program-reflection',1,None,mode='source').result(10),['Program']*3+[True]*3+['main.chaos']*2+[True])
        self.assertEqual(len(requests),2)

    def test_nominal_projection_preserves_ownership_and_agent_function_ceiling(self):
        foreign=self.code('program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"42"},languageVersions:{"main.chaos":2}})')
        with self.assertRaises(LanguageError) as caught:Evaluator().run('class_member(input).value',{'input':foreign})
        self.assertEqual(caught.exception.code,'ownership_error')
        calls=[];ev=Evaluator(agent_handler=lambda n,c,v:calls.append(n)or v)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        source='agent secret{tools:["secret"]};let fromReturn=limited(fn(){secret});let fromMetadata=limited(fn(){class_member(secret)}).value;let fromField=limited(fn(){class C{let actor=secret};C.actor});[try{fromReturn(1)}catch e{e.code},try{fromMetadata(1)}catch e{e.code},try{fromField(1)}catch e{e.code}]'
        self.assertEqual(ev.run(source,{'limited':limited}),['capability_error']*3);self.assertEqual(calls,[])
        with self.assertRaises(LanguageError) as caught:ev.run('class Wide{}limited(fn(){class_member(Wide).value})',{'limited':limited})
        self.assertEqual(caught.exception.code,'capability_error')

    def test_restricted_returns_reject_wider_nominal_and_deferred_authority(self):
        for expression in ['Wide','instance','[Wide]','{value:instance}','tuple([Wide])','coldTask','coldGenerator']:
            calls=[];ev=Evaluator(tool_handler=lambda n,v:calls.append(n)or 42)
            limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
            source='class Wide{fn secret(s){tool("secret",1)}}let instance=Wide();async fn later(){tool("secret",1)}fn* stream(){yield tool("secret",1)}let coldTask=later();let coldGenerator=stream();limited(fn(){'+expression+'})'
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:ev.run(source,{'limited':limited})
            self.assertEqual(caught.exception.code,'capability_error');self.assertEqual(calls,[])
        calls=[];ev=Evaluator(tool_handler=lambda n,v:calls.append(n)or 42)
        limited=Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        source='let builtin=limited(fn(){Type});let safe=limited(fn(){class Safe{fn secret(s){tool("secret",1)}};Safe});[builtin==Type,builtin(safe)==Type,try{safe().secret()}catch e{e.code}]'
        self.assertEqual(ev.run(source,{'limited':limited}),[True,True,'capability_error']);self.assertEqual(calls,[])

    def test_async_tasks_keep_separate_creation_contexts_and_effect_thread(self):
        main=threading.get_ident();threads=[]
        source='''agent a{};class M(Type){fn __new__(m,n,b,ns){a(n);Type.__new__(m,n,b,ns)}}
        async fn work(x){class C(metaclass=M){let n=x;fn own(s){C}}[C().n,C().own()==C]}
        let one=work(41);let two=work(42);[await one,await two]'''
        self.assertEqual(self.code(source,agent_handler=lambda n,c,x:threads.append(threading.get_ident())or x),[[41,True],[42,True]])
        self.assertTrue(threads);self.assertTrue(all(t!=main for t in threads))

    def test_cancellation_budget_and_failed_binding_cleanup(self):
        stop=threading.Event();ev=Evaluator(cancelled=stop.is_set,tool_handler=lambda n,v:stop.set()or {})
        with self.assertRaises(ResourceLimit):ev.run('class M(Type){fn __prepare__(m,n,b){tool("stop",1)}}async fn work(){class C(metaclass=M){}}await work()')
        self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)
        with self.assertRaises(ResourceLimit):self.code('class M(Type){fn __prepare__(m,n,b){while true{1}}}class C(metaclass=M){}',step_limit=500)
        session=Session();session.evaluate('class M(Type){fn __init__(c,n,b,ns){throw "failed"}}')
        with self.assertRaises(LanguageError):session.evaluate('class Hidden(metaclass=M){}')
        self.assertNotIn('Hidden',session.env.values)

    def test_type_creation_resource_limits_and_header_source_order(self):
        ev=Evaluator();ev.object_class_count=1000
        with self.assertRaises(ResourceLimit):ev.run('Type("C",[],{})')
        source='''var seen=[];class B{}class M(Type){fn __prepare__(m,n,b,*,x){seen=append(seen,"prepare");{}}fn __new__(m,n,b,ns,*,x){Type.__new__(m,n,b,ns)}fn __init__(c,n,b,ns,*,x){null}}
        fn value(x){seen=append(seen,x);x}class C(value(B),metaclass=value(M),x=value(1)){let v=value(2)}[seen[0]==B,seen[1]==M,seen[2],seen[3],seen[4]]'''
        self.assertEqual(self.code(source),[True,True,1,'prepare',2])

if __name__=='__main__': unittest.main()
