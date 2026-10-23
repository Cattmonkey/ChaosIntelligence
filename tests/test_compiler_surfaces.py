"""One source compilation across SDK, HTTP, CLI, Agent draft tools and live IDE."""
from workbench_actions import button as workbench_button, view as workbench_view
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from chaos import Client, Framework, Settings, compile_program
from chaos.client import ValidationError
from chaos.server import make_server
from chaos.ide_language import analyze, format_source
from chaos.program import parse_program

ROOT=Path(__file__).resolve().parent.parent
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None


def bundle():
    return {'main.chaos':'import "lib/math.chaos" as M;M.answer(input)',
            'lib/math.chaos':'fn answer(x:Int)->Int{x+2};export {answer};'}


def identity(report):
    value=copy.deepcopy(report)
    for field in ('cacheHits','cacheMisses','cacheBypasses'):value['summary'].pop(field)
    for item in value['modules']:item.pop('cacheStatus')
    return value


class CompilerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_sdk_http_and_legacy_route_compile_identical_closed_snapshot(self):
        files=bundle();before=self.app.get_project('workspace');events=self.app.events('workspace')['lastEventId']
        direct=compile_program(files['main.chaos'],files=files)
        sdk=Framework.compile(files['main.chaos'],files=files)
        remote=self.client.compile(files['main.chaos'],files=files)
        request=urllib.request.Request(self.origin+'/api/language/compile',data=json.dumps({'source':files['main.chaos'],'files':files}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:legacy=json.load(response)
        self.assertEqual(identity(direct),identity(sdk));self.assertEqual(identity(direct),identity(remote));self.assertEqual(identity(direct),identity(legacy))
        self.assertEqual(remote['schema'],'chaos.compilation/1');self.assertEqual(remote['target'],'portable-ast')
        self.assertEqual({m['file'] for m in remote['modules']},set(files))
        self.assertEqual(self.app.get_project('workspace'),before);self.assertEqual(self.app.events('workspace')['lastEventId'],events)

    def test_compilation_never_runs_tools_agents_or_user_operators(self):
        source='agent worker{provider:"local",tools:[]};class A{fn __add__(self,x){throw "operator ran"}};tool("unknown",{});worker(42);A()+2;throw "ran"'
        with patch('chaos.language.Evaluator.run',side_effect=AssertionError('runtime execution')):
            report=self.client.compile(source)
        self.assertTrue(report['summary']['effects']);self.assertTrue(report['summary']['latentEffects'])
        self.assertEqual(report['entry'],'main.chaos')

    def test_strict_request_orphan_errors_and_dialects(self):
        for extra in ('position','file','target','capabilities','passed'):
            with self.subTest(extra=extra),self.assertRaises(ValidationError):
                self.client._request('POST','/language/compile',{'source':'42',extra:None})
        with self.assertRaises(ValidationError):self.client.compile('42',files={'bad.chaos':'fn ('})
        with self.assertRaises(ValidationError):self.client.compile('42',language_versions={'ghost.chaos':2})
        with self.assertRaises(ValidationError):self.client.compile('42',language_versions={'main.chaos':True})
        old=self.client.compile('[40,42][1]',language_versions={'main.chaos':1})
        new=self.client.compile('[40,42][1]',language_versions={'main.chaos':2})
        self.assertNotEqual(old['artifactHash'],new['artifactHash'])
        self.assertEqual(old['languageVersions'],{'main.chaos':1})
        with self.assertRaises(ValidationError):self.client.compile(42)

    def test_request_source_is_authoritative_entry_not_files_mirror(self):
        report=self.client.compile('42',files={'main.chaos':'43','other.chaos':'0'})
        main=next(m for m in report['modules'] if m['file']=='main.chaos')
        self.assertEqual(main['sourceHash'],hashlib.sha256(b'42').hexdigest())
        self.assertEqual(self.client.compile('42',entry='a.chaos')['entry'],'a.chaos')

    def test_openapi_reports_additive_contract_and_preserves_other_endpoints(self):
        spec=self.client._request('GET','/openapi.json');schema=spec['components']['schemas']
        self.assertFalse(schema['CompilationRequest']['additionalProperties'])
        self.assertEqual(set(schema['CompilationRequest']['properties']),{'source','entry','files','languageVersions'})
        self.assertEqual(spec['paths']['/language/compile']['post']['operationId'],'compileSource')
        self.assertEqual(spec['paths']['/language/analyze']['post']['operationId'],'analyzeSource')
        report=self.client.compile('42')
        self.assertTrue(set(schema['Compilation']['required'])<=set(report))

    @unittest.skipUnless(sync_playwright is not None,'Playwright unavailable')
    def test_browser_full_unsaved_bundle_stale_response_and_mobile(self):
        files=bundle();project=self.app.get_project('workspace');project.update(source=files['main.chaos'],files=files,entry='main.chaos')
        project=self.app.save_project(project,from_view='source');saved=copy.deepcopy(project)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.origin);workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
            page.evaluate('window.ChaosIDE.switchFile("lib/math.chaos")')
            draft='fn answer(x:Int)->Int{x+3};export {answer};'
            page.evaluate('(text)=>{const e=window.ChaosIDE.editor();e.dispatch({changes:{from:0,to:e.state.doc.length,insert:text}})}',draft)
            with page.expect_request('**/api/language/compile') as request:
                workbench_button(page,'compileSource')
            body=request.value.post_data_json
            self.assertEqual(body['source'],files['main.chaos']);self.assertEqual(body['files']['lib/math.chaos'],draft)
            page.wait_for_function('()=>!!document.querySelector("[data-compile-result]").dataset.artifactHash')
            expected=self.client.compile(files['main.chaos'],files={**files,'lib/math.chaos':draft},language_versions=body['languageVersions'])
            self.assertEqual(page.locator('[data-compile-result]').get_attribute('data-artifact-hash'),expected['artifactHash'])
            self.assertIn('lib/math.chaos',page.locator('.compilation-result').inner_text())
            self.assertEqual(self.app.get_project('workspace'),saved)
            # A change invalidates the visible result immediately, with no implicit save.
            page.evaluate('()=>{const e=window.ChaosIDE.editor();e.dispatch({changes:{from:e.state.doc.length,insert:" // changed"}})}')
            page.wait_for_function('()=>document.querySelector("[data-compile-status]").textContent.includes("已过期")')
            self.assertEqual(page.locator('[data-compile-result]').inner_text(),'')
            held=[]
            def hold(route):held.append(route)
            page.route('**/api/language/compile',hold)
            page.get_by_role('button',name='重新编译当前草稿').click()
            deadline=time.monotonic()+4
            while not held and time.monotonic()<deadline:page.wait_for_timeout(20)
            self.assertEqual(len(held),1)
            page.evaluate('window.ChaosIDE.switchFile("main.chaos")')
            page.wait_for_function('()=>document.querySelector("[data-compile-status]").textContent.includes("已过期")')
            try:held[0].fulfill(status=200,content_type='application/json',body=json.dumps(expected))
            except Exception:pass # Client cancellation may already have ended the request.
            page.unroute('**/api/language/compile',hold);page.wait_for_timeout(100)
            self.assertEqual(page.locator('[data-compile-result]').inner_text(),'')
            page.get_by_role('button',name='重新编译当前草稿').click()
            page.wait_for_function('()=>document.querySelector("[data-compile-result]").children.length>0')
            page.set_viewport_size({'width':390,'height':900})
            self.assertTrue(page.locator('#compilationDialog').evaluate('el=>el.getBoundingClientRect().left>=0&&el.getBoundingClientRect().right<=innerWidth'))
            self.assertTrue(page.locator('#compilationDialog').evaluate('el=>el.scrollWidth<=el.clientWidth'))
            self.assertEqual(errors,[]);self.assertEqual(self.app.get_project('workspace'),saved)
            browser.close()


class CompilerCLITests(unittest.TestCase):
    def test_cli_file_imports_stdin_errors_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);files=bundle()
            for name,text in files.items():path=base/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_text(text)
            before={p.relative_to(base).as_posix():p.read_bytes() for p in base.rglob('*.chaos')}
            command=[sys.executable,'-m','chaos','compile',str(base/'main.chaos'),'--root',str(base)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);report=json.loads(result.stdout)
            self.assertEqual(identity(report),identity(compile_program(files['main.chaos'],files=files)))
            self.assertEqual(before,{p.relative_to(base).as_posix():p.read_bytes() for p in base.rglob('*.chaos')})
            result=subprocess.run([sys.executable,'-m','chaos','compile','-'],input='42',capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['entry'],'main.chaos')
            result=subprocess.run([sys.executable,'-m','chaos','compile','-'],input='fn broken(',capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,1);self.assertEqual(result.stdout,'');self.assertIn('error',json.loads(result.stderr))

    def test_cli_formats_already_compiled_bundle_without_second_expansion(self):
        from chaos.cli import compile_main
        from chaos.modules import ProgramBundle
        from io import StringIO
        compiled=ProgramBundle('macro identity(x){x};identity!(42)')
        with patch('chaos.cli.load_file_bundle',return_value=compiled) as load,patch('chaos.macros.expand_trees',side_effect=AssertionError('recompiled')),patch('sys.stdout',new_callable=StringIO) as output:
            self.assertEqual(compile_main(['entry.chaos']),0)
        self.assertEqual(load.call_count,1);self.assertEqual(json.loads(output.getvalue())['schema'],'chaos.compilation/1')

    def test_language_compilation_signature_alias_shadow_and_format(self):
        text='let inspect=program_compile;let p=current_program();let report=inspect(p);report.'
        info=analyze(text);self.assertIn('artifactHash',{c['label'] for c in info['completions']})
        call='let p=current_program();program_compile(p)'
        info=analyze(call,position=call.rindex('p)')+1)
        self.assertIn('program_compile',info['signatureHelp']['label'])
        text='fn program_compile(value)->String{"local"};let answer=program_compile(1);answer'
        self.assertEqual(next(s['type'] for s in analyze(text)['symbols'] if s['name']=='answer'),'String')
        self.assertEqual(parse_program(text)['semanticHash'],parse_program(format_source(text))['semanticHash'])

    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_javascript_compile_request_uses_full_bundle_and_snapshot_contracts(self):
        subprocess.run([shutil.which('node'),str(ROOT/'tests/compiler_ui.cjs'),str(ROOT)],check=True,capture_output=True,text=True,timeout=20)


