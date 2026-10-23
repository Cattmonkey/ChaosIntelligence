"""Resumable iteration uses unchanged source, metadata and external interfaces."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
from chaos import Client,Framework,Settings
from chaos.cli import main
from chaos.client import ConflictError
from chaos.code_presentation import render_code
from chaos.documentation import Documentation
from chaos.ide_language import analyze
from chaos.language import Evaluator,Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import SHAPES
from chaos.program import decode_program,format_program,parse_program,plain
from chaos.release import ReleaseError,validate_wheel
from chaos.server import make_server
from chaos.source_format import format_source
from tests.test_native_jit import NATIVE
from tests.test_native_iteration_integration import SOURCE,SOLVER

ROOT=Path(__file__).resolve().parent.parent


class NativeIterationSurfaces(unittest.TestCase):
    def test_actual_backend_and_unchanged_diagnostic_schemas(self):
        target='native-flow'if NATIVE else 'auto-flow'
        report=Framework.evaluate(SOLVER.replace('auto-flow',target)+'''let value=fast([40,1]);
{value:value,flow:compile_flow_info(fast),old:compile_info(fast),optimization:compile_optimization_info(fast)}''')
        self.assertEqual(report['value'],42)
        self.assertEqual(len(report['flow']),11);self.assertEqual(len(report['old']),12);self.assertEqual(len(report['optimization']),13)
        self.assertEqual(report['flow']['abi'],'pc-host-events/1')
        self.assertEqual(report['flow']['status'],'native'if NATIVE else 'portable')
        self.assertGreater(report['flow']['nativeResumes'if NATIVE else 'portableResumes'],0)
        self.assertGreater(report['flow']['checkpointCount'],0)

    def test_inert_signatures_flow_fields_and_original_for_scope(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed protocol')):
            report=analyze(SOLVER+'fast(')
            self.assertEqual([p['name']for p in report['signatureHelp']['parameters']],['items'])
            lsp=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':SOLVER+'fast('})
            self.assertIn('items',lsp['signatures'][0]['label'])
            fields={x['label']for x in analyze(SOLVER+'compile_flow_info(fast).')['completions']}
            self.assertEqual(fields,{field.name for field in SHAPES['CompiledFlowInfo']})
            params=analyze('compile_function(')['signatureHelp']['parameters']
            self.assertEqual([p['name']for p in params],['fn','target'])
            shadow='let compile_function=fn(text:String)->String{text};let value=compile_function("local")'
            self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='value'),'String')

    def test_source_ir_macro_format_and_debug_keep_original_nodes(self):
        macro='''macro make(values){quote{fn(){var result=0;for (a,b) in unquote(values){result=result+a+b};result}}}
let original=make!([(20,1),(20,1)]);let fast=compile_function(original,target="auto-flow");fast()'''
        for source in (SOURCE,macro):
            for version in (1,2):
                program=parse_program(source,language_version=version)
                for text in (format_program(program),format_source(source,language_version=version,line_width=48)):
                    self.assertEqual(Framework.evaluate(text),42)
                    self.assertEqual(plain(Parser(text).program()),plain(decode_program(program)))
        formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
        self.assertIn('chaos-token-function',render_code(formatted,'chaos'))
        seen=[]
        def checkpoint(node,env,stack):
            location=getattr(node,'span',{})
            if location:seen.append(location)
        evaluator=Evaluator(debug_hook=checkpoint)
        value,info=evaluator.run(SOLVER+'[fast([40,1]),compile_flow_info(fast)]');self.assertEqual(value,42)
        self.assertTrue(seen);self.assertTrue(all(item['line']>0 for item in seen))
        self.assertGreater(info['portableResumes'],0)

    def test_cli_sdk_http_ir_cas_preserve_iteration_snapshot(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);path=root/'iteration.chaos';path.write_text(SOURCE)
            output=io.StringIO();errors=io.StringIO()
            with patch.object(sys,'argv',['chaos','run',str(path)]),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue()),42);self.assertEqual(errors.getvalue(),'')
            self.assertEqual(Framework.evaluate(path.read_text()),42)
            server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('iteration-surface',source=SOURCE,capabilities=[])
                old=client.start(p['id'],p['version'],None);self.assertEqual(old.result(),42)
                program=client.program(p['id'])['program']
                def literal(value):
                    if isinstance(value,dict):
                        if value.get('value')==40 and 'id'in value:return value
                        for child in value.values():
                            found=literal(child)
                            if found:return found
                    elif isinstance(value,list):
                        for child in value:
                            found=literal(child)
                            if found:return found
                target=literal(program['root']);self.assertIsNotNone(target)
                updated=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':41}])
                self.assertEqual(client.start(p['id'],updated['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
                with self.assertRaises(ConflictError):client.edit_program(p['id'],p['version'],operations=[])
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_package_requires_same_flow_runtime_guide_and_example(self):
        topic=next(x for x in Documentation().entries()if x['id']=='native-flow')
        self.assertEqual(topic['path'],'NATIVE_FLOW.md')
        self.assertIn('for',(ROOT/'docs/NATIVE_FLOW.md').read_text())
        self.assertEqual(Framework.evaluate((ROOT/'examples/native-iteration.chaos').read_text()),42)
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory)/'source'
            for folder in ('chaos','web','vendor/deepseek','docs','examples'):(root/folder).mkdir(parents=True,exist_ok=True)
            (root/'chaos/__init__.py').write_text('__version__="0.6.0"\n')
            for resource in ('chaos/jit_flow.py','docs/NATIVE_FLOW.md','examples/native-iteration.chaos'):(root/resource).write_bytes((ROOT/resource).read_bytes())
            expected={}
            for name in ('chaos','web','vendor','docs','examples'):
                for path in (root/name).rglob('*'):
                    if path.is_file():
                        relative=str(path.relative_to(root));expected[relative if name=='chaos'else 'chaos/'+relative]=path.read_bytes()
            def wheel(values):
                target=Path(directory)/'fixture.whl'
                with zipfile.ZipFile(target,'w')as archive:
                    for name,data in values.items():archive.writestr(name,data)
                    archive.writestr('fixture.dist-info/METADATA','Name: fixture\nVersion: 0.6.0\n')
                return target
            validate_wheel(wheel(expected),root,'0.6.0')
            missing=dict(expected);missing.pop('chaos/jit_flow.py')
            with self.assertRaisesRegex(ReleaseError,'missing'):validate_wheel(wheel(missing),root,'0.6.0')
            for name in ('chaos/jit_flow.py','chaos/docs/NATIVE_FLOW.md','chaos/examples/native-iteration.chaos'):
                changed=dict(expected);changed[name]=b'altered resource'
                with self.subTest(resource=name),self.assertRaisesRegex(ReleaseError,'differs'):validate_wheel(wheel(changed),root,'0.6.0')


if __name__=='__main__':unittest.main()
