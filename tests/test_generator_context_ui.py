"""Actual browser resource-factory hints, formatting, saved IR and execution."""
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
from chaos import Framework, Settings, __version__
from chaos.program import parse_program
from chaos.server import make_server


ROOT=Path(__file__).resolve().parent.parent
SOURCE='''import "std/context" as Context;
var released=false;
@Context.contextmanager
fn* resource(seed:Int,*,extra:Int=2)->String{try{yield {answer:seed+extra}}finally{released=true};"done"}
let answer=with resource(40,extra=2) as value{value.answer};[answer,released]'''


class GeneratorContextUI(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_actual_browser_factory_hints_format_highlight_save_and_run(self):
        from playwright.sync_api import sync_playwright
        output=os.environ.get('CHAOS_CONTEXT_GENERATOR_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='chaos-context-browser-') as folder, Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('generator-context',source=SOURCE,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1000})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('http**://**',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=generator-context');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        content=page.locator('.cm-content[contenteditable="true"]')
                        def shot(name):
                            if output:page.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        def replace(text,position=None):
                            content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(text)
                            if position is not None:
                                page.evaluate('(position)=>ChaosIDE.editor().dispatch({selection:{anchor:position}})',position)
                        # Completion reads the value yielded by the generator,
                        # although its annotated completion value is String.
                        draft=SOURCE.replace('value.answer','value.ans')
                        replace(draft,draft.index('value.ans')+len('value.ans'))
                        page.keyboard.press('Control+Space')
                        completion=page.locator('.cm-completionLabel').filter(has_text='answer').first
                        completion.wait_for();shot('resource-completion');completion.click()
                        self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),SOURCE)
                        replace(SOURCE,SOURCE.index('extra=2)')+len('extra='))
                        workbench_button(page,'ideSignatureButton')
                        page.wait_for_function("()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('extra')")
                        self.assertIn('ContextManager',page.locator('#ideSignatureHelp').inner_text());shot('factory-signature')
                        workbench_button(page,'formatSource');page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>10')
                        formatted=page.evaluate('ChaosIDE.editor().state.doc.toString()')
                        self.assertIn('\n  ',formatted)
                        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask')
                        saved=app.get_project('generator-context');self.assertEqual(saved['source'],formatted)
                        page.locator('#runButton').click()
                        page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify([42,true],null,2)')
                        page.wait_for_function("()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新')")
                        function=page.locator('.ide-token-function').filter(has_text='resource').first
                        self.assertTrue(function.is_visible());self.assertEqual(function.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        self.assertTrue(page.locator('.ide-token-keyword').filter(has_text='yield').first.is_visible())
                        shot('formatted-source-result')
                        workbench_view(page,'structure')
                        page.wait_for_function('()=>programDraft&&programDraft.root.statements.some(n=>n.kind==="decorated")')
                        self.assertEqual(page.evaluate('programDraft.semanticHash'),parse_program(saved['source'])['semanticHash'])
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            evidence={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'savedVersion':saved['version'],'runVersion':page.evaluate('runVersion'),'result':[42,True],'resourceCompletion':True,'factorySignature':True,'readableFormatter':True,'sourceIRIdentity':True,'semanticHighlight':True,'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('chaos/ide_language.py','chaos/native_specs.py','tests/test_generator_context_ide.py','tests/test_generator_context_ui.py')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
