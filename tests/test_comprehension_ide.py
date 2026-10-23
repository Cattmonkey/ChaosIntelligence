"""IDE scopes follow comprehension binding order rather than token order."""
import unittest
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator
from chaos.program import parse_program
from chaos.source_format import format_source
from chaos.code_presentation import render_code


def units(text):return len(text.encode('utf-16-le'))//2


class ComprehensionIDE(unittest.TestCase):
    def result(self,source,position=None):
        result=analyze(source,position=position)
        self.assertTrue(result['complete'],result['diagnostics'])
        self.assertFalse(result['diagnostics'],result['diagnostics'])
        return result

    def test_projection_sees_later_binder_completion_and_hover(self):
        source='// 🙂 中文\nlet letters=[letter for letter in "hi" if letter!=""];letters'
        cursor=source.index('[letter')+len('[let')
        result=self.result(source,units(source[:cursor]))
        self.assertIn('letter',{item['label'] for item in result['completions']})
        self.assertIn('String',result['hover']['contents'])
        binder=next(item for item in result['symbols'] if item['name']=='letter')
        self.assertEqual(binder['from'],units(source[:source.index('for letter')+4]))
        self.assertEqual(binder['type'],'String')
        self.assertEqual(next(item['type'] for item in result['symbols'] if item['name']=='letters'),'List')

    def test_iterable_shadowing_prefix_guards_and_nonleaking_scope(self):
        source='let item=["a"];[item for item in item];item'
        result=self.result(source,source.index('in item')+4)
        self.assertIn('List',result['hover']['contents'])
        result=self.result(source,source.index('[item')+2)
        self.assertIn('Any',result['hover']['contents'])
        source='[later for item in "a" if later=="a" for later in "b"]'
        result=analyze(source,position=source.index('if later')+len('if la'))
        self.assertNotIn('later',{item['label'] for item in result['completions']})
        self.assertEqual([item['code'] for item in result['diagnostics']],['undefined_name'])
        self.assertEqual(result['diagnostics'][0]['from'],source.index('if later')+3)
        source='[private for private in "a"];private'
        self.assertEqual([item['from'] for item in analyze(source)['diagnostics']],[source.rindex('private')])

    def test_later_clause_and_nested_projection_inherit_correct_binding(self):
        source='let output=[[outer+inner for inner in outer] for outer in "ab"];output'
        self.result(source)
        source='[x for x in "ab" for x in x]'
        result=self.result(source,source.rindex('in x')+4)
        self.assertIn('String',result['hover']['contents'])
        self.assertEqual(len([item for item in result['symbols'] if item['name']=='x']),2)
        source='[later for x in "a"];let later="late"'
        result=analyze(source,position=source.index('later')+2)
        self.assertNotIn('later',{item['label'] for item in result['completions']})

    def test_types_dynamic_record_keys_and_inert_protocols(self):
        source='let a=[x for x in "a"];let b={x:x for x in "a"};let c=(x for x in "a");let d=async (x for x in "a");'
        types={item['name']:item['type'] for item in self.result(source)['symbols']}
        self.assertEqual([types[name] for name in 'abcd'],['List','Record','Generator','AsyncGenerator'])
        for source in ('[x for x in {}]','[x async for x in []]','[x for x in "a" if 42]','{42:x for x in "a"}'):
            self.assertIn('type_mismatch',{item['code'] for item in analyze(source)['diagnostics']})
        self.result('class C{fn __iter__(self){throw "not called"}}[x for x in C()]')
        self.result('async fn* stream(){yield 1};[x async for x in stream()]')

    def test_real_function_signature_in_projection_and_async_identifier(self):
        source='fn pair(left:String,right:String){left+right};[pair(letter, "") for letter in "hi"]'
        result=self.result(source,source.index(', ""')+2)
        self.assertEqual(result['signatureHelp']['activeParameter'],1)
        self.assertIn('right: String',result['signatureHelp']['label'])
        source='fn async(x){x};let result=async(42);let stream=async (x for x in "a");'
        result=self.result(source)
        data=source.encode('utf-16-le')
        kinds=[item['type'] for item in result['tokens'] if data[item['from']*2:item['to']*2].decode('utf-16-le')=='async']
        self.assertEqual(kinds,['function','function','keyword'])

    def test_incomplete_clause_keeps_projection_binding_without_execution(self):
        source='[value for value in'
        result=analyze(source,position=3)
        self.assertFalse(result['complete'])
        self.assertIn('value',{item['label'] for item in result['completions']})
        source='[value for value in "a" if '
        result=analyze(source)
        self.assertIn('value',{item['label'] for item in result['completions']})
        source='fn work(x:String)->String{x};[work(x) for x in "a"]'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed')):
            self.result(source);html=render_code(source,'chaos');formatted=format_source(source)
            self.assertIn('chaos-token-function',html)
            self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(source)['semanticHash'])


if __name__=='__main__':unittest.main()
