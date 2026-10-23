"""Real controls and browser use the same comprehension source, schema and IR."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from theme_assertions import theme_color
from chaos import Framework, Settings, __version__
from chaos.language import Evaluator
from chaos.program import SHAPES, parse_program, templates, format_program
from chaos.server import make_server

ROOT=Path(__file__).resolve().parent.parent
SOURCE='fn* inputs(){yield 20;yield 21};async fn work(value:Int)->Int{value*2};let jobs=[work(value) for value in inputs()];let keyed={letter:letter+letter for letter in "ab"};let lazy=(value+1 for value in [1,2]);let stream=async (await work(value) for value in [2,3]);[await gather(jobs),keyed,collect(lazy),await acollect(stream)]'


class ComprehensionUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_actual_structural_controls_preserve_clause_invariants(self):
        fixture={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program('[x*2 for x in [20,21] if x>0]')}
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/comprehension_controls.cjs')],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout);self.assertEqual(report['status'],'passed')
        self.assertEqual(Evaluator().run(format_program(report['program'])),[40,42])

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_real_browser_completion_format_save_ir_docs_and_live_source(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='chaos-comprehension-browser-') as folder, Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('comprehension',source=SOURCE,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[];checks=[]
            output=os.environ.get('CHAOS_COMPREHENSION_AUDIT')
            if output:Path(output).mkdir(parents=True,exist_ok=True)
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1050})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('http**://**',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=comprehension');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        content=page.locator('.cm-content[contenteditable="true"]')
                        def shot(name,target=page):
                            if output:target.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        def replace(text):
                            content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(text)
                        def run(expected):
                            page.locator('#runButton').click()
                            page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=expected)
                            checks.append({'version':page.evaluate('runVersion'),'result':expected})
                        # Edit the leading projection using the later lexical binder.
                        draft='[val for value in "hi"]';replace(draft)
                        page.keyboard.press('Home')
                        for _ in range(4):page.keyboard.press('ArrowRight')
                        page.keyboard.press('Control+Space');page.locator('.cm-completionLabel').filter(has_text='value').first.click()
                        self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),'[value for value in "hi"]')
                        shot('projection-completion')
                        draft='fn pair(left:String,right:String){left+right};[pair(value, "") for value in "hi"]';replace(draft)
                        page.keyboard.press('Home')
                        for _ in range(draft.index(', ""')+2):page.keyboard.press('ArrowRight')
                        workbench_button(page,'ideSignatureButton');page.wait_for_function("()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('right')");shot('projection-signature')
                        replace(SOURCE);workbench_button(page,'formatSource');page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>4');workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask')
                        saved=app.get_project('comprehension');self.assertGreater(saved['version'],1)
                        page.reload();page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source');page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=saved['source'])
                        expected=[[40,42],{'a':'aa','b':'bb'},[2,3],[4,6]];run(expected)
                        page.wait_for_function("()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新')")
                        function=page.locator('.ide-token-function').filter(has_text='work').first
                        self.assertEqual(function.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        shot('saved-source-result')
                        workbench_view(page,'structure');page.wait_for_function('()=>programDraft&&programDraft.root.statements.some(n=>n.kind==="let"&&n.name==="lazy")')
                        target=page.evaluate('programDraft.root.statements.find(n=>n.kind==="let"&&n.name==="lazy").value.element.right.id')
                        page.locator('[data-syntax="'+target+'"]').click();field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='value'))
                        field.locator('input').fill('2');field.get_by_role('button',name='应用',exact=True).click();workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty')
                        updated=app.get_project('comprehension');self.assertGreater(updated['version'],saved['version']);shot('ir-edit')
                        workbench_view(page,'source');page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=updated['source']);expected=[[40,42],{'a':'aa','b':'bb'},[3,4],[4,6]];run(expected)
                        # A separate HTTP client changes the saved source; the open editor catches up.
                        remote_source=updated['source'].replace('value * 2','value * 3')
                        self.assertNotEqual(remote_source,updated['source'])
                        body=dict(updated,source=remote_source,files=dict(updated['files'],**{updated['entry']:remote_source}),editFrom='source')
                        response=page.request.put(origin+'/api/v1/projects/comprehension',data=body);self.assertTrue(response.ok,response.text());remote=response.json()
                        page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source&&!dirty',arg=remote_source,timeout=15000)
                        expected=[[60,63],{'a':'aa','b':'bb'},[3,4],[6,9]];run(expected);shot('http-source-sync')
                        docs=context.new_page();docs.on('pageerror',lambda error:errors.append(str(error)));docs.goto(origin+'/docs#/comprehensions');docs.locator('#article-content pre code .chaos-token-keyword').first.wait_for()
                        self.assertIn('推导',docs.locator('.article-header h1').inner_text());self.assertIn(__version__,docs.locator('#docs-version').inner_text())
                        token=docs.locator('#article-content pre code .chaos-token-function').first
                        self.assertTrue(token.is_visible());self.assertNotEqual(token.evaluate('el=>getComputedStyle(el).color'),token.evaluate('el=>getComputedStyle(el.parentElement).color'))
                        self.assertTrue(docs.locator('#article-content pre code').first.inner_text().strip());shot('docs-highlight',docs)
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            evidence={'status':'passed','sourceRoot':str(ROOT),'origin':origin,'runtimeVersion':__version__,'savedVersion':saved['version'],'irVersion':updated['version'],'remoteVersion':remote['version'],'checks':checks,'projectionCompletion':True,'signatureHelp':True,'semanticColor':True,'docsHighlight':True,'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('chaos/program.py','chaos/ide_language.py','chaos/source_format.py','web/language.js','tests/test_comprehension_ui.py','tests/test_comprehension_program.py','tests/test_comprehension_ide.py','tests/comprehension_controls.cjs')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
