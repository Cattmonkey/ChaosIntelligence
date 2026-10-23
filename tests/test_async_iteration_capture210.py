"""One phase matrix over all 64 charged offsets plus the wrap checkpoint."""
import unittest
import copy
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native
from chaos.execution_loops import LoopFrame
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
import _generator_flow208 as streams
import _async_iteration_flow210 as fixture

class AsyncIterationCapture210(unittest.TestCase):
    def test_all65_async_generator_consumer_keeps_own_stream_holder_and_bidirectional_exit(self):
        charges=[]
        for padding in range(65):
            rows=[]
            for target in streams.targets():
                effects=[];trace=[];debug=[];held=[];effect_tasks=[];seen=[];observations=[];functions=[];base=[None]
                e=Evaluator(timeout=30,tool_handler=lambda n,v:v,effect_handler=lambda k,p,c:effects.append((k,copy.deepcopy(p)))or p['value'])
                def choose(f):answer=streams.compile_choice(e,f,target);functions.append(answer);return answer
                def remember(operation,stream,pad,upstream):held.append((operation,stream,pad,upstream));base[0]=e.steps;trace.clear();debug.clear()
                for name,fn in [('choose',choose),('remember',remember),('remember_effect',lambda:effect_tasks.append(e.suspension.current.task))]:e.env.bind(name,Native(name,fn,e,captures=()))
                def traced(row):
                    if base[0]is not None:
                        row=copy.deepcopy(row)
                        if type(row.get('step'))is int:row['step']-=base[0]
                        trace.append(row)
                e.trace_handler=traced;e.debug_hook=lambda n,env,frames:debug.append((n[0],dict(e.current_span),e.steps-base[0]))if base[0]is not None else None
                seal=observed_seal()
                with streams.observe_states()as states,effect_schedule('first_wait')as schedule:
                    wait=suspension.wait
                    def waiting(futures,*a,**kw):
                        futures=tuple(futures)
                        try:
                            if held and effect_tasks and not seen:
                                upstream_operation=effect_tasks[0];future=upstream_operation.runner.future
                                if future is not None and future in futures:
                                    seen.append(future);self.assertFalse(future.done());self.assertEqual(upstream_operation.runner.state,'effect')
                                    operation,stream,pad,upstream=held[0]
                                    self.assertNotIn(operation.state,suspension.TERMINAL);self.assertIsNot(operation,upstream_operation)
                                    self.assertIs(stream.capture_roots[1]['pad'],pad);self.assertEqual(pad,list(range(padding)))
                                    self.assertIsNot(stream.runner,operation.runner)
                                    if target not in ('raw','interpreter'):
                                        live=[s for s in states if not s.closed];self.assertEqual(len(live),1);state=live[0];state.validate(full=True)
                                        self.assertEqual(state.flavor,'async_gen_fn');self.assertTrue(state.generating and state.asynchronous)
                                        self.assertIs(state.pending_cursor,stream.runner.iterator);self.assertEqual(state.pending_kind,'await')
                                        loop=next(f for f in stream.runner.iterator.frames if type(f)is LoopFrame and f.kind=='async_for')
                                        self.assertTrue(loop.boundary.flow_structural);self.assertIs(loop.operation,upstream_operation)
                                        self.assertIs(state.pending_request.task,upstream_operation)
                                        self.assertTrue(any(getattr(f,'compiled_flow_state',None)is state for f in stream.runner.capture_frames))
                                    else:self.assertEqual(states,[])
                                    _,delta,paths=seal(e,(operation,stream,upstream,upstream_operation),None);observations.append((delta,paths))
                        finally:answer=wait(futures,*a,**kw)
                        return answer
                    source='''let padding=range(PADDING);let upstream=(async fn*(){remember_effect();yield tool("upstream_hold",40);yield 99})();
let g=choose(async fn*(pad,input){async for value in input{let sent=yield value+2;if sent==7{break}else{null}}else{yield 0};return 9});
let stream=g(padding,upstream);let operation=anext(stream);remember(operation,stream,padding,upstream);spawn(operation);
let first=await operation;let second=await asend(stream,7);await aclose(stream);[first,second,await anext(upstream)]'''.replace('PADDING',str(padding))
                    with patch.object(suspension,'wait',waiting):value=e.run(source)
                    self.assertEqual(schedule['waitBoundaryReleases'],1);self.assertEqual(schedule['shutdownReleases'],0)
                with self.subTest(padding=padding,target=target):
                    self.assertEqual(value,[{'done':False,'value':42},{'done':True,'value':9},{'done':False,'value':99}])
                    self.assertEqual(len(observations),1);fixture.retired(self,target,states,functions)
                    self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(held[0][1].busy)
                    rows.append((value,e.steps-base[0],effects,trace,debug,observations))
            self.assertTrue(all(row==rows[0]for row in rows),rows);charges.append(rows[0][-1][0][0]);self.assertGreater(charges[-1],0)
        self.assertGreater(len(set(charges)),1)

    def test_all65_source_factory_anext_body_else_real_futures_match_bare(self):
        charges={name:[] for name in ('source_hold','acquire_hold','anext_hold','body_hold','else_hold')}
        for padding in range(65):
            rows=[]
            for target in fixture.TARGETS():
                with self.subTest(padding=padding,target=target):
                    row,states,functions=fixture.pending(self,padding,target)
                    self.assertEqual(row[0],('value',42));fixture.retired(self,target,states,functions);rows.append(row)
            self.assertEqual(len(rows),len(fixture.TARGETS()))
            self.assertTrue(all(row==rows[0]for row in rows),rows)
            for name in charges:
                values=[x[1]for x in rows[0][-1]if x[0]==name]
                self.assertTrue(values);self.assertTrue(all(value>0 for value in values));charges[name].append(tuple(values))
        for name,values in charges.items():
            self.assertGreater(len(set(values)),1,name+' must cross a real charged checkpoint')

    def test_poisoned_real_pending_operation_rejects_before_next_body_effect(self):
        for field,value in [('epoch',True),('frames',[None])]:
            def poison(e,state,consumer,operation):setattr(state,field,value)
            with self.subTest(field=field):
                row,states,_=fixture.pending(self,2,'portable',poison=poison)
                self.assertEqual(row[0],('error','ownership_error'))
                self.assertEqual([payload['name']for _,payload in row[2]],['source_hold','acquire_hold','anext_hold'])
                self.assertTrue(states)
