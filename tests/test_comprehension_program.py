"""Comprehension source and structural IR remain one lossless representation."""
import copy
import unittest
from chaos.language import Evaluator, LanguageError
from chaos.program import SHAPES, parse_program, decode_program, format_program, patch_program, project_program, templates
from chaos.source_format import format_source


class ComprehensionProgram(unittest.TestCase):
    def test_four_shapes_roundtrip_and_execute(self):
        cases=[('[x*2 for x in [20,21] if x>0]',[40,42]),
               ('{x:x+x for x in "ab"}',{'a':'aa','b':'bb'}),
               ('collect((x*2 for x in [20,21]))',[40,42]),
               ('async fn work(x){x*2};await acollect(async (await work(x) for x in [20,21]))',[40,42]),
               ('async fn* source(){yield 20;yield 21};[x*2 async for x in source()]',[40,42])]
        for source,expected in cases:
            with self.subTest(source=source):
                program=parse_program(source);formatted=format_program(program)
                self.assertEqual(parse_program(formatted)['semanticHash'],program['semanticHash'])
                self.assertEqual(Evaluator().run(formatted),expected)
                self.assertEqual(parse_program(format_source(source))['semanticHash'],program['semanticHash'])
        self.assertEqual(SHAPES['record_comp'],[('key','node'),('value','node'),('clauses','nodes')])

    def test_old_identities_and_dialects_are_unchanged(self):
        fixtures=[(1,'let value={answer:42};fn read(x){x["answer"]};read(value)','5d87b8f20279d5c4d946da2bd625af76425ec43acce6f27578b37764411e6910',
          ['n_ef8027a09a0a9c0b121a','n_341d96a010c3fd3cb629','n_d9c8f461eda7dcbedbed','n_a394cf7338ee38fd09e4','n_4455128874c5305bcf09','n_e764dc9abbc9a29df044','n_581695f4c8e226a54a8d','n_7f1e51896fdfe86f99a7','n_525b8bf0891ff7e9c877','n_555a1ba962b442d4dc7d','n_e2ced0793303b2523ee3','n_374f8c685221c6c393d9','n_1cc2add1df536b392544']),
          (2,'let value=[20,22];fn read(x){x[0]+x[1]};read(value)','79bb82385263a27fd1902801830c424efc7abcdac250067869f55327225015de',
          ['n_ef8027a09a0a9c0b121a','n_341d96a010c3fd3cb629','n_9e923c735912d3867b29','n_219d426f6e31f5e3e79f','n_01742110babfdc71bf18','n_4455128874c5305bcf09','n_a716ed58e6b1c2b08f0d','n_7168374609c4b2f233e1','n_14030fa3f611bead9779','n_b5a94a62fdd737bdf971','n_721c08e2da2f8ffce044','n_ade208bf66aefaf980a5','n_809b85107a4b360db699','n_ef0b70693eb223ca1273','n_b810efae076377d096b2','n_e2ced0793303b2523ee3','n_374f8c685221c6c393d9','n_1cc2add1df536b392544'])]
        for version,source,digest,ids in fixtures:
            program=parse_program(source,language_version=version)
            self.assertEqual(program['schema'],'chaos.program/1');self.assertEqual(program['semanticHash'],digest)
            self.assertEqual([node['id'] for node in project_program(program)['nodes']],ids)
            self.assertEqual(Evaluator().run(format_program(program)),42)
            program.pop('semanticHash')
            self.assertEqual(parse_program(format_program(program))['semanticHash'],digest)

    def test_clause_context_and_order_are_rejected_atomically(self):
        original=parse_program('[x for x in [1] if true]');comp=original['root']['statements'][0]
        invalid=[]
        stray=copy.deepcopy(original);stray['root']['statements']=[stray['root']['statements'][0]['clauses'][0]];invalid.append(stray)
        for clauses in ([],list(reversed(comp['clauses'])),[comp['element']]):
            changed=copy.deepcopy(original);changed['root']['statements'][0]['clauses']=clauses;invalid.append(changed)
        for program in invalid:
            with self.assertRaises(LanguageError):decode_program(program)
        before=copy.deepcopy(original)
        with self.assertRaises(LanguageError):patch_program(original,[{'op':'move','id':comp['id'],'field':'clauses','from':0,'to':1}])
        self.assertEqual(original,before)
        changed=patch_program(original,[{'op':'set','id':comp['clauses'][1]['condition']['id'],'field':'value','value':False}])
        self.assertEqual(Evaluator().run(format_program(changed)),[])
        self.assertEqual(changed['root']['statements'][0]['id'],comp['id'])

    def test_formatting_preserves_trivia_and_clause_semantics(self):
        source='// 🙂 async for is text\nlet result={x: x + " for if async " for x in "abc" if x != "b"}; result'
        rendered=format_source(source,line_width=40)
        self.assertIn('" for if async "',rendered)
        self.assertIn('// 🙂 async for is text',rendered)
        self.assertEqual(parse_program(rendered)['semanticHash'],parse_program(source)['semanticHash'])
        self.assertEqual(format_source(rendered,line_width=40),rendered)
        self.assertIn('\n  for ',rendered)
        self.assertIn('\n  if ',rendered)
        compact=format_source('async (x for x in [1,2])')
        self.assertIn('async (',compact);self.assertIn('in [',compact)
        source='[row["value"] for row in input]'
        self.assertEqual(parse_program(format_source(source,language_version=1))['semanticHash'],parse_program(source,language_version=1)['semanticHash'])

    def test_all_templates_and_projection_slots_are_real_nodes(self):
        values=templates()
        for kind in ('list_comp','record_comp','gen_comp','async_gen_comp'):
            program=parse_program('null');program['root']['statements']=[values[kind]];format_program(program)
            slots={edge['slot'] for edge in project_program(program)['edges'] if edge['source']==values[kind]['id']}
            self.assertIn('clauses.0',slots)
        self.assertEqual(values['comp_if']['condition']['value'],True)


if __name__=='__main__':unittest.main()
