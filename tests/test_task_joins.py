import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.runtime import Budget, Cancelled, CombinedCancel, TaskBudget
from chaos.suspension import Await, TaskCancelled


class TaskJoinTests(unittest.TestCase):
    def test_info_is_cold_and_detached(self):
        result = Evaluator().run('''
          var count=0; async fn f(){count=count+1;42}
          let a=f(); let joined=gather([a,a]);
          let first=task_info(joined); let child=task_info(a);
          let value=await joined;
          [first,child,value,count,task_info(joined),task_info(a)]
        ''')
        before, child, values, count, after, finished = result
        self.assertEqual((before['state'],before['kind'],before['observed']),('created','gather',False))
        self.assertEqual(before['memberTaskIds'],[child['id'],child['id']])
        self.assertEqual(before['memberCount'],2)
        self.assertFalse(before['membersTruncated'])
        self.assertEqual((child['state'],child['observed']),('created',False))
        self.assertEqual((values,count),([42,42],1))
        self.assertEqual((after['state'],finished['state']),('completed','completed'))
        self.assertTrue(after['observed']);self.assertTrue(finished['observed'])

    def test_parent_identity_and_no_result_materialization(self):
        result=Evaluator().run('''
          async fn child(){throw "secret"}
          async fn parent(){let t=child();task_info(t)}
          let p=parent();[task_info(p),await p]
        ''')
        self.assertEqual(result[1]['parentTaskId'],result[0]['id'])
        self.assertNotIn('result',result[1]);self.assertNotIn('error',result[1])

    def test_info_members_and_debug_snapshot_are_bounded(self):
        evaluator=Evaluator();seen=[]
        def trace(event):
            if event.get('kind')=='gather' and event['event']=='task.created':
                seen.append(evaluator.suspension.snapshot())
        evaluator.trace_handler=trace
        value=evaluator.run('async fn f(n){n}let tasks=map(fn(n){f(n)},range(105));task_info(gather(tasks))')
        self.assertEqual((value['memberCount'],len(value['memberTaskIds']),value['membersTruncated']),(105,100,True))
        self.assertEqual((seen[0]['total'],len(seen[0]['tasks']),seen[0]['truncated']),(106,100,True))

    def test_info_rejects_epoch_and_foreign_owner_without_observing(self):
        first=Evaluator();handle=first.run('async fn f(){1}f()')
        for evaluator in (first,Evaluator()):
            evaluator.suspension.begin()
            with self.assertRaises(LanguageError):evaluator.suspension.info(handle)
            self.assertFalse(handle.observed)
            evaluator.suspension.finish()

    def test_snapshot_does_not_apply_public_recording_filter(self):
        evaluator=Evaluator();scheduler=evaluator.suspension;scheduler.begin()
        def empty():
            if False:yield
        outer=scheduler.task(empty());recorded=scheduler.task(empty())
        recorded.recording_scope='recorded';scheduler.current=recorded.runner
        try:
            with self.assertRaises(LanguageError):scheduler.info(outer)
            self.assertEqual(scheduler.snapshot()['total'],2)
            self.assertFalse(outer.observed)
        finally:
            scheduler.current=None;scheduler.finish()

    def test_gather_failure_uses_completion_order(self):
        second_done=threading.Event();barrier=threading.Barrier(2)
        def trace(event):
            if event.get('taskId')=='task-2' and event['event']=='task.failed':second_done.set()
        def effect(kind,payload,cancel):
            barrier.wait(2)
            if payload['value']==1:
                self.assertTrue(second_done.wait(2));raise LanguageError('late first')
            raise LanguageError('early second')
        value=Evaluator(tool_handler=lambda *a:None,effect_handler=effect,trace_handler=trace).run(
            'async fn f(n){tool("x",n)}try{await gather([f(1),f(2)])}catch e{e.message}')
        self.assertEqual(value,'early second')

    def test_nested_and_overlapping_batches_keep_order(self):
        self.assertEqual(Evaluator().run('''
          async fn f(n){n}let a=f(1);let b=f(2);
          await gather([gather([a,b,a]),gather([b,a])])
        '''),[[1,2,1],[2,1]])

    def test_inner_peer_cancel_does_not_mask_original_failure(self):
        self.assertEqual(Evaluator().run('''
          async fn bad(){throw "original"}async fn good(){7}
          try{await gather([gather([bad(),good()]),good()])}catch e{e.value}
        '''),'original')

    def test_deadlock_reaches_catch_only_after_batch_terminates(self):
        programs = [
            '''var joined=null;async fn child(){await joined}
               let one=child();joined=gather([one]);
               try{await joined}catch e{[e.code,task_info(one).state,task_info(joined).state]}''',
            '''var a=null;var b=null;async fn fa(){await b}async fn fb(){await a}
               a=fa();b=fb();let joined=gather([a,b]);
               try{await joined}catch e{[e.code,task_info(a).state,task_info(b).state,task_info(joined).state]}''',
            '''var a=null;var b=null;async fn f(){await b}async fn g(){await a}a=f();b=g();let inner=gather([a,b]);let outer=gather([inner]);
               try{await outer}catch e{[e.code,task_info(a).state,task_info(inner).state,task_info(outer).state]}'''
        ]
        for source in programs:
            with self.subTest(source=source):
                value=Evaluator().run(source)
                self.assertEqual(value[0],'task_deadlock')
                self.assertTrue(all(state in ('completed','failed','cancelled','closed') for state in value[1:]),value)

    def test_explicitly_cancelled_member_remains_hard(self):
        with self.assertRaises(TaskCancelled):
            Evaluator().run('async fn f(){1}let a=f();cancel(a);try{await gather([a])}catch e{0}')

    def test_unrelated_explicit_cancel_does_not_poison_success(self):
        self.assertEqual(Evaluator().run('async fn f(n){n}let a=f(1);let b=f(2);cancel(a);await gather([b])'),[2])

    def test_existing_host_taskcancelled_acknowledges_only_its_worker(self):
        entered=threading.Event();exited=threading.Event();signal=TaskCancelled('existing host cancellation')
        def effect(kind,payload,cancel):
            if payload['value']==1:
                entered.set()
                try:
                    self.assertTrue(cancel.wait(2));raise signal
                finally:exited.set()
            self.assertTrue(entered.wait(2));return 42
        value=Evaluator(tool_handler=lambda *a:None,effect_handler=effect).run('''
          async fn f(n){tool("work",n)}let one=spawn(f(1));let two=spawn(f(2));
          let value=await two;cancel(one);value
        ''')
        self.assertEqual(value,42);self.assertTrue(exited.is_set())
        self.assertIsNotNone(signal._cancel_event)

    def test_unrequested_or_subclass_host_cancellation_remains_hard(self):
        class HostControl(TaskCancelled):pass
        for signal in (TaskCancelled('not requested'),HostControl('explicit host control')):
            with self.subTest(signal=type(signal).__name__):
                def effect(*args):raise signal
                with self.assertRaises(type(signal)) as caught:
                    Evaluator(tool_handler=lambda *a:None,effect_handler=effect).run(
                        'async fn f(){tool("work",1)}try{await gather([f()])}catch e{0}')
                self.assertIs(caught.exception,signal)

    def test_epoch_drain_consumes_late_hard_result(self):
        entered=threading.Event();error=ResourceLimit('late drain failure')
        def effect(kind,payload,cancel):
            entered.set();self.assertTrue(cancel.wait(2));raise error
        def tool(name,value):self.assertTrue(entered.wait(2));return 0
        # The second task holds the top-level await until the unobserved task's
        # worker has entered; only epoch teardown asks that worker to stop.
        def wait_effect(kind,payload,cancel):
            if payload['name']=='wait':self.assertTrue(entered.wait(2));return 0
            return effect(kind,payload,cancel)
        with self.assertRaises(ResourceLimit) as caught:
            Evaluator(tool_handler=tool,effect_handler=wait_effect).run(
                'async fn f(){tool("work",0)}async fn wait(){tool("wait",0)}spawn(f());await wait();0')
        self.assertIs(caught.exception,error)

    def test_local_runtime_cancellation_has_event_identity(self):
        parent,one,two=threading.Event(),threading.Event(),threading.Event()
        shared=Budget(parent)
        one.set()
        with self.assertRaises(TaskCancelled) as caught:TaskBudget(shared,CombinedCancel(parent,one)).tick()
        self.assertIs(caught.exception._cancel_event,one)
        TaskBudget(shared,CombinedCancel(parent,two)).tick()
        with self.assertRaises(TaskCancelled) as nested:
            TaskBudget(shared,CombinedCancel(CombinedCancel(parent,one),two)).tick()
        self.assertIs(nested.exception._cancel_event,one)
        parent.set()
        with self.assertRaises(Cancelled):TaskBudget(shared,CombinedCancel(parent,one)).tick()

    def test_lifecycle_metadata_and_join_phase_are_real(self):
        events=[];snapshots=[];evaluator=Evaluator(trace_handler=events.append)
        evaluator.debug_hook=lambda *args:snapshots.append(evaluator.suspension.snapshot())
        evaluator.run('async fn f(n){n+1}await gather([f(1),f(2)])')
        started=[event for event in events if event['event']=='task.started' and event['kind']=='gather']
        self.assertEqual(len(started),1);self.assertEqual(started[0]['memberCount'],2)
        self.assertTrue(any(task['phase']=='joining' for snapshot in snapshots for task in snapshot['tasks']))
        self.assertEqual({event['taskId'] for event in events if event['event']=='task.created'},
                         {event['taskId'] for event in events if event['event']=='task.completed'})


if __name__=='__main__':unittest.main()
