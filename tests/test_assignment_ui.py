"""Actual structural edits, semantic reading and live source agree on patterns."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import importlib.util,json,os,shutil,subprocess,tempfile,threading,unittest
from pathlib import Path
from chaos import Framework,Settings
from chaos.server import make_server
from chaos.language import Evaluator
from chaos.program import SHAPES,format_program,parse_program,templates
from tests.test_assignment_surfaces import SOURCE

ROOT=Path(__file__).resolve().parent.parent

class AssignmentUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_actual_structural_controls_restrict_roots_and_preserve_patterns(self):
        fixture={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/assignment_controls.cjs')],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout)
        self.assertEqual(report['status'],'passed');self.assertEqual(Evaluator().run(format_program(report['program'])),43)

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright unavailable')
    def test_real_browser_pattern_edit_run_remote_sync_and_code_hints(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='chaos-pattern-state-ui-')as folder,Framework(Path(folder)/'db',settings=Settings(':memory:'))as app:
            app.create_project('pattern-state-ui',source=SOURCE,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[];checks=[]
            output=os.environ.get('CHAOS_PATTERN_STATE_AUDIT')
            if output:Path(output).mkdir(parents=True,exist_ok=True)
            try:
                with sync_playwright()as pw:
                    browser=pw.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1600,'height':1300},permissions=['clipboard-read','clipboard-write'])
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('**/*',route);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        def shot(name,target=page):
                            if output:target.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        def run(expected):
                            page.locator('#runButton').click();page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=expected)
                            self.assertEqual(app.get_run(page.evaluate('currentRun'))['output'],expected);checks.append({'version':page.evaluate('runVersion'),'value':expected})
                        page.goto(origin+'/?project=pattern-state-ui');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        workbench_button(page,'formatSource');formatted=Framework.format_source(SOURCE);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                        page.locator('.ide-token-variable',has_text='left').first.wait_for();self.assertTrue(page.locator('.ide-token-operator',has_text='=').first.is_visible())
                        # Query the actual editor's completion at a reassignment leaf.
                        page.evaluate('()=>{const editor=ChaosIDE.editor(),at=editor.state.doc.toString().indexOf("[left, right]")+3;editor.dispatch({selection:{anchor:at}});editor.focus()}')
                        page.keyboard.press('Control+Space');page.locator('.cm-completionLabel').filter(has_text='left').first.wait_for();shot('assignment-completion');page.keyboard.press('Escape')
                        run(42);shot('assignment-source-run')
                        workbench_view(page,'structure');page.wait_for_function('()=>programDraft?.root.statements.some(n=>n.kind==="assign_pattern")')
                        assignment=page.evaluate('programDraft.root.statements.find(n=>n.kind==="assign_pattern").id');page.locator('[data-syntax="'+assignment+'"]').click()
                        target=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='pattern')).locator('select')
                        self.assertEqual(sorted(target.locator('option').evaluate_all('nodes=>nodes.map(n=>n.value)')),['p_class','p_list','p_record','p_tag','p_tuple'])
                        literal=page.evaluate('programDraft.root.statements.find(n=>n.kind==="assign_pattern").value.items[0].id');page.locator('[data-syntax="'+literal+'"]').click()
                        field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='value'));field.locator('input').fill('21');field.get_by_role('button',name='应用',exact=True).click()
                        workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty');saved=app.get_project('pattern-state-ui');self.assertEqual(saved['program']['semanticHash'],parse_program(saved['source'])['semanticHash']);shot('assignment-structure-edit')
                        workbench_view(page,'source');page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=saved['source']);run(43)
                        # Independent HTTP update must be reflected in the open clean editor.
                        remote=saved['source'].replace('[21, 22]','[30, 12]');self.assertNotEqual(remote,saved['source'])
                        response=page.request.put(origin+'/api/v1/projects/pattern-state-ui',data={**saved,'source':remote,'files':{**saved['files'],saved['entry']:remote},'editFrom':'source'})
                        self.assertTrue(response.ok,response.text());page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source&&!dirty',arg=remote,timeout=15000);run(42);shot('assignment-http-sync')
                        page.reload();page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source');page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=remote)
                        # The shared reader uses the same analysis and preserves exact original copy.
                        reader=context.new_page();reader.on('pageerror',lambda e:errors.append(str(e)));reader.goto(origin+'/code.css');reader.set_content('<!doctype html><main id="host"></main>');reader.add_style_tag(url=origin+'/code.css');reader.add_script_tag(url=origin+'/code.js')
                        reader.evaluate('(source)=>{window.reader=ChaosCode.render(source);document.querySelector("#host").append(reader);return reader.ready}',SOURCE)
                        self.assertTrue(reader.locator('code .chaos-token-variable',has_text='left').first.is_visible())
                        reader.locator('code [title]',has_text='left').first.click();self.assertIn('Int',reader.locator('.chaos-code-hint-text').text_content())
                        reader.get_by_role('button',name='格式化阅读',exact=True).click();reader.wait_for_function('()=>reader.dataset.codeView==="formatted"&&reader.dataset.highlight==="ready"');self.assertEqual(reader.locator('code').text_content(),formatted)
                        reader.locator('.copy-code').click();self.assertEqual(reader.evaluate('navigator.clipboard.readText()'),SOURCE);shot('assignment-reader-hints',reader)
                        reader.set_viewport_size({'width':390,'height':844});shot('assignment-reader-mobile',reader)
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:(Path(output)/'browser-result.json').write_text(json.dumps({'status':'passed','checks':checks,'root':str(ROOT),'errors':errors,'external':external,'completion':True,'typedHint':True,'readonlyFormat':True,'originalCopy':True,'remoteSync':True,'reloadPersistence':True},ensure_ascii=False,indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()
