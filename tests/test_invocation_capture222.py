"""Real held invocation work, exact old charged traversal, and authoritative stages.

The old generator oracle and first-wait fixture remain separate test dependencies.
No product root, queue, tick, counter, scheduler or invocation algorithm is replaced.
"""
import copy
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError, Native
from chaos.execution_frames import InvocationFrame, InvocationRequest, InvocationCloseCursor
from chaos.execution_invocation import InvocationDispatchFrame
from chaos.suspension import Effect
import chaos.control_prompts as control
import chaos.multiple_values as values

from _capture_paths204 import observed_seal
import _invocation_fixture222 as fixture


def native_setup(calls):
    def setup(e):
        producer = Native('producer222', lambda: Effect('tool', {'tool': 'native_hold', 'value': 40}), e)
        def seed():
            calls.append('seed')
            return control.Request('fixture-handoff', ())
        e.env.bind('seed222', Native('seed222', seed, e))
        e._core_producer222 = producer
    return setup


def handoff(calls):
    def resolve(e, request):
        if request.operation != 'fixture-handoff':
            raise AssertionError('Unexpected controlled native handoff')
        calls.append('control')
        if False:
            yield None
        return values.request(e, 'list', producers=(e._core_producer222,))
    return resolve


def source_for(kind, padding):
    original = {'lookup': fixture.LOOKUP, 'agent': fixture.AGENT, 'native': fixture.NATIVE}[kind]
    source = original.replace('async fn work(){', 'async fn work(pad){').replace('let task=work();', 'let task=work(range(' + str(padding) + '));')
    if source == original:
        raise AssertionError('The actual Task must retain the requested padding binding')
    return source


def captured(test, kind, padding, legacy):
    paths = []
    stages = []
    calls = []
    seal = observed_seal()
    def inspect(e, task, target, old):
        test.assertIs(old, legacy)
        pad = task.capture_roots[1]['pad']
        test.assertIs(type(pad), list)
        test.assertEqual(pad, list(range(padding)))
        test.assertFalse(task.runner.future.done())
        test.assertEqual(task.runner.state, 'effect')
        matches = [] if old else [(frame, cursor) for frame, cursor in fixture.owned_frames(test, task.runner.iterator)
                                   if frame.state.fn is target and frame.phase == kind]
        test.assertEqual(len(matches), 0 if old else 1)
        if not old:
            frame, cursor = matches[0]
            bridge = cursor.parent_bridge
            test.assertIs(bridge.dispatch_cursor, cursor)
            test.assertIsNot(getattr(bridge, 'pending_cursor', None), cursor)
            test.assertIs(frame.state.owner, e)
            test.assertEqual(frame.epoch, e.suspension.epoch)
            frame.validate_state()
            stages.append((frame.phase, frame.native_stage))
        _, charged_ticks, vector = seal(e, (task,), None)
        paths.append((charged_ticks, vector))
    options = dict(legacy=legacy, phase=kind, agent=kind == 'agent', pending_check=inspect)
    if kind == 'native':
        options['setup'] = native_setup(calls)
        with patch.object(control, 'resolve', handoff(calls)):
            row, seen, pending = fixture.run(test, source_for(kind, padding), **options)
    else:
        row, seen, pending = fixture.run(test, source_for(kind, padding), **options)
    test.assertEqual(len(paths), 1)
    test.assertEqual(len(stages), 0 if legacy else 1)
    test.assertEqual(row[0], ('value', [40] if kind == 'native' else 42))
    test.assertEqual(calls, ['seed', 'control'] if kind == 'native' else [])
    return row, paths, calls


