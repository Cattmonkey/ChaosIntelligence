"""Actual held host Futures and the original charged capture walk at all offsets."""
import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,LanguageError,serializable
from chaos.multiple_values import Completion
from chaos.execution_multiple_values import MultipleValueReceiveFrame,MultipleValueResolveFrame
from _multiple_value_reference220 import reference,observe
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
from test_callable_execution_frames import owned_frame

BODIES={
 'list':'multiple_value_list(fn(){remember_effect("producer_hold");tool("producer_hold",0);return_values(40,pad)})',
 'prog1':'multiple_value_prog1(fn(){return_values(40,pad)},fn(){remember_effect("after_hold");tool("after_hold",2)})',
 'call':'multiple_value_call(fn(*xs){length(xs)},fn(){return_values(40,pad)},fn(){remember_effect("next_hold");tool("next_hold",2);return_values(2)})',
 'bind':'multiple_value_bind(3,fn(){return_values(40,pad)},fn(a,b,c){remember_effect("consumer_hold");tool("consumer_hold",2);[a,length(b),c]})',
}

def pending(test,padding,operation,legacy=False,poison=False,borrowed=False,cancel=False):
    effects=[];trace=[];debug=[];roots=[];tasks=[];seen=[];observations=[];base=[None];retained=[]
    def effect(kind,payload,event):effects.append((kind,copy.deepcopy(payload)));return payload['value']
    e=Evaluator(step_limit=50000,timeout=30,tool_handler=lambda n,v:v,effect_handler=effect)
    def remember(task,pad,child=None):roots.append((task,pad,child));base[0]=e.steps;e.step_limit=e.steps+50000;trace.clear();debug.clear()
    def remember_effect(name):tasks.append((e.suspension.current.task,name))
    for name,fn in [('remember',remember),('remember_effect',remember_effect)]:e.env.bind(name,Native(name,fn,e,captures=()))
    def traced(row):
        if base[0]is not None:
            row=copy.deepcopy(row)
            if type(row.get('step'))is int:row['step']-=base[0]
            trace.append(row)
    e.trace_handler=traced;e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps-base[0]))if base[0]is not None else None
    seal=observed_seal()
    body=BODIES[operation]
    source='let padding=range(PADDING);async fn work(pad){BODY};let task=work(padding);remember(task,padding);spawn(task);await task'
    if borrowed:
        source='let padding=range(PADDING);let child=spawn((async fn(){remember_effect("borrowed_hold");tool("borrowed_hold",40)})());async fn work(pad,borrowed){multiple_value_prog1(fn(){return_values(40,pad)},async fn(){await borrowed;return_values(99)})};let task=work(padding,child);remember(task,padding,child);spawn(task);FINAL'
    source=source.replace('PADDING',str(padding)).replace('BODY',body).replace('FINAL','await child'if cancel else'await task')
    with reference()if legacy else nullcontext(),observe()as(counts,frames),effect_schedule('first_wait')as schedule:
        original_wait=suspension.wait
        def waiting(futures,*args,**kwargs):
            futures=tuple(futures)
            try:
                for task,name in reversed(tasks):
                    future=task.runner.future
                    if future is None or future not in futures or future in seen:continue
                    seen.append(future);test.assertFalse(future.done());test.assertEqual(task.runner.state,'effect')
                    root,pad,child=roots[0];test.assertIs(root.capture_roots[1]['pad'],pad)
                    if borrowed:
                        test.assertIs(task,child);test.assertIs(root.runner.waiting,child);test.assertEqual(root.runner.state,'waiting')
                    else:test.assertIs(task,root)
                    if legacy:test.assertEqual(counts,{})
                    else:
                        frame=owned_frame(test,root.runner.iterator,MultipleValueResolveFrame)
                        test.assertIs(frame.owner,e);test.assertEqual(frame.epoch,e.suspension.epoch)
                        expected_phase='after'if operation=='prog1'or borrowed else'consumer'if operation=='bind'else'producer'
                        test.assertEqual(frame.phase,expected_phase);test.assertEqual(frame.operation,operation)
                        if operation in ('prog1','bind')or borrowed:
                            test.assertIs(type(frame.result),Completion);test.assertEqual(frame.result.items[0],40);test.assertIs(frame.result.items[1],pad)
                            test.assertIs(frame.items[1],pad)
                        if operation=='call':test.assertEqual(frame.index,1);test.assertEqual(frame.args[0],40);test.assertIs(frame.args[1],pad)
                        if operation=='bind':test.assertEqual(frame.args[0],40);test.assertIs(frame.args[1],pad);test.assertIsNone(frame.args[2])
                        if operation!='bind':
                            receive=owned_frame(test,root.runner.iterator,MultipleValueReceiveFrame)
                            test.assertEqual(receive.phase,'callback');test.assertIs(receive.owner,e)
                        if poison:
                            other=Evaluator();foreign=Native('foreign',lambda:42,other);retained.extend((other,foreign))
                            original=frame.result
                            original_args=list(frame.args)
                            try:
                                if operation=='call':frame.args.append(foreign)
                                else:frame.result=Completion(e,(40,foreign))
                                with test.assertRaises(LanguageError)as caught:seal(e,(root,),None)
                                test.assertEqual(caught.exception.code,'ownership_error');test.assertIsNone(foreign.capabilities)
                                retained.extend((caught.exception,caught.exception.__traceback__))
                            finally:frame.result=original;frame.args[:]=original_args
                    _,delta,paths=seal(e,(root,child)if child is not None else(root,),None)
                    observations.append((name,delta,paths))
                    if cancel:
                        e.suspension.cancel(root);test.assertFalse(child.cancel_event.is_set())
            finally:answer=original_wait(futures,*args,**kwargs)
            return answer
        with patch.object(suspension,'wait',waiting):result=serializable(e.run(source))
        expected='borrowed_hold'if borrowed else'producer_hold'if operation=='list'else'after_hold'if operation=='prog1'else'next_hold'if operation=='call'else'consumer_hold'
        test.assertEqual([row[0]for row in observations],[expected]);test.assertEqual(schedule['waitBoundaryReleases'],1);test.assertEqual(schedule['shutdownReleases'],0)
    test.assertEqual(e.depth,0);test.assertEqual(e.frames,[]);test.assertIsNone(e.handled_error);test.assertIsNone(e.error_context);test.assertFalse(e.suspension.active);test.assertIsNone(e.suspension.pool)
    if cancel:
        root,pad,child=roots[0];test.assertEqual(root.state,'cancelled');test.assertEqual(child.state,'completed');test.assertFalse(child.cancel_event.is_set())
    return result,e.steps-base[0],effects,trace,debug,observations


