"""Unambiguous attribute/item IR, legacy dialect and complete slice editing."""
import copy
import unittest
from chaos.language import Evaluator, LanguageError, Parser
from chaos.program import parse_program, decode_program, format_program, plain, patch_program, children, templates, project_program
from chaos.ide_language import format_source


class IndexingIRTests(unittest.TestCase):
    def test_dot_legacy_and_brackets_are_distinct(self):
        dot=parse_program('input.value');legacy=parse_program('input.["value"]');item=parse_program('input["value"]')
        self.assertEqual(dot['semanticHash'],legacy['semanticHash'])
        self.assertEqual(dot['root']['statements'][0]['id'],legacy['root']['statements'][0]['id'])
        self.assertNotEqual(dot['semanticHash'],item['semanticHash'])
        self.assertEqual(item['root']['statements'][0]['kind'],'subscript')
        self.assertEqual(parse_program('input[0]=42')['root']['statements'][0]['kind'],'set_item')

    def test_historical_ir_roundtrips_without_metadata(self):
        for source in ['input["field"]','input[key]','input[key]=42','input[0][1]']:
            with self.subTest(source=source):
                old=parse_program(source,language_version=1);old.pop('languageVersion')
                old['layout']={'retained':True};before=copy.deepcopy(old)
                emitted=format_program(old);self.assertIn('.[',emitted)
                self.assertEqual(plain(decode_program(old)),plain(Parser(emitted).program()))
                self.assertEqual(parse_program(emitted)['semanticHash'],old['semanticHash'])
                self.assertEqual(old,before)

    def test_version_metadata_validation_and_patch_preservation(self):
        program=parse_program('input[key]',language_version=1);program['sourceVersions']={'main.chaos':1,'lib.chaos':2}
        key=program['root']['statements'][0]['index']
        changed=patch_program(program,[{'op':'set','id':key['id'],'field':'name','value':'other'}])
        self.assertEqual(changed['sourceVersions'],program['sourceVersions']);self.assertEqual(changed['languageVersion'],1)
        for field,value in [('languageVersion',True),('languageVersion',3),('sourceVersions',{'x':False}),('sourceVersions',[] )]:
            invalid=copy.deepcopy(program);invalid[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(LanguageError):decode_program(invalid)

    def test_all_slice_omissions_and_explicit_null_roundtrip(self):
        for bounds in [':','::','1:',':3','1:3',':3:2','1::2','::-1','null:null:-1']:
            with self.subTest(bounds=bounds):
                source='input['+bounds+']';program=parse_program(source)
                self.assertEqual(program['root']['statements'][0]['index']['kind'],'slice')
                self.assertEqual(plain(decode_program(program)),plain(Parser(format_program(program)).program()))
        self.assertIsNone(parse_program('input[:]')['root']['statements'][0]['index']['start'])
        self.assertEqual(parse_program('input[null:]')['root']['statements'][0]['index']['start']['kind'],'literal')

    def test_slice_only_valid_as_direct_item_key(self):
        slice_node=templates()['slice']
        for source,field in [('input.value','index'),('let value=0','value'),('input[0]','object')]:
            program=parse_program(source);program['root']['statements'][0][field]=copy.deepcopy(slice_node)
            with self.subTest(source=source),self.assertRaises(LanguageError):decode_program(program)
        program=parse_program('input[:]');program['root']['statements'][0]['index']['start']=copy.deepcopy(slice_node)
        with self.assertRaises(LanguageError):decode_program(program)

    def test_slice_bounds_patch_traversal_and_graph(self):
        program=parse_program('input[1:5:2]');item=program['root']['statements'][0];spec=item['index']
        self.assertEqual([key for key,_ in children(spec)],['start','stop','step'])
        changed=patch_program(program,[{'op':'set','id':spec['start']['id'],'field':'value','value':2},{'op':'set','id':spec['id'],'field':'stop','value':None}])
        self.assertEqual(changed['root']['statements'][0]['id'],item['id'])
        self.assertIn('[2::2]',format_program(changed))
        self.assertTrue(any(row['kind']=='slice' for row in project_program(changed)['nodes']))

    def test_templates_match_advertised_kinds(self):
        for key,value in templates().items():self.assertEqual(key,value['kind'])

    def test_comments_and_legacy_format_upgrade_are_semantically_safe(self):
        for source in ['// retained\n(a)[1]','(a[1])[2]','a // retained\n [2]','(a+b)[f()[0]]','a[1]=b[2]']:
            with self.subTest(source=source):
                emitted=format_source(source,language_version=1)
                self.assertEqual(plain(Parser(source,legacy_items=True).program()),plain(Parser(emitted).program()))
                self.assertEqual(format_source(emitted),emitted)
                if '// retained' in source:self.assertIn('// retained',emitted)
        source='// slices\nlet x=[0,1,2,3];x[1: : -1]'
        self.assertEqual(plain(Parser(source).program()),plain(Parser(format_source(source)).program()))

    def test_legacy_ir_does_not_activate_item_protocol(self):
        source='class C{let x=7;fn __getitem__(self,key){42}}let c=C();c["x"]'
        old=parse_program(source,language_version=1)
        self.assertEqual(Evaluator().run(format_program(old)),7)
        self.assertEqual(Evaluator().run(source),42)

