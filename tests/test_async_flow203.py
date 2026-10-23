"""Real async compiled bodies, compared with the original interpreter target."""
import copy
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError, ExecutionControl, Native, serializable
from chaos.jit import compile_function, compile_info
from chaos.jit_flow import info, validate_program
from chaos.compiled_flow_state import FlowState
from chaos.multiple_values import components
from chaos.native_jit import BackendUnavailable
from _effect_schedule158 import effect_schedule


def make(source, target='auto-flow', **options):
    e = Evaluator(step_limit=30000, timeout=30, **options)
    plain = e.run(source)
    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('test-portable')):
        function = compile_function(e, plain, target)
    return e, function


def record(source, args=(), target='auto-flow', limit=30000, cancel=None):
    effects, trace, debug, states = [], [], [], []
    e, function = make(source, target, tool_handler=lambda name, value: effects.append((name, copy.deepcopy(value))) or value)
    e.steps = 0; e.step_limit = limit
    e.cancelled = lambda: cancel is not None and e.steps >= cancel
    e.trace_handler = trace.append
    e.debug_hook = lambda node, env, frames: debug.append((node[0], dict(e.current_span), e.steps))
    original = FlowState.__init__
    def entered(state, *a, **kw):
        original(state, *a, **kw); states.append(state)
    failure = None
    with patch.object(FlowState, '__init__', entered), effect_schedule('already_done'):
        try:
            value = e.invoke(function, list(args), force=True, all_results=True)
            result = ('values', serializable(list(components(value, e))))
        except LanguageError as error:
            failure = error; result = ('error', error.record())
        except ExecutionControl as error:
            failure = error; result = ('control', type(error).__name__, str(error))
        finally:
            e.suspension.finish(failure)
    return (result, e.steps, effects, trace, debug, e.depth, len(e.frames)), states, function


