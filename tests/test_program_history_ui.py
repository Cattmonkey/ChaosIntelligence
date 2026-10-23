import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator
ROOT=Path(__file__).resolve().parent.parent

class ProgramHistoryEditorTests(unittest.TestCase):
    def kind(self,source):
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return next(s['type'] for s in result['symbols'] if s['name']=='answer')
    def test_cold_history_and_revision(self):
        self.assertEqual(self.kind('let answer=program_history()'),'Task')
        self.assertEqual(self.kind('let answer=await program_history()'),'Record')
        self.assertEqual(self.kind('let answer=program_revision(1)'),'Task')
        self.assertEqual(self.kind('let answer=await program_revision(1)'),'Program')
    def test_page_completion_and_types(self):
        result=analyze('let page=await program_history();page.')
        self.assertEqual({x['label'] for x in result['completions']},{'items','nextCursor','currentVersion','firstAvailableVersion'})
        self.assertEqual(self.kind('let page=await program_history();let answer:Int=page.currentVersion'),'Int')
    def test_alias_and_lexical_shadow(self):
        self.assertEqual(self.kind('let read=program_revision;let answer=await read(2)'),'Program')
        self.assertEqual(self.kind('fn program_revision(v)->String{"local"}let answer=program_revision(2)'),'String')
    def test_history_program_is_callable(self):
        self.assertEqual(self.kind('let p=await program_revision(1);let answer=p(null)'),'Task')
    def test_required_revision_and_named_paging(self):
        self.assertTrue(any(d['code']=='argument_error' for d in analyze('program_revision()')['diagnostics']))
        self.assertEqual(self.kind('let answer=await program_history(limit=3,after=1)'),'Record')
    def test_analysis_never_executes(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis must remain pure')):
            self.assertEqual(self.kind('let answer=program_metadata(await program_revision(1))'),'Record')
    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_controls(self):
        subprocess.run([shutil.which('node'),str(ROOT/'tests/program_history_controls.cjs'),str(ROOT)],capture_output=True,text=True,check=True,timeout=20)
