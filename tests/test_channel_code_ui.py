"""Shared dynamic code previews use the same inert UTF-16 reference hints."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.ide_language import analyze

ROOT=Path(__file__).resolve().parent.parent

class ChannelCodeUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_real_analysis_hints_dynamic_paint_copy_and_invalid_ranges(self):
        source='let text="😃 中文";let ch:Channel=channel();channel_send(ch,42);'
        fixture={'source':source,'analysis':analyze(source)}
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/channel_code_controls.cjs')],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(json.loads(result.stdout)['status'],'passed')
