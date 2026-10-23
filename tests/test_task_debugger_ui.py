"""Debugger task UI consumes actual language task_info records."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.language import Evaluator

ROOT = Path(__file__).resolve().parent.parent


class TaskDebuggerUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_read_only_task_cards_member_truncation_and_pause_transitions(self):
        tasks = Evaluator().run('async fn work(){42};let one=work();let joined=gather(map(fn(i){one},range(101)));[task_info(one),task_info(joined)]')
        self.assertEqual(tasks[1]['memberCount'], 101)
        self.assertTrue(tasks[1]['membersTruncated'])
        fixture = {'tasks':tasks,'total':103,'truncated':True}
        result = subprocess.run([shutil.which('node'),str(ROOT/'tests/task_debugger_controls.cjs')],
                                input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'passed')
