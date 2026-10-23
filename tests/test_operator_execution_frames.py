"""Authoritative operator state and exact original protocol differential."""
import copy
import threading
import unittest
from contextlib import contextmanager,nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native,TailCall
from chaos.execution_frames import ExecutionCursor,Done,Push,ProtocolBridge,frame_roots,ensure_cursor
from chaos.execution_operators import (OPERATOR_FRAMES,OperatorFrameRequest,OperatorInvokeFrame,
    OperatorCompareFrame,OperatorEqualFrame,OperatorBinaryFrame,OperatorInplaceFrame,OperatorOperationFrame)
from _effect_schedule158 import effect_schedule
from _operator_reference160 import reference


def snapshot(source,legacy=False,limit=100000,cancel=None,setup=None,effect_policy='already_done'):
    effects=[];trace=[];debug=[]
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)))
        return payload['value'] if kind=='tool' else payload.get('input')
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda name,value:value,effect_handler=effect,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup is not None:setup(e)
    with effect_schedule(effect_policy),reference() if legacy else nullcontext():
        try:value=('value',e.run(source))
        except LanguageError as error:value=('error',error.record())
        except ExecutionControl as error:value=('control',type(error).__name__,str(error))
    return value,e.steps,effects,trace,debug,e.depth,len(e.frames)


@contextmanager
def active(e,caps=None):
    e.suspension.begin()
    def cold():
        if False:yield None
    task=e.suspension.task(cold());runner=task.runner
    runner.capabilities=caps;runner.condition_state=e.suspension.condition_state;runner.control_state=e.suspension.control_state
    runner.context_state=e.suspension.context_state;runner.decimal_state=e.suspension.decimal_state
    e.suspension.current=runner
    try:yield runner
    finally:e.suspension.current=None;e.suspension.finish()


