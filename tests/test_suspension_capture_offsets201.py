"""Real pending Task/Future roots at all64 charge alignments plus wrap."""
import copy
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,serializable
from chaos.suspension import Task,AsyncGenerator
from chaos.execution_suspension import SuspensionSyntaxFrame
from chaos.capture_graph import seal
from _suspension_reference201 import reference
from _effect_schedule158 import effect_schedule
from test_callable_execution_frames import owned_frame
from test_suspension_syntax_frames201 import observe

SOURCES={
 'operand':'async fn execute(pad){remember_effect("operand_hold");try{await tool("operand_hold",42)}catch e{e.code}};',
 'await_return':'async fn target(){remember_effect("await_hold");tool("await_hold",42)};async fn execute(pad){await target()};',
 'delegate_return':'async fn* inner(pad){remember_effect("delegate_hold");yield tool("delegate_hold",42)};async fn* outer(pad){yield from inner(pad)};async fn execute(pad){let stream=outer(pad);remember_stream(stream);let item=await anext(stream);item.value};'}

class SuspensionCaptureOffsets201(unittest.TestCase):
    def record(self,padding,phase,legacy):
        effects=[];trace=[];debug=[];remembered=[];effect_tasks=[];streams=[];observations=[];future_names={};seen_futures=[]
        def effect(kind,payload,event):effects.append((kind,copy.deepcopy(payload)));return payload['value']
        e=Evaluator(tool_handler=lambda name,value:value,effect_handler=effect,trace_handler=trace.append,step_limit=30000,timeout=30)
        e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
        def remember(task,pad):self.assertIs(type(task),Task);remembered.append((task,pad));return None
        def remember_effect(name):effect_tasks.append((e.suspension.current.task,name));return None
        def remember_stream(stream):self.assertIs(type(stream),AsyncGenerator);streams.append(stream);return None
        for name,fn in [('remember',remember),('remember_effect',remember_effect),('remember_stream',remember_stream)]:e.env.bind(name,Native(name,fn,e,captures=()))
        source='let padding=range('+str(padding)+');'+SOURCES[phase]+'let t=execute(padding);remember(t,padding);spawn(t);await t'
        with effect_schedule('first_wait')as schedule,reference()if legacy else nullcontext()as reached,observe()as steps:
            controlled_wait=suspension.wait;submit=suspension.ThreadPoolExecutor.submit
            def inspect_submit(pool,fn,*args,**kwargs):
                future=submit(pool,fn,*args,**kwargs)
                if args and type(args[0])is suspension.Effect:future_names[future]=args[0].payload.get('name')
                return future
            def inspect_wait(futures,*args,**kwargs):
                futures=tuple(futures)
                try:
                    for task,name in effect_tasks:
                        future=task.runner.future
                        if future is None or not any(future is item for item in futures)or any(future is item for item in seen_futures):continue
                        seen_futures.append(future);self.assertFalse(future.done());self.assertEqual(task.runner.state,'effect');self.assertEqual(future_names[future],name)
                        root,pad=remembered[0];self.assertEqual(pad,list(range(padding)));self.assertNotIn(root.state,('completed','failed','closed','cancelled'));self.assertIs(root.capture_roots[1]['pad'],pad)
                        expected_name={'operand':'operand_hold','await_return':'await_hold','delegate_return':'delegate_hold'}[phase];self.assertEqual(name,expected_name)
                        selected=streams[0].runner.iterator if phase=='delegate_return'else root.runner.iterator
                        frame=None
                        if legacy:self.assertGreater(reached['yield_from'if phase=='delegate_return'else'await'],0);self.assertEqual(steps['steps'],{})
                        else:
                            frame=owned_frame(self,selected,SuspensionSyntaxFrame);self.assertEqual(frame.phase,phase);self.assertIs(frame.env.get('pad'),pad);self.assertGreater(steps['steps'].get('yield_from'if phase=='delegate_return'else'await',0),0)
                        import chaos.execution_frames as frames
                        actual=[];old=frames.frame_roots
                        def roots(value):actual.append(value);return old(value)
                        before=e.steps;caps=root.runner.capabilities
                        with patch.object(frames,'frame_roots',roots):seal(e,(root,),None)
                        self.assertIs(root.runner.capabilities,caps)
                        if legacy:self.assertFalse(any(type(value)is SuspensionSyntaxFrame for value in actual))
                        else:self.assertTrue(any(value is frame for value in actual),'real pending algorithm must be seal-reachable through declared Task/stream/cursor roots')
                        observations.append((phase,e.steps-before))
                finally:waited=controlled_wait(futures,*args,**kwargs)
                return waited
            with patch.object(suspension.ThreadPoolExecutor,'submit',inspect_submit),patch.object(suspension,'wait',inspect_wait):result=serializable(e.run(source))
            self.assertEqual(result,'argument_error'if phase=='operand'else 42);self.assertEqual(len(observations),1);self.assertEqual(len(seen_futures),1)
            self.assertEqual(schedule['waitBoundaryReleases'],1);self.assertEqual(schedule['shutdownReleases'],0);self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
        return result,e.steps,effects,trace,debug,observations,dict(schedule)

    def test_real_operand_await_and_delegate_pending_tasks_preserve65_charged_offsets(self):
        for phase in SOURCES:
            charges=[]
            for padding in range(65):
                with self.subTest(phase=phase,padding=padding):
                    old=self.record(padding,phase,True);modern=self.record(padding,phase,False);self.assertEqual(modern,old);self.assertGreater(modern[5][0][1],0);charges.append(modern[5][0][1])
            self.assertGreater(len(set(charges)),1,'padding must actually cross a charged checkpoint')
