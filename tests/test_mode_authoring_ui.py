"""Structured mode edits retain references, provenance and concurrent drafts."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ModeAuthoringUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js unavailable')
    def test_structured_controls(self):
        result = subprocess.run([shutil.which('node'), str(ROOT / 'tests/mode_authoring_controls.cjs'), str(ROOT)],
                                cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