class OperatorExecutionFrames(unittest.TestCase):
    def check(self,source,expected,**kwargs):
        modern=snapshot(source,**kwargs);old=snapshot(source,legacy=True,**kwargs)
        self.assertEqual(modern[0],('value',expected));self.assertEqual(modern,old)
    def test_all_six_original_algorithms_have_actual_oracle_witnesses(self):
        source='class C{fn __iadd__(s,x){NotImplemented}fn __add__(s,x){x+1}fn __eq__(s,x){true}}var c=C();c+=41;[c,C()==1,length([1])]'
        with reference() as reached:self.assertEqual(snapshot(source)[0],('value',[42,True,1]))
        self.assertEqual(set(name for name,count in reached.items()if count),{'invoke','compare_hooks','equal','binary','inplace','operation'})
    def test_primitives_preserve_strict_rules_and_constant_optimization(self):self.check('[1+2,7-2,3*4,7/2,7%2,2**3,7&3,4<<2,-8>>2]',[3,5,12,3.5,1,8,3,16,-2])
    def test_forward_and_new_subtype_reverse_order(self):self.check('var e=[];class A{fn __add__(s,x){e=append(e,"a");42}}class B(A){fn __radd__(s,x){e=append(e,"b");NotImplemented}}[A()+B(),e]',[42,['b','a']])
    def test_inherited_reverse_arithmetic_does_not_preempt_forward(self):self.check('var e=[];class A{fn __add__(s,x){e=append(e,"a");42}fn __radd__(s,x){e=append(e,"r");41}}class B(A){}[A()+B(),e]',[42,['a']])
    def test_comparison_inherited_reverse_precedes_forward(self):self.check('var e=[];class A{fn __lt__(s,x){e=append(e,"lt");true}fn __gt__(s,x){e=append(e,"gt");false}}class B(A){}[A()<B(),e]',[False,['gt']])
    def test_same_class_arithmetic_suppresses_reverse(self):self.check('var e=[];class C{fn __add__(s,x){e=append(e,"add");NotImplemented}fn __radd__(s,x){e=append(e,"rev");42}}try{C()+C()}catch x{null};e',['add'])
    def test_not_equal_hook_and_fallback_equality(self):self.check('class A{fn __ne__(s,x){true}fn __eq__(s,x){true}}class B{fn __ne__(s,x){NotImplemented}fn __eq__(s,x){true}}[A()!=1,B()!=1]',[True,False])
    def test_notimplemented_identity_and_marker_record(self):self.check('class C{fn __add__(s,x){{"$notImplemented":true}}}[C()+1,NotImplemented=={"$notImplemented":true}]',[{'$notImplemented':True},False])
    def test_descriptor_binding_is_a_real_effect_boundary(self):self.check('class D{fn __get__(s,o,c){tool("bind",1);fn(x){tool("call",x+1)}}}class C{let __add__=D()}async fn f(){C()+41}await f()',42)
    def test_static_and_class_method_operator_binding(self):self.check('class C{\n@staticmethod\nfn __add__(x){x+1}\n@classmethod\nfn __radd__(cls,x){x+2}\n}[C()+41,40+C()]',[42,42])
    def test_recursive_equality_visits_repeated_effectful_aliases(self):self.check('var n=0;class C{fn __eq__(s,x){n+=1;true}}let a=C();[[a,a]==[a,a],n]',[True,2])
    def test_primitive_shared_dag_and_cycle_equality(self):
        def setup(e):
            a=[];b=[];a.append(a);b.append(b)
            e.env.bind('a',a);e.env.bind('b',b)
        self.check('a==b',True,setup=setup)
        self.check('let x=[1,2];let y=[1,2];[x,x]==[y,y]',True)
    def test_record_and_tuple_ordered_equality(self):self.check('[{a:1,b:[2]}=={a:1,b:[2]},(1,2)==(1,2),[1]==(1,),{a:1}=={b:1}]',[True,True,False,False])
    def test_set_and_hash_map_dependencies_remain_explicit(self):self.check('let h=hash_set(hash_map(),"x",42);[contains({1,2},1),{1,2}=={2,1},length(h),contains(h,"x"),h==h]',[True,True,1,True,True])
    def test_inplace_decline_then_binary_fallback(self):self.check('var e=[];class C{fn __iadd__(s,x){e=append(e,"i");NotImplemented}fn __add__(s,x){e=append(e,"a");x+1}}var c=C();c+=41;[c,e]',[42,['i','a']])
    def test_inplace_exception_never_tries_binary(self):self.check('var e=[];class C{fn __iadd__(s,x){throw "stop"}fn __add__(s,x){e=append(e,"bad");42}}var c=C();try{c+=1}catch x{x.value};e',[])
    def test_length_bool_contains_and_all_unary_operations(self):self.check('class C{fn __len__(s){2}fn __bool__(s){false}fn __contains__(s,x){x==42}fn __neg__(s){-42}fn __pos__(s){42}fn __abs__(s){42}fn __invert__(s){41}}let c=C();[length(c),bool(c),contains(c,42),-c,+c,abs(c),~c]',[2,False,True,-42,42,42,41])
    def test_bool_length_fallback_and_context_primitives(self):self.check('class C{fn __len__(s){0}}let v=context_var("v",1);let c=context();[bool(C()),length(c),contains(c,v)]',[False,0,False])
    def test_contains_preserves_live_native_list_mutation_order(self):
        def setup(e):
            values=[]
            e.env.bind('items',values)
            e.env.bind('append_probe160',Native('append_probe160',lambda value:values.append(value),e))
        self.check('var n=0;class C{fn __eq__(s,x){n+=1;if n==1{append_probe160(42)}else{null};false}}append_probe160(C());[contains(items,42),n]',[True,1],setup=setup)
    def test_comparison_and_boolean_protocol_results_are_strict(self):
        for source in ['class C{fn __eq__(s,x){1}}C()==1','class C{fn __lt__(s,x){null}}C()<1','class C{fn __bool__(s){NotImplemented}}bool(C())','class C{fn __len__(s){true}}length(C())','class C{fn __contains__(s,x){NotImplemented}}contains(C(),1)']:
            with self.subTest(source=source):
                a=snapshot(source);self.assertEqual(a,snapshot(source,legacy=True));self.assertEqual(a[0][0],'error')
    def test_cold_tasks_generators_and_multiple_result_primary(self):self.check('var n=0;class C{async fn __add__(s,x){n+=1;x+1}fn* __neg__(s){yield 42}fn __eq__(s,x){return_values(true,99)}}let t=C()+41;let before=n;[before,await t,n,collect(-C()),C()==1]',[0,42,1,[42],True])
    def test_primitive_error_codes_and_resource_bounds_match(self):
        for source in ['true+1','1<<-1','1<<14000','pow(2,14001)','pow(-1,0.5)','class C{fn __len__(s){-1}}length(C())']:
            with self.subTest(source=source):self.assertEqual(snapshot(source),snapshot(source,legacy=True))
    def test_pending_invoke_args_are_actual_resumed_arguments(self):
        original=OperatorInvokeFrame.step
        def step(frame,cursor,value,error):
            if frame.phase=='access' and frame.method=='__add__':frame.args[0]=41
            return original(frame,cursor,value,error)
        with patch.object(OperatorInvokeFrame,'step',step):self.assertEqual(snapshot('class C{fn __add__(s,x){x+1}}C()+1')[0],('value',42))
    def test_pending_equal_todo_is_authoritative(self):
        original=OperatorEqualFrame.step
        def step(frame,cursor,value,error):
            if frame.phase=='hooks':frame.todo=[(42,42,None)]
            return original(frame,cursor,value,error)
        with patch.object(OperatorEqualFrame,'step',step):self.assertEqual(snapshot('class C{fn __eq__(s,x){true}}[C(),1]==[C(),2]')[0],('value',True))
    def test_pending_binary_candidate_queue_controls_fallback(self):
        original=OperatorBinaryFrame.step
        def step(frame,cursor,value,error):
            if frame.phase=='invoke':frame.candidates=[]
            return original(frame,cursor,value,error)
        with patch.object(OperatorBinaryFrame,'step',step):
            a=snapshot('class A{fn __add__(s,x){NotImplemented}}class B{fn __radd__(s,x){42}}A()+B()')
        self.assertEqual(a[0][0],'error');self.assertEqual(a[0][1]['code'],'argument_error')
    def test_inplace_and_operation_fields_drive_real_computation(self):
        original=OperatorInplaceFrame.step
        def step(frame,cursor,value,error):
            if frame.phase=='start':frame.right=41
            return original(frame,cursor,value,error)
        with patch.object(OperatorInplaceFrame,'step',step):self.assertEqual(snapshot('class C{fn __iadd__(s,x){x+1}}var c=C();c+=1;c')[0],('value',42))
        original=OperatorOperationFrame.step
        def operation(frame,cursor,value,error):
            if frame.phase=='start' and frame.operation=='length':frame.operands=([1,2],)
            return original(frame,cursor,value,error)
        with patch.object(OperatorOperationFrame,'step',operation):self.assertEqual(snapshot('length([1])')[0],('value',2))
    def test_every_declared_root_and_request_rejects_foreign_values(self):
        from chaos.capture_graph import seal
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        factories=[lambda:OperatorInvokeFrame(None,'__add__',[]),lambda:OperatorCompareFrame('==',None,None),lambda:OperatorEqualFrame(None,None),lambda:OperatorBinaryFrame('+',None,None),lambda:OperatorInplaceFrame('+',None,None),lambda:OperatorOperationFrame('length',([],))]
        for factory in factories:
            for field in factory().ROOTS:
                frame=factory();payload=foreign
                if field in ('todo','candidates'):payload=[(foreign,None,None)]
                elif field in ('args','operands','current','pair'):payload=(foreign,)
                setattr(frame,field,payload)
                with self.subTest(frame=type(frame).__name__,field=field),self.assertRaises(LanguageError):seal(e,(ExecutionCursor(e,frame),),None)
        for values in ((foreign,),[foreign],((foreign,),)):
            with self.assertRaises(LanguageError):seal(e,(OperatorFrameRequest('operation',values),),None)
    def test_root_bounds_and_atomic_capability_updates(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        frame=OperatorInvokeFrame(None,'x',([None]*10000,[None]*10000))
        with self.assertRaises(ResourceLimit):seal(e,(ExecutionCursor(e,frame),),None)
        deep=None
        for _ in range(102):deep=[deep]
        frame.args=(deep,)
        with self.assertRaises(ResourceLimit):seal(e,(ExecutionCursor(e,frame),),None)
        token=NextDescriptor(e,None);value=Native('next',lambda:None,e,generic=token,captures=())
        frame.args=(foreign,value)
        with self.assertRaises(LanguageError):seal(e,(ExecutionCursor(e,frame),),frozenset())
        self.assertIsNone(token.capabilities);self.assertIsNone(value.capabilities)
    def test_callee_argument_ceiling_does_not_narrow_unrelated_receiver(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator()
        with active(e,frozenset({'outside'})) as runner:
            inside=NextDescriptor(e,None);outside=NextDescriptor(e,None)
            arg=Native('arg',lambda:None,e,generic=inside,captures=());receiver=Native('outer',lambda:None,e,generic=outside,captures=())
            frame=OperatorInvokeFrame(receiver,'x',[arg]);frame.function=Native('callee',lambda x:x,e,capabilities=frozenset())
            cursor=ExecutionCursor(e,frame);seal(e,(cursor,),frozenset({'outside'}))
            self.assertEqual(inside.capabilities,frozenset());self.assertEqual(outside.capabilities,frozenset({'outside'}))
            self.assertEqual(runner.capabilities,frozenset({'outside'}));seal(e,(cursor,),frozenset());self.assertEqual(outside.capabilities,frozenset())
    def test_pending_descriptor_request_is_a_real_root_after_slot_replacement(self):
        from chaos.capture_graph import seal
        from chaos.objects import AccessorCall
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        frame=OperatorInvokeFrame(None,'__add__',[])
        frame.access_request=AccessorCall(foreign,[])
        with self.assertRaises(LanguageError):seal(e,(ExecutionCursor(e,frame),),None)
    def test_root_walk_never_invokes_host_storage_hooks(self):
        from chaos.capture_graph import seal
        class Bomb:
            def __iter__(self):raise AssertionError('host iterate')
            def __getattr__(self,key):raise AssertionError('host getattr')
        e=Evaluator();frame=OperatorInvokeFrame(None,'x',Bomb());seal(e,(ExecutionCursor(e,frame),OperatorFrameRequest('invoke',Bomb())),None)
    def test_force_close_restores_actual_invocation_authority_and_frames(self):
        from chaos.operators import invoke
        from chaos.suspension import Effect,CHECKPOINT
        e=Evaluator(tool_handler=lambda name,value:value)
        receiver=e.run('class C{fn __add__(s,x){tool("wait",x)}}C()')
        from chaos.objects import member_value
        member_value(receiver.klass.members['__add__']).capabilities=frozenset({'wait'})
        with active(e,frozenset({'outer','wait'})) as runner:
            caps=runner.capabilities;cursor=ensure_cursor(e,invoke(e,receiver,'__add__',[41]))
            request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect)
            self.assertEqual(runner.capabilities,frozenset({'wait'}))
            cursor.close();self.assertIs(runner.capabilities,caps);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
    def test_unstarted_throw_preserves_identity_and_does_not_tick(self):
        from chaos import operators
        for legacy in (False,True):
            e=Evaluator();failure=ResourceLimit('same')
            with reference() if legacy else nullcontext():
                for iterator in [operators.invoke(e,None,'x',[]),operators.compare_hooks(e,'==',1,1),operators.equal(e,1,1),operators.binary(e,'+',1,1),operators.inplace(e,'+',1,1),operators.operation(e,'length',([],))]:
                    before=e.steps
                    with self.assertRaises(ResourceLimit) as caught:iterator.throw(failure)
                    self.assertIs(caught.exception,failure);self.assertEqual(e.steps,before)
    def test_every_small_budget_and_cancel_threshold_matches_original(self):
        samples=['class C{fn __iadd__(s,x){NotImplemented}fn __add__(s,x){x+1}}var c=C();c+=41;c','class C{fn __eq__(s,x){true}}[[C(),1],2]==[[C(),1],2]','class C{fn __contains__(s,x){tool("contains",true)}}async fn f(){contains(C(),42)}await f()']
        for source in samples:
            total=snapshot(source,legacy=True)[1]
            for limit in range(total+2):
                with self.subTest(limit=limit,source=source):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
            for cancel in range(total+2):
                with self.subTest(cancel=cancel,source=source):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))
    def test_all64_real_pending_task_capture_charge_alignments_match_original(self):
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
                e.env.bind('capture_probe160',Native('capture_probe160',capture,e,captures=()))
            source='''let padding=range(PADDING);let ready=channel(1);
              class C{fn __add__(s,x){spawn(channel_send(ready,0));tool("operator_hold",x+1)}}
              async fn work(){C()+41}let task=work();spawn(task);await channel_receive(ready);
              capture_probe160(task);await task'''.replace('PADDING',str(padding))
            with self.subTest(padding=padding):
                a=snapshot(source,setup=setup,effect_policy='first_wait');b=snapshot(source,legacy=True,setup=setup,effect_policy='first_wait')
                self.assertEqual(a[0],('value',42));self.assertEqual(a,b)
                self.assertIn(OperatorInvokeFrame,witnesses[0]);self.assertNotIn(OperatorInvokeFrame,witnesses[1])


if __name__=='__main__':unittest.main()
