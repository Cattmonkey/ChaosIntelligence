"""Whole original access algorithms, real pending state and security contracts."""
import copy
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native
from chaos.execution_frames import ExecutionCursor,ensure_cursor
from chaos.execution_access import (ACCESS_FRAMES,AccessFrameRequest,AttributeHookFrame,
    AttributeReadFrame,AttributeWriteFrame,AttributeDirectoryFrame,AccessResultFrame,
    ItemKeyFrame,LegacyIndexFrame,ScopedGetMemberFrame,ScopedSetMemberFrame,
    ScopedDeleteMemberFrame,IntegerIndexFrame,IndexResolveFrame,ItemInvokeFrame,
    ItemOperationFrame,ObjectAccessEvaluationFrame)
from chaos.indexing import IndexRequest,ItemRequest
from _access_reference164 import reference
from _effect_schedule158 import effect_schedule
from test_operator_execution_frames import active


def snapshot(source,legacy=False,limit=100000,cancel=None,setup=None,effect_policy='already_done'):
    effects=[];trace=[];debug=[]
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)))
        return payload['value'] if kind=='tool' else payload.get('input')
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:v,effect_handler=effect,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup is not None:setup(e)
    with effect_schedule(effect_policy),reference() if legacy else nullcontext():
        try:value=('value',e.run(source))
        except LanguageError as failure:value=('error',failure.record())
        except ExecutionControl as failure:value=('control',type(failure).__name__,str(failure))
    return value,e.steps,effects,trace,debug,e.depth,len(e.frames)


