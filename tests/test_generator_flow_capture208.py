"""65 real stream-yield and Task/Future suspension capture alignments."""
import copy
import unittest
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,LanguageError,serializable
from chaos.compiled_flow_state import FlowState
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
import _generator_flow208 as fixture

AWAIT_SOURCE=('let padding=range(PADDING);let g=choose(async fn*(pad){remember_effect("stream_hold");'
              'let value=tool("stream_hold",40);yield value+2;return 9});'
              'let s=g(padding);let operation=anext(s);remember(operation,s,padding);spawn(operation);'
              'let first=await operation;await aclose(s);first')

class GeneratorFlowCapture208(unittest.TestCase):
    def test_all65_yield_and_delegated_yield_capture_paths_match_bare(self):
        for flavor,nextop,closeop in [('fn*','next(s)','close(s)'),('async fn*','await anext(s)','await aclose(s)')]:
            for delegated in(False,True):
                charges=[]
                for padding in range(65):
                    results=[]
                    for target in fixture.targets():
                        observations=[];capture=observed_seal()
                        def inspect(e,s,states,functions):
                            pad=s.capture_roots[1]['pad'];self.assertEqual(pad,list(range(padding)))
                            self.assertEqual(s.state,'suspended');self.assertFalse(s.busy)
                            if target not in('raw','interpreter'):
                                live=[x for x in states if not x.closed and x.function is functions[-1]];self.assertEqual(len(live),1)
                                state=live[0];state.validate(full=True);self.assertIs(state.owner,e)
                                self.assertEqual(state.pending_kind,'yield');self.assertIs(state.pending_cursor,s.runner.iterator)
                                self.assertIs(type(state.pending_request),suspension.Yield);self.assertIs(state.scope.get('pad'),pad)
                                self.assertTrue(any(getattr(f,'compiled_flow_state',None)is state for f in s.runner.capture_frames))
                            else:self.assertEqual(states,[])
                            result,delta,paths=capture(e,(s,),None);observations.append((delta,paths))
                        prefix='let inner=choose(fn*(pad){yield length(pad)-length(pad)+1;return 9});'if delegated else''
                        body='let value=yield from inner(pad);return value'if delegated else'yield length(pad)-length(pad)+1;return 9'
                        source='let padding=range('+str(padding)+');'+prefix+'let g=choose('+flavor+'(pad){'+body+'});let s=g(padding);mark(s);let first='+nextop+';inspect_stream(s);'+closeop+';first'
                        with self.subTest(flavor=flavor,delegated=delegated,padding=padding,target=target):
                            row,states,functions,_=fixture.run(source,target,inspect=inspect)
                            self.assertEqual(row[0],('value',{'done':False,'value':1}));self.assertEqual(len(observations),1)
                            fixture.assert_compiled(self,target,states,functions);results.append((row,observations))
                    self.assertTrue(all(x==results[0]for x in results),results)
                    self.assertGreater(results[0][1][0][0],0);charges.append(results[0][1][0][0])
                self.assertGreater(len(set(charges)),1,'all64 residues pluswrap mustcross a real chargedcheckpoint')

    def pending(self,padding,target,mutate=False,await_child=False):
        effects=[];trace=[];debug=[];states=[];held=[];effect_tasks=[];seen=[];names={};requests={};observations=[];functions=[];base=[None]
        def effect(kind,payload,event):effects.append((kind,copy.deepcopy(payload)));return payload['value']
        e=Evaluator(tool_handler=lambda n,v:v,effect_handler=effect,step_limit=30000,timeout=30)
        def choose(fn):answer=fixture.compile_choice(e,fn,target);functions.append(answer);return answer
        def remember(task,stream,pad,child=None):held.append((task,stream,pad,child));base[0]=e.steps;trace.clear();debug.clear()
        def remember_effect(name):effect_tasks.append((e.suspension.current.task,name))
        for n,fn in [('choose',choose),('remember',remember),('remember_effect',remember_effect)]:e.env.bind(n,Native(n,fn,e,captures=()))
        def traced(row):
            if base[0]is not None:
                row=copy.deepcopy(row)
                if type(row.get('step'))is int:row['step']-=base[0]
                trace.append(row)
        e.trace_handler=traced;e.debug_hook=lambda n,env,frames:debug.append((n[0],dict(e.current_span),e.steps-base[0]))if base[0]is not None else None
        capture=observed_seal();original=FlowState.__init__
        def created(s,*a,**kw):original(s,*a,**kw);states.append(s)
        with effect_schedule('first_wait')as schedule,patch.object(FlowState,'__init__',created):
            controlled_wait=suspension.wait;submit=suspension.ThreadPoolExecutor.submit
            def submitting(pool,fn,*args,**kw):
                future=submit(pool,fn,*args,**kw)
                if args and type(args[0])is suspension.Effect:names[future]=args[0].payload.get('name');requests[future]=args[0]
                return future
            def waiting(futures,*args,**kw):
                futures=tuple(futures)
                try:
                    for task,name in effect_tasks:
                        future=task.runner.future
                        if future is None or future not in futures or future in seen:continue
                        seen.append(future);self.assertFalse(future.done());self.assertEqual(task.runner.state,'effect');self.assertEqual(names[future],name);self.assertEqual(name,'stream_hold')
                        operation,stream,pad,child=held[0]
                        self.assertIs(task,child if await_child else operation);self.assertNotIn(task.state,suspension.TERMINAL)
                        self.assertNotIn(operation.state,suspension.TERMINAL)
                        self.assertIsNot(stream.runner,task.runner);self.assertIs(stream.capture_roots[1]['pad'],pad);self.assertEqual(pad,list(range(padding)))
                        if target not in('raw','interpreter'):
                            live=[s for s in states if not s.closed];self.assertEqual(len(live),1);state=live[0];state.validate(full=True)
                            self.assertEqual(state.flavor,'async_gen_fn');self.assertTrue(state.generating and state.asynchronous)
                            self.assertIs(state.pending_cursor,stream.runner.iterator)
                            if await_child:
                                self.assertIs(type(state.pending_request),suspension.Await);self.assertIs(state.pending_request.task,child)
                                self.assertEqual(state.pending_kind,'await');self.assertIsNot(child,operation)
                            else:
                                self.assertIs(state.pending_request,requests[future]);self.assertEqual(state.pending_kind,'host')
                            self.assertTrue(any(getattr(f,'compiled_flow_state',None)is state for f in stream.runner.capture_frames))
                        else:self.assertEqual(states,[]);state=None
                        result,delta,paths=capture(e,(operation,stream,task),None);observations.append((name,delta,paths))
                        if mutate:state.function.body[1][-1]=copy.deepcopy(state.function.body[1][-1])
                finally:answer=controlled_wait(futures,*args,**kw)
                return answer
            with patch.object(suspension.ThreadPoolExecutor,'submit',submitting),patch.object(suspension,'wait',waiting):
                try:
                    source=AWAIT_SOURCE
                    if await_child:
                        source=('let padding=range(PADDING);let child=(async fn(pad){remember_effect("stream_hold");tool("stream_hold",40)})(padding);'
                                'let g=choose(async fn*(pad,work){let value=await work;yield value+2;return 9});'
                                'let s=g(padding,child);let operation=anext(s);remember(operation,s,padding,child);spawn(operation);'
                                'let first=await operation;await aclose(s);first')
                    result=('value',serializable(e.run(source.replace('PADDING',str(padding)))))
                except LanguageError as error:result=('error',error.code)
            self.assertEqual(len(seen),1);self.assertEqual(schedule['waitBoundaryReleases'],1);self.assertEqual(schedule['shutdownReleases'],0)
        self.assertTrue(all(s.closed and not s.frames and not s._owned_activations and s.pending_cursor is None for s in states));self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
        return (result,e.steps-base[0],effects,trace,debug,observations),states,functions

    def test_all65_real_anext_task_future_capture_keeps_distinct_stream_holder(self):
        charges=[]
        for padding in range(65):
            values=[]
            for target in fixture.targets():
                with self.subTest(padding=padding,target=target):
                    row,states,functions=self.pending(padding,target);self.assertEqual(row[0],('value',{'done':False,'value':42}));fixture.assert_compiled(self,target,states,functions);values.append(row)
            self.assertTrue(all(x==values[0]for x in values),values);charges.append(values[0][-1][0][1]);self.assertGreater(charges[-1],0)
        self.assertGreater(len(set(charges)),1)

    def test_all65_pending_await_holds_original_child_task_and_stream_roots(self):
        charges=[]
        for padding in range(65):
            rows=[]
            for target in fixture.targets():
                with self.subTest(padding=padding,target=target):
                    row,states,functions=self.pending(padding,target,await_child=True)
                    self.assertEqual(row[0],('value',{'done':False,'value':42}));fixture.assert_compiled(self,target,states,functions);rows.append(row)
            self.assertTrue(all(row==rows[0]for row in rows),rows);charges.append(rows[0][-1][0][1]);self.assertGreater(charges[-1],0)
        self.assertGreater(len(set(charges)),1)

    def test_definition_mutation_at_real_pending_effect_rejects_without_reissue(self):
        row,states,functions=self.pending(2,'portable',mutate=True)
        self.assertEqual(row[0],('error','definition_changed'));self.assertEqual(row[2],[('tool',{'name':'stream_hold','value':40})]);self.assertTrue(states)
