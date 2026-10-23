"""No host protocols, hash collision escapes, live authority or hidden budget growth."""
import unittest
from unittest.mock import patch
from chaos.language import Evaluator, LanguageError, Native, ResourceLimit, serializable
from chaos.set_values import SetValue, SetBuilder, set_binary, set_contains, set_equal
from chaos.tuple_values import TupleValue


class SetBoundaryTests(unittest.TestCase):
    def test_all_host_subclasses_reject_before_hooks(self):
        hooks=[]
        class Int(int):
            def bit_length(self):hooks.append('int');return 1
        class String(str):
            def encode(self,*a,**k):hooks.append('str');return b'x'
        class Float(float):
            def __float__(self):hooks.append('float');return 1.0
        class List(list):
            def __iter__(self):hooks.append('list');return iter(())
        class Record(dict):
            def items(self):hooks.append('dict');return ()
        for value in (Int(1),String('x'),Float(1),List(),Record()):
            with self.subTest(kind=type(value)),self.assertRaises(LanguageError):SetValue([value])
        with self.assertRaises(LanguageError):SetValue(List())
        self.assertEqual(hooks,[])

    def test_native_source_recognition_rejects_host_set_subclass_without_hooks(self):
        calls=[]
        class HostSet(SetValue):
            def __iter__(self):calls.append('iter');return iter((42,))
            def __len__(self):calls.append('len');return 1
        value=HostSet([42]);evaluator=Evaluator();evaluator.env.bind('host',value)
        with self.assertRaises(LanguageError):evaluator.run('set_of(host)')
        self.assertEqual(calls,[])

    def test_invalid_unicode_finite_numbers_and_integer_limits(self):
        for value in ('\ud800',float('nan'),float('inf'),float('-inf')):
            with self.subTest(kind=type(value)),self.assertRaises(LanguageError):SetValue([value])
        with self.assertRaises(ResourceLimit):SetValue([1<<14001])

    def test_live_values_and_inert_records_are_not_keys(self):
        # Nominal Instances now have protocol/identity keys; other live handles remain rejected.
        self.assertIs(type(Evaluator().run('class C{};{C()}')),SetValue)
        for source in ('{fn(){42}}','async fn f(){42};{f()}','{channel()}','{{"$set":[42]}}'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)
        with self.assertRaises(LanguageError):SetValue([Native('foreign',lambda:42,Evaluator())])

    def test_digest_collision_never_defines_equality(self):
        with patch('chaos.set_values._fingerprint',lambda *args:b'fixed'):
            value=SetValue([True,1,1.0,'1',TupleValue([1,2]),TupleValue([2,1])])
            self.assertEqual(len(value),6)
            self.assertFalse(set_contains(value,2))
            self.assertTrue(set_contains(value,TupleValue([1,2])))
            self.assertTrue(set_equal(SetValue([SetValue([1,2])]),SetValue([SetValue([2,1])])) )
            self.assertFalse(set_equal(SetValue([SetValue([1,2])]),SetValue([SetValue([2,3])])) )

    def test_collision_comparison_work_is_budgeted(self):
        ticks=[]
        with patch('chaos.set_values._fingerprint',lambda *args:b'fixed'):
            builder=SetBuilder(lambda:ticks.append(None))
            with self.assertRaises(ResourceLimit):
                for value in range(1000):builder.add(value)
        self.assertLessEqual(len(ticks),20001)

    def test_large_colliding_host_integers_do_not_use_numeric_hash_buckets(self):
        period=(1<<61)-1
        values=[n*period for n in range(1000)]
        builder=SetBuilder()
        for value in values:builder.add(value)
        self.assertEqual(len(builder.finish()),1000)
        self.assertLess(builder.budget.nodes,3000)

    def test_shared_tuple_dag_hashing_is_linear_and_depth_bounded(self):
        value=TupleValue([1])
        for _ in range(60):value=TupleValue([value,value])
        builder=SetBuilder();builder.add(value)
        self.assertLess(builder.budget.nodes,200)
        self.assertEqual(len(builder.finish()),1)
        for _ in range(42):value=TupleValue([value])
        with self.assertRaises(ResourceLimit):SetValue([value])

    def test_cycle_is_rejected_without_recursive_host_hashing(self):
        value=TupleValue([]);object.__setattr__(value,'items',(value,))
        with self.assertRaises(LanguageError):SetValue([value])

    def test_algebra_cannot_accumulate_strings_beyond_shared_domain_budget(self):
        left=SetValue(['a'*150000]);right=SetValue(['b'*150000])
        with self.assertRaises(ResourceLimit):set_binary('|',left,right)
        evaluator=Evaluator();evaluator.env.bind('left',left);evaluator.env.bind('right',right)
        with self.assertRaises(ResourceLimit):evaluator.run('let combined=left|right;true')
        with self.assertRaises(ResourceLimit):SetValue(['a'*150000,'b'*150000])

    def test_strict_json_rejects_native_set_and_explicit_projection_is_inert(self):
        from chaos.extensions import json_copy
        value=SetValue([1,2])
        with self.assertRaises((ValueError,TypeError)):json_copy(value)
        self.assertEqual(json_copy(serializable(value)),{'$set':[1,2]})
        self.assertEqual(Evaluator().host_value(value),{'$set':[1,2]})

    def test_channel_payload_accounting_includes_set_and_nested_tuple(self):
        from chaos.channels import validate_payload
        evaluator=Evaluator()
        value=SetValue([TupleValue(['abc',42]),'long'])
        self.assertEqual(validate_payload(evaluator,value),(5,7))

    def test_cancellation_in_key_projection_prevents_new_value(self):
        evaluator=Evaluator(cancelled=lambda:True)
        builder=SetBuilder(evaluator.tick)
        with self.assertRaises(ResourceLimit):builder.add(42)
        self.assertEqual(len(builder.finish()),0)


if __name__=='__main__':unittest.main()
