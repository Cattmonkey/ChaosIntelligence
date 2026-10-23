"""Real match subject/guard/body Task waits preserve original charged capture paths."""
import copy
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,serializable
from chaos.capture_graph import seal
from chaos.execution_patterns import PatternSyntaxFrame
from chaos.execution_frames import ExecutionCursor
from chaos.suspension import Task
from _pattern_reference196 import reference
from _effect_schedule158 import effect_schedule
from test_pattern_execution_frames import observe_pattern_entries
from test_callable_execution_frames import owned_frame

SOURCE='let padding=range(PADDING);\nasync fn execute(pad){match tool("subject_hold",42){value if tool("guard_hold",true)=>do{let result=tool("body_hold",value);result};_=>0}}\nlet task=execute(padding);remember_pattern_task196(task,padding);spawn(task);await task'


class PatternCaptureOffsets196(unittest.TestCase):
    def observe(self,padding,legacy):
        effects=[];trace=[];debug=[];remembered=[];observations=[];frame_witnesses=[];seen_futures=[];future_names={}
        def effect(kind,payload,event):
            effects.append((kind,copy.deepcopy(payload)))
            return payload['value']
        e=Evaluator(tool_handler=lambda name,value:value,effect_handler=effect,
                    trace_handler=trace.append,step_limit=30000,timeout=30)
        e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
        def remember(task,values):
            self.assertIs(type(task),Task);self.assertEqual(values,list(range(padding)))
            remembered.append((task,values));return None
        e.env.bind('remember_pattern_task196',Native('remember_pattern_task196',remember,e,captures=()))
        # The fixed schedule still uses real worker Futures. Observe at their
        # first actual scheduler wait, before the existing handoff releases them.
        # Unlike a source ready signal, this cannot precede Effect dispatch.
        with effect_schedule('first_wait')as schedule,reference()if legacy else nullcontext()as reached,observe_pattern_entries()as steps:
            controlled_wait=suspension.wait
            original_submit=suspension.ThreadPoolExecutor.submit
            def inspect_submit(pool,function,*args,**kwargs):
                # Forward the exact real submit first: never create, complete,
                # replace or release a Future. Only keep controlled Effect.name
                # metadata attached to the Future that it actually returned.
                future=original_submit(pool,function,*args,**kwargs)
                if args and type(args[0])is suspension.Effect:
                    request=args[0]
                    self.assertIs(type(request.payload),dict)
                    self.assertIs(type(request.payload.get('name')),str)
                    future_names[future]=request.payload['name']
                return future
            def inspect_wait(futures,*args,**kwargs):
                futures=tuple(futures)
                try:
                    if remembered:
                        task,values=remembered[0];runner=task.runner;future=runner.future
                        if future is not None and any(future is item for item in futures) and all(future is not item for item in seen_futures):
                            # Mark this actual handoff before assertions so a
                            # failing observation is not repeated during cleanup.
                            seen_futures.append(future)
                            self.assertLess(len(observations),3)
                            phase=('subject','guard','body')[len(observations)]
                            future_name=future_names.get(future)
                            diagnostic=('; actualFutureName='+repr(future_name)+'; observedPhases='+repr([r[0]for r in observations])+
                                '; completedEffectNames='+repr([row[1]['name']for row in effects if row[0]=='tool'])+
                                '; actualPatternSteps='+repr(dict(steps)))
                            self.assertEqual(future_name,phase+'_hold',diagnostic)
                            self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                            self.assertEqual(runner.state,'effect')
                            self.assertEqual(runner.effect_kind,'tool')
                            self.assertFalse(future.done())
                            self.assertIs(task.capture_roots[1]['pad'],values)
                            self.assertEqual(values,list(range(padding)))
                            if legacy:
                                self.assertGreater(reached['match'],0)
                                self.assertEqual(steps,{})
                            else:
                                try:
                                    frame=owned_frame(self,runner.iterator,PatternSyntaxFrame)
                                except self.failureException as failure:
                                    # Bounded diagnostics traverse only the same
                                    # declared managed cursor edges as the real
                                    # ownership assertion. No generator locals,
                                    # arbitrary host graph or effect payloads.
                                    rows=[];todo=[runner.iterator];seen=set()
                                    while todo and len(rows)<128:
                                        current=todo.pop()
                                        if not isinstance(current,ExecutionCursor):
                                            rows.append(('noncursor',type(current).__name__));continue
                                        if id(current)in seen:
                                            rows.append(('repeated-cursor',));continue
                                        seen.add(id(current));number=len(seen)-1
                                        for actual in current.frames:
                                            if len(rows)>=128:break
                                            name=type(actual).__name__;state=getattr(actual,'phase',None)
                                            state=state if type(state)is str else type(state).__name__
                                            node=getattr(actual,'node',None)
                                            nodekind=node[0]if isinstance(node,tuple)and node and type(node[0])is str else None
                                            child=getattr(actual,'pending_cursor',None)
                                            rows.append((number,name,state,nodekind,type(child).__name__ if child is not None else None))
                                            if child is not None:todo.append(child)
                                    raise self.failureException(str(failure)+'; managed pending graph='+repr(rows)+
                                        '; runnerEffect='+repr(runner.effect_kind)+'; taskState='+repr(task.state)+diagnostic)from failure
                                self.assertEqual(frame.phase,phase)
                                self.assertIs(frame.env.get('pad'),values)
                                self.assertGreater(steps.get('PatternSyntaxFrame',0),0)
                            import chaos.execution_frames as frames
                            actual_roots=[];original=frames.frame_roots
                            def roots(frame):
                                actual_roots.append(type(frame));return original(frame)
                            before=e.steps;capabilities=runner.capabilities
                            with patch.object(frames,'frame_roots',roots):seal(e,(task,),None)
                            self.assertIs(runner.capabilities,capabilities)
                            if legacy:self.assertNotIn(PatternSyntaxFrame,actual_roots)
                            else:self.assertIn(PatternSyntaxFrame,actual_roots)
                            frame_witnesses.append(PatternSyntaxFrame in actual_roots)
                            observations.append((phase,e.steps-before))
                finally:
                    # Release/join selected real workers even if an observation
                    # fails; returning in finally would hide the original error.
                    waited=controlled_wait(futures,*args,**kwargs)
                return waited
            with patch.object(suspension.ThreadPoolExecutor,'submit',inspect_submit),patch.object(suspension,'wait',inspect_wait):
                result=serializable(e.run(SOURCE.replace('PADDING',str(padding))))
            self.assertEqual(result,42)
            self.assertEqual(len(remembered),1)
            task,values=remembered[0]
            self.assertEqual(task.state,'completed')
            self.assertTrue(task.runner.iterator.closed)
            self.assertEqual(task.runner.iterator.frames,[])
            self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
            self.assertEqual([row[0]for row in observations],['subject','guard','body'])
            self.assertEqual(len(seen_futures),3)
            self.assertEqual([row[1]['name']for row in effects],['subject_hold','guard_hold','body_hold'])
            self.assertEqual(schedule['waitBoundaryReleases'],3)
            self.assertEqual(schedule['shutdownReleases'],0)
            if legacy:
                self.assertEqual(steps,{})
                self.assertGreater(reached['match'],0)
            else:self.assertGreater(steps.get('PatternSyntaxFrame',0),0)
        return (result,e.steps,effects,trace,debug,list(observations),e.depth,len(e.frames),dict(schedule)),frame_witnesses

    def test_real_pending_match_subject_guard_body_preserve_all64_charged_alignments_and_wrap(self):
        phase_charges=[[],[],[]]
        for padding in range(65):
            with self.subTest(padding=padding):
                modern,modern_frames=self.observe(padding,False)
                old,old_frames=self.observe(padding,True)
                self.assertEqual(modern,old)
                self.assertEqual(modern_frames,[True,True,True])
                self.assertEqual(old_frames,[False,False,False])
                for index in range(3):
                    self.assertGreater(modern[5][index][1],0)
                    phase_charges[index].append(modern[5][index][1])
        for phase,charges in zip(('subject','guard','body'),phase_charges):
            self.assertGreater(len(set(charges)),1,'real '+phase+' pending Task roots must cross a charged checkpoint')
