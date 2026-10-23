"""Editor identity, whitespace edits, selection and real CodeMirror undo contracts."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

NODE = shutil.which('node')
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(NODE, 'Node.js is not available')
class EditorReadabilityUI(unittest.TestCase):
    def test_readability_editor_contracts(self):
        result = subprocess.run([NODE, str(ROOT / 'tests' / 'editor_readability_controls.cjs')],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
