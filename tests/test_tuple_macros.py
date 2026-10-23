"""Tuple products remain native across both macro phases and source forms."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


class TupleMacros(unittest.TestCase):
    def variants(self, source, expected, **options):
        for text in (source, Framework.format_source(source, line_width=40),
                     format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text, **options), expected)

    def test_pure_constructor_patterns_comprehension_and_literal_reification(self):
        source='''macro values(){
let (a,b,..rest)=tuple(items=[20,22,99]);
let rows=[x+1 for x in (a,b)];
syntax_literal((a+b,rows,rest,tuple(),tuple("好!")))};values!()'''
        self.variants(source, {'$tuple':[42,[21,23],{'$tuple':[99]}, {'$tuple':[]}, {'$tuple':['好','!']}]})

    def test_tuple_equality_is_strict_in_both_phases(self):
        self.variants('macro compare(){syntax_literal([(true,)==(1,), (1,)==[1], (1,)+(2,)==(1,2)])};[compare!(),(true,)==(1,)]',
                      [[False,False,True],False])

    def test_pure_function_spread_and_parameter_tuple_pattern(self):
        source='''macro answer(){fn sum(pair as (a,b):Tuple,/){a+b};
fn forward(action,value){action(value)};
syntax_literal(forward(*((fn(pair){sum(pair)}),(20,22))))};answer!()'''
        self.variants(source,42)

    def test_template_tuple_state_assignment_and_resource_pattern_are_hygienic(self):
        source='''macro work(input){quote{do{
var (a,b)=unquote(input);(a,b)=(b,a);
class R{fn __enter__(self){(20,22)}fn __exit__(self,error){null}}
with R() as (left,right){(a+b,left+right)}
}}};let a=100;[work!((19,23)),a]'''
        self.variants(source,[{'$tuple':[42,42]},100])

    def test_imported_macro_async_tuple_callback_keeps_cold_execution(self):
        files={'forms.chaos':'''macro task(action){quote{async fn(pair as (a,b):Tuple,/){(unquote(action)(a),b)}}};export{task}'''}
        source='''import "forms.chaos" as F;var seen=0;
fn double(x){seen=seen+1;x*2};let work=F.task!(double);let result=work((21,22));
let before=seen;[before,await result,seen]'''
        self.variants(source,[0,{'$tuple':[42,22]},1],files=files)

    def test_quote_composite_slice_key_has_same_order_and_shape(self):
        source='''macro access(target){quote{unquote(target)[1:5:2,3]}}
class Matrix{fn __getitem__(self,key as (row,col):Tuple){[row.start,row.stop,row.step,col]}}
access!(Matrix())'''
        self.variants(source,[1,5,2,3])

    def test_quoted_generator_and_comprehension_keep_tuple_leaf_scope(self):
        source='''macro stream(){quote{fn*(){yield ((20,22));yield ((21,21))}}}
let make=stream!();[a+b for (a,b) in make()]'''
        self.variants(source,[42,42])
        self.variants('macro outer(){quote{((x for x in (20,22)),)}};let value=outer!();collect(value[0])',
                      [20,22])

    def test_compiler_only_values_cannot_escape_inside_tuple_or_run_during_analysis(self):
        calls=[]
        bad='tool("before",null);macro bad(){syntax_literal((fn(x){x},))};bad!()'
        with self.assertRaises(LanguageError):
            Evaluator(tool_handler=lambda *args:calls.append(args)).run(bad)
        self.assertEqual(calls,[])
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            report=Framework.analyze('macro values(){syntax_literal((20,22))};values!()')
        self.assertNotIn('runtime_error',[d.get('code') for d in report['diagnostics']])


if __name__=='__main__':unittest.main()
