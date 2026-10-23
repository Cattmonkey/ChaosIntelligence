"""The actual installed std/iter source drives every language surface."""
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
from chaos.language import Evaluator,Function,Parser,LanguageError
from chaos.lsp_adapters import convert
from chaos.modules import ProgramBundle,STDLIB
from chaos.program import decode_program,format_program,parse_program,plain
from chaos.release import ReleaseError,validate_wheel
from chaos.server import make_server
from chaos.source_format import format_source

ROOT=Path(__file__).resolve().parent.parent
SYNC=('map','filter','flat_map','take','drop','take_while','drop_while','enumerate','zip','chain','batches','scan')
NAMES=set(SYNC)|{'a'+name for name in SYNC}|{'from_sync','count','repeat'}
SOURCE='''// Demand-driven composition uses the installed language module.
import "std/iter" as I;
let values=I.take(I.map(fn(value){value+2},[40]),1);
fold(fn(total,value){total+value},0,values)'''


class LazyStreamSurfaces(unittest.TestCase):
    def test_exact_bundled_functions_and_compiler_debug_source_identity(self):
        self.assertEqual(STDLIB['std/iter'],(ROOT/'chaos/stdlib/iter.chaos').read_text())
        exports=Evaluator().run('import "std/iter" as I;I')
        self.assertEqual(set(exports),NAMES)
        self.assertTrue(all(type(value)is Function for value in exports.values()))
        bundle=ProgramBundle(SOURCE);self.assertIn('std/iter',bundle.compilation_units)
        self.assertEqual(set(bundle.exports['std/iter']),NAMES)
        self.assertEqual(getattr(bundle.trees['std/iter'],'span',{})['file'],'std/iter')
        seen=[]
        def checkpoint(node,env,stack):
            location=getattr(node,'span',{})
            if location.get('file')=='std/iter':seen.append(location)
        self.assertEqual(Evaluator(debug_hook=checkpoint).run(SOURCE),42)
        self.assertTrue(seen);self.assertTrue(all(item['line']>0 for item in seen))
        with self.assertRaises(LanguageError):ProgramBundle(SOURCE,{'std/iter':'export{}'})

    def test_real_source_signatures_completion_lsp_and_inert_analysis(self):
        expected={'map':['mapper','source'],'take':['source','count'],'scan':['reducer','initial','source'],
                  'enumerate':['source','start'],'zip':['sources'],'repeat':['value','count']}
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed callback')):
            self.assertEqual({c['label']for c in analyze('import "std/iter" as I;I.')['completions']},NAMES)
            for name,parameters in expected.items():
                source='import "std/iter" as I;I.'+name+'(';report=analyze(source)
                self.assertEqual([p['name']for p in report['signatureHelp']['parameters']],parameters)
                lsp=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
                self.assertIn(parameters[0],lsp['signatures'][0]['label'])
            report=analyze('import "std/iter" as I;let stream=I.map(fn(x){x},[1]);let asyncStream=I.from_sync([1]);')
            types={s['name']:s['type']for s in report['symbols']}
            self.assertEqual([types[n]for n in ('stream','asyncStream')],['Generator','AsyncGenerator'])
            shadow='let I={map:fn(value:String)->String{value}};let text=I.map("local")'
            self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='text'),'String')

    def test_alias_reexport_format_ir_and_macro_share_original_nodes(self):
        files={'adapter.chaos':'import "std/iter" as I;let select=I.take;let transform=I.map;export{select,transform};'}
        alias='import "adapter.chaos" as A;collect(A.select(A.transform(fn(x){x+2},[40]),1))[0]'
        self.assertEqual(Framework.evaluate(alias,files=files),42)
        help=analyze(alias+';A.select(',files=files)['signatureHelp']
        self.assertEqual([p['name']for p in help['parameters']],['source','count'])
        macro='''macro pipe(module,values){quote{unquote(module).take(unquote(module).map(fn(x){x+2},unquote(values)),1)}}
import "std/iter" as I;collect(pipe!(I,[40]))[0]'''
        for source in (SOURCE,macro):
            for version in (1,2):
                program=parse_program(source,language_version=version)
                for text in (format_program(program),format_source(source,language_version=version,line_width=48)):
                    self.assertEqual(Framework.evaluate(text),42)
                    self.assertEqual(plain(Parser(text).program()),plain(decode_program(program)))
        formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
        self.assertIn('// Demand-driven',formatted);self.assertIn('chaos-token-function',render_code(formatted,'chaos'))

    def test_cli_sdk_http_cas_and_ir_edits_preserve_original_snapshot(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);path=root/'lazy.chaos';path.write_text(SOURCE)
            output=io.StringIO();errors=io.StringIO()
            with patch.object(sys,'argv',['chaos','run',str(path)]),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue()),42);self.assertEqual(errors.getvalue(),'')
            self.assertEqual(Framework.evaluate(path.read_text()),42)
            server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('lazy-surface',source=SOURCE,capabilities=[])
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
                # Reusing the old version cannot silently overwrite the new module.
                with self.assertRaises(ConflictError):client.edit_program(p['id'],p['version'],operations=[])
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_release_requires_exact_stdlib_source_and_installed_documentation(self):
        topic=next(x for x in Documentation().entries()if x['id']=='streams')
        self.assertEqual(topic['path'],'STREAMS.md')
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory)/'source'
            for folder in ('chaos','web','vendor/deepseek','docs','examples'):(root/folder).mkdir(parents=True,exist_ok=True)
            (root/'chaos/__init__.py').write_text('__version__="0.6.0"\n')
            resource=root/'chaos/stdlib/iter.chaos'
            resource.parent.mkdir();resource.write_text(STDLIB['std/iter'])
            (root/'docs/STREAMS.md').write_bytes((ROOT/'docs/STREAMS.md').read_bytes())
            (root/'examples/lazy-streams.chaos').write_bytes((ROOT/'examples/lazy-streams.chaos').read_bytes())
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
            missing=dict(expected);missing.pop('chaos/stdlib/iter.chaos')
            with self.assertRaisesRegex(ReleaseError,'missing'):validate_wheel(wheel(missing),root,'0.6.0')
            for name in ('chaos/stdlib/iter.chaos','chaos/docs/STREAMS.md','chaos/examples/lazy-streams.chaos'):
                changed=dict(expected);changed[name]=b'altered resource'
                with self.subTest(resource=name),self.assertRaisesRegex(ReleaseError,'differs'):validate_wheel(wheel(changed),root,'0.6.0')


if __name__=='__main__':unittest.main()
