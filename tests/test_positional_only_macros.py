"""Private parameter names remain lexical under both macro phases."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


class PositionalOnlyMacros(unittest.TestCase):
    def variants(self, source, expected, **options):
        for text in (source, Framework.format_source(source, line_width=40),
                     format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text, **options), expected)

    def test_template_slot_alias_does_not_capture_keyword_data(self):
        source='''macro maker(){quote{fn(value,/,**options){[value,options]}}}
let value=100;let f=maker!();[f(20,value=22),value]'''
        self.variants(source, [[20, {'value':22}], 100])

    def test_pure_macro_functions_use_same_positional_only_binding(self):
        source='''macro total(){
fn add(value,/,**options){value+options.value}
syntax_literal(add(20,value=22))};total!()'''
        self.variants(source, 42)

    def test_pure_phase_rejects_keyword_substitution_before_effects(self):
        calls=[]
        source='''tool("before",null);macro bad(){
fn identity(value,/){value};syntax_literal(identity(value=42))};bad!()'''
        with self.assertRaises(LanguageError):
            Evaluator(tool_handler=lambda *args:calls.append(args)).run(source)
        self.assertEqual(calls, [])

    def test_template_patterns_and_defaults_keep_outer_identity(self):
        source='''macro maker(fallback){quote{
fn(value as [left,right]:List=unquote(fallback),/,*,offset:Int=0,**options){
[left+right+offset,options]}}}
let left=100;let f=maker!([20,22]);[f(value="metadata"),f([1,2],offset=39),left]'''
        self.variants(source, [[42, {'value':'metadata'}], [42, {}], 100])

    def test_imported_macro_default_then_cold_async_call(self):
        files={'forms.chaos':'''macro wrap(action){quote{
async fn(value,/,*,offset=0,**options){[unquote(action)(value)+offset,options]}}};export{wrap}'''}
        source='''import "forms.chaos" as F;fn double(x){x*2};let f=F.wrap!(double);
let task=f(20,offset=2,value=99);[task_info(task).state,await task]'''
        self.variants(source, ['created', [42, {'value':99}]], files=files)

    def test_generated_methods_keep_receiver_and_keyword_rest_distinct(self):
        source='''macro component(){quote{
class C{fn answer(self,value,/,**options){[value,options]}};C()}}
let self=100;let object=component!();[object.answer(42,self="data",value=1),self]'''
        self.variants(source, [[42, {'self':'data','value':1}], 100])

    def test_generator_templates_preserve_cold_binding_and_private_labels(self):
        source='''macro stream(){quote{fn*(value,/,**options){yield ([value,options])}}}
let items=stream!();collect(items(42,value="label"))'''
        self.variants(source, [[42, {'value':'label'}]])

    def test_analysis_does_not_run_macro_or_function_to_infer_signature(self):
        source='macro make(){quote{fn(value,/,**options){[value,options]}}};make!()(42,value=1)'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            report=Framework.analyze(source)
        self.assertNotIn('runtime_error', [d.get('code') for d in report['diagnostics']])


if __name__=='__main__':unittest.main()
