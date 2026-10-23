"""Dynamic errors follow logical execution, resource and cancellation boundaries."""
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled, TERMINAL


class ExceptionResourceTests(unittest.TestCase):
    def run_code(self, source, **kwargs): return Evaluator(**kwargs).run(source)

    def test_interleaved_tasks_keep_separate_errors_across_await(self):
        source = '''fn current(){try{throw}catch e{e.value}};
          async fn work(n){try{throw n}catch {value:x,.._} if do{await sleep(0);current()==x}{
          let a=current();await sleep(0);[a,current()]}};await gather([work(20),work(22)])'''
        self.assertEqual(self.run_code(source), [[20, 20], [22, 22]])

    def test_new_cold_tasks_ignore_both_creator_and_awaiting_errors(self):
        source = '''async fn child(){try{throw}catch e{e.code}};
          let task=try{throw 20}catch e{child()};
          try{throw 22}catch e{[await task,await child(),e.value]}'''
        self.assertEqual(self.run_code(source), ['rethrow_error', 'rethrow_error', 22])

    def test_task_group_lexical_body_inherits_but_members_do_not(self):
        source = '''fn current(){try{throw}catch e{e.value}};
          async fn member(){try{throw}catch e{e.code}};
          try{throw 42}catch outer{async with task_group() as group{
          let child=group.spawn(member());[current(),await child,current()]}}'''
        self.assertEqual(self.run_code(source), [42, 'rethrow_error', 42])
        self.assertEqual(self.run_code('try{try{throw 42}catch e{async with task_group() as group{throw}}}catch out{out.value}'), 42)

    def test_sync_and_async_streams_resume_their_own_handler_state(self):
        for flavor, step in [('fn*', 'next'), ('async fn*', 'await anext')]:
            source = '''fn current(){try{throw}catch e{e.value}};'''+flavor+''' work(n){
              try{throw n}catch e{yield current();throw}};
              let a=work(20);let b=work(22);'''+step+'(a);'+step+'''(b);
              try{throw 99}catch outer{let x=try{'''+step+'''(a)}catch e{e.value};
              let y=try{'''+step+'''(b)}catch e{e.value};[x,y,current()]}'''
            with self.subTest(flavor=flavor): self.assertEqual(self.run_code(source), [20, 22, 99])

    def test_unstarted_stream_does_not_import_creation_or_resumer_context(self):
        for flavor, step in [('fn*', 'next'), ('async fn*', 'await anext')]:
            source = flavor+' child(){try{throw}catch e{yield e.code}};let stream=try{throw 20}catch e{child()};try{throw 22}catch e{'+step+'(stream).value}'
            # Parenthesize await results so the member belongs to the item.
            if flavor.startswith('async'): source = source.replace('await anext(stream).value', '(await anext(stream)).value')
            with self.subTest(flavor=flavor): self.assertEqual(self.run_code(source), 'rethrow_error')

    def test_close_runs_existing_handler_cleanup_without_leaking_to_caller(self):
        for flavor, step, close in [('fn*', 'next', 'close'), ('async fn*', 'await anext', 'await aclose')]:
            source = '''fn current(){try{throw}catch e{[e.code,e.value]}};var seen=[];'''+flavor+''' work(){
              try{throw 42}catch e{try{yield 1}finally{seen=append(seen,current())}}};
              let stream=work();'''+step+'(stream);'+close+'(stream);[seen,current()]'
            with self.subTest(flavor=flavor):
                self.assertEqual(self.run_code(source), [[['user_throw', 42]], ['rethrow_error', None]])

    def test_illegal_cleanup_yield_force_abort_restores_caller_error(self):
        for flavor, first, closing in [('fn*', 'next(s)', 'close(s)'), ('async fn*', 'await anext(s)', 'await aclose(s)')]:
            evaluator = Evaluator()
            source = '''fn current(){try{throw}catch e{e.value}};'''+flavor+''' bad(){
              try{throw 7}catch e{try{yield 1}finally{yield 2}}};let s=bad();'''+first+''';
              let result=try{throw 42}catch outer{try{'''+closing+'''}catch closeError{null};current()};result'''
            with self.subTest(flavor=flavor): self.assertEqual(evaluator.run(source), 42)
            self.assertIsNone(evaluator.handled_error)
            self.assertEqual((evaluator.frames, evaluator.depth), ([], 0))

    def test_with_exit_does_not_activate_pending_body_error(self):
        prefix = 'class C{fn __enter__(s){null}fn __exit__(s,e){throw}};'
        self.assertEqual(self.run_code(prefix+'try{with C() as resource{throw 22}}catch e{e.code}'), 'rethrow_error')
        source = prefix+'try{throw 20}catch outer{try{with C() as resource{throw 22}}catch e{e.value}}'
        self.assertEqual(self.run_code(source), 20)

    def test_exit_stack_callback_uses_only_existing_outer_context(self):
        source = 'try{with exit_stack() as stack{stack.callback(fn(){throw});throw 22}}catch e{e.code}'
        self.assertEqual(self.run_code(source), 'rethrow_error')
        source = 'try{throw 20}catch outer{try{with exit_stack() as stack{stack.callback(fn(){throw});throw 22}}catch e{e.value}}'
        self.assertEqual(self.run_code(source), 20)

    def test_async_error_finally_keeps_original_across_await(self):
        source = '''async fn work(){try{throw 42}finally{await sleep(0);throw}};
          try{await work()}catch {value:x,.._}{x}'''
        self.assertEqual(self.run_code(source), 42)

    def test_hard_errors_skip_all_cases_and_cleanup_and_restore_state(self):
        for failure in (ResourceLimit('limit'), ExecutionControl('host'), TaskCancelled('foreign'), KeyboardInterrupt(), SystemExit(7)):
            seen = []
            def effect(kind, payload, cancel):
                seen.append(payload['name'])
                if payload['name'] == 'stop': raise failure
            evaluator = Evaluator(tool_handler=lambda *a: None, effect_handler=effect)
            source = '''async fn work(){try{throw 20}catch outer{try{tool("stop",null)}
              catch e if true{tool("catch",null)}catch e{tool("later",null)}
              finally{tool("cleanup",null)}}};await work()'''
            with self.subTest(failure=type(failure).__name__), self.assertRaises(type(failure)) as raised:
                evaluator.run(source)
            self.assertIs(raised.exception, failure)
            self.assertEqual(seen, ['stop'])
            self.assertIsNone(evaluator.handled_error)

    def test_local_cancel_in_suspended_guard_retracts_receive_and_clears_context(self):
        captured, seen = [], []
        def debug(node, env, frames):
            try: captured[:] = [env.get('messages')]
            except LanguageError: pass
        source = '''let messages=channel();var target=null;
          async fn consumer(){try{throw 42}catch e if do{await channel_receive(messages);true}{tool("bad",null)}};
          async fn stop(){while channel_info(messages).waitingReceivers!=1{1};cancel(target)};
          target=consumer();spawn(stop());await target'''
        evaluator = Evaluator(debug_hook=debug, tool_handler=lambda *a: seen.append(a), step_limit=100000)
        with self.assertRaises(TaskCancelled): evaluator.run(source)
        self.assertEqual(seen, [])
        self.assertEqual(len(captured[0].receivers), 0)
        self.assertTrue(all(task.state in TERMINAL for task in evaluator.suspension.tasks))
        self.assertIsNone(evaluator.handled_error)

    def test_group_abort_skips_handlers_before_outer_catch_receives_original(self):
        source = '''let messages=channel();var seen=[];async fn bad(){
          while channel_info(messages).waitingReceivers!=1{1};throw "peer"};
          let result=try{async with task_group() as group{group.spawn(bad());
          try{await channel_receive(messages)}catch e if true{seen=append(seen,"wrong")}catch e{0}}}
          catch {value:x,.._}{x};[result,seen]'''
        self.assertEqual(self.run_code(source, step_limit=100000), ['peer', []])

    def test_guard_loop_and_repeated_rethrow_share_original_budget(self):
        for source in ('try{throw 1}catch e if do{while true{};true}{42}',
                       'fn f(){try{throw}catch e{f()}};f()'):
            evaluator = Evaluator(step_limit=200)
            with self.subTest(source=source), self.assertRaises(ResourceLimit): evaluator.run(source)
            self.assertIsNone(evaluator.handled_error)


if __name__ == '__main__': unittest.main()
