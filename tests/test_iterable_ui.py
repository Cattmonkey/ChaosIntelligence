"""Shared code previews consume genuine iterable analysis metadata safely."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.ide_language import analyze

ROOT=Path(__file__).resolve().parent.parent


class IterableUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_shared_dynamic_preview_uses_real_hints_and_preserves_source(self):
        source='// 😃 中文\nfn* values(){yield 20;yield 21};async fn work(x){x*2};let jobs=map(work,values());await gather(jobs);fn add(a,b){a+b};add(*values());apply(add,values());'
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/iterable_controls.cjs')],input=json.dumps({'source':source,'analysis':analyze(source)}),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')


if __name__=='__main__':unittest.main()
