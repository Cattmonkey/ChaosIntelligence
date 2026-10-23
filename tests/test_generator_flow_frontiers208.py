"""Original public stream driver boundaries, cancellation and disclosed costs."""
import json,os,time,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,LanguageError,serializable
from chaos.compiled_flow_state import FlowState
from _effect_schedule158 import effect_schedule
import _generator_flow208 as fixture

class GeneratorFlowFrontiers208(unittest.TestCase):
    def test_cancel_real_pending_consumer_does_not_cancel_borrowed_task(self):
        rows=[]
        for target in fixture.targets():
            effects=[];held=[];seen=[];states=[];base=[None]
            e=Evaluator(timeout=30,tool_handler=lambda n,v:v,effect_handler=lambda k,p,c:effects.append(p['name'])or p['value'])
            e.env.bind('choose',Native('choose',lambda f:fixture.compile_choice(e,f,target),e,captures=()))
            def remember(child,operation,stream):held.append((child,operation,stream));base[0]=e.steps
            e.env.bind('remember',Native('remember',remember,e,captures=()))
            with effect_schedule('first_wait')as schedule,fixture.observe_states()as states:
                wait=suspension.wait
                def waiting(futures,*a,**kw):
                    futures=tuple(futures)
                    try:
                        if held and not seen:
                            child,operation,stream=held[0];future=child.runner.future
                            if future is not None and any(future is f for f in futures):
                                self.assertFalse(future.done());self.assertEqual(child.runner.state,'effect')
                                self.assertIs(operation.runner.waiting,child);self.assertEqual(operation.runner.state,'waiting')
                                self.assertIsNot(stream.runner,operation.runner)
                                if target not in('raw','interpreter'):
                                    self.assertEqual(len(states),1);s=states[0];self.assertEqual(s.pending_kind,'await')
                                    self.assertIs(type(s.pending_request),suspension.Await);self.assertIs(s.pending_request.task,child)
                                    self.assertIs(s.pending_cursor,stream.runner.iterator);s.validate(full=True)
                                else:self.assertEqual(states,[])
                                e.suspension.cancel(operation);self.assertFalse(child.cancel_event.is_set());seen.append(True)
                    finally:answer=wait(futures,*a,**kw)
                    return answer
                source='let borrowed=spawn((async fn(){tool("borrowed_generator_hold",42)})());let g=choose(async fn*(work){let value=await work;yield value});let stream=g(borrowed);let operation=spawn(anext(stream));remember(borrowed,operation,stream);await borrowed'
                with patch.object(suspension,'wait',waiting):value=e.run(source)
            child,operation,stream=held[0];self.assertEqual(value,42);self.assertEqual(seen,[True])
            self.assertEqual(child.state,'completed');self.assertFalse(child.cancel_event.is_set());self.assertEqual(operation.state,'cancelled')
            self.assertFalse(stream.busy);self.assertTrue(all(s.closed and not s.frames and not s._owned_activations and s.pending_cursor is None for s in states))
            self.assertEqual(schedule['waitBoundaryReleases'],1);self.assertEqual(schedule['shutdownReleases'],0)
            rows.append((value,e.steps-base[0],effects,child.state,operation.state,stream.state,dict(schedule)))
        self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_hostile_user_delegate_close_preserves_native_error_frontier(self):
        # The host exception enters through the real user __close__ protocol.
        # No replacement evaluator, stream cursor or native close is installed.
        retained=[]
        for asynchronous in(False,True):
            for failure_type in(StopIteration,GeneratorExit,RuntimeError):
                rows=[]
                for target in fixture.targets():
                    events=[];outcomes=[];streams=[];states=[];base=[None]
                    e=Evaluator(timeout=30);fault=failure_type('delegate-close208');original_cause=ValueError('prior-cause208');fault.__cause__=original_cause
                    def fail():events.append('delegate.close');raise fault
                    def probe(stream):
                        streams.append(stream);iterator=stream.runner.iterator;retained.extend((fault,original_cause,iterator,stream))
                        base[0]=e.steps
                        for index in range(2):
                            operation=e.suspension.iteration.close(stream,asynchronous)
                            try:value=e.suspension.drive(operation);outcome=('value',value)
                            except BaseException as error:
                                retained.extend((error,error.__traceback__))
                                outcome=('error',type(error).__name__,str(error),error is fault,error.__cause__ is fault,error.__cause__ is original_cause)
                            outcomes.append((outcome,tuple(events),stream.state,stream.busy,iterator.closed,len(iterator.frames)))
                        return None
                    e.env.bind('fault',Native('fault',fail,e,captures=()));e.env.bind('probe',Native('probe',probe,e,captures=()))
                    e.env.bind('choose',Native('choose',lambda f:fixture.compile_choice(e,f,target),e,captures=()))
                    cursor=('class Cursor{fn __aiter__(self){self}async fn __anext__(self){{done:false,value:1}}async fn __aclose__(self){fault()}}'if asynchronous else'class Cursor{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}fn __close__(self){fault()}}')
                    flavor='async fn*'if asynchronous else'fn*';first='await anext(s)'if asynchronous else'next(s)'
                    with fixture.observe_states()as states,effect_schedule('already_done'):
                        try:
                            value=e.run(cursor+';let g=choose('+flavor+'(){yield from Cursor()});let s=g();let first='+first+';probe(s);first')
                            outer=('value',value)
                        except BaseException as error:
                            retained.extend((error,error.__traceback__))
                            outer=('error',type(error).__name__,str(error),error is fault,error.__cause__ is fault,error.__cause__ is original_cause)
                            if failure_type is GeneratorExit:self.assertIs(error,fault)
                    # The original driver's terminal depends on the actual
                    # stream/Task boundary. Establish the bare result first;
                    # do not impose one latch policy across both flavors or
                    # supported Python versions. Modern rows must match it.
                    if failure_type is GeneratorExit and outer[0]=='error':
                        self.assertEqual(outer,('error','GeneratorExit','delegate-close208',True,False,True))
                    else:self.assertEqual(outer,('value',{'done':False,'value':1}))
                    hard=e.suspension.hard_failure
                    latch=None if hard is None else(type(hard).__name__,hard is fault,hard.__cause__ is original_cause)
                    if rows:self.assertEqual(outer,rows[0][0])
                    self.assertIs(fault.__cause__,original_cause)
                    self.assertEqual(len(outcomes),2)
                    self.assertEqual(events,['delegate.close']);self.assertEqual(outcomes[0][0][0],'error')
                    self.assertEqual(outcomes[1][0],('value',None));self.assertFalse(streams[0].busy)
                    self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.active);self.assertIsNone(e.suspension.pool)
                    if target in('raw','interpreter'):self.assertEqual(states,[])
                    else:
                        self.assertTrue(states);self.assertTrue(all(s.generating and s.closed and not s.frames and not s._owned_activations and s.pending_cursor is None and s.pending_request is None for s in states))
                    retirement=(streams[0].state,streams[0].busy,streams[0].runner.iterator.closed,len(streams[0].runner.iterator.frames),e.depth,len(e.frames),e.suspension.active,e.suspension.pool is None)
                    rows.append((outer,outcomes,e.steps-base[0],events,retirement,latch))
                self.assertTrue(all(row==rows[0]for row in rows),rows)
        self.assertTrue(retained,'faults, tracebacks and actual stream cursors remain strongly retained across both closes')

    def test_measured_cold_and_stream_throughput_retire_all_actual_states(self):
        measurements=[]
        for flavor,consume in [('fn*','collect(s)'),('async fn*','await acollect(s)')]:
            for target in fixture.targets():
                for count in(16,128):
                    compile_cost=[];streams=[];states=[];cold=[];started=[None]
                    e=Evaluator(step_limit=30000,timeout=30)
                    def choose(fn):
                        before=time.perf_counter_ns();answer=fixture.compile_choice(e,fn,target);compile_cost.append(time.perf_counter_ns()-before);return answer
                    def mark(stream,function):
                        streams.append(stream);cold.append((stream.state,len(states),0 if target=='raw'else function.compiled_plan.flow.resume_calls if function.compiled_plan.flow else 0));started[0]=time.perf_counter_ns()
                    e.env.bind('choose',Native('choose',choose,e,captures=()));e.env.bind('mark',Native('mark',mark,e,captures=()))
                    source='let g=choose('+flavor+'(n){var i=0;while i<n{yield i;i=i+1};return 9});let s=g('+str(count)+');mark(s,g);'+consume
                    with fixture.observe_states()as states,effect_schedule('already_done'):value=e.run(source)
                    elapsed=time.perf_counter_ns()-started[0];self.assertEqual(value,list(range(count)));self.assertEqual(cold,[('created',0,0)])
                    self.assertGreater(compile_cost[0],0);self.assertGreater(elapsed,0);self.assertFalse(streams[0].busy)
                    if target in('raw','interpreter'):self.assertEqual(states,[]);resumes=0
                    else:
                        self.assertEqual(len(states),1);s=states[0];resumes=s.program.resume_calls;self.assertGreater(resumes,0)
                        self.assertTrue(s.closed);self.assertEqual(s.frames,[]);self.assertEqual(s._owned_activations,[])
                        self.assertIsNone(s.pending_cursor);self.assertIsNone(s.pending_request);self.assertIsNone(s.function);self.assertIsNone(s.scope);self.assertIsNone(s.result)
                        if target=='native':self.assertGreater(s.program.native_resumes,0)
                        else:self.assertGreater(s.program.portable_resumes,0)
                    measurements.append(dict(flavor=flavor,target=target,items=count,coldCompileNs=compile_cost[0],consumeNs=elapsed,resumes=resumes,retainedActiveStates=0,languageSteps=e.steps))
        print(json.dumps({'kind':'generator-flow-measurements208','measurements':measurements,'accelerationClaim':False,'isolatedMicrobenchmark':False,'physicalHardwareTest':False},separators=(',',':')))
        audit=os.environ.get('CHAOS_GENERATOR_FLOW_AUDIT')
        if audit:
            folder=Path(audit);folder.mkdir(parents=True,exist_ok=True);(folder/'generator-flow-measurements.json').write_text(json.dumps({'measurements':measurements,'accelerationClaim':False,'isolatedMicrobenchmark':False,'physicalHardwareTest':False},indent=2)+'\n')
