"""Operator editor intelligence stays inert and uses the existing syntax tree."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
from chaos.ide_language import Analysis, analyze, builtin_symbols, format_source
from chaos.debugger import preview, locals_snapshot
from chaos.language import Evaluator, Parser
from chaos.program import plain
from chaos.operators import NOT_IMPLEMENTED

ROOT=Path(__file__).resolve().parent.parent

class OperatorAnalysisTests(unittest.TestCase):
    def result(self, source, name='answer'):
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return next(item['type'] for item in result['symbols'] if item['name']==name)

    def test_builtin_constant_is_not_callable(self):
        item=builtin_symbols()['NotImplemented']
        self.assertEqual(item.type,'NotImplemented');self.assertEqual(item.parameters,[])
        result=analyze('NotImplemented',position=4)
        self.assertIn('不是函数',result['hover']['contents'])
        self.assertEqual(result['tokens'][0]['type'],'variable')
        completion=next(c for c in analyze('NotImpl')['completions'] if c['label']=='NotImplemented')
        self.assertEqual(completion['type'],'variable');self.assertEqual(completion['apply'],'NotImplemented')

    def test_constant_alias_and_shadowing(self):
        self.assertEqual(self.result('let answer=NotImplemented'),'NotImplemented')
        self.assertEqual(self.result('let NotImplemented=42;let answer=NotImplemented'),'Int')

    def test_primitive_results_preserved(self):
        for expression,expected in [('1+2','Int'),('1/2','Number'),('"a"+"b"','String'),('[1]+[2]','List'),('-2','Int'),('abs(-2)','Int'),('pow(2,3)','Number'),('floordiv(7,2)','Number')]:
            with self.subTest(expression=expression):self.assertEqual(self.result('let answer='+expression),expected)

    def test_stable_arithmetic_contract(self):
        self.assertEqual(self.result('class C{fn __add__(self,other)->Int{42}}let answer=C()+1'),'Int')
        self.assertEqual(self.result('class C{fn __neg__(self)->String{"minus"}}let answer=-C()'),'String')
        self.assertEqual(self.result('class C{fn __abs__(self)->Int{42}}let f=abs;let answer=f(x=C())'),'Int')
        self.assertEqual(self.result('class C{fn __pow__(self,n)->String{"x"}}let answer=pow(exponent=2,base=C())'),'String')

    def test_async_arithmetic_carries_awaited_contract(self):
        source='class C{async fn __add__(self,other)->Int{42}}let task=C()+1;let answer=await task'
        self.assertEqual(self.result(source,'task'),'Task');self.assertEqual(self.result(source),'Int')

    def test_reflected_or_fallback_unknown(self):
        for source in ['class C{fn __radd__(self,n)->Int{42}}let answer=1+C()',
                       'class C{fn __add__(self,n)->NotImplemented{NotImplemented}}let answer=C()+1',
                       'class C{fn __add__(self,n){42}}let answer=C()+1',
                       'class A{fn __add__(self,n)->Int{42}}class B(A){fn __radd__(self,n)->String{"x"}}let answer=A()+B()']:
            with self.subTest(source=source):self.assertEqual(self.result(source),'Any')

    def test_mutable_and_nominal_subclasses_unknown(self):
        self.assertEqual(self.result('class C{var __add__=fn(n)->Int{42}}let answer=C()+1'),'Any')
        self.assertEqual(self.result('class C{fn __add__(self,n)->Int{42}}var c=C();let answer=c+1'),'Any')
        source='class C{fn __add__(self,n)->Int{42}}fn use(c:C){let answer=c+1;answer}'
        self.assertEqual(self.result(source),'Any')

    def test_dynamic_descriptor_does_not_run(self):
        source='class D{fn __get__(self,obj,owner)->Function{throw "must not run"}}class C{let __add__=D()}let answer=C()+1'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            self.assertEqual(self.result(source),'Any')

    def test_strict_result_builtins(self):
        for expression,expected in [('bool(true)','Bool'),('contains([1],1)','Bool'),('length([1])','Int'),('1==2','Bool'),('!true','Bool')]:
            self.assertEqual(self.result('let answer='+expression),expected)

    def test_nested_operator_analysis_bounded(self):
        original=Analysis.walk;count=[0]
        def bounded(this,*args,**kwargs):
            count[0]+=1
            self.assertLess(count[0],150)
            return original(this,*args,**kwargs)
        with patch.object(Analysis,'walk',bounded):self.result('let answer='+'abs('*20+'1'+')'*20)
        self.assertLess(count[0],60)

    def test_soft_keyword_formatting_preserves_semantics(self):
        sources=['async fn f(){42} await (f())',
                 'fn* values(){yield (1+2);yield from ([4,5])} collect(values())',
                 'fn await(x){x} await(42)',
                 'fn yield(x){x} yield(42)',
                 'let obj={await:fn(x){x}};obj.await(42)']
        for source in sources:
            with self.subTest(source=source):
                formatted=format_source(source)
                self.assertEqual(plain(Parser(formatted).program()),plain(Parser(source).program()))
                self.assertEqual(format_source(formatted),formatted)
        self.assertIn('await (',format_source(sources[0]))
        self.assertIn('await(42)',format_source(sources[2]))

    def test_marker_preview_uses_identity_only(self):
        self.assertEqual(preview(NOT_IMPLEMENTED),{'$notImplemented':True})
        values=locals_snapshot(Evaluator().env)
        item=next(v for v in values if v['name']=='NotImplemented')
        self.assertEqual(item['type'],'NotImplemented');self.assertEqual(item['value'],{'$notImplemented':True})
        class Hostile:
            def __eq__(self,other):raise AssertionError('equality called')
            def __bool__(self):raise AssertionError('bool called')
            def __repr__(self):raise AssertionError('repr called')
        self.assertEqual(preview(Hostile()),{'type':'Hostile'})

    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_structural_controls(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/operator_controls.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')