class CompilerAgentTests(unittest.TestCase):
    def test_agent_compiles_changed_proposal_without_save_execution_or_cache_budget_shortcut(self):
        import test_ide_agent as support
        fixture=support.IDEAgentTests();fixture.setUp()
        try:
            evidence=[]
            def inspect(request):
                messages=[m for m in request['messages'] if m.get('role')=='tool']
                report=json.loads(messages[-1]['content']);evidence.append(report)
                self.assertEqual(report['schema'],'chaos.compilation/1');self.assertEqual(report['summary']['cacheHits'],0)
                self.assertGreaterEqual(report['summary']['cacheBypasses'],1)
                self.assertEqual(report['entry'],'main.chaos')
                return 'Compiled the proposal without running it.'
            draft='tool("unknown",{});42'
            job=fixture.start([[('draft.write',{'path':'main.chaos','source':draft})],[('draft.compile',{})],inspect])
            result=fixture.wait(job)
            self.assertEqual(result['status'],'completed',result.get('error'));self.assertEqual(len(evidence),1)
            self.assertTrue(result['validation']['ok']);self.assertEqual(fixture.app.get_project('project')['source'],'input')
            self.assertEqual(result['checks'],[]);self.assertTrue(evidence[0]['summary']['effects'])
        finally:fixture.tearDown()

    def test_agent_rejects_compile_fields_and_exposes_current_guide(self):
        import test_ide_agent as support
        from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
        self.assertIn('compiler',LANGUAGE_REFERENCE_TOPICS)
        fixture=support.IDEAgentTests();fixture.setUp()
        try:
            def inspect(request):
                messages=[m for m in request['messages'] if m.get('role')=='tool']
                self.assertIn('Invalid draft tool arguments',json.loads(messages[-1]['content'])['error']['message'])
                return 'Rejected unrecognized compiler inputs.'
            result=fixture.wait(fixture.start([[('draft.compile',{'target':'native'})],inspect]))
            self.assertEqual(result['status'],'completed');self.assertEqual(result['changes'],[])
        finally:fixture.tearDown()
