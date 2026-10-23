"""Causal protocol cleanup probes, plus real Bot/Agent/resource execution.

The small barrier probes use real scheduler-owned Task/TaskGroup/Join identities
and inject an observed completion into _await; they do not claim worker execution.
The two integration probes execute the original scheduler and actual host workers.
No external model, browser, device, or production database is used.
"""
import threading
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec
from chaos.contexts import _await
from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import Await, Drain, Join, TaskCancelled
from chaos.task_groups import GroupAbort, TaskGroup


def idle():
    if False:
        yield


class ProtocolCancellationTests(unittest.TestCase):
    def barrier(self, *, body=True, member=True):
        evaluator = Evaluator()
        scheduler = evaluator.suspension
        scheduler.begin()
        owner = scheduler.task(idle())
        scheduler.current = owner.runner
        group = TaskGroup(scheduler)
        group.state = 'active'
        if body:
            owner.scope_group = group
        else:
            owner.group = group
        target = scheduler.task(idle())
        if member:
            target.group = group
        self.addCleanup(lambda: self.close_barrier(scheduler))
        return evaluator, scheduler, group, owner, target

    @staticmethod
    def close_barrier(scheduler):
        # These probes never started workers or ran the scheduler loop.
        scheduler.current = None
        for task in scheduler.tasks:
            task.runner.iterator.close()
        scheduler.active = False

    def interrupt_barrier(self, evaluator, target, original, *, owned=True):
        waiting = _await(evaluator, target, owned)
        first = next(waiting)
        self.assertIsInstance(first, Await)
        self.assertIs(first.task, target)
        if owned:
            drain = waiting.throw(original)
            self.assertIsInstance(drain, Drain)
            self.assertIs(drain.task, target)
        return waiting

    def test_group_owned_cleanup_preserves_original_for_body_and_member(self):
        for body in (True, False):
            for abort in (True, False):
                with self.subTest(body=body, abort=abort):
                    evaluator, scheduler, group, owner, target = self.barrier(body=body)
                    failure = LanguageError('original ordinary failure', code='original_failure')
                    original = GroupAbort(group, failure) if abort else failure
                    waiting = self.interrupt_barrier(evaluator, target, original)
                    self.assertEqual(target.cancel_sources, {group})
                    self.assertTrue(target.cancel_event.is_set())
                    acknowledgement = scheduler.cancelled_error(target)
                    with self.assertRaises(type(original)) as caught:
                        waiting.throw(acknowledgement)
                    self.assertIs(caught.exception, original)

    def test_same_group_owned_join_can_coexist_with_protocol_cleanup(self):
        evaluator, scheduler, group, owner, target = self.barrier(body=False)
        join = Join([target])
        join.runner = owner.runner
        scheduler._cancel(target, join)
        original = LanguageError('original', code='original_failure')
        waiting = self.interrupt_barrier(evaluator, target, original)
        self.assertEqual(target.cancel_sources, {join, group})
        with self.assertRaises(LanguageError) as caught:
            waiting.throw(scheduler.cancelled_error(target))
        self.assertIs(caught.exception, original)

    def test_explicit_global_and_epoch_cancellation_remain_hard(self):
        for source in ('explicit', 'global', 'epoch'):
            with self.subTest(source=source):
                evaluator, scheduler, group, owner, target = self.barrier()
                scheduler._cancel(target, source)
                waiting = self.interrupt_barrier(evaluator, target, LanguageError('ordinary'))
                self.assertIn(source, target.cancel_sources)
                self.assertIn(group, target.cancel_sources)
                acknowledgement = scheduler.cancelled_error(target)
                with self.assertRaises(TaskCancelled) as caught:
                    waiting.throw(acknowledgement)
                self.assertIs(caught.exception, acknowledgement)

    def test_foreign_group_and_unattributed_source_are_not_internal(self):
        for foreign_kind in ('group', 'anonymous'):
            with self.subTest(foreign_kind=foreign_kind):
                evaluator, scheduler, group, owner, target = self.barrier()
                foreign = TaskGroup(scheduler) if foreign_kind == 'group' else object()
                scheduler._cancel(target, foreign)
                waiting = self.interrupt_barrier(evaluator, target, LanguageError('ordinary'))
                self.assertEqual(target.cancel_sources, {foreign, group})
                acknowledgement = scheduler.cancelled_error(target)
                with self.assertRaises(TaskCancelled) as caught:
                    waiting.throw(acknowledgement)
                self.assertIs(caught.exception, acknowledgement)

    def test_foreign_join_and_foreign_event_cannot_borrow_group_identity(self):
        for foreign_kind in ('join', 'event'):
            with self.subTest(foreign_kind=foreign_kind):
                evaluator, scheduler, group, owner, target = self.barrier()
                if foreign_kind == 'join':
                    foreign_owner = scheduler.task(idle())
                    foreign_owner.group = TaskGroup(scheduler)
                    foreign = Join([target])
                    foreign.runner = foreign_owner.runner
                    scheduler._cancel(target, foreign)
                waiting = self.interrupt_barrier(evaluator, target, LanguageError('ordinary'))
                event = threading.Event() if foreign_kind == 'event' else target.cancel_event
                acknowledgement = TaskCancelled('foreign control', _cancel_event=event)
                with self.assertRaises(TaskCancelled) as caught:
                    waiting.throw(acknowledgement)
                self.assertIs(caught.exception, acknowledgement)

    def test_borrowed_task_is_never_cancelled_or_drained_by_protocol(self):
        evaluator, scheduler, group, owner, target = self.barrier()
        original = GroupAbort(group, LanguageError('member failure'))
        waiting = _await(evaluator, target, False)
        self.assertIs(next(waiting).task, target)
        with self.assertRaises(GroupAbort) as caught:
            waiting.throw(original)
        self.assertIs(caught.exception, original)
        self.assertEqual(target.cancel_sources, set())
        self.assertFalse(target.cancel_event.is_set())
        self.assertEqual(target.state, 'created')

    def test_cross_group_operation_retains_unique_cleanup_source(self):
        evaluator, scheduler, group, owner, target = self.barrier()
        target.group = TaskGroup(scheduler)
        scheduler._cancel(target, target.group)
        waiting = self.interrupt_barrier(evaluator, target, LanguageError('ordinary'))
        self.assertNotIn(group, target.cancel_sources)
        self.assertEqual(len(target.cancel_sources), 2)
        acknowledgement = scheduler.cancelled_error(target)
        with self.assertRaises(TaskCancelled) as caught:
            waiting.throw(acknowledgement)
        self.assertIs(caught.exception, acknowledgement)

    def test_group_and_current_scheduler_epoch_ownership_is_required(self):
        for invalid in ('group_epoch', 'group_owner', 'current_epoch', 'current_owner'):
            with self.subTest(invalid=invalid):
                evaluator, scheduler, group, owner, target = self.barrier()
                if invalid == 'group_epoch':
                    group.epoch -= 1
                elif invalid == 'group_owner':
                    group.owner = Evaluator().suspension
                elif invalid == 'current_epoch':
                    owner.epoch -= 1
                else:
                    owner.owner = Evaluator().suspension
                scheduler._cancel(target, group)
                waiting = self.interrupt_barrier(evaluator, target, LanguageError('ordinary'))
                self.assertIn(group, target.cancel_sources)
                self.assertEqual(len(target.cancel_sources), 2)
                acknowledgement = scheduler.cancelled_error(target)
                with self.assertRaises(TaskCancelled) as caught:
                    waiting.throw(acknowledgement)
                self.assertIs(caught.exception, acknowledgement)

    def test_cross_epoch_target_is_rejected_before_cleanup_authority(self):
        evaluator, scheduler, group, owner, target = self.barrier()
        target.epoch -= 1
        waiting = _await(evaluator, target, True)
        with self.assertRaises(LanguageError):
            next(waiting)
        self.assertEqual(target.cancel_sources, set())
        self.assertFalse(target.cancel_event.is_set())
        self.assertEqual(target.state, 'created')

    def test_unattributed_and_higher_priority_host_errors_keep_exact_identity(self):
        for failure in (TaskCancelled('unattributed'), ResourceLimit('late budget'),
                        ExecutionControl('host stop'), KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(error=type(failure).__name__):
                evaluator, scheduler, group, owner, target = self.barrier()
                waiting = self.interrupt_barrier(evaluator, target, LanguageError('ordinary'))
                with self.assertRaises(type(failure)) as caught:
                    waiting.throw(failure)
                self.assertIs(caught.exception, failure)

    def integration_case(self, shape, mode):
        entered, finished = threading.Event(), threading.Event()
        after, exits, blocks, requests = [], [], [], []
        registry = Registry()

        def block(value, context):
            blocks.append(value)
            entered.set()
            try:
                self.assertTrue(context.cancel.task.wait(3), 'owned protocol worker was not cancelled')
                raise TaskCancelled('actual cooperative acknowledgement')
            finally:
                finished.set()

        def fail(value, context):
            self.assertTrue(entered.wait(3), 'original failure preceded worker entry')
            raise LanguageError('original peer failure', code='original_peer')

        def choose(request, context):
            context.checkpoint()
            requests.append(request)
            return ToolCall('work', request.input) if request.step == 1 else Final(request.history[-1]['output'])

        registry.register_tool(ToolSpec('block', block))
        registry.register_tool(ToolSpec('fail', fail))
        registry.register_tool(ToolSpec('after', lambda value, context: after.append(value)))
        registry.register_tool(ToolSpec('exit', lambda value, context: exits.append(value)))
        registry.register_provider(ProviderSpec('fixture.protocol', choose))
        if shape == 'agent':
            prefix = '''async fn handler(x){tool("block",x);tool("after",x);42}
            agent actor{provider:"fixture.protocol",tools:["block","after"]};
            let equipped=agent_bind(actor,[agent_tool("work",handler,capabilities=["block","after"])]);'''
            action = 'await equipped(p)'
        else:
            prefix = '''class Resource{
              async fn __aenter__(self){tool("block",0);tool("after",0);42}
              async fn __aexit__(self,error){tool("exit",null);null}
            }'''
            action = 'async with Resource() as item{tool("after",item)}'
        source = prefix + '''async fn bad(){tool("fail",null)}
        async fn execute(p,s){spawn(bad());''' + action + ''';tool("after","continued")}
        let worker=bot("ordinary protocol failure",fn(s){null},execute,
          fn(s){{done:true,context:null,output:s.evidence}});
        let result=await worker();[result.status,bot_info(worker).busy,tool("finished",null)]'''
        registry.register_tool(ToolSpec('finished', lambda value, context: finished.is_set()))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('protocol', source=source, capabilities=['block', 'fail', 'after', 'exit', 'finished'])
            run = app.start('protocol', 1, None, mode=mode)
            self.assertEqual(run.result(8), ['needs_reconciliation', False, True])
            events = app.events('protocol', run_id=run.id, limit=300)['items']
            self.assertTrue(any(row['type'] == 'bot.scope.closed' for row in events))
        self.assertEqual(len(blocks), 1)
        self.assertTrue(entered.is_set())
        self.assertTrue(finished.is_set())
        self.assertEqual(after, [])
        self.assertEqual(exits, [])
        if shape == 'agent':
            self.assertEqual(len(requests), 1, 'failed callback resumed or replayed the model')
        self.assertFalse([thread.name for thread in threading.enumerate()
                          if thread.name.startswith('chaos-effect') and thread.is_alive()])

    def test_actual_bot_nested_agent_async_handler_preserves_ordinary_failure(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode):
                self.integration_case('agent', mode)

    def test_actual_bot_async_context_private_enter_preserves_ordinary_failure(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode):
                self.integration_case('context', mode)


if __name__ == '__main__':
    unittest.main()
