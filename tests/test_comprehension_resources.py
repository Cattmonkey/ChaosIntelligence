"""Comprehensions borrow runtime resources and bound each source independently."""
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.session import Session


SYNC = '''class Source{var reads=0;var factories=0;var closes=0;var sends=0;var throws=0;var guards=0;
fn __iter__(self){self.factories=self.factories+1;self}
fn __next__(self){self.reads=self.reads+1;{done:false,value:self.reads}}
fn __send__(self,x){self.sends=self.sends+1;{done:false,value:x}}
fn __throw__(self,x){self.throws=self.throws+1;{done:false,value:x}}
fn __close__(self){self.closes=self.closes+1;null}}let raw=Source();raw.closes=0;'''
ASYNC = '''class Source{var reads=0;var factories=0;var closes=0;
fn __aiter__(self){self.factories=self.factories+1;self}
async fn __anext__(self){self.reads=self.reads+1;{done:false,value:self.reads}}
async fn __aclose__(self){self.closes=self.closes+1;null}}let raw=Source();raw.closes=0;'''


class ComprehensionResourceTests(unittest.TestCase):
    def evaluate(self, source, bindings=None, **kwargs):
        return Evaluator(**kwargs).run(source, bindings)

    def test_first_source_fixed_at_creation_but_factory_and_step_are_lazy(self):
        for prefix, expression, advance in [
            (SYNC, '(x*2 for x in selected)', 'next(comp)'),
            (ASYNC, 'async (x*2 async for x in selected)', 'await anext(comp)'),
        ]:
            with self.subTest(expression=expression):
                source = prefix + 'var selected=raw;let comp=' + expression + ''';
selected=Source();let before=[raw.factories,raw.reads];let first=''' + advance + ''';
[before,first,raw.factories,raw.reads,selected.factories,selected.reads]'''
                self.assertEqual(self.evaluate(source), [[0, 0], {'done': False, 'value': 2}, 1, 1, 0, 0])

    def test_close_cold_wrappers_never_acquires_or_closes_captured_source(self):
        for prefix, expression, closing, advance in [
            (SYNC, '(x for x in raw)', 'close(comp)', 'next(comp)'),
            (ASYNC, 'async (x async for x in raw)', 'await aclose(comp)', 'await anext(comp)'),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(self.evaluate(prefix + 'let comp=' + expression + ';' + closing +
                    ';[raw.factories,raw.reads,raw.closes,' + advance + ']'),
                    [0, 0, 0, {'done': True, 'value': None}])

    def test_suspended_wrapper_does_not_hold_borrow_or_close_user_handle(self):
        for prefix, acquire, expression, advance, cursor_next, closing in [
            (SYNC, 'iter(raw)', '(x*10 for x in cursor)', 'next(comp)', 'next(cursor)', 'close(comp)'),
            (ASYNC, 'aiter(raw)', 'async (x*10 async for x in cursor)', 'await anext(comp)', 'await anext(cursor)', 'await aclose(comp)'),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(self.evaluate(prefix + 'let cursor=' + acquire + ';let comp=' + expression +
                    ';let first=' + advance + ';let alias=' + cursor_next + ';let second=' + advance +
                    ';' + closing + ';[first,alias,second,raw.closes,' + cursor_next + ']'),
                    [{'done': False, 'value': 10}, {'done': False, 'value': 2},
                     {'done': False, 'value': 30}, 0, {'done': False, 'value': 4}])

    def test_wrapper_send_ignored_and_throw_not_forwarded_to_source(self):
        source = SYNC + '''let cursor=iter(raw);let comp=(x*10 for x in cursor);
let first=next(comp);let second=send(comp,999);let error=try{throw_into(comp,"wrapper error")}catch e{e.value};
[first,second,error,raw.sends,raw.throws,raw.closes,next(cursor)]'''
        self.assertEqual(self.evaluate(source), [{'done': False, 'value': 10}, {'done': False, 'value': 20},
                                                'wrapper error', 0, 0, 0, {'done': False, 'value': 3}])

    def test_guard_and_record_key_failures_stop_before_head_and_leave_source_open(self):
        for expression, expected_type in (('[tool("unexpected",x) for x in cursor if 1]', 'Bool'),
                                          ('{x:tool("unexpected",x) for x in cursor}', 'String')):
            seen = []
            with self.subTest(expression=expression):
                result = self.evaluate(SYNC + 'let cursor=iter(raw);let error=try{' + expression +
                    '}catch e{e.message};[error,raw.reads,raw.closes,next(cursor)]',
                    tool_handler=lambda name, value: seen.append(value))
                self.assertIn(expected_type, result[0])
                self.assertEqual(result[1:], [1, 0, {'done': False, 'value': 2}])
                self.assertEqual(seen, [])

    def test_finite_limit_probes_eof_even_when_every_guard_rejects(self):
        for expression in ('[x for x in raw if false]', '{"same":x for x in raw if false}',
                           'collect((x for x in raw if false))',
                           'await acollect(async (x for x in raw if false))'):
            prefix = '''class Source{var reads=0;fn __iter__(self){self}
fn __next__(self){self.reads=self.reads+1;{done:self.reads>10000,value:self.reads}}}let raw=Source();raw.closes=0;'''
            with self.subTest(expression=expression):
                expected = {} if expression.startswith('{') else []
                self.assertEqual(self.evaluate(prefix + 'let result=' + expression + ';[result,raw.reads]',
                                               step_limit=3000000, timeout=30), [expected, 10001])

    def test_input_budget_cannot_be_evaded_with_false_guard(self):
        for expression in ('[x for x in cursor if reject(x)]',
                           '{"same":x for x in cursor if reject(x)}',
                           'collect((x for x in cursor if reject(x)))',
                           'await acollect(async (x for x in cursor if reject(x)))'):
            ev = Evaluator(step_limit=3000000, timeout=30)
            with self.subTest(expression=expression), self.assertRaises(ResourceLimit):
                ev.run(SYNC + '''let cursor=iter(raw);raw.guards=0;
fn reject(x){raw.guards=raw.guards+1;false}''' + expression)
            raw = next(h.target for h in ev.suspension.iteration.handles if h.target is not None)
            self.assertEqual(raw.fields['reads'].value, 10001)
            self.assertEqual(raw.fields['guards'].value, 10000)
            self.assertEqual(raw.fields['closes'].value, 0)
            self.assertTrue(all(not h.busy for h in ev.suspension.iteration.handles))
            self.assertEqual(ev.frames, [])
            self.assertIsNone(ev.suspension.pool)

    def test_eager_entry_budget_counts_duplicate_record_keys(self):
        for expression in ('[x+y for x in range(100) for y in range(101)]',
                           '{"same":x+y for x in range(100) for y in range(101)}'):
            with self.subTest(expression=expression), self.assertRaises(ResourceLimit):
                self.evaluate(expression, step_limit=2000000, timeout=30)

    def test_private_native_cursors_do_not_consume_persistent_handle_quota(self):
        expressions = ('[x for x in []]', '{x:x for x in ""}',
                       '[x for x in [1]]', '{x:x for x in "a"}')
        for expression in expressions:
            ev = Evaluator(step_limit=4000000, timeout=30)
            with self.subTest(expression=expression):
                result = ev.run('var i=0;while i<10001{' + expression + ';i=i+1};i')
                self.assertEqual(result, 10001)
                self.assertEqual(ev.suspension.iteration.handles, [])
        ev = Evaluator(step_limit=3000000, timeout=30)
        self.assertEqual(ev.run('length(collect((x for x in range(10000) for y in [0])))'), 10000)
        self.assertEqual(ev.suspension.iteration.handles, [])

    def test_foreign_source_and_stale_wrapper_cannot_cross_execution_epochs(self):
        for factory in ('iter([1])', 'fn* values(){yield 1}values()'):
            foreign = Evaluator().run(factory)
            for expression in ('[x for x in input]', '{"key":x for x in input}',
                               'collect((x for x in input))', 'await acollect(async (x for x in input))'):
                with self.subTest(factory=factory, expression=expression), self.assertRaises(LanguageError):
                    self.evaluate(expression, bindings={'input': foreign})
        for setup, resume in [('let comp=(x for x in [1])', 'next(comp)'),
                              ('let comp=async (x for x in [1])', 'await anext(comp)')]:
            session = Session(); session.evaluate(setup)
            with self.subTest(resume=resume), self.assertRaises(LanguageError):
                session.evaluate(resume)

    def test_hard_cancel_drains_inflight_head_without_closing_borrowed_source(self):
        for expression in ('[tool("wait",x) for x in cursor]',
                           'collect((tool("wait",x) for x in cursor))',
                           'await acollect(async (tool("wait",x) for x in cursor))'):
            cancelled, entered, exited = threading.Event(), threading.Event(), threading.Event()
            failures = []
            def effect(kind, payload, cancel):
                entered.set()
                try:
                    if not cancel.wait(3): raise AssertionError('effect cancellation was not delivered')
                    return 1
                finally: exited.set()
            ev = Evaluator(tool_handler=lambda *args: None, effect_handler=effect,
                           cancelled=cancelled.is_set)
            def run():
                try: ev.run(SYNC + 'let cursor=iter(raw);async fn consume(){' + expression + '};await consume()')
                except BaseException as error: failures.append(error)
            worker = threading.Thread(target=run); worker.start()
            try:
                self.assertTrue(entered.wait(3), expression)
            finally:
                cancelled.set(); worker.join(5)
            self.assertFalse(worker.is_alive()); self.assertTrue(exited.is_set())
            self.assertEqual(len(failures), 1); self.assertIsInstance(failures[0], ResourceLimit)
            raw = next(h.target for h in ev.suspension.iteration.handles if h.target is not None)
            self.assertEqual(raw.fields['closes'].value, 0)
            self.assertTrue(all(not h.busy for h in ev.suspension.iteration.handles))
            self.assertIsNone(ev.suspension.pool); self.assertEqual(ev.frames, [])


if __name__ == '__main__': unittest.main()