class AsyncFlow203(unittest.TestCase):
    def same(self, source, args=(), **options):
        old, oldstates, _ = record(source, args, target='interpreter', **options)
        new, states, function = record(source, args, **options)
        self.assertEqual(new, old)
        self.assertEqual(oldstates, [])
        self.assertTrue(states, 'must execute compiled body, not unsupported whole-AST fallback')
        self.assertTrue(all(state.asynchronous and state.closed and not state.frames for state in states))
        self.assertGreater(info(function.owner, function)['resumeCalls'], 0)
        self.assertEqual(new[-2:], (0, 0))
        return new, states, function

    def test_real_control_cfg_await_loop_branch_and_complete_results(self):
        cases = [
            ('async fn(n){var i=0;var total=0;while i<n{await sleep(0);i=i+1;if i==2{continue}else{null};total=total+i};return_values(total,"done")}', [3]),
            ('async fn(xs){var total=0;for [a,b] in xs{await sleep(0);total=total+a+b}else{total=total+1};total}', [[[20,1],[19,1]]]),
            ('async fn(n){try{await sleep(0);if n>0{throw n}else{n}}catch e{e.value+2}finally{null}}', [40]),
            ('async fn(n){var events=[];let v=with closing((fn*(){try{yield n}finally{events=append(events,"closed")}})()) as g{await sleep(0);next(g).value};[v,events]}', [42]),
        ]
        for source, args in cases:
            with self.subTest(source=source): self.same(source, args)

    def test_cold_task_creation_uses_same_task_and_actual_flow_after_await(self):
        e = Evaluator(); seen = []
        def choose(function):
            with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('portable')):
                compiled = compile_function(e, function, 'auto-flow')
            seen.append(compiled); return compiled
        e.env.bind('choose', Native('choose', choose, e, captures=()))
        result = e.run('var calls=0;let f=choose(async fn(x){calls=calls+1;await sleep(0);x+2});let t=f(40);let before=calls;let answer=await t;[type_of(t),before,answer,calls]')
        self.assertEqual(result, ['Task', 0, 42, 1])
        self.assertGreater(seen[0].compiled_plan.flow.await_suspensions, 0)
        self.assertGreater(info(e, seen[0])['portableResumes'], 0)
        self.assertEqual(len(compile_info(e, seen[0])), 12); self.assertEqual(len(info(e, seen[0])), 11)

    def test_effect_metadata_is_conservative_and_compile_never_calls_tools(self):
        calls = []; e = Evaluator(tool_handler=lambda name, value: calls.append((name, value)) or value)
        plain = e.run('async fn(tool){await sleep(0);tool(40)}')
        with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('portable')):
            compiled = compile_function(e, plain, 'auto-flow')
        self.assertEqual(calls, [])
        program = compiled.compiled_plan.flow; validate_program(program)
        self.assertTrue(any('suspend' in row for row in program.effects))
        self.assertTrue(any({'call','unknown'} <= row for row in program.effects))
        self.assertTrue(any(row[0] == 'await' for row in program.instructions))
        e.env.bind('user_tool', Native('user_tool', lambda x: calls.append(('actual', x)) or x+2, e, captures=()))
        self.assertEqual(e.invoke(compiled, [e.env.get('user_tool')], force=True), 42)
        self.assertEqual(calls, [('actual',40)]); e.suspension.finish()

    def test_cancel_and_budget_cutpoints_match_original_without_prefix_replay(self):
        source = 'async fn(n){var i=0;var result=0;while i<n{i=i+1;await sleep(0);result=result+i};result}'
        complete, _, _ = self.same(source, [2])
        for value in (1, 2, 5, 12, complete[1]-1, complete[1]):
            with self.subTest(limit=value): self.assertEqual(record(source,[2],limit=value)[0], record(source,[2],target='interpreter',limit=value)[0])
            with self.subTest(cancel=value): self.assertEqual(record(source,[2],cancel=value)[0], record(source,[2],target='interpreter',cancel=value)[0])

    def test_error_identity_and_cause_survive_compiled_host_event(self):
        for target in ('auto-flow','interpreter'):
            fault = LanguageError('same', code='same_fault'); cause = ValueError('cause'); fault.__cause__ = cause
            e, function = make('async fn(f){await sleep(0);f()}', target)
            def fail(): raise fault
            callback = Native('fail', fail, e, captures=())
            try:
                with self.assertRaises(LanguageError) as caught: e.invoke(function, [callback], force=True)
                self.assertIs(caught.exception, fault); self.assertIs(caught.exception.__cause__, cause)
            finally: e.suspension.finish(fault)

    def test_closed_state_is_retired_and_cannot_be_changed_into_live_dispatch(self):
        _, states, _ = self.same('async fn(){await sleep(0);42}')
        for state in states:
            self.assertEqual(state.roots(), ())
            self.assertIsNone(state.pending_cursor); self.assertIsNone(state.pending_request)
            state.phase = 'dispatch'
            with self.assertRaises(LanguageError) as caught: state.validate()
            self.assertEqual(caught.exception.code, 'ownership_error'); state.phase = 'closed'

    def test_program_replacement_is_rejected_before_any_next_dispatch(self):
        _, states, _ = self.same('async fn(){await sleep(0);42}')
        state = states[0]; original = state.program.effects
        state.program.effects = tuple(list(original))
        try:
            with self.assertRaises(LanguageError) as caught: state.validate(full=True)
            self.assertEqual(caught.exception.code, 'ownership_error')
        finally: state.program.effects = original

    def test_before_entry_definition_change_falls_back_without_partial_compilation(self):
        e, function = make('async fn(x){await sleep(0);x+2}')
        function.body[1][-1] = copy.deepcopy(function.body[1][-1])
        self.assertEqual(e.invoke(function,[40],force=True),42)
        self.assertEqual(compile_info(e,function)['lastFallbackReason'],'definition_changed')
        self.assertEqual(info(e,function)['resumeCalls'],0); e.suspension.finish()

    def test_existing_targets_and_generator_flavors_remain_explicitly_unsupported(self):
        for source in ('fn*(){yield 42}', 'async fn*(){yield 42}'):
            for target in ('auto','auto-control'):
                e, function = make(source,target); self.assertEqual(compile_info(e,function)['status'],'unsupported')
                self.assertIsNone(function.compiled_plan.flow)
            e, function = make(source,'auto-flow'); e.env.bind('generator208',function)
            self.assertIsNotNone(function.compiled_plan.flow);self.assertEqual(info(e,function)['resumeCalls'],0)
            value=e.run('let s=generator208();'+('await anext(s)'if source.startswith('async')else'next(s)'))
            self.assertEqual(value,{'done':False,'value':42});self.assertGreater(info(e,function)['portableResumes'],0)
            e.suspension.finish()
        for target in ('auto','auto-control'):
            e, function = make('async fn(){42}',target); self.assertEqual(compile_info(e,function)['status'],'unsupported')

    def test_backend_missing_strict_target_and_real_native_when_available(self):
        e = Evaluator(); plain = e.run('async fn(x){await sleep(0);if x>0{x+2}else{0}}')
        with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('unavailable')):
            with self.assertRaises(LanguageError) as caught: compile_function(e, plain, 'native-flow')
        self.assertEqual(caught.exception.code,'compiler_error')
        from test_native_jit import NATIVE
        if NATIVE:
            function = compile_function(e,plain,'native-flow'); self.assertEqual(e.invoke(function,[40],force=True),42)
            self.assertGreater(info(e,function)['nativeResumes'],0); self.assertGreater(function.compiled_plan.flow.await_suspensions,0)
            self.assertIn('switch i32',str(function.compiled_plan.flow.kernel._module)); e.suspension.finish()

    def test_corrupt_pending_state_is_validated_on_throw_and_hard_cancel_before_child_resume(self):
        from test_operator_execution_frames import active
        from test_callable_execution_frames import pending
        from chaos.execution_frames import ensure_cursor
        from chaos.suspension import Await
        from chaos.language import ResourceLimit
        for hard in (False,True):
            events=[];e,function=make('async fn(note){note("prefix");await sleep(0);note("suffix");42}')
            note=Native('note',lambda value:events.append(value),e,captures=())
            original=FlowState.__init__;seen=[]
            def entered(state,*args,**kw):original(state,*args,**kw);seen.append(state)
            with active(e),patch.object(FlowState,'__init__',entered):
                cursor=ensure_cursor(e,e._s_invoke(function,[note],force=True))
                request=pending(cursor);self.assertIs(type(request),Await)
                state=seen[0];self.assertEqual(state.phase,'host');state.epoch=True
                fault=ResourceLimit('cancel')if hard else LanguageError('injected',code='injected')
                with self.assertRaises(LanguageError)as caught:cursor.throw(fault)
                if hard:self.assertIs(caught.exception,fault)
                else:self.assertEqual(caught.exception.code,'ownership_error')
                self.assertEqual(events,['prefix']);self.assertTrue(state.closed)
                self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
                cursor.close()