class AccessExecutionFrames(unittest.TestCase):
    def check(self,source,expected,**kwargs):
        a=snapshot(source,**kwargs);b=snapshot(source,legacy=True,**kwargs)
        self.assertEqual(a[0],('value',expected));self.assertEqual(a,b)
    def test_exact_original_all14_algorithms_and_five_source_kinds_have_witnesses(self):
        source='''class C{var x=40;fn __getattr__(s,n){42};fn __setattr__(s,n,v){default_setattr(s,n,v);null}
        fn __delattr__(s,n){default_delattr(s,n);null};fn __dir__(s){["x"]}
        fn __getitem__(s,k){k};fn __setitem__(s,k,v){default_setattr(s,"x",v);null}}
        let c=C();c.x=41;let a=c.x;c[0]=41;c[0]+=1;delattr(c,"x");[a,c.missing,dir(c),[10,20][index(1)],c[::2],c.["x"]]'''
        with reference() as reached:result=snapshot(source)[0]
        self.assertEqual(result[0],'value')
        self.assertEqual(set(k for k,v in reached.items()if v),set(reached))
    def test_attribute_default_fallback_and_augmented_effect_order(self):
        self.check('''var log=[];class C{var x=40;fn __getattribute__(s,n){log=append(log,"get");default_getattr(s,n)}
        fn __getattr__(s,n){42};fn __setattr__(s,n,v){log=append(log,"set");default_setattr(s,n,v);null}}
        let c=C();c.x+=2;[default_getattr(c,"x"),c.missing,log]''',[42,42,['get','set','get']])
    def test_fallback_causal_context_is_not_a_language_catch(self):
        self.check('''class C{fn __getattribute__(s,n){throw AttributeError("first")}
        fn __getattr__(s,n){throw ValueError("second")}}
        try{C().x}catch e{[e.code,current_error().context.code]}''',['value_error','attribute_error'])
    def test_non_attribute_failure_does_not_fallback(self):
        self.check('var n=0;class C{fn __getattribute__(s,k){throw ValueError("bad")}fn __getattr__(s,k){n+=1;42}}try{C().x}catch e{null};n',0)
    def test_raw_default_delegation_preserves_aliases_and_field_contracts(self):
        self.check('class C{var x:Int=1}let c=C();[default_setattr(c,"x",42),default_getattr(c,"x"),try{default_setattr(c,"x","bad")}catch e{e.code}]',[42,42,'runtime_error'])
    def test_descriptor_get_set_delete_and_nested_binding(self):
        self.check('''var log=[];class D{fn __get__(s,o,c){log=append(log,"get");42}
        fn __set__(s,o,v){log=append(log,v);null};fn __delete__(s,o){log=append(log,"delete");null}}
        class C{let x=D()};let c=C();let x=c.x;c.x=41;delattr(c,"x");[x,log]''',[42,['get',41,'delete']])
    def test_super_and_metaclass_raw_selection_are_preserved(self):
        self.check('class A{let x=40}class B(A){fn __getattribute__(s,n){42}}let b=B();[b.x,default_getattr(b,"x"),super(B,b).x]',[42,40,40])
        self.check('class M(Type){fn __getattr__(c,n){42}fn __getitem__(c,k){k+1}}class C(metaclass=M){}[C.x,C[41]]',[42,42])
    def test_default_class_getitem_and_meta_precedence(self):
        self.check('''class M(Type){fn __getitem__(c,k){k+2}}class C(metaclass=M){@classmethod
        fn __class_getitem__(c,k){0}}class D{@classmethod
        fn __class_getitem__(c,k){k+1}}[C[40],D[41]]''',[42,42])
    def test_cold_getter_task_and_generator_are_not_started(self):
        self.check('''var n=0;class C{async fn __getattr__(s,k){n+=1;42}}
        let pending=C().x;let before=n;[before,await pending,n]''',[0,42,1])
        self.check('class C{fn* __getattr__(s,n){yield n}}collect(C().hello)',['hello'])
    def test_writes_require_synchronous_null_without_starting_cold_code(self):
        for flavor,body in [('async fn','null'),('fn*','yield 1'),('fn','42')]:
            source='var n=0;class C{'+flavor+' __setattr__(s,k,v){n+=1;'+body+'}}try{C().x=42}catch e{null};n'
            self.check(source,1 if flavor=='fn' else 0)
    def test_directory_generator_sorts_duplicates_and_releases_borrow(self):
        self.check('class C{fn* __dir__(s){yield "z";yield "a";yield "a"}}dir(C())',['a','a','z'])
        self.check('let it=iter(["a",1,"z"]);class C{fn __dir__(s){it}}[try{dir(C())}catch e{e.code},next(it),next(it)]',['argument_error',{'done':False,'value':'z'},{'done':True,'value':None}])
    def test_directory_async_source_is_rejected_cold(self):
        self.check('var n=0;class C{async fn __dir__(s){n+=1;["x"]}}try{dir(C())}catch e{null};n',0)
    def test_exact_index_protocol_and_slice_conversion_order(self):
        self.check('''var log=[];class P{var n=0;fn __init__(s,n){s.n=n;null}
        fn __index__(s){log=append(log,s.n);s.n}}
        let a=P(1);let b=P(4);let c=P(2);let value=[0,1,2,3,4][a:b:c];[value,log]''',[[1,3],[1,4,2]])
    def test_tuple_scalar_keys_are_not_coerced_and_slice_keys_are(self):
        self.check('''var n=0;class P{fn __index__(s){n+=1;1}}let p=P();
        class C{fn __getitem__(s,k){[type_of(k[0]),type_of(k[1]),k[1].start]}}
        [C()[p,p::],n]''',[['Instance','Slice',1],1])
    def test_item_target_key_rhs_evaluate_once_in_order(self):
        self.check('''var log=[];class C{var x=40;fn __getitem__(s,k){log=append(log,"get");s.x}
        fn __setitem__(s,k,v){log=append(log,"set");s.x=v;null}}let c=C();
        fn t(){log=append(log,"target");c}fn k(){log=append(log,"key");0}
        fn r(){log=append(log,"rhs");2}t()[k()]+=r();[c.x,log]''',[42,['target','key','get','rhs','set']])
    def test_legacy_index_and_bracket_semantics_remain_distinct(self):
        self.check('[ [40,42].[1], [40,42][-1],try{[40,42].[-1]}catch e{e.code}]',[42,42,'runtime_error'])
    def test_index_bool_async_and_noninteger_results_are_rejected(self):
        for body in ['true','1.5','null','(async fn(){1})()']:
            source='class C{fn __index__(s){'+body+'}}try{index(C())}catch e{e.code}'
            self.check(source,'argument_error')
    def test_native_immutable_item_and_hashmap_boundary(self):
        self.check('let h=hash_set(hash_map(),"x",42);[h["x"],try{[1][0]=2}catch e{e.code}]',[42,'argument_error'])
    def test_pending_attribute_label_controls_real_callback(self):
        original=AttributeReadFrame.advance
        def advance(frame,cursor,value,error):
            if frame.phase=='hook':frame.label='answer'
            return original(frame,cursor,value,error)
        with patch.object(AttributeReadFrame,'advance',advance):self.assertEqual(snapshot('class C{fn __getattribute__(s,n){n}}C().x')[0],('value','answer'))
    def test_pending_descriptor_args_are_real_invocation_arguments(self):
        from chaos.objects import AccessorCall
        from chaos.execution_frames import Push,InvocationFrame
        e=Evaluator();function=e.run('fn(x){x+1}')
        frame=AccessResultFrame(AccessorCall(function,[1]));original=AccessResultFrame.advance;witness=[]
        def advance(frame,cursor,value,error):
            action=original(frame,cursor,value,error)
            if type(action) is Push and type(action.frame) is InvocationFrame:
                self.assertIs(action.frame.args,frame.args);frame.args[0]=41;witness.append(True)
            return action
        with patch.object(AccessResultFrame,'advance',advance):self.assertEqual(e.suspension.drive(ExecutionCursor(e,frame)),42)
        self.assertEqual(witness,[True])
    def test_pending_integer_result_is_authoritative(self):
        original=IntegerIndexFrame.advance
        def advance(frame,cursor,value,error):
            if frame.phase=='start' and type(frame.value) is int:frame.value=1
            return original(frame,cursor,value,error)
        with patch.object(IntegerIndexFrame,'advance',advance):self.assertEqual(snapshot('[40,42][0]')[0],('value',42))
    def test_pending_slice_parts_control_result(self):
        original=IndexResolveFrame.advance
        def advance(frame,cursor,value,error):
            if frame.phase=='part' and frame.index==1:frame.parts[0]=1
            return original(frame,cursor,value,error)
        with patch.object(IndexResolveFrame,'advance',advance):self.assertEqual(snapshot('[40,41,42][0:3]')[0],('value',[41,42]))
    def test_pending_source_key_is_used_in_actual_read(self):
        original=ObjectAccessEvaluationFrame.advance
        def advance(frame,cursor,value,error):
            if frame.phase=='key' and frame.node[0]=='subscript':value=1
            return original(frame,cursor,value,error)
        with patch.object(ObjectAccessEvaluationFrame,'advance',advance):self.assertEqual(snapshot('[40,42][0]')[0],('value',42))
    def test_pending_attribute_write_item_is_not_a_monitoring_copy(self):
        original=AttributeWriteFrame.advance
        def advance(frame,cursor,value,error):
            if frame.phase=='hook':frame.item=42
            return original(frame,cursor,value,error)
        with patch.object(AttributeWriteFrame,'advance',advance):self.assertEqual(snapshot('class C{var x=0}let c=C();c.x=1;c.x')[0],('value',42))
    def test_pending_directory_accumulator_controls_final_names(self):
        original=AttributeDirectoryFrame.advance
        def advance(frame,cursor,value,error):
            if frame.phase=='step' and value is not None and value['done']:frame.result.append('extra')
            return original(frame,cursor,value,error)
        with patch.object(AttributeDirectoryFrame,'advance',advance):self.assertEqual(snapshot('class C{fn __dir__(s){["x"]}}dir(C())')[0],('value',['extra','x']))
    def test_all15_actual_frame_algorithms_are_executed(self):
        from contextlib import ExitStack
        seen=set()
        with ExitStack() as stack:
            for kind in ACCESS_FRAMES:
                original=kind.advance
                def advance(frame,cursor,value,error,_kind=kind,_original=original):
                    seen.add(_kind);return _original(frame,cursor,value,error)
                stack.enter_context(patch.object(kind,'advance',advance))
            result=snapshot('''class C{var x=40;fn __getattr__(s,n){42};fn __dir__(s){["x"]}
            fn __getitem__(s,k){k};fn __setitem__(s,k,v){default_setattr(s,"x",v);null}}
            let c=C();c.x=41;c[0]=41;c[0]+=1;delattr(c,"x");[c.missing,dir(c),[40,42][index(1)],c[::2],c.["x"]]''')[0]
        self.assertEqual(result[0],'value');self.assertEqual(seen,set(ACCESS_FRAMES))
    def test_pending_item_target_and_key_fields_drive_dispatch(self):
        original=ItemOperationFrame.advance
        def advance(frame,cursor,value,error):
            if frame.phase=='start' and type(frame.target) is list:frame.target=[40,42];frame.key=1
            return original(frame,cursor,value,error)
        with patch.object(ItemOperationFrame,'advance',advance):self.assertEqual(snapshot('[1][0]')[0],('value',42))
    def factories(self):
        boundary=SimpleNamespace(node=('index',None,None),env=None,options={})
        return [lambda:AttributeHookFrame(None,None,'x'),lambda:AttributeReadFrame(None,'x'),lambda:AttributeWriteFrame(None,'x'),lambda:AttributeDirectoryFrame(None,None),lambda:AccessResultFrame(None),lambda:ItemKeyFrame(None,None),lambda:LegacyIndexFrame(None,None),lambda:ScopedGetMemberFrame(None,'x'),lambda:ScopedSetMemberFrame(None,'x',None),lambda:ScopedDeleteMemberFrame(None,'x'),lambda:IntegerIndexFrame(None),lambda:IndexResolveFrame(IndexRequest('slice',(None,None,None))),lambda:ItemInvokeFrame(None,ItemRequest('get',None,None)),lambda:ItemOperationFrame(ItemRequest('get',None,None)),lambda:ObjectAccessEvaluationFrame(boundary)]
    def test_every_declared_root_rejects_foreign_values(self):
        from chaos.capture_graph import seal
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        self.assertEqual(len(self.factories()),len(ACCESS_FRAMES))
        for factory in self.factories():
            for field in factory().ROOTS+('previous_authority',):
                frame=factory();setattr(frame,field,foreign)
                with self.subTest(frame=type(frame).__name__,field=field),self.assertRaises(LanguageError) as caught:seal(e,(ExecutionCursor(e,frame),),None)
                self.assertEqual(caught.exception.code,'ownership_error')
        frame=AttributeReadFrame(None,'x');frame.previous_authority=foreign
        with self.assertRaises(LanguageError) as caught:seal(e,(ExecutionCursor(e,frame),),frozenset())
        self.assertEqual(caught.exception.code,'ownership_error')
        for values in ((foreign,),[foreign],((foreign,),)):
            with self.assertRaises(LanguageError):seal(e,(AccessFrameRequest('read',values),),None)
    def test_capture_bounds_and_capability_updates_are_atomic(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();frame=AccessResultFrame(None);frame.args=([None]*10000,[None]*10000)
        with self.assertRaises(ResourceLimit):seal(e,(ExecutionCursor(e,frame),),None)
        deep=None
        for _ in range(102):deep=[deep]
        frame.args=(deep,)
        with self.assertRaises(ResourceLimit):seal(e,(ExecutionCursor(e,frame),),None)
        token=NextDescriptor(e,None);value=Native('next',lambda:None,e,generic=token,captures=())
        frame.args=(Evaluator().run('fn(){42}'),value)
        with self.assertRaises(LanguageError):seal(e,(ExecutionCursor(e,frame),),frozenset())
        self.assertIsNone(token.capabilities);self.assertIsNone(value.capabilities)
    def test_frame_roots_do_not_invoke_host_storage_hooks(self):
        from chaos.capture_graph import seal
        class Bomb:
            def __iter__(self):raise AssertionError('host iterate')
            def __getattr__(self,key):raise AssertionError('host getattr')
        e=Evaluator();frame=AccessResultFrame(Bomb());frame.args=Bomb()
        seal(e,(ExecutionCursor(e,frame),AccessFrameRequest('access',Bomb())),None)
    def test_callee_argument_ceiling_preserves_unrelated_outer_value(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();inside=NextDescriptor(e,None);outside=NextDescriptor(e,None)
        arg=Native('arg',lambda:None,e,generic=inside,captures=());outer=Native('outer',lambda:None,e,generic=outside,captures=())
        frame=AccessResultFrame(outer);frame.args=[arg];frame.function=Native('callee',lambda x:x,e,capabilities=frozenset())
        seal(e,(ExecutionCursor(e,frame),),frozenset({'outside'}))
        self.assertEqual(inside.capabilities,frozenset());self.assertEqual(outside.capabilities,frozenset({'outside'}))
    def test_restoration_runner_identity_does_not_recapture_unrelated_caller(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.language import Env
        e=Evaluator();token=NextDescriptor(e,None);outer=Native('outer',lambda:None,e,generic=token,captures=())
        with active(e,frozenset({'outside','wait'})) as runner:
            caller=Env();caller.bind('outside',outer);runner.frames=[{'env':caller}]
            frame=AttributeReadFrame(None,'x');cursor=ExecutionCursor(e,frame)
            frame.enter_authority(e,frozenset({'wait'}));seal(e,(cursor,),frozenset({'wait'}))
            self.assertIsNone(token.capabilities);self.assertIsNone(outer.capabilities)
            frame.close(cursor);self.assertEqual(runner.capabilities,frozenset({'outside','wait'}))
    def test_foreign_invalid_and_stale_restore_anchors_are_rejected(self):
        from chaos.capture_graph import seal
        from chaos.suspension import Runner
        class Bomb:
            def __getattr__(self,key):raise AssertionError('host getattr')
        e=Evaluator();foreign=Evaluator();foreign_runner=Runner(ExecutionCursor(foreign,AccessResultFrame(None)))
        for value in (foreign_runner,Bomb(),foreign.run('fn(){42}')):
            frame=AttributeReadFrame(None,'x');frame.authority_runner=value;frame.authority_epoch=e.suspension.epoch
            with self.subTest(value=type(value).__name__),self.assertRaises(LanguageError) as caught:seal(e,(ExecutionCursor(e,frame),),None)
            self.assertEqual(caught.exception.code,'ownership_error')
        with active(e,None):
            frame=AttributeReadFrame(None,'x');cursor=ExecutionCursor(e,frame);frame.enter_authority(e,None)
            frame.authority_epoch-=1
            with self.assertRaises(LanguageError):seal(e,(cursor,),None)
            frame.authority_epoch+=1;frame.close(cursor)
    def test_force_close_restores_exact_runner_authority_and_fallback_context(self):
        from chaos.suspension import CHECKPOINT,Effect
        e=Evaluator(tool_handler=lambda n,v:v)
        target=e.run('class C{fn __getattribute__(s,n){throw AttributeError("first")}fn __getattr__(s,n){tool("wait",42)}}C()')
        target.capabilities=frozenset({'wait'})
        with active(e,frozenset({'outside','wait'})) as runner:
            previous=runner.capabilities;context=e.error_context
            cursor=ensure_cursor(e,e._s_get_member(target,'x'));request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect);self.assertEqual(runner.capabilities,frozenset({'wait'}))
            cursor.close();self.assertIs(runner.capabilities,previous);self.assertIs(e.error_context,context)
            self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertEqual(runner.capture_frames,[])
    def test_force_close_directory_releases_borrowed_cursor_and_capture(self):
        from chaos.suspension import CHECKPOINT,Effect
        from chaos.attribute_protocols import directory
        e=Evaluator(tool_handler=lambda n,v:v)
        target=e.run('class I{fn __iter__(s){s}fn __next__(s){tool("wait",1);{done:false,value:"x"}}}class C{fn __dir__(s){I()}}C()')
        with active(e,frozenset({'wait'})) as runner:
            cursor=ensure_cursor(e,directory(e,target,lambda x:[]));request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect)
            directory_frame=next(f for f in cursor.frames if type(f) is AttributeDirectoryFrame)
            self.assertTrue(directory_frame.cursor.busy);cursor.close()
            self.assertFalse(directory_frame.cursor.busy);self.assertFalse(directory_frame.locked)
            self.assertEqual(runner.capture_frames,[]);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
    def test_unstarted_throw_preserves_failure_identity_and_ticks(self):
        from chaos import attribute_protocols,indexing
        for legacy in (False,True):
            e=Evaluator();failure=ResourceLimit('same')
            with reference() if legacy else nullcontext():
                values=[attribute_protocols.read(e,None,'x'),attribute_protocols.write(e,None,'x'),attribute_protocols.directory(e,None,None),indexing.integer_index(e,None),indexing.resolve_index(e,IndexRequest('index',(None,))),e._s_access_result(None),e._s_item_key(None,None)]
                for iterator in values:
                    before=e.steps
                    with self.assertRaises(ResourceLimit) as caught:iterator.throw(failure)
                    self.assertIs(caught.exception,failure);self.assertEqual(e.steps,before)
    def test_small_budget_and_cancel_thresholds_match_original(self):
        samples=['class C{var x=40;fn __getattr__(s,n){42}}let c=C();c.x+=2;c.x','class C{fn __getitem__(s,k){tool("read",k+1)}}async fn f(){C()[41]}await f()','class C{fn __dir__(s){["x","a"]}}dir(C())']
        for source in samples:
            total=snapshot(source,legacy=True)[1]
            for limit in range(total+2):
                with self.subTest(limit=limit,source=source):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
            for cancel in range(total+2):
                with self.subTest(cancel=cancel,source=source):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))
    def test_real_first_wait_access_effects_match_complete_original_trace(self):
        source='''class D{fn __get__(s,o,c){tool("bind",1);fn(k){tool("read",k+1)}}}class C{let __getitem__=D()}
        async fn f(){C()[41]}await f()'''
        self.check(source,42,effect_policy='first_wait')
    def test_all64_pending_task_capture_charge_alignments_match_original(self):
        from chaos.capture_graph import seal
        from chaos.suspension import Task
        for padding in range(64):
            witnesses=[]
            def setup(e):
                def capture(task):
                    self.assertIs(type(task),Task);self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                    seen=[];original=__import__('chaos.execution_frames',fromlist=['frame_roots']).frame_roots
                    def roots(frame):seen.append(type(frame));return original(frame)
                    with patch('chaos.execution_frames.frame_roots',roots):seal(e,(task,),None)
                    witnesses.append(set(seen));return None
                e.env.bind('capture_probe164',Native('capture_probe164',capture,e,captures=()))
            source='''let padding=range(PADDING);let ready=channel(1);
            class C{fn __getattr__(s,n){spawn(channel_send(ready,0));tool("access_hold",42)}}
            async fn work(){C().missing}let task=work();spawn(task);await channel_receive(ready);
            capture_probe164(task);await task'''.replace('PADDING',str(padding))
            with self.subTest(padding=padding):
                a=snapshot(source,setup=setup,effect_policy='first_wait');b=snapshot(source,legacy=True,setup=setup,effect_policy='first_wait')
                self.assertEqual(a[0],('value',42));self.assertEqual(a,b)
                self.assertIn(AttributeReadFrame,witnesses[0]);self.assertNotIn(AttributeReadFrame,witnesses[1])


        # Independently preserve every capture-charge alignment while augmented
        # assignment is pending inside its existing public operator gateway.
        from chaos.execution_operators import OperatorInplaceFrame
        for padding in range(64):
            witnesses=[]
            def setup_augmented(e):
                def capture(task):
                    self.assertIs(type(task),Task);self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                    seen=[];original=__import__('chaos.execution_frames',fromlist=['frame_roots']).frame_roots
                    def roots(frame):seen.append(type(frame));return original(frame)
                    with patch('chaos.execution_frames.frame_roots',roots):seal(e,(task,),None)
                    witnesses.append(set(seen));return None
                e.env.bind('capture_probe164',Native('capture_probe164',capture,e,captures=()))
            source='''let padding=range(PADDING);let ready=channel(1);
            class C{fn __iadd__(s,n){spawn(channel_send(ready,0));tool("access_hold",n+40)}}
            async fn work(){var value=C();value+=2;value}let task=work();spawn(task);await channel_receive(ready);
            capture_probe164(task);await task'''.replace('PADDING',str(padding))
            with self.subTest(augmented=True,padding=padding):
                a=snapshot(source,setup=setup_augmented,effect_policy='first_wait');b=snapshot(source,legacy=True,setup=setup_augmented,effect_policy='first_wait')
                self.assertEqual(a[0],('value',42));self.assertEqual(a,b)
                self.assertIn(ObjectAccessEvaluationFrame,witnesses[0]);self.assertNotIn(ObjectAccessEvaluationFrame,witnesses[1])
                self.assertIn(OperatorInplaceFrame,witnesses[0]);self.assertIn(OperatorInplaceFrame,witnesses[1])


if __name__=='__main__':unittest.main()
