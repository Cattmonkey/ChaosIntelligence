"""Execute the actual illustrative async-flow Markdown fence, without a copy."""
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from chaos.compiled_flow_state import FlowState
from chaos.framework import Framework


class AsyncFlowDocumentation204(unittest.TestCase):
    def test_actual_unregistered_async_flow_snippet_executes_compiled_and_retires(self):
        root = Path(__file__).resolve().parents[1]
        markdown = (root / 'docs/NATIVE_FLOW.md').read_text(encoding='utf-8')
        heading = '## 异步函数体与效果恢复'
        self.assertEqual(markdown.count(heading), 1)
        section = markdown.split(heading, 1)[1]
        fences = re.findall(r'^```chaos([^\n]*)\n(.*?)^```\s*$', section, re.M | re.S)
        self.assertEqual(len(fences), 1)
        info, source = fences[0]
        self.assertNotIn('example=', info)
        states = []
        original = FlowState.__init__
        def entered(state, *args, **kwargs):
            original(state, *args, **kwargs)
            states.append(state)
        with patch.object(FlowState, '__init__', entered):
            result = Framework.evaluate(source, {}, step_limit=30000, timeout=10)
        self.assertEqual(result, ['Task', 42, 'pc-host-events/1'])
        self.assertTrue(states, 'The Markdown must execute actual compiled flow, not whole-AST fallback')
        self.assertTrue(all(state.asynchronous for state in states))
        self.assertGreater(sum(state.program.resume_calls for state in states), 0)
        for state in states:
            self.assertTrue(state.closed)
            self.assertEqual(state.phase, 'closed')
            self.assertEqual(state.frames, [])
            self.assertEqual(state._owned_activations, [])
            self.assertIsNone(state.pending_cursor)
            self.assertIsNone(state.pending_request)
            self.assertEqual(state.roots(), ())
