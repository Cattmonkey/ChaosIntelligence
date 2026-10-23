"""Real browser source authoring and durable bound-Session evidence."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from theme_assertions import theme_color
from chaos import Client, Framework, __version__
from chaos.program import parse_program
from chaos.server import make_server
from chaos.source_format import format_source
from tests.test_session_integration import NativeFixture
from tests.test_session_tools_ide import SOURCE, EXPECTED

ROOT=Path(__file__).resolve().parent.parent
FILES=('chaos/native_specs.py','chaos/ide_language.py','chaos/openapi.py','web/sessions.js','chaos/ide_agent.py','tests/test_session_ui.py','tests/test_session_tools_ide.py','tests/test_session_tools_ui.py')
AGENT={'kind':'agent','name':'worker','config':{'provider':'model','tools':[]}}

def response(body):
    messages=body['messages'];last=max(i for i,m in enumerate(messages)if m['role']=='user')
    tools=[m for m in messages[last:]if m['role']=='tool']
    if body.get('tools') and not tools:
        return {'role':'assistant','content':None,'tool_calls':[{'id':'add_'+str(last),'type':'function','function':{'name':body['tools'][0]['function']['name'],'arguments':'{"input":40}'}}]}
    return {'role':'assistant','content':tools[-1]['content'] if tools else 'raw-'+str(sum(m['role']=='user'for m in messages))}


class SessionToolsUI(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_browser_bound_closure_persistent_receipt_and_explicit_raw_send(self):
        from playwright.sync_api import sync_playwright
        output=os.environ.get('CHAOS_SESSION_TOOLS_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        fixture=NativeFixture(response);self.addCleanup(fixture.close)
        with tempfile.TemporaryDirectory(prefix='chaos-session-tools-browser-')as folder,Framework(Path(folder)/'runtime.sqlite',settings=fixture.settings)as app:
            app.create_project('session-tools',source=SOURCE,capabilities=['session.manage'])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
            try:
                with sync_playwright()as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1450})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('**/*',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=session-tools');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        content=page.locator('.cm-content[contenteditable="true"]');draft=SOURCE.replace('binding.session.key','binding.session.ke')
                        content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(draft)
                        page.evaluate('(p)=>ChaosIDE.editor().dispatch({selection:{anchor:p}})',draft.index('binding.session.ke')+len('binding.session.ke'))
                        page.keyboard.press('Control+Space');completion=page.locator('.cm-completionLabel').filter(has_text='key').first;completion.wait_for();completion.click()
                        self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),SOURCE)
                        workbench_button(page,'formatSource');formatted=format_source(SOURCE)
                        page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        page.evaluate('(p)=>ChaosIDE.editor().dispatch({selection:{anchor:p},scrollIntoView:true})',formatted.index('message_id =')+len('message_id ='))
                        workbench_button(page,'ideSignatureButton');page.wait_for_function("()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('message_id')")
                        self.assertIn('-> Task',page.locator('#ideSignatureHelp').inner_text())
                        function=page.locator('.ide-token-function').filter(has_text='session_bind').first;function.wait_for()
                        self.assertEqual(function.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        self.assertEqual(fixture.requests,[])
                        if output:page.screenshot(path=str(Path(output)/'session-binding-signature-format.png'),full_page=True)
                        workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');saved=app.get_project('session-tools')
                        self.assertEqual(saved['source'],formatted);self.assertEqual(fixture.requests,[])
                        workbench_view(page,'structure');page.wait_for_function('()=>programDraft&&programDraft.root.statements.some(n=>n.kind==="agent")')
                        self.assertEqual(page.evaluate('programDraft.semanticHash'),parse_program(formatted)['semanticHash']);workbench_view(page,'source')
                        page.locator('#runButton').click();page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=EXPECTED)
                        self.assertEqual(len(fixture.requests),2)
                        row=app.agent_session('session-tools','chat');binding=row['lastToolBinding'];self.assertEqual(row['turn'],1)
                        self.assertEqual(binding['tools'][0]['name'],'add');self.assertEqual(binding['sourceVersion'],saved['version'])
                        history=app.agent_session_history('session-tools','chat')['items'];self.assertEqual(len(history),4)
                        self.assertTrue(all(item['toolBinding']==binding for item in history));self.assertEqual(history[2]['message']['content'],42)
                        workbench_button(page,'agentSessionsButton');dialog=page.get_by_role('dialog',name='原生智能体会话',exact=True);dialog.wait_for()
                        page.wait_for_function('(hash)=>ChaosSessions.state.record?.lastToolBinding?.bindingHash===hash',arg=binding['bindingHash'])
                        region=dialog.get_by_role('region',name='最近成功的语言工具轮次',exact=True);region.locator('summary').click()
                        self.assertEqual(json.loads(region.locator('pre').inner_text()),binding)
                        card=dialog.locator('.session-card.selected');self.assertIn('1 轮 · r'+str(row['revision']),card.inner_text())
                        self.assertIn('不携带语言函数工具',dialog.get_by_role('region',name='会话操作',exact=True).inner_text())
                        dialog.locator('.session-message.tool summary').filter(has_text='add ·').click()
                        dialog.locator('.session-history').evaluate('e=>{e.scrollTop=e.scrollHeight}')
                        if output:page.screenshot(path=str(Path(output)/'durable-language-tool-receipt.png'),full_page=True)
                        dialog.get_by_label('消息输入类型').select_option('json');dialog.get_by_role('textbox',name='会话消息',exact=True).fill('40')
                        dialog.get_by_role('textbox',name='幂等消息 ID（可选）',exact=True).fill('one')
                        dialog.get_by_role('button',name='发送并运行',exact=True).click()
                        page.wait_for_function("()=>document.querySelector('.session-dialog>.session-notice')?.textContent.includes('版本冲突')")
                        self.assertEqual(len(fixture.requests),2);self.assertEqual(app.agent_session_history('session-tools','chat')['items'],history)
                        self.assertEqual(dialog.get_by_role('textbox',name='会话消息',exact=True).input_value(),'40')
                        dialog.get_by_role('textbox',name='幂等消息 ID（可选）',exact=True).fill('raw-two');dialog.get_by_role('button',name='发送并运行',exact=True).click()
                        page.wait_for_function('()=>ChaosSessions.state.record?.turn===2&&ChaosSessions.state.record?.status==="idle"')
                        final=app.agent_session('session-tools','chat');self.assertEqual(final['lastToolBinding'],binding)
                        self.assertIn('2 轮 · r'+str(final['revision']),card.inner_text())
                        raw=app.agent_session_history('session-tools','chat')['items'];self.assertTrue(all('toolBinding'not in item for item in raw if item['turn']==2))
                        self.assertEqual(len(fixture.requests),3);self.assertNotIn('tools',fixture.requests[-1])
                        self.assertEqual([m['role']for m in fixture.requests[-1]['messages']],['system','user','assistant','tool','assistant','user'])
                        page.wait_for_function('()=>ChaosSessions.state.history.some(item=>item.turn===2)')
                        region.locator('summary').click();dialog.locator('.session-history').evaluate('e=>{e.scrollTop=e.scrollHeight}');dialog.evaluate('e=>{e.scrollTop=0}')
                        page.set_viewport_size({'width':1500,'height':1650})
                        if output:page.screenshot(path=str(Path(output)/'raw-turn-retains-historical-binding.png'),full_page=True)
                        client=Client(origin);self.assertEqual(client.agent_session('session-tools','chat'),final)
                        command=[sys.executable,'-m','chaos','session','get','session-tools','chat','--server',origin]
                        cli=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=10);self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout),final)
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            report={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'savedVersion':saved['version'],'result':EXPECTED,'binding':binding,'finalRevision':final['revision'],'completedTurns':final['turn'],'contractCompletion':True,'boundTaskSignature':True,'semanticHighlight':True,'formatterSemanticIdentity':True,'sourceIRIdentity':True,'coldBeforeAwait':True,'capturedClosure':True,'replayNoDuplicateTool':True,'rawSameIdConflict':True,'rawTurnDoesNotRestoreBinding':True,'lastBindingHistorical':True,'historyContractMatchesStore':True,'sdkHttpCliParity':True,'selectedCardMatchesDetail':True,'nativeRequests':len(fixture.requests),'externalHTTP':external,'errors':errors,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in FILES}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_older_list_detail_and_selection_cannot_regress_actual_session_records(self):
        from playwright.sync_api import sync_playwright
        fixture=NativeFixture(response);self.addCleanup(fixture.close)
        with tempfile.TemporaryDirectory(prefix='chaos-session-poll-browser-')as folder,Framework(Path(folder)/'runtime.sqlite',settings=fixture.settings)as app:
            app.create_project('sync',source=SOURCE,capabilities=['session.manage']);app.open_agent_session('sync',1,'chat',AGENT);app.open_agent_session('sync',1,'other',AGENT)
            older=app.agent_sessions('sync');app.start('sync',1,None,mode='source').result(timeout=10);current=app.agent_session('sync','chat');other=app.agent_session('sync','other')
            self.assertGreater(current['revision'],older['items'][0]['revision'])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port);path='/api/v1/projects/sync/agent-sessions'
            response_data={'list':older,'chat':current,'other':other,'hold':None};held=[];errors=[]
            try:
                with sync_playwright()as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        page=browser.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        def route(request):
                            url=request.request.url
                            if not url.startswith(origin+'/'):request.abort();return
                            suffix=url[len(origin):]
                            if suffix==path:request.fulfill(json=response_data['list'])
                            elif suffix in (path+'/chat',path+'/other'):
                                key=suffix.rsplit('/',1)[1]
                                if response_data['hold']==key:held.append(request)
                                else:request.fulfill(json=response_data[key])
                            else:request.continue_()
                        page.route('**/*',route);page.goto(origin+'/?project=sync');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source');workbench_button(page,'agentSessionsButton')
                        page.wait_for_function('(r)=>ChaosSessions.state.record?.revision===r',arg=current['revision']);page.evaluate('()=>{clearTimeout(ChaosSessions.state.timer);clearTimeout(ChaosSessions.state.streamTimer);ChaosSessions.state.stream?.close()}')
                        dialog=page.get_by_role('dialog',name='原生智能体会话',exact=True)
                        def consistent(record):
                            shown=json.loads(page.evaluate('()=>stringifyJSON({detail:ChaosSessions.state.record,list:ChaosSessions.state.items.find(item=>item.key===ChaosSessions.state.selected)})'))
                            self.assertEqual(shown['detail'],record);self.assertEqual(shown['list'],record)
                            self.assertIn(str(record['turn'])+' 轮 · r'+str(record['revision']),dialog.locator('.session-card.selected').inner_text())
                            self.assertIn('完成 '+str(record['turn'])+' 轮',dialog.get_by_role('region',name='会话状态',exact=True).inner_text())
                        def pending():
                            for _ in range(500):
                                if held:return held.pop(0)
                                page.wait_for_timeout(10)
                            self.fail('held detail response missing')
                        def refresh():
                            with page.expect_request(lambda request:request.url==origin+path+'/chat'):
                                page.evaluate('()=>{window.pollFinished=false;ChaosSessions.reload().then(()=>window.pollFinished=true)}')
                            return pending()
                        consistent(current);response_data['hold']='chat';request=refresh();consistent(current);request.fulfill(json=current);page.wait_for_function('()=>window.pollFinished')
                        app.start_session_turn('sync',1,'chat',current['revision'],40,message_id='raw-next').result(timeout=10)
                        newest=app.agent_session('sync','chat');response_data['list']=app.agent_sessions('sync');response_data['chat']=current
                        request=refresh();consistent(newest);request.fulfill(json=current);page.wait_for_function('()=>window.pollFinished');consistent(newest)
                        request=refresh();dialog.locator('.session-card').filter(has=page.get_by_text('other',exact=True)).click();page.wait_for_function('()=>ChaosSessions.state.record?.key==="other"');request.fulfill(json=current);page.wait_for_function('()=>window.pollFinished');consistent(other)
                        response_data['hold']=None
                        for item in held:item.fulfill(json=current)
                        held.clear();page.wait_for_timeout(50)
                        self.assertEqual(len(page.evaluate('ChaosSessions.state.items')),2);self.assertEqual(errors,[])
                        output=os.environ.get('CHAOS_SESSION_TOOLS_AUDIT')
                        if output:
                            Path(output).mkdir(parents=True,exist_ok=True)
                            (Path(output)/'revision-sync-result.json').write_text(json.dumps({'status':'passed','actualOlderRevision':older['items'][0]['revision'],'actualNewerRevision':current['revision'],'actualNewestRevision':newest['revision'],'oldListCannotRegress':True,'newListUpdatesBothPanels':True,'oldDetailCannotRegress':True,'supersededSelectionIgnored':True,'unrelatedRecordPreserved':True,'errors':errors,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ('web/sessions.js','tests/test_session_tools_ui.py')}},indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
