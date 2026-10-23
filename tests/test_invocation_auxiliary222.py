"""The new auxiliary slot cannot hide malformed state behind a closed flag."""
import unittest
from chaos.language import Evaluator, LanguageError, Native
from chaos.execution_frames import (ensure_cursor, InvocationFrame, InvocationRequest,
                                    InvocationCloseCursor)
from chaos.execution_invocation import InvocationDispatchFrame
from chaos.capture_graph import seal
from test_operator_execution_frames import active


class InvocationAuxiliary222(unittest.TestCase):
    def test_closed_outer_still_validates_retained_dispatch_closed_flag_and_frames(self):
        e = Evaluator(timeout=10)
        calls = []
        def cold():
            if False:
                yield None
        with active(e):
            # The carrier is a real, actually closed outer cursor. Its root
            # bridge remains the supported retained-frontier audit anchor.
            outer = ensure_cursor(e, cold())
            outer.close()
            self.assertTrue(outer.closed)
            self.assertEqual(outer.frames, [])
            bridge = outer.root_bridge
            target = Native('not-entered222', lambda: calls.append('entered'), e)
            state = InvocationFrame(InvocationRequest(target, []))
            state.owner = e
            frame = InvocationDispatchFrame(state)
            dispatch = InvocationCloseCursor(e, frame)
            bridge.dispatch_cursor = dispatch
            dispatch.parent_bridge = bridge
            try:
                # This is an explicit embedding metadata test. No old public
                # generator or language effect is simulated or resumed here.
                seal(e, (outer,), None)
                self.assertIs(bridge.dispatch_cursor, dispatch)
                self.assertFalse(dispatch.started)
                self.assertEqual(frame.phase, 'start')
                dispatch.closed = True
                with self.assertRaises(LanguageError) as caught:
                    seal(e, (outer,), None)
                self.assertEqual(caught.exception.code, 'ownership_error')
                self.assertEqual(dispatch.frames, [frame])
                self.assertIs(bridge.dispatch_cursor, dispatch)
                dispatch.closed = False
                seal(e, (outer,), None)
                self.assertEqual(calls, [])
            finally:
                dispatch.closed = False
                dispatch.close()
            self.assertIsNone(bridge.dispatch_cursor)
            self.assertEqual(dispatch.frames, [])
            self.assertTrue(dispatch.closed)
            self.assertEqual(calls, [])
