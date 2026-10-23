"""Real worker waits, declared compiled-state roots and continuation ordering."""
import copy
import unittest
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator, Native, LanguageError, serializable
from chaos.jit import compile_function
from chaos.compiled_flow_state import FlowState
from chaos.capture_graph import seal
from chaos.native_jit import BackendUnavailable
from _effect_schedule158 import effect_schedule

SOURCE = ('let padding=range(PADDING);let work=choose(async fn(pad){'
          'remember_effect("compiled_hold");let value=tool("compiled_hold",40);'
          'await sleep(0);if value>0{value+2}else{0}});'
          'let t=work(padding);remember(t,padding);spawn(t);await t')


class AsyncFlowCapture203(unittest.TestCase):
    def run_pending(self, padding, compiled=True, inspect=None, mutate_definition=False, enable_trace=False):
        effects, trace, debug, states, held, effect_tasks, observations = [], [], [], [], [], [], []
        base = [None]; future_names = {}; future_requests = {}; visited = []
        def effect(kind, payload, event):
            effects.append((kind, copy.deepcopy(payload))); return payload['value']
        e = Evaluator(tool_handler=lambda name, value: value, effect_handler=effect, step_limit=30000, timeout=30)
        def trace_event(event):
            row=copy.deepcopy(event)
            if base[0] is not None:
                if type(row.get('step')) is int:row['step']-=base[0]
                trace.append(row)
        e.trace_handler=None if enable_trace else trace_event
        e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps-base[0])) if base[0] is not None else None
        def choose(function):
            with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('portable-test')):
                return compile_function(e,function,'auto-flow' if compiled else 'interpreter')
        def remember(task,pad):
            held.append((task,pad));base[0]=e.steps;trace.clear();debug.clear()
        def remember_effect(name):effect_tasks.append((e.suspension.current.task,name))
        for name,call in [('choose',choose),('remember',remember),('remember_effect',remember_effect)]:e.env.bind(name,Native(name,call,e,captures=()))
        original=FlowState.__init__
        def created(state,*args,**kwargs):original(state,*args,**kwargs);states.append(state)
        with effect_schedule('first_wait') as schedule,patch.object(FlowState,'__init__',created):
            controlled_wait=suspension.wait;submit=suspension.ThreadPoolExecutor.submit
            def submitting(pool,fn,*args,**kwargs):
                future=submit(pool,fn,*args,**kwargs)
                if args and type(args[0])is suspension.Effect:
                    future_names[future]=args[0].payload.get('name');future_requests[future]=args[0]
                return future
            def waiting(futures,*args,**kwargs):
                futures=tuple(futures)
                try:
                    for task,name in effect_tasks:
                        future=task.runner.future
                        if future is None or future not in futures or future in visited:continue
                        visited.append(future);self.assertFalse(future.done());self.assertEqual(task.runner.state,'effect')
                        self.assertEqual(name,future_names[future]);self.assertEqual(name,'compiled_hold')
                        root,pad=held[0];self.assertIs(root,task);self.assertIs(task.capture_roots[1]['pad'],pad)
                        self.assertEqual(pad,list(range(padding)));self.assertNotIn(task.state,suspension.TERMINAL)
                        if compiled:
                            live=[s for s in states if not s.closed];self.assertEqual(len(live),1);state=live[0]
                            self.assertIs(state.owner,e);self.assertEqual(state.phase,'host');state.validate(full=True)
                            self.assertIs(state.pending_cursor,task.runner.iterator)
                            self.assertIs(state.pending_request,future_requests[future])
                            self.assertEqual(state.pc,state.pending_pc+1)
                            self.assertTrue(any(getattr(frame,'compiled_flow_state',None)is state for frame in task.runner.capture_frames))
                            self.assertIs(state.scope.get('pad'),pad)
                        else:self.assertEqual(states,[]);state=None
                        if inspect:inspect(e,task,state)
                        before=e.steps;seal(e,(task,),None);delta=e.steps-before
                        observations.append((name,delta))
                        if mutate_definition:
                            state.function.body[1][-1]=copy.deepcopy(state.function.body[1][-1])
                        if enable_trace:e.trace_handler=trace_event
                finally:answer=controlled_wait(futures,*args,**kwargs)
                return answer
            with patch.object(suspension.ThreadPoolExecutor,'submit',submitting),patch.object(suspension,'wait',waiting):
                try:result=('value',serializable(e.run(SOURCE.replace('PADDING',str(padding)))))
                except LanguageError as error:result=('error',error.code)
            self.assertEqual(len(visited),1);self.assertEqual(schedule['waitBoundaryReleases'],1);self.assertEqual(schedule['shutdownReleases'],0)
            self.assertTrue(all(s.closed and not s.frames and s.pending_cursor is None for s in states))
            self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
        return (result,e.steps-base[0],effects,trace,debug,observations),states

    def test_actual_pending_compiled_task_preserves_all65_capture_offsets(self):
        charges=[]
        for padding in range(65):
            with self.subTest(padding=padding):
                old,_=self.run_pending(padding,False);modern,states=self.run_pending(padding,True)
                self.assertEqual(modern,old);self.assertEqual(modern[0],('value',42));self.assertTrue(states)
                self.assertGreater(modern[-1][0][1],0);charges.append(modern[-1][0][1])
        self.assertGreater(len(set(charges)),1)

    def test_real_pending_state_metadata_and_foreign_roots_fail_atomically(self):
        foreign=Evaluator().run('fn foreign(){1}')
        def inspect(e,task,state):
            fields=[('epoch',True),('phase',foreign),('pc',state.pc+1),('pending_pc',state.pending_pc-1),('flags',[foreign]),('pending_cursor',foreign),('pending_request',foreign),('pending_authority',foreign),('env',foreign),('closed',foreign),('capabilities',foreign)]
            for field,bad in fields:
                old=getattr(state,field);setattr(state,field,bad)
                try:
                    with self.subTest(field=field),self.assertRaises(LanguageError)as caught:seal(e,(task,),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
                finally:setattr(state,field,old)
            local=e.env.get('tool');prior=local.capabilities;old=state.result;state.result=foreign
            try:
                with self.assertRaises(LanguageError):seal(e,(task,local),frozenset())
                self.assertIs(local.capabilities,prior)
            finally:state.result=old
        result,_=self.run_pending(3,inspect=inspect);self.assertEqual(result[0],('value',42))

    def test_definition_change_after_real_effect_never_replays_prefix(self):
        result,states=self.run_pending(2,mutate_definition=True)
        self.assertEqual(result[0],('error','definition_changed'))
        self.assertEqual(result[2],[('tool',{'name':'compiled_hold','value':40})])
        self.assertTrue(all(state.closed for state in states))

    def test_enabling_trace_during_pending_portable_event_does_not_restart_prefix(self):
        result,states=self.run_pending(1,enable_trace=True)
        self.assertEqual(result[0],('value',42));self.assertEqual(len(result[2]),1)
        self.assertTrue(states);self.assertGreater(states[0].program.portable_resumes,0)
