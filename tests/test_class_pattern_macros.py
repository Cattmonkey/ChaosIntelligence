"""Class pattern templates preserve lexical type identity and binder hygiene."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


class ClassPatternMacros(unittest.TestCase):
    def variants(self, source, expected, **options):
        for text in (source, Framework.format_source(source, line_width=40),
                     format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text, **options), expected)

    def test_explicit_alias_and_same_name_leaf_use_outer_type(self):
        self.variants('''class Box{let value=42};macro read(type,value){quote{
let C=unquote(type);do{let C::{value:C}=unquote(value);C}}};
let C=7;[read!(Box,Box()),C]''',[42,7])

    def test_parameter_pattern_alias_does_not_capture_caller(self):
        self.variants('''class Box{let value=42};macro reader(type){quote{
let C=unquote(type);fn(item as C::{value:value}){value}}};
let value=7;let read=reader!(Box);[read(Box()),value]''',[42,7])

    def test_imported_qualified_alias_template_preserves_class_identity(self):
        files={'types.chaos':'class Box{let value=42};export{Box}',
            'forms.chaos':'macro read(types,value){quote{let M=unquote(types);let M.Box::{value:answer}=unquote(value);answer}};export{read}'}
        self.variants('import "types.chaos" as T;import "forms.chaos" as F;F.read!(T,T.Box())',42,files=files)

    def test_class_assignment_template_updates_hygienic_cells(self):
        self.variants('''macro swap(){quote{class Box{let left=22;let right=20};
var left=20;var right=22;Box::{left:left,right:right}=Box();[left,right]}};
let left=7;[swap!(),left]''',[[22,20],7])

    def test_resource_pattern_template_keeps_exit_and_value(self):
        self.variants('''macro open(){quote{class Box{let value=42};class Resource{
fn __enter__(self){Box()};fn __exit__(self,error){false}};
with Resource() as Box::{value:value}{value}}};open!()''',42)

    def test_catch_and_nested_patterns_share_explicit_type_aliases(self):
        self.variants('''class Inner{let value=42};class Outer{let nested=Inner()};
macro recover(inner,outer,action){quote{let I=unquote(inner);let O=unquote(outer);
try{unquote(action)()}catch {value:O::{nested:I::{value:answer}},.._}{answer}}};
recover!(Inner,Outer,fn(){throw Outer()})''',42)

    def test_comprehension_and_generator_templates_use_same_pattern(self):
        self.variants('''macro read(consume){quote{class Box{let value=21};
fn* stream(item as Box::{value:value}){yield value};
[[value*2 for Box::{value:value} in [Box()]],unquote(consume)(stream(Box()))]}};read!(collect)''',[[42],[21]])

    def test_compile_time_rejects_runtime_projection_and_analysis_is_inert(self):
        for source in ('macro bad(){let C::{value:x}={value:42};quote{0}};bad!()',
                       'macro bad(){let f=fn(x as C::{value:y}){y};f({value:42})};bad!()'):
            with self.assertRaises(LanguageError) as caught:Framework.evaluate(source)
            self.assertEqual(caught.exception.code,'macro_error')
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed runtime')):
            result=Framework.analyze('class Box{let value:Int=42};let Box::{value:answer}=Box();answer')
        self.assertFalse(result['diagnostics'])

if __name__=='__main__':unittest.main()
