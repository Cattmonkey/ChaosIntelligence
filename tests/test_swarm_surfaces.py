"""A real standard-language module reaches existing tools and distributions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from chaos import Framework
from chaos.ide_language import analyze, format_source
from chaos.language import Evaluator, Function, LanguageError
from chaos.modules import ProgramBundle, STDLIB
from chaos.program import parse_program, format_program
from chaos.release import ReleaseError, validate_wheel
from tests.test_release import fixture

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''import "std/swarm" as S;let initial=S.create({a:1});
await S.step(initial,{a:fn(context){context.state+1}})'''
EXPECTED={'states':{'a':2},'edges':[],'tick':1}


class SwarmSurfaceTests(unittest.TestCase):
    def test_bundled_source_contains_actual_function_exports(self):
        self.assertEqual(STDLIB['std/swarm'],(ROOT/'chaos/stdlib/swarm.chaos').read_text())
        exports=Evaluator().run('import "std/swarm" as S;S')
        self.assertEqual(set(exports),{'create','step','frames','run'})
        self.assertTrue(all(isinstance(value,Function) for value in exports.values()))
        self.assertEqual([exports[name].flavor for name in ('create','step','frames','run')],['fn','async_fn','async_gen_fn','async_fn'])

    def test_inert_module_analysis_signatures_and_formatted_ast(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed target')):
            result=analyze(SOURCE);self.assertEqual(result['diagnostics'],[])
            labels={c['label'] for c in analyze('import "std/swarm" as S;S.')['completions']}
            self.assertTrue({'create','step','frames','run'}<=labels)
            signature=analyze('import "std/swarm" as S;S.frames(')['signatureHelp']
            self.assertEqual([p['name'] for p in signature['parameters']],['initial','workers','rounds','prepare','rewire','measure','stop'])
            formatted=format_source(SOURCE)
            ir=parse_program(SOURCE);roundtrip=format_program(ir)
        self.assertEqual(Evaluator().run(formatted),EXPECTED)
        self.assertEqual(Evaluator().run(roundtrip),EXPECTED)
        self.assertEqual(parse_program(roundtrip)['semanticHash'],ir['semanticHash'])

    def test_alias_reexport_and_sealed_official_module(self):
        source='import "adapter.chaos" as A;await A.iterate(A.seed({a:1}),{a:fn(c){c.state+1}})'
        files={'adapter.chaos':'import "std/swarm" as S;let seed=S.create;let iterate=S.step;export{seed,iterate}'}
        self.assertEqual(Evaluator().run(source,files=files),EXPECTED)
        self.assertEqual(analyze(source,files=files)['diagnostics'],[])
        with self.assertRaises(LanguageError):ProgramBundle(SOURCE,{'std/swarm':'export{}'})

    def test_cli_and_sdk_execute_the_same_source(self):
        self.assertEqual(Framework.evaluate(SOURCE),EXPECTED)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(SOURCE)
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
            checked=subprocess.run([sys.executable,'-m','chaos','check',str(path)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(checked.returncode,0,checked.stderr)
            ran=subprocess.run([sys.executable,'-m','chaos','run',str(path)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(ran.returncode,0,ran.stderr);self.assertEqual(json.loads(ran.stdout),EXPECTED)

    def test_original_compiler_records_module_source_identity(self):
        bundle=ProgramBundle(SOURCE)
        self.assertIn('std/swarm',bundle.compilation_units)
        self.assertEqual(bundle.exports['std/swarm'],['create','step','frames','run'])
        tree=bundle.trees['std/swarm'];self.assertEqual(tree[0],'block')
        self.assertEqual(getattr(tree,'span',{})['file'],'std/swarm')

    def test_release_validation_requires_exact_chaos_resource_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=fixture(Path(directory)/'source');resource=root/'chaos/stdlib/swarm.chaos'
            resource.parent.mkdir();resource.write_text(STDLIB['std/swarm'])
            expected={}
            for name in ('chaos','web','vendor','docs','examples'):
                for path in (root/name).rglob('*'):
                    if path.is_file():
                        relative=str(path.relative_to(root));expected[(relative if name=='chaos' else 'chaos/'+relative)]=path.read_bytes()
            def wheel(values):
                target=Path(directory)/'fixture.whl'
                with zipfile.ZipFile(target,'w') as archive:
                    for name,data in values.items():archive.writestr(name,data)
                    archive.writestr('fixture.dist-info/METADATA','Name: fixture\nVersion: 0.6.0\n')
                return target
            validate_wheel(wheel(expected),root,'0.6.0')
            missing=dict(expected);missing.pop('chaos/stdlib/swarm.chaos')
            with self.assertRaisesRegex(ReleaseError,'missing'):validate_wheel(wheel(missing),root,'0.6.0')
            changed=dict(expected);changed['chaos/stdlib/swarm.chaos']=b'export{}'
            with self.assertRaisesRegex(ReleaseError,'differs'):validate_wheel(wheel(changed),root,'0.6.0')


if __name__=='__main__':unittest.main()
