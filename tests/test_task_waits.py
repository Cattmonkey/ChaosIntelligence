import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl
from chaos.suspension import Await, TERMINAL
from chaos.task_waits import sleep, wait


class Clock:
    def __init__(self): self.now, self.idles = 100.0, []
    def read(self): return self.now
    def advance(self, seconds): self.idles.append(seconds); self.now += seconds


class TaskWaitTests(unittest.TestCase):
    def evaluator(self):
        e = Evaluator()
        clock = Clock()
        e.suspension.task_waits.clock = clock.read
        e.suspension.task_waits.idle_wait = clock.advance
        return e, clock

    def clean(self, evaluator):
        engine = evaluator.suspension.task_waits
        self.assertEqual((engine.active, engine.timers, engine.references), ({}, [], 0))
        self.assertTrue(all(not task.completion_waits for task in evaluator.suspension.tasks))

    def test_cold_sleep_binding_and_no_effect_worker(self):
        e, clock = self.evaluator()
        result = e.run('var n=2;let t=sleep(n);n=9;let before=task_info(t);await t;[before,task_info(t)]')
        self.assertEqual(result[0]['state'], 'created')
        self.assertEqual(result[0]['timing'], dict(seconds=2.0,until=None,timeoutSeconds=None,remainingSeconds=None))
        self.assertEqual(result[1]['state'], 'completed')
        self.assertEqual(result[1]['timing']['remainingSeconds'], None)
        self.assertAlmostEqual(clock.now, 102.0)
        self.assertIsNone(e.suspension.pool)
        self.clean(e)

    def test_stable_unique_partition_and_no_member_observation(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('''
          let a=sleep(2);let b=sleep(1);let c=sleep(3);
          let r=await task_wait([a,b,a,c,b]);
          [r.done==[b],r.pending==[a,c],r.timedOut,
           task_info(a).observed,task_info(b).observed,task_info(c).observed]
        '''), [True,True,False,False,False,False])
        self.clean(e)

    def test_all_mode_returns_every_member_in_input_order(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('let a=sleep(2);let b=sleep(1);let r=await task_wait([a,b],"all");[r.done==[a,b],r.pending,r.timedOut]'), [True,[],False])
        self.clean(e)

    def test_error_mode_waits_past_success_and_leaves_error_unobserved(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('''
          async fn bad(){await sleep(2);throw {code:"fixture"}};
          let a=sleep(1);let b=bad();let c=sleep(4);
          let r=await task_wait([a,b,c],"error");
          let observed=task_info(b).observed;
          let caught=try{await b}catch x{x.code};
          [r.done==[a,b],r.pending==[c],r.timedOut,observed,caught]
        '''), [True,True,False,False,'user_throw'])
        self.clean(e)

    def test_error_mode_all_success_and_empty_lists(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('''
          let a=sleep(1);let b=sleep(2);let r=await task_wait([a,b],"error");
          [r.done==[a,b],r.timedOut,await task_wait([]),await task_wait([],"all",0),await task_wait([],"error")]
        '''), [True,False]+[dict(done=[],pending=[],timedOut=False)]*3)

    def test_zero_timeout_predicate_wins_and_borrowed_pending_survives(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('''
          let done=sleep(0);await done;let pending=sleep(5);
          let first=await task_wait([done,pending],"first",0);
          let all=await task_wait([done,pending],"all",0);
          let before=task_info(pending);await pending;
          [first.done==[done],first.timedOut,all.timedOut,all.pending==[pending],before.cancellationRequested]
        '''), [True,False,True,True,False])
        self.clean(e)

    def test_timeout_starts_at_execution_not_factory(self):
        e, clock = self.evaluator()
        self.assertEqual(e.run('''
          let target=sleep(10);let w=task_wait([target],"all",2);
          await sleep(3);let r=await w;[r.timedOut,task_info(target).cancellationRequested]
        '''), [True,False])
        self.assertAlmostEqual(clock.now, 105.0)
        self.clean(e)

    def test_sleep_zero_yields_and_can_be_cancelled_cold(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('let t=sleep(0);[cancel(t),task_info(t).state,cancel(t)]'), [True,'cancelled',False])
        self.clean(e)

    def test_cancel_wait_detaches_without_cancel_or_observation(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('''
          let target=sleep(2);let w=spawn(task_wait([target]));await sleep(0);
          let cancelled=cancel(w);let before=task_info(target);await target;
          [cancelled,task_info(w).state,before.observed,before.cancellationRequested]
        '''), [True,'cancelled',False,False])
        self.clean(e)

    def test_or_cycle_has_timer_escape_and_all_cycle_has_timeout(self):
        for mode in ('first','all','error'):
            with self.subTest(mode=mode):
                e, _ = self.evaluator()
                source='''
                  var a=null;var b=null;
                  async fn fa(){let r=await task_wait([b,sleep(1)],MODE,TIMEOUT);[42,r.timedOut]};
                  async fn fb(){await a};a=fa();b=fb();await a
                '''.replace('MODE','"'+mode+'"').replace('TIMEOUT','null' if mode=='first' else '2')
                self.assertEqual(e.run(source), [42,mode!='first'])
                self.clean(e)

    def test_deadlock_fails_registration_without_cancelling_caller(self):
        e, _ = self.evaluator()
        self.assertEqual(e.run('''
          var a=null;async fn work(){let w=task_wait([a]);
          let code=try{await w}catch e{e.code};[code,task_info(w).state,task_info(a).cancellationRequested]};
          a=work();await a
        '''), ['task_deadlock','failed',False])
        self.clean(e)

    def test_unconsumed_member_failure_still_surfaces_at_epoch_finish(self):
        e, _ = self.evaluator()
        with self.assertRaises(LanguageError) as caught:
            e.run('async fn bad(){throw {code:"unobserved"}};await task_wait([bad()]);42')
        self.assertEqual(caught.exception.code, 'user_throw')
        self.clean(e)

    def test_argument_validation_has_no_start_side_effect(self):
        e, _ = self.evaluator(); s=e.suspension; s.begin()
        try:
            good=sleep(e,1)
            for invalid in (True,-1,float('inf'),float('nan'),'1',10**1000):
                with self.assertRaises(LanguageError): sleep(e,invalid)
                with self.assertRaises(LanguageError): wait(e,[good],timeout=invalid)
            for members,mode in (([good,1],'first'),([good],'any'),((good,),'all')):
                with self.assertRaises(LanguageError):wait(e,members,mode)
            self.assertEqual(good.state,'created')
            self.assertFalse(good.observed)
        finally:s.finish()
        self.clean(e)

    def test_recording_admission_and_queued_scope_change_are_atomic(self):
        e, _ = self.evaluator();s=e.suspension;s.begin()
        try:
            a=sleep(e,3);b=sleep(e,4);w=wait(e,[a,b])
            b.recording_scope='other'
            def consume():return (yield Await(w))
            with self.assertRaises(LanguageError) as caught:s.drive(consume())
            self.assertEqual(caught.exception.code,'mode_error')
            self.assertEqual((a.state,b.state),('created','created'))
            self.assertFalse(a.observed or b.observed)
        finally:s.finish()
        self.clean(e)

    def test_reference_limit_releases_on_cold_cancel(self):
        e, _ = self.evaluator();s=e.suspension;s.begin()
        try:
            members=[sleep(e,1) for _ in range(100)]
            waits=[wait(e,members) for _ in range(100)]
            self.assertEqual(s.task_waits.references,10000)
            with self.assertRaises(ResourceLimit):wait(e,members)
            s.cancel(waits[0]);self.assertEqual(s.task_waits.references,9900)
            wait(e,members);self.assertEqual(s.task_waits.references,10000)
        finally:s.finish()
        self.clean(e)

    def test_many_overlapping_wait_notifications_are_iterative(self):
        e, _ = self.evaluator();s=e.suspension;s.begin()
        try:
            first=sleep(e,1);last=first
            for _ in range(800):last=wait(e,[last])
            def consume():return (yield Await(last))
            result=s.drive(consume())
            self.assertEqual(len(result['done']),1)
            self.assertTrue(all(task.state=='completed' for task in s.tasks))
            self.assertEqual(len({t.terminal_order for t in s.tasks}),len(s.tasks))
        finally:s.finish()
        self.clean(e)

    def test_terminal_callback_failure_cannot_strand_expired_timers(self):
        for hard in (LanguageError('notice'), ResourceLimit('notice'), ExecutionControl('notice'), KeyboardInterrupt('notice'), SystemExit('notice')):
            e, _ = self.evaluator();s=e.suspension;s.begin();fired=[]
            tasks=[sleep(e,1),sleep(e,1)]
            def trace(event):
                if event['event']=='task.completed' and not fired:
                    fired.append(True);raise hard
            e.trace_handler=trace
            for task in tasks:s.spawn(task)
            for task in tasks:s._step(task.runner)
            s.task_waits.clock=lambda:102.0
            try:
                with self.assertRaises(type(hard)) as caught:s.task_waits.poll()
                self.assertIs(caught.exception,hard)
                self.assertEqual([t.state for t in tasks],['completed','completed'])
                self.assertEqual(len(s.terminal_queue),0)
            finally:
                e.trace_handler=None;s.finish(original=hard)
            self.clean(e)

    def test_hard_member_priority_uses_completion_order_without_observing(self):
        e, _ = self.evaluator();s=e.suspension;s.begin()
        first,second=ResourceLimit('first'),KeyboardInterrupt('second')
        try:
            a=sleep(e,1);b=sleep(e,1)
            s.task_waits.complete(b,error=first)
            s.task_waits.complete(a,error=second)
            s._flush_terminals()
            w=wait(e,[a,b],timeout=0)
            def consume():return (yield Await(w))
            with self.assertRaises(ResourceLimit) as caught:s.drive(consume())
            self.assertIs(caught.exception,first)
            self.assertFalse(a.observed or b.observed)
            self.assertFalse(a.cancel_event.is_set() or b.cancel_event.is_set())
        finally:s.finish(original=first)
        self.clean(e)

    def test_metadata_is_read_only_and_remaining_is_captured(self):
        e, clock = self.evaluator();s=e.suspension;s.begin()
        try:
            a=sleep(e,2);w=wait(e,[a],'all',4)
            self.assertIsNone(s.info(w)['timing']['remainingSeconds'])
            self.assertFalse(w.observed);self.assertEqual(w.state,'created')
            s.spawn(w);s._step(w.runner)
            self.assertEqual(s.info(w)['phase'],'waiting_tasks')
            self.assertEqual(s.info(w)['timing'],dict(seconds=None,until='all',timeoutSeconds=4.0,remainingSeconds=4.0))
            clock.now+=1
            self.assertEqual(s.snapshot()['tasks'][1]['timing']['remainingSeconds'],3.0)
        finally:s.finish()
        self.clean(e)


if __name__=='__main__':unittest.main()
