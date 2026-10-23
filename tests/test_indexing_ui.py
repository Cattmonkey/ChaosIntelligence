"""Pure item intelligence, inert Slice preview and real interpreter stops."""
import json
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch
from chaos import Framework, Settings
from chaos.ide_language import Analysis, analyze
from chaos.language import Evaluator
from chaos.debugger import preview
from chaos.indexing import Slice

ROOT=Path(__file__).resolve().parent.parent


class IndexingEditorTests(unittest.TestCase):
    def kind(self,source,name='answer',**kwargs):
        result=analyze(source,**kwargs)
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return next(item['type'] for item in result['symbols'] if item['name']==name)

    def test_native_index_and_slice_types(self):
        for expression,expected in [('[1,2][0]','Any'),('[1,2][:]','List'),('"text"[1]','String'),('"text"[::-1]','String'),('{x:42}["x"]','Int')]:
            with self.subTest(expression=expression):self.assertEqual(self.kind('let answer='+expression),expected)

    def test_item_hook_does_not_replace_attribute_intelligence(self):
        base='class C{let x="field";fn __getitem__(self,key)->Int{42}}let c=C();'
        self.assertEqual(self.kind(base+'let answer=c.x'),'String')
        self.assertEqual(self.kind(base+'let answer=c.["x"]'),'String')
        self.assertEqual(self.kind(base+'let answer=c["x"]'),'Int')
        self.assertEqual(self.kind('class C{let x="fallback"}let answer=C()["x"]'),'String')

    def test_task_and_stream_results_are_not_implicitly_consumed(self):
        for flavor,expected in [('fn','Int'),('async fn','Task'),('fn*','Generator'),('async fn*','AsyncGenerator')]:
            source='class C{'+flavor+' __getitem__(self,key)->Int{42}}let answer=C()[0]'
            with self.subTest(flavor=flavor):self.assertEqual(self.kind(source),expected)
        self.assertEqual(self.kind('class C{async fn __getitem__(self,key)->Int{42}}let answer=await C()[0]'),'Int')

    def test_dynamic_hooks_subclasses_and_descriptors_stay_unknown(self):
        for source in ['class C{var __getitem__=fn(k){42}}let answer=C()[0]',
                       'class C{fn __getitem__(self,k)->Int{42}}fn use(c:C){let answer=c[0];answer}',
                       'class D{fn __get__(self,obj,owner)->Function{throw "no"}}class C{let __getitem__=D()}let answer=C()[0]']:
            with self.subTest(source=source),patch.object(Evaluator,'run',side_effect=AssertionError('executed source')):self.assertEqual(self.kind(source),'Any')

    def test_explicit_api_aliases_keywords_and_shadowing(self):
        base='class C{fn __getitem__(self,k)->Int{42}}let c=C();'
        self.assertEqual(self.kind(base+'let lookup=getitem;let answer=lookup(key=0,value=c)'),'Int')
        self.assertEqual(self.kind('fn getitem(value,key)->String{"own"}let answer=getitem(1,2)'),'String')
        for expr in ['setitem(input,0,1)','delitem(input,0)']:
            self.assertEqual(self.kind('let answer='+expr),'Null')

    def test_slice_fields_completion_contract_and_inert_preview(self):
        result=analyze('let spec=slice_key(1,null,-1);spec.')
        fields={item['label']:item['detail'] for item in result['completions']}
        for key in ('start','stop','step'):self.assertIn('Int | Null',fields[key])
        self.assertEqual(self.kind('fn use(s:Slice){s};let answer=slice_key()'),'Slice')
        self.assertEqual(self.kind('let answer=slice_indices(slice_key(),3)'),'List')
        self.assertTrue(any(d['code']=='immutable_binding' for d in analyze('let s=slice_key();s.start=3')['diagnostics']))
        self.assertEqual(preview(Slice(None,5,-1)),{'type':'Slice','start':None,'stop':5,'step':-1})
        self.assertEqual(self.kind('let answer=slice_key()["start"]'),'Any')

    def test_write_does_not_borrow_property_readonly_rule(self):
        result=analyze('class C{let x=1;fn __setitem__(self,key,item){null}}let c=C();c["x"]=2')
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(self.kind('class C{fn __setitem__(self,k,v){null}}let answer=do{C()[0]=1}'),'Null')

    def test_mutable_item_slot_deletion_has_unknown_assignment_result(self):
        source='class C{var x=1;var __setitem__=fn(k,v){null}}delattr(C,"__setitem__");let answer:Int=do{C()["x"]=42};answer'
        self.assertEqual(Evaluator().run(source),42)
        self.assertEqual(self.kind(source),'Int')

    def test_naming_hook_can_delete_mutable_item_slot(self):
        source='class D{fn __set_name__(self,owner,name){delattr(owner,"__setitem__");null}}class C{let marker=D();var x=1;var __setitem__=fn(k,v){null}}let answer:Int=do{C()["x"]=42};answer'
        self.assertEqual(Evaluator().run(source),42)
        self.assertEqual(self.kind(source),'Int')

    def test_legacy_version_and_import_versions(self):
        source='class C{let x="field";fn __getitem__(self,k)->Int{42}}let answer=C()["x"]'
        self.assertEqual(self.kind(source,language_versions={'main.chaos':1}),'String')
        self.assertEqual(self.kind(source,language_versions={'main.chaos':2}),'Int')
        module='class C{let x="field";fn __getitem__(self,k)->Int{42}}let found=C()["x"];export{found}'
        for version,expected in [(1,'String'),(2,'Int')]:
            self.assertEqual(self.kind('import "lib.chaos" as L;let answer=L.found',files={'lib.chaos':module},language_versions={'lib.chaos':version}),expected)

    def test_analysis_walk_is_linear_and_bounds_walk_once(self):
        original=Analysis.walk;count=[0]
        def bounded(this,*args,**kwargs):count[0]+=1;self.assertLess(count[0],180);return original(this,*args,**kwargs)
        with patch.object(Analysis,'walk',bounded):analyze('let answer='+'getitem('*15+'[]'+',slice_key())'*15)
        self.assertLess(count[0],80)

    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_controls(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/indexing_controls.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')

    def test_real_item_get_and_delete_breakpoints_slice_locals(self):
        source='class C{\n fn __getitem__(self,key){\n  key.start\n }\n fn __delitem__(self,key){\n  null\n }\n}\nlet c=C();let answer=c[1:5:2];delitem(c,0);answer'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            p=app.create_project('item-debug',source=source)
            run=app.start_debug(p['id'],p['version'],pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3},{'file':'main.chaos','line':6}])
            def paused(after=0):
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    state=run.debugger()
                    if state['status']=='paused' and state['pauseId']>after:return state
                    if state['status'] not in ('paused','running'):self.fail(str(run.get()))
                    time.sleep(.005)
                self.fail('interpreter did not pause')
            try:
                first=paused();self.assertEqual(first['frames'][0]['name'],'__getitem__')
                key=next(v for v in first['frames'][0]['locals'] if v['name']=='key')
                self.assertEqual(key['value'],{'type':'Slice','start':1,'stop':5,'step':2})
                run.debug_command('continue',pause_id=first['pauseId']);second=paused(first['pauseId'])
                self.assertEqual(second['frames'][0]['name'],'__delitem__')
                run.debug_command('continue',pause_id=second['pauseId']);self.assertEqual(run.result(3),1)
            finally:
                if run.get()['status']=='running':run.cancel()

