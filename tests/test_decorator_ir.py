"""Decorators are additive IR wrappers and survive canonical and source formatting."""
import copy
import unittest
from chaos.language import Evaluator,LanguageError
from chaos.program import parse_program,format_program,templates,children
from chaos.ide_language import format_source

SOURCE="""fn identity(value){value}
@identity
class C {
 var raw:Int=40;
 @staticmethod
 fn plus(x:Int=2)->Int{x+1}
 @classmethod
 fn make(cls){cls()}
 @property
 fn value(self)->Int{self.raw}
 @value.setter
 fn value(self,value:Int){self.raw=value;null}
}
let item=C.make();item.value=42;[item.value,C.plus()]"""

class DecoratorIR(unittest.TestCase):
    def test_roundtrip_and_source_format_retain_wrappers_and_comments(self):
        program=parse_program(SOURCE)
        canonical=format_program(program)
        self.assertEqual(parse_program(canonical)['semanticHash'],program['semanticHash'])
        self.assertEqual(Evaluator().run(canonical),[42,3])
        wrapper=program['root']['statements'][1]
        self.assertEqual([slot for slot,_ in children(wrapper)],['decorators.0','declaration'])
        formatted=format_source('// keep decorator comment\n'+SOURCE)
        self.assertIn('// keep decorator comment',formatted)
        self.assertEqual(Evaluator().run(formatted),[42,3])
        self.assertEqual(format_source(formatted),formatted)

    def test_structural_default_edit_preserves_descriptor_and_legacy_shapes(self):
        program=parse_program(SOURCE)
        cls=program['root']['statements'][1]['declaration']
        member=next(n for n in cls['body']['statements'] if n['kind']=='decorated' and n['declaration']['name']=='plus')
        member['declaration']['function']['parameters'][0]['default']['value']=3
        self.assertEqual(Evaluator().run(format_program(program)),[42,4])
        self.assertEqual(templates()['decorated']['kind'],'decorated')
        self.assertEqual(set(parse_program('fn old(x){x}')['root']['statements'][0])-{'id','span'},{'kind','name','function'})

    def test_invalid_wrapper_and_class_declaration_context_rejected(self):
        program=parse_program(SOURCE);wrapper=program['root']['statements'][1]
        wrapper['decorators']=[]
        with self.assertRaises(LanguageError):format_program(program)
        program=parse_program(SOURCE);program['root']['statements'][1]['declaration']['body']['statements'][1]['declaration']=templates()['class']
        with self.assertRaises(LanguageError):format_program(program)

    def test_multiline_decorator_expression_and_comment_boundaries(self):
        source='@(fn(value){\n // decorator body\n value\n})\n// before declaration\nfn f(x=42){x}\nf()'
        formatted=format_source(source)
        self.assertIn('// decorator body',formatted);self.assertIn('// before declaration',formatted)
        self.assertEqual(Evaluator().run(formatted),42)
