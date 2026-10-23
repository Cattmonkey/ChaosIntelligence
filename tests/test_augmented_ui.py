"""Actual no-code augmented edits round-trip to source and execute live."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import importlib.util,json,os,tempfile,threading,unittest
from pathlib import Path
from chaos import Framework,Settings
from chaos.server import make_server
from chaos.program import parse_program
from tests.test_augmented_surfaces import SOURCE

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright unavailable')
class AugmentedUI(unittest.TestCase):
    def test_structure_source_saved_run_and_readonly_hints(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='chaos-augmented-ui-')as folder,Framework(Path(folder)/'db',settings=Settings(':memory:'))as app:
            app.create_project('augmented-ui',source=SOURCE,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
            try:
                with sync_playwright()as pw:
                    browser=pw.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1600,'height':1300},permissions=['clipboard-read','clipboard-write'])
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('**/*',route);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        page.goto(origin+'/?project=augmented-ui');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        workbench_button(page,'formatSource');formatted=Framework.format_source(SOURCE);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                        for op in ['<<=','|=']:
                            token=page.locator('.ide-token-operator',has_text=op).first;token.wait_for();self.assertEqual(token.text_content(),op)
                        workbench_view(page,'structure');page.wait_for_function('()=>programDraft?.root.statements.some(n=>n.kind==="aug_assign")')
                        self.assertEqual(page.evaluate('()=>{const select=chooseKind("unquote",()=>{},"assignmentTarget");return [...select.options].map(o=>o.value)}'),['name','index','subscript','unquote'])
                        ident=page.evaluate('programDraft.root.statements.find(n=>n.kind==="aug_assign").id');page.locator('[data-syntax="'+ident+'"]').click()
                        target=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='target')).locator('select')
                        self.assertEqual(target.locator('option').evaluate_all('nodes=>nodes.map(n=>n.value)'),['name','index','subscript'])
                        page.get_by_label('增强赋值运算符').select_option('**');self.assertTrue(page.evaluate('syntaxDirty'));workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty')
                        saved=app.get_project('augmented-ui');self.assertIn('**=',saved['source']);self.assertEqual(saved['program']['semanticHash'],parse_program(saved['source'])['semanticHash'])
                        workbench_view(page,'source');page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=saved['source']);page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()==="127"')
                        self.assertEqual(app.get_run(page.evaluate('currentRun'))['output'],127)
                        output=os.environ.get('CHAOS_OPERATORS_AUDIT')
                        if output:Path(output).mkdir(parents=True,exist_ok=True);page.screenshot(path=str(Path(output)/'augmented-ide-run.png'),full_page=True)
                        reader=context.new_page();reader.on('pageerror',lambda e:errors.append(str(e)));reader.goto(origin+'/code.css');reader.set_content('<!doctype html><main id="host"></main>');reader.add_style_tag(url=origin+'/code.css');reader.add_script_tag(url=origin+'/code.js')
                        reader.evaluate('(source)=>{window.reader=ChaosCode.render(source);document.querySelector("#host").append(reader);return reader.ready}',SOURCE)
                        for op in ['<<=','|=']:self.assertEqual(reader.locator('code .chaos-token-operator',has_text=op).text_content(),op)
                        reader.locator('code [title]',has_text='count').first.click();self.assertIn('Int',reader.locator('.chaos-code-hint-text').text_content())
                        reader.get_by_role('button',name='格式化阅读',exact=True).click();reader.wait_for_function('()=>reader.dataset.codeView==="formatted"&&reader.dataset.highlight==="ready"');self.assertEqual(reader.locator('code').text_content(),formatted)
                        reader.locator('.copy-code').click();self.assertEqual(reader.evaluate('navigator.clipboard.readText()'),SOURCE)
                        if output:reader.screenshot(path=str(Path(output)/'augmented-readonly-highlight.png'),full_page=True)
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()
