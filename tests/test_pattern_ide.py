"""Destructuring intelligence follows exact leaf spans and pre-binding scopes."""
import unittest
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator
from chaos.code_presentation import render_code


def u(text):return len(text.encode('utf-16-le'))//2


class PatternIDE(unittest.TestCase):
    def result(self,source,position=None):
        result=analyze(source,position=position)
        self.assertTrue(result['complete'],result['diagnostics']);self.assertFalse(result['diagnostics'],result['diagnostics'])
        return result

    def test_nested_immutable_metadata_and_mutable_leaf_contracts(self):
        source='let [first,{name:label},..rest]:List=[20,{name:"Ada"},42];var {x:mutable,..extra}:Record={x:1,y:2};mutable="ok";[first,label,rest,mutable,extra]'
        types={item['name']:item['type'] for item in self.result(source)['symbols']}
        self.assertEqual({name:types[name] for name in ('first','label','rest','mutable','extra')},{'first':'Int','label':'String','rest':'List','mutable':'Any','extra':'Any'})
        self.result('let {name:x,..rest}={name:"Ada",answer:42};let {answer:y}=rest;y')

    def test_rhs_completion_uses_prebinding_scope_and_names_do_not_leak(self):
        source='let [fresh]=[fresh]'
        result=analyze(source,position=source.rindex('fresh')+2)
        self.assertNotIn('fresh',{item['label'] for item in result['completions']})
        self.assertEqual([item['code'] for item in result['diagnostics']],['undefined_name'])
        source='let value="outer";do {let [value]=[value];value}'
        result=self.result(source,source.index('=[value]')+3);self.assertIn('String',result['hover']['contents'])
        source='for [hidden] in [[1]]{hidden};hidden'
        self.assertEqual([item['from'] for item in analyze(source)['diagnostics']],[source.rindex('hidden')])

    def test_comprehension_all_leafs_visible_only_in_projection_and_later_clauses(self):
        source='// 🙂 中文\n[left+right for [left,right] in [[20,22]]]'
        result=self.result(source,u(source[:source.index('[left')+3]))
        self.assertIn('left',{item['label'] for item in result['completions']});self.assertIn('Int',result['hover']['contents'])
        for name in ('left','right'):
            symbol=next(item for item in result['symbols'] if item['name']==name)
            self.assertEqual(symbol['from'],u(source[:source.index(name,source.index('for'))]))
        source='[x for [x] in [[x]]]'
        result=analyze(source,position=source.rindex('x')+1)
        self.assertNotIn('x',{item['label'] for item in result['completions']})
        source='[inner for [outer] in [["a"]] for [inner] in [[outer]]]';self.result(source)

    def test_destructured_functions_keep_signatures_and_var_does_not(self):
        source='let [call]=[fn(left:Int,right:Int)->Int{left+right}];call(20,22)'
        result=self.result(source,source.index('call(20,')+len('call(20,'))
        self.assertEqual(result['signatureHelp']['activeParameter'],1);self.assertIn('right: Int',result['signatureHelp']['label'])
        source='var [call]=[fn(left:Int,right:Int)->Int{left+right}];call(20,22)'
        result=self.result(source,source.index('call(20,')+len('call(20,'));self.assertIsNone(result['signatureHelp'])
        source='let {map:mapper,..rest}=input;mapper(42)';self.result(source)

    def test_constructor_is_resolved_before_same_name_leaf_is_declared(self):
        source='data Option{Some(value)};do {let Some(Some)=Some(42);Some}'
        result=self.result(source,source.index('let Some')+5)
        self.assertIn('Some(',result['hover']['contents'])
        texts=source.encode('utf-16-le');kinds=[item['type'] for item in result['tokens'] if texts[item['from']*2:item['to']*2].decode('utf-16-le')=='Some']
        self.assertEqual(kinds,['function','function','variable','function','variable'])
        self.assertEqual(next(item['type'] for item in result['symbols'] if item['name']=='Some' and item['from']>source.index('let')),'Any')

    def test_incomplete_rhs_and_inert_analysis_have_real_leaf_types(self):
        source='[value for [value] in'
        result=analyze(source,position=3);self.assertFalse(result['complete']);self.assertIn('value',{item['label'] for item in result['completions']})
        source='let [fnAlias]=[fn(x:Int)->Int{tool("forbidden",x)}];fnAlias(42)'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed')):
            result=self.result(source);html=render_code(source,'chaos')
            self.assertIn('chaos-token-function',html)
        for source in ('let [x]="a"','let {x:y}=[]','let [a,b]=[1]'):
            self.assertTrue(analyze(source)['diagnostics'])

    def test_immutable_function_and_class_leaf_declarations_keep_semantic_kind(self):
        source='class C{};let {call:f,klass:Alias}={call:fn(x:Int)->Int{x},klass:C};[f(42),Alias()]'
        result=self.result(source)
        symbols={item['name']:item for item in result['symbols']}
        self.assertEqual((symbols['f']['kind'],symbols['f']['type']),('function','Function'))
        self.assertEqual((symbols['Alias']['kind'],symbols['Alias']['type']),('class','Class'))
        for name,kind in [('f','function'),('Alias','type')]:
            self.assertEqual([item['type'] for item in result['tokens'] if source[item['from']:item['to']]==name],[kind,kind])
        self.assertIn('chaos-token-function',render_code(source,'chaos'))
        self.assertIn('chaos-token-type',render_code(source,'chaos'))
        variable='var [call]=[fn(x:Int)->Int{x}];call(42)'
        self.assertEqual(next(item['kind'] for item in self.result(variable)['symbols'] if item['name']=='call'),'variable')

    def test_exact_record_mismatch_is_reported_for_literals_aliases_and_rest(self):
        cases=[
            'let {x:a}={x:1,y:2};a',
            'let {x:a,y:b}={x:1};b',
            'let {}={x:1};42',
            'let {x:a,.._}={y:1};a',
            'let source={x:1};let {y:a}=source;a',
            'let {x:a,..rest}={x:1,y:2};let {z:b}=rest;b',
            'let {outer:{x:a}}={outer:{y:1}};a',
            'for {x:a} in [{y:1},{y:2}]{a}',
            '[a for {x:a} in [{y:1},{y:2}]]',
        ]
        for source in cases:
            with self.subTest(source=source):
                diagnostics=analyze(source)['diagnostics']
                self.assertEqual([item['code'] for item in diagnostics],['pattern_mismatch'])
                self.assertGreater(diagnostics[0]['to'],diagnostics[0]['from'])

    def test_exact_record_proof_is_conservative_after_mutation_and_unknown_results(self):
        cases=[
            'let {x:a,.._}={x:1,y:2};a',
            'let {}={};42',
            'let {x:a,..rest}=input;let {y:b}=rest;b',
            'fn read(value:Record){let {x:a}=value;a};null',
            'agent worker{};let {x:a}=worker(null);a',
            'var value:Record={x:1};value={y:2};let {y:a}=value;a',
            'var value:Record={nested:{x:1}};value={nested:{y:2}};let snapshot=value;let {y:a}=snapshot.nested;a',
            'var rows:List=[{x:1}];rows=[{y:2}];for {y:a} in rows {a}',
            'let {y:a}=if true {{x:1}}else{{y:2}};a',
        ]
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed')):
            for source in cases:
                with self.subTest(source=source):self.result(source)

    def test_pattern_keys_are_properties_without_recoloring_binders_or_strings(self):
        source='// 🙂\nlet {foo:{foo:foo},"quoted":alias}={foo:{foo:42},"quoted":1};match foo {{bar:bar,.._}=>bar,_=>foo}'
        result=self.result(source)
        raw=source.encode('utf-16-le')
        tokens=[(raw[t['from']*2:t['to']*2].decode('utf-16-le'),t['type']) for t in result['tokens']]
        self.assertEqual([kind for text,kind in tokens if text=='foo'],['property','property','variable','property','property','variable','variable'])
        self.assertEqual([kind for text,kind in tokens if text=='bar'],['property','variable','variable'])
        self.assertEqual([kind for text,kind in tokens if text=='"quoted"'],['property','property'])
        self.assertEqual([kind for text,kind in tokens if text=='alias'],['variable'])


if __name__=='__main__':unittest.main()
