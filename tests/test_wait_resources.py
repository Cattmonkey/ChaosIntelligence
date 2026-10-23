"""Independent wait lifecycle regressions using causal events, not wall-clock races."""
import threading
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled, TERMINAL


class WaitResourceTests(unittest.TestCase):
    def evaluator(self, effect=None, **options):
        if effect is None:
            return Evaluator(timeout=8, **options)
        return Evaluator(timeout=8, effect_handler=effect,
                         tool_handler=lambda name, value: effect(
                             'tool', {'name': name, 'value': value}, threading.Event()), **options)

    def assert_quiescent(self, evaluator):
        self.assertFalse(evaluator.suspension.active)
        self.assertTrue(all(t.state in TERMINAL for t in evaluator.suspension.tasks))
        engine = evaluator.suspension.task_waits
        self.assertFalse(engine.active)
        self.assertFalse(engine.timers)
        self.assertFalse(engine.references)
        self.assertFalse([t.name for t in threading.enumerate()
                          if t.name.startswith('chaos-effect') and t.is_alive()])

    def test_timeout_does_not_cancel_or_observe_a_running_host_member(self):
        entered, release, ended = threading.Event(), threading.Event(), threading.Event()
        cancelled = []
        def effect(kind, payload, cancel):
            if payload['name'] == 'work':
                entered.set()
                try:
                    self.assertTrue(release.wait(3))
                    cancelled.append(cancel.is_set())
                    return 42
                finally:
                    ended.set()
            if payload['name'] == 'ready':
                self.assertTrue(entered.wait(3))
            else:
                release.set()
        evaluator = self.evaluator(effect)
        try:
            result = evaluator.run('''async fn work(){tool("work",null)}
let member=spawn(work());tool("ready",null);
let result=await task_wait([member],timeout=0);
let before=[length(result.done),length(result.pending),result.timedOut,
 task_info(member).observed,task_info(member).cancellationRequested];
tool("release",null);[before,await member]''')
        finally:
            release.set()
        self.assertEqual(result, [[0, 1, True, False, False], 42])
        self.assertEqual(cancelled, [False])
        self.assertTrue(ended.is_set())
        self.assert_quiescent(evaluator)

    def test_cancel_native_wait_unlinks_only_wait_not_channel_member(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel();let member=channel_receive(c);
let watcher=spawn(task_wait([member]));
while task_info(watcher).phase!="waiting_tasks"{1};
let changed=cancel(watcher);
let before=[task_info(watcher).state,task_info(member).observed,
 task_info(member).cancellationRequested,channel_info(c).waitingReceivers];
await channel_send(c,41);[changed,before,(await member).value]''')
        self.assertEqual(result, [True, ['cancelled', False, False, 1], 41])
        self.assert_quiescent(evaluator)

    def test_group_abort_cancels_owned_wait_but_not_its_borrowed_member(self):
        for expression in ('await task_wait([member])', 'let w=task_wait([member]);await w'):
            with self.subTest(expression=expression):
                evaluator = self.evaluator(step_limit=100000)
                result = evaluator.run('''let c=channel();let member=channel_receive(c);
async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "first"}
let code=try{async with task_group() as g{g.spawn(bad());EXPRESSION}}catch e{e.value};
let before=[task_info(member).observed,task_info(member).cancellationRequested,
 channel_info(c).waitingReceivers];await channel_send(c,42);
