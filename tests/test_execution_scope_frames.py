"""Actual lexical scope fields, scheduler ownership and the source-only oracle."""
import unittest

from chaos.language import Evaluator, Env, ExecutionControl, LanguageError, Native, Parser, ResourceLimit
from chaos.execution_frames import ExecutionCursor, frame_roots
from chaos.execution_scopes import PromptScopeFrame, CancelShieldFrame, TaskGroupFrame, GroupBodyFrame
from chaos.suspension import CHECKPOINT, TaskCancelled


def snapshot(source, legacy=False, limit=100000, cancel=None):
    from contextlib import nullcontext
    from _context_reference142 import reference
    effects, trace, debug = [], [], []
    e = Evaluator(step_limit=limit, timeout=30,
                  tool_handler=lambda name, value: effects.append((name, value)) or value,
                  trace_handler=trace.append)
    e.cancelled = lambda: cancel is not None and e.steps >= cancel
    e.debug_hook = lambda node, env, frames: debug.append((node[0], dict(e.current_span), e.steps))
    with reference(e) if legacy else nullcontext():
        try:
            outcome = ('value', e.run(source))
        except LanguageError as error:
            outcome = ('error', error.record())
        except ExecutionControl as error:
            outcome = ('control', type(error).__name__, str(error))
    return outcome, e.steps, effects, trace, debug


