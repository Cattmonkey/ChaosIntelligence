"""Closed Set values and hygienic source generation across macro phases."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program

class SetMacros(unittest.TestCase):
    def variants(self, source, expected, **options):
        for text in (source, Framework.format_source(source,line_width=40), format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text,**options),expected)

    def test_pure_literal_constructor_spread_and_nested_keys(self):
        source='''macro values(){let value={1,true,1.0,1,(20,22),{2,3},{3,2}};
syntax_literal((value,set_of("aba"),{*(),*[],*value,42}))};values!()'''
        items=[1,True,1.0,{'$tuple':[20,22]},{'$set':[2,3]}]
        self.variants(source,{'$tuple':[{'$set':items},{'$set':['a','b']},{'$set':items+[42]}]})

    def test_empty_reification_is_native_under_constructor_shadow(self):
        self.variants('macro empty(){syntax_literal(set_of())};let set_of=fn(){99};[empty!(),type_of(empty!())]',[{'$set':[]},'Set'])

    def test_equality_and_algebra_match_runtime(self):
        source='''macro compute(){let a={1,2};let b={2,3};syntax_literal([a=={2,1},a|b,a&b,a-b,a^b,a< (a|b),set_is_disjoint(a,{3}),set_add(a,3),set_discard(a,2)])};compute!()'''
        self.variants(source,[True,{'$set':[1,2,3]},{'$set':[2]},{'$set':[1]},{'$set':[1,3]},True,True,{'$set':[1,2,3]},{'$set':[1]}])
        self.variants('macro strict(){syntax_literal({true,1,1.0}=={1,true,1.0})};[strict!(),{true}!={1},{{1,2}}=={{2,1}}]',[True,True,True])

    def test_pure_set_comprehension_and_function_spread(self):
        self.variants('macro answer(){fn sum(a,b){a+b};let values={x for x in (20,22,20)};syntax_literal(sum(*tuple(values)))};answer!()',42)
        self.variants('macro unique(){syntax_literal({a+b for (a,b) in [(20,22),(21,21)]})};unique!()',{'$set':[42]})

    def test_quote_comprehension_bindings_are_hygienic(self):
        source='''macro unique(values){quote{{x+1 for x in unquote(values) if x>0}}};let x=100;[unique!([41,41]),x]'''
        self.variants(source,[{'$set':[42]},100])

    def test_imported_macro_async_comprehension_keeps_real_suspension(self):
        files={'forms.chaos':'macro distinct(source){quote{{x async for x in unquote(source)}}};export{distinct}'}
        source='''import "forms.chaos" as F;async fn* values(){yield 42;await sleep(0);yield 42};F.distinct!(values())'''
        self.variants(source,{'$set':[42]},files=files)

    def test_invalid_keys_and_compile_time_iterators_fail_before_effects(self):
        for expression in ('{[]}','{fn(x){x}}','set_of(fn(x){x})','{*(fn(x){x})}'):
            calls=[]
            with self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run('tool("before",null);macro bad(){syntax_literal('+expression+')};bad!()')
            self.assertEqual(calls,[])

    def test_analysis_does_not_execute_set_or_macro_runtime(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            report=Framework.analyze('macro values(){syntax_literal({20,22})};let result=values!();set_add(result,42)')
        self.assertNotIn('runtime_error',[x.get('code') for x in report['diagnostics']])

if __name__=='__main__':unittest.main()
