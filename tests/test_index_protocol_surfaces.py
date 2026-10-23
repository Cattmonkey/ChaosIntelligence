"""Native index metadata and the same ordinary AST across visible/headless tools."""
from workbench_actions import button as workbench_button, view as workbench_view
import hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys, tempfile, threading, unittest
from types import SimpleNamespace
from unittest.mock import patch
from chaos import Client, Framework, Settings
from chaos.ide_language import analyze, format_source
from chaos.language import Evaluator, LanguageError
from chaos.lsp_adapters import convert
from chaos.modules import ProgramBundle
from chaos.native_specs import spec_for
from chaos.program import SHAPES as SYNTAX_SHAPES, parse_program, format_program, templates
from chaos.server import make_server
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''// 🧠 Integer protocol remains an ordinary language method.
class Offset{fn __index__(self)->Int{1}}
let offset=Offset();let convert=index;let spec=slice_key(offset);
{index:convert(offset),value:[10,20,30][offset],slice:[10,20,30][spec],normalized:slice_indices(spec,3)}'''
def expected(n=1):return {'index':n,'value':10*(n+1),'slice':[10*(i+1) for i in range(n,3)],'normalized':[n,3,1]}


class IndexProtocolAnalysis(unittest.TestCase):
    def test_shared_signatures_aliases_imports_and_exact_integer_result(self):
        self.assertEqual(spec_for('index').result_type,'Int')
        self.assertEqual([p.name for p in spec_for('index').parameters],['value'])
        self.assertEqual(spec_for('index').parameters[0].type,'Any')
        for source, files in [('let convert=index;let answer=convert(1);answer',{}),('import "indices.chaos" as I;let answer=I.convert(1);answer',{'indices.chaos':'let convert=index;export{convert}'})]:
            result=analyze(source,files=files);self.assertEqual(result['diagnostics'],[])
            self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='answer'),'Int')
        source='let convert=index;convert('
        result=analyze(source);self.assertEqual(result['signatureHelp']['activeParameter'],0)
        self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['value'])
        self.assertIn('-> Int',result['signatureHelp']['label'])
        lsp=convert('textDocument/signatureHelp',result,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
        self.assertEqual(lsp['activeParameter'],0)
        source='fn index(value)->String{"own"};let answer=index(1)'
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='answer'),'String')

    def test_slice_field_shapes_and_integer_protocol_hint_stay_shared(self):
        result=analyze('let make=slice_key;let spec=make(1);spec.')
        self.assertEqual({c['label'] for c in result['completions']},{'start','stop','step'})
        self.assertTrue(all('Int | Null' in c['detail'] for c in result['completions']))
        self.assertEqual(analyze('slice_key(step=')['signatureHelp']['activeParameter'],2)
        self.assertIn('null',analyze('slice_key(')['signatureHelp']['label'])
        self.assertIn('__index__',analyze('index(')['signatureHelp']['label'])
        self.assertIn('IR2',analyze('gpu_map(')['signatureHelp']['label'])

    def test_formatter_program_source_semantics_and_highlights(self):
        formatted=format_source(SOURCE,line_width=64);program=parse_program(SOURCE)
        self.assertEqual(program['semanticHash'],parse_program(formatted)['semanticHash'])
        self.assertEqual(program['semanticHash'],parse_program(format_program(program))['semanticHash'])
        self.assertEqual(format_source(formatted,line_width=64),formatted)
        self.assertEqual(Framework.evaluate(SOURCE),expected())
        self.assertEqual(Framework.evaluate(format_program(program)),expected())
        result=analyze(SOURCE);self.assertEqual(result['diagnostics'],[])
        for marker, kind in [('index;','function'),('__index__','function'),('Int{','type')]:
            at=len(SOURCE[:SOURCE.index(marker)].encode('utf-16-le'))//2
            self.assertTrue(any(t['from']==at and t['type']==kind for t in result['tokens']))

    def test_analysis_remains_inert_and_legacy_dialect_unchanged(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed source')):
            self.assertEqual(analyze('class P{fn __index__(self)->Int{tool("never",0);1}}let answer=index(P())')['diagnostics'],[])
        self.assertEqual(Framework.evaluate(ProgramBundle('[10,20][1]',language_versions={'main.chaos':1})),20)
        source='class P{fn __index__(self)->Int{1}}[10,20][P()]'
        with self.assertRaises(LanguageError):Framework.evaluate(ProgramBundle(source,language_versions={'main.chaos':1}))
        with self.assertRaises(LanguageError):Framework.evaluate('class P{fn __index__(self)->Int{1}}[10,20].[P()]')
        self.assertEqual(Framework.evaluate(source),20)

    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_existing_structural_editor_changes_the_method_body(self):
        payload={'info':{'syntax':SYNTAX_SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run(['node',str(ROOT/'tests/index_protocol_controls.cjs')],input=json.dumps(payload),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        changed=json.loads(result.stdout);self.assertEqual(changed['status'],'passed')
        self.assertEqual(Framework.evaluate(format_program(changed['program'])),expected(2))


class IndexProtocolHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Framework(Path(self.tmp.name)/'data',settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.tmp.name)/'control',update_home=Path(self.tmp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
    def close_server(self):self.server.shutdown();self.thread.join(3);self.server.server_close()

    def test_sdk_http_cli_and_pure_source_are_the_same_language(self):
        project=self.client.create_project('indices',source=SOURCE)
        self.assertEqual(self.client.start('indices',project['version'],None,mode='source').result(),expected())
        self.assertEqual(self.client.program('indices')['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
        self.assertEqual(self.client.analyze('index(')['signatureHelp']['parameters'][0]['name'],'value')
        result=subprocess.run([sys.executable,'-m','chaos','run','-'],input=SOURCE,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),expected())

    @unittest.skipUnless(sync_playwright,'Playwright unavailable')
    def test_real_browser_hints_structure_format_and_headless_consistency(self):
        project=self.app.get_project('workspace');project.update(source=SOURCE,files={'main.chaos':SOURCE},entry='main.chaos');self.app.save_project(project,from_view='source')
        folder=os.environ.get('CHAOS_INDEX_PROTOCOL_AUDIT');output=Path(folder) if folder else None
        if output:output.mkdir(parents=True,exist_ok=True)
        errors=[];external=[];checks=[]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                def route(r):
                    if r.request.url.startswith(self.origin+'/'):r.continue_()
                    else:external.append(r.request.url);r.abort()
                context.route('**/*',route)
                def shot(name):
                    if output:page.screenshot(path=str(output/(name+'.png')))
                def run(n):
                    page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runStatus")?.textContent.includes("已完成")&&document.querySelector("#resultPanel")?.textContent.includes("normalized")')
                    value=json.loads(page.locator('#resultPanel').inner_text());self.assertEqual(value,expected(n));checks.append(value);return value
                page.goto(self.origin);workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                workbench_button(page,'formatSource');formatted=format_source(SOURCE);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                self.assertTrue(page.locator('.ide-token-function',has_text='__index__').count())
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString();e.dispatch({selection:{anchor:s.indexOf("convert(offset)")+8},scrollIntoView:true})}')
                workbench_button(page,'ideSignatureButton');page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("value")');shot('index-signature')
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString();e.dispatch({selection:{anchor:s.indexOf("[spec]")+1},scrollIntoView:true});e.focus()}');page.locator('.cm-editor').press('Escape')
                workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');original=run(1);shot('index-result')
                current=self.client.get_project('workspace');self.assertEqual(self.client.start('workspace',current['version'],None,mode='source').result(),original);self.assertEqual(Framework.evaluate(current['source']),original)
                workbench_view(page,'structure');page.wait_for_function('()=>programDraft?.root?.statements?.[0]?.body')
                target=page.evaluate('programDraft.root.statements[0].body.statements[0].function.body.statements[0].id');page.locator('[data-syntax="'+target+'"]').click()
                field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='value'));field.locator('input').fill('2');field.get_by_role('button',name='应用',exact=True).click();shot('index-properties')
                workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty');changed=self.client.get_project('workspace');program=parse_program(changed['source'])
                self.assertEqual(program['root']['statements'][0]['body']['statements'][0]['function']['body']['statements'][0]['value'],2)
                workbench_view(page,'source');workbench_button(page,'formatSource');readable=format_source(changed['source']);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=readable)
                self.assertEqual(parse_program(readable)['semanticHash'],program['semanticHash']);workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');modified=run(2)
                changed=self.client.get_project('workspace');self.assertEqual(self.client.start('workspace',changed['version'],None,mode='source').result(),modified);self.assertEqual(Framework.evaluate(changed['source']),modified)
                page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runStatus")?.textContent.includes("已完成")');page.evaluate('()=>ChaosIDE.editor().dispatch({selection:{anchor:0},scrollIntoView:true})');page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")');self.assertTrue(page.locator('.ide-token-function',has_text='__index__').count());shot('index-edited-source')
                editor=page.locator('.cm-content[contenteditable="true"]');editor.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text('ind');page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")');page.keyboard.press('Control+Space');page.wait_for_selector('.cm-tooltip-autocomplete');self.assertIn('index',page.locator('.cm-tooltip-autocomplete .cm-completionLabel').all_text_contents());shot('index-completion');page.keyboard.press('Escape')
                page.evaluate('(text)=>{const e=ChaosIDE.editor();e.dispatch({changes:{from:0,to:e.state.doc.length,insert:text},selection:{anchor:0},scrollIntoView:true})}',readable);page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                page.set_viewport_size({'width':390,'height':900});self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'));shot('index-mobile')
                self.assertEqual(errors,[]);self.assertEqual(external,[])
                if output:
                    (output/'browser-result.json').write_text(json.dumps({'status':'passed','sourceRoot':str(ROOT),'checks':checks,'sourceStructureSDKHTTPPureEqual':True,'signature':True,'completion':True,'formatSemanticHash':parse_program(formatted)['semanticHash'],'editedSemanticHash':program['semanticHash'],'mobileNoOverflow':True,'errors':errors,'externalRequests':external,'sourceHashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['chaos/indexing.py','chaos/language.py','chaos/native_specs.py','chaos/ide_language.py','tests/test_index_protocol.py','tests/test_index_protocol_surfaces.py','tests/index_protocol_controls.cjs']},'screenshots':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.png')}},indent=2)+'\n')
            finally:browser.close()


if __name__=='__main__':unittest.main()
