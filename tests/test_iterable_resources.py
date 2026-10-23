"""Adversarial consumption, exclusivity, ownership and finite input budgets."""
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.session import Session


CURSOR = '''class Cursor{var reads=0;var callbacks=0;var closes=0;
fn __iter__(self){self}fn __next__(self){self.reads=self.reads+1;
{done:false,value:self.reads}}fn __close__(self){self.closes=self.closes+1;null}}
let raw=Cursor();raw.callbacks=0;raw.closes=0;let cursor=iter(raw);'''


class IterableResourceTests(unittest.TestCase):
    def evaluate(self, source, **kwargs):
        return Evaluator(**kwargs).run(source)

    def test_callbacks_cannot_consume_raw_or_wrapped_aliases(self):
        for alias in ('raw', 'cursor'):
            callbacks = {
                'map': 'map(fn(x){next(' + alias + ')},cursor)',
                'filter': 'filter(fn(x){next(' + alias + ');true},cursor)',
                'fold': 'fold(fn(a,x){next(' + alias + ');a+x},0,cursor)',
                'any': 'any(fn(x){next(' + alias + ');true},cursor)',
                'all': 'all(fn(x){next(' + alias + ');false},cursor)',
            }
            for name, expression in callbacks.items():
                with self.subTest(alias=alias, consumer=name):
                    result = self.evaluate(CURSOR + 'let code=try{' + expression +
                        '}catch e{e.code};[code,raw.reads,raw.closes,next(cursor),raw.reads]')
                    self.assertEqual(result, ['argument_error', 1, 0,
                                             {'done': False, 'value': 2}, 2])

    def test_short_circuit_never_probes_past_decisive_predicate(self):
        for expression, answer in [('any(fn(x){x==2},cursor)', True),
                                   ('all(fn(x){x<2},cursor)', False)]:
            with self.subTest(expression=expression):
                self.assertEqual(self.evaluate(CURSOR + 'let answer=' + expression +
                    ';[answer,raw.reads,raw.closes,next(cursor),raw.reads]'),
                    [answer, 2, 0, {'done': False, 'value': 3}, 3])

    def test_callback_and_bool_contract_failure_release_borrow(self):
        for expression in ('map(fn(x){throw "failed"},cursor)',
                           'fold(fn(a,x){throw "failed"},0,cursor)',
                           'filter(fn(x){1},cursor)', 'any(fn(x){1},cursor)',
                           'all(fn(x){1},cursor)'):
            with self.subTest(expression=expression):
                result = self.evaluate(CURSOR + 'try{' + expression +
                    '}catch e{null};[raw.reads,raw.closes,next(cursor)]')
                self.assertEqual(result, [1, 0, {'done': False, 'value': 2}])

    def test_finite_ten_thousand_input_can_produce_empty_output(self):
        source = '''class Source{var reads=0;fn __iter__(self){self}
fn __next__(self){self.reads=self.reads+1;{done:self.reads>10000,value:self.reads}}}
let source=Source();let out=filter(fn(x){false},source);[out,source.reads]'''
        self.assertEqual(self.evaluate(source, step_limit=2000000, timeout=30), [[], 10001])

    def test_every_consumer_limits_input_even_if_output_is_empty(self):
        expressions = {
            'map': 'map(fn(x){raw.callbacks=raw.callbacks+1;x},cursor)',
            'filter': 'filter(fn(x){raw.callbacks=raw.callbacks+1;false},cursor)',
            'fold': 'fold(fn(a,x){raw.callbacks=raw.callbacks+1;a},null,cursor)',
            'any': 'any(fn(x){raw.callbacks=raw.callbacks+1;false},cursor)',
            'all': 'all(fn(x){raw.callbacks=raw.callbacks+1;true},cursor)',
            'apply': 'apply(fn(*args){raw.callbacks=raw.callbacks+1;null},cursor)',
            'spread': '(fn(*args){raw.callbacks=raw.callbacks+1;null})(*cursor)',
        }
        for name, expression in expressions.items():
            with self.subTest(consumer=name):
                evaluator = Evaluator(step_limit=3000000, timeout=30)
                with self.assertRaises(ResourceLimit):
                    evaluator.run(CURSOR + expression)
                handles = evaluator.suspension.iteration.handles
                target = next(handle.target for handle in handles if handle.target is not None)
                self.assertEqual(target.fields['reads'].value, 10001)
                self.assertEqual(target.fields['callbacks'].value,
                                 0 if name in ('apply', 'spread') else 10000)
                self.assertEqual(target.fields['closes'].value, 0)
                self.assertTrue(all(not handle.busy for handle in handles))
                self.assertIsNone(evaluator.suspension.pool)
                self.assertEqual(evaluator.frames, [])

    def test_hard_cancellation_drains_effect_and_clears_borrowed_cursor(self):
        cancelled, entered, exited = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def effect(kind, payload, cancel):
            entered.set()
            try:
                if not cancel.wait(3):
                    raise AssertionError('effect did not receive cancellation')
                return 1
            finally:
                exited.set()
        evaluator = Evaluator(tool_handler=lambda *args: None, effect_handler=effect,
                              cancelled=cancelled.is_set)
        def run():
            try:
                evaluator.run(CURSOR + 'async fn consume(){map(fn(x){tool("wait",x)},cursor)}await consume()')
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
        finally:
            cancelled.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ResourceLimit)
        self.assertTrue(exited.is_set())
        self.assertTrue(evaluator.suspension.iteration.handles)
        self.assertTrue(all(not handle.busy for handle in evaluator.suspension.iteration.handles))
        self.assertIsNone(evaluator.suspension.pool)
        self.assertEqual(evaluator.frames, [])

    def test_combined_argument_limit_rejects_before_target(self):
        for expression in ('target(0,*values())', 'target(*values(),extra=1)',
                           'apply(target,values(),{extra:1})'):
            with self.subTest(expression=expression):
                seen = []
                source = '''fn* values(){var i=0;while i<10000{yield i;i=i+1}}
fn target(*args,**kwargs){tool("unexpected",null);null}''' + expression
                evaluator = Evaluator(tool_handler=lambda name, value: seen.append(name),
                                      step_limit=2000000, timeout=30)
                with self.assertRaises(ResourceLimit):
                    evaluator.run(source)
                self.assertEqual(seen, [])

    def test_foreign_and_stale_handles_are_rejected_by_every_consumer(self):
        consumers = ('map(fn(x){x},input)', 'filter(fn(x){true},input)',
                     'fold(fn(a,x){a},0,input)', 'any(fn(x){true},input)',
                     'all(fn(x){false},input)', 'apply(fn(*args){args},input)',
                     '(fn(*args){args})(*input)')
        foreign_values = ('iter([1])', 'fn* values(){yield 1}values()',
                          'class Source{fn __iter__(self){self}fn __next__(self){{done:true,value:1}}}Source()')
        for factory in foreign_values:
            foreign = Evaluator().run(factory)
            for source in consumers:
                with self.subTest(factory=factory, consumer=source), self.assertRaises(LanguageError):
                    Evaluator().run(source, {'input': foreign})
        for source in consumers:
            session = Session()
            session.evaluate('let input=iter([1])')
            with self.subTest(stale=source), self.assertRaises(LanguageError):
                session.evaluate(source)

    def test_async_sources_are_rejected_without_starting_them(self):
        factories = ('async fn* values(){started=started+1;yield 1}let source=values();',
                     'class Source{fn __aiter__(self){started=started+1;self}async fn __anext__(self){started=started+1;{done:false,value:1}}}let source=Source();',
                     'async fn work(){started=started+1;[1]}let source=work();')
        for factory in factories:
            for operation in ('map(fn(x){x},source)', 'filter(fn(x){true},source)',
                              'fold(fn(a,x){a},0,source)', 'any(fn(x){true},source)',
                              'all(fn(x){false},source)', 'apply(fn(*xs){xs},source)',
                              '(fn(*xs){xs})(*source)'):
                with self.subTest(factory=factory, operation=operation):
                    result = self.evaluate('var started=0;' + factory +
                        'let code=try{' + operation + '}catch e{e.code};[code,started]')
                    self.assertEqual(result, ['argument_error', 0])


if __name__ == '__main__':
    unittest.main()
