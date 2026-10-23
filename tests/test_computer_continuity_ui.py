"""Real browser projection of native persistent Computer leases; no mocked UI state."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from chaos import Framework, Settings, __version__
from chaos.server import make_server

ROOT=Path(__file__).resolve().parents[1]
SOURCE='''async with await computer_connect("form") as computer {
  let fresh = await computer_observe(computer);
  if input.mode == "navigate" {
    observation_info(await computer(computer_action(fresh,{action:"navigate",url:input.url})))
  } else if input.mode == "hold" {
    await sleep(20000); observation_info(fresh)
  } else if input.mode == "reconcile" {
    await computer_reconcile(computer,fresh,"Inspected this fresh page; no retry requested"); computer_info(computer)
  } else { observation_info(fresh) }
}'''

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ComputerContinuityUI(unittest.TestCase):
    def test_real_sessions_hints_and_refresh_preserve_editor_and_action_drafts(self):
        from playwright.sync_api import sync_playwright
        class Form(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                body=b'<title>Continuous form</title><label>Draft<input id="draft"></label><button id="continue">Continue</button>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
        fixture=ThreadingHTTPServer(('127.0.0.1',0),Form);fixture_thread=threading.Thread(target=fixture.serve_forever,daemon=True);fixture_thread.start()
        self.addCleanup(fixture.server_close);self.addCleanup(fixture.shutdown)
        fixture_url='http://127.0.0.1:'+str(fixture.server_port)
        output=os.environ.get('CHAOS_COMPUTER_CONTINUITY_AUDIT');out=Path(output) if output else None
        if out:out.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='chaos-continuity-ui-') as folder:
            settings=Settings(':memory:');settings.update({'runtime':{'workspace':folder,'timeout':30}},1)
            with Framework(Path(folder)/'db',settings=settings) as app:
                project=app.create_project('continuity-ui',source=SOURCE,capabilities=['browser.control'])
                first=app.start(project['id'],project['version'],{'mode':'navigate','url':fixture_url},mode='source');observation=first.result(20)
                initial=app.computer_session(project['id'],'form');self.assertEqual(initial['status'],'idle')
                example=(ROOT/'examples/continuous-computer.chaos').read_text()
                app.create_project('documented-example',source=example,capabilities=['browser.control'])
                one=app.start('documented-example',1,{'key':'guide','url':fixture_url},mode='source').result(15)
                two=app.start('documented-example',1,{'key':'guide','url':fixture_url},mode='source').result(15)
                self.assertEqual(one['stableSessionId'],two['stableSessionId']);self.assertNotEqual(one['computerId'],two['computerId'])
                self.assertTrue(two['continued']);self.assertEqual(one['url'],two['url'])

                server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
                errors=[];external=[];held=None
                try:
                    with sync_playwright() as pw:
                        browser=pw.chromium.launch(headless=True)
                        try:
                            context=browser.new_context(viewport={'width':1550,'height':1100},permissions=['clipboard-read','clipboard-write'])
                            def route(req):
                                if req.request.url.startswith(origin+'/'):req.continue_()
                                else:external.append(req.request.url);req.abort()
                            context.route('http**://**',route)
                            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                            page.goto(origin+'/?project=continuity-ui');page.locator('#ideEditorMount .cm-editor').wait_for(state='attached')
                            workbench_view(page,'source')
                            draft=SOURCE+'\n// 🙂 preserved source draft'
                            page.evaluate('(source)=>{const view=ChaosIDE.editor();view.dispatch({changes:{from:0,to:view.state.doc.length,insert:source}})}',draft)
                            before=page.evaluate('({source:ChaosIDE.editor().state.doc.toString(),dirty,version:project.version,editSerial})')
                            workbench_button(page,'computersButton')
                            page.locator('[data-session-key="form"]').wait_for()
                            page.wait_for_function("()=>document.querySelector('.computer-session-detail .chaos-code')?.dataset.highlight==='ready'")
                            detail=page.locator('.computer-session-detail');self.assertIn(initial['id'],detail.text_content());self.assertIn('状态 idle',detail.text_content())
                            token=detail.locator('.chaos-token-function').filter(has_text='computer_connect');self.assertEqual(token.count(),1);self.assertIn('key',token.get_attribute('title'))
                            color=token.evaluate('el=>getComputedStyle(el).color');plain=detail.locator('code').evaluate('el=>getComputedStyle(el).color');self.assertNotEqual(color,plain)
                            expected='async with await computer_connect("form") as computer {\n  let fresh = await computer_observe(computer);\n  observation_info(fresh)\n}'
                            detail.locator('.copy-code').click();self.assertEqual(page.evaluate('navigator.clipboard.readText()'),expected)
                            if out:page.screenshot(path=str(out/'session-idle-hints.png'),full_page=True)
                            page.locator('[data-record-id="'+observation['id']+'"]').click()
                            page.locator('[aria-label="动作类型"]').select_option('navigate')
                            page.locator('[aria-label="目标 URL"]').fill('http://draft.invalid/keep')
                            held=app.start(project['id'],project['version'],{'mode':'hold'},mode='source')
                            page.wait_for_function("()=>document.querySelector('.computer-session-detail')?.textContent.includes('状态 leased')")
                            self.assertIn(held.id,detail.text_content());self.assertIn('generation 2',detail.text_content())
                            self.assertEqual(page.locator('[aria-label="目标 URL"]').input_value(),'http://draft.invalid/keep')
                            self.assertEqual(page.evaluate('({source:ChaosIDE.editor().state.doc.toString(),dirty,version:project.version,editSerial})'),before)
                            if out:
                                page.wait_for_function("()=>document.querySelector('.computer-session-detail .chaos-code')?.dataset.highlight==='ready'")
                                page.locator('.computer-dialog').evaluate('el=>el.scrollTop=0')
                                page.screenshot(path=str(out/'session-held-draft.png'),full_page=True)
                            held.cancel();held.wait(10);held=None
                            # A genuine dispatched navigation fails, producing uncertain action evidence.
                            failed=app.start(project['id'],project['version'],{'mode':'navigate','url':'http://127.0.0.1:1/unreachable'},mode='source')
                            with self.assertRaises(Exception):failed.result(15)
                            page.wait_for_function("()=>document.querySelector('.computer-session-detail .error')?.textContent.includes('需要协调')")
                            uncertain=app.computer_session(project['id'],'form');self.assertTrue(uncertain['requiresReconcile'])
                            rows=app.computer_history(project['id'],run_id=failed.id)['items'];self.assertTrue(any(row['kind']=='action' and row['status']=='uncertain' for row in rows))
                            self.assertEqual(page.locator('[aria-label="目标 URL"]').input_value(),'http://draft.invalid/keep')
                            if out:
                                page.wait_for_function("()=>document.querySelector('.computer-session-detail .chaos-code')?.dataset.highlight==='ready'")
                                page.locator('.computer-dialog').evaluate('el=>el.scrollTop=0')
                                page.screenshot(path=str(out/'session-uncertain.png'),full_page=True)
                            result=app.start(project['id'],project['version'],{'mode':'reconcile'},mode='source').result(15)
                            self.assertFalse(result['requiresReconcile'])
                            page.wait_for_function("()=>document.querySelector('.computer-session-detail')?.textContent.includes('无待协调效果')")
                            page.set_viewport_size({'width':390,'height':900})
                            self.assertTrue(page.locator('.computer-dialog').evaluate('el=>el.scrollWidth<=el.clientWidth'))
                            if out:
                                page.wait_for_function("()=>document.querySelector('.computer-session-detail .chaos-code')?.dataset.highlight==='ready'")
                                page.locator('.computer-dialog').evaluate('el=>el.scrollTop=0')
                                page.screenshot(path=str(out/'session-mobile.png'),full_page=True)
                            page.set_viewport_size({'width':1550,'height':1100});page.locator('.computer-header button').click()
                            self.assertEqual(page.evaluate('({source:ChaosIDE.editor().state.doc.toString(),dirty,version:project.version,editSerial})'),before)
                            self.assertEqual(app.get_project(project['id'])['source'],SOURCE)
                            self.assertEqual(errors,[]);self.assertEqual(external,[])
                            if out:
                                (out/'browser-result.json').write_text(json.dumps({'status':'passed','version':__version__,'checks':['real connect and navigation','stable session metadata','shared native hint and computed color','raw source copy','actual held run','SSE preserves source and action drafts','actual uncertain action','fresh observation and reconciliation','390px layout','no project source mutation'],'initialSession':initial,'uncertainSession':uncertain,'errors':errors,'externalHTTP':external,'sourceHashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'web/computers.js',ROOT/'web/computers.css',Path(__file__).resolve()]}},ensure_ascii=False,indent=2)+'\n')
                        finally:browser.close()
                finally:
                    if held:held.cancel()
                    server.shutdown();server.server_close();thread.join(3)

if __name__=='__main__':unittest.main()
