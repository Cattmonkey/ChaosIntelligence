"""Compiled Functions keep one language identity across authoring and execution."""
from workbench_actions import button as workbench_button, view as workbench_view, run_option as workbench_run_option
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

from chaos import Client, Framework, Settings
from chaos.ide_language import analyze, format_source
from chaos.lsp_adapters import convert
from chaos.native_specs import SHAPES, spec_for
from chaos.program import parse_program, format_program
from chaos.server import make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None

SOURCE='fn add(x:Int,/,*,by:Int=2)->Int{x+by};let fast=compile_function(add);let answer=fast(40,by=2);{answer:answer,compilation:compile_info(fast)}'
EXPECTED_FIELDS={'schema','status','requestedTarget','backend','target','compilerVersion','parameterGuards','nodeCount','nativeCalls','interpreterCalls','fallbackReason','lastFallbackReason'}


class JITSignatureTests(unittest.TestCase):
    def help(self,text,**options):
        value=analyze(text,**options)['signatureHelp'];self.assertIsNotNone(value,text);return value

    def test_alias_keeps_parameter_modes_defaults_return_and_keyword_hint(self):
        prefix='fn add(x:Int,/,*,by:Int=2)->Int{x+by};let build=compile_function;let fast=build(add);'
        help_=self.help(prefix+'fast(40,by=')
        self.assertEqual([p['name'] for p in help_['parameters']],['x','by'])
        self.assertEqual([p['mode'] for p in help_['parameters']],['positional_only','keyword_only'])
        self.assertTrue(help_['parameters'][1]['optional']);self.assertEqual(help_['activeParameter'],1)
        self.assertIn('-> Int',help_['label'])
        result=analyze(prefix+'let answer=fast(40);answer')
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='answer'),'Int')
        self.assertTrue(any(d['code']=='argument_error' for d in analyze(prefix+'fast(x=40)')['diagnostics']))
        self.assertTrue(any(d['code']=='type_mismatch' for d in analyze(prefix+'fast("bad")')['diagnostics']))

    def test_qualified_import_and_named_factory_argument_keep_callable_metadata(self):
        files={'jit.chaos':'let build=compile_function;export{build};',
               'math.chaos':'fn plus(left:Int,right:Int=2)->Int{left+right};export{plus};'}
        prefix='import "jit.chaos" as J;import "math.chaos" as M;let fast=J.build(target="auto",fn=M.plus);'
        help_=self.help(prefix+'fast(40,',files=files)
        self.assertEqual([p['name'] for p in help_['parameters']],['left','right'])
        self.assertEqual(help_['activeParameter'],1)
        source=prefix+'let answer=fast(40);answer'
        self.assertEqual(next(s['type'] for s in analyze(source,files=files)['symbols'] if s['name']=='answer'),'Int')

    def test_decorator_and_alias_are_real_bindings_and_shadowing_is_ordinary(self):
        for decorator,prefix in [('compile_function',''),('build','let build=compile_function;')]:
            source=prefix+'@'+decorator+'\nfn add(x:Int,/,*,by:Int=2)->Int{x+by};'
            help_=self.help(source+'add(40,by=')
            self.assertEqual(help_['activeParameter'],1);self.assertEqual(help_['parameters'][0]['mode'],'positional_only')
            self.assertEqual(next(s['type'] for s in analyze(source+'let answer=add(40);answer')['symbols'] if s['name']=='answer'),'Int')
        source='fn compile_function(value)->String{"ordinary"};let answer=compile_function(42);answer'
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='answer'),'String')
        self.assertIsNone(analyze(source+';answer(')['signatureHelp'])
        source='fn compile_function(value){value};@compile_function fn f(x:Int)->Int{x};f('
        self.assertIsNone(analyze(source)['signatureHelp'])

    def test_dynamic_expansion_remains_unknown_and_does_not_invent_input_shape(self):
        source='fn add(x:Int)->Int{x+2};let fast=compile_function(*input);let answer=fast(40);answer'
        result=analyze(source)
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='fast'),'Function')
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='answer'),'Any')
        help_=self.help(source+';fast(');self.assertEqual(help_['parameters'],[]);self.assertIsNone(help_['activeParameter'])

    def test_compilation_info_fields_and_factory_signature_share_native_spec(self):
        self.assertEqual(spec_for('compile_function').inference_rule,'compiled_function')
        self.assertEqual({f.name for f in SHAPES['CompiledFunctionInfo']},EXPECTED_FIELDS)
        source='fn add(x:Int)->Int{x+2};let fast=compile_function(add);let metadata=compile_info(fast);metadata.'
        self.assertEqual({c['label'] for c in analyze(source)['completions']},EXPECTED_FIELDS)
        help_=self.help('compile_function(input,target=')
        self.assertEqual([p['name'] for p in help_['parameters']],['fn','target']);self.assertEqual(help_['activeParameter'],1)
        self.assertIn('"auto"',help_['label'])
        help_=self.help('let inspect=compile_info;inspect(');self.assertEqual(help_['parameters'][0]['name'],'fn')

    def test_analysis_does_not_execute_target_or_initialize_llvm(self):
        # A clean subprocess detects accidental LLVM imports hidden by test order.
        script='''import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name=="llvmlite" or name.startswith("llvmlite."):raise AssertionError("analysis initialized LLVM")
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from chaos.ide_language import analyze
from chaos.language import Evaluator
from unittest.mock import patch
with patch.object(Evaluator,"run",side_effect=AssertionError("analysis ran source")):
 result=analyze('fn add(x:Int)->Int{x+2};let fast=compile_function(add);fast(')
 assert result['signatureHelp']['parameters'][0]['name']=='x'
print("inert")
'''
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'inert')

    def test_formatter_ir_highlights_and_lsp_keep_same_semantics(self):
        source='// 🧠 保留源码\n@compile_function\nfn add(x:Int,/,*,by:Int=2)->Int{x+by};let answer=add(40,by=2);answer'
        formatted=format_source(source,line_width=50)
        self.assertIn('// 🧠 保留源码',formatted)
        self.assertEqual(parse_program(source)['semanticHash'],parse_program(formatted)['semanticHash'])
        self.assertEqual(parse_program(source)['semanticHash'],parse_program(format_program(parse_program(source)))['semanticHash'])
        result=analyze(source)
        def offset(pos):return len(source[:pos].encode('utf-16-le'))//2
        token=next(t for t in result['tokens'] if t['from']==offset(source.index('compile_function')))
        self.assertEqual(token['type'],'function')
        info=analyze(source,position=offset(source.rindex('by=')+3))
        snapshot=SimpleNamespace(name='main.chaos')
        help_=convert('textDocument/signatureHelp',info,snapshot,{'main.chaos':source})
        self.assertEqual(help_['activeParameter'],1);self.assertIn('by: Int',help_['signatures'][0]['parameters'][1]['documentation'])

    def test_return_shapes_and_parameter_patterns_survive_interpreter_choice(self):
        prefix='fn unpack(record as {x:value}:Record)->Tuple{(value,)};let converted=compile_function(unpack,target="interpreter");'
        help_=self.help(prefix+'converted(')
        self.assertIn('as {',help_['parameters'][0]['label']);self.assertIn('-> Tuple',help_['label'])
        self.assertEqual(next(s['type'] for s in analyze(prefix+'let value=converted({x:42});value')['symbols'] if s['name']=='value' and s['kind']=='variable'),'Tuple')


class JITTracePreferenceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_trace_defaults_and_storage_failures_preserve_usable_controls(self):
        root=Path(__file__).resolve().parent.parent
        result=subprocess.run([shutil.which('node'),str(root/'tests/jit_controls.cjs'),str(root)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')


class JITHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def output(self,value,*,traced=False):
        self.assertEqual(value['answer'],42);info=value['compilation']
        self.assertEqual(set(info),EXPECTED_FIELDS);self.assertEqual(info['schema'],'chaos.compiled-function/1')
        self.assertEqual(info['nativeCalls']+info['interpreterCalls'],1)
        if info['status']=='native' and not traced:self.assertEqual(info['nativeCalls'],1);self.assertEqual(info['backend'],'llvm')
        else:
            self.assertEqual(info['interpreterCalls'],1)
            if info['status']=='native':self.assertEqual(info['lastFallbackReason'],'expression_trace')
            else:self.assertEqual(info['backend'],'ast')
        return info

    def test_source_runtime_sdk_http_and_cli_reach_same_function(self):
        direct=self.output(Framework.evaluate(SOURCE))
        project=self.client.create_project('compiled',source=SOURCE)
        remote=self.output(self.client.start('compiled',project['version'],None,mode='source').result())
        self.assertEqual(direct,remote)
        result=subprocess.run([sys.executable,'-m','chaos','run','-'],input=SOURCE,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.output(json.loads(result.stdout)),remote)
        info=self.client.analyze(SOURCE);self.assertEqual(info['diagnostics'],[])
        self.assertEqual(next(s['type'] for s in info['symbols'] if s['name']=='answer'),'Int')
        report=self.client.compile(SOURCE);self.assertEqual(report['target'],'portable-ast')

    @unittest.skipUnless(sync_playwright is not None,'Playwright unavailable')
    def test_browser_signature_format_native_execution_and_headless_agree(self):
        source=SOURCE.replace('let answer=fast(40,by=2);','let answer=fast(40,by=2);tool("text.normalize"," lifecycle evidence ");')
        project=self.app.get_project('workspace');project.update(source=source,files={'main.chaos':source},entry='main.chaos');self.app.save_project(project,from_view='source')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(self.origin);workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                workbench_button(page,'formatSource');page.wait_for_function('()=>window.ChaosIDE.editor().state.doc.lines>1')
                formatted=page.evaluate('window.ChaosIDE.state.source');self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(source)['semanticHash'])
                # Formatter inserts spaces; choose the actual named argument token.
                page.evaluate('()=>{const e=window.ChaosIDE.editor(),s=e.state.doc.toString(),m=/fast\\(40,\\s*by\\s*=/.exec(s);e.dispatch({selection:{anchor:m.index+m[0].length}})}')
                workbench_button(page,'ideSignatureButton');page.wait_for_function('()=>document.querySelector("#ideSignatureHelp")?.textContent.includes("by: Int")')
                self.assertIn('x: Int',page.locator('#ideSignatureHelp').inner_text())
                self.assertIn('by: Int',page.locator('#ideSignatureHelp .active').inner_text())
                workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask')
                self.assertTrue(workbench_run_option(page,'expressionTrace').is_checked())
                self.assertTrue(workbench_run_option(page,'expressionTrace').is_visible())
                with page.expect_request(lambda r:r.method=='POST' and r.url==self.origin+'/api/runs') as request:
                    page.locator('#runButton').click()
                self.assertIs(request.value.post_data_json['trace'],True)
                page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.includes("nativeCalls")')
                self.output(json.loads(page.locator('#resultPanel').inner_text()),traced=True)
                workbench_run_option(page,'expressionTrace').uncheck()
                page.reload();workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                self.assertFalse(workbench_run_option(page,'expressionTrace').is_checked())
                before=self.client.events('workspace')['lastEventId']
                with page.expect_request(lambda r:r.method=='POST' and r.url==self.origin+'/api/runs') as request:
                    page.locator('#runButton').click()
                self.assertIs(request.value.post_data_json['trace'],False)
                page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.includes("nativeCalls")')
                displayed=json.loads(page.locator('#resultPanel').inner_text());self.output(displayed)
                kinds={e['type'] for e in self.client.events('workspace',after=before)['items']}
                self.assertTrue({'tool.started','tool.completed','run.completed'}<=kinds,kinds)
                current=self.client.get_project('workspace');self.assertEqual(current['source'],page.evaluate('window.ChaosIDE.state.source'))
                self.assertEqual(self.client.start('workspace',current['version'],None,mode='source').result(),displayed)
                page.reload();workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                self.assertEqual(page.evaluate('window.ChaosIDE.state.source'),current['source']);self.assertEqual(errors,[])
            finally:browser.close()