[code,before,(await member).value]'''.replace('EXPRESSION', expression))
                self.assertEqual(result, ['first', [False, False, 1], 42])
                waits = [t for t in evaluator.suspension.tasks if evaluator.suspension._info(t)['kind'] == 'wait']
                self.assertEqual(len(waits), 1)
                self.assertEqual(waits[0].state, 'cancelled')
                self.assert_quiescent(evaluator)

    def test_group_abort_does_not_cancel_prestarted_external_wait(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel();let member=channel_receive(c);
let watcher=spawn(task_wait([member]));
async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "first"}
let code=try{async with task_group() as g{g.spawn(bad());await watcher}}catch e{e.value};
let before=[task_info(watcher).state,task_info(watcher).cancellationRequested,
 task_info(member).cancellationRequested];await channel_send(c,42);
let result=await watcher;[code,before,length(result.done),result.timedOut,(await member).value]''')
        self.assertEqual(result, ['first', ['running', False, False], 1, False, 42])
        self.assert_quiescent(evaluator)

    def test_completed_wait_late_cancel_does_not_roll_back_members(self):
        evaluator = self.evaluator()
        self.assertEqual(evaluator.run('''let member=sleep(0);let w=task_wait([member]);
let r=await w;[cancel(w),task_info(w).state,task_info(member).state,
 task_info(member).observed,length(r.done),await member]'''),
                         [False, 'completed', 'completed', False, 1, None])
        self.assert_quiescent(evaluator)

    def test_error_wait_does_not_observe_failure_and_pending_remains_usable(self):
        evaluator = self.evaluator()
        result = evaluator.run('''let c=channel();let pending=channel_receive(c);
async fn bad(){throw "member failure"};let failed=bad();
let r=await task_wait([pending,failed],until="error");
let before=[length(r.done),length(r.pending),r.timedOut,task_info(failed).observed,
 task_info(pending).cancellationRequested];
let error=try{await failed}catch e{e.value};await channel_send(c,41);
[before,error,(await pending).value]''')
        self.assertEqual(result, [[1, 1, False, False, False], 'member failure', 41])
        self.assert_quiescent(evaluator)

    def test_unobserved_member_error_still_escapes_epoch_finish(self):
        evaluator = self.evaluator()
        with self.assertRaises(LanguageError) as result:
            evaluator.run('''async fn bad(){throw "unobserved"};
let task=bad();await task_wait([task],until="error");42''')
        self.assertEqual(result.exception.value, 'unobserved')
        self.assert_quiescent(evaluator)

    def test_hard_member_failure_prevents_post_wait_effects_and_cleanup(self):
        for kind in (ResourceLimit, ExecutionControl, KeyboardInterrupt, SystemExit):
            with self.subTest(kind=kind.__name__):
                hard, effects = kind('hard member'), []
                def effect(effect_kind, payload, cancel):
                    raise hard
                evaluator = Evaluator(timeout=8, effect_handler=effect,
                                      tool_handler=lambda name, value: effects.append(name))
                with self.assertRaises(kind) as caught:
                    evaluator.run('''async fn bad(){tool("bad",null)}let member=bad();
try{await task_wait([member]);tool("after",null)}finally{tool("cleanup",null)}''')
                self.assertIs(caught.exception, hard)
                self.assertEqual(effects, [])
                member = next(t for t in evaluator.suspension.tasks if t.kind == 'task')
                self.assertFalse(member.observed)
                self.assert_quiescent(evaluator)

    def test_first_wait_or_cycle_can_progress_through_timer(self):
        evaluator = self.evaluator()
        self.assertEqual(evaluator.run('''var b=null;
async fn first(){let r=await task_wait([b,sleep(0)],until="first");[length(r.done),42]}
let a=first();async fn second(){await a};b=second();
let answer=await a;[answer,await b]'''), [[1, 42], [1, 42]])
        self.assert_quiescent(evaluator)

    def test_all_wait_cycle_can_progress_through_timeout(self):
        evaluator = self.evaluator()
        self.assertEqual(evaluator.run('''var b=null;
async fn first(){let r=await task_wait([b],until="all",timeout=0);[r.timedOut,42]}
let a=first();async fn second(){await a};b=second();
let answer=await a;[answer,await b]'''), [[True, 42], [True, 42]])
        self.assert_quiescent(evaluator)

    def test_unbreakable_wait_cycle_reports_deadlock_and_drains(self):
        evaluator = self.evaluator()
        result = evaluator.run('''var b=null;
async fn first(){await task_wait([b],until="first")};let a=first();
async fn second(){await a};b=second();
let code=try{await a;null}catch e{e.code};
let other=try{await b;null}catch e{e.code};[code,other]''')
        self.assertEqual(result, ['task_deadlock', 'task_deadlock'])
        self.assert_quiescent(evaluator)

    def test_timer_and_wait_cancel_do_not_allocate_host_workers(self):
        evaluator = self.evaluator(step_limit=100000)
        with patch('chaos.suspension.ThreadPoolExecutor', side_effect=AssertionError('timer allocated worker')):
            result = evaluator.run('''let timer=spawn(sleep(600));let watcher=spawn(task_wait([timer],timeout=600));
while task_info(timer).phase!="sleeping" || task_info(watcher).phase!="waiting_tasks"{1};
let before=[task_info(timer).timing.seconds,task_info(watcher).timing.timeoutSeconds];
[cancel(watcher),task_info(timer).cancellationRequested,cancel(timer),before]''')
        self.assertEqual(result, [True, False, True, [600, 600]])
        self.assert_quiescent(evaluator)

    def test_group_cancel_late_hard_host_failure_preserves_original_identity(self):
        for kind in (ResourceLimit, ExecutionControl, KeyboardInterrupt, SystemExit):
            with self.subTest(kind=kind.__name__):
                entered, queued, ended = threading.Event(), threading.Event(), threading.Event()
                hard, cleanup = kind('late wait fixture'), []
                def effect(effect_kind, payload, cancel):
                    name = payload['name']
                    if name == 'host':
                        entered.set()
                        try:
                            self.assertTrue(cancel.wait(3))
                            raise hard
                        finally:
                            ended.set()
                    if name == 'ready':
                        self.assertTrue(entered.wait(3));queued.set();return None
                    if name == 'cleanup':
                        cleanup.append(True);return None
                    self.assertTrue(queued.wait(3))
                    raise LanguageError('first ordinary', code='wait_fixture')
                evaluator = self.evaluator(effect, step_limit=1000000)
                with self.assertRaises(kind) as result:
                    evaluator.run('''async fn host(){tool("host",null)}async fn bad(){tool("bad",null)}
try{async with task_group() as g{
 let timer=g.spawn(sleep(600));g.spawn(host());g.spawn(bad());
 tool("ready",null);await task_wait([timer])
}}finally{tool("cleanup",null)}''')
                self.assertIs(result.exception, hard)
                self.assertEqual(cleanup, [])
                self.assertTrue(ended.is_set())
                self.assert_quiescent(evaluator)

    def test_global_cancel_during_timer_idle_has_no_user_cleanup(self):
        registered, cancelled = threading.Event(), threading.Event()
        cleanups = []
        def trace(payload):
            if payload['event'] == 'task.started' and payload.get('kind') == 'sleep':
                registered.set()
        def control():
            if registered.wait(3): cancelled.set()
        thread = threading.Thread(target=control)
        thread.start()
        evaluator = self.evaluator(cancelled=cancelled.is_set, trace_handler=trace,
                                   tool_handler=lambda name, value: cleanups.append(True))
        try:
            with self.assertRaises(ResourceLimit):
                evaluator.run('try{await sleep(600)}finally{tool("cleanup",null)}')
        finally:
            cancelled.set();thread.join(3)
        self.assertTrue(registered.is_set())
        self.assertFalse(thread.is_alive())
        self.assertEqual(cleanups, [])
        self.assert_quiescent(evaluator)

    def fake_clock(self, evaluator):
        now, waits = [100.0], []
        def idle(seconds):
            self.assertGreaterEqual(seconds, 0)
            waits.append(seconds)
            now[0] += seconds
        evaluator.suspension.task_waits.clock = lambda: now[0]
        evaluator.suspension.task_waits.idle_wait = idle
        return now, waits

    def test_timer_clock_starts_when_task_starts_not_at_cold_creation(self):
        now = None
        def advance(name, value):
            now[0] += value
        evaluator = self.evaluator(tool_handler=advance)
        now, waits = self.fake_clock(evaluator)
        with patch('chaos.suspension.ThreadPoolExecutor', side_effect=AssertionError('timer allocated worker')):
            result = evaluator.run('''let timer=sleep(3);let cold=task_info(timer).timing;
tool("advance",20);await timer;[cold.remainingSeconds,task_info(timer).timing.remainingSeconds]''')
        self.assertEqual(result, [None, None])
        self.assertAlmostEqual(now[0], 123)
        self.assertAlmostEqual(sum(waits), 3)
        self.assert_quiescent(evaluator)

    def test_timeout_clock_is_independent_of_borrowed_timer_deadline(self):
        evaluator = self.evaluator()
        now, waits = self.fake_clock(evaluator)
        result = evaluator.run('''let timer=sleep(5);let result=await task_wait([timer],timeout=2);
let before=[result.timedOut,length(result.pending),task_info(timer).cancellationRequested,
 task_info(timer).timing.remainingSeconds];await timer;before''')
        self.assertEqual(result[:3], [True, 1, False])
        self.assertAlmostEqual(result[3], 3)
        self.assertAlmostEqual(now[0], 105)
        self.assertAlmostEqual(sum(waits), 5)
        self.assert_quiescent(evaluator)

    def test_cold_timers_and_waits_leave_no_registration_across_epochs(self):
        evaluator = self.evaluator()
        now, waits = self.fake_clock(evaluator)
        self.assertEqual(evaluator.run('let timer=sleep(600);task_wait([timer],timeout=600);42'), 42)
        self.assertEqual(waits, [])
        self.assert_quiescent(evaluator)
        self.assertEqual(evaluator.run('await sleep(2);43'), 43)
        self.assertAlmostEqual(now[0], 102)
        self.assert_quiescent(evaluator)

    def test_budget_hard_stop_cancels_native_waits(self):
        evaluator = self.evaluator(step_limit=1200)
        with self.assertRaises(ResourceLimit):
            evaluator.run('''let timer=spawn(sleep(600));let w=spawn(task_wait([timer]));
while true{1}''')
        self.assert_quiescent(evaluator)


if __name__ == '__main__':
    unittest.main()
