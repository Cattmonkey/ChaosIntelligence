"""Synchronous composition tooling follows actual bindings without iteration."""
import inspect
import unittest
from unittest.mock import patch

from chaos.code_presentation import render_code, presentation_tokens
from chaos.ide_language import analyze
from chaos.language import Evaluator, TYPES
from chaos.native_specs import SPECS
from chaos.program import parse_program, format_program
from chaos.source_format import format_source


class IterableIDE(unittest.TestCase):
    def symbols(self, source):
        return {row['name']:row['type'] for row in analyze(source)['symbols']}

    def test_native_signatures_match_runtime_and_std_list_aliases(self):
        natives=Evaluator().env.values
        for name in ('apply','map','filter','fold','any','all'):
            with self.subTest(name=name):
                spec=SPECS[name]
                runtime=inspect.signature(natives[name].call).parameters
                self.assertEqual([p.name for p in spec.parameters],list(runtime))
                self.assertEqual([p.optional for p in spec.parameters],[p.default is not p.empty for p in runtime.values()])
                result=analyze(name+'(')['signatureHelp']
                self.assertEqual([p['name'] for p in result['parameters']],list(runtime))
                self.assertIn('同步可迭代',result['label'])
        self.assertNotIn('Iterable',TYPES)
        for name in ('map','filter','fold','any','all'):
            result=analyze('import "std/list" as L;let alias=L.'+name+';alias(')['signatureHelp']
            self.assertTrue(result['label'].endswith(analyze(name+'(')['signatureHelp']['label']))

    def test_materialized_results_apply_callback_and_cold_task_completion(self):
        source='fn* values(){yield 1};async fn work(x)->Int{x};fn sum(a,b)->Int{a+b};let jobs=map(work,values());let results=await gather(jobs);let kept=filter(fn(x){true},values());let exists=any(fn(x){true},values());let every=all(fn(x){true},values());let forwarded=apply(sum,values());let cold=apply(work,values());let done=await cold;let reduced=fold(sum,0,values());'
        result=self.symbols(source)
        self.assertEqual([result[key] for key in ('jobs','results','kept','exists','every','forwarded','cold','done','reduced')],['List','List','List','Bool','Bool','Int','Task','Int','Any'])
        result=self.symbols('let invoke=apply;let job=invoke(fn*(){yield 1},[]);let step=next(job);let done=step.done;')
        self.assertEqual(result['job'],'Generator');self.assertEqual(result['done'],'Bool')

    def test_shadowed_and_mutable_names_do_not_receive_native_inference(self):
        for source in ('fn apply(target,args)->String{"local"};let answer=apply(null,42);', 'let apply=fn(target,args)->String{"local"};let answer=apply(null,42);'):
            result=analyze(source)
            self.assertFalse(result['diagnostics'])
            self.assertEqual(next(row['type'] for row in result['symbols'] if row['name']=='answer'),'String')
        source='fn map(target,xs)->Int{42};let answer=map(null,{});'
        self.assertEqual(self.symbols(source)['answer'],'Int');self.assertFalse(analyze(source)['diagnostics'])
        self.assertEqual(self.symbols('var invoke=apply;let answer=invoke(fn()->Int{42},[]);')['answer'],'Any')

    def test_known_invalid_sources_and_kwargs_are_diagnosed(self):
        for source in ('map(fn(x){x},{})','filter(fn(x){true},42)','fold(fn(a,b){a},0,false)','any(fn(x){true},null)','all(fn(x){true},channel())','apply(fn(x){x},sleep(0))','fn f(*args){args};f(*{})','async fn* values(){yield 1};map(fn(x){x},values())','apply(fn(){42},[],kwargs=[])'):
            with self.subTest(source=source):self.assertTrue(any(row['code']=='type_mismatch' for row in analyze(source)['diagnostics']))
        for source in ('fn use(source){map(fn(x){x},source)}','class Cursor{fn __iter__(self){self}fn __next__(self){{done:true,value:null}}};map(fn(x){x},Cursor())','map(42,[])','map(fn(x){x},"hi")','map(fn(x){x},iter([1]))'):
            with self.subTest(source=source):self.assertFalse(analyze(source)['diagnostics'])

    def test_dynamic_expansion_does_not_guess_arity_or_callback(self):
        prefix='fn* values(){yield 1};fn target(first:Int,second:Int,*rest){first};'
        self.assertIsNone(analyze(prefix+'target(*values(),')['signatureHelp']['activeParameter'])
        self.assertFalse(analyze(prefix+'target(*values());')['diagnostics'])
        self.assertEqual(self.symbols('async fn work(x)->Int{x};let result=apply(*[work,[]]);')['result'],'Any')
        self.assertEqual(analyze('apply(fn(){42},args=')['signatureHelp']['activeParameter'],1)
        self.assertEqual(analyze('fold(fn(a,b){a},0,items=')['signatureHelp']['activeParameter'],2)

    def test_utf16_hints_colors_and_format_ir_are_same_execution_free_source(self):
        source='// 🙂 中文\nfn* values(){yield 20;yield 21};async fn work(value)->Int{value*2};let jobs=map(work,values());let done=await gather(jobs);fn add(a,b)->Int{a+b};[done,add(*values()),apply(add,values())]'
        with patch.object(Evaluator,'run',side_effect=AssertionError('no execution')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('no execution')):
            data=analyze(source);html=render_code(source,'chaos');formatted=format_source(source)
            ir=parse_program(source);reparsed=parse_program(format_program(ir))
        self.assertFalse(data['diagnostics'])
        self.assertEqual(ir['semanticHash'],reparsed['semanticHash'])
        self.assertEqual(ir['semanticHash'],parse_program(formatted)['semanticHash'])
        self.assertEqual([{key:token[key] for key in ('from','to','type')} for token in presentation_tokens(source)],data['tokens'])
        self.assertIn('map(fn, xs) -&gt; List',html);self.assertIn('apply(fn, args, kwargs: Record | Null = null)',html)
        raw=source.encode('utf-16-le');hints={raw[row['from']*2:row['to']*2].decode('utf-16-le'):row['detail'] for row in data['referenceHints']}
        self.assertIn('同步可迭代',hints['map']);self.assertIn('List',hints['jobs'])


if __name__=='__main__':unittest.main()
