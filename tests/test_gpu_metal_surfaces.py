"""Opt-in Metal kernels keep ordinary function syntax and legacy GPU results."""
from workbench_actions import button as workbench_button, view as workbench_view, run_option as workbench_run_option
import hashlib
import json
import os
from pathlib import Path
import shutil
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
from chaos.program import SHAPES as SYNTAX_SHAPES, format_program, parse_program, templates
from chaos.server import make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parent.parent
FIELDS = {'requestedCompiler','compiler','status','irHash','sourceHash','sourceCacheHit','dispatches','isolated','fallbackReason'}
NULLABLE = {'compiler','sourceHash','sourceCacheHit','dispatches','isolated','fallbackReason'}
RESULT_FIELDS = {'values','device','backend','dtype','fallbackReason'}
TASK_FIELDS = {'id','state','phase','kind','parentTaskId','groupId','channelId','memberTaskIds','memberCount','membersTruncated','cancellationRequested','cancellationDeferred','shieldDepth','observed','location','timing','selection'}


def source_for(device='cpu', compiler='tensor'):
    return '''fn transform(x:Number)->Number{x*x+2};
let submit=gpu_map;let query=gpu_kernel_info;
let job=submit(transform,[1,2,3],device="'''+device+'''",compiler="'''+compiler+'''");
let before=query(job);
let mapped=await job;let kernel=query(job);
{actualDevice:mapped.device,actualCompiler:kernel.compiler,values:mapped.values,kernel:kernel,before:before,mapped:mapped}'''


