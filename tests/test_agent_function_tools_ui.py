"""Real browser authoring of an Agent with a language closure tool."""
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
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, __version__
from chaos.program import parse_program
from chaos.server import make_server


ROOT=Path(__file__).resolve().parent.parent
SOURCE='''var seen=[];
fn make(offset:Int){fn(value:Int)->Int{seen=append(seen,value);value+offset}}
agent worker{provider:"app.function_tools",tools:[]};
let tool=agent_tool("add",make(2),description="Add captured offset",input_type="Int",output_type="Int");
let bound=agent_bind(worker,[tool]);
let cold=bound(input=40);
let before=seen;
let answer=await cold;
let contract=agent_tool_info(tool);
let binding=agent_binding_info(bound);
[answer,before,seen,contract.inputType,binding["agent"].name,[type_of(tool),type_of(bound),type_of(cold)]]'''
EXPECTED=[42,[],[40],'Int','worker',['AgentTool','Function','Task']]


class AgentFunctionToolsUI(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_actual_browser_bound_closure_hints_format_highlight_save_and_run(self):
        from playwright.sync_api import sync_playwright
        output=os.environ.get('CHAOS_AGENT_FUNCTION_TOOLS_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        calls=[];registry=Registry()
        def provider(request,context):
            calls.append({'input':request.input,'tools':request.tools,'history':request.history})
            return Final(request.history[-1]['output']) if request.history else ToolCall('add',request.input)
        registry.register_provider(ProviderSpec('app.function_tools',provider))
        with tempfile.TemporaryDirectory(prefix='chaos-function-tools-browser-') as folder, Framework(Path(folder)/'runtime.sqlite',registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('function-tools',source=SOURCE,capabilities=[])
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
                        page.goto(origin+'/?project=function-tools');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        content=page.locator('.cm-content[contenteditable="true"]')
                        def shot(name):
                            if output:page.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        def replace(text,position=None):
                            content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(text)
                            if position is not None:page.evaluate('(position)=>ChaosIDE.editor().dispatch({selection:{anchor:position}})',position)
                        draft=SOURCE.replace('contract.inputType','contract.inp')
                        replace(draft,draft.index('contract.inp')+len('contract.inp'))
                        page.keyboard.press('Control+Space')
                        completion=page.locator('.cm-completionLabel').filter(has_text='inputType').first
                        completion.wait_for();shot('tool-contract-completion');completion.click()
                        self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),SOURCE)
                        replace(SOURCE,SOURCE.index('bound(input=')+len('bound(input='))
                        workbench_button(page,'ideSignatureButton')
                        page.wait_for_function("()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('input')")
                        self.assertIn('-> Task',page.locator('#ideSignatureHelp').inner_text());shot('bound-agent-signature')
                        self.assertEqual(calls,[])
                        workbench_button(page,'formatSource');page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>20')
                        formatted=page.evaluate('ChaosIDE.editor().state.doc.toString()')
                        self.assertIn('\n  ',formatted)
                        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask')
                        saved=app.get_project('function-tools');self.assertEqual(saved['source'],formatted)
                        self.assertEqual(calls,[])
                        page.locator('#runButton').click()
                        page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=EXPECTED)
                        page.wait_for_function("()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新')")
                        function=page.locator('.ide-token-function').filter(has_text='agent_bind').first
                        self.assertTrue(function.is_visible());self.assertEqual(function.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        self.assertTrue(page.locator('.ide-token-keyword').filter(has_text='await').first.is_visible())
                        page.set_viewport_size({'width':1500,'height':1600})
                        page.locator('#expandConsole').click()
                        page.locator('.cm-scroller').evaluate('(element)=>{element.scrollTop=element.scrollHeight}')
                        shot('formatted-source-result')
                        workbench_view(page,'structure')
                        page.wait_for_function('()=>programDraft&&programDraft.root.statements.some(n=>n.kind==="agent")')
                        self.assertEqual(page.evaluate('programDraft.semanticHash'),parse_program(saved['source'])['semanticHash'])
                        self.assertEqual(len(calls),2);self.assertEqual(calls[0]['input'],40)
                        self.assertEqual(calls[0]['history'],[]);self.assertEqual(calls[1]['history'][-1]['output'],42)
                        self.assertEqual(calls[0]['tools']['add']['inputType'],'Int')
                        self.assertEqual(calls[0]['tools']['add']['outputType'],'Int')
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            evidence={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'savedVersion':saved['version'],'runVersion':page.evaluate('runVersion'),'result':EXPECTED,'contractCompletion':True,'boundTaskSignature':True,'readableFormatter':True,'sourceIRIdentity':True,'semanticHighlight':True,'capturedClosure':True,'coldBeforeAwait':True,'providerCalls':calls,'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('chaos/ide_language.py','chaos/native_specs.py','tests/test_agent_function_tools_ide.py','tests/test_agent_function_tools_ui.py')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
