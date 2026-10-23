"""Observable completion semantics, independent of carrier representation."""
import unittest
from chaos.language import Evaluator, LanguageError
from chaos.tuple_values import TupleValue


class MultipleValueTests(unittest.TestCase):
    def run_source(self, source):
        return Evaluator(step_limit=300000).run(source)

    def test_zero_one_null_and_tuple_remain_distinct(self):
        result=self.run_source('''[multiple_value_list(fn(){return_values()}),
          multiple_value_list(fn(){return_values(null)}),
          multiple_value_list(fn(){(1,2)}),
          multiple_value_list(fn(){return_values((1,2),[3,4])})]''')
        self.assertEqual(result[:2],[[],[None]])
        self.assertIs(type(result[2][0]),TupleValue)
        self.assertEqual(list(result[2][0]),[1,2])
        self.assertEqual(result[3][1],[3,4])

    def test_ordinary_operands_consume_primary(self):
        self.assertEqual(self.run_source('''let x=return_values(1,2);var y=return_values(3,4);
          y=return_values(5,6);fn take(x,*,y){[x,y]};
          [x,y,return_values(),return_values(return_values(7,8),return_values()),
           take(return_values(9,10),y=return_values(11,12)),
           [return_values(13,14)],{x:return_values(15,16)},return_values(17,18)+1]'''),
          [1,5,None,7,[9,11],[13],{'x':15},18])

    def test_list_inverse_and_missing_nth(self):
        self.assertEqual(self.run_source('''[multiple_value_list(fn(){values_list([1,null,3])}),
          multiple_value_list(fn(){values_list([])}),nth_value(1,fn(){return_values(1,2)}),
          nth_value(8,fn(){return_values(1,2)})]'''),[[1,None,3],[],2,None])

    def test_call_concatenates_each_producer_once_in_order(self):
        self.assertEqual(self.run_source('''var seen=[];
          let result=multiple_value_list(fn(){multiple_value_call(return_values,
            fn(){seen=append(seen,1);return_values(1,2)},
            fn(){seen=append(seen,2);return_values()},
            fn(){seen=append(seen,3);return_values(null,4)})});[result,seen]'''),
          [[1,2,None,4],[1,2,3]])

    def test_bind_pads_discards_and_uses_normal_named_parameters(self):
        self.assertEqual(self.run_source('''let outer=9;
          [multiple_value_bind(3,fn(){return_values(1,2)},fn(a,b,c){[a,b,c,outer]}),
           multiple_value_bind(1,fn(){return_values(3,4)},fn(a){a}),
           multiple_value_list(fn(){multiple_value_bind(0,fn(){return_values(5)},fn(){return_values()})})]'''),
          [[1,2,None,9],3,[]])

    def test_prog1_preserves_all_first_and_discards_afters(self):
        self.assertEqual(self.run_source('''var seen=[];
          let result=multiple_value_list(fn(){multiple_value_prog1(
            fn(){seen=append(seen,1);return_values(1,2)},
            fn(){seen=append(seen,2);return_values(9,8)},
            fn(){seen=append(seen,3);return_values()})});[result,seen]'''),[[1,2],[1,2,3]])

    def test_typed_tail_calls_and_empty_contract_keep_full_completion(self):
        self.assertEqual(self.run_source('''fn f(n)->Int{if n==0{return_values(7,"s")}else{return f(n-1)}};
          fn empty()->Null{return_values()};
          [multiple_value_list(fn(){f(2000)}),multiple_value_list(empty)]'''),[[7,'s'],[]])
        with self.assertRaises(LanguageError):self.run_source('fn f()->String{return_values(1,"validsecondary")};f()')

    def test_branches_pipeline_boolean_and_continuations(self):
        self.assertEqual(self.run_source('''fn f()->Bool{return_values(true,42)};
          [multiple_value_list(fn(){if true{f()}else{return_values()}}),
           multiple_value_list(fn(){match 1{1=>return_values(1,2)}}),
           multiple_value_list(fn(){1 |> fn(x){return_values(x,2)}}),
           multiple_value_list(fn(){true&&f()}),
           multiple_value_list(fn(){compose(fn(x){return_values(x+1,9)},fn(){return_values(1,2)})()})]'''),
          [[True,42],[1,2],[1,2],[True],[2,9]])

    def test_first_class_apply_partial_and_legacy_record_values(self):
        self.assertEqual(self.run_source('''let emit=return_values;
          [multiple_value_list(partial(emit,1,2)),
           multiple_value_list(fn(){apply(emit,[3,4])}),values({a:1,b:2})]'''),[[1,2],[3,4],[1,2]])

    def test_bad_receivers_are_eager_argument_errors(self):
        for source in ('values_list((1,2))','multiple_value_list(42)','nth_value(-1,fn(){1})',
                       'nth_value(true,fn(){1})','multiple_value_bind(-1,fn(){1},fn(x){x})'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_source(source)

