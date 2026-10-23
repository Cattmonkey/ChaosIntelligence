"""Real browser editing and execution of mixed dynamic resource stacks."""
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
var released=[];
fn note(label:String){released=append(released,label)}
@Context.contextmanager
fn* sync_resource(seed:Int)->String{try{yield {answer:seed}}finally{note("sync")};"done"}
@Context.asynccontextmanager
async fn* resource(seed:Int)->String{try{yield {answer:seed}}finally{await sleep(0);note("async")};"done"}
async fn finish(label:String)->Null{await sleep(0);note(label);null}
let answer=async with Context.AsyncExitStack(max_entries=8) as stack{
stack.callback(note,"callback");
let first=stack.enter_context(sync_resource(20));
let value=await stack.enter_async_context(manager=resource(22));
stack.push_async_callback(finish,"last");
first.answer+value.answer
};[answer,released]'''
EXPECTED=[42,['last','async','sync','callback']]


class ResourceStackUI(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_actual_browser_stack_hints_format_highlight_save_and_run(self):
        from playwright.sync_api import sync_playwright
        output=os.environ.get('CHAOS_RESOURCE_STACK_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='chaos-stack-browser-') as folder, Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('resource-stack',source=SOURCE,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1250})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('http**://**',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=resource-stack');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        content=page.locator('.cm-content[contenteditable="true"]')
                        def shot(name):
                            if output:page.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        def replace(text,position=None):
                            content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(text)
                            if position is not None:page.evaluate('(position)=>ChaosIDE.editor().dispatch({selection:{anchor:position}})',position)
                        draft=SOURCE.replace('value.answer','value.ans')
                        replace(draft,draft.index('value.ans')+len('value.ans'))
                        page.keyboard.press('Control+Space')
                        completion=page.locator('.cm-completionLabel').filter(has_text='answer').first
                        completion.wait_for();shot('stack-resource-completion');completion.click()
                        self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),SOURCE)
                        replace(SOURCE,SOURCE.index('manager=resource')+len('manager='))
                        workbench_button(page,'ideSignatureButton')
                        page.wait_for_function("()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('manager')")
                        self.assertIn('-> Task',page.locator('#ideSignatureHelp').inner_text());shot('native-stack-signature')
                        workbench_button(page,'formatSource');page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>25')
                        formatted=page.evaluate('ChaosIDE.editor().state.doc.toString()')
                        self.assertIn('\n  ',formatted)
                        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask')
                        saved=app.get_project('resource-stack');self.assertEqual(saved['source'],formatted)
                        page.locator('#runButton').click()
                        page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=EXPECTED)
                        page.wait_for_function("()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新')")
                        function=page.locator('.ide-token-function').filter(has_text='enter_async_context').first
                        self.assertTrue(function.is_visible());self.assertEqual(function.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        self.assertTrue(page.locator('.ide-token-keyword').filter(has_text='await').first.is_visible())
                        page.locator('.cm-scroller').evaluate('(element)=>{element.scrollTop=element.scrollHeight}')
                        shot('formatted-source-result')
                        workbench_view(page,'structure')
                        page.wait_for_function('()=>programDraft&&programDraft.root.statements.some(n=>n.kind==="decorated")')
                        self.assertEqual(page.evaluate('programDraft.semanticHash'),parse_program(saved['source'])['semanticHash'])
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            evidence={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'savedVersion':saved['version'],'runVersion':page.evaluate('runVersion'),'result':EXPECTED,'resourceCompletion':True,'nativeStackSignature':True,'readableFormatter':True,'sourceIRIdentity':True,'semanticHighlight':True,'mixedLIFORelease':True,'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('chaos/ide_language.py','chaos/native_specs.py','tests/test_resource_stack_ide.py','tests/test_resource_stack_ui.py')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
