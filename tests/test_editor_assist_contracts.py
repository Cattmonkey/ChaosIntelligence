"""Shared completion edits use actual bundled CodeMirror states without a DOM."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js unavailable')
class EditorAssistContracts(unittest.TestCase):
    def test_real_completion_ranges_multicursors_and_stale_boundaries(self):
        result = subprocess.run(
            [shutil.which('node'), str(ROOT / 'tests/editor_assist_controls.cjs'), str(ROOT)],
            cwd=ROOT, text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['passed'])
        self.assertTrue(report['actualCodeMirror'])
        self.assertGreaterEqual(report['checks'], 21)
        self.assertFalse(report['dom'])
        self.assertFalse(report['network'])
