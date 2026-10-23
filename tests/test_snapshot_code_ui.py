"""Real snapshot/debugger code presentation, pinned to the executing module bundle."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from chaos import Framework, Settings
from chaos.server import make_server


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class SnapshotCodeUI(unittest.TestCase):
    def test_snapshot_modules_and_debugger_share_lossless_semantics(self):
        from playwright.sync_api import sync_playwright
        main = 'import "lib/value.chaos" as L;\n// 🙂 snapshot source\nL.answer(20,22)'
        lib = 'fn answer(left:Int,right:Int)->Int{\n  left+right\n};export{answer}'
        with tempfile.TemporaryDirectory(prefix='chaos-snapshot-ui-') as folder, Framework(Path(folder)/'db', settings=Settings(':memory:')) as app:
            project = app.create_project('snapshot-code', source=main, files={'lib/value.chaos': lib}, capabilities=[])
            run = app.start_debug(project['id'], project['version'], pause_on_entry=False, breakpoints=[{'file':'lib/value.chaos','line':2}])
            deadline=time.monotonic()+5
            while run.debugger()['status']!='paused' and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(run.debugger()['status'],'paused')
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[];analysis=[]
            try:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1000},permissions=['clipboard-read','clipboard-write'])
                        def route(request):
                            if request.request.url.startswith(origin+'/'): request.continue_()
                            else: external.append(request.request.url);request.abort()
                        context.route('http**://**',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.on('request',lambda req:analysis.append(req.post_data_json) if req.url.endswith('/api/language/analyze') else None)
                        page.goto(origin+'/?project=snapshot-code');page.locator('#ideEditorMount .cm-editor').wait_for(state='attached')
                        page.wait_for_function("()=>document.querySelector('#debugSource')?.dataset.highlight==='ready'")
                        output=os.environ.get('CHAOS_SNAPSHOT_AUDIT')
                        if output:
                            Path(output).mkdir(parents=True,exist_ok=True);page.screenshot(path=str(Path(output)/'debugger-highlight.png'),full_page=True)
                        self.assertEqual(page.locator('#debugSource code').text_content(),lib)
                        self.assertEqual(page.locator('#debugSource [aria-current="step"]').get_attribute('data-line'),'2')
                        self.assertIn('right: Int',page.locator('#debugSource .chaos-token-parameter[title]').last.get_attribute('title'))
                        page.locator('#debugSource .copy-code').click()
                        self.assertEqual(page.evaluate('navigator.clipboard.readText()'),lib)
                        workbench_view(page,'source')
                        page.evaluate("()=>{const v=ChaosIDE.editor();v.dispatch({changes:{from:0,to:v.state.doc.length,insert:'99 // unsaved draft'}})}")
                        before=page.evaluate('({draft:ChaosIDE.editor().state.doc.toString(),version:project.version,edit:editSerial,dirty})')
                        workbench_button(page,'viewSnapshot')
                        page.wait_for_function("()=>document.querySelector('#snapshotSource')?.dataset.highlight==='ready'")
                        self.assertEqual(page.locator('#snapshotSource code').text_content(),main)
                        self.assertEqual(page.locator('#snapshotSource .chaos-token-function').last.text_content(),'answer')
                        self.assertIn('left: Int',page.locator('#snapshotSource .chaos-token-function').last.get_attribute('title'))
                        page.locator('#snapshotSource .copy-code').click()
                        self.assertEqual(page.evaluate('navigator.clipboard.readText()'),main)
                        page.locator('#snapshotSource .chaos-code-wrap').click()
                        self.assertEqual(page.locator('#snapshotSource code').text_content(),main)
                        page.locator('[aria-label="执行快照模块"]').select_option('lib/value.chaos')
                        page.wait_for_function("()=>document.querySelector('#snapshotSource')?.dataset.highlight==='ready'")
                        self.assertEqual(page.locator('#snapshotSource code').text_content(),lib)
                        output=os.environ.get('CHAOS_SNAPSHOT_AUDIT')
                        if output:
                            Path(output).mkdir(parents=True,exist_ok=True);page.screenshot(path=str(Path(output)/'snapshot-module.png'),full_page=True)
                        page.set_viewport_size({'width':390,'height':900})
                        self.assertTrue(page.locator('#snapshotDialog').evaluate('el=>el.scrollWidth<=el.clientWidth'))
                        if output:page.screenshot(path=str(Path(output)/'snapshot-mobile.png'),full_page=True)
                        page.set_viewport_size({'width':1500,'height':1000})
                        page.locator('#closeSnapshot').click()
                        self.assertEqual(page.evaluate('({draft:ChaosIDE.editor().state.doc.toString(),version:project.version,edit:editSerial,dirty})'),before)
                        # The same renderer keeps only the latest response when a frame changes.
                        page.evaluate("()=>ChaosCode.update(document.querySelector('#debugSource'),'42',{activeLine:1})")
                        page.evaluate("()=>ChaosCode.update(document.querySelector('#debugSource'),'43',{activeLine:1})")
                        page.wait_for_function("()=>document.querySelector('#debugSource')?.dataset.highlight==='ready'")
                        self.assertEqual(page.locator('#debugSource code').text_content(),'43')
                        self.assertEqual(page.locator('#debugSource [aria-current="step"]').count(),1)
                        snapshot_calls=[body for body in analysis if body.get('source') in (main,lib)]
                        self.assertTrue(any(body.get('files',{}).get('main.chaos')==main and body.get('files',{}).get('lib/value.chaos')==lib and body.get('languageVersions',{}).get('lib/value.chaos')==2 for body in snapshot_calls))
                        state=run.debugger();run.debug_command('continue',pause_id=state['pauseId']);self.assertEqual(run.result(5),42)
                        self.assertEqual(app.get_project(project['id'])['source'],main)
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:(Path(output)/'browser-result.json').write_text(json.dumps({'status':'passed','result':42,'snapshotVersion':project['version'],'rawCopy':True,'moduleHints':True,'debugActiveLine':True,'draftPreserved':True,'errors':errors,'externalHTTP':external},indent=2)+'\n')
                    finally: browser.close()
            finally:
                run.cancel();server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
