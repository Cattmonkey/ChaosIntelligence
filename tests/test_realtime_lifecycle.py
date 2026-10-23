"""Regression coverage for assistant terminal tails and cached-page recovery."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
class RealtimeLifecycleTests(unittest.TestCase):
    def test_terminal_tail_cancel_and_page_restore(self):
        result = subprocess.run(
            [shutil.which('node'), str(ROOT / 'tests/realtime_lifecycle_controls.cjs'), str(ROOT)],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['cases'], 6)


if __name__ == '__main__':
    unittest.main()