class MetalAnalysisTests(unittest.TestCase):
    def test_shared_opt_in_signature_and_separate_info_shape(self):
        self.assertEqual({f.name for f in SHAPES['GpuResult']}, RESULT_FIELDS)
        self.assertEqual({f.name for f in SHAPES['TaskInfo']}, TASK_FIELDS)
        self.assertEqual({f.name for f in SHAPES['GpuKernelInfo']}, FIELDS)
        self.assertEqual({f.name for f in SHAPES['GpuKernelInfo'] if f.nullable}, NULLABLE)
        self.assertEqual(spec_for('gpu_kernel_info').result_shape, 'GpuKernelInfo')
        parameters = spec_for('gpu_map').parameters
        self.assertEqual([p.name for p in parameters], ['fn','values','device','compiler'])
        self.assertEqual(parameters[-1].mode, 'keyword_only')
        self.assertEqual(parameters[-1].default_display, '"tensor"')
        source = 'let submit=gpu_map;submit(fn(x){x},[1],device="mps",compiler='
        result = analyze(source); help_ = result['signatureHelp']
        self.assertEqual(help_['activeParameter'], 3)
        self.assertEqual(help_['parameters'][-1]['name'], 'compiler')
        self.assertIn('"tensor"', help_['label'])
        lsp = convert('textDocument/signatureHelp', result, SimpleNamespace(name='main.chaos'), {'main.chaos':source})
        self.assertEqual(lsp['activeParameter'], 3)
        self.assertEqual([p.name for p in spec_for('gpu_matmul').parameters], ['left','right','device'])

    def test_info_alias_import_completion_and_legacy_await_shape(self):
        samples = [('let submit=gpu_map;let query=gpu_kernel_info;let job=submit(fn(x){x},[1]);let info=query(job);info.',{}),
                   ('import "compute.chaos" as G;let task=G.submit(fn(x){x},[1]);let info=G.query(task);info.',{'compute.chaos':'let submit=gpu_map;let query=gpu_kernel_info;export{submit,query};'})]
        for source, files in samples:
            with self.subTest(source=source):
                result = analyze(source, files=files)
                self.assertEqual({c['label'] for c in result['completions']}, FIELDS)
                lsp = convert('textDocument/completion', result, SimpleNamespace(name='main.chaos'), {'main.chaos':source,**files})
                items = lsp.get('items',[]) if isinstance(lsp,dict) else lsp
                self.assertEqual({c['label'] for c in items}, FIELDS)
                details = {c['label']:c['detail'] for c in result['completions']}
                for name in NULLABLE: self.assertIn('Null', details[name])
        source = 'let job=gpu_map(fn(x){x},[1],compiler="metal");let result=await spawn(job);result.'
        self.assertEqual({c['label'] for c in analyze(source)['completions']}, RESULT_FIELDS)

    def test_keyword_only_compiler_and_query_shadowing_are_language_rules(self):
        source = 'gpu_map(fn(x){x},[1],"mps","metal")'
        self.assertTrue(any(d['code']=='argument_error' for d in analyze(source)['diagnostics']))
        source = 'gpu_matmul([[1]],[[1]],compiler="metal")'
        self.assertTrue(any(d['code']=='argument_error' for d in analyze(source)['diagnostics']))
        source = 'fn gpu_kernel_info(task)->String{"ordinary"};let info=gpu_kernel_info(1);info'
        result = analyze(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='info'), 'String')

    def test_static_analysis_never_loads_hardware_or_runs_compiler(self):
        script = '''import builtins,subprocess
from unittest.mock import patch
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name=="torch" or name.startswith("torch."):raise AssertionError("analysis imported Torch")
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from chaos.ide_language import analyze
from chaos.language import Evaluator
with patch.object(Evaluator,"run",side_effect=AssertionError("analysis executed source")),patch.object(subprocess,"Popen",side_effect=AssertionError("analysis spawned compiler")):
 result=analyze('let task=gpu_map(fn(x){x*x},[1],compiler="metal");let info=gpu_kernel_info(task);info.')
 assert len(result['completions'])==9
 assert analyze('gpu_map(fn(x){x},[1],compiler=')['signatureHelp']['activeParameter']==3
print("inert")
'''
        result = subprocess.run([sys.executable,'-c',script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'inert')

    def test_original_ir_formatting_highlights_and_compiler_keywords(self):
        source = '// 🧠 显式融合编译仍使用普通函数\n'+source_for('mps','metal')
        formatted = format_source(source, line_width=60)
        self.assertEqual(parse_program(source)['semanticHash'], parse_program(formatted)['semanticHash'])
        self.assertEqual(parse_program(source)['semanticHash'], parse_program(format_program(parse_program(source)))['semanticHash'])
        self.assertEqual(format_source(formatted,line_width=60), formatted)
        result = analyze(source); self.assertEqual(result['diagnostics'], [])
        for word, expected in [('gpu_map','function'),('gpu_kernel_info','function'),('Number','type'),('await','keyword'),('compiler','property')]:
            offset = len(source[:source.index(word)].encode('utf-16-le'))//2
            self.assertEqual(next(t['type'] for t in result['tokens'] if t['from']==offset), expected)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_real_structure_form_changes_only_function_expression(self):
        fixture = {'info':{'syntax':SYNTAX_SHAPES,'templates':templates()},'program':parse_program(source_for('mps','metal'))}
        result = subprocess.run([shutil.which('node'),str(ROOT/'tests/gpu_metal_controls.cjs')], input=json.dumps(fixture), capture_output=True, text=True, cwd=ROOT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        report = json.loads(result.stdout); self.assertEqual(report['status'], 'passed')
        changed = format_program(report['program'])
        self.assertEqual(parse_program(changed)['root']['statements'][0]['function']['body']['statements'][0]['right']['value'], 3)
        self.assertEqual(analyze(changed)['diagnostics'], [])
        self.assertNotEqual(parse_program(changed)['semanticHash'], parse_program(source_for('mps','metal'))['semanticHash'])


class MetalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.app = Framework(Path(self.temp.name)/'data', settings=Settings(':memory:')); self.addCleanup(self.app.close)
        self.server = make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start(); self.addCleanup(self.close_server)
        self.origin = 'http://127.0.0.1:'+str(self.server.server_port); self.client = Client(self.origin)

    def close_server(self):
        self.server.shutdown(); self.thread.join(3); self.server.server_close()

    def output(self, value, device='cpu', compiler='tensor', expected=None):
        mapped, before, info = value['mapped'], value['before'], value['kernel']
        self.assertEqual(set(mapped), RESULT_FIELDS)
        self.assertEqual(set(before), FIELDS); self.assertEqual(set(info), FIELDS)
        self.assertEqual(before['status'], 'created')
        self.assertEqual(before['requestedCompiler'], compiler)
        self.assertRegex(before['irHash'], '^[0-9a-f]{64}$')
        for field in NULLABLE: self.assertIsNone(before[field])
        self.assertEqual(info['status'], 'completed')
        self.assertEqual(info['requestedCompiler'], compiler)
        self.assertEqual(info['irHash'], before['irHash'])
        self.assertEqual(info['compiler'], 'tensor' if device=='cpu' else 'metal')
        self.assertEqual(value['actualCompiler'], info['compiler'])
        self.assertEqual(value['values'], [3,6,11] if expected is None else expected)
        self.assertEqual(mapped['values'], value['values'])
        self.assertEqual(value['actualDevice'], mapped['device'])
        self.assertEqual(mapped['device'].split(':')[0], device)
        self.assertEqual(mapped['backend'], 'python' if device=='cpu' else 'torch')
        self.assertEqual(mapped['dtype'], 'float32'); self.assertIsNone(mapped['fallbackReason'])
        self.assertIsNone(info['fallbackReason'])
        if device=='mps':
            self.assertRegex(info['sourceHash'], '^[0-9a-f]{64}$')
            self.assertEqual(info['dispatches'], 1); self.assertIs(info['isolated'], True)
            self.assertIs(info['sourceCacheHit'], False)
        else:
            self.assertIsNone(info['sourceHash']); self.assertIsNone(info['sourceCacheHit'])
            self.assertEqual(info['dispatches'], 0)
            self.assertIs(info['isolated'], False)
        return value

    def test_cpu_source_sdk_http_cli_and_query_snapshot_agree(self):
        source = source_for(); direct = self.output(Framework.evaluate(source))
        project = self.client.create_project('gpu-kernel-cpu',source=source)
        remote = self.output(self.client.start('gpu-kernel-cpu',project['version'],None,mode='source').result())
        self.assertEqual(remote, direct)
        result = subprocess.run([sys.executable,'-m','chaos','run','-'],input=source,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output(json.loads(result.stdout)), direct)
        self.assertEqual(self.client.program('gpu-kernel-cpu')['program']['semanticHash'], parse_program(source)['semanticHash'])
        self.assertEqual(self.client.compile(source)['target'], 'portable-ast')

    @unittest.skipUnless(sync_playwright is not None, 'Playwright unavailable')
    def test_real_isolated_metal_browser_source_structure_and_headless(self):
        hardware = Framework.evaluate('await gpu_devices()')
        if not hardware['mps']: self.skipTest('real MPS GPU unavailable')
        import torch
        if not callable(getattr(torch.mps,'compile_shader',None)): self.skipTest('Metal compiler unavailable')
        source = source_for('mps','metal')
        project = self.app.get_project('workspace'); project.update(source=source,files={'main.chaos':source},entry='main.chaos'); self.app.save_project(project,from_view='source')
        audit = os.environ.get('CHAOS_GPU_METAL_AUDIT'); output = Path(audit) if audit else None
        if output: output.mkdir(parents=True,exist_ok=True)
        errors = []; external = []; checks = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width':1440,'height':1000}); page = context.new_page(); page.on('pageerror',lambda error:errors.append(str(error)))
                def route(request):
                    if request.request.url.startswith(self.origin+'/'): request.continue_()
                    else: external.append(request.request.url); request.abort()
                context.route('**/*',route)
                def shot(name):
                    if output: page.screenshot(path=str(output/(name+'.png')))
                def run(expected=None):
                    with page.expect_request(lambda r:r.method=='POST' and r.url==self.origin+'/api/runs') as request: page.locator('#runButton').click()
                    self.assertEqual(request.value.post_data_json['mode'],'source')
                    try: page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runStatus")?.textContent.includes("已完成")&&document.querySelector("#resultPanel")?.textContent.includes("sourceCacheHit")',timeout=60000)
                    except Exception: self.fail(json.dumps({'result':page.locator('#resultPanel').inner_text(),'status':page.locator('#runStatus').inner_text(),'errors':errors},ensure_ascii=False))
                    value = self.output(json.loads(page.locator('#resultPanel').inner_text()),'mps','metal',expected)
                    checks.append(value); return value
                page.goto(self.origin); workbench_view(page,'source'); page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                workbench_button(page,'formatSource'); formatted = format_source(source)
                page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(source)['semanticHash'])
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                self.assertTrue(page.locator('.ide-token-type',has_text='Number').count())
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString(),m=/compiler\\s*=/.exec(s);e.dispatch({selection:{anchor:m.index+m[0].length},scrollIntoView:true})}')
                workbench_button(page,'ideSignatureButton'); page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("compiler")'); shot('metal-signature')
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString();e.dispatch({selection:{anchor:s.indexOf("mapped.device")+7},scrollIntoView:true});e.focus()}')
                page.locator('.cm-editor').press('Control+Space'); page.wait_for_selector('.cm-tooltip-autocomplete')
                self.assertEqual(set(page.locator('.cm-tooltip-autocomplete .cm-completionLabel').all_text_contents()),RESULT_FIELDS)
                page.locator('.cm-editor').press('Escape')
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString();e.dispatch({selection:{anchor:s.indexOf("kernel.compiler")+7},scrollIntoView:true});e.focus()}')
                page.wait_for_function('()=>{const e=ChaosIDE.editor(),c=e.coordsAtPos(e.state.selection.main.head),r=e.scrollDOM.getBoundingClientRect();return c&&c.top>=r.top&&c.bottom<=r.bottom&&document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")}')
                page.locator('.cm-editor').press('Control+Space'); page.wait_for_selector('.cm-tooltip-autocomplete')
                self.assertEqual(set(page.locator('.cm-tooltip-autocomplete .cm-completionLabel').all_text_contents()),FIELDS)
                page.wait_for_function('()=>{const r=document.querySelector(".cm-tooltip-autocomplete")?.getBoundingClientRect();return r&&r.width>0&&r.top>=0&&r.bottom<=innerHeight}'); shot('metal-completion')
                page.locator('.cm-editor').press('Escape')
                workbench_button(page,'saveButton'); page.wait_for_function('()=>!dirty&&!saveTask')
                workbench_run_option(page,'expressionTrace').uncheck(); original = run()
                page.locator('#expandConsole').click(); page.wait_for_function('()=>document.querySelector("#console").getBoundingClientRect().height>420')
                page.locator('#resultPanel').evaluate('(el)=>el.scrollTop=0'); shot('metal-result'); page.locator('#expandConsole').click()
                current = self.client.get_project('workspace'); self.assertEqual(current['source'],page.evaluate('ChaosIDE.state.source'))
                remote = self.output(self.client.start('workspace',current['version'],None,mode='source').result(),'mps','metal')
                pure = self.output(Framework.evaluate(current['source']),'mps','metal')
                self.assertEqual(remote,original); self.assertEqual(pure,original)
                workbench_view(page,'structure'); page.wait_for_function('()=>programDraft?.root?.statements?.[0]?.function?.body')
                target = page.evaluate('programDraft.root.statements[0].function.body.statements[0].right.id')
                page.locator('[data-syntax="'+target+'"]').click()
                field = page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='value'))
                field.locator('input').fill('3'); field.get_by_role('button',name='应用',exact=True).click(); shot('metal-properties')
                workbench_button(page,'applyProgram'); page.wait_for_function('()=>!syntaxDirty&&!dirty')
                changed = self.client.get_project('workspace'); program = parse_program(changed['source'])
                self.assertEqual(changed['program']['semanticHash'],program['semanticHash'])
                self.assertEqual(program['root']['statements'][0]['function']['body']['statements'][0]['right']['value'],3)
                workbench_view(page,'source'); page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=changed['source'])
                readable = format_source(changed['source'])
                workbench_button(page,'formatSource'); page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=readable)
                self.assertEqual(parse_program(readable)['semanticHash'],program['semanticHash'])
                workbench_button(page,'saveButton'); page.wait_for_function('()=>!dirty&&!saveTask')
                changed = self.client.get_project('workspace')
                self.assertEqual(changed['source'],readable)
                self.assertEqual(changed['program']['semanticHash'],program['semanticHash'])
                modified = run([4,7,12])
                self.assertNotEqual(modified['kernel']['irHash'],original['kernel']['irHash'])
                self.assertNotEqual(modified['kernel']['sourceHash'],original['kernel']['sourceHash'])
                self.assertEqual(self.output(self.client.start('workspace',changed['version'],None,mode='source').result(),'mps','metal',[4,7,12]),modified)
                self.assertEqual(self.output(Framework.evaluate(changed['source']),'mps','metal',[4,7,12]),modified)
                page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runStatus")?.textContent.includes("已完成")')
                page.evaluate('()=>ChaosIDE.editor().dispatch({selection:{anchor:0},scrollIntoView:true})')
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")'); shot('metal-edited-source')
                page.set_viewport_size({'width':390,'height':900}); self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth')); self.assertTrue(page.locator('#runButton').is_visible()); shot('metal-mobile')
                self.assertEqual(errors,[]); self.assertEqual(external,[])
                if output:
                    record = {'status':'passed','root':str(ROOT),'hardware':hardware,'checks':checks,'signature':True,'completion':True,'formattedSemanticHash':parse_program(formatted)['semanticHash'],'editedSemanticHash':program['semanticHash'],'guiHeadlessEqual':True,'actualMetal':True,'isolated':True,'hashesChangedAfterStructureEdit':True,'mobileNoOverflow':True,'errors':errors,'external':external,'screenshots':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.png')}}
                    (output/'browser-result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            finally: browser.close()
