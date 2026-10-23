"""Real browser authoring and native Process evolution receipts."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from theme_assertions import theme_color
from chaos import Framework, Settings, __version__
from chaos.program import parse_program
from chaos.runtime import Context
from chaos.server import make_server
from chaos.source_format import format_source
from tests.test_process_evolution_ide import SOURCE
from tests.test_process_integration import definition, wait_for


ROOT=Path(__file__).resolve().parent.parent
FILES=('chaos/native_specs.py','chaos/ide_language.py','chaos/openapi.py','web/processes.js','chaos/ide_agent.py','tests/test_process_evolution_ide.py','tests/test_process_evolution_ui.py')


class ProcessEvolutionUI(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_browser_native_evolution_failure_then_atomic_commit_and_next_turn(self):
        from playwright.sync_api import sync_playwright
        output=os.environ.get('CHAOS_PROCESS_EVOLUTION_AUDIT')
        if output:Path(output).mkdir(parents=True,exist_ok=True)
        calls=[]
        def forbidden(*args,**kwargs):calls.append(True);raise AssertionError('real Agent unexpectedly invoked')
        formatted=format_source(SOURCE)
        with tempfile.TemporaryDirectory(prefix='chaos-process-evolution-browser-')as folder,Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:'))as app,patch.object(Context,'agent',forbidden):
            app.create_project('evolution',source=formatted,files={'behavior.chaos':'input'},capabilities=['process.manage','program.adopt'])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
            try:
                with sync_playwright()as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1500,'height':1350})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('**/*',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=evolution');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source')
                        content=page.locator('.cm-content[contenteditable="true"]')
                        draft=SOURCE.replace('info.testCount','info.test')
                        content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(draft)
                        page.evaluate('(position)=>ChaosIDE.editor().dispatch({selection:{anchor:position}})',draft.index('info.test')+len('info.test'))
                        page.keyboard.press('Control+Space');completion=page.locator('.cm-completionLabel').filter(has_text='testCount').first
                        completion.wait_for();completion.click()
                        self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),SOURCE)
                        workbench_button(page,'formatSource');page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                        page.evaluate('(position)=>ChaosIDE.editor().dispatch({selection:{anchor:position},scrollIntoView:true})',formatted.index('reason =')+len('reason ='))
                        workbench_button(page,'ideSignatureButton')
                        page.wait_for_function("()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('reason')")
                        signature=page.locator('#ideSignatureHelp').inner_text();self.assertIn('-> ProcessEvolution',signature);self.assertIn('不执行测试或采用',signature)
                        function=page.locator('.ide-token-function').filter(has_text='process_evolve').first
                        function.wait_for();self.assertEqual(function.evaluate('el=>getComputedStyle(el).color'),theme_color(page, 'function'))
                        if output:page.screenshot(path=str(Path(output)/'evolution-signature-and-format.png'),full_page=True)
                        workbench_view(page,'structure');page.wait_for_function('()=>programDraft&&programDraft.root.statements.some(n=>n.kind==="def")')
                        self.assertEqual(page.evaluate('programDraft.semanticHash'),parse_program(formatted)['semanticHash'])
                        workbench_view(page,'source');base_version=app.get_project('evolution')['version'];adopted_version=base_version+1
                        self.assertEqual(app.get_project('evolution')['source'],formatted)
                        workbench_button(page,'processesButton');dialog=page.get_by_role('dialog',name='持续进程',exact=True);dialog.wait_for()
                        def create(key,increment):
                            dialog.get_by_role('button',name='创建进程',exact=True).click()
                            editor=dialog.get_by_role('region',name='进程定义编辑器')
                            editor.get_by_role('textbox',name='进程键',exact=True).fill(key)
                            editor.get_by_role('textbox',name='显示名称',exact=True).fill(key)
                            editor.get_by_role('textbox',name='模块路径',exact=True).fill('main.chaos')
                            editor.get_by_role('textbox',name='导出绑定',exact=True).fill('step')
                            editor.get_by_role('textbox',name='最大轮数（留空：持续运行）',exact=True).fill('10')
                            editor.get_by_role('textbox',name='初始状态 JSON',exact=True).fill(json.dumps({'count':0,'increment':increment}))
                            editor.get_by_role('button',name='校验定义并生成源码',exact=True).click()
                            page.wait_for_function("()=>document.querySelector('.process-editor [role=status]')?.textContent.includes('校验通过')")
                            editor.get_by_role('button',name='创建并启动首轮',exact=True).click()
                        create('bad',40)
                        page.wait_for_function("()=>ChaosProcesses.state.record?.key==='bad'&&ChaosProcesses.state.record?.status==='needs_reconciliation'")
                        failed=app.get_process('evolution','bad');self.assertEqual(failed['state'],{'count':0,'increment':40})
                        self.assertNotIn('lastEvolution',failed);self.assertEqual(app.get_project('evolution')['version'],base_version)
                        self.assertEqual(app.get_project('evolution')['files']['behavior.chaos'],'input')
                        self.assertEqual(dialog.locator('[data-process-evolution]').count(),0)
                        state=dialog.get_by_role('region',name='进程状态');state.locator('details').filter(has=page.get_by_text('已提交状态',exact=True)).locator('summary').click()
                        state.get_by_role('heading',name='bad · bad').scroll_into_view_if_needed()
                        if output:page.screenshot(path=str(Path(output)/'failed-evolution-keeps-source-state.png'),full_page=True)
                        create('good',41)
                        page.wait_for_function("(version)=>ChaosProcesses.state.record?.key==='good'&&ChaosProcesses.state.record?.lastEvolution?.version===version",arg=adopted_version)
                        record=app.get_process('evolution','good');receipt=record['lastEvolution']
                        self.assertEqual(record['lastVersion'],base_version);self.assertEqual(record['state'],{'count':1,'increment':41})
                        self.assertEqual(record['output'],{'executed':base_version});self.assertEqual(app.get_project('evolution')['version'],adopted_version)
                        self.assertEqual(app.get_project('evolution')['files']['behavior.chaos'],'input + 41')
                        card=dialog.locator('.process-card.selected')
                        self.assertIn('完成 1 轮 · r'+str(record['revision']),card.inner_text())
                        self.assertIn('等待消息 / 定时唤醒',card.inner_text())
                        self.assertIn('配置 r'+str(record['revision']),state.inner_text())
                        evolution=dialog.get_by_role('region',name='已提交原子演进')
                        self.assertIn(f'源码 v{base_version} → v{adopted_version}',evolution.inner_text());self.assertIn(f'最近执行快照：v{base_version}',evolution.inner_text())
                        evolution.locator('summary').click();self.assertEqual(json.loads(evolution.locator('pre').inner_text()),receipt)
                        self.assertEqual(evolution.get_by_role('button',name='查看演进运行').count(),1)
                        evolution.scroll_into_view_if_needed()
                        if output:page.screenshot(path=str(Path(output)/'committed-evolution-receipt.png'),full_page=True)
                        state.get_by_role('textbox',name='消息输入 JSON',exact=True).fill('null')
                        state.get_by_role('textbox',name='幂等消息 ID（可选）',exact=True).fill('continue-v2')
                        state.get_by_role('button',name='加入持久邮箱',exact=True).click()
                        page.wait_for_function("(version)=>ChaosProcesses.state.record?.status==='completed'&&ChaosProcesses.state.record?.lastVersion===version",arg=adopted_version)
                        continued=app.get_process('evolution','good');self.assertEqual(continued['state'],{'count':2,'increment':41});self.assertEqual(continued['output'],adopted_version)
                        self.assertEqual(continued['lastEvolution'],receipt)
                        self.assertIn('完成 2 轮 · r'+str(continued['revision']),card.inner_text())
                        self.assertIn('已完成',card.inner_text())
                        self.assertIn('配置 r'+str(continued['revision']),state.inner_text())
                        self.assertEqual(app.send_process('evolution','good',None,message_id='continue-v2')['status'],'consumed')
                        self.assertEqual(app.get_run(receipt['runId'])['snapshot']['version'],base_version)
                        self.assertEqual(calls,[]);self.assertEqual(errors,[]);self.assertEqual(external,[])
                        if output:
                            evidence={'status':'passed','sourceRoot':str(ROOT),'runtimeVersion':__version__,'failedVersion':base_version,'adoptedVersion':adopted_version,'executedVersion':record['lastVersion'],'continuedVersion':continued['lastVersion'],'failedState':failed['state'],'committedState':record['state'],'continuedState':continued['state'],'receipt':receipt,'ownedIntentSignature':True,'reflectionCompletion':True,'semanticHighlight':True,'formatterSemanticIdentity':True,'sourceIRIdentity':True,'failurePreservesSourceAndState':True,'nativeEvolutionViaProcess':True,'receiptMatchesDurableStore':True,'nextTurnUsesAdoptedSource':True,'selectedCardMatchesDetail':True,'realProviderCalls':len(calls),'errors':errors,'externalHTTP':external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in FILES}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_older_list_and_detail_responses_cannot_regress_selected_record(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='chaos-process-poll-browser-')as folder,Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:'))as app:
            source='async fn step(c){await sleep(0.2);process_step(c.state+1,c.turn)};export {step}'
            app.create_project('sync',source='null',files={'dot.chaos':source},capabilities=['process.manage'])
            app._engine.pause_dispatch(True)
            app.create_process('sync',1,'dot',definition(),state=0)
            app.create_process('sync',1,'other',definition(name='other'),state=10)
            app._engine.pause_dispatch(False)
            wait_for(app,'sync','dot',lambda row:row['status']=='running')
            # Every routed response below is an actual, previously persisted snapshot.
            older=app.processes('sync');self.assertEqual(older['items'][0]['status'],'running')
            current=wait_for(app,'sync','dot');wait_for(app,'sync','other')
            other=app.get_process('sync','other');self.assertGreater(current['revision'],older['items'][0]['revision'])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);path='/api/v1/projects/sync/processes'
            response={'list':older,'dot':current,'other':other,'hold':None};held=[];errors=[]
            try:
                with sync_playwright()as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        page=browser.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        def route(request):
                            url=request.request.url
                            if not url.startswith(origin+'/'):request.abort();return
                            suffix=url.removeprefix(origin)
                            if suffix==path:request.fulfill(json=response['list'])
                            elif suffix in (path+'/dot',path+'/other'):
                                key=suffix.rsplit('/',1)[1]
                                if response['hold']==key:held.append(request)
                                else:request.fulfill(json=response[key])
                            else:request.continue_()
                        page.route('**/*',route)
                        page.goto(origin+'/?project=sync');page.locator('.cm-editor').wait_for(state='attached');workbench_view(page,'source');workbench_button(page,'processesButton')
                        page.wait_for_function('(revision)=>ChaosProcesses.state.record?.revision===revision',arg=current['revision'])
                        # Manual refreshes make the response ordering deterministic.
                        page.evaluate('()=>clearTimeout(ChaosProcesses.state.timer)')
                        dialog=page.get_by_role('dialog',name='持续进程',exact=True)
                        def consistent(record):
                            shown=json.loads(page.evaluate('()=>stringifyJSON({selected:ChaosProcesses.state.record,list:ChaosProcesses.state.items.find(item=>item.key===ChaosProcesses.state.selected)})'))
                            self.assertEqual(shown['selected'],record);self.assertEqual(shown['list'],record)
                            card=dialog.locator('.process-card.selected').inner_text()
                            self.assertIn('完成 '+str(record['turn'])+' 轮 · r'+str(record['revision']),card)
                            detail=dialog.get_by_role('region',name='进程状态').inner_text()
                            self.assertIn('完成轮次 '+str(record['turn']),detail);self.assertIn('配置 r'+str(record['revision']),detail)
                        def take_held():
                            # The request event precedes dispatch to its route handler.
                            for _ in range(500):
                                if held:return held.pop()
                                page.wait_for_timeout(10)
                            self.fail('expected intercepted detail request')
                        consistent(current)
                        # An old list arrives after the detail has reached a newer revision.
                        response['hold']='dot'
                        with page.expect_request(lambda request:request.url==origin+path+'/dot'):
                            page.evaluate('()=>{window.pollFinished=false;ChaosProcesses.refresh().then(()=>window.pollFinished=true)}')
                        pending=take_held();consistent(current)
                        pending.fulfill(json=current);page.wait_for_function('()=>window.pollFinished')
                        # Now the list advances, while the following detail response is old.
                        app.send_process('sync','dot',None,message_id='advance')
                        newest=wait_for(app,'sync','dot',lambda row:row['turn']==2)
                        response['list']=app.processes('sync');response['dot']=current
                        with page.expect_request(lambda request:request.url==origin+path+'/dot'):
                            page.evaluate('()=>{window.pollFinished=false;ChaosProcesses.refresh().then(()=>window.pollFinished=true)}')
                        pending=take_held();consistent(newest)
                        pending.fulfill(json=current);page.wait_for_function('()=>window.pollFinished');consistent(newest)
                        self.assertEqual(json.loads(page.evaluate('()=>stringifyJSON(ChaosProcesses.state.items.find(item=>item.key==="other"))')),other)
                        # A superseded selection must not apply its late detail response.
                        response['hold']='other';response['dot']=newest
                        with page.expect_request(lambda request:request.url==origin+path+'/other'):
                            dialog.locator('.process-card').filter(has=page.get_by_text('other',exact=True)).click()
                        pending=take_held()
                        with page.expect_request(lambda request:request.url==origin+path+'/dot'):
                            dialog.locator('.process-card').filter(has=page.get_by_text('dot',exact=True)).click()
                        page.wait_for_function('()=>ChaosProcesses.state.selected==="dot"&&ChaosProcesses.state.record?.key==="dot"')
                        pending.fulfill(json=other)
                        consistent(newest);self.assertEqual(errors,[])
                        output=os.environ.get('CHAOS_PROCESS_EVOLUTION_AUDIT')
                        if output:
                            Path(output).mkdir(parents=True,exist_ok=True)
                            (Path(output)/'revision-sync-result.json').write_text(json.dumps({'status':'passed','sourceRoot':str(ROOT),'oldRevision':older['items'][0]['revision'],'detailRevision':current['revision'],'newListRevision':newest['revision'],'staleListRejectedBeforeDetailResponse':True,'newListUpdatesBothPanels':True,'staleDetailRejected':True,'supersededSelectionRejected':True,'unrelatedRowPreserved':True,'errors':errors,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ('web/processes.js','tests/test_process_evolution_ui.py')}},indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