class InvocationCapture222(unittest.TestCase):
    def compare_offsets(self, kind):
        for padding in range(65):
            with self.subTest(kind=kind, padding=padding):
                raw = captured(self, kind, padding, True)
                modern = captured(self, kind, padding, False)
                # Even physical children metadata remains unchanged: the new
                # dispatch slot is separate from the old pending_cursor roots.
                self.assertEqual(modern, raw)

    def test_agent_pending_all_65_offsets_keep_original_charged_paths_and_once_effect(self):
        self.compare_offsets('agent')

    def test_native_pending_all_65_offsets_keep_original_charged_paths_and_once_prefix(self):
        self.compare_offsets('native')

    def test_callable_lookup_all_65_offsets_keep_original_charged_paths_and_once_descriptor(self):
        self.compare_offsets('lookup')

    def test_supplementary_dispatch_foreign_stage_epoch_and_capability_metadata_fail_closed(self):
        calls = []
        inspected = []
        retained = []
        seal = observed_seal()
        def inspect(e, task, target, legacy):
            self.assertFalse(legacy)
            matches = [(frame, cursor) for frame, cursor in fixture.owned_frames(self, task.runner.iterator)
                       if frame.state.fn is target and frame.phase == 'native']
            self.assertEqual(len(matches), 1)
            frame, cursor = matches[0]
            bridge = cursor.parent_bridge
            original_pending = getattr(bridge, 'pending_cursor', None)
            self.assertIs(bridge.dispatch_cursor, cursor)
            self.assertIsNot(original_pending, cursor)
            other = Evaluator()
            foreign_state = InvocationFrame(InvocationRequest(Native('foreign222', lambda: 42, other), []))
            foreign_state.owner = other
            foreign = InvocationCloseCursor(other, InvocationDispatchFrame(foreign_state))
            retained.extend((other, foreign_state, foreign))
            cases = [(bridge, 'dispatch_cursor', foreign),
                     (cursor, 'parent_bridge', None),
                     (frame, 'native_stage', True),
                     (frame, 'native_stage', len(frame.STAGES) + 1),
                     (frame, 'epoch', True),
                     (frame, 'epoch', e.suspension.epoch + 1),
                     (frame, 'capabilities', ['not-an-immutable-authority'])]
            for subject, name, bad in cases:
                with self.subTest(field=name, bad_type=type(bad).__name__):
                    saved = getattr(subject, name)
                    try:
                        setattr(subject, name, bad)
                        with self.assertRaises(LanguageError) as caught:
                            seal(e, (task,), None)
                        self.assertEqual(caught.exception.code, 'ownership_error')
                        retained.extend((caught.exception, caught.exception.__traceback__))
                    finally:
                        setattr(subject, name, saved)
                    self.assertIs(bridge.dispatch_cursor, cursor)
                    self.assertIs(getattr(bridge, 'pending_cursor', None), original_pending)
            frame.validate_state()
            seal(e, (task,), None)
            inspected.append(len(cases))
        with patch.object(control, 'resolve', handoff(calls)):
            row, _, _ = fixture.run(self, fixture.NATIVE, phase='native', setup=native_setup(calls), pending_check=inspect)
        self.assertEqual(row[0], ('value', [40]))
        self.assertEqual(calls, ['seed', 'control'])
        self.assertEqual(inspected, [7])
        self.assertEqual(len(row[2]), 1)

    def test_actual_pending_native_stage_selects_the_later_effect_without_replaying_seed(self):
        # The real first resolver returns a real later Effect after its held
        # Future. Skipping only the final station must skip that Effect, proving
        # native_stage controls dispatch rather than merely recording a count.
        def run(legacy=False, skip=False):
            calls = []
            held = []
            def setup(e):
                def seed():
                    calls.append('seed')
                    return control.Request('stage-handoff', ())
                e.env.bind('seed222', Native('seed222', seed, e))
            def resolve(e, request):
                self.assertEqual(request.operation, 'stage-handoff')
                calls.append('control')
                yield Effect('tool', {'tool': 'stage_hold', 'value': 40})
                return Effect('tool', {'tool': 'later', 'value': 42})
            def inspect(e, task, target, old):
                if old:
                    held.append('raw')
                    return
                matches = [(frame, cursor) for frame, cursor in fixture.owned_frames(self, task.runner.iterator)
                           if frame.state.fn is target and frame.phase == 'native']
                self.assertEqual(len(matches), 1)
                frame, cursor = matches[0]
                self.assertIs(cursor.parent_bridge.dispatch_cursor, cursor)
                self.assertEqual(calls, ['seed', 'control'])
                if not held:
                    self.assertEqual(frame.native_stage, 1)
                    if skip:
                        frame.native_stage = len(frame.STAGES)
                        frame.validate_state()
                else:
                    self.assertFalse(skip)
                    self.assertEqual(frame.native_stage, len(frame.STAGES))
                held.append(frame.native_stage)
            with patch.object(control, 'resolve', resolve):
                row, _, _ = fixture.run(self, fixture.NATIVE, legacy=legacy, setup=setup, pending_check=inspect)
            self.assertEqual(calls, ['seed', 'control'])
            return row, held
        raw, raw_held = run(legacy=True)
        normal, held = run()
        self.assertEqual(normal, raw)
        self.assertEqual(normal[0], ('value', 42))
        self.assertEqual([payload['tool'] for kind, payload in normal[2]], ['stage_hold', 'later'])
        self.assertEqual(held, [1, len(InvocationDispatchFrame.STAGES)])
        self.assertEqual(raw_held, ['raw', 'raw'])
        skipped, skip_held = run(skip=True)
        self.assertEqual(skipped[0][0], 'error')
        self.assertEqual(skipped[0][1][0], 'LanguageError')
        self.assertEqual(skipped[0][1][1]['code'], 'runtime_error')
        self.assertEqual([payload['tool'] for kind, payload in skipped[2]], ['stage_hold'])
        self.assertEqual(skip_held, [len(InvocationDispatchFrame.STAGES)])
