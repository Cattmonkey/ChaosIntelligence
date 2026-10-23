"""Real suspended Mode work retains old capture costs and actual owned state."""
from contextlib import nullcontext
import unittest

from chaos.language import Evaluator, LanguageError, Native
from chaos.execution_frames import ExecutionCursor, ModeCloseCursor, ensure_cursor
from chaos.execution_modes import MODE_FRAMES, ModeHostFrame, ModeSerialPlanFrame, ModeInvokeFrame
from chaos.capture_graph import seal
from test_operator_execution_frames import active
from _capture_paths204 import observed_seal
from _mode_reference224 import original_modes, fixed_execution_ids
import _invocation_fixture222 as fixture


def owned_modes(test, root):
    todo = [root]; visited = set(); found = []
    while todo:
        cursor = todo.pop()
        test.assertIsInstance(cursor, ExecutionCursor)
        test.assertIs(cursor.owner, root.owner)
        test.assertNotIn(id(cursor), visited)
        visited.add(id(cursor))
        for frame in cursor.frames:
            if type(frame) in MODE_FRAMES:
                test.assertIs(frame.owner, root.owner)
                found.append((frame, cursor))
            for slot in ('pending_cursor', 'dispatch_cursor', 'mode_cursor'):
                child = getattr(frame, slot, None)
                if child is not None:
                    test.assertIs(child.owner, root.owner)
                    test.assertIs(child.parent_bridge, frame)
                    if slot == 'mode_cursor': test.assertIs(type(child), ModeCloseCursor)
                    todo.append(child)
    return found


def source(padding=0):
    return ('let task=run_plan(plan([{id:"a",call:"work"}]),'
            '{work:fn(c){tool("mode_hold",c.input.value)}},'
            '{value:40,pad:range(' + str(padding) + ')});'
            'remember222(task,null);await task')


class ModeCapture224(unittest.TestCase):
    def test_all_65_padding_offsets_preserve_actual_charged_capture_and_effect_once(self):
        observe = observed_seal()
        for padding in range(65):
            rows = []
            for old in (True, False):
                paths = []
                def inspect(e, task, target, ignored):
                    self.assertIsNone(target)
                    self.assertFalse(task.runner.future.done())
                    self.assertEqual(task.capture_roots[2]['pad'], list(range(padding)))
                    frames = owned_modes(self, task.runner.iterator)
                    if old: self.assertEqual(frames, [])
                    else:
                        selected = [(f, c) for f, c in frames if type(f) is ModeSerialPlanFrame]
                        self.assertEqual(len(selected), 1)
                        frame, cursor = selected[0]
                        self.assertEqual(frame.phase, 'step')
                        self.assertEqual(frame.epoch, e.suspension.epoch)
                        self.assertIs(cursor.parent_bridge.mode_cursor, cursor)
                        frame.validate_state()
                    _, ticks, vector = observe(e, (task,), None)
                    paths.append((ticks, vector))
                with original_modes() if old else nullcontext(), fixed_execution_ids():
                    row, _, _ = fixture.run(self, source(padding), pending_check=inspect)
                self.assertEqual(row[0], ('value', {'a': 40}))
                self.assertEqual(len(row[2]), 1)
                self.assertEqual(len(paths), 1)
                rows.append((row, paths))
            with self.subTest(padding=padding): self.assertEqual(rows[1], rows[0])

    def test_live_auxiliary_state_rejects_foreign_owner_epoch_phase_and_capability(self):
        inspected = []; retained = []
        def inspect(e, task, target, ignored):
            matches = [(f, c) for f, c in owned_modes(self, task.runner.iterator)
                       if type(f) is ModeInvokeFrame]
            self.assertEqual(len(matches), 1)
            frame, cursor = matches[0]; bridge = cursor.parent_bridge
            previous_pending = getattr(bridge, 'pending_cursor', None)
            other = Evaluator(); foreign = ModeCloseCursor(other, ModeHostFrame('unused', {}))
            retained.extend((other, foreign))
            cases = [(bridge, 'mode_cursor', foreign), (cursor, 'parent_bridge', None),
                     (cursor, 'closed', True), (frame, 'phase', 'not-a-phase'),
                     (frame, 'epoch', True), (frame, 'epoch', e.suspension.epoch + 1),
                     (frame, 'capabilities', ['not-immutable']),
                     (frame, 'action', Native('foreign', lambda value: value, other))]
            for obj, key, bad in cases:
                saved = getattr(obj, key)
                try:
                    setattr(obj, key, bad)
                    with self.subTest(field=key), self.assertRaises(LanguageError) as caught:
                        seal(e, (task,), None)
                    self.assertEqual(caught.exception.code, 'ownership_error')
                    retained.extend((caught.exception, caught.exception.__traceback__))
                finally: setattr(obj, key, saved)
                self.assertIs(bridge.mode_cursor, cursor)
                self.assertIs(getattr(bridge, 'pending_cursor', None), previous_pending)
            seal(e, (task,), None); inspected.append(True)
        with fixed_execution_ids(): row, _, _ = fixture.run(self, source(), pending_check=inspect)
        self.assertEqual(row[0], ('value', {'a': 40})); self.assertEqual(len(row[2]), 1)
        self.assertEqual(inspected, [True])

    def test_completed_prefix_field_drives_remaining_plan_without_callback_replay(self):
        program = ('var calls=0;fn first(c){calls+=1;1};'
                   'let task=run_plan(plan([{id:"a",call:"first"},{id:"b",call:"work",needs:["a"]}]),'
                   '{first:first,work:fn(c){tool("mode_hold",40)}});'
                   'remember222(task,null);let result=await task;[result,calls]')
        inspected = []
        def inspect(e, task, target, ignored):
            frames = [f for f, c in owned_modes(self, task.runner.iterator) if type(f) is ModeSerialPlanFrame]
            self.assertEqual(len(frames), 1); frame = frames[0]
            self.assertEqual(frame.results, {'a': 1})
            self.assertEqual(frame.selected['id'], 'b')
            frame.results['a'] = 42
            inspected.append(True)
        with fixed_execution_ids(): row, _, _ = fixture.run(self, program, pending_check=inspect)
        self.assertEqual(row[0], ('value', [{'a': 42, 'b': 40}, 1]))
        self.assertEqual(len(row[2]), 1); self.assertEqual(inspected, [True])

    def test_closed_outer_still_audits_retained_mode_and_cold_close_never_runs_host(self):
        e = Evaluator(); calls = []
        e.mode_handler = lambda operation, payload: calls.append(operation)
        def cold():
            if False: yield None
        with active(e):
            outer = ensure_cursor(e, cold()); outer.close()
            bridge = outer.root_bridge; frame = ModeHostFrame('recording.begin', {})
            cursor = ModeCloseCursor(e, frame)
            bridge.mode_cursor = cursor; cursor.parent_bridge = bridge
            seal(e, (outer,), None)
            self.assertFalse(cursor.started)
            cursor.closed = True
            try:
                with self.assertRaises(LanguageError) as caught: seal(e, (outer,), None)
                self.assertEqual(caught.exception.code, 'ownership_error')
            finally: cursor.closed = False
            cursor.close()
            self.assertTrue(cursor.closed); self.assertEqual(cursor.frames, [])
            self.assertIsNone(bridge.mode_cursor); self.assertEqual(calls, [])
