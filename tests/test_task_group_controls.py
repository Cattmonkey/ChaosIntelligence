"""Independent causal probes for lexical task-group interruption and draining."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled, TERMINAL


HARD_ERRORS = (ResourceLimit, ExecutionControl, KeyboardInterrupt, SystemExit)


class TaskGroupControlTests(unittest.TestCase):
    def evaluate(self, source, effect, **options):
        evaluator = Evaluator(
            tool_handler=lambda name, value: effect('tool', {'name': name, 'value': value}, threading.Event()),
            effect_handler=effect, timeout=10, **options)
        return evaluator.run(source)

    def assert_drained(self):
        self.assertFalse([thread.name for thread in threading.enumerate()
                          if thread.name.startswith('chaos-effect') and thread.is_alive()])

    def test_child_failure_preempts_compute_body_and_cannot_be_swallowed(self):
        body_entered = threading.Event()
        cleanup = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'body':
                body_entered.set()
                return None
            if payload['name'] == 'cleanup':
                cleanup.append(True)
                return None
            self.assertTrue(body_entered.wait(3))
            raise LanguageError('child failed', code='child_fixture')

        source = '''async fn child(){tool("child",null)}
var continued=false;let group=task_group();
let error=try{async with group as g{
    g.spawn(child());
    try{tool("body",null);while true{1}}catch e{continued=true}
    finally{tool("cleanup",null)};
    continued=true
}}catch e{e.code};
[error,continued,task_group_info(group).state,41+1]'''
        self.assertEqual(self.evaluate(source, effect, step_limit=1000000),
                         ['child_fixture', False, 'closed', 42])
        self.assertEqual(cleanup, [True])
        self.assert_drained()

    def test_preempted_foreign_await_does_not_cancel_or_drain_foreign_task(self):
        foreign_entered = threading.Event()
        release_foreign = threading.Event()
        foreign_done = threading.Event()
        foreign_cancel = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'foreign':
                foreign_entered.set()
                self.assertTrue(release_foreign.wait(3), 'group exit waited for an unowned task')
                foreign_cancel.append(cancel.is_set())
                foreign_done.set()
                return 41
            if payload['name'] == 'release':
                self.assertFalse(foreign_done.is_set())
                release_foreign.set()
                return None
            self.assertTrue(foreign_entered.wait(3))
            raise LanguageError('member failed', code='child_fixture')

        source = '''async fn outside(){tool("foreign",null)}async fn bad(){tool("bad",null)}
let external=spawn(outside());let group=task_group();
let error=try{async with group as g{g.spawn(bad());await external}}catch e{e.code};
let cancelled=task_info(external).cancellationRequested;
tool("release",null);[error,cancelled,await external,task_group_info(group).state]'''
        self.assertEqual(self.evaluate(source, effect), ['child_fixture', False, 41, 'closed'])
        self.assertEqual(foreign_cancel, [False])
        self.assert_drained()

    def body_effect_case(self, error_type=None):
        entered = threading.Event()
        ended = threading.Event()
        cleanups = []
        hard = error_type('late body effect') if error_type else None

        def effect(kind, payload, cancel):
            if payload['name'] == 'body':
                self.assertTrue(threading.current_thread().name.startswith('chaos-effect'))
                entered.set()
                try:
                    self.assertTrue(cancel.wait(3), 'body effect was not interrupted')
                    if hard is not None: raise hard
                    return 1
                finally:
                    ended.set()
            if payload['name'] == 'done': return ended.is_set()
            if payload['name'] == 'cleanup':
                cleanups.append(True)
                return None
            self.assertTrue(entered.wait(3))
            raise LanguageError('ordinary child failure', code='child_fixture')

        source = '''async fn bad(){tool("bad",null)}
let group=task_group();
let error=try{async with group as g{
    g.spawn(bad());try{tool("body",null)}finally{tool("cleanup",null)}
}}catch e{e.code};[error,tool("done",null),task_group_info(group).state]'''
        if hard is None:
            self.assertEqual(self.evaluate(source, effect), ['child_fixture', True, 'closed'])
            self.assertEqual(cleanups, [True])
        else:
            with self.assertRaises(error_type) as caught:
                self.evaluate(source, effect)
            self.assertIs(caught.exception, hard)
            self.assertEqual(cleanups, [])
        self.assertTrue(ended.is_set())
        self.assert_drained()

    def test_top_level_body_effect_really_ends_before_outside_catch(self):
        self.body_effect_case()

    def test_late_body_effect_hard_error_preserves_identity(self):
        for error_type in HARD_ERRORS:
            with self.subTest(error=error_type.__name__):
                self.body_effect_case(error_type)

    def test_late_member_hard_error_wins_body_ordinary_failure(self):
        for error_type in HARD_ERRORS:
            with self.subTest(error=error_type.__name__):
                entered = threading.Event()
                ended = threading.Event()
                hard = error_type('late member failure')

                def effect(kind, payload, cancel):
                    if payload['name'] == 'ready':
                        self.assertTrue(entered.wait(3))
                        return None
                    entered.set()
                    try:
                        self.assertTrue(cancel.wait(3))
                        raise hard
                    finally:
                        ended.set()

                source = '''async fn work(){tool("member",null)}
try{async with task_group() as g{g.spawn(work());tool("ready",null);throw "body"}}
catch error{"must not swallow hard"}'''
                with self.assertRaises(error_type) as caught:
                    self.evaluate(source, effect)
                self.assertIs(caught.exception, hard)
                self.assertTrue(ended.is_set())
                self.assert_drained()

    def test_global_cancellation_during_group_drain_remains_hard(self):
        entered = threading.Event()
        outer_cancel = threading.Event()
        ended = threading.Event()
        cleanup = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'ready':
                self.assertTrue(entered.wait(3))
                return None
            if payload['name'] == 'cleanup':
                cleanup.append(True)
                return None
            entered.set()
            try:
                self.assertTrue(cancel.wait(3))
                outer_cancel.set()
                return None
            finally:
                ended.set()

        source = '''async fn member(){try{tool("member",null)}finally{tool("cleanup",null)}}
try{async with task_group() as g{g.spawn(member());tool("ready",null);throw "body"}}
catch error{"must not catch global cancellation"}'''
        with self.assertRaises(ResourceLimit):
            self.evaluate(source, effect, cancelled=outer_cancel.is_set)
        self.assertTrue(ended.is_set())
        self.assertEqual(cleanup, [])
        self.assert_drained()

    def test_budget_exhaustion_cannot_run_user_cleanup_or_escape_drain(self):
        cleanup = []
        source = '''async fn loop(){try{while true{1}}finally{tool("cleanup",null)}}
try{async with task_group() as g{g.spawn(loop());while true{1}}}catch error{0}'''
        with self.assertRaises(ResourceLimit):
            self.evaluate(source, lambda *args: cleanup.append(True), step_limit=300)
        self.assertEqual(cleanup, [])
        self.assert_drained()

    def test_member_public_cancel_stays_hard(self):
        source = '''async fn member(){while true{1}}
try{async with task_group() as g{let child=g.spawn(member());cancel(child)}}catch error{0}'''
        with self.assertRaises(TaskCancelled):
            Evaluator(step_limit=1000).run(source)
        self.assert_drained()

    def test_public_owner_cancel_during_drain_is_not_consumed_as_group_abort(self):
        entered = threading.Event()
        draining = threading.Event()
        release = threading.Event()
        ended = threading.Event()

        def effect(kind, payload, cancel):
            name = payload['name']
            if name == 'ready':
                self.assertTrue(entered.wait(3))
                return None
            if name == 'draining':
                self.assertTrue(draining.wait(3))
                return None
            if name == 'release':
                release.set()
                return None
            entered.set()
            try:
                self.assertTrue(cancel.wait(3))
                draining.set()
                self.assertTrue(release.wait(3))
                raise TaskCancelled('cooperative member stop')
            finally:
                ended.set()

        source = '''var owner=null;async fn member(){tool("member",null)}
async fn run(){async with task_group() as g{g.spawn(member());tool("ready",null);throw "body"}}
async fn interrupt(){tool("draining",null);cancel(owner);tool("release",null)}
owner=run();spawn(interrupt());try{await owner}catch error{"must not catch cancellation"}'''
        with self.assertRaises(TaskCancelled):
            self.evaluate(source, effect)
        self.assertTrue(ended.is_set())
        self.assert_drained()

    def test_normal_return_joins_member_without_cancelling_it(self):
        release = threading.Event()
        ended = threading.Event()
        cancelled = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'leave':
                release.set()
                return None
            if payload['name'] == 'done': return ended.is_set()
            self.assertTrue(release.wait(3))
            cancelled.append(cancel.is_set())
            ended.set()
            return 42

        source = '''async fn work(){tool("work",null)}
async fn run(){async with task_group() as g{g.spawn(work());
    try{return 41}finally{tool("leave",null)}};0}
let result=await run();[result,tool("done",null)]'''
        self.assertEqual(self.evaluate(source, effect), [41, True])
        self.assertEqual(cancelled, [False])
        self.assert_drained()

    def test_generator_consumption_and_reentrant_native_driver_keep_scope(self):
        entered = threading.Event()
        ended = threading.Event()
        cleanup = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'cleanup':
                cleanup.append(True)
                return None
            if payload['name'] == 'body':
                entered.set()
                try:
                    self.assertTrue(cancel.wait(3))
                    return 0
                finally:
                    ended.set()
            self.assertTrue(entered.wait(3))
            raise LanguageError('child', code='child_fixture')

        source = '''fn* items(){try{tool("body",null);yield 1}finally{tool("cleanup",null)}}
async fn bad(){tool("bad",null)}
let group=task_group();let stream=items();
let code=try{async with group as g{g.spawn(bad());map(fn(x){next(stream)},[0])}}
catch error{error.code};[code,task_group_info(group).state]'''
        self.assertEqual(self.evaluate(source, effect), ['child_fixture', 'closed'])
        self.assertTrue(ended.is_set())
        self.assertEqual(cleanup, [True])
        self.assert_drained()

    def test_async_generator_can_be_consumed_inside_group(self):
        source = '''async fn* values(){yield 20;yield 21}
async fn consume(){var total=0;async for value in values(){total=total+value};total}
let group=task_group();var child=null;
async with group as g{child=g.spawn(consume())};
[await child,task_group_info(group).state]'''
        self.assertEqual(Evaluator().run(source), [41, 'closed'])

    def test_inner_context_exit_cannot_suppress_group_child_failure(self):
        entered = threading.Event()
        exits = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'exit':
                exits.append(payload['value'])
                return None
            if payload['name'] == 'body':
                entered.set()
                self.assertTrue(cancel.wait(3))
                return None
            self.assertTrue(entered.wait(3))
            raise LanguageError('member', code='child_fixture')

        source = '''class Guard{fn __enter__(self){0}
fn __exit__(self,error){tool("exit",error);true}}
async fn bad(){tool("bad",null)}
let result=try{async with task_group() as g{
g.spawn(bad());with Guard() as held{tool("body",null)};"continued"
}}catch error{error.code};result'''
        self.assertEqual(self.evaluate(source, effect), 'child_fixture')
        self.assertEqual(len(exits), 1)
        self.assert_drained()

    def test_gather_closure_rejection_never_adopts_or_cancels_foreign_task(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        external_cancelled = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'ready':
                self.assertTrue(started.wait(3))
                return None
            if payload['name'] == 'release':
                release.set()
                return None
            calls.append(payload['value'])
            started.set()
            self.assertTrue(release.wait(3))
            external_cancelled.append(cancel.is_set())
            return 41

        source = '''async fn work(value){tool("work",value)}
let external=spawn(work("outside"));tool("ready",null);let local=work("inside");
let group=task_group();let code=try{async with group as g{g.spawn(gather([local,external]))}}
catch error{error.code};let cold=task_info(local);tool("release",null);
[code,cold.state,cold.observed,cold.cancellationRequested,await external,task_group_info(group).state]'''
        result = self.evaluate(source, effect)
        self.assertIsInstance(result[0], str)
        self.assertEqual(result[1:], ['created', False, False, 41, 'closed'])
        self.assertEqual(calls, ['outside'])
        self.assertEqual(external_cancelled, [False])
        self.assert_drained()

    def test_outward_yield_cannot_detach_active_group(self):
        source = '''var group=null;var child=null;async fn work(){while true{1}}
async fn* escaped(){group=task_group();async with group as g{child=g.spawn(work());yield 1}}
let stream=escaped();let code=try{await anext(stream);"escaped"}catch error{error.code};
[code,task_group_info(group).state,task_info(child).state]'''
        result = Evaluator(step_limit=1000).run(source)
        self.assertNotEqual(result[0], 'escaped')
        self.assertEqual(result[1], 'closed')
        self.assertIn(result[2], TERMINAL)

    def test_scope_body_failure_events_report_failure_and_return_reports_completion(self):
        for source, expected in (
            ('try{async with task_group() as g{throw "body"}}catch error{error.code}', 'task.failed'),
            ('async fn f(){async with task_group() as g{return 42}};await f()', 'task.completed'),
        ):
            with self.subTest(expected=expected):
                events = []
                Evaluator(trace_handler=events.append).run(source)
                bodies = [event['taskId'] for event in events
                          if event['event'] == 'task.created' and event.get('kind') == 'scope']
                self.assertEqual(len(bodies), 1)
                terminals = [event['event'] for event in events
                             if event.get('taskId') == bodies[0]
                             and event['event'] in ('task.failed', 'task.completed', 'task.cancelled')]
                self.assertEqual(terminals, [expected])

    def test_existing_member_can_add_work_while_owner_is_joining(self):
        source = '''async fn leaf(){42}var added=null;
async fn adding(group){while task_group_info(group).state!="joining"{1};
    added=group.spawn(leaf());await added}
let group=task_group();async with group as g{g.spawn(adding(g));null};
[await added,task_group_info(group).state,task_group_info(group).memberCount]'''
        self.assertEqual(Evaluator(step_limit=10000).run(source), [42, 'closed', 2])

    def test_cold_gather_closure_is_owned_once_and_observers_share_results(self):
        source = '''var calls=0;async fn leaf(value){calls=calls+1;value}
let one=leaf(20);let two=leaf(21);let collector=gather([two,one,two]);let group=task_group();
async with group as g{g.spawn(collector);g.spawn(one)};
[await collector,await gather([one,two]),calls,task_group_info(group).memberCount]'''
        self.assertEqual(Evaluator().run(source), [[21,20,21], [20,21], 2, 3])


if __name__ == '__main__':
    unittest.main()
