"""One GPU language API for source, structure, IDE, SDK and HTTP."""
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
RESULT_FIELDS = {'values', 'device', 'backend', 'dtype', 'fallbackReason'}
DEVICE_FIELDS = {'backend', 'version', 'cpu', 'mps', 'cuda', 'devices', 'defaultDevice'}


def source_for(device='cpu'):
    return '''fn transform(x:Number)->Number{x*x+2};
let submit=gpu_map;
let job=submit(transform,[1,2,3],device="'''+device+'''");
let cold=task_info(job).state;
let matrixJob=spawn(gpu_matmul([[1,2],[3,4]],[[2,0],[1,2]],device="'''+device+'''"));
let mapped=await job;let matrix=await matrixJob;
let hardware=await gpu_devices();
{cold:cold,mapped:mapped,matrix:matrix,hardware:hardware,actualDevice:mapped.device}'''


class GPUAnalysisTests(unittest.TestCase):
    def test_shared_task_shapes_modes_and_named_argument_help(self):
        self.assertEqual({f.name for f in SHAPES['GpuResult']}, RESULT_FIELDS)
        self.assertEqual({f.name for f in SHAPES['GpuDevices']}, DEVICE_FIELDS)
        for name, parameters in [('gpu_map', ['fn', 'values', 'device', 'compiler']), ('gpu_matmul', ['left', 'right', 'device'])]:
            with self.subTest(name=name):
                spec = spec_for(name)
                self.assertEqual(spec.result_type, 'Task')
                self.assertEqual(spec.awaited_type, 'Record')
                self.assertEqual(spec.awaited_shape, 'GpuResult')
                self.assertEqual([p.name for p in spec.parameters], parameters)
                self.assertEqual(spec.parameters[-1].mode, 'keyword_only')
                help_ = analyze(name+'(input,input,device=')['signatureHelp']
                self.assertEqual([p['name'] for p in help_['parameters']], parameters)
                self.assertEqual(help_['activeParameter'], 2)
                self.assertIn('"auto"', help_['label'])
        self.assertEqual(spec_for('gpu_devices').awaited_shape, 'GpuDevices')

    def test_alias_import_spawn_and_await_preserve_record_field_hints(self):
        prefix = 'let submit=gpu_map;let job=submit(fn(x:Number){x*x},[1],device="cpu");let result=await spawn(job);result.'
        result = analyze(prefix)
        self.assertEqual({c['label'] for c in result['completions']}, RESULT_FIELDS)
        files = {'gpu.chaos': 'let submit=gpu_map;export{submit};'}
        source = 'import "gpu.chaos" as G;let result=await G.submit(fn(x:Number){x+2},[1],device="cpu");result.'
        result = analyze(source, files=files)
        self.assertEqual({c['label'] for c in result['completions']}, RESULT_FIELDS)
        lsp = convert('textDocument/completion', result, SimpleNamespace(name='main.chaos'), {'main.chaos': source, **files})
        items = lsp.get('items', []) if isinstance(lsp, dict) else lsp
        self.assertEqual({c['label'] for c in items}, RESULT_FIELDS)
        source = 'let query=gpu_devices;let devices=await query();devices.'
        self.assertEqual({c['label'] for c in analyze(source)['completions']}, DEVICE_FIELDS)
        source = 'let result=await gpu_map(fn(x:Number){x*x},[1]);let selected=result.device;selected'
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='selected'), 'String')

    def test_keyword_only_device_and_shadowing_follow_existing_call_rules(self):
        for source in ['gpu_map(fn(x:Number){x},[1],"cpu")', 'gpu_matmul([[1]],[[1]],"cpu")']:
            self.assertTrue(any(d['code']=='argument_error' for d in analyze(source)['diagnostics']))
        source = 'fn gpu_map(value)->String{"ordinary"};let result=gpu_map(1);result'
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='result'), 'String')
        self.assertEqual(analyze(source)['diagnostics'], [])

    def test_analysis_and_metadata_never_import_torch_or_execute_gpu_work(self):
        script = '''import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name=="torch" or name.startswith("torch."):raise AssertionError("analysis imported Torch")
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from chaos.ide_language import analyze
from chaos.language import Evaluator
from unittest.mock import patch
with patch.object(Evaluator,"run",side_effect=AssertionError("analysis executed source")):
 result=analyze('let job=gpu_map(fn(x:Number){x*x},[1]);let value=await job;value.')
 assert {c['label'] for c in result['completions']}=={'values','device','backend','dtype','fallbackReason'}
 assert analyze('gpu_matmul(input,input,device=')['signatureHelp']['activeParameter']==2
print("inert")
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'inert')

    def test_original_parser_formatter_ir_and_semantic_highlights(self):
        source = '// 🧠 GPU 批计算仍是函数和 Task\n'+source_for()
        formatted = format_source(source, line_width=60)
        program = parse_program(source)
        self.assertEqual(program['semanticHash'], parse_program(formatted)['semanticHash'])
        self.assertEqual(program['semanticHash'], parse_program(format_program(program))['semanticHash'])
        self.assertEqual(format_source(formatted, line_width=60), formatted)
        result = analyze(source)
        self.assertEqual(result['diagnostics'], [])
        for word, expected in [('gpu_map', 'function'), ('gpu_matmul', 'function'), ('Number', 'type'), ('await', 'keyword')]:
            offset = len(source[:source.index(word)].encode('utf-16-le'))//2
            self.assertEqual(next(t['type'] for t in result['tokens'] if t['from']==offset), expected)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_existing_structural_form_edits_gpu_function_without_new_syntax(self):
        fixture = {'info': {'syntax': SYNTAX_SHAPES, 'templates': templates()}, 'program': parse_program(source_for())}
        result = subprocess.run([shutil.which('node'), str(ROOT/'tests/gpu_controls.cjs')], input=json.dumps(fixture), capture_output=True, text=True, cwd=ROOT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        source = format_program(report['program'])
        self.assertEqual(analyze(source)['diagnostics'], [])
        self.assertEqual(Framework.evaluate(source)['mapped']['values'], [4, 7, 12])


class GPUHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.app = Framework(Path(self.temp.name)/'data', settings=Settings(':memory:')); self.addCleanup(self.app.close)
        self.server = make_server('127.0.0.1', 0, framework=self.app, state_dir=Path(self.temp.name)/'control', update_home=Path(self.temp.name)/'updates')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start(); self.addCleanup(self.close_server)
        self.origin = 'http://127.0.0.1:'+str(self.server.server_port); self.client = Client(self.origin)

    def close_server(self):
        self.server.shutdown(); self.thread.join(3); self.server.server_close()

    def output(self, value, device='cpu', expected=None):
        self.assertEqual(value['cold'], 'created')
        self.assertEqual(value['mapped']['values'], [3, 6, 11] if expected is None else expected)
        self.assertEqual(value['matrix']['values'], [[4, 4], [10, 8]])
        for result in [value['mapped'], value['matrix']]:
            self.assertEqual(set(result), RESULT_FIELDS)
            self.assertEqual(result['device'].split(':')[0], device)
            self.assertEqual(result['backend'], 'python' if device=='cpu' else 'torch')
            self.assertEqual(result['dtype'], 'float32')
            self.assertIsNone(result['fallbackReason'])
        self.assertEqual(value['actualDevice'], value['mapped']['device'])
        hardware = value['hardware']; self.assertEqual(set(hardware), DEVICE_FIELDS)
        self.assertTrue(hardware['cpu'])
        self.assertIn(hardware['defaultDevice'], hardware['devices'])
        self.assertIn(hardware['backend'], ['torch', 'python'])
        return value

    def test_pure_cpu_sdk_http_cli_and_compiler_use_same_language(self):
        source = source_for(); direct = self.output(Framework.evaluate(source))
        project = self.client.create_project('gpu-cpu', source=source)
        remote = self.output(self.client.start('gpu-cpu', project['version'], None, mode='source').result())
        self.assertEqual(remote, direct)
        result = subprocess.run([sys.executable, '-m', 'chaos', 'run', '-'], input=source, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output(json.loads(result.stdout)), direct)
        self.assertEqual(self.client.program('gpu-cpu')['program']['semanticHash'], parse_program(source)['semanticHash'])
        self.assertEqual(self.client.compile(source)['target'], 'portable-ast')

    @unittest.skipUnless(sync_playwright is not None, 'Playwright unavailable')
    def test_real_mps_browser_hints_structure_run_and_headless_agree(self):
        hardware = Framework.evaluate('await gpu_devices()')
        if not hardware['mps']: self.skipTest('real MPS GPU unavailable')
        source = source_for('mps')
        project = self.app.get_project('workspace'); project.update(source=source, files={'main.chaos':source}, entry='main.chaos'); self.app.save_project(project, from_view='source')
        audit = os.environ.get('CHAOS_GPU_AUDIT'); output = Path(audit) if audit else None
        if output: output.mkdir(parents=True, exist_ok=True)
        errors = []; external = []; checks = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width':1440,'height':1000}); page = context.new_page(); page.on('pageerror', lambda error:errors.append(str(error)))
                def route(request):
                    if request.request.url.startswith(self.origin+'/'): request.continue_()
                    else: external.append(request.request.url); request.abort()
                context.route('**/*', route)
                def shot(name):
                    if output: page.screenshot(path=str(output/(name+'.png')))
                def run(expected=None):
                    with page.expect_request(lambda r:r.method=='POST' and r.url==self.origin+'/api/runs') as request: page.locator('#runButton').click()
                    self.assertEqual(request.value.post_data_json['mode'], 'source')
                    try: page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.includes("actualDevice")')
                    except Exception: self.fail(json.dumps({'result':page.locator('#resultPanel').inner_text(),'status':page.locator('#runStatus').inner_text(),'errors':errors}, ensure_ascii=False))
                    value = self.output(json.loads(page.locator('#resultPanel').inner_text()), 'mps', expected)
                    self.assertTrue(value['hardware']['mps']); checks.append(value); return value
                page.goto(self.origin); workbench_view(page,'source'); page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                workbench_button(page,'formatSource'); formatted = format_source(source)
                page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text', arg=formatted)
                self.assertEqual(parse_program(formatted)['semanticHash'], parse_program(source)['semanticHash'])
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                self.assertTrue(page.locator('.ide-token-type', has_text='Number').count())
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString(),m=/submit\\(transform,\\s*\\[1,\\s*2,\\s*3\\],\\s*device\\s*=/.exec(s);e.dispatch({selection:{anchor:m.index+m[0].length}})}')
                workbench_button(page,'ideSignatureButton'); page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("device")'); shot('gpu-signature')
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString();e.dispatch({selection:{anchor:s.indexOf("mapped.device")+7},scrollIntoView:true});e.focus()}')
                page.wait_for_function('()=>{const e=ChaosIDE.editor(),c=e.coordsAtPos(e.state.selection.main.head),r=e.scrollDOM.getBoundingClientRect();return c&&c.top>=r.top&&c.bottom<=r.bottom&&document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")}')
                page.locator('.cm-editor').press('Control+Space'); page.wait_for_selector('.cm-tooltip-autocomplete')
                self.assertEqual(set(page.locator('.cm-tooltip-autocomplete .cm-completionLabel').all_text_contents()), RESULT_FIELDS)
                page.wait_for_function('()=>{const r=document.querySelector(".cm-tooltip-autocomplete")?.getBoundingClientRect();return r&&r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight}')
                shot('gpu-completion')
                page.locator('.cm-editor').press('Escape'); workbench_button(page,'saveButton'); page.wait_for_function('()=>!dirty&&!saveTask')
                workbench_run_option(page,'expressionTrace').uncheck(); original = run()
                page.locator('#expandConsole').click(); page.wait_for_function('()=>document.querySelector("#console").getBoundingClientRect().height>420')
                page.locator('#resultPanel').evaluate('(el)=>el.scrollTop=0'); shot('gpu-mps-result')
                page.locator('#expandConsole').click()
                current = self.client.get_project('workspace'); self.assertEqual(current['source'], page.evaluate('ChaosIDE.state.source'))
                self.assertEqual(self.client.start('workspace', current['version'], None, mode='source').result(), original)
                self.assertEqual(Framework.evaluate(current['source']), original)
                workbench_view(page,'structure'); page.wait_for_function('()=>programDraft?.root?.statements?.[0]?.function?.body')
                target = page.evaluate('programDraft.root.statements[0].function.body.statements[0].right.id')
                page.locator('[data-syntax="'+target+'"]').click()
                field = page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label', has_text='value'))
                field.locator('input').fill('3'); field.get_by_role('button', name='应用', exact=True).click(); shot('gpu-properties')
                workbench_button(page,'applyProgram'); page.wait_for_function('()=>!syntaxDirty&&!dirty')
                changed = self.client.get_project('workspace'); program = parse_program(changed['source'])
                self.assertEqual(changed['program']['semanticHash'], program['semanticHash'])
                self.assertEqual(program['root']['statements'][0]['function']['body']['statements'][0]['right']['value'], 3)
                workbench_view(page,'source'); page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text', arg=changed['source'])
                modified = run([4,7,12]); self.assertEqual(self.client.start('workspace', changed['version'], None, mode='source').result(), modified)
                self.assertEqual(Framework.evaluate(changed['source']), modified); shot('gpu-edited-source')
                page.set_viewport_size({'width':390,'height':900}); self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth')); self.assertTrue(page.locator('#runButton').is_visible()); shot('gpu-mobile')
                self.assertEqual(errors, []); self.assertEqual(external, [])
                if output:
                    record = {'status':'passed','root':str(ROOT),'hardware':hardware,'checks':checks,'signature':True,'completion':True,'formattedSemanticHash':parse_program(formatted)['semanticHash'],'editedSemanticHash':program['semanticHash'],'guiHeadlessEqual':True,'actualMPS':True,'mobileNoOverflow':True,'errors':errors,'external':external,'screenshots':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.png')}}
                    (output/'browser-result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
            finally: browser.close()
