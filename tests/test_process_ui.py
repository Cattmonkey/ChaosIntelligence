import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator
ROOT=Path(__file__).resolve().parent.parent
class ProcessEditorTests(unittest.TestCase):
    def kind(self,source):
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return next(s['type'] for s in result['symbols'] if s['name']=='answer')
    def test_process_callable_signature_and_contract(self):
        self.assertEqual(self.kind('let answer=process_from(input)'),'Process')
        self.assertEqual(self.kind('let p=process_from(input);let answer=p("counter")'),'Task')
        self.assertEqual(analyze('let p:Function=process_from(input);p("counter",null)')['diagnostics'],[])
        self.assertTrue(any(d['code']=='argument_error' for d in analyze('let p=process_from(input);p()')['diagnostics']))
        self.assertTrue(any(d['code']=='type_mismatch' for d in analyze('let p:Process={};null')['diagnostics']))
    def test_info_and_step_fields(self):
        self.assertEqual({v['label'] for v in analyze('let p=process_info(input);p.')['completions']},{'kind','name','module','export','intervalSeconds','maxTurns','roles'})
        self.assertEqual({v['label'] for v in analyze('let next=process_step(input);next.')['completions']},{'state','output','done'})
    def test_effects_are_cold_and_awaited_records(self):
        for expression in ['process_start("p",input)','process_send("p",42)','process_status("p")','process_control("p",1,"pause")','process_configure("p",1,input)','process_reconcile("p",1,null)']:
            with self.subTest(expression=expression):
                self.assertEqual(self.kind('let answer='+expression),'Task')
                self.assertEqual(self.kind('let answer=await '+expression),'Record')
    def test_alias_and_shadowing_are_normal_language(self):
        self.assertEqual(self.kind('let from=process_from;let answer=from(input)'),'Process')
        self.assertEqual(self.kind('fn process_from(value)->String{"custom"};let answer=process_from(input)'),'String')
    def test_analysis_does_not_execute_process(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('no execution')):
            self.assertEqual(self.kind('let answer=process_source(process_from(input))'),'String')
    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_controls(self):
        subprocess.run([shutil.which('node'),str(ROOT/'tests/process_controls.cjs'),str(ROOT)],capture_output=True,text=True,check=True,timeout=20)
