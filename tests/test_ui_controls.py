"""Run the browser-control regression harness when Node.js is available."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


NODE = shutil.which('node')
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(NODE, 'Node.js is not available')
class UIControlsContract(unittest.TestCase):
    def test_graph_delete_guards_and_finally_structure(self):
        result = subprocess.run(
            [NODE, str(ROOT / 'tests' / 'ui_controls.cjs')],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['checks'], 19)


if __name__ == '__main__':
    unittest.main()
