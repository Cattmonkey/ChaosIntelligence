"""Class source, canonical IR, and structural mutations use the same language."""
import copy
import json
import unittest
from chaos.language import Evaluator,LanguageError
from chaos.program import parse_program,format_program,plain,decode_program,templates,children
from chaos.ide_language import format_source

SOURCE='class Base {var value:Int=40;fn __init__(self,value:Int=40){self.value=value;null};fn read(self,delta:Int=2)->Int{self.value+delta}}class Child(Base){fn __call__(self,delta:Int=2)->Int{super(Child,self).read(delta)}}let item=Child();let detached=item.read;[item(),detached()]'

class ObjectIR(unittest.TestCase):
    def test_roundtrip_preserves_class_bases_set_member_and_all_method_flavors(self):
        program=parse_program(SOURCE);canonical=format_program(json.loads(json.dumps(program)))
        self.assertEqual(plain(decode_program(program)),plain(decode_program(parse_program(canonical))))
        self.assertEqual(Evaluator().run(canonical),[42,42])
        base=program['root']['statements'][0]
        self.assertEqual(base['kind'],'class');self.assertEqual(base['bases'],[])
        self.assertEqual(base['body']['statements'][1]['function']['body']['statements'][0]['kind'],'set_member')
        source='class C{fn normal(self){1};async fn task(self){1};fn* stream(self){yield 1};async fn* asyncStream(self){yield 1}};null'
        self.assertEqual(parse_program(format_program(parse_program(source)))['semanticHash'],parse_program(source)['semanticHash'])
        formatted=format_source('// keep\n'+SOURCE)
        self.assertIn('// keep',formatted);self.assertEqual(Evaluator().run(formatted),[42,42])

    def test_field_structure_edit_runs_and_invalid_class_body_is_rejected(self):
        program=parse_program(SOURCE);default=program['root']['statements'][0]['body']['statements'][1]['function']['parameters'][1]['default'];default['value']=41
        self.assertEqual(Evaluator().run(format_program(program)),[43,43])
        invalid=copy.deepcopy(program);invalid['root']['statements'][0]['body']['statements'].append(templates()['literal'])
        with self.assertRaises(LanguageError):format_program(invalid)
        self.assertEqual(templates()['class']['kind'],'class');self.assertEqual(templates()['set_member']['kind'],'set_member')
        self.assertEqual([slot for slot,_ in children(program['root']['statements'][1])],['bases.0','body'])

    def test_old_record_and_name_assignment_shapes_stay_unchanged(self):
        program=parse_program('var n=0;n=1;let r={x:2};r.x')
        self.assertEqual(set(program['root']['statements'][1])-{'id','span'}, {'kind','name','value'})
        self.assertEqual(set(program['root']['statements'][3])-{'id','span'}, {'kind','object','index'})