class ExecutionScopeFrames(unittest.TestCase):
    def active(self, evaluator, cls):
        return next(frame for frame in reversed(evaluator._execution_cursor.frames) if type(frame) is cls)

    def test_prompt_body_and_handler_are_actual_scope_states(self):
        e = Evaluator()
        observed = []
        def probe(phase):
            frame = self.active(e, PromptScopeFrame)
            self.assertEqual(frame.phase, phase)
            self.assertIs(frame.owner, e)
            self.assertIn(frame.manager, frame_roots(frame))
            self.assertEqual(frame.manager.state, 'active' if phase == 'body' else 'closed')
            observed.append((phase, frame.entered))
        e.env.bind('probe', Native('probe', probe, e))
        source = 'let p=prompt_tag();with prompt_scope(p,fn(a,b){probe("handler");a+b}) as s{probe("body");abort_prompt(p,20,22)}'
        self.assertEqual(e.run(source), 42)
        self.assertEqual(observed, [('body', True), ('handler', False)])
        self.assertEqual(e.suspension.control_state.stack, [])

    def test_pending_prompt_handler_field_drives_actual_invocation(self):
        e = Evaluator()
        replacement = e.run('fn(value){value+2}')
        def replace():
            frame = self.active(e, PromptScopeFrame)
            self.assertEqual(frame.phase, 'body')
            frame.handler = replacement
        e.env.bind('replace_handler', Native('replace_handler', replace, e))
        source = 'let p=prompt_tag();with prompt_scope(p,fn(value){value+1}) as s{replace_handler();abort_prompt(p,40)}'
        self.assertEqual(e.run(source), 42)

    def test_pending_prompt_handler_root_rejects_foreign_evaluator(self):
        from chaos.capture_graph import seal
        e = Evaluator()
        foreign = Evaluator().run('fn(value){value}')
        def inject():
            frame = self.active(e, PromptScopeFrame)
            frame.handler = foreign
            seal(e, (e.suspension.current,), None)
        e.env.bind('inject', Native('inject', inject, e))
        with self.assertRaises(LanguageError) as caught:
            e.run('let p=prompt_tag();async fn work(){with prompt_scope(p,fn(value){value}) as s{inject();42}};await work()')
        self.assertEqual(caught.exception.code, 'ownership_error')
        self.assertTrue(all(scope.state == 'closed' for scope in e.suspension.control_scopes))

    def test_forced_prompt_close_releases_scope_without_running_handler_or_body(self):
        e = Evaluator()
        effects = []
        e.env.bind('effect', Native('effect', lambda: effects.append(1), e))
        tree = Parser('let p=prompt_tag();with prompt_scope(p,fn(x){effect()}) as scope{effect();42}').program()
        e.suspension.begin()
        cursor = e._s_evaluate(tree, Env(e.env))
        self.assertIs(type(cursor), ExecutionCursor)
        frame = None
        try:
            for _ in range(100):
                request = next(cursor)
                live = [f for f in cursor.frames if type(f) is PromptScopeFrame and f.entered]
                if live:
                    frame = live[-1]
                    self.assertIs(request, CHECKPOINT)
                    break
            self.assertIsNotNone(frame)
            self.assertEqual(frame.manager.state, 'active')
            cursor.close()
            self.assertEqual(frame.manager.state, 'closed')
            self.assertIsNone(frame.manager.holder)
            self.assertEqual(e.suspension.control_state.stack, [])
            self.assertEqual(effects, [])
        finally:
            cursor.close()
            e.suspension.finish()

    def test_shield_closed_notification_reads_authoritative_pending_result(self):
        effects, frames = [], []
        e = Evaluator(tool_handler=lambda name, value: effects.append((name, value)))
        def trace(event):
            if event.get('event') == 'shield.closed':
                frame = self.active(e, CancelShieldFrame)
                self.assertEqual(frame.phase, 'retiring')
                self.assertFalse(frame.entered)
                self.assertEqual(frame.manager.state, 'closed')
                self.assertEqual(frame.result, 42)
                frame.result = 84
                frames.append(frame)
        e.trace_handler = trace
        self.assertEqual(e.run('async fn work(){with cancel_shield() as guard{tool("body",0);42}};await work()'), 84)
        self.assertEqual(effects, [('body', 0)])
        self.assertEqual(len(frames), 1)

    def test_shield_pending_cancel_keeps_exact_source_and_stable_event(self):
        e = Evaluator()
        frames = []
        def probe():
            frame = self.active(e, CancelShieldFrame)
            self.assertEqual(frame.phase, 'body')
            self.assertTrue(frame.entered)
            self.assertIs(frame.task, e.suspension.current.task)
            self.assertIs(frame.manager.task, frame.task)
            self.assertEqual(frame.task.cancel_sources, {'explicit'})
            self.assertFalse(frame.task.cancel_event.is_set())
            frames.append(frame)
        e.env.bind('probe', Native('probe', probe, e))
        with self.assertRaises(TaskCancelled) as caught:
            e.run('var me=null;async fn work(){with cancel_shield() as guard{cancel(me);await sleep(0);probe();42}};me=work();await me')
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertIs(caught.exception._cancel_event, frame.task.cancel_event)
        self.assertEqual(frame.task.cancel_sources, {'explicit'})
        self.assertEqual(frame.task.cancel_shields, [])
        self.assertTrue(frame.manager.requested)
        self.assertEqual(frame.manager.state, 'closed')

    def test_nested_shields_do_not_deliver_or_clear_outer_pending_control(self):
        seen = []
        e = Evaluator()
        def probe():
            frames = [frame for frame in e._execution_cursor.frames if type(frame) is CancelShieldFrame]
            self.assertTrue(frames)
            seen.append([frame.manager.depth for frame in frames])
            self.assertEqual(e.suspension.current.task.cancel_sources, {'explicit'})
        e.env.bind('probe', Native('probe', probe, e))
        with self.assertRaises(TaskCancelled):
            e.run('var me=null;async fn work(){with cancel_shield() as a{with cancel_shield() as b{cancel(me);probe()};probe();42}};me=work();await me')
        self.assertEqual(seen, [[1, 2], [1]])

    def test_forced_shield_cursor_close_keeps_cancel_source_without_executing_body(self):
        from types import SimpleNamespace
        from chaos.cancellation import create
        e = Evaluator()
        seen = []
        e.env.bind('mark', Native('mark', lambda: seen.append('body'), e))
        e.suspension.begin()
        task = e.suspension.task(iter(()))
        e.suspension.current = task.runner
        node = Parser('with cancel_shield() as guard{mark();42}').program()[1][0]
        manager = create(e)
        boundary = SimpleNamespace(node=node, env=Env(e.env), capture=None)
        frame = CancelShieldFrame(boundary, manager)
        cursor = ExecutionCursor(e, frame)
        task.runner.iterator = cursor
        try:
            self.assertIs(next(cursor), CHECKPOINT)
            self.assertTrue(frame.entered)
            self.assertEqual(frame.phase, 'body')
            e.suspension.cancel(task)
            self.assertFalse(task.cancel_event.is_set())
            self.assertEqual(task.cancel_sources, {'explicit'})
            cursor.close()
            self.assertFalse(frame.entered)
            self.assertEqual(manager.state, 'closed')
            self.assertTrue(manager.requested)
            self.assertEqual(task.cancel_shields, [])
            self.assertTrue(task.cancel_event.is_set())
            self.assertEqual(task.cancel_sources, {'explicit'})
            self.assertEqual(seen, [])
            self.assertEqual(cursor.frames, [])
        finally:
            cursor.close()
            e.suspension.current = None
            e.suspension.finish(original=GeneratorExit())

    def test_group_body_and_parent_use_actual_distinct_scheduler_cursors(self):
        e = Evaluator()
        seen = []
        def probe():
            body = self.active(e, GroupBodyFrame)
            runner = e.suspension.current
            self.assertIs(body.group, runner.task.scope_group)
            self.assertIs(body.scope.get('group'), body.group)
            self.assertIs(runner.iterator, e._execution_cursor)
            parent = next(frame for frame in body.group.runner.iterator.frames if type(frame) is TaskGroupFrame)
            self.assertIs(parent.body_cursor, runner.iterator)
            self.assertIsNot(parent.body_cursor, body.group.runner.iterator)
            self.assertIn(parent.body_cursor, frame_roots(parent))
            seen.append(body.group)
        e.env.bind('probe', Native('probe', probe, e))
        self.assertEqual(e.run('async fn child(){await sleep(0);20};async with task_group() as group{group.spawn(child());probe();await sleep(0);42}'), 42)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].state, 'closed')
        self.assertTrue(all(task.state == 'completed' for task in seen[0].members))

    def test_group_outcome_field_is_the_scheduler_return_value(self):
        e = Evaluator()
        bodies = []
        def remember():
            bodies.append(self.active(e, GroupBodyFrame))
        def trace(event):
            if event.get('event') == 'group.closed':
                body = bodies[-1]
                self.assertIs(body.outcome, body.group.outcome)
                self.assertEqual(body.phase, 'done')
                self.assertEqual(body.outcome.value, 42)
                body.outcome.value = 84
        e.env.bind('remember', Native('remember', remember, e))
        e.trace_handler = trace
        self.assertEqual(e.run('async with task_group() as group{remember();42}'), 84)
        self.assertEqual(len(bodies), 1)

    def test_group_body_soft_transfers_and_full_values_keep_original_cleanup(self):
        source = '''var events=[];async fn work(){async with task_group() as g{
          try{return return_values(20,22)}finally{events=append(events,"cleanup")}}};
          let values=multiple_value_list(async fn(){await work()});
          var total=0;for n in [1,2,3]{async with task_group() as g{
            if n==1{continue}else{null};if n==3{break}else{null};total+=n}};
          [values,total,events]'''
        self.assertEqual(Evaluator().run(source), [[20,22],2,['cleanup']])

    def test_source_only_oracle_exact_results_ticks_effects_debug_and_trace(self):
        sources = [
            'let p=prompt_tag();multiple_value_list(fn(){with prompt_scope(p,fn(a,b){return_values(a+b,b)}) as s{abort_prompt(p,20,22)}})',
            'let p=prompt_tag();try{with prompt_scope(p) as s{throw 42}}catch e{e.value}',
            'async fn work(){with cancel_shield() as s{await sleep(0);return 42}};await work()',
            'var me=null;async fn work(){with cancel_shield() as s{cancel(me);42}};me=work();await me',
            'async fn child(n){await sleep(0);n};async with task_group() as g{let a=g.spawn(child(20));let b=g.spawn(child(22));(await a)+(await b)}',
            'async fn bad(){throw 42};try{async with task_group(errors="group") as g{g.spawn(bad());await sleep(0)}}catch* _ as errors{errors.exceptions[0].value}',
            'let p=prompt_tag();with prompt_scope(p) as s{async with task_group() as g{try{abort_prompt(p,42)}finally{tool("cleanup",0)}}}',
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(snapshot(source), snapshot(source, legacy=True))

    def test_every_small_scope_budget_and_cancel_boundary_matches_source_oracle(self):
        sources = ['let p=prompt_tag();with prompt_scope(p) as s{20+22}',
                   'async fn f(){with cancel_shield() as s{42}};await f()',
                   'async with task_group() as g{42}']
        for source in sources:
            total = snapshot(source, legacy=True)[1]
            for limit in range(total+2):
                with self.subTest(source=source, limit=limit):
                    self.assertEqual(snapshot(source, limit=limit), snapshot(source, legacy=True, limit=limit))
            for cancel in range(total+2):
                with self.subTest(source=source, cancel=cancel):
                    self.assertEqual(snapshot(source, cancel=cancel), snapshot(source, legacy=True, cancel=cancel))

    def test_hard_control_skips_prompt_handler_and_closes_active_shield(self):
        calls = []
        e = Evaluator()
        failure = ResourceLimit('scope hard stop')
        def stop():
            raise failure
        e.env.bind('stop', Native('stop', stop, e))
        e.env.bind('mark', Native('mark', lambda: calls.append(1), e))
        with self.assertRaises(ResourceLimit) as caught:
            e.run('let p=prompt_tag();async fn work(){with prompt_scope(p,fn(x){mark()}) as s{with cancel_shield() as shield{stop()}}};await work()')
        self.assertIs(caught.exception, failure)
        self.assertEqual(calls, [])
        self.assertTrue(all(scope.state == 'closed' for scope in e.suspension.control_scopes))
        self.assertTrue(all(shield.state == 'closed' for shield in e.suspension.cancel_shields))


if __name__ == '__main__':
    unittest.main()
