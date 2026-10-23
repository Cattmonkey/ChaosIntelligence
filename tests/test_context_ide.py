"""Context syntax uses lexical scopes and inert protocol metadata in the IDE."""
import bisect
import unittest
from unittest.mock import patch

from chaos.ide_language import analyze, SourceMap, Analysis
from chaos.language import Evaluator, Parser
from chaos.program import plain
from chaos.source_format import format_source
from chaos.code_presentation import render_code


class ContextIDE(unittest.TestCase):
    def result(self, source, **kwargs):
        result = analyze(source, **kwargs)
        self.assertTrue(result['complete'], result['diagnostics'])
        self.assertFalse(result['diagnostics'], result['diagnostics'])
        return result

    def binding(self, source, name='resource'):
        return next(symbol for symbol in self.result(source)['symbols'] if symbol['name'] == name)

    def tokens(self, source):
        mapping = SourceMap(source)
        return [(source[bisect.bisect_left(mapping.units, item['from']):bisect.bisect_left(mapping.units, item['to'])], item['type'])
                for item in self.result(source)['tokens']]

    def test_sync_and_async_enter_annotations_and_native_computer(self):
        for declaration, syntax, expected in [
            ('fn __enter__(self)->Int{42}', 'with', 'Int'),
            ('async fn __aenter__(self)->String{"hi"}', 'async with', 'String'),
            ('async fn __enter__(self)->String{"hi"}', 'with', 'Task'),
            ('fn __aenter__(self)->Int{42}', 'async with', 'Any'),
            ('fn __enter__(self){42}', 'with', 'Any'),
        ]:
            with self.subTest(declaration=declaration):
                source='class C{'+declaration+'}'+syntax+' C() as resource{resource}'
                self.assertEqual(self.binding(source)['type'],expected)
        self.assertEqual(self.binding('fn inspect(value:Computer){async fn run(){async with value as resource{resource}}}')['type'],'Computer')

    def test_mro_and_stable_member_inference(self):
        source='class B{fn __enter__(self)->Int{42}}class C(B){}with C() as resource{resource}'
        self.assertEqual(self.binding(source)['type'],'Int')
        source='class Result{let answer=42}class C{fn __enter__(self)->Result{Result()}}with C() as resource{resource.answer}'
        result=self.result(source,position=source.index('resource.answer')+len('resource.ans'))
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='resource'),'Result')
        self.assertIn('Int',result['hover']['contents'])

    def test_dynamic_protocols_and_nominal_parameters_remain_unknown(self):
        for source in [
            'class C{var __enter__=fn()->Int{42}}with C() as resource{resource}',
            'class C{fn __enter__(self)->Int{42}}fn inspect(c:C){with c as resource{resource}}',
            'class D{fn __get__(self,obj,owner)->Function{throw "no"}}class C{let __enter__=D()}with C() as resource{resource}',
            'with input as resource{resource}',
        ]:
            with self.subTest(source=source):self.assertEqual(self.binding(source)['type'],'Any')

    def test_manager_uses_outer_scope_and_body_binding_does_not_leak(self):
        source='class C{fn __enter__(self)->Int{42}}let resource=C();with resource as resource{let inside=resource;inside};resource'
        result=self.result(source)
        resources=[s for s in result['symbols'] if s['name']=='resource']
        self.assertEqual([s['type'] for s in resources],['C','Int'])
        self.assertIn('C',result['hover']['contents'])
        source='with input as private{private};private'
        errors=analyze(source)['diagnostics']
        self.assertEqual([e['code'] for e in errors],['undefined_name'])
        self.assertEqual(errors[0]['from'],source.rindex('private'))

    def test_comma_lowering_has_sequential_scopes_and_exact_binding_ranges(self):
        source='// 🙂\nclass C{fn __enter__(self)->C{self}}with C() as first, first as second{second}'
        result=self.result(source)
        for name in ('first','second'):
            symbol=next(s for s in result['symbols'] if s['name']==name)
            offset=source.index('as '+name)+3
            self.assertEqual(symbol['from'],len(source[:offset].encode('utf-16-le'))//2)
        at=source.rindex('second')+2
        labels={item['label'] for item in analyze(source,position=len(source[:at].encode('utf-16-le'))//2)['completions']}
        self.assertIn('second',labels)

    def test_only_actual_context_prefixes_are_keywords(self):
        for source in ['let with=42;with','fn with(x){x};with(42)','let async=1;let with=2;async with']:
            with self.subTest(source=source):
                self.assertTrue(all(kind!='keyword' for text,kind in self.tokens(source) if text in ('with','async')))
        source='let with=input;async with with as value{value}'
        self.assertEqual([kind for text,kind in self.tokens(source) if text=='with'],['variable','keyword','variable'])
        self.assertEqual([kind for text,kind in self.tokens(source) if text=='async'],['keyword'])
        source='let with=input;with with as value, with as other{other}'
        self.assertEqual([kind for text,kind in self.tokens(source) if text=='with'],['variable','keyword','variable','variable'])
        self.assertIn('chaos-token-keyword">with',render_code('with input as value{value}','chaos'))

    def test_manager_nested_context_does_not_steal_outer_as_token(self):
        source='with (with input as inner{inner}) as outer{outer}'
        result=self.result(source)
        for name in ('inner','outer'):
            item=next(s for s in result['symbols'] if s['name']==name)
            self.assertEqual(item['from'],source.index('as '+name)+3)

    def test_recovery_completions_and_nonexecuting_analysis(self):
        source='class C{fn __enter__(self)->Int{tool("forbidden",null)}}with C() as resource{res'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('analysis evaluated')):
            result=analyze(source)
        self.assertFalse(result['complete'])
        self.assertIn('resource',{c['label'] for c in result['completions']})
        self.assertIn('with',{c['label'] for c in analyze('wi')['completions']})

    def test_comment_formatter_roundtrip_and_bounded_analysis(self):
        source='// context\nwith input as first, first as second{ // body\nsecond};null'
        formatted=format_source(source)
        self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
        self.assertEqual(format_source(formatted),formatted)
        self.assertIn('// context',formatted);self.assertIn('// body',formatted)
        original=Analysis.walk;calls=[0]
        def bounded(this,*args,**kwargs):
            calls[0]+=1;self.assertLess(calls[0],150)
            return original(this,*args,**kwargs)
        with patch.object(Analysis,'walk',bounded):self.result('with input as value{'*20+'value'+'}'*20)
