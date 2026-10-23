"""Catch* partitions are live cursor state, including suspended guards/handlers."""
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, Env, LanguageError, Native, Parser
from chaos.execution_frames import ExecutionCursor, GroupTryFrame, frame_roots
from chaos.errors import GroupedError, leaves
from chaos.suspension import CHECKPOINT


class ExecutionGroupFrames(unittest.TestCase):
    def frame(self, evaluator):
        return next(frame for frame in reversed(evaluator._execution_cursor.frames)
                    if type(frame) is GroupTryFrame)

    def test_legacy_partition_has_exact_ticks_spans_effects_and_failures(self):
        from _execution_group_reference import legacy_group
        original_execute = Evaluator._s_execute

        def old_execute(evaluator, node, env, tail=False, **options):
            if node[0] == 'try_group':
                evaluator.tick()
                return (yield from legacy_group(evaluator, node, env, options.get('_completion')))
            return (yield from original_execute(evaluator, node, env, tail, **options))

        def snapshot(source, old):
            from contextlib import ExitStack
            from chaos.language import serializable
            events, effects = [], []
            e = Evaluator(tool_handler=lambda name, value: effects.append((name, value)) or value)
            e.trace_handler = events.append
            with ExitStack() as stack:
                if old:
                    stack.enter_context(patch.object(GroupTryFrame, 'KINDS', set()))
                    stack.enter_context(patch.object(Evaluator, '_s_execute', old_execute))
                try:
                    result = ('value', serializable(e.run(source)))
                except LanguageError as error:
                    result = ('error', error.record())
            return result, e.steps, events, effects

        cases = [
            'try{tool("body",42)}catch* _ as group{tool("unexpected",0)}else{tool("else",43)}',
            'try{throw error_group("root",[error(20),error(22)])}catch* {value:x,.._} if do{tool("guard",x);x==20} as group{tool("first",20)}catch* _ as group{tool("second",22)}',
            'try{throw error_group("root",[error(20),error(22)])}catch* _ as group{throw}',
            'try{throw error_group("root",[error(20),error(22)])}catch* {value:x,.._} if do{if x==22{throw 99}else{true}} as group{tool("unexpected",0)}',
            'try{throw error_group("root",[error(20),error(22)])}catch* {value:20,.._} as group{throw 99}catch* _ as group{tool("second",22)}',
            'multiple_value_list(fn(){try{throw 1}catch* _ as group{return_values(20,22)}})',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(snapshot(source, False), snapshot(source, True))

    def test_guard_and_handler_use_real_authoritative_cursor_fields(self):
        e = Evaluator()
        observed = []

        def inspect(phase):
            frame = self.frame(e)
            self.assertEqual(frame.phase, phase)
            self.assertIs(frame.owner, e)
            self.assertTrue(frame.context_active)
            self.assertIs(frame.original, frame.occurrences[0][1])
            self.assertIn(frame.original, frame_roots(frame))
            observed.append((phase, len(frame.remaining), len(frame.selected)))
            return True

        e.env.bind('inspect', Native('inspect', inspect, e))
        self.assertEqual(e.run('try{throw 42}catch* _ if inspect("guard") as group{inspect("handler");group.exceptions[0].value}'), 42)
        self.assertEqual(observed, [('guard', 1, 0), ('handler', 0, 1)])
        self.assertIsNone(e.handled_error)
        self.assertIsNone(e.error_context)

    def test_changed_pending_remaining_field_changes_the_actual_partition(self):
        e = Evaluator()
        seen = []

        def omit_second():
            frame = self.frame(e)
            self.assertEqual(frame.phase, 'guard')
            self.assertEqual(frame.remaining, {(0,), (1,)})
            frame.remaining.remove((1,))
            seen.append(frame.path)
            return True

        e.env.bind('omit_second', Native('omit_second', omit_second, e))
        source = 'try{throw error_group("root",[error(20),error(22)])}catch* _ if omit_second() as group{map(fn(x){x.value},group.exceptions)}'
        self.assertEqual(e.run(source), [20])
        self.assertEqual(seen, [(0,)])

    def test_suspended_guard_keeps_uncommitted_duplicate_occurrences(self):
        e = Evaluator()
        source = 'fn* stream(){let same=error(7);try{throw error_group("root",[same,same])}catch* _ if do{yield 1;true} as group{yield length(group.exceptions)}};let s=stream();[next(s).value,next(s).value,next(s).value,next(s).done]'
        self.assertEqual(e.run(source), [1, 1, 2, True])

    def test_guard_failure_does_not_commit_any_selected_occurrence(self):
        source = 'try{throw error_group("root",[error(1),error(2)])}catch* {value:n,.._} if do{if n==2{throw 99}else{true}} as group{throw 100}catch* _ as rest{throw 101}'
        with self.assertRaises(GroupedError) as caught:
            Evaluator().run(source)
        self.assertEqual([leaf.value for _, leaf in leaves(caught.exception)], [99, 1, 2])

    def test_handler_reraise_keeps_original_tree_and_error_identity(self):
        e = Evaluator()
        original = e.run('error_group("root",[error(20),error(22)])')
        e.env.bind('original', original)
        with self.assertRaises(GroupedError) as caught:
            e.run('try{throw original}catch* _ as group{throw}')
        self.assertIs(caught.exception, original.exception)
        self.assertIsNone(e.handled_error)
        self.assertIsNone(e.error_context)

    def test_async_guard_and_handler_resume_without_replaying_leaf_effects(self):
        e = Evaluator()
        calls = []
        e.env.bind('mark', Native('mark', lambda value: calls.append(value) or True, e))
        source = 'async fn f(){try{throw error_group("root",[error(20),error(22)])}catch* {value:n,.._} if do{mark(n);await sleep(0);true} as group{await sleep(0);length(group.exceptions)}};await f()'
        self.assertEqual(e.run(source), 2)
        self.assertEqual(calls, [20, 22])

    def test_handler_multiple_values_and_try_else_completion_are_distinct(self):
        e = Evaluator()
        source = 'let normal=try{20}catch* _ as group{0}else{42};let handled=multiple_value_list(fn(){try{throw 1}catch* _ as group{return_values(20,22)}else{return_values(0,0)}});[normal,handled]'
        self.assertEqual(e.run(source), [42, [20, 22]])

    def test_forced_close_restores_dynamic_context_without_running_handler(self):
        e = Evaluator()
        old = LanguageError('outer')
        e.handled_error = e.error_context = old
        calls = []
        e.env.bind('mark', Native('mark', lambda: calls.append(1), e))
        tree = Parser('try{throw 42}catch* _ if do{true} as group{mark()}').program()
        e.suspension.begin()
        cursor = e._s_evaluate(tree, Env(e.env))
        self.assertIs(type(cursor), ExecutionCursor)
        frame = None
        try:
            for _ in range(100):
                request = next(cursor)
                active = [item for item in cursor.frames if type(item) is GroupTryFrame]
                if active and active[-1].phase == 'guard':
                    frame = active[-1]
                    self.assertIs(request, CHECKPOINT)
                    break
            self.assertIsNotNone(frame)
            self.assertTrue(frame.context_active)
            cursor.close()
            self.assertFalse(frame.context_active)
            self.assertIs(e.handled_error, old)
            self.assertIs(e.error_context, old)
            self.assertEqual(calls, [])
            self.assertEqual(cursor.frames, [])
        finally:
            cursor.close()
            e.suspension.finish()


if __name__ == '__main__':
    unittest.main()
