"""Actual multibuffer reference/rename authoring, with causal stale responses."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from theme_assertions import theme_color
from chaos import Client,Framework,Settings,__version__
from chaos.program import parse_program
from chaos.server import make_server
from tests.test_symbols_surfaces import SOURCE,LIBRARY,REVERSE,FILES
ROOT=Path(__file__).resolve().parent.parent
PRODUCT_FILES=('chaos/framework.py','chaos/client.py','chaos/server.py','chaos/openapi.py','chaos/lsp.py','chaos/lsp_adapters.py','chaos/lsp_workspace.py','chaos/ide_agent.py','web/ide.js','web/ide.css','web/sync.js','tests/test_symbols_surfaces.py','tests/test_symbols_lsp.py','tests/test_symbols_ui.py')
def hashes():return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in PRODUCT_FILES}
def units(text):return len(text.encode('utf-16-le'))//2

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright unavailable')
class SymbolsUI(unittest.TestCase):
    def browser(self,work):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='chaos-symbol-browser-')as folder,Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:'))as app:
            app.create_project('symbols',source=SOURCE,files=FILES,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
            output=os.environ.get('CHAOS_SYMBOLS_AUDIT');output=Path(output)if output else None
            if output:output.mkdir(parents=True,exist_ok=True)
            errors=[];external=[]
            try:
                with sync_playwright()as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1600,'height':1450})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('**/*',route);page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=symbols');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        work(page,app,Client(origin),output)
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()
    def cursor(self,page):
        text=page.evaluate('ChaosIDE.editor().state.doc.toString()');position=units(text[:text.index('L.answer')+2]);page.evaluate('(position)=>ChaosIDE.editor().dispatch({selection:{anchor:position},scrollIntoView:true})',position)
    def open(self,page):
        self.cursor(page);workbench_button(page,'ideRenameSymbolButton');dialog=page.get_by_role('dialog',name='符号引用与重命名');dialog.wait_for();page.wait_for_function('()=>!document.querySelector("#symbolDialog input").disabled');return dialog
    def preview(self,page):
        dialog=self.open(page);dialog.get_by_label('符号新名称').fill('respond');dialog.get_by_role('button',name='预览重命名',exact=True).click();page.wait_for_function('()=>!document.querySelector("#symbolApply").disabled');return dialog
    def test_a_stale_edit_switch_and_remote_commit_preserve_every_draft(self):
        def work(page,app,client,output):
            dialog=self.open(page);dialog.get_by_label('符号新名称').fill('respond');held=[]
            page.route('**/api/language/rename',lambda route:held.append((route,route.fetch())))
            dialog.get_by_role('button',name='预览重命名',exact=True).click();page.wait_for_timeout(100);self.assertTrue(held)
            content=page.locator('.cm-content[contenteditable="true"]');content.click();page.keyboard.press('ControlOrMeta+End');page.keyboard.insert_text('\n// newer draft')
            edited=page.evaluate('JSON.stringify(project.files)')
            for route,response in held:route.fulfill(response=response)
            page.unroute('**/api/language/rename');page.wait_for_function('()=>document.querySelector("#toast")?.textContent.includes("重构期间")')
            self.assertTrue(dialog.locator('#symbolApply').is_disabled());self.assertEqual(page.evaluate('JSON.stringify(project.files)'),edited);self.assertEqual(app.get_project('symbols')['version'],1)
            dialog.get_by_role('button',name='关闭',exact=True).click();dialog=self.preview(page)
            page.locator('#ideFileTree [data-path="reverse.chaos"]').click();dialog.locator('#symbolApply').click();page.wait_for_function('()=>document.querySelector("#toast")?.textContent.includes("预览已过期")')
            self.assertEqual(page.evaluate('JSON.stringify(project.files)'),edited);self.assertEqual(page.evaluate('activeFile'),'reverse.chaos')
            dialog.get_by_role('button',name='关闭',exact=True).click();page.locator('#ideFileTree [data-path="main.chaos"]').click();dialog=self.preview(page)
            old=client.get_project('symbols');remote={**old['files'],'reverse.chaos':old['files']['reverse.chaos']+'\n// SDK revision'};saved=client.save_project({**old,'files':remote})
            page.wait_for_function('(version)=>remoteProject?.version===version',arg=saved['version']);dialog.locator('#symbolApply').click()
            page.wait_for_function('()=>document.querySelector("#toast")?.textContent.includes("预览已过期")');self.assertEqual(page.evaluate('JSON.stringify(project.files)'),edited)
            self.assertEqual(app.get_project('symbols')['files'],remote);self.assertEqual(app.runs('symbols')['items'],[])
            if output:(output/'stale-result.json').write_text(json.dumps({'status':'passed','sourceRoot':str(ROOT),'sourceHashes':hashes(),'localEditWhileRenamePending':True,'fileSwitchBeforeApply':True,'remoteCASBeforeApply':True,'allDraftsPreserved':True,'actualRemoteVersion':saved['version'],'actualInitialVersion':1,'projectWritesByRefactor':False,'executionRequests':False},indent=2)+'\n')
        self.browser(work)
    def test_b_browser_unsaved_reverse_module_preview_atomic_apply_save_ir_run(self):
        def work(page,app,client,output):
            drafts={}
            for name in ('lib.chaos','reverse.chaos','main.chaos'):
                page.locator('#ideFileTree [data-path="'+name+'"]').click();content=page.locator('.cm-content[contenteditable="true"]');content.click();page.keyboard.press('ControlOrMeta+End');page.keyboard.insert_text('\n// retained '+name);drafts[name]=page.evaluate('ChaosIDE.editor().state.doc.toString()')
            dialog=self.open(page);self.assertEqual(dialog.locator('.symbol-reference').count(),4)
            self.assertIn('reverse.chaos',dialog.locator('.symbol-references').inner_text())
            self.assertEqual(app.get_project('symbols')['version'],1)
            if output:page.screenshot(path=str(output/'symbol-unsaved-references.png'),full_page=True)
            dialog.get_by_label('符号新名称').fill('respond');dialog.get_by_role('button',name='预览重命名',exact=True).click();page.wait_for_function('()=>!document.querySelector("#symbolApply").disabled')
            self.assertEqual(dialog.locator('.symbol-diff details').count(),3)
            self.assertEqual(page.evaluate('project.files'),drafts);self.assertEqual(app.get_project('symbols')['files'],FILES)
            if output:page.screenshot(path=str(output/'symbol-rename-candidate-diff.png'),full_page=True)
            candidate=Framework.rename(drafts['main.chaos'],files=drafts,position=units(drafts['main.chaos'][:drafts['main.chaos'].index('L.answer')+2]),new_name='respond')
            dialog.locator('#symbolApply').click();page.wait_for_function('()=>!document.querySelector("#symbolDialog").open')
            self.assertEqual(page.evaluate('project.files'),candidate['files']);self.assertEqual(app.get_project('symbols')['version'],1)
            for name,text in candidate['files'].items():
                page.locator('#ideFileTree [data-path="'+name+'"]').click();self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),text)
            page.locator('#ideFileTree [data-path="main.chaos"]').click();workbench_button(page,'formatSource');formatted=Framework.format_source(candidate['files']['main.chaos']);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
            self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(candidate['files']['main.chaos'])['semanticHash'])
            page.evaluate('(position)=>ChaosIDE.editor().dispatch({selection:{anchor:position}})',units(formatted[:formatted.index('alias(40')+len('alias(')]));workbench_button(page,'ideSignatureButton');page.wait_for_function('()=>document.querySelector("#ideSignatureHelp")?.textContent.includes("value: Int")')
            function=page.locator('.ide-token-function').filter(has_text='respond').first;function.wait_for();self.assertEqual(function.evaluate('element=>getComputedStyle(element).color'),theme_color(page, 'function'))
            workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');saved=app.get_project('symbols');self.assertEqual(saved['version'],2)
            workbench_view(page,'structure');page.wait_for_function('()=>programDraft&&programDraft.semanticHash');self.assertEqual(page.evaluate('programDraft.semanticHash'),parse_program(formatted)['semanticHash']);workbench_view(page,'source')
            page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()==="42"')
            run=app.get_run(page.evaluate('currentRun'));self.assertEqual(run['version'],saved['version']);self.assertEqual(run['output'],42)
            page.wait_for_function('(version)=>document.querySelector("#runStatus").textContent.trim()==="已完成 · v"+version',arg=saved['version'])
            self.assertEqual(page.locator('#runStatus').inner_text(),'已完成 · v'+str(saved['version']))
            if output:page.screenshot(path=str(output/'symbol-renamed-result-source-ir.png'),full_page=True)
            refs=client.references(saved['source'],files=saved['files'],position=units(saved['source'][:saved['source'].index('L.respond')+2]));self.assertTrue(refs['complete']);self.assertEqual(len(refs['references']),4)
            if output:
                stale_path=output/'stale-result.json';stale=json.loads(stale_path.read_text())if stale_path.exists()else None
                if stale:self.assertEqual(stale['sourceHashes'],hashes())
                report={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'savedVersion':saved['version'],'runVersion':run['version'],'completedRunHeader':page.locator('#runStatus').inner_text(),'runId':run['id'],'result':42,'executedSourceHashes':{name:hashlib.sha256(text.encode()).hexdigest()for name,text in saved['files'].items()},'semanticHash':parse_program(formatted)['semanticHash'],'multiFileEdits':candidate['edits'],'referenceCount':4,'reverseImporterIncluded':True,'threeUnsavedDraftsPreserved':True,'previewDoesNotSave':True,'atomicDraftApply':True,'saveUsesExistingCAS':True,'sourceIRIdentity':True,'formatterSemanticIdentity':True,'semanticHighlight':True,'parameterHints':True,'sdkHttpParity':True,'staleProof':stale,'errors':[],'externalHTTP':[],'sourceHashes':hashes()}
                (output/'browser-result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        self.browser(work)
