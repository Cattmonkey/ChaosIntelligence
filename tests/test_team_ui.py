import unittest,subprocess,shutil
from pathlib import Path
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator
ROOT=Path(__file__).resolve().parent.parent
class TeamEditorTests(unittest.TestCase):
    def kind(self,source):
        result=analyze(source);self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return next(s['type'] for s in result['symbols'] if s['name']=='answer')
    def test_team_nominal_and_function_call_task(self):
        self.assertEqual(self.kind('let answer=team_from(input)'),'Team')
        self.assertEqual(self.kind('let t=team_from(input);let answer=t(42)'),'Task')
        self.assertTrue(any(x['code']=='argument_error' for x in analyze('let t=team_from(input);t()')['diagnostics']))
    def test_role_callables_are_cold(self):
        self.assertEqual(self.kind('let answer=team_role(input,"worker")'),'Function')
        self.assertEqual(self.kind('let role=team_role(input,"worker");let answer=role(42)'),'Task')
        self.assertEqual(self.kind('let role=team_role(input,"worker");let answer=await role(42)'),'Any')
    def test_function_contract(self):
        self.assertEqual(self.kind('fn use(t:Team){t(42)};let answer=team_bindings(input)'),'Record')
        self.assertEqual(analyze('fn use(t:Function){t(42)};use(team_from(input))')['diagnostics'],[])
    def test_info_fields_and_source(self):
        result=analyze('let info=team_info(input);info.')
        self.assertEqual({x['label'] for x in result['completions']},{'kind','name','entry','roles'})
        self.assertEqual(self.kind('let answer=team_source(input)'),'String')
    def test_builtin_alias_and_shadowing(self):
        self.assertEqual(self.kind('let create=team_from;let answer=create(input)'),'Team')
        self.assertEqual(self.kind('fn team_from(value)->String{"custom"}let answer=team_from(input)'),'String')
    def test_analyzer_never_executes_team_or_role(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('not pure')):
            self.assertEqual(self.kind('let t=team_from(input);let answer=team_source(t)'),'String')
    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_controls(self):
        subprocess.run([shutil.which('node'),str(ROOT/'tests/team_controls.cjs'),str(ROOT)],capture_output=True,text=True,check=True,timeout=20)
