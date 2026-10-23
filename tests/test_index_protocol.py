"""Lossless user indices are ordinary owned calls in the language scheduler."""
import threading
import unittest
from chaos import Evaluator, Framework, Registry, ProviderSpec, ToolCall, Final, Settings
from chaos.framework import RunFailed
from chaos.language import LanguageError, ResourceLimit
from chaos.indexing import Slice, native_get, slice_key


class IndexProtocolTests(unittest.TestCase):
    def evaluate(self, source, **options):
        return Evaluator(**options).run(source)

    def test_integer_passthrough_and_no_number_or_int_coercion(self):
        self.assertEqual(self.evaluate('[index(0),index(-3),index(123456789012345678901234567890)]'),[0,-3,123456789012345678901234567890])
        for value in (True,False,1.0,'1',None,[],{}):
            with self.subTest(value=value), self.assertRaises(LanguageError) as caught:
                Evaluator().run('index(input)',{'input':value})
            self.assertEqual(caught.exception.code,'argument_error')
        with self.assertRaises(LanguageError):self.evaluate('class C{fn __int__(self)->Int{1}}index(C())')
        with self.assertRaises(ResourceLimit):Evaluator().run('index(input)',{'input':1<<14000})

    def test_class_slot_mro_static_classmethod_and_ignores_instance_field(self):
        source='''class A{var __index__=fn()->Int{1}}class B(A){}let b=B();b.__index__=fn()->Int{0};
        class S{let __index__=staticmethod(fn()->Int{2})}class K{let __index__=classmethod(fn(cls)->Int{3})}
        [index(b),[10,20][b],index(S()),index(K())]'''
        self.assertEqual(self.evaluate(source),[1,20,2,3])
        with self.assertRaises(LanguageError):self.evaluate('class C{}let c=C();c.__index__=fn()->Int{0};index(c)')
        self.assertEqual(self.evaluate('class A{fn __index__(self)->Int{1}}class B{fn __index__(self)->Int{2}}class C(A,B){}index(C())'),1)

    def test_descriptor_binding_and_effects_use_existing_invocation(self):
        source='''var seen=[];class D{fn __get__(self,obj,owner){seen=append(seen,"bind");fn(){seen=append(seen,"call");2}}}
        class C{let __index__=D()}let result=[10,20,30][C()];[result,seen]'''
        self.assertEqual(self.evaluate(source),[30,['bind','call']])
        with self.assertRaises(LanguageError):self.evaluate('class C{let __index__=42}index(C())')

    def test_protocol_result_must_be_exact_integer(self):
        for expression in ('true','1.0','null','"1"','[]','self','async fn(){1}()','fn*(){yield 1}()'):
            with self.subTest(expression=expression), self.assertRaises(LanguageError) as caught:
                self.evaluate('class C{fn __index__(self){'+expression+'}}index(C())')
            self.assertEqual(caught.exception.code,'argument_error')
        with self.assertRaises(ResourceLimit):Evaluator().run('class C{fn __index__(self){input}}index(C())',{'input':1<<14000})

    def test_async_and_generator_methods_never_start(self):
        for flavor in ('async fn','fn*','async fn*'):
            calls=[]
            with self.subTest(flavor=flavor), self.assertRaises(LanguageError):
                self.evaluate('class C{'+flavor+' __index__(self){tool("unexpected",0);1}}index(C())',tool_handler=lambda n,v:calls.append((n,v)))
            self.assertEqual(calls,[])

    def test_native_sequence_bracket_getitem_tuple_and_unicode(self):
        source='''class P{var n:Int=0;fn __init__(self,n:Int){self.n=n;null}fn __index__(self)->Int{self.n}}
        [[10,20][P(1)],getitem([10,20],P(-1)),(10,20)[P(0)],"😀ab"[P(0)],"😀ab"[P(-1)]]'''
        self.assertEqual(self.evaluate(source),[20,20,10,'😀','b'])
        with self.assertRaises(LanguageError):self.evaluate('class P{fn __index__(self)->Int{4}}[1][P()]')

    def test_slice_endpoints_evaluate_then_convert_once_in_source_order(self):
        source='''var seen=[];class P{var n:Int=0;fn __init__(self,n){self.n=n;null}fn __index__(self)->Int{seen=append(seen,self.n);self.n}}
        fn point(n){seen=append(seen,str(n));P(n)}let result=[0,1,2,3,4][point(1):point(4):point(2)];[result,seen]'''
        self.assertEqual(self.evaluate(source),[[1,3],['1','4','2',1,4,2]])
        source='''var seen=[];class P{var n:Int=0;fn __init__(self,n){self.n=n;null}fn __index__(self)->Int{seen=append(seen,self.n);self.n}}
        fn point(n){seen=append(seen,str(n));P(n)}let spec=slice_key(step=point(2),stop=point(4),start=point(1));[[0,1,2,3,4][spec],seen]'''
        self.assertEqual(self.evaluate(source),[[1,3],['2','4','1',1,4,2]])
        source='''var calls=0;class P{fn __index__(self)->Int{calls=calls+1;1}}let p=P();let spec=slice_key(p,p,p);[calls,spec.start,spec.stop,spec.step]'''
        self.assertEqual(self.evaluate(source),[3,1,1,1])

    def test_slice_keys_indices_and_zero_step_negative_length(self):
        source='''class P{var n:Int=0;fn __init__(self,n){self.n=n;null}fn __index__(self)->Int{self.n}}
        let make=slice_key;let normalized=slice_indices;let s=make(start=P(-3),step=P(1));
        [[0,1,2,3,4][s],s.start,s.stop,s.step,normalized(spec=s,length=P(5)),type_of(s)]'''
        self.assertEqual(self.evaluate(source),[[2,3,4],-3,None,1,[2,5,1],'Slice'])
        for source in ('slice_key(step=P(0))','[1][::P(0)]','slice_indices(slice_key(),P(-1))'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate('class P{var n=0;fn __init__(self,n){self.n=n;null}fn __index__(self){self.n}}'+source)
        calls=[]
        with self.assertRaises(LanguageError):self.evaluate('class P{fn __index__(self){tool("bad",0);1}}slice_indices(42,P())',tool_handler=lambda n,v:calls.append(v))
        self.assertEqual(calls,[])

    def test_record_and_user_item_keys_remain_arbitrary_and_unconverted(self):
        source='''var converted=0;class P{fn __index__(self)->Int{converted=converted+1;0}}
        class C{var last=null;var value=1;fn __getitem__(self,k){self.last=k;self.value}fn __setitem__(self,k,v){self.last=k;self.value=v;null}fn __delitem__(self,k){self.last=k;null}}
        let p=P();let c=C();c[p]+=2;let result=c.value;setitem(c,p,4);delitem(c,p);[result,type_of(c.last),converted]'''
        self.assertEqual(self.evaluate(source),[3,'Instance',0])
        calls=[]
        with self.assertRaises(LanguageError):self.evaluate('class P{fn __index__(self){tool("bad",0);0}}{x:1}[P()]',tool_handler=lambda n,v:calls.append(v))
        self.assertEqual(calls,[])
        self.assertEqual(self.evaluate('{x:42}["x"]'),42)

    def test_native_augmented_and_write_rejection_do_not_repeat_index(self):
        source='''var calls=0;class P{fn __index__(self)->Int{calls=calls+1;0}}let p=P();
        try{[1][p]+=2}catch e{null};let after=calls;try{setitem([1],p,2)}catch e{null};try{delitem([1],p)}catch e{null};[after,calls]'''
        self.assertEqual(self.evaluate(source),[1,1])

    def test_user_slice_key_is_normalized_without_converting_tuple_scalar_keys(self):
        source='''var calls=0;class P{fn __index__(self)->Int{calls=calls+1;1}}class C{fn __getitem__(self,key){[type_of(key[0]),key[0].start,type_of(key[1])]}}
        let answer=C()[P():,P()];[answer,calls]'''
        self.assertEqual(self.evaluate(source),[['Slice',1,'Instance'],1])

    def test_protocol_failure_and_global_cancel_keep_original_error(self):
        self.assertEqual(self.evaluate('class P{fn __index__(self){throw 42}}try{index(P())}catch e{[e.code,e.value]}'),['user_throw',42])
        cancelled=threading.Event();calls=[]
        def effect(n,v):calls.append(v);cancelled.set();return 1
        ev=Evaluator(tool_handler=effect,cancelled=cancelled.is_set)
        with self.assertRaises(ResourceLimit):ev.run('class P{fn __index__(self){tool("read",1)}}index(P())')
        self.assertEqual(calls,[1]);self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)
        with self.assertRaises(ResourceLimit):self.evaluate('class P{fn __index__(self){index(self)}}index(P())',step_limit=1000)

    def test_protocol_owner_is_current_interpreter(self):
        p=Evaluator().run('class P{fn __index__(self)->Int{0}}P()')
        for source in ('index(input)','[1][input]','slice_key(input)'):
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:Evaluator().run(source,{'input':p})
            self.assertEqual(caught.exception.code,'ownership_error')

    def test_hot_index_calls_suspend_host_effects_and_overlap_in_async_wrappers(self):
        barrier=threading.Barrier(2);seen=[]
        def effect(kind,payload,cancel):seen.append(threading.get_ident());barrier.wait(timeout=2);return payload['value']
        source='''class P{var n:Int=0;fn __init__(self,n){self.n=n;null}fn __index__(self)->Int{tool("read",self.n)}}
        async fn work(n){[10,20][P(n)]}await gather([work(0),work(1)])'''
        self.assertEqual(self.evaluate(source,tool_handler=lambda *a:None,effect_handler=effect),[10,20]);self.assertEqual(len(set(seen)),2)

    def test_macros_expand_runtime_indices_but_compile_phase_stays_pure(self):
        self.assertEqual(self.evaluate('macro choose(op,v){quote {unquote(op)(unquote(v))}}class P{fn __index__(self)->Int{2}}choose!(index,P())'),2)
        calls=[]
        with self.assertRaises(LanguageError):self.evaluate('macro choose(){index(1)}tool("seen",1);choose!()',tool_handler=lambda n,v:calls.append(v))
        self.assertEqual(calls,[])
        with self.assertRaises(LanguageError):native_get([1],object())
        with self.assertRaises(LanguageError):slice_key(object())

    def test_real_language_agent_tool_uses_indices_and_restricts_capabilities(self):
        def provider(request,context):return ToolCall('pick',request.input) if not request.history else Final(request.history[-1]['output'])
        def run(source,caps=()):
            registry=Registry();registry.register_provider(ProviderSpec('choose',provider))
            with Framework(registry=registry,settings=Settings(':memory:')) as app:
                app.create_project('indices',source=source,capabilities=list(caps));return app.start('indices',1,1,mode='source').result(10)
        base='agent a{provider:"choose",tools:[]};'
        source=base+'''fn pick(n:Int)->Int{class P{fn __index__(self)->Int{n}}[10,20][P()]}
        let work=agent_tool("pick",pick,input_type="Int",output_type="Int");await agent_bind(a,[work])(input)'''
        self.assertEqual(run(source),20)
        source=base+'''fn pick(n:Int)->Int{class P{fn __index__(self)->Int{tool("denied",n)}}index(P())}
        let work=agent_tool("pick",pick,input_type="Int",output_type="Int",capabilities=[]);await agent_bind(a,[work])(input)'''
        with self.assertRaises(RunFailed) as caught:run(source)
        self.assertIn('Agent tool capability denied: denied',caught.exception.run['error'])


if __name__=='__main__':unittest.main()
