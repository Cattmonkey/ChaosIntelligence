"""Structured compiled functions use existing source, structure and run APIs."""
from workbench_actions import button as workbench_button, view as workbench_view, run_option as workbench_run_option
import hashlib
import importlib.util
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
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.ide_language import analyze, format_source
from chaos.language import Evaluator, LanguageError
from chaos.lsp_adapters import convert
from chaos.program import SHAPES, format_program, parse_program, templates
from chaos.server import make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parent.parent
NATIVE_AVAILABLE = sys.version_info >= (3, 10) and importlib.util.find_spec('llvmlite') is not None
FUNCTION = 'fn score(x:Int,/,*,floor:Int=0)->Int{let y=x*2;if y<floor{let delta=floor-y;if delta>10{floor+10}else{floor}}else{let delta=y-floor;y+delta}};'
SOURCE = FUNCTION+'let build=compile_function;let fast=build(score);{values:[fast(-20,floor=2),fast(-1,floor=2),fast(21)],compilation:compile_info(fast)}'
FIELDS = {'schema','status','requestedTarget','backend','target','compilerVersion','parameterGuards','nodeCount','nativeCalls','interpreterCalls','fallbackReason','lastFallbackReason'}


class StructuredAnalysisTests(unittest.TestCase):
    def test_known_non_bool_conditions_report_exact_utf16_range(self):
        for expression in ['0','"no"','null','[]','{}','fn(){true}']:
            with self.subTest(expression=expression):
                source='// 🧠\nif '+expression+' {1}else{2}'
                start=source.index(expression,source.index('if ')+3)
                errors=[d for d in analyze(source)['diagnostics'] if d['code']=='type_mismatch']
                self.assertEqual(len(errors),1,errors)
                self.assertEqual(errors[0]['from'],len(source[:start].encode('utf-16-le'))//2)
                self.assertEqual(errors[0]['to'],len(source[:start+len(expression)].encode('utf-16-le'))//2)
                self.assertIn('Bool',errors[0]['message'])

    def test_bool_and_unknown_conditions_remain_conservative_without_execution(self):
        samples=['if true{1}else{2}','fn f(value){if value{1}else{2}}',
                 'var value=1;if value{1}else{2}',
                 'fn f(value:Int){if value>0{1}else{2}}',
                 'if tool("probe",null){1}else{2}']
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed source')):
            for source in samples:
                with self.subTest(source=source):self.assertEqual(analyze(source)['diagnostics'],[])

    def test_named_condition_contract_and_branch_scope(self):
        source='fn invalid(value:Int)->Int{if value{1}else{2}}'
        self.assertEqual([d['code'] for d in analyze(source)['diagnostics']],['type_mismatch'])
        source=FUNCTION+'delta'
        self.assertTrue(any(d['code']=='undefined_name' and 'delta' in d['message'] for d in analyze(source)['diagnostics']))
        result=analyze(FUNCTION)
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual([s['type'] for s in result['symbols'] if s['name']=='delta'],['Int','Int'])

    def test_comparison_chain_retains_original_language_semantics(self):
        program=parse_program('1<2<3')['root']['statements'][0]
        self.assertEqual(program['kind'],'binary');self.assertEqual(program['left']['kind'],'binary')
        with self.assertRaises(LanguageError):Evaluator().run('1<2<3')

    def test_compiled_alias_import_decorator_keep_structured_function_signature(self):
        samples=[(FUNCTION+'let build=compile_function;let fast=build(score);fast(-20,floor=',{}),
                 ('import "score.chaos" as M;let fast=compile_function(M.score);fast(-20,floor=',{'score.chaos':FUNCTION+'export{score}'}),
                 ('@compile_function\n'+FUNCTION+'score(-20,floor=',{})]
        for source,files in samples:
            with self.subTest(source=source):
                result=analyze(source,files=files);help_=result['signatureHelp']
                self.assertEqual([p['name'] for p in help_['parameters']],['x','floor'])
                self.assertEqual(help_['activeParameter'],1);self.assertIn('-> Int',help_['label'])
                self.assertEqual([p['mode'] for p in help_['parameters']],['positional_only','keyword_only'])
                lsp=convert('textDocument/signatureHelp',result,SimpleNamespace(name='main.chaos'),{'main.chaos':source,**files})
                self.assertEqual(lsp['activeParameter'],1)
        source='fn positive(x:Int)->Bool{let y=x+1;y>0};let fast=compile_function(positive);let answer=fast(1);answer'
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='answer'),'Bool')

    def test_formatter_and_structure_preserve_nested_control_and_highlights(self):
        source='// 🧠 分支与局部变量\n'+SOURCE
        formatted=format_source(source,line_width=60)
        self.assertGreater(formatted.count('\n'),10)
        self.assertEqual(parse_program(source)['semanticHash'],parse_program(formatted)['semanticHash'])
        self.assertEqual(parse_program(source)['semanticHash'],parse_program(format_program(parse_program(source)))['semanticHash'])
        self.assertEqual(format_source(formatted,line_width=60),formatted)
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[])
        tokens={t['from']:t['type'] for t in result['tokens']}
        for word,kind in [('if','keyword'),('let','keyword'),('compile_function','function'),('Int','type')]:
            index=source.index(word);self.assertEqual(tokens[len(source[:index].encode('utf-16-le'))//2],kind)

    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_structure_controls_edit_only_selected_branch(self):
        fixture={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/structured_jit_controls.cjs')],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout);self.assertEqual(report['status'],'passed')
        changed=format_program(report['program'])
        self.assertEqual(Evaluator().run(changed)['values'],[22,2,84])
        self.assertEqual(analyze(changed)['diagnostics'],[])


class StructuredHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(Path(self.temp.name)/'data',settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)

    def close_server(self):
        self.server.shutdown();self.thread.join(3);self.server.server_close()

    def output(self,value,*,traced=False,values=None):
        self.assertEqual(value['values'],[12,2,84] if values is None else values);info=value['compilation'];self.assertEqual(set(info),FIELDS)
        if NATIVE_AVAILABLE:
            self.assertEqual(info['status'],'native');self.assertEqual(info['backend'],'llvm')
            if traced:self.assertEqual(info['nativeCalls'],0);self.assertGreater(info['interpreterCalls'],0)
            else:self.assertEqual(info['nativeCalls'],3)
        else:self.assertEqual(info['nativeCalls'],0);self.assertGreater(info['interpreterCalls'],0)
        return info

    def test_source_sdk_http_cli_and_compile_report_agree(self):
        direct=Framework.evaluate(SOURCE);self.output(direct)
        project=self.client.create_project('structured',source=SOURCE)
        remote=self.client.start('structured',project['version'],None,mode='source').result();self.output(remote)
        self.assertEqual(direct,remote)
        result=subprocess.run([sys.executable,'-m','chaos','run','-'],input=SOURCE,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),remote)
        report=self.client.compile(SOURCE);self.assertEqual(report['target'],'portable-ast')
        self.assertGreater(report['modules'][0]['analysis']['kinds']['if'],0)

    @unittest.skipUnless(sync_playwright is not None,'Playwright unavailable')
    def test_real_browser_nested_control_signature_format_trace_and_headless(self):
        source=SOURCE.replace('{values:[','tool("text.normalize"," lifecycle evidence ");{values:[')
        project=self.app.get_project('workspace');project.update(source=source,files={'main.chaos':source},entry='main.chaos');self.app.save_project(project,from_view='source')
        audit=os.environ.get('CHAOS_STRUCTURED_JIT_AUDIT');output=Path(audit) if audit else None
        if output:output.mkdir(parents=True,exist_ok=True)
        errors=[];external=[];checks=[]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                def route(request):
                    if request.request.url.startswith(self.origin+'/'):request.continue_()
                    else:external.append(request.request.url);request.abort()
                context.route('**/*',route)
                def shot(name):
                    if output:page.screenshot(path=str(output/(name+'.png')))
                def run(traced,values=None):
                    workbench_run_option(page,'expressionTrace').set_checked(traced)
                    with page.expect_request(lambda r:r.method=='POST' and r.url==self.origin+'/api/runs') as request:page.locator('#runButton').click()
                    self.assertIs(request.value.post_data_json['trace'],traced)
                    try:page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.includes("nativeCalls")')
                    except Exception:
                        self.fail(json.dumps({'request':request.value.post_data_json,'result':page.locator('#resultPanel').inner_text(),'status':page.locator('#runStatus').inner_text(),'errors':errors},ensure_ascii=False))
                    value=json.loads(page.locator('#resultPanel').inner_text());self.output(value,traced=traced,values=values);checks.append({'trace':traced,'result':value});return value
                page.goto(self.origin);workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                workbench_button(page,'formatSource');formatted=format_source(source)
                page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(source)['semanticHash'])
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                self.assertTrue(page.locator('.ide-token-type',has_text='Int').count())
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString(),m=/fast\\(-20,\\s*floor\\s*=/.exec(s);e.dispatch({selection:{anchor:m.index+m[0].length}})}')
                workbench_button(page,'ideSignatureButton');page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("floor")')
                self.assertIn('x: Int',page.locator('#ideSignatureHelp').inner_text());shot('structured-signature')
                workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask')
                self.assertTrue(workbench_run_option(page,'expressionTrace').is_checked());traced=run(True);shot('structured-trace')
                before=self.client.events('workspace')['lastEventId'];native=run(False);shot('structured-native')
                kinds={e['type'] for e in self.client.events('workspace',after=before)['items']}
                self.assertTrue({'tool.started','tool.completed','run.completed'}<=kinds,kinds)
                page.locator('#expandConsole').click();page.wait_for_function('()=>document.querySelector("#console").getBoundingClientRect().height>420');page.locator('#resultPanel').evaluate('(el)=>el.scrollTop=el.scrollHeight');shot('structured-native-counters')
                current=self.client.get_project('workspace');self.assertEqual(current['source'],page.evaluate('ChaosIDE.state.source'))
                self.assertEqual(self.client.start('workspace',current['version'],None,mode='source').result(),native)
                page.reload();workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                self.assertFalse(workbench_run_option(page,'expressionTrace').is_checked());self.assertEqual(page.evaluate('ChaosIDE.state.source'),current['source'])
                workbench_view(page,'structure');page.wait_for_function('()=>programDraft?.root?.statements?.[0]?.function?.body')
                target=page.evaluate('programDraft.root.statements[0].function.body.statements[1].then.statements[1].then.statements[0].right.id')
                page.locator('[data-syntax="'+target+'"]').click()
                field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='value'))
                field.locator('input').fill('20');field.get_by_role('button',name='应用',exact=True).click();shot('structured-properties')
                workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty')
                changed=self.client.get_project('workspace');program=parse_program(changed['source'])
                self.assertEqual(changed['program']['semanticHash'],program['semanticHash'])
                branch=program['root']['statements'][0]['function']['body']['statements'][1]
                self.assertEqual(branch['then']['statements'][1]['then']['statements'][0]['right']['value'],20)
                self.assertEqual(branch['then']['statements'][1]['condition']['right']['value'],10)
                workbench_view(page,'source');page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=changed['source'])
                modified=run(False,values=[22,2,84]);self.assertEqual(self.client.start('workspace',changed['version'],None,mode='source').result(),modified)
                pure=json.loads(json.dumps(program));self.assertEqual(pure['root']['statements'][-2]['kind'],'call')
                del pure['root']['statements'][-2]
                self.assertEqual(Framework.evaluate(format_program(pure)),modified);shot('structured-edited-source')
                page.set_viewport_size({'width':390,'height':900});self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'));self.assertTrue(workbench_run_option(page,'expressionTrace').is_visible());shot('structured-mobile')
                self.assertEqual(errors,[]);self.assertEqual(external,[])
                if output:
                    record={'status':'passed','root':str(ROOT),'checks':checks,'lifecycleEvents':sorted(kinds),'signature':True,'formattedSemanticHash':parse_program(formatted)['semanticHash'],'guiHeadlessEqual':True,'structuralEditSemanticHash':program['semanticHash'],'selectedBranchOnly':True,'traceOffPersisted':True,'mobileNoOverflow':True,'errors':errors,'external':external,'screenshots':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.png')}}
                    (output/'browser-result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            finally:browser.close()
