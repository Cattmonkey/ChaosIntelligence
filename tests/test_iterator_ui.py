"""User-iterator editor contracts and inert handle previews."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
from chaos.ide_language import Analysis, analyze, format_source
from chaos.language import Evaluator, Parser
from chaos.program import plain
from chaos.debugger import preview
from chaos.iteration import Iterator, AsyncIterator

ROOT=Path(__file__).resolve().parent.parent

class IteratorEditorTests(unittest.TestCase):
    def kind(self, source, name='answer'):
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return next(item['type'] for item in result['symbols'] if item['name']==name)

    def test_native_factories_and_identity(self):
        for expression,expected in [('iter([1,2])','Iterator'),('iter("hi")','Iterator'),('iter(iter([]))','Iterator'),('iter((fn*(){yield 1})())','Generator'),('aiter((async fn*(){yield 1})())','AsyncGenerator')]:
            with self.subTest(expression=expression):self.assertEqual(self.kind('let answer='+expression),expected)
        self.assertEqual(self.kind('fn use(value:AsyncIterator){let answer=aiter(value);answer}'),'AsyncIterator')

    def test_stable_user_factory_annotations(self):
        self.assertEqual(self.kind('class C{fn __iter__(self)->C{self}fn __next__(self)->Record{{done:true,value:null}}}let answer=iter(C())'),'Iterator')
        self.assertEqual(self.kind('class C{fn __aiter__(self)->C{self}async fn __anext__(self)->Record{{done:true,value:null}}}let answer=aiter(C())'),'AsyncIterator')
        self.assertEqual(self.kind('class C{fn* __iter__(self)->Int{yield "text";42}}let answer=iter(C())'),'Generator')
        self.assertEqual(self.kind('class C{async fn* __aiter__(self)->Int{yield "text";42}}let answer=aiter(C())'),'AsyncGenerator')

    def test_unknown_factory_stays_conservative(self):
        for source in ['class C{fn __iter__(self){self}}let answer=iter(C())',
                       'class C{var __iter__=fn(){null}}let answer=iter(C())',
                       'class C{fn __iter__(self)->C{self}}fn use(c:C){let answer=iter(c);answer}',
                       'class C{async fn __aiter__(self)->C{self}}let answer=aiter(C())']:
            with self.subTest(source=source):self.assertEqual(self.kind(source),'Any')

    def test_analysis_does_not_execute_factory_or_descriptor(self):
        source='class D{fn __get__(self,obj,owner)->Function{throw "no"}}class C{let __iter__=D()}let answer=iter(C())'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis evaluated source')):
            self.assertEqual(self.kind(source),'Any')

    def test_completion_records_for_sync_and_async_apis(self):
        for expression in ['next(input)','send(input,null)','throw_into(input,"x")','await anext(input)','await asend(input,null)','await athrow(input,"x")']:
            with self.subTest(expression=expression):
                result=analyze('let row='+expression+';row.')
                suggestions={item['label']:item for item in result['completions']}
                self.assertIn('done',suggestions);self.assertIn('Bool',suggestions['done']['detail'])
                self.assertIn('value',suggestions);self.assertIn('Any',suggestions['value']['detail'])
                self.assertEqual(self.kind('let row='+expression+';let answer=row.done'),'Bool')
                self.assertEqual(self.kind('let row='+expression+';let answer=row.value'),'Any')

    def test_cold_task_has_no_completion_record_before_await(self):
        self.assertEqual(self.kind('let answer=anext(input)'),'Task')
        result=analyze('let pending=anext(input);pending.')
        self.assertNotIn('done',[item['label']for item in result['completions']])
        result=analyze('anext',position=3)
        self.assertIn('冷 Task',result['hover']['contents'])

    def test_builtin_alias_and_shadowing(self):
        self.assertEqual(self.kind('let factory=iter;let answer=factory(value="x")'),'Iterator')
        self.assertEqual(self.kind('let step=anext;let row=await step(input);let answer=row.done'),'Bool')
        self.assertEqual(self.kind('fn iter(value)->String{"custom"}let answer=iter(1)'),'String')
        result=analyze('fn next(x){x};let row=next(input);row.')
        self.assertNotIn('done',[item['label']for item in result['completions']])

    def test_loop_element_types_do_not_reuse_generator_final_type(self):
        self.assertEqual(self.kind('for letter in "hi"{letter}','letter'),'String')
        self.assertEqual(self.kind('for value in [1,2]{value}','value'),'Any')
        self.assertEqual(self.kind('fn* g()->Int{yield "text";42}for value in g(){value}','value'),'Any')
        self.assertEqual(self.kind('async fn* g()->Int{yield "text";42}async for value in g(){value}','value'),'Any')

    def test_formatter_and_ir_use_existing_nodes(self):
        source='class C{fn __iter__(self){self}fn __next__(self){{done:true,value:null}}async fn __anext__(self){{done:true,value:null}}}let it=iter(C());next(it)'
        formatted=format_source(source)
        self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
        self.assertEqual(format_source(formatted),formatted)

    def test_protocol_analysis_has_no_repeated_walk(self):
        original=Analysis.walk;count=[0]
        def bounded(this,*args,**kwargs):
            count[0]+=1;self.assertLess(count[0],150)
            return original(this,*args,**kwargs)
        with patch.object(Analysis,'walk',bounded):self.kind('let answer='+'iter('*20+'[]'+')'*20)
        self.assertLess(count[0],60)

    def test_preview_only_trusted_inert_fields(self):
        class Hostile:
            def __repr__(self):raise AssertionError('repr called')
            def __iter__(self):raise AssertionError('iteration called')
            def __getattribute__(self,name):raise AssertionError('target accessed')
        for kind in (Iterator,AsyncIterator):
            handle=object.__new__(kind)
            handle.__dict__.update(id='fixture',state='suspended',busy=True,close_attempted=False,target=Hostile(),owner=Hostile(),result=Hostile(),epoch=99)
            self.assertEqual(preview(handle),{'type':kind.__name__,'id':'fixture','state':'suspended','busy':True,'close_attempted':False})
        class Impostor:
            @property
            def __dict__(self):raise AssertionError('untrusted property')
        self.assertEqual(preview(Impostor()),{'type':'Impostor'})

    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_structural_controls(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/iterator_controls.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')
