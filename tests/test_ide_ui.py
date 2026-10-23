"""Deterministic IDE adapter contracts; browser behavior is covered by live acceptance."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

NODE = shutil.which('node')
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(NODE, 'Node.js is not available')
class IDEUIContract(unittest.TestCase):
    def test_editor_adapter_contracts(self):
        result = subprocess.run([NODE, str(ROOT / 'tests' / 'ide_controls.cjs')], cwd=ROOT,
                                capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
