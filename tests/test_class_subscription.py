"""Class subscription composes owned language functions, not a host generic alias."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Evaluator, Framework, Registry, Settings, ProviderSpec, ToolCall, Final, Client
from chaos.framework import RunFailed
from chaos.language import LanguageError, ResourceLimit, Native, TailCall
from chaos.ide_language import analyze, format_source
from chaos.program import parse_program, format_program
from chaos.server import make_server

SOURCE = '''class Factory { fn __class_getitem__(cls, bias:Int) { fn(x:Int)->Int { x+bias } } }
let work=Factory[40]; work(2)'''


class ClassSubscriptionTests(unittest.TestCase):
    def evaluate(self, source, **options):
        return Evaluator(**options).run(source)

    def test_inherited_hook_binds_actual_class_and_cooperative_super(self):
        self.assertEqual(self.evaluate('''var expected=null;class A{fn __class_getitem__(cls,k){[cls==expected,k]}}
        class B(A){fn __class_getitem__(cls,k){super(B,cls).__class_getitem__(k+1)}}
        expected=B;[B[41],B.__class_getitem__(41),B().__class_getitem__(41)]'''), [[True,42]]*3)

    def test_class_instance_and_explicit_member_access_are_distinct(self):
        self.assertEqual(self.evaluate('''class C{let answer=7;fn __class_getitem__(cls,k){["class",k]}
        fn __getitem__(self,k){["instance",k]}}
        [C["answer"],C()["answer"],C.answer,C.["answer"],getitem(C,"answer")]'''),
        [['class','answer'],['instance','answer'],7,7,['class','answer']])

    def test_legacy_class_member_fallback_is_unchanged(self):
        self.assertEqual(self.evaluate('class C{var x=40}let a=do{C["x"]=42};[a,C["x"],getitem(C,"x")]'),[42,42,42])
        for call in ('setitem(C,"x",1)','delitem(C,"x")'):
            with self.subTest(call=call),self.assertRaises(LanguageError):self.evaluate('class C{var x=40}'+call)

    def test_metaclass_slot_precedes_class_hook_and_class_shadow(self):
        self.assertEqual(self.evaluate('''var expected=null;class M(Type){fn __getitem__(cls,k){[cls==expected,k]}}class N(M){}
        class C(metaclass=N){let __getitem__=42;fn __class_getitem__(cls,k){throw "wrong"}}
        expected=C;[C[41],getitem(C,42)]'''),[[True,41],[True,42]])

    def test_noncallable_slot_and_protocol_failure_do_not_fall_back(self):
        for prefix in ('class M(Type){let __getitem__=null}class C(metaclass=M){fn __class_getitem__(cls,k){42}}',
                       'class C{let __class_getitem__=null;let x=42}'):
            with self.subTest(prefix=prefix),self.assertRaises(LanguageError):self.evaluate(prefix+'C["x"]')
        self.assertEqual(self.evaluate('class C{fn __class_getitem__(cls,k){throw 42}}try{C[0]}catch e{[e.code,e.value]}'),['user_throw',42])

    def test_explicit_static_class_and_value_function_bindings(self):
        self.assertEqual(self.evaluate('''class S{let __class_getitem__=staticmethod(fn(k){k+1})}
        class C{let __class_getitem__=classmethod(fn(cls,k){[cls==C,k]})}
        class V{let __class_getitem__=fn(k){k+2}}[S[41],C[42],V[40]]'''),[42,[True,42],42])

    def test_dynamic_class_member_metadata_and_copy_share_binding(self):
        self.assertEqual(self.evaluate('''let method=class_member(fn(cls,k){[type_of(cls),k]},method=true);
        let A=new_class("A",members={__class_getitem__:method});let B=Type("B",[],class_members(A));
        [A[41],B[42],class_members(B).__class_getitem__.method]'''),[['Class',41],['Class',42],True])

    def test_class_descriptor_and_metaclass_descriptor_bind_once(self):
        self.assertEqual(self.evaluate('''var seen=[];var expected=null;class D{fn __get__(s,obj,owner){seen=append(seen,[obj==null,owner==expected]);fn(k){seen=append(seen,k);42}}}
        class C{let __class_getitem__=D()}expected=C;let result=C[7];[result,seen]'''),[42,[[True,True],7]])
        self.assertEqual(self.evaluate('''var seen=[];var expected=null;var expectedMeta=null;class D{fn __get__(s,obj,owner){seen=append(seen,[obj==expected,owner==expectedMeta]);fn(k){42}}}
        class M(Type){let __getitem__=D()}class C(metaclass=M){}expected=C;expectedMeta=M;let result=C[0];[result,seen]'''),[42,[[True,True]]])

    def test_metaclass_data_descriptor_can_intercept_class_hook(self):
        self.assertEqual(self.evaluate('''class D{fn __get__(s,obj,owner){fn(k){k+1}}fn __set__(s,obj,v){null}}
        class M(Type){let __class_getitem__=D()}class C(metaclass=M){fn __class_getitem__(cls,k){0}}C[41]'''),42)

    def test_keys_and_endpoints_evaluate_once_without_generic_type_coercion(self):
        self.assertEqual(self.evaluate('''var seen=[];fn mark(n){seen=append(seen,n);n}
        class P{fn __index__(s){throw "not an index"}}class C{fn __class_getitem__(cls,k){[k[0].start,k[0].stop,type_of(k[1])]}}
        fn target(){seen=append(seen,0);C}let result=target()[mark(1):mark(3),P()];[result,seen]'''),[[1,3,'Instance'],[0,1,3]])

    def test_metaclass_write_delete_augmented_order_and_null_contract(self):
        self.assertEqual(self.evaluate('''var seen=[];class M(Type){fn __getitem__(cls,k){seen=append(seen,"get");cls.value}
        fn __setitem__(cls,k,v){seen=append(seen,"set");cls.value=v;null}
        fn __delitem__(cls,k){seen=append(seen,"delete");cls.value=0;null}}
        class C(metaclass=M){var value=40}fn target(){seen=append(seen,"target");C}fn key(){seen=append(seen,"key");"x"}
        target()[key()]+=2;let result=C.value;delitem(C,"x");[result,C.value,seen]'''),[42,0,['target','key','get','set','delete']])
        for slot,call in [('__setitem__','setitem(C,0,1)'),('__delitem__','delitem(C,0)')]:
            with self.subTest(slot=slot),self.assertRaises(LanguageError):self.evaluate('class M(Type){fn '+slot+'(cls,*args){1}}class C(metaclass=M){}'+call)

    def test_async_and_generator_reads_remain_cold_until_consumed(self):
        self.assertEqual(self.evaluate('''var calls=0;class A{async fn __class_getitem__(cls,k){calls=calls+1;k}}
        let task=A[42];let before=calls;let result=await task;[before,result,calls]'''),[0,42,1])
        self.assertEqual(self.evaluate('''var calls=0;class A{fn* __class_getitem__(cls,k){calls=calls+1;yield k}}
        let stream=A[42];let before=calls;let result=collect(stream);[before,result,calls]'''),[0,[42],1])
        self.assertEqual(self.evaluate('''var calls=0;class M(Type){async fn* __getitem__(cls,k){calls=calls+1;yield k}}
        class A(metaclass=M){}let stream=A[42];let before=calls;var values=[];async for x in stream{values=append(values,x)};[before,values,calls]'''),[0,[42],1])

    def test_async_writers_are_rejected_before_body(self):
        for flavor in ('async fn','fn*','async fn*'):
            for slot,call in [('__setitem__','setitem(C,0,1)'),('__delitem__','delitem(C,0)')]:
                seen=[]
                with self.subTest(flavor=flavor,slot=slot),self.assertRaises(LanguageError):
                    self.evaluate('class M(Type){'+flavor+' '+slot+'(cls,*args){tool("bad",0);null}}class C(metaclass=M){}'+call,tool_handler=lambda n,v:seen.append(n))
                self.assertEqual(seen,[])

    def test_owned_target_nested_keys_and_returned_values(self):
        foreign=Evaluator().run('class C{fn __class_getitem__(cls,k){42}}C')
        for code in ('input[0]','class C{fn __class_getitem__(cls,k){42}}C[(input,)]'):
            with self.subTest(code=code),self.assertRaises(LanguageError) as caught:Evaluator().run(code,{'input':foreign})
            self.assertEqual(caught.exception.code,'ownership_error')
        ev=Evaluator();borrow=Native('borrow',lambda:foreign,ev)
        with self.assertRaises(LanguageError) as caught:ev.run('class C{fn __class_getitem__(cls,k){borrow()}}C[0]',{'borrow':borrow})
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_function_results_are_real_closures_and_budget_is_shared(self):
        self.assertEqual(self.evaluate(SOURCE),42)
        self.assertEqual(self.evaluate('class C{fn __class_getitem__(cls,k){new_class("Generated",members={answer:class_member(k)})}}C[42]().answer'),42)
        with self.assertRaises(ResourceLimit):self.evaluate('class C{fn __class_getitem__(cls,k){C[k]}}C[0]',step_limit=500)

    def test_context_and_cancellation_follow_existing_scheduler(self):
        self.assertEqual(self.evaluate('let v=context_var("v",default=0);class C{fn __class_getitem__(cls,k){v.get()+k}}with context_bind([{variable:v,value:40}]) as binding{C[2]}'),42)
        cancelled=threading.Event();seen=[]
        def effect(n,v):seen.append(v);cancelled.set();return v
        ev=Evaluator(tool_handler=effect,cancelled=cancelled.is_set)
        with self.assertRaises(ResourceLimit):ev.run('class C{fn __class_getitem__(cls,k){tool("read",k)}}C[42]')
        self.assertEqual(seen,[42]);self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)

    def test_metaclass_only_class_hook_descriptor_is_visible(self):
        self.assertEqual(self.evaluate('var seen=0;class D{fn __get__(s,obj,owner){seen=seen+1;fn(k){[class_of(obj)==owner,k]}}}class M(Type){let __class_getitem__=D()}class C(metaclass=M){}[C[42],seen]'),[[True, 42], 1])

    def test_narrow_meta_callback_cannot_escape_authority(self):
        calls=[]; ev=Evaluator(tool_handler=lambda n,v:calls.append(n))
        limited=Native("limited",lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        self.assertEqual(ev.run('fn secret(x){tool("secret",x)}fn build(){class M(Type){fn __getitem__(cls,k){secret}}new_class("C",[],{}, {metaclass:M})}let C=limited(build);try{C[0](1)}catch e{e.code}', {"limited":limited}),'capability_error')
        self.assertEqual(calls,[])

    def test_narrow_class_callback_cannot_escape_authority(self):
        calls=[]; ev=Evaluator(tool_handler=lambda n,v:calls.append(n))
        limited=Native("limited",lambda f:TailCall(f,[]),ev,capabilities=frozenset())
        self.assertEqual(ev.run('fn secret(x){tool("secret",x)}fn build(){class C{fn __class_getitem__(cls,k){[secret,{f:secret}]}}C}let C=limited(build);let xs=C[0];[try{xs[0](1)}catch e{e.code},try{xs[1].f(1)}catch e{e.code}]', {"limited":limited}),['capability_error', 'capability_error'])
        self.assertEqual(calls,[])

    def test_actual_agent_tool_can_be_built_by_subscription(self):
        registry=Registry();registry.register_provider(ProviderSpec('choose',lambda req,ctx:ToolCall('work',req.input) if not req.history else Final(req.history[-1]['output'])))
        source='''agent a{provider:"choose",tools:[]};class Factory{fn __class_getitem__(cls,bias){
        fn work(x:Int)->Int{x+bias}agent_bind(a,[agent_tool("work",work,input_type="Int",output_type="Int")])}}
        await Factory[40](2)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('subscription-agent',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None).result(10),42)

    def test_agent_tool_authority_covers_class_and_meta_descriptors(self):
        registry=Registry();registry.register_provider(ProviderSpec('choose',lambda req,ctx:ToolCall('work',req.input) if not req.history else Final(req.history[-1]['output'])))
        for body in ['class C{fn __class_getitem__(cls,k){tool("denied",k)}}C[1]',
                     'class D{fn __get__(s,o,c){tool("denied",1);fn(k){k}}}class M(Type){let __getitem__=D()}class C(metaclass=M){}C[1]']:
            source='agent a{provider:"choose",tools:[]};fn work(x){'+body+'};await agent_bind(a,[agent_tool("work",work,capabilities=[])])(1)'
            with self.subTest(body=body),Framework(registry=registry,settings=Settings(':memory:')) as app:
                p=app.create_project('subscription-denied',source=source,capabilities=[])
                with self.assertRaises(RunFailed) as caught:app.start(p['id'],p['version'],None).result(10)
                self.assertIn('Agent tool capability denied: denied',caught.exception.run['error'])


class ClassSubscriptionSurfaces(unittest.TestCase):
    def test_source_ir_format_and_cli_share_semantics(self):
        program=parse_program(SOURCE)
        for source in (SOURCE,format_program(program),format_source(SOURCE,line_width=40)):
            self.assertEqual(Framework.evaluate(source),42)
            self.assertEqual(parse_program(source)['semanticHash'],program['semanticHash'])
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'factory.chaos';path.write_text(SOURCE)
            result=subprocess.run([sys.executable,'-m','chaos','run',str(path)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),42)

    def test_macro_generated_and_runtime_generated_class(self):
        source='macro specialize(c,k){quote{unquote(c)[unquote(k)]}}class C{fn __class_getitem__(cls,k){k+1}}specialize!(C,41)'
        self.assertEqual(Framework.evaluate(source),42)
        self.assertEqual(Framework.evaluate('let f=function_from(code_from("fn(){class C{fn __class_getitem__(cls,k){k+1}}C[41]}"));f()'),42)

    def test_analysis_does_not_run_hooks_and_preserves_bound_signature(self):
        source='class C{fn __class_getitem__(cls,key:Int)->Int{key}}'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed during analysis')):
            result=analyze(source+'C.__class_getitem__(')
            self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['key'])
            result=analyze(source+'let a=C[1];let b=getitem(C,2);')
            self.assertEqual({s['name']:s['type'] for s in result['symbols'] if s['name'] in ('a','b')},{'a':'Int','b':'Int'})
            self.assertEqual(result['diagnostics'],[])
            result=analyze('class C{let old=1;fn __class_getitem__(cls,k)->String{"new"}}let x=C["old"];')
            self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='x'),'String')

    def test_http_sdk_source_and_program_roundtrip(self):
        with tempfile.TemporaryDirectory() as td,Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(td)/'state',update_home=Path(td)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                p=client.create_project('subscription-http',source=SOURCE,capabilities=[])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='source').result(),42)
                edited=client.edit_program(p['id'],p['version'],program=parse_program(SOURCE))
                current=client.get_project(p['id'])
                self.assertEqual(client.start(current['id'],current['version'],None,mode='source').result(),42)
            finally:server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':unittest.main()
