"""External handoff parity, actual live capture, and real worker budget drain."""
import threading
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, Native, ResourceLimit, LanguageError, TailCall
from chaos.suspension import Task
from _effect_schedule158 import effect_schedule
from test_bot_execution_frames import snapshot, BASIC, VERIFY


def without_step(value):
    if type(value) is dict:return {key:without_step(item) for key,item in value.items() if key!='step'}
    if type(value) in (list,tuple):return [without_step(item) for item in value]
    return value


class EffectSchedule158(unittest.TestCase):
    def test_same_program_cost_depends_on_real_worker_handoff_not_native_algorithm(self):
        cases={}
        for policy in ('already_done','first_wait'):
            for legacy in (False,True):
                with effect_schedule(policy) as stats:
                    value=snapshot(BASIC,legacy=legacy,effect_policy=None)
                self.assertEqual(value[0],('value',['achieved',42,1]))
                self.assertEqual(stats['shutdownReleases'],0)
                self.assertGreater(stats['submitted'],0)
                self.assertEqual(stats['realExecutors'],1)
                if policy=='already_done':
                    self.assertEqual(stats['completedBeforeSubmitReturns'],stats['submitted'])
                    self.assertEqual(stats['waitCalls'],0)
                else:
                    self.assertEqual(stats['pendingBeforeSubmitReturns'],stats['submitted'])
                    self.assertEqual(stats['waitBoundaryReleases'],stats['submitted'])
                    self.assertEqual(stats['waitCalls'],stats['submitted'])
                cases[policy,legacy]=(value,dict(stats))
            self.assertEqual(cases[policy,False][0],cases[policy,True][0])
        for legacy in (False,True):
            ready,rs=cases['already_done',legacy];pending,ps=cases['first_wait',legacy]
            self.assertEqual(ready[0],pending[0]);self.assertEqual(ready[2:4],pending[2:4])
            self.assertEqual(without_step(ready[4]),without_step(pending[4]))
            self.assertEqual([item[:2] for item in ready[5]],[item[:2] for item in pending[5]])
            self.assertGreater(pending[1],ready[1])
            self.assertEqual(pending[1]-ready[1],ps['waitCalls']-rs['waitCalls'])

        # Existing old threshold loops retain their exact source/default limits
        # under already_done. Exercise that same source at every cut point under
        # the other fixed real-worker handoff as independent coverage.
        source='let b=bot("g",fn(s){1},fn(p,s){p},'+VERIFY+');(await b()).status'
        total=snapshot(source,legacy=True,effect_policy='first_wait')[1]
        for limit in range(total+2):
            with self.subTest(first_wait_limit=limit):
                self.assertEqual(snapshot(source,limit=limit,effect_policy='first_wait'),
                    snapshot(source,legacy=True,limit=limit,effect_policy='first_wait'))
        for cancel in range(total+2):
            with self.subTest(first_wait_cancel=cancel):
                self.assertEqual(snapshot(source,cancel=cancel,effect_policy='first_wait'),
                    snapshot(source,legacy=True,cancel=cancel,effect_policy='first_wait'))

    def test_actual_live_bot_task_capture_walks_typed_pending_frames_for_all_alignments(self):
        from chaos.capture_graph import seal
        import chaos.execution_frames as frames
        from chaos.execution_bot import BotRunFrame
        from chaos.execution_context_protocols import ResourceAwaitFrame
        for policy in ('already_done','first_wait'):
            for padding in range(64):
                witnesses=[]
                def setup(e,host):
                    e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
                    def capture(task):
                        self.assertIs(type(task),Task);e.suspension.validate(task,Task)
                        self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                        self.assertIs(type(task.runner.iterator),frames.ExecutionCursor)
                        kinds=[];original=frames.frame_roots
                        def roots(frame):kinds.append(type(frame));return original(frame)
                        with patch.object(frames,'frame_roots',roots):seal(e,(task,),None)
                        self.assertIn(ResourceAwaitFrame,kinds)
                        witnesses.append(set(kinds));return None
                    e.env.bind('capture_probe158',Native('capture_probe158',capture,e,captures=()))
                source='''let padding=range(PADDING);let ready=channel(1);let gate=channel();
                  var saved=null;let sink=limited(fn(){fn(t){saved=t;null}});
                  async fn plan(s){await channel_send(ready,0);await channel_receive(gate);42};
                  let b=bot("g",plan,fn(p,s){p},'''+VERIFY+''');let task=b();spawn(task);
                  await channel_receive(ready);capture_probe158(task);sink(task);await channel_send(gate,0);(await saved).output'''
                source=source.replace('PADDING',str(padding))
                with self.subTest(policy=policy,padding=padding):
                    modern=snapshot(source,setup=setup,effect_policy=policy)
                    old=snapshot(source,legacy=True,setup=setup,effect_policy=policy)
                    self.assertEqual(modern[0],('value',42));self.assertEqual(modern,old)
                    self.assertEqual(len(witnesses),2);self.assertIn(BotRunFrame,witnesses[0])
                    self.assertNotIn(BotRunFrame,witnesses[1])

    def test_original_real_worker_idle_budget_hook_cancels_and_drains_before_return(self):
        entered=threading.Event();release=threading.Event();ended=threading.Event();seen=[]
        failure=ResourceLimit('actual global budget hook')
        class Probe(Evaluator):
            def tick(self):
                if entered.is_set() and not release.is_set() and self.suspension.current is None:
                    seen.append(self.suspension.current);release.set();raise failure
                return super().tick()
        def handler(kind,payload,cancel):
            entered.set()
            if not release.wait(2):raise AssertionError('Original idle hook was skipped')
            if not cancel.wait(2):raise AssertionError('Global cancellation did not reach worker')
            ended.set();return 42
        evaluator=Probe(tool_handler=lambda *args:None,effect_handler=handler)
        with self.assertRaises(ResourceLimit) as caught:evaluator.run('async fn f(){tool("body",0)}await f()')
        self.assertIs(caught.exception,failure);self.assertEqual(seen,[None]);self.assertTrue(ended.is_set())
        self.assertFalse(evaluator.suspension.active)
        self.assertIsNone(evaluator.suspension.pool)

    def test_real_future_adoption_preserves_worker_exception_identity_in_each_policy(self):
        for policy in ('already_done','first_wait'):
            failure=LanguageError('actual worker failure',code='fixture_error');ended=threading.Event()
            def handler(kind,payload,cancel):ended.set();raise failure
            evaluator=Evaluator(tool_handler=lambda *args:None,effect_handler=handler)
            with effect_schedule(policy) as stats:
                with self.assertRaises(LanguageError) as caught:evaluator.run('async fn f(){tool("body",0)}await f()')
            self.assertIs(caught.exception,failure);self.assertTrue(ended.is_set())
            self.assertEqual(stats['submitted'],1);self.assertEqual(stats['shutdownReleases'],0)
            self.assertFalse(evaluator.suspension.active);self.assertIsNone(evaluator.suspension.pool)
