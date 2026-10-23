"""New syntax shares the existing structural-editor child traversal."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parent.parent

@unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
class DescriptorsUI(unittest.TestCase):
    def test_structural_editing_contract(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/descriptor_controls.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')
