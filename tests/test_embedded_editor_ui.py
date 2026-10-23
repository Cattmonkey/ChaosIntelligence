"""Workflow function editing uses shared language semantics and real save paths."""
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
from chaos import Framework, Settings, __version__
from chaos.program import parse_program
from chaos.server import make_server

ROOT=Path(__file__).resolve().parent.parent
SOURCE='// 🙂 函数草稿\nimport "lib/math.chaos" as M;let [left,right]=[20,22];M.add(left,right)'
FILES={'lib/math.chaos':'fn add(left:Int,right:Int)->Int{left+right};export {add}'}
def graph():
    return {'nodes':[{'id':id,'kind':kind,'label':id,'x':index*260,'y':180,'config':{'source':SOURCE if id=='compute' else 'input'}} for index,(id,kind) in enumerate([('input','input'),('compute','function'),('next','function'),('output','output')])],'edges':[{'id':a+'_'+b,'source':a,'target':b} for a,b in [('input','compute'),('compute','next'),('next','output')]]}


class EmbeddedEditorUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_whitespace_edits_preserve_unicode_and_normalization(self):
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');const window={};vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window});
for(const [before,after] of [['// 🙂 中文\nlet x=42;x','// 🙂 中文\nlet x = 42;\nx\n'],['value[0]','value.[0]\n'],['"🙂  保留"','"🙂  保留"\n']]){
 const changes=window.ChaosEditor.formatChanges(before,after);let rendered=before;for(const change of [...changes].reverse())rendered=rendered.slice(0,change.from)+change.insert+rendered.slice(change.to);assert.equal(rendered,after);for(const change of changes)for(const offset of [change.from,change.to])assert(!(offset>0&&offset<before.length&&/[\uD800-\uDBFF]/.test(before[offset-1])&&/[\uDC00-\uDFFF]/.test(before[offset])));
}
console.log('passed');
'''
        result=subprocess.run([shutil.which('node'),'-e',script,str(ROOT/'web/editor.js')],text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_browser_function_intelligence_format_save_and_stale_guards(self):
        from playwright.sync_api import sync_playwright, Error
        output=os.environ.get('CHAOS_EMBEDDED_EDITOR_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='chaos-embedded-editor-') as tmp,Framework(Path(tmp)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('embedded',graph=graph(),files=FILES,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(tmp)/'control',update_home=Path(tmp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
            errors=[];external=[]
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1600,'height':1100})
                        def local(route):
                            if route.request.url.startswith(origin+'/'):route.continue_()
                            else:external.append(route.request.url);route.abort()
                        context.route('http**://**',local)
                        page=context.new_page();
                        for asset,mime in [('editor.js','text/javascript'),('editor.css','text/css')]:
                            response=page.request.get(origin+'/'+asset);self.assertTrue(response.ok);self.assertIn(mime,response.headers['content-type']);self.assertEqual(response.body(),(ROOT/'web'/asset).read_bytes())
                        page.on('pageerror',lambda error:errors.append(str(error)));page.goto(origin+'/?project=embedded')
                        workbench_view(page,'graph');page.locator('[data-node="compute"]').wait_for();page.locator('[data-node="compute"]').click()
                        editor=page.locator('.embedded-editor');content=editor.locator('.cm-content')
                        def text():return page.locator('textarea.source-field').input_value()
                        def replace(value):content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(value)
                        def ready():
                            try:page.wait_for_function("()=>document.querySelector('.embedded-editor-status')?.textContent.includes('分析已更新')",timeout=10000)
                            except Exception:
                                self.fail(str({'pageErrors':errors,'status':editor.inner_text() if editor.count() else 'no embedded editor'}))
                        def shot(name):
                            if output:page.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        ready();self.assertEqual(text(),SOURCE)
                        replace('let value=42;val');page.keyboard.press('Control+Space');editor.locator('.cm-completionLabel',has_text='value').first.wait_for();shot('workflow-completion');editor.locator('.cm-completionLabel',has_text='value').first.click();ready();self.assertEqual(text(),'let value=42;value')
                        signature='import "lib/math.chaos" as M;M.add(20,22)';replace(signature)
                        for _ in range(3):page.keyboard.press('ArrowLeft')
                        editor.get_by_role('button',name='参数提示',exact=True).click();page.wait_for_function("()=>document.querySelector('.embedded-editor-parameters .active')?.textContent==='right: Int'")
                        function=editor.locator('.chaos-token-function',has_text='add').first;self.assertNotEqual(function.evaluate('el=>getComputedStyle(el).color'),content.evaluate('el=>getComputedStyle(el).color'));shot('workflow-signature')
                        replace(SOURCE)
                        page.evaluate("()=>{const cm=ChaosEditor.get(document.querySelector('textarea.source-field')).editor;const from=cm.state.doc.toString().indexOf('left');cm.dispatch({selection:{anchor:from,head:from+4}})}")
                        editor.get_by_role('button',name='格式化',exact=True).click();page.wait_for_function("()=>document.querySelector('textarea.source-field').value.includes('let [left, right]')")
                        selected=page.evaluate("()=>{const cm=ChaosEditor.get(document.querySelector('textarea.source-field')).editor;return cm.state.sliceDoc(cm.state.selection.main.from,cm.state.selection.main.to)}")
                        self.assertEqual(selected,'left');formatted=text();self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        content.focus();page.keyboard.press('ControlOrMeta+Z');self.assertEqual(text(),SOURCE)
                        # Hidden textarea remains the form bridge, including restored drafts.
                        page.evaluate("value=>{const t=document.querySelector('textarea.source-field');t.value=value;t.dispatchEvent(new Event('input',{bubbles:true}))}",SOURCE+'\n')
                        self.assertEqual(content.inner_text().strip(),(SOURCE+'\n').strip())
                        # Hold a real formatter response while switching the selected node.
                        held=[];page.route('**/api/language/format-source',lambda route:held.append(route))
                        editor.get_by_role('button',name='格式化',exact=True).click();page.wait_for_timeout(80);self.assertEqual(len(held),1)
                        page.locator('[data-node="next"]').click();page.wait_for_function("()=>document.querySelector('textarea.source-field')?.value==='input'")
                        try:held[0].fulfill(status=200,content_type='application/json',body=json.dumps({'source':'999\n'}))
                        except Error:pass
                        page.unroute('**/api/language/format-source');ready();self.assertEqual(text(),'input')
                        # An old node's delayed analysis cannot repaint its replacement.
                        analyses=[]
                        def hold_analysis(route):
                            if route.request.post_data_json.get('source')=='let oldOnly=1;oldOnly':analyses.append(route)
                            else:route.continue_()
                        page.route('**/api/language/analyze',hold_analysis);replace('let oldOnly=1;oldOnly');page.wait_for_timeout(350);self.assertTrue(analyses)
                        page.locator('[data-node="compute"]').click()
                        for pending in analyses:
                            try:pending.fulfill(status=200,content_type='application/json',body=json.dumps({'complete':True,'tokens':[],'diagnostics':[{'message':'STALE ANALYSIS','from':0,'to':1}]}))
                            except Error:pass
                        page.unroute('**/api/language/analyze');ready();self.assertNotIn('STALE ANALYSIS',editor.inner_text())
                        page.locator('[data-node="next"]').click();replace('input');page.locator('[data-node="compute"]').click()
                        # Invalid format-on-save rejects the save without clearing input.
                        replace('let [broken =')
                        with page.expect_response('**/api/language/format-source') as rejected:workbench_button(page,'saveButton')
                        self.assertFalse(rejected.value.ok);page.wait_for_function('()=>!saveTask');self.assertEqual(text(),'let [broken =');self.assertEqual(app.get_project('embedded')['version'],1)
                        replace(SOURCE)
                        # View state disables the editor as well as the native form field.
                        workbench_view(page,'source');page.wait_for_function("()=>document.querySelector('.embedded-editor .cm-content')?.getAttribute('contenteditable')==='false'")
                        workbench_view(page,'graph');page.locator('[data-node="compute"]').click();replace(SOURCE+'\n// 保存函数\n')
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!controlDraft&&!saveTask')
                        saved=app.get_project('embedded');saved_source=next(n['config']['source'] for n in saved['graph']['nodes'] if n['id']=='compute')
                        self.assertIn('let [left, right]',saved_source);self.assertEqual(parse_program(saved_source)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        page.reload();workbench_view(page,'graph');page.locator('[data-node="compute"]').click();ready();self.assertEqual(text(),saved_source)
                        workbench_run_options(page);page.locator('#autoOptimize').uncheck();page.locator('#jsonInput').check();page.locator('#taskInput').fill('null');page.locator('#runButton').click();page.wait_for_function("()=>!busy&&!launching&&document.querySelector('#resultPanel').textContent.trim()==='42'");shot('workflow-saved-run')
                        # Mouse-triggered inspector flushing must preserve the disabled preference.
                        editor.get_by_label('保存时格式化',exact=True).uncheck();raw='let raw=42;raw';replace(raw);workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!controlDraft&&!saveTask')
                        self.assertEqual(next(n['config']['source'] for n in app.get_project('embedded')['graph']['nodes'] if n['id']=='compute'),raw)
                        page.reload();workbench_view(page,'graph');page.locator('[data-node="compute"]').click();ready();self.assertFalse(editor.get_by_label('保存时格式化',exact=True).is_checked());self.assertTrue(page.locator('#formatOnSave').is_checked());self.assertEqual(text(),raw)
                        editor.get_by_label('保存时格式化',exact=True).check()
                        # A remote commit must preserve uncommitted embedded source.
                        replace('let draft=43;draft');remote=app.get_project('embedded');remote['name']='remote edit';app.save_project(remote,from_view='graph')
                        page.wait_for_function('()=>remoteProject&&remoteProject.version>project.version');self.assertEqual(text(),'let draft=43;draft');shot('workflow-conflict')
                        # Without CodeMirror integration, ordinary textarea editing survives.
                        fallback=context.new_page();fallback.on('pageerror',lambda error:errors.append(str(error)));fallback.route('**/editor.js',lambda route:route.abort());fallback.goto(origin+'/?project=embedded');workbench_view(fallback,'graph');fallback.locator('[data-node="compute"]').click();self.assertTrue(fallback.locator('textarea.source-field').is_visible());fallback.locator('textarea.source-field').fill('42');workbench_button(fallback,'saveButton');fallback.wait_for_function('()=>!dirty&&!controlDraft&&!saveTask');fallback.close()
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            report={'status':'passed','sourceRoot':str(ROOT),'version':__version__,'origin':origin,'checks':['actual-completion','module-signature','semantic-color','format-semanticHash','unicode-selection','separate-undo','textarea-restore','stale-node-format','stale-node-analysis','invalid-format-preserves-draft','disabled-view','automatic-save-format','unchecked-mouse-save-preference','preference-reload-independent','save-reload-run42','remote-draft-preserved','textarea-fallback'],'savedVersion':saved['version'],'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('web/editor.js','web/editor.css','web/app.js','web/index.html','web/sync.js','chaos/server.py','tests/test_embedded_editor_ui.py')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
