"""Class matching is a bounded authority projection, never member execution."""
import unittest

from chaos.language import Cell, Env, Evaluator, LanguageError, Native, Parser, ResourceLimit
from chaos.objects import ChaosClass, ChaosInstance, pattern_fields
from chaos.tuple_values import TupleValue


class ClassPatternBoundaryTests(unittest.TestCase):
    def fixture(self, fields=None, capabilities=None):
        evaluator = Evaluator(step_limit=200000)
        klass = ChaosClass('C', (), evaluator, building=False)
        klass.mro = (klass,)
        value = ChaosInstance(klass, evaluator, capabilities=capabilities)
        value.fields = {key: Cell(item, 'Any') for key,item in (fields or {}).items()}
        return evaluator, klass, value

    def test_container_snapshot_preserves_dag_aliases_without_mutating_source(self):
        shared = [42]
        evaluator, klass, value = self.fixture({'a': shared, 'b': {'same': shared}, 't': TupleValue([shared])})
        result = pattern_fields(value, klass, evaluator, (), True)
        self.assertIs(result['a'], result['b']['same'])
        self.assertIs(result['a'], result['t'][0])
        self.assertIsNot(result['a'], shared)
        result['a'].append(9)
        self.assertEqual(shared, [42])

    def test_cycles_and_raw_host_tuple_fail_without_hanging(self):
        cycle = []; cycle.append(cycle)
        for item in (cycle, (42,), Parser('42').program()):
            evaluator, klass, value = self.fixture({'item': item})
            with self.subTest(kind=type(item)), self.assertRaises(LanguageError):
                pattern_fields(value, klass, evaluator, ['item'])

    def test_foreign_callable_and_instance_are_rejected_recursively(self):
        foreign = Evaluator()
        foreign_class = ChaosClass('Other', (), foreign, building=False)
        foreign_class.mro = (foreign_class,)
        for item in (Native('foreign', lambda: 42, foreign), ChaosInstance(foreign_class, foreign)):
            evaluator, klass, value = self.fixture({'nested': [{'tuple': TupleValue([item])}]})
            with self.subTest(kind=type(item)), self.assertRaises(LanguageError) as caught:
                pattern_fields(value, klass, evaluator, ['nested'])
            self.assertEqual(caught.exception.code, 'ownership_error')

    def test_unselected_and_ignored_rest_do_not_inspect_foreign_fields(self):
        evaluator, klass, value = self.fixture({'good': 42, 'bad': Native('foreign', lambda: 0, Evaluator())})
        evaluator.env.bind('C', klass)
        for text in ('C::{good:n}', 'C::{good:n,.._}'):
            pattern = Parser('let '+text+'=null').program()[1][0][1]
            bound = {}
            self.assertTrue(evaluator.pattern_match(pattern, value, evaluator.env, bound))
            self.assertEqual(bound, {'n': 42})
        with self.assertRaises(LanguageError): pattern_fields(value, klass, evaluator, ['good'], True)

    def test_exact_native_proxy_retains_empty_receiver_ceiling_after_escape(self):
        evaluator, klass, value = self.fixture(capabilities=frozenset())
        callback = Native('effect', lambda: evaluator.invoke(evaluator.env.get('tool'), ['blocked', 42]), evaluator)
        value.fields['callback'] = Cell({'nested': TupleValue([callback])}, 'Any')
        proxy = pattern_fields(value, klass, evaluator, ['callback'])['callback']['nested'][0]
        self.assertIsNot(proxy, callback)
        self.assertEqual(proxy.capabilities, frozenset())
        evaluator.env.bind('projected', proxy)
        self.assertEqual(evaluator.run('try{projected()}catch e{e.code}'), 'capability_error')
        self.assertIsNone(callback.capabilities)

    def test_non_whitelisted_callable_subclass_cannot_escape_capabilities(self):
        class ExternalNative(Native): pass
        evaluator, klass, value = self.fixture(capabilities=frozenset())
        callback = ExternalNative('external', lambda: 42, evaluator)
        value.fields['callback'] = Cell(callback, 'Any')
        with self.assertRaises(LanguageError) as caught:
            pattern_fields(value, klass, evaluator, ['callback'])
        self.assertEqual(caught.exception.code, 'capability_error')
        self.assertIsNone(callback.capabilities)

    def test_host_container_subclasses_cannot_run_hooks_or_bypass_projection(self):
        hooks = []
        class ForeignList(list):
            def __iter__(self):
                hooks.append('iter'); return super().__iter__()
        class ForeignRecord(dict):
            def items(self):
                hooks.append('items'); return super().items()
        evaluator, klass, value = self.fixture(capabilities=frozenset())
        callback = Native('external', lambda: 42, evaluator)
        for item in (ForeignList([callback]), ForeignRecord(callback=callback)):
            value.fields['item'] = Cell(item, 'Any')
            with self.subTest(kind=type(item)), self.assertRaises(LanguageError) as caught:
                pattern_fields(value, klass, evaluator, ['item'])
            self.assertEqual(caught.exception.code, 'value_error')
        self.assertEqual(hooks, [])

    def test_host_scalar_subclasses_are_rejected_without_hooks(self):
        calls = []
        class ForeignString(str):
            def encode(self, *args, **kwargs):
                calls.append('encode'); return super().encode(*args, **kwargs)
        class ForeignInt(int):
            def bit_length(self):
                calls.append('bit_length'); return super().bit_length()
        class ForeignFloat(float):
            def __float__(self):
                calls.append('float'); return super().__float__()
        for item in (ForeignString('bad'), ForeignInt(42), ForeignFloat(42)):
            evaluator, klass, value = self.fixture({'text': item})
            with self.subTest(kind=type(item)), self.assertRaises(LanguageError) as caught:
                pattern_fields(value, klass, evaluator, ['text'])
            self.assertEqual(caught.exception.code, 'value_error')
        self.assertEqual(calls, [])

    def test_descriptor_class_mro_checks_are_owned_and_budgeted(self):
        from chaos.objects import Member
        evaluator, klass, value = self.fixture({'field': 42})
        chain = tuple(ChaosClass('D'+str(i), (), evaluator, building=False) for i in range(20))
        chain[0].mro = chain
        candidate = ChaosInstance(chain[0], evaluator)
        klass.members['field'] = Member(candidate)
        evaluator.step_limit = 10
        with self.assertRaises(ResourceLimit): pattern_fields(value, klass, evaluator, ['field'])
        evaluator.step_limit = 200000
        candidate.klass = ChaosClass('Foreign', (), Evaluator(), building=False)
        with self.assertRaises(LanguageError) as caught: pattern_fields(value, klass, evaluator, ['field'])
        self.assertEqual(caught.exception.code, 'ownership_error')

    def test_opaque_live_value_cannot_be_narrowed_or_cloned(self):
        evaluator, klass, value = self.fixture(capabilities=frozenset())
        child = ChaosInstance(klass, evaluator)
        for item in (klass, child):
            value.fields['item'] = Cell(item, 'Any')
            with self.subTest(kind=type(item)), self.assertRaises(LanguageError) as caught:
                pattern_fields(value, klass, evaluator, ['item'])
            self.assertEqual(caught.exception.code, 'capability_error')
            self.assertIsNone(item.capabilities)
        child.capabilities = frozenset()
        value.fields['item'] = Cell(child, 'Any')
        self.assertIs(pattern_fields(value, klass, evaluator, ['item'])['item'], child)

    def test_building_or_foreign_class_is_an_error_even_for_noninstance(self):
        evaluator, klass, value = self.fixture()
        klass.building = True
        with self.assertRaises(LanguageError): pattern_fields(1, klass, evaluator, [])
        foreign = ChaosClass('Foreign', (), Evaluator(), building=False)
        with self.assertRaises(LanguageError) as caught: pattern_fields(1, foreign, evaluator, [])
        self.assertEqual(caught.exception.code, 'ownership_error')

    def test_every_projection_checkpoint_precedes_atomic_assignment(self):
        pattern = Parser('C::{first:a,second:b}=null').program()[1][0][1]
        baseline, klass, value = self.fixture({'first': 20, 'second': 22})
        env = Env(); env.bind('C', klass); env.bind('a', Cell(1, 'Int')); env.bind('b', Cell(2, 'Int'))
        baseline.assign_pattern(pattern, value, env)
        total = baseline.steps
        self.assertGreater(total, 10)
        for limit in range(total):
            evaluator, klass, value = self.fixture({'first': 20, 'second': 22})
            evaluator.step_limit = limit
            env = Env(); env.bind('C', klass); env.bind('a', Cell(1, 'Int')); env.bind('b', Cell(2, 'Int'))
            with self.subTest(limit=limit), self.assertRaises(ResourceLimit):
                evaluator.assign_pattern(pattern, value, env)
            self.assertEqual([env.get('a'), env.get('b')], [1, 2])

    def test_cancellation_during_projection_leaves_targets_unchanged(self):
        evaluator, klass, value = self.fixture({'first': [20], 'second': 22})
        env = Env(); env.bind('C', klass); env.bind('a', Cell([1], 'List')); env.bind('b', Cell(2, 'Int'))
        pattern = Parser('C::{first:a,second:b}=null').program()[1][0][1]
        checks = [0]
        def cancelled():
            checks[0] += 1
            return checks[0] == 8
        evaluator.cancelled = cancelled
        with self.assertRaises(ResourceLimit): evaluator.assign_pattern(pattern, value, env)
        self.assertEqual([env.get('a'), env.get('b')], [[1], 2])

    def test_field_and_depth_limits_are_shared_with_native_resources(self):
        evaluator, klass, value = self.fixture({str(i): i for i in range(10001)})
        with self.assertRaises(ResourceLimit): pattern_fields(value, klass, evaluator, [], True)
        nested = 0
        for _ in range(102): nested = [nested]
        evaluator, klass, value = self.fixture({'item': nested})
        with self.assertRaises(ResourceLimit): pattern_fields(value, klass, evaluator, ['item'])


if __name__ == '__main__': unittest.main()
