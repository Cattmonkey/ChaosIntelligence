"""True native two-close frontiers, including successful structural commits.

The old algorithm on the current interpreter is the oracle.  In particular,
this test does not turn Python3.13 native close completion into Python3.9
behavior, or infer success from a StopIteration message.
"""
import copy
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, Parser, Cell, LanguageError
from chaos.execution_patterns import PatternSyntaxFrame
from chaos.suspension import Effect, CHECKPOINT
from chaos.execution_frames import (ExecutionCursor, EvaluationRequest, InvocationRequest,
                                    ProtectedValueRequest, gateway)
from _pattern_reference196 import reference
from test_operator_execution_frames import active


class PatternCloseCompletion196(unittest.TestCase):
    def test_native_close_completion_commit_guard_and_second_close_frontier(self):
        scenarios = [
            ('let_pattern', 'let [a]=[0]', 'rhs', [42], 'stop'),
            ('var_pattern', 'var [a]=[0]', 'rhs', [42], 'stop'),
            ('assign_pattern', '[a]=[0]', 'rhs', [42], 'stop'),
            ('match', 'match 0{x=>tool("chosen",x)}', 'subject', 42, 'stop'),
            ('match', 'match 42{x if true=>tool("chosen",x);_=>tool("fallback",0)}', 'guard', True, 'stop'),
            ('match', 'match 42{x if true=>tool("chosen",x);_=>tool("fallback",0)}', 'guard', False, 'stop'),
            ('match', 'match 42{x if true=>tool("chosen",x);_=>0}', 'guard', 1, 'stop'),
            ('match', 'match 42{x=>0}', 'body', 42, 'stop'),
            ('match', 'match 42{x if true=>0;_=>0}', 'guard', None, 'runtime'),
        ]
        scenarios = [row + (action,) for row in scenarios for action in ('close', 'send', 'throw')]
        retained = []
        for kind, source, phase, completion, failure_kind, action in scenarios:
            with self.subTest(kind=kind, phase=phase, completion=completion, failure=failure_kind, resume=action):
                outcomes = []
                # Old first: this is an actual native-generator comparison on
                # each interpreter, never a hardcoded close-version table.
                for legacy in (True, False):
                    e = Evaluator(tool_handler=lambda name, value: value)
                    if kind == 'assign_pattern':
                        e.env.bind('a', Cell(1, 'Int'))
                    node = Parser(source).program()[1][0]
                    child = (node[1] if phase == 'subject' else
                             node[2][0][1] if phase == 'guard' else
                             node[2][0][2] if phase == 'body' else
                             node[2] if kind == 'assign_pattern' else node[3])
                    value = copy.deepcopy(completion)
                    cause = ValueError('original raw close cause')
                    fault = StopIteration(value) if failure_kind == 'stop' else RuntimeError('raw close failure')
                    if failure_kind == 'runtime':
                        fault.__cause__ = cause
                    resume_fault = LanguageError('original resume failure', code='pattern_resume_test')
                    events = []; held = []; child_iterators = []; errors = []; frames = []; witnessed = {}

                    class RawChild:
                        def __init__(self, event_rows, original_fault):
                            self.event_rows, self.original_fault = event_rows, original_fault
                        def __iter__(self): return self
                        def __next__(self): return self.send(None)
                        def send(self, sent):
                            self.event_rows.append(('send', sent))
                            return Effect('tool', {'name': 'raw-pending', 'value': 42})
                        def throw(self, error): raise error
                        def close(self):
                            self.event_rows.append(('close', type(self.original_fault).__name__))
                            raise self.original_fault

                    raw = RawChild(events, fault); original_evaluate = e._s_evaluate
                    def evaluate(actual, env, *args, **kwargs):
                        events.append(('evaluate', actual[0], actual is child, repr(actual)))
                        result = raw if actual is child else original_evaluate(actual, env, *args, **kwargs)
                        child_iterators.append(result)
                        return result
                    original_step = PatternSyntaxFrame.step
                    def step(frame, cursor, sent, error):
                        name = type(frame).__name__; witnessed[name] = witnessed.get(name, 0) + 1
                        frames.append((frame, cursor))
                        self.assertIs(cursor.owner, e)
                        return original_step(frame, cursor, sent, error)

                    with active(e, None), reference(retained=held) if legacy else nullcontext() as reached, patch.object(e, '_s_evaluate', evaluate), patch.object(PatternSyntaxFrame, 'step', step):
                        # Use the public native iterator itself. Wrapping it in
                        # an outer cursor would hide its second-close frontier.
                        iterator = e._s_execute(node, e.env)
                        native = iterator.iterator if legacy else iterator
                        # Retain the actual native/delegate even if an advance
                        # or witness assertion fails before both observations.
                        retained.append((iterator, native, raw, fault, cause, errors, held, child_iterators, frames))
                        def advance_public(actual, sent=None):
                            # Pattern's declared close cursor forwards genuine
                            # Evaluation/Invocation requests to its caller. A
                            # direct native caller must execute those gateways
                            # before it reaches the selected raw child Effect.
                            # Keep this driver separate from the iterator whose
                            # actual native close frontier is under test.
                            for _ in range(4096):
                                try: request = actual.send(sent)
                                except StopIteration as completed: return True, completed.value
                                if request is CHECKPOINT:
                                    sent = None; continue
                                if type(request) in (EvaluationRequest, InvocationRequest, ProtectedValueRequest):
                                    nested = gateway(e, request)
                                    self.assertIsInstance(nested, ExecutionCursor)
                                    child_iterators.append(nested)
                                    done, result = advance_public(nested)
                                    self.assertTrue(done, 'pre-target gateway must finish, not fabricate a raw Effect')
                                    sent = result; continue
                                return False, request
                            self.fail('bounded real native gateway advance did not reach its pending child')
                        done, request = advance_public(iterator)
                        self.assertFalse(done)
                        self.assertIsInstance(request, Effect)
                        self.assertEqual(request.payload['name'], 'raw-pending')
                        if legacy:
                            self.assertGreater(reached[kind], 0)
                            self.assertEqual(witnessed, {})
                            self.assertTrue(any(old is native and old.gi_code is code for _, old, code in held))
                        else:
                            self.assertGreater(witnessed.get('PatternSyntaxFrame', 0), 0)
                            self.assertTrue(frames)
                            self.assertIs(frames[-1][0].node, node)
                            self.assertEqual(frames[-1][0].phase, phase)
                        observations = []
                        for close_index in range(2):
                            error = None; returned = None
                            try:
                                if close_index == 0 or action == 'close': returned = iterator.close()
                                elif action == 'send': returned = iterator.send(42)
                                else: returned = iterator.throw(resume_fault)
                            except BaseException as caught:
                                error = caught; errors.append((caught, caught.__traceback__))
                            actual_cause = getattr(error, '__cause__', None)
                            bound = e.env.values.get('a')
                            observations.append({
                                'closeIndex': close_index,
                                'errorType': type(error).__name__ if error is not None else None,
                                'errorMessage': str(error) if error is not None else None,
                                'errorCode': getattr(error, 'code', None),
                                'errorIsOriginalFault': error is fault,
                                'errorIsOriginalResumeFault': error is resume_fault,
                                'stopValue': copy.deepcopy(error.value) if type(error) is StopIteration else None,
                                'causeType': type(actual_cause).__name__ if actual_cause is not None else None,
                                'causeIsOriginalFault': actual_cause is fault,
                                'causeIsOriginalExplicitCause': actual_cause is cause,
                                'returned': returned,
                                'events': copy.deepcopy(events),
                                'rawCloseCount': sum(row[0] == 'close' for row in events),
                                'aExists': 'a' in e.env.values,
                                'aIsCell': type(bound) is Cell,
                                'aValue': bound.value if type(bound) is Cell else bound,
                                'steps': e.steps, 'depth': e.depth, 'frames': len(e.frames),
                                'nativeClosed': native.gi_frame is None,
                            })
                        # Record both boundaries before any semantic comparison
                        # so a negative retains the real failure, not IndexError.
                        outcomes.append(observations)
                        self.assertEqual(observations[0]['rawCloseCount'], 1)
                        # A native pending frontier may close its raw delegate
                        # again on the second close/throw. Compare that actual
                        # count and every event to the old algorithm below.
                        if failure_kind == 'runtime':
                            self.assertTrue(observations[0]['errorIsOriginalFault'])
                            self.assertTrue(observations[0]['causeIsOriginalExplicitCause'])
                        if kind != 'match' and observations[0]['errorType'] is None:
                            # A native close which completed the original RHS
                            # must perform the real atomic commit, with the
                            # original declaration/assignment Cell contract.
                            self.assertTrue(observations[0]['aExists'])
                            self.assertEqual(observations[0]['aValue'], 42)
                            self.assertEqual(observations[0]['aIsCell'], kind != 'let_pattern')
                self.assertEqual(len(outcomes), 2)
                self.assertEqual(outcomes[1], outcomes[0])
        # Strong references above prevent GC from silently performing the
        # second close before it is observed. Explicit cleanup is last.
        for iterator, native, raw, fault, cause, errors, held, children, frames in retained:
            if native.gi_frame is not None:
                try: iterator.close()
                except BaseException as error: errors.append((error, error.__traceback__))
