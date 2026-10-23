"""Definition navigation keeps current source drafts and ignores late analysis."""
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
from chaos import Framework, Settings
from chaos.server import make_server


ROOT=Path(__file__).resolve().parent.parent
SOURCE='import "lib/math.chaos" as M; let result=M.add(20,22); result'
LIBRARY='fn add(left,right){left+right}; export {add}'


class DefinitionUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_navigation_adapter_contracts(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/definition_controls.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_browser_unsaved_imports_selection_f12_and_stale_reply(self):
        from playwright.sync_api import sync_playwright, Error
        output=os.environ.get('CHAOS_DEFINITION_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='chaos-definition-') as folder, Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('definitions',source=SOURCE,files={'lib/math.chaos':LIBRARY},capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[];checks=[]
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1000})
                        def local_only(route):
                            if route.request.url.startswith(origin+'/'):route.continue_()
                            else:external.append(route.request.url);route.abort()
                        context.route('http**://**',local_only)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=definitions');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        def replace(text):
                            page.evaluate('text=>{const e=ChaosIDE.editor();e.dispatch({changes:{from:0,to:e.state.doc.length,insert:text},selection:{anchor:text.length}});e.focus()}',text)
                        def cursor(needle):
                            page.evaluate('needle=>{const e=ChaosIDE.editor(),pos=e.state.doc.toString().lastIndexOf(needle);e.dispatch({selection:{anchor:pos}});e.focus()}',needle)
                        def active():return page.evaluate('activeFile')
                        def selected():return page.evaluate('()=>{const e=ChaosIDE.editor();return e.state.sliceDoc(e.state.selection.main.from,e.state.selection.main.to)}')
                        def current():return page.evaluate('ChaosIDE.editor().state.doc.toString()')
                        def shot(name):
                            if output:page.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        page.wait_for_function('()=>document.querySelector("#ideLanguageStatus").textContent.includes("分析已更新")')
                        function_token=page.locator('.ide-token-function').filter(has_text='add').first
                        self.assertEqual(function_token.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        cursor('22');workbench_button(page,'ideSignatureButton')
                        page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("right")')
                        self.assertIn('add',page.locator('#ideSignatureHelp').inner_text());checks.append('visible semantic color and imported callable parameter hint');shot('language-hints')
                        formatted=Framework.format_source(SOURCE)
                        workbench_button(page,'formatSource');page.wait_for_function('text=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                        self.assertGreater(page.evaluate('ChaosIDE.editor().state.doc.lines'),1)
                        checks.append('actual format button renders shared comment-preserving formatting');shot('formatted-source')
                        page.evaluate('ChaosIDE.editor().focus()');page.keyboard.press('ControlOrMeta+Z')
                        self.assertEqual(current(),SOURCE)
                        # Every file is edited through the actual CodeMirror view;
                        # switching tabs must preserve each unsaved document state.
                        page.locator('#ideFileTree [data-path="lib/math.chaos"]').click()
                        library='// 🧠 unsaved library\n'+LIBRARY+'\n// keep library draft'
                        replace(library);page.locator('#ideFileTree [data-path="main.chaos"]').click()
                        main=SOURCE+'\n// 🙂 keep main draft';replace(main);cursor('add(20')
                        with page.expect_response(lambda response:response.url.endswith('/api/language/analyze') and response.request.post_data_json.get('position')==main.index('add(20')) as response:
                            workbench_button(page,'ideDefinitionButton')
                        payload=response.value.json();expected=len(library[:library.index('add')].encode('utf-16-le'))//2
                        self.assertEqual(payload['definition'],{'file':'lib/math.chaos','from':expected,'to':expected+3})
                        page.wait_for_function('()=>activeFile==="lib/math.chaos"')
                        self.assertEqual(current(),library);self.assertEqual(selected(),'add');self.assertTrue(page.evaluate('ChaosIDE.editor().hasFocus'))
                        self.assertEqual(page.evaluate('project.files["main.chaos"]'),main)
                        self.assertEqual(app.get_project('definitions')['version'],1);checks.append('actual HTTP definition follows unsaved UTF-16 target and preserves both drafts');shot('unsaved-definition')
                        page.locator('#ideFileTree [data-path="main.chaos"]').click();self.assertEqual(current(),main);cursor('result\n')
                        page.keyboard.press('F12');page.wait_for_function('()=>{const e=ChaosIDE.editor();return e.state.selection.main.to-e.state.selection.main.from===6}')
                        self.assertEqual(active(),'main.chaos');self.assertEqual(selected(),'result');self.assertEqual(page.evaluate('ChaosIDE.editor().state.selection.main.from'),main.index('result'));checks.append('F12 selects local lexical declaration');shot('local-definition')
                        # Hold a response from the shared endpoint while a real
                        # edit changes the current document, then release it.
                        held=[]
                        page.route('**/api/language/analyze',lambda route:held.append(route))
                        cursor('add(20');workbench_button(page,'ideDefinitionButton');page.wait_for_timeout(80);self.assertTrue(held)
                        newer=main+'\n// edited while definition was pending';replace(newer)
                        for pending in list(held):
                            try:pending.fulfill(status=200,content_type='application/json',body=json.dumps({'complete':True,'tokens':[],'diagnostics':[],'definition':payload['definition']}))
                            except Error:pass
                        page.unroute('**/api/language/analyze');page.wait_for_timeout(120)
                        self.assertEqual(active(),'main.chaos');self.assertEqual(current(),newer);self.assertEqual(page.evaluate('project.files["lib/math.chaos"]'),library);checks.append('late definition cannot navigate a newer edit')
                        # File navigation alone also invalidates an outstanding result.
                        held=[];page.route('**/api/language/analyze',lambda route:held.append(route));cursor('result\n');workbench_button(page,'ideDefinitionButton');page.wait_for_timeout(80);self.assertTrue(held)
                        page.locator('#ideFileTree [data-path="lib/math.chaos"]').click()
                        local={'file':'main.chaos','from':main.index('result'),'to':main.index('result')+6}
                        for pending in list(held):
                            try:pending.fulfill(status=200,content_type='application/json',body=json.dumps({'complete':True,'tokens':[],'diagnostics':[],'definition':local}))
                            except Error:pass
                        page.unroute('**/api/language/analyze');page.wait_for_timeout(120)
                        self.assertEqual(active(),'lib/math.chaos');self.assertEqual(current(),library);self.assertEqual(page.evaluate('project.files["main.chaos"]'),newer);checks.append('late definition cannot navigate a different active file');shot('drafts-preserved')
                        self.assertEqual(errors,[]);self.assertEqual(external,[]);self.assertEqual(app.get_project('definitions')['version'],1)
                        if output:
                            evidence={'status':'passed','sourceRoot':str(ROOT),'checks':checks,'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('chaos/ide_language.py','web/ide.js','tests/test_definition_language.py','tests/test_definition_ui.py','tests/definition_controls.cjs')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
