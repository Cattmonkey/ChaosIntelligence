"""A suppressed completed auxiliary result is not an argument to a later narrow method."""
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos.language import Evaluator, Native, LanguageError
from chaos import agent_tools, generic_functions as generic
from chaos.execution_frames import ensure_cursor
from chaos.execution_callables import GenericResolveFrame
from chaos.capture_graph import seal
from chaos.suspension import Effect
from _callable_reference191 import reference
from test_operator_execution_frames import active
from test_callable_execution_frames import pending, owned_frame


class GenericDeadCompleted194(unittest.TestCase):
    def test_suppressed_previous_auxiliary_value_is_not_narrowed_by_the_next_method(self):
        outcomes = []
        for legacy in (False, True):
            e = Evaluator(tool_handler=lambda name, value: value)
            saved = []
            early = generic.Method(('Int',), Native('early', lambda x: saved[0], e), 'before', None)
            late = generic.Method(('Int',), Native('late', lambda x: Effect('tool', {'name': 'wait', 'value': 0}), e,
                                                 capabilities=frozenset()), 'before', frozenset())
            primary = generic.Method(('Int',), Native('primary', lambda x: 42, e), 'primary', None)
            descriptor = generic.Descriptor(e, 'example', 1, (early, late, primary))
            combination = generic.Combination((primary,), (early, late), (), ())
            request = generic.GenericRequest(descriptor, (42,), {}, combination)
            original_scope = agent_tools.scope
            events = []
            modern_steps = []
            original_step = GenericResolveFrame.step

            class Scope:
                def __init__(self, evaluator, ceiling):
                    self.cm = original_scope(evaluator, ceiling)
                def __enter__(self):
                    return self.cm.__enter__()
                def __exit__(self, kind, error, traceback):
                    events.append(None if kind is None else kind.__name__)
                    result = self.cm.__exit__(kind, error, traceback)
                    return bool(result or (isinstance(error, LanguageError) and error.code == 'generic_protocol_error'))

            def step(frame, cursor, value, error):
                modern_steps.append(frame)
                return original_step(frame, cursor, value, error)

            with active(e, None), reference() if legacy else nullcontext() as witnesses, \
                    patch.object(agent_tools, 'scope', Scope), patch.object(GenericResolveFrame, 'step', step):
                # This is an actual generic next native with its declared captures.
                # The early host callback returns it once, without making it an
                # input or declared capture of the later narrow callback.
                wide = generic.continuation(e, request, combination, 0, 'primary')
                saved.append(wide)
                # Resolve through the module attribute inside the reference
                # context: never bind the modern function before patching it.
                cursor = ensure_cursor(e, generic.resolve(e, request))
                try:
                    observed = pending(cursor)
                    self.assertIs(type(observed), Effect)
                    self.assertEqual(observed.payload['name'], 'wait')
                    if legacy:
                        self.assertGreater(witnesses['generic.resolve'], 0)
                        self.assertEqual(modern_steps, [])
                    else:
                        self.assertTrue(modern_steps)
                        live=owned_frame(self,cursor,GenericResolveFrame)
                        self.assertIs(live,modern_steps[-1])
                        self.assertIs(live.method,late)
                        self.assertEqual(live.phase,'before_return')
                    self.assertIsNone(wide.capabilities)
                    self.assertIsNone(wide.generic.capabilities)
                    seal(e, (cursor,), None)
                    outcomes.append((wide.capabilities, wide.generic.capabilities, list(events)))
                finally:
                    cursor.close()
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertIsNone(outcomes[1][0])
        self.assertIsNone(outcomes[1][1])
