"""Structural editing consumes the actual context IR schema and templates."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from chaos.language import Evaluator
from chaos.program import SHAPES, parse_program, templates, format_program, project_program

ROOT=Path(__file__).resolve().parent.parent

class ContextUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_real_schema_controls_edit_and_validate_context(self):
        source='class C{fn __enter__(self){42}fn __exit__(self,error){null}}with C() as resource{resource}'
        fixture={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(source)}
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/context_controls.cjs')],input=json.dumps(fixture),cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout);self.assertEqual(report['status'],'passed')
        edited=report['program'];self.assertEqual(Evaluator().run(format_program(edited)),42)
        self.assertEqual(edited['root']['statements'][1]['name'],'renamed')
        self.assertEqual({e['slot'] for e in project_program(edited)['edges'] if e['source']==edited['root']['statements'][1]['id']},{'manager','body'})
        for helper in report['helpers']:
            program=parse_program('class C{}');program['root']['statements'][0]['body']['statements']=[helper]
            format_program(program)
