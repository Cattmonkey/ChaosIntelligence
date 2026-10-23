"""Adversarial joins use causal events, never timing-based sleep assertions."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled


HARD_ERRORS = (ResourceLimit, ExecutionControl, KeyboardInterrupt, SystemExit)
PAIR = '''async fn work(n){tool("fixture.work",n)}
let one=work(1);let two=work(2);
try{await gather([one,two])}catch e{e.code}'''


class TaskJoinControlTests(unittest.TestCase):
    def assert_no_effect_threads(self):
        self.assertFalse([t.name for t in threading.enumerate()
                          if t.name.startswith('chaos-effect') and t.is_alive()])

    def hard_case(self, error_type, after_cancel):
        entered = threading.Barrier(2)
        second_terminal = threading.Event()
        exited = threading.Event()
        created = []
        error = error_type('identity-marker')

        def trace(row):
            if row['event'] == 'task.created':
                created.append(row['taskId'])
            if (len(created) >= 2 and row.get('taskId') == created[1]
                    and row['event'] in ('task.failed', 'task.cancelled')):
                second_terminal.set()

        def effect(kind, payload, cancel):
            entered.wait(timeout=3)
            if payload['value'] == 1:
                if not after_cancel:
                    self.assertTrue(second_terminal.wait(3), 'second failure never reached scheduler')
                raise LanguageError('ordinary sibling', code='ordinary_fixture')
            try:
                if after_cancel:
                    self.assertTrue(cancel.wait(3), 'peer failure did not request cancellation')
                raise error
            finally:
                exited.set()

        evaluator = Evaluator(tool_handler=lambda *args: None, effect_handler=effect,
                              trace_handler=trace, timeout=10)
        with self.assertRaises(error_type) as caught:
            evaluator.run(PAIR)
        self.assertIs(caught.exception, error)
        self.assertTrue(exited.is_set())
        self.assert_no_effect_threads()

    def test_already_terminal_later_hard_error_wins_ordinary(self):
        for error_type in HARD_ERRORS:
            with self.subTest(error=error_type.__name__):
                self.hard_case(error_type, after_cancel=False)

    def test_future_hard_error_after_peer_cancel_keeps_identity(self):
        for error_type in HARD_ERRORS:
            with self.subTest(error=error_type.__name__):
                self.hard_case(error_type, after_cancel=True)

    def test_host_taskcancelled_without_cancellation_keeps_hard_identity(self):
        self.hard_case(TaskCancelled, after_cancel=False)

    def test_taskcancelled_subclass_after_cancel_is_still_hard(self):
        class HostControl(TaskCancelled):
            pass
        self.hard_case(HostControl, after_cancel=True)

    def test_cooperative_host_taskcancelled_does_not_mask_ordinary_failure(self):
        entered = threading.Barrier(2)
        finished = threading.Event()

        def effect(kind, payload, cancel):
            entered.wait(timeout=3)
            if payload['value'] == 1:
                raise LanguageError('ordinary sibling', code='ordinary_fixture')
            try:
                self.assertTrue(cancel.wait(3), 'peer cancellation was not delivered')
                raise TaskCancelled('cooperative host cancellation')
            finally:
                finished.set()

        evaluator = Evaluator(tool_handler=lambda *args: None, effect_handler=effect)
        self.assertEqual(evaluator.run(PAIR), 'ordinary_fixture')
        self.assertTrue(finished.is_set())
        self.assert_no_effect_threads()

    def test_explicit_cooperative_host_cancellation_preserves_identity(self):
        started = threading.Event()
        finished = threading.Event()
        error = TaskCancelled('explicit cooperative cancellation')

        def effect(kind, payload, cancel):
            if payload['name'] == 'fixture.started':
                self.assertTrue(started.wait(3))
                return None
            started.set()
            try:
                self.assertTrue(cancel.wait(3))
                raise error
            finally:
                finished.set()

        source = '''async fn work(){tool("fixture.work",null)}
let task=spawn(work());tool("fixture.started",null);cancel(task);await task'''
        evaluator = Evaluator(tool_handler=lambda *args: None, effect_handler=effect)
        with self.assertRaises(TaskCancelled) as caught:
            evaluator.run(source)
        self.assertIs(caught.exception, error)
        self.assertTrue(finished.is_set())
        self.assert_no_effect_threads()

    def test_catch_observes_terminal_peer_and_finished_effect(self):
        entered = threading.Barrier(2)
        finished = threading.Event()

        def effect(kind, payload, cancel):
            if payload['name'] == 'fixture.finished':
                return finished.is_set()
            entered.wait(timeout=3)
            if payload['value'] == 'fail':
                raise LanguageError('ordinary', code='ordinary_fixture')
            try:
                self.assertTrue(cancel.wait(3))
                return 42
            finally:
                finished.set()

        source = '''async fn work(v){tool("fixture.work",v)}
async fn finished(){tool("fixture.finished",null)}
let bad=work("fail");let peer=work("peer");
try{await gather([bad,peer])}catch e{[e.code,task_info(peer).state,await finished()]}'''
        result = Evaluator(tool_handler=lambda *args: None, effect_handler=effect).run(source)
        self.assertEqual(result, ['ordinary_fixture', 'cancelled', True])
        self.assert_no_effect_threads()

    def test_failure_cancels_earlier_input_without_waiting_for_its_result(self):
        entered = threading.Barrier(2)
        cancelled = threading.Event()

        def effect(kind, payload, cancel):
            entered.wait(timeout=3)
            if payload['value'] == 'fail':
                raise LanguageError('later failed', code='later_fixture')
            self.assertTrue(cancel.wait(3), 'join waited in input order instead of cancelling peer')
            cancelled.set()
            return 42

        source = '''async fn work(v){tool("fixture.work",v)}
try{await gather([work("peer"),work("fail")])}catch e{e.code}'''
        self.assertEqual(Evaluator(tool_handler=lambda *args: None, effect_handler=effect).run(source),
                         'later_fixture')
        self.assertTrue(cancelled.is_set())
        self.assert_no_effect_threads()

    def test_explicit_member_cancel_is_not_discarded_as_peer_cancel(self):
        source = '''async fn cold(){42}async fn bad(){throw "ordinary"}
let task=cold();cancel(task);try{await gather([bad(),task])}catch e{0}'''
        with self.assertRaises(TaskCancelled):
            Evaluator().run(source)

    def test_duplicate_member_runs_once_and_preserves_both_result_positions(self):
        source = '''var calls=0;async fn once(){calls=calls+1;41}
let task=once();let results=await gather([task,task]);[calls,results]'''
        self.assertEqual(Evaluator().run(source), [1, [41, 41]])

    def test_current_caller_in_nested_gather_reports_deadlock(self):
        source = '''var current=null;async fn recursive(){await gather([current])}
current=recursive();try{await current}catch e{e.code}'''
        self.assertEqual(Evaluator(step_limit=1000).run(source), 'task_deadlock')

    def test_outer_cancellation_drains_join_and_skips_language_cleanup(self):
        entered = threading.Barrier(2)
        outer_cancel = threading.Event()
        done = [threading.Event(), threading.Event()]
        cleaned = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'fixture.cleanup':
                cleaned.append(True)
                return None
            index = payload['value']
            entered.wait(timeout=3)
            outer_cancel.set()
            try:
                self.assertTrue(cancel.wait(3))
                return index
            finally:
                done[index].set()

        source = '''async fn work(n){try{tool("fixture.work",n)}finally{tool("fixture.cleanup",null)}}
try{await gather([work(0),work(1)])}catch e{"cannot catch"}'''
        with self.assertRaises(ResourceLimit):
            Evaluator(tool_handler=lambda *args: None, effect_handler=effect,
                      cancelled=outer_cancel.is_set).run(source)
        self.assertTrue(all(event.is_set() for event in done))
        self.assertEqual(cleaned, [])
        self.assert_no_effect_threads()

    def test_budget_during_join_cannot_be_caught_or_run_finally(self):
        cleanups = []
        source = '''async fn loop(){try{while true{1}}finally{tool("cleanup",null)}}
try{await gather([loop(),loop()])}catch e{0}'''
        with self.assertRaises(ResourceLimit):
            Evaluator(step_limit=200, tool_handler=lambda *args: cleanups.append(args)).run(source)
        self.assertEqual(cleanups, [])
        self.assert_no_effect_threads()

    def test_hard_error_first_found_during_epoch_drain_skips_stream_cleanup(self):
        for error_type in HARD_ERRORS:
            with self.subTest(error=error_type.__name__):
                entered = threading.Event()
                cleanups = []
                error = error_type('late epoch failure')

                def effect(kind, payload, cancel):
                    if payload['name'] == 'ready':
                        self.assertTrue(entered.wait(3))
                        return 0
                    entered.set()
                    self.assertTrue(cancel.wait(3))
                    raise error

                source = '''fn* stream(){try{yield 1}finally{tool("cleanup",null)}}
let opened=stream();next(opened);
async fn work(){tool("work",null)}async fn ready(){tool("ready",null)}
spawn(work());await ready();0'''
                with self.assertRaises(error_type) as caught:
                    Evaluator(tool_handler=lambda *args: cleanups.append(args),
                              effect_handler=effect).run(source)
                self.assertIs(caught.exception, error)
                self.assertEqual(cleanups, [])
                self.assert_no_effect_threads()


if __name__ == '__main__':
    unittest.main()
