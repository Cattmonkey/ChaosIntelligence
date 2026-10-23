"""General descriptors use ordinary class, function, call and decorated IR."""
import unittest
from chaos.language import Evaluator
from chaos.program import parse_program,format_program
from chaos.ide_language import format_source

SOURCE="""class Field{fn __get__(self,obj,owner)->Int{42};fn __delete__(self,obj){null};fn __set_name__(self,owner,name){null}}class C{let value=Field()};let c=C();delattr(c,"value");c.value"""
class DescriptorIR(unittest.TestCase):
    def test_protocol_and_delete_roundtrip_preserves_existing_nodes(self):
        program=parse_program(SOURCE);canonical=format_program(program)
        self.assertEqual(parse_program(canonical)['semanticHash'],program['semanticHash'])
        call=program['root']['statements'][-2]
        self.assertEqual(call['kind'],'call');self.assertEqual(call['callee']['name'],'delattr')
        self.assertEqual(Evaluator().run(canonical),42)
        formatted=format_source('// protocol comment\n'+SOURCE)
        self.assertIn('// protocol comment',formatted);self.assertEqual(Evaluator().run(formatted),42)

    def test_structural_getter_edit_and_property_deleter_survive(self):
        program=parse_program(SOURCE)
        program['root']['statements'][0]['body']['statements'][0]['function']['body']['statements'][0]['value']=43
        self.assertEqual(Evaluator().run(format_program(program)),43)
        source='class C{@property\nfn x(self){42}\n@x.deleter\nfn x(self){null}\n};let c=C();delattr(c,"x");c.x'
        self.assertEqual(Evaluator().run(format_program(parse_program(source))),42)
