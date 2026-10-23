"""Actual browser fixture editing, failed adoption and verified version commit."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from theme_assertions import theme_color
from chaos import Framework, ProviderSpec, Registry, Settings, __version__
from chaos.program import parse_program
from chaos.server import make_server
from tests.test_agent_fixtures_ide import SOURCE, FIXTURES, EXPECTED, definition


ROOT=Path(__file__).resolve().parent.parent


class AgentFixturesUI(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_browser_edits_highlighted_fixtures_and_adopts_only_after_real_checks(self):
        from playwright.sync_api import expect, sync_playwright
        output=os.environ.get('CHAOS_AGENT_FIXTURES_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        calls=[];registry=Registry()
        def forbidden(request,context):calls.append(request);raise AssertionError('real provider was invoked')
        registry.register_provider(ProviderSpec('app.never',forbidden))
        with tempfile.TemporaryDirectory(prefix='chaos-fixture-browser-')as folder,Framework(Path(folder)/'runtime.sqlite',registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('agent-fixtures',source='0',capabilities=['program.adopt'])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[];requests=[]
            try:
                with sync_playwright()as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1250})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('http**://**',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        def submitted(request):
                            if request.url.endswith('/program-adopt'):requests.append(request.post_data_json)
                        page.on('request',submitted)
                        page.goto(origin+'/?project=agent-fixtures');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        content=page.locator('.cm-content[contenteditable="true"]');content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(SOURCE)
                        workbench_button(page,'formatSource');page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>12')
                        formatted=page.evaluate('ChaosIDE.editor().state.doc.toString()')
                        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        self.assertEqual(app.get_project('agent-fixtures')['version'],1)
                        workbench_button(page,'programCandidateButton');dialog=page.get_by_role('dialog',name='程序候选预览')
                        dialog.wait_for();candidate=definition(formatted)
                        dialog.locator('[data-program-history] summary').click()
                        definition_field=dialog.get_by_role('textbox',name='候选程序定义 JSON',exact=True)
                        cases_field=dialog.get_by_role('textbox',name='测试用例 JSON（可选 fixtures）',exact=True)
                        definition_field.fill(json.dumps(candidate,ensure_ascii=False,indent=2))
                        cases_format=dialog.get_by_role('button',name='格式化 测试用例 JSON（可选 fixtures）',exact=True)
                        malformed='[{"fixtures": '
                        cases_field.fill(malformed);cases_format.click()
                        self.assertEqual(cases_field.input_value(),malformed)
                        self.assertIn('原草稿已保留',cases_field.locator('..').get_by_role('status').inner_text())
                        exact='[{"input":9007199254740993,"expect":1.0000000000000001}]'
                        cases_field.fill(exact);cases_format.click()
                        self.assertIn('9007199254740993',cases_field.input_value())
                        self.assertIn('1.0000000000000001',cases_field.input_value())
                        cases=[{'input':40,'expect':EXPECTED,'fixtures':copy.deepcopy(FIXTURES)}]
                        cases[0]['fixtures']['agents'][0]['actions'][0]['expect']=43
                        wrong_json=json.dumps(cases,ensure_ascii=False,indent=2);cases_field.fill(json.dumps(cases));cases_format.click()
                        self.assertEqual(cases_field.input_value(),wrong_json)
                        preview=cases_field.locator('..').locator('[data-program-json-preview]')
                        preview.locator('summary').click();code=preview.locator('code')
                        expect(code).to_have_text(wrong_json)
                        self.assertGreater(preview.locator('.chaos-token-property').count(),0)
                        self.assertGreater(preview.locator('.chaos-token-number').count(),0)
                        self.assertEqual(preview.locator('.chaos-code-line').first.evaluate('el=>getComputedStyle(el).whiteSpace'),'pre')
                        self.assertEqual(preview.locator('.chaos-token-property').first.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'property'))
                        # A preview remains text: it must not interpret JSON strings as markup.
                        text='[{"input":"<script>window.fixtureInjected=true</script>","expect":null}]'
                        cases_field.fill(text);self.assertEqual(code.text_content(),text)
                        self.assertEqual(preview.locator('script').count(),0)
                        self.assertIsNone(page.evaluate('window.fixtureInjected'))
                        cases_field.fill(wrong_json)
                        if output:
                            preview.locator('.chaos-token-property').filter(has_text='"fixtures"').scroll_into_view_if_needed()
                            page.screenshot(path=str(Path(output)/'fixture-json-preview.png'),full_page=True)
                        dialog.get_by_role('button',name='校验并预览差异',exact=True).click()
                        page.wait_for_function("()=>document.querySelector('[data-program-status]')?.textContent.includes('编译校验通过')")
                        changed=dialog.locator('[data-program-diff] .ide-change');changed.locator('summary').click()
                        highlighted=changed.locator('.added .chaos-token-function').filter(has_text='agent_bind').first
                        highlighted.wait_for();self.assertEqual(highlighted.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        self.assertEqual(changed.locator('.added code').text_content(),formatted)
                        dialog.locator('[data-program-adopt]').click()
                        page.wait_for_function("()=>document.querySelector('[data-program-status]')?.textContent.includes('采用失败')")
                        self.assertEqual(app.get_project('agent-fixtures')['version'],1)
                        self.assertEqual(cases_field.input_value(),wrong_json)
                        self.assertEqual(calls,[])
                        if output:
                            preview.locator('summary').click()
                            dialog.locator('[data-program-status]').scroll_into_view_if_needed()
                            page.screenshot(path=str(Path(output)/'failed-fixture-keeps-v1.png'),full_page=True)
                            preview.locator('summary').click();expect(code).to_have_text(wrong_json)
                        cases[0]['fixtures']=copy.deepcopy(FIXTURES);correct_json=json.dumps(cases,ensure_ascii=False,indent=2)
                        cases_field.fill(correct_json);self.assertEqual(code.text_content(),correct_json)
                        self.assertTrue(dialog.locator('[data-program-adopt]').is_disabled())
                        dialog.get_by_role('button',name='校验并预览差异',exact=True).click()
                        page.wait_for_function("()=>document.querySelector('[data-program-status]')?.textContent.includes('编译校验通过')")
                        dialog.locator('[data-program-adopt]').click()
                        page.wait_for_function("()=>document.querySelector('[data-program-status]')?.textContent.includes('已采用为 v2')")
                        saved=app.get_project('agent-fixtures');self.assertEqual(saved['version'],2);self.assertEqual(saved['source'],formatted)
                        self.assertEqual(parse_program(saved['source'])['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        self.assertEqual(cases_field.input_value(),correct_json)
                        self.assertEqual(len(requests),2);self.assertEqual(requests[0]['cases'][0]['fixtures']['agents'][0]['actions'][0]['expect'],43)
                        self.assertEqual(requests[1]['cases'],cases)
                        self.assertEqual(calls,[]);self.assertEqual(errors,[]);self.assertEqual(external,[])
                        events=app.events('agent-fixtures',limit=300)['items']
                        self.assertEqual(sum(event['type']=='program.adopted'for event in events),1)
                        if output:
                            dialog.locator('.program-candidate-receipts').scroll_into_view_if_needed()
                            page.screenshot(path=str(Path(output)/'verified-fixture-adopts-v2.png'),full_page=True)
                            evidence={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'failedVersion':1,'adoptedVersion':saved['version'],'expected':EXPECTED,'jsonPreviewPreservesText':True,'jsonHighlight':True,'jsonFormatPreservesExactNumbers':True,'malformedJSONPreservesDraft':True,'markupIsText':True,'editableTextarea':True,'fixturesSubmittedUnchanged':True,'failedFixturePreservesDraft':True,'sourceDiffHighlight':True,'formatSemanticIdentity':True,'sourceIRIdentity':True,'realProviderCalls':len(calls),'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ('chaos/ide_agent.py','chaos/openapi.py','web/ide.js','tests/test_agent_fixtures_ide.py','tests/test_agent_fixtures_ui.py')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
