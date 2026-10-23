"""Real generic before/after Task waits preserve original charged capture paths."""
import copy
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,serializable
from chaos.capture_graph import seal
from chaos.execution_callables import GenericResolveFrame
from chaos.suspension import Task
from _callable_reference191 import reference
from _effect_schedule158 import effect_schedule
from test_callable_execution_frames import observe_callable_entries,owned_frame

SOURCE='''let padding=range(PADDING);let ready=channel(2);
var work=generic_method(generic_function("work",2),["Int","List"],fn(value,pad){value});
work=generic_method(work,["Int","List"],fn(value,pad){spawn(channel_send(ready,"before"));tool("before_hold",null);null},"before");
work=generic_method(work,["Int","List"],fn(value,pad){spawn(channel_send(ready,"after"));tool("after_hold",null);null},"after");
async fn execute(pad){work(42,pad)}
let task=execute(padding);remember_generic_task194(task,padding);spawn(task);await task'''


class GenericCaptureOffsets194(unittest.TestCase):
    def observe(self,padding,legacy):
        effects=[];trace=[];debug=[];remembered=[];observations=[];frame_witnesses=[];seen_futures=[]
        def effect(kind,payload,event):
            effects.append((kind,copy.deepcopy(payload)))
            return payload['value']
        e=Evaluator(tool_handler=lambda name,value:value,effect_handler=effect,
                    trace_handler=trace.append,step_limit=30000,timeout=30)
        e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
        def remember(task,values):
            self.assertIs(type(task),Task);self.assertEqual(values,list(range(padding)))
            remembered.append((task,values));return None
        e.env.bind('remember_generic_task194',Native('remember_generic_task194',remember,e,captures=()))
        # The fixed schedule still uses real worker Futures. Observe at their
        # first actual scheduler wait, before the existing handoff releases them.
        # Unlike a source ready signal, this cannot precede Effect dispatch.
        with effect_schedule('first_wait')as schedule,reference()if legacy else nullcontext()as reached,observe_callable_entries()as steps:
            controlled_wait=suspension.wait
            def inspect_wait(futures,*args,**kwargs):
                futures=tuple(futures)
                try:
                    if remembered:
                        task,values=remembered[0];runner=task.runner;future=runner.future
                        if future is not None and any(future is item for item in futures) and all(future is not item for item in seen_futures):
                            # Mark this actual handoff before assertions so a
                            # failing observation is not repeated during cleanup.
                            seen_futures.append(future)
                            self.assertLess(len(observations),2)
                            phase=('before_return','after_return')[len(observations)]
                            self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                            self.assertEqual(runner.state,'effect')
                            self.assertEqual(runner.effect_kind,'tool')
                            self.assertFalse(future.done())
                            self.assertIs(task.capture_roots[1]['pad'],values)
                            self.assertEqual(values,list(range(padding)))
                            if legacy:
                                self.assertGreater(reached['generic.resolve'],0)
                                self.assertEqual(steps,{})
                            else:
                                frame=owned_frame(self,runner.iterator,GenericResolveFrame)
                                self.assertEqual(frame.phase,phase)
                                self.assertIs(frame.request.args[1],values)
                                self.assertGreater(steps.get('GenericResolveFrame',0),0)
                            import chaos.execution_frames as frames
                            actual_roots=[];original=frames.frame_roots
                            def roots(frame):
                                actual_roots.append(type(frame));return original(frame)
                            before=e.steps;capabilities=runner.capabilities
                            with patch.object(frames,'frame_roots',roots):seal(e,(task,),None)
                            self.assertIs(runner.capabilities,capabilities)
                            if legacy:self.assertNotIn(GenericResolveFrame,actual_roots)
                            else:self.assertIn(GenericResolveFrame,actual_roots)
                            frame_witnesses.append(GenericResolveFrame in actual_roots)
                            observations.append((phase,e.steps-before))
                finally:
                    # Release/join selected real workers even if an observation
                    # fails; returning in finally would hide the original error.
                    waited=controlled_wait(futures,*args,**kwargs)
                return waited
            with patch.object(suspension,'wait',inspect_wait):
                result=serializable(e.run(SOURCE.replace('PADDING',str(padding))))
            self.assertEqual(result,42)
            self.assertEqual(len(remembered),1)
            task,values=remembered[0]
            self.assertEqual(task.state,'completed')
            self.assertTrue(task.runner.iterator.closed)
            self.assertEqual(task.runner.iterator.frames,[])
            self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
            self.assertEqual([row[0]for row in observations],['before_return','after_return'])
            self.assertEqual(len(seen_futures),2)
            self.assertEqual([row[1]['name']for row in effects],['before_hold','after_hold'])
            self.assertEqual(schedule['waitBoundaryReleases'],2)
            self.assertEqual(schedule['shutdownReleases'],0)
            if legacy:
                self.assertEqual(steps,{})
                self.assertGreater(reached['generic.resolve'],0)
            else:self.assertGreater(steps.get('GenericResolveFrame',0),0)
        return (result,e.steps,effects,trace,debug,list(observations),e.depth,len(e.frames),dict(schedule)),frame_witnesses

    def test_real_pending_generic_before_and_after_preserve_all64_charged_alignments_and_wrap(self):
        before_charges=[];after_charges=[]
        for padding in range(65):
            with self.subTest(padding=padding):
                modern,modern_frames=self.observe(padding,False)
                old,old_frames=self.observe(padding,True)
                self.assertEqual(modern,old)
                self.assertEqual(modern_frames,[True,True])
                self.assertEqual(old_frames,[False,False])
                self.assertGreater(modern[5][0][1],0)
                self.assertGreater(modern[5][1][1],0)
                before_charges.append(modern[5][0][1]);after_charges.append(modern[5][1][1])
        self.assertGreater(len(set(before_charges)),1,'real before-pending Task roots must cross a charged checkpoint')
        self.assertGreater(len(set(after_charges)),1,'real after-pending Task roots must cross a charged checkpoint')
