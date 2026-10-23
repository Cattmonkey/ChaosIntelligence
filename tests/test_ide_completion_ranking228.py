"""Bounded completion menus retain real visible source bindings."""
import unittest
from chaos.ide_language import analyze,builtin_symbols
from chaos.language import Evaluator,Native


class CompletionRanking228(unittest.TestCase):
    def test_blank_prefix_retains_late_local_function_and_variable_with_real_catalog(self):
        # Use the actual public catalog, rather than a synthetic short environment.
        natives={name for name,value in Evaluator().env.values.items() if type(value)is Native}
        self.assertEqual(len(natives),335)
        self.assertGreater(sum(name<'zzzzz_result' for name in builtin_symbols()),150)
        result=analyze('fn zzzzz_worker(){42};var zzzzz_result=1;')
        self.assertTrue(result['complete']);self.assertEqual(result['diagnostics'],[])
        labels=[row['label'] for row in result['completions']]
        self.assertEqual(len(labels),150)
        self.assertEqual(labels[:2],['zzzzz_result','zzzzz_worker'])
        self.assertIn('abs',labels)

    def test_visible_scope_and_comprehension_priority_precede_catalog(self):
        source='fn zzzzz_scope(zzzzz_parameter:Int){let zzzzz_local=zzzzz_parameter; };let zzzzz_future=2'
        result=analyze(source,position=source.index(' };'))
        labels=[row['label'] for row in result['completions']]
        self.assertIn('zzzzz_local',labels);self.assertIn('zzzzz_parameter',labels)
        self.assertNotIn('zzzzz_future',labels)
        self.assertLess(labels.index('zzzzz_local'),labels.index('abs'))
        outside=analyze(source+';')['completions']
        self.assertNotIn('zzzzz_local',{row['label'] for row in outside})
        source='let aaaaa_outer="ab";[zzzzz_item for zzzzz_item in aaaaa_outer]'
        result=analyze(source,position=source.index('[')+1)
        self.assertTrue(result['complete']);self.assertEqual(result['diagnostics'],[])
        self.assertEqual(result['completions'][0]['label'],'zzzzz_item')

    def test_parameter_prefix_and_member_order_keep_existing_semantics(self):
        source='fn collect(a:Int,b=2,*rest,required,**options)->Record{{}};collect(1,req'
        choices=analyze(source)['completions']
        self.assertEqual(choices[0]['label'],'required=')
        self.assertEqual(choices[0]['apply'],'required=')
        self.assertTrue(all(row['label'].startswith('req')for row in choices))
        choices=analyze('let record={aaa:1,zzz:2};record.')['completions']
        self.assertEqual([row['label']for row in choices],['aaa','zzz'])
        self.assertEqual([row['label']for row in analyze('fn zzzzz_worker(){42};zzzzz_w')['completions']],['zzzzz_worker'])


if __name__=='__main__':unittest.main()
