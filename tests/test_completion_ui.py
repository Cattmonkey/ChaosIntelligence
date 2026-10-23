"""Normal-completion branches survive structural and headless edits."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import importlib.util,json,os,shutil,subprocess,tempfile,threading,unittest
from pathlib import Path
from chaos import Framework,Settings
from chaos.server import make_server
from chaos.language import Evaluator
from chaos.program import SHAPES,format_program,parse_program,templates
from tests.test_completion_surfaces import SOURCE

ROOT=Path(__file__).resolve().parent.parent

class CompletionUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_actual_completion_controls(self):
        fixture={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/completion_controls.cjs')],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout)
        self.assertEqual(report['status'],'passed');self.assertEqual(Evaluator().run(format_program(report['program'])),42)

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright unavailable')
    def test_real_browser_completion_help_edit_run_live_sync_and_reader(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='chaos-completion-ui-')as folder,Framework(Path(folder)/'db',settings=Settings(':memory:'))as app:
            app.create_project('completion-ui',source=SOURCE,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[];checks=[]
            output=os.environ.get('CHAOS_COMPLETION_AUDIT')
            if output:Path(output).mkdir(parents=True,exist_ok=True)
            try:
                with sync_playwright()as pw:
                    browser=pw.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1200},permissions=['clipboard-read','clipboard-write'])
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('**/*',route);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        def shot(name,target=page):
                            if output:target.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        def run(expected):
                            page.locator('#runButton').click();page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=expected)
                            self.assertEqual(app.get_run(page.evaluate('currentRun'))['output'],expected);checks.append({'version':page.evaluate('runVersion'),'value':expected})
                        page.goto(origin+'/?project=completion-ui');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        workbench_button(page,'formatSource');formatted=Framework.format_source(SOURCE);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                        self.assertTrue(page.locator('.ide-token-type',has_text='Int').count())
                        page.evaluate('()=>{const e=ChaosIDE.editor(),text=e.state.doc.toString(),at=text.lastIndexOf("add(")+4;e.dispatch({selection:{anchor:at}});e.focus()}')
                        workbench_button(page,'ideSignatureButton');page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("value")')
                        self.assertIn('value: Int',page.locator('#ideSignatureHelp').text_content());shot('completion-signature-help');run(42)
                        workbench_view(page,'structure');page.wait_for_function('()=>programDraft?.root.statements[1].value.kind==="loop_else"')
                        pid=page.evaluate('programDraft.root.statements[1].value.id');page.locator('[data-syntax="'+pid+'"]').click()
                        self.assertIn('循环正常完成分支',page.locator('#syntaxTitle').text_content())
                        loop_field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='loop'))
                        self.assertEqual(set(loop_field.locator('option').evaluate_all('(nodes)=>nodes.map(n=>n.value)')),{'while','for','async_for','for_pattern','async_for_pattern'})
                        else_field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='otherwise'))
                        self.assertEqual(else_field.locator('option').evaluate_all('(nodes)=>nodes.map(n=>n.value)'),['block'])
                        tid=page.evaluate('programDraft.root.statements[1].value.otherwise.statements[0].id');page.locator('[data-syntax="'+tid+'"]').click()
                        self.assertIn('异常保护成功分支',page.locator('#syntaxTitle').text_content())
                        body_field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='body'))
                        self.assertEqual(set(body_field.locator('option').evaluate_all('(nodes)=>nodes.map(n=>n.value)')),{'try','try_cases','try_group'})
                        lid=page.evaluate('programDraft.root.statements[1].value.otherwise.statements[0].otherwise.statements[0].arguments[0].id');page.locator('[data-syntax="'+lid+'"]').click()
                        field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='value'));field.locator('input').fill('42');field.get_by_role('button',name='应用',exact=True).click()
                        workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty');saved=app.get_project('completion-ui');shot('completion-structure-edit')
                        workbench_view(page,'source');page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=saved['source']);run(43)
                        remote=saved['source'].replace('(add)(42)', '(add)(41)');self.assertNotEqual(remote,saved['source'])
                        response=page.request.put(origin+'/api/v1/projects/completion-ui',data={**saved,'source':remote,'files':{**saved['files'],saved['entry']:remote},'editFrom':'source'})
                        self.assertTrue(response.ok,response.text());page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source&&!dirty',arg=remote,timeout=15000);run(42);shot('completion-http-sync')
                        self.assertEqual(app.get_project('completion-ui')['program']['semanticHash'],parse_program(remote)['semanticHash'])
                        page.reload();page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source');page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=remote)
                        reader=context.new_page();reader.on('pageerror',lambda e:errors.append(str(e)));reader.goto(origin+'/code.css');reader.set_content('<!doctype html><main id="host"></main>');reader.add_style_tag(url=origin+'/code.css');reader.add_script_tag(url=origin+'/code.js')
                        reader.evaluate('(source)=>{window.reader=ChaosCode.render(source);document.querySelector("#host").append(reader);return reader.ready}',SOURCE)
                        self.assertTrue(reader.locator('code .chaos-token-type',has_text='Int').count())
                        reader.locator('code [title]',has_text='add').first.click();self.assertIn('value: Int',reader.locator('.chaos-code-hint-text').text_content())
                        reader.get_by_role('button',name='格式化阅读',exact=True).click();reader.wait_for_function('()=>reader.dataset.codeView==="formatted"&&reader.dataset.highlight==="ready"');self.assertEqual(reader.locator('code').text_content(),formatted)
                        reader.locator('.copy-code').click();self.assertEqual(reader.evaluate('navigator.clipboard.readText()'),SOURCE);shot('completion-reader-hints',reader)
                        reader.set_viewport_size({'width':390,'height':844});reader.get_by_role('button',name='切换代码自动折行',exact=True).click();self.assertTrue(reader.evaluate('document.documentElement.scrollWidth<=innerWidth'));self.assertTrue(reader.locator('pre').evaluate('el=>el.scrollWidth<=el.clientWidth'));shot('completion-reader-mobile',reader)
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:(Path(output)/'browser-result.json').write_text(json.dumps({'status':'passed','checks':checks,'root':str(ROOT),'errors':errors,'external':external,'completionSignatureHelp':True,'loopStructure':True,'tryStructure':True,'restrictedRoots':True,'typedHint':True,'readonlyFormat':True,'originalCopy':True,'remoteSync':True,'reloadPersistence':True,'mobileNoOverflow':True},ensure_ascii=False,indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()