class MultipleValueCapture220(unittest.TestCase):
    def assert_charged_vector(self,modern,raw):
        # Keep both complete physical observations. The declared new child is
        # unpriced supplementary control state, so it is absent from charged
        # visits but is honestly present in its parent's children metadata.
        # Project that one exact typed edge only; retain every visit/path/depth,
        # queue/seen/shape and real tick count. No root or walker is modified.
        self.assertEqual(modern[:5],raw[:5])
        projected=[];supplementary_edges=0
        for name,delta,paths in modern[5]:
            rows=[]
            for row in paths:
                path,depth,kind,shape,children,seen,queued=row
                if kind=='ProtocolBridge' and children==('NoneType','MultipleValuesCloseCursor'):
                    supplementary_edges+=1;children=('NoneType',)
                rows.append((path,depth,kind,shape,children,seen,queued))
            projected.append((name,delta,tuple(rows)))
        self.assertGreater(supplementary_edges,0,'the actual new declared supplementary child must remain observable')
        self.assertEqual(projected,raw[5])
    def test_all_65_offsets_preserve_real_retained_secondary_and_accumulated_args(self):
        for operation in ('list','prog1','call','bind'):
            for padding in range(65):
                with self.subTest(operation=operation,padding=padding):
                    raw=pending(self,padding,operation,legacy=True)
                    modern=pending(self,padding,operation)
                    self.assert_charged_vector(modern,raw)
                    self.assertEqual(modern[0],[40,list(range(padding))]if operation=='list'else 40 if operation=='prog1'else 3 if operation=='call'else[40,padding,None])

    def test_real_borrowed_await_and_cancel_keep_prefix_once_and_child_independent(self):
        for cancel in (False,True):
            for padding in range(65):
                with self.subTest(cancel=cancel,padding=padding):
                    raw=pending(self,padding,'prog1',legacy=True,borrowed=True,cancel=cancel)
                    modern=pending(self,padding,'prog1',borrowed=True,cancel=cancel)
                    self.assert_charged_vector(modern,raw);self.assertEqual(modern[0],40)

    def test_actual_frame_supplementary_roots_reject_foreign_retained_secondary(self):
        for operation in ('prog1','call','bind'):
            with self.subTest(operation=operation):
                result=pending(self,64,operation,poison=True)
                self.assertEqual(result[0],40 if operation=='prog1'else 3 if operation=='call'else[40,64,None])
