"""Actual Team form to language binding authoring and role execution in a browser."""
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
from chaos.source_format import format_source
from tests.test_team_functions_ide import SOURCE,EXPECTED

ROOT=Path(__file__).resolve().parent.parent
FILES=('chaos/native_specs.py','chaos/ide_language.py','chaos/ide_agent.py','web/language.js','tests/test_team_functions_ide.py','tests/test_team_functions_ui.py')

class TeamFunctionsUI(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright unavailable')
    def test_form_source_roles_cold_tasks_and_real_trace(self):
        from playwright.sync_api import sync_playwright
        output=os.environ.get('CHAOS_TEAM_FUNCTIONS_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='chaos-team-functions-browser-')as folder,Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:'))as app:
            app.create_project('team-functions',source='null',capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
            try:
                with sync_playwright()as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1350})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('**/*',route);page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=team-functions');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        workbench_button(page,'teamAuthorButton');dialog=page.get_by_role('dialog',name='团队定义编辑器');dialog.wait_for()
                        dialog.get_by_label('团队名称',exact=True).fill('Function crew');dialog.get_by_label('导出绑定名',exact=True).fill('crew')
                        dialog.get_by_role('button',name='添加成员',exact=True).click();dialog.get_by_label('成员 2 标识',exact=True).fill('fallback');dialog.get_by_label('成员 2 标识',exact=True).press('Tab')
                        dialog.get_by_label('成员 2 父成员',exact=True).select_option('worker');dialog.get_by_label('成员 2 Agent 名称',exact=True).fill('fallback')
                        self.assertIn('team_bind',dialog.locator('.syntax-note').first.inner_text());self.assertIn('默认 Agent',dialog.locator('.team-author-relations').inner_text())
                        dialog.get_by_role('button',name='验证并生成源码',exact=True).click();page.wait_for_function('()=>!document.querySelector("#teamAuthorImport").disabled')
                        generated=page.evaluate('teamAuthor.preview.source');definition=page.evaluate('teamAuthor.preview.definition');definition_hash=page.evaluate('teamAuthor.preview.definitionHash')
                        self.assertEqual(definition['entry'],'worker');self.assertEqual([r['id']for r in definition['roles']],['worker','fallback']);self.assertNotIn('bindings',definition)
                        self.assertIn('team_from',generated);self.assertEqual(app.get_project('team-functions')['files'],{'main.chaos':'null'})
                        if output:page.screenshot(path=str(Path(output)/'team-default-definition-form.png'),full_page=True)
                        dialog.locator('#teamAuthorImport').click();page.wait_for_function('()=>activeFile==="teams/crew.chaos"')
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask')
                        page.locator('#ideFileTree [data-path="main.chaos"]').click();content=page.locator('.cm-content[contenteditable="true"]')
                        draft=SOURCE.replace('roles.worker(10','roles.worke(10');content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(draft)
                        page.evaluate('(p)=>ChaosIDE.editor().dispatch({selection:{anchor:p}})',draft.index('roles.worke(10')+len('roles.worke'))
                        page.keyboard.press('Control+Space');completion=page.locator('.cm-completionLabel').filter(has_text='worker').first;completion.wait_for();completion.click()
                        self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),SOURCE)
                        workbench_button(page,'formatSource');formatted=format_source(SOURCE);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        page.evaluate('(p)=>ChaosIDE.editor().dispatch({selection:{anchor:p},scrollIntoView:true})',formatted.index('scale = 2')+len('scale ='))
                        workbench_button(page,'ideSignatureButton');page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("scale")')
                        self.assertIn('-> Task',page.locator('#ideSignatureHelp').inner_text());self.assertIn('Int',page.locator('#ideSignatureHelp').inner_text())
                        function=page.locator('.ide-token-function').filter(has_text='team_bind').first;function.wait_for();self.assertEqual(function.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        self.assertEqual(app.runs('team-functions')['items'],[])
                        if output:page.screenshot(path=str(Path(output)/'team-live-role-signature-format.png'),full_page=True)
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');saved=app.get_project('team-functions')
                        workbench_view(page,'structure');page.wait_for_function('()=>programDraft&&programDraft.semanticHash')
                        self.assertEqual(page.evaluate('programDraft.semanticHash'),parse_program(formatted)['semanticHash']);workbench_view(page,'source')
                        page.locator('#runButton').click();page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=EXPECTED)
                        run_id=page.evaluate('currentRun');run=app.get_run(run_id);self.assertEqual(run['status'],'completed');self.assertEqual(run['version'],saved['version'])
                        events=app.events('team-functions',run_id=run_id,limit=300)['items'];roles=[e for e in events if e['type'].startswith('team.role.')]
                        self.assertEqual([e['type']for e in roles],['team.role.started','team.role.completed']*2)
                        for event in roles:
                            data=event['payload'];self.assertEqual(data['roleId'],'worker');self.assertEqual(data['definitionHash'],definition_hash);self.assertEqual(data['implementationKind'],'Function')
                        self.assertEqual(len({e['payload']['invocationId']for e in roles}),2)
                        self.assertFalse(any(e['type'].startswith('agent.')for e in events));self.assertIn('team.role.completed',page.locator('#eventsPanel').inner_text())
                        page.locator('#expandConsole').click();page.set_viewport_size({'width':1500,'height':1550});page.wait_for_function('()=>document.querySelector("#console").getBoundingClientRect().height>=innerHeight*.349')
                        if output:page.screenshot(path=str(Path(output)/'team-role-result-provenance.png'),full_page=True)
                        client=Client(origin);files=saved['files'];position=formatted.index('scale = 2')+len('scale =')
                        self.assertEqual(client.analyze(formatted,files=files,position=position),Framework.analyze(formatted,files=files,position=position));self.assertEqual(client.get_project('team-functions')['files'],app.get_project('team-functions')['files'])
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            report={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'savedVersion':saved['version'],'result':EXPECTED,'definitionHash':definition_hash,'definition':definition,'runId':run_id,'roleEvents':roles,'runVersion':run['version'],'executedSourceHashes':{name:hashlib.sha256(text.encode()).hexdigest()for name,text in saved['files'].items()},'semanticHash':parse_program(formatted)['semanticHash'],'formGeneratedOrdinaryModule':True,'defaultAgentDefinitionOnly':True,'roleCompletion':True,'actualKeywordOnlySignature':True,'semanticHighlight':True,'formatterSemanticIdentity':True,'sourceIRIdentity':True,'coldBeforeAwait':True,'capturedClosure':True,'multiArgumentForwarding':True,'actualRoleTrace':True,'noProviderInvoked':True,'sdkHttpParity':True,'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in FILES}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()
