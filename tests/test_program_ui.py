import unittest,subprocess,shutil
from pathlib import Path
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator
ROOT=Path(__file__).resolve().parent.parent
class ProgramEditorTests(unittest.TestCase):
    def kind(self,source):
        result=analyze(source);self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return next(s['type'] for s in result['symbols'] if s['name']=='answer')
    def test_nominal_program_and_cold_call(self):
        self.assertEqual(self.kind('let answer=program_from(input)'),'Program')
        self.assertEqual(self.kind('let p=current_program();let answer=p(42)'),'Task')
        self.assertTrue(any(x['code']=='argument_error' for x in analyze('let p=current_program();p()')['diagnostics']))
    def test_reflection_fields(self):
        result=analyze('let info=program_info(current_program());info.')
        self.assertEqual({x['label'] for x in result['completions']},{'kind','entry','files','languageVersions'})
        self.assertEqual({x['label'] for x in analyze('let m=program_metadata(current_program());m.')['completions']},{'definitionHash','origin'})
    def test_program_edit_and_alias_not_callname(self):
        self.assertEqual(self.kind('let edit=program_edit;let answer=edit(input,"main.chaos",[])'),'Program')
        self.assertEqual(self.kind('fn program_from(value)->String{"custom"}let answer=program_from(input)'),'String')
    def test_effect_task_reports(self):
        self.assertEqual(self.kind('let answer=run_program(input,42)'),'Task')
        self.assertEqual(self.kind('let answer=await program_probe(input,[])'),'Record')
        self.assertEqual(self.kind('let answer=await program_adopt(input,input,[])'),'Record')
    def test_known_callable_contracts(self):
        for source in ['let p:Function=current_program();type_of(p)', 'let p=current_program();fn use(f:Function)->Function{f};use(p)', 'fn current()->Function{current_program()};current()', 'let p:Function=plan([]);null', 'let t:Function=team_from(input);null', 'class C{};let constructor:Function=C;null']:
            with self.subTest(source=source):self.assertEqual(analyze(source)['diagnostics'],[])
        for source in ['let p:Program={};null', 'let r:Record=current_program();null', 'let f:Function={};null']:
            with self.subTest(source=source):self.assertTrue(any(d['code']=='type_mismatch' for d in analyze(source)['diagnostics']))
        self.assertEqual(Evaluator().run('let p:Function=current_program();type_of(p)'),'Program')
    def test_static_analysis_no_execution(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('must remain pure')):
            self.assertEqual(self.kind('let answer=program_source(current_program())'),'String')
    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_controls(self):
        subprocess.run([shutil.which('node'),str(ROOT/'tests/program_controls.cjs'),str(ROOT)],capture_output=True,text=True,check=True,timeout=20)
