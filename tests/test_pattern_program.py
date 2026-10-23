"""Destructuring targets round-trip without changing ordinary binding IR."""
import copy
import unittest
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program,format_program,decode_program,patch_program,templates,project_program
from chaos.source_format import format_source


class PatternProgram(unittest.TestCase):
    def test_six_nodes_and_nested_rest_targets_roundtrip(self):
        sources=[('let [a,{x:b},..rest]:List=[20,{x:21},1];[a,b,rest]',[20,21,[1]]),
          ('var {x:a,..rest}:Record={x:20,y:22};a="changed";[a,rest]',['changed',{'y':22}]),
          ('var total=0;for [a,b] in [[20,22]]{total=a+b};total',42),
          ('async fn* rows(){yield ([20,22])};var total=0;async for [a,b] in rows(){total=a+b};total',42),
          ('[a+b for [a,b] in [[20,22]] if a>0]',[42]),
          ('async fn* rows(){yield ([20,22])};[a+b async for [a,b] in rows()]',[42]),
          ('data Option{Some(value)};let Some(value)=Some(42);value',42)]
        for source,expected in sources:
            with self.subTest(source=source):
                program=parse_program(source)
                for rendered in (format_program(program),format_source(source)):
                    self.assertEqual(parse_program(rendered)['semanticHash'],program['semanticHash'])
                    self.assertEqual(Evaluator().run(rendered),expected)

    def test_target_ir_rejects_plain_names_invalid_children_and_duplicate_leaves(self):
        original=parse_program('let [left,right]=[20,22];left+right')
        for pattern in [templates()['p_bind'],templates()['p_any'],templates()['list']]:
            bad=copy.deepcopy(original);bad['root']['statements'][0]['pattern']=pattern
            with self.assertRaises(LanguageError):decode_program(bad)
        bad=copy.deepcopy(original);bad['root']['statements'][0]['pattern']['items'][1]['name']='left'
        with self.assertRaises(LanguageError):decode_program(bad)
        bad=copy.deepcopy(original);bad['root']['statements'][0]['pattern']['rest']=templates()['p_list']
        with self.assertRaises(LanguageError):decode_program(bad)
        source=parse_program('[x for [x] in [[1]]]');bad=copy.deepcopy(source);bad['root']['statements']=[bad['root']['statements'][0]['clauses'][0]]
        with self.assertRaises(LanguageError):decode_program(bad)

    def test_simple_identifier_including_uses_original_shapes(self):
        for source,kind in [('let _=42','let'),('var _:Int=42','var'),('for _ in []{_}','for'),('async for _ in input{_}','async_for')]:
            node=parse_program(source)['root']['statements'][0]
            self.assertEqual(node['kind'],kind);self.assertEqual(node['name'],'_');self.assertNotIn('pattern',node)
        for source,kind in [('[x for x in []]','comp_for'),('[x async for x in input]','comp_async_for')]:
            clause=parse_program(source)['root']['statements'][0]['clauses'][0]
            self.assertEqual(clause['kind'],kind);self.assertNotIn('pattern',clause)
        self.assertEqual(parse_program('let _=42;_')['semanticHash'],'320a48d163175c79ddb14c8ffca7487874bc35394a87a06b5a4e1b04007d652b')

    def test_pattern_patch_and_templates_are_structurally_editable(self):
        program=parse_program('let [left,right]=[20,22];left+right');target=program['root']['statements'][0]['value']['items'][1]
        changed=patch_program(program,[{'op':'set','id':target['id'],'field':'value','value':23}])
        self.assertEqual(Evaluator().run(format_program(changed)),43)
        self.assertTrue(any(edge['slot']=='pattern' for edge in project_program(changed)['edges']))
        values=templates()
        for kind in ('let_pattern','var_pattern','for_pattern','async_for_pattern'):
            shell=parse_program('null');shell['root']['statements']=[values[kind]];format_program(shell)
        for kind in ('comp_for_pattern','comp_async_for_pattern'):
            shell=parse_program('[value for value in []]');shell['root']['statements'][0]['clauses']=[values[kind]];format_program(shell)

    def test_yield_list_grouping_and_legacy_contextual_identifier_survive(self):
        for source in ('fn* rows(){yield ([20,22])};collect(rows())',
                       'async fn* rows(){yield ([20,22])};await acollect(rows())',
                       'fn* rows(){let yield=[42];yield[0]};collect(rows())'):
            program=parse_program(source);rendered=format_program(program)
            self.assertEqual(parse_program(rendered)['semanticHash'],program['semanticHash'])
            self.assertEqual(Evaluator().run(rendered),Evaluator().run(source))
        source='// 🙂 keep spelling\nlet {left:[x,..xs],..rest}:Record={left:[20,22],name:"if for"};[x,xs,rest]'
        formatted=format_source(source,line_width=40)
        self.assertIn('// 🙂 keep spelling',formatted);self.assertIn('"if for"',formatted)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(source)['semanticHash'])
        self.assertEqual(format_source(formatted,line_width=40),formatted)
        self.assertEqual(format_source('let[x]=[1];for[x]in[[1]]{x}'), 'let [x] = [1];\nfor [x] in [[1]] {\n  x\n}\n')


if __name__=='__main__':unittest.main()
