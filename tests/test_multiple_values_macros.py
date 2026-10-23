import unittest
from chaos.language import Evaluator,LanguageError,ResourceLimit
from chaos.macros import Expander
from chaos.multiple_values import Completion


class MultipleValueMacroTests(unittest.TestCase):
    def evaluate(self,body):return Evaluator().run('macro m(){'+body+'};m!()')

    def test_pure_zero_null_primary_and_list(self):
        self.assertEqual(self.evaluate('syntax_literal([multiple_value_list(fn(){return_values()}),multiple_value_list(fn(){return_values(null)}),return_values(1,2),multiple_value_list(fn(){values_list([3,4])})])'),
          [[],[None],1,[3,4]])

    def test_pure_call_bind_prog1_nth_and_pipeline(self):
        self.assertEqual(self.evaluate('''syntax_literal([
          multiple_value_list(fn(){multiple_value_call(return_values,fn(){return_values(1,2)},fn(){return_values()})}),
          multiple_value_bind(3,fn(){return_values(1,2)},fn(a,b,c){[a,b,c]}),
          multiple_value_list(fn(){multiple_value_prog1(fn(){return_values(3,4)},fn(){return_values(9)})}),
          nth_value(1,fn(){return_values(5,6)}),
          multiple_value_list(fn(){7 |> fn(x){return_values(x,8)}})])'''),
          [[1,2],[1,2,None],[3,4],6,[7,8]])

    def test_pure_function_return_contract_and_secondary_syntax(self):
        self.assertEqual(self.evaluate('fn f()->Int{if true{return return_values(1,2)}else{return_values()}};syntax_literal(multiple_value_list(f))'),[1,2])
        self.assertEqual(self.evaluate('return_values(quote{42},quote{99})'),42)
        with self.assertRaises(LanguageError):self.evaluate('fn f()->String{return_values(1,"secondary")};syntax_literal(f())')

    def test_pure_callbacks_cannot_obtain_runtime_effects(self):
        with self.assertRaises(LanguageError) as error:self.evaluate('syntax_literal(multiple_value_list(fn(){tool("secret",null)}))')
        self.assertEqual(error.exception.code,'macro_error')

    def test_phase_owned_completion_and_depth_budget(self):
        first=Expander({});second=Expander({})
        with self.assertRaises(LanguageError):second.value_budget(Completion(first,(1,)))
        with self.assertRaises(ResourceLimit):self.evaluate('fn f(){multiple_value_list(f)};syntax_literal(f())')

