"""A real multi-effect finally must finish after one compiler metadata fault."""
import unittest
from unittest.mock import patch

from chaos import suspension
from chaos.language import Evaluator, Native, LanguageError
from chaos.jit import compile_function
from chaos.compiled_flow_state import FlowState
from chaos.native_jit import BackendUnavailable
from _effect_schedule158 import effect_schedule


class FlowCleanup204(unittest.TestCase):
    def test_metadata_failure_preserves_two_real_finally_effects_and_nested_compiled_cleanup(self):
        foreign = Evaluator().run('fn foreign(){0}')

        def run(target, corrupt):
            calls, states, tasks, seen = [], [], [], []
            def handler(kind, payload, event):
                self.assertEqual(kind,'tool')
                calls.append(payload['name'])
                if payload['name']=='hold' and not corrupt:
                    raise LanguageError('Malformed compiled flow invocation',code='ownership_error')
                return payload['value']
            e = Evaluator(tool_handler=lambda name,value:value,effect_handler=handler,
                          step_limit=30000,timeout=30)
            def choose(function):
                with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('portable-test')):
                    return compile_function(e,function,'auto-flow')
            def outer(function):
                return choose(function) if target=='auto-flow' else function
            e.env.bind('choose',Native('choose',choose,e,captures=()))
            e.env.bind('outer',Native('outer',outer,e,captures=()))
            e.env.bind('remember',Native('remember',lambda task:tasks.append(task),e,captures=()))
            source = ('let cleanup=choose(fn(name,value){let result=tool(name,value);result});'
                      'let work=outer(async fn(){try{tool("hold",0)}finally{'
                      'cleanup("cleanup1",1);cleanup("cleanup2",2)};'
                      'tool("forbidden",0);42});'
                      'let task=work();remember(task);spawn(task);await task')
            original = FlowState.__init__
            def created(state,*args,**kwargs):
                original(state,*args,**kwargs);states.append(state)
            with effect_schedule('first_wait') as schedule,patch.object(FlowState,'__init__',created):
                controlled_wait = suspension.wait
                def waiting(futures,*args,**kwargs):
                    futures=tuple(futures)
                    try:
                        self.assertEqual(len(tasks),1)
                        task=tasks[0];future=task.runner.future
                        if future is not None and future in futures and future not in seen:
                            seen.append(future)
                            self.assertFalse(future.done());self.assertEqual(task.runner.state,'effect')
                            parents=[state for state in states if state.asynchronous and not state.closed]
                            if len(seen)==1 and target=='auto-flow':
                                self.assertEqual(len(parents),1)
                                state=parents[0]
                                self.assertIs(state.pending_cursor,task.runner.iterator)
                                self.assertEqual(state.pending_request.payload['name'],'hold')
                                if corrupt:state.frames=foreign
                            if len(seen)>1:
                                children=[state for state in states if not state.asynchronous and not state.closed]
                                self.assertEqual(len(children),1)
                                self.assertGreater(children[0].program.resume_calls,0)
                                self.assertEqual(children[0].pending_request.payload['name'],
                                                 'cleanup1' if len(seen)==2 else 'cleanup2')
                    finally:
                        result=controlled_wait(futures,*args,**kwargs)
                    return result
                with patch.object(suspension,'wait',waiting):
                    try:e.run(source)
                    except LanguageError as error:
                        outcome=(type(error).__name__,error.code)
                    else:self.fail('invalid compiled state resumed the normal body')
                self.assertEqual(calls,['hold','cleanup1','cleanup2'])
                self.assertEqual(len(seen),3)
                self.assertEqual(schedule['waitBoundaryReleases'],3)
                self.assertEqual(schedule['shutdownReleases'],0)
                self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
                self.assertTrue(states)
                self.assertTrue(all(state.closed and not state.frames and
                                    not state._owned_activations and state.pending_cursor is None
                                    for state in states))
                self.assertEqual(sum(not state.asynchronous for state in states),2)
            return outcome,calls

        baseline=run('bare',False)
        self.assertEqual(baseline,(('LanguageError','ownership_error'),['hold','cleanup1','cleanup2']))
        self.assertEqual(run('auto-flow',False),baseline)
        self.assertEqual(run('auto-flow',True),baseline)
