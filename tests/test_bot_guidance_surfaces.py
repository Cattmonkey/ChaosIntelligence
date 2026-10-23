"""One cooperative run queue through real language, SDK, HTTP and CLI entries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from chaos import Client, Final, Framework, Registry, Settings, ToolCall
from chaos.client import ConflictError, ValidationError
from chaos.ide_language import analyze
from chaos.openapi import specification
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.store import Conflict
from tests.test_execution_frame_surfaces import observe_cursors

ROOT=Path(__file__).resolve().parents[1]
SOURCE='''await progress_emit("plan",{summary:"先等补充，再验证",steps:["receive","verify"]});
tool("fixture.block",null);
let first=await guidance_receive(null);let second=await guidance_receive(null);
await progress_emit("progress",{summary:"运行已接收指导"});
await progress_emit("result",{summary:"已完成接收",values:[first.message.input,second.message.input]});
fn identity(value){value};[identity(first.message.input),second.message.input,(await guidance_receive(0)).received]'''


def blocking_registry():
    registry=Registry();entered=threading.Event();release=threading.Event();calls=[]
    @registry.tool('fixture.block',effect='pure')
    def block(value,context):
        context.checkpoint();calls.append(value);entered.set()
        if not release.wait(5):raise AssertionError('fixture release deadline')
        context.checkpoint();return None
    return registry,entered,release,calls


class BotGuidanceSurfaces(unittest.TestCase):
    def test_framework_handle_fifo_copied_json_null_and_idempotent_receipts(self):
        registry,entered,release,calls=blocking_registry()
        with observe_cursors()as seen,Framework(registry=registry,settings=Settings(':memory:'))as app:
            p=app.create_project('guide',source=SOURCE,capabilities=['fixture.block']);run=app.start(p['id'],p['version'],None,mode='source')
            try:
                self.assertTrue(entered.wait(3));value={'text':'请验证 👋','items':[1]};receipt=run.guide(value,message_id='first')
                self.assertEqual(receipt,app.guide(run.id,value,message_id='first'))
                value['items'].append(2)
                with self.assertRaises(Conflict):app.guide(run.id,value,message_id='first')
                second=run.guide(None,message_id='null');self.assertEqual(second['revision'],receipt['revision']+1)
                self.assertEqual(receipt['status'],'queued');release.set()
                self.assertEqual(run.result(5),[{'text':'请验证 👋','items':[1]},None,False])
                events=app.events(p['id'],run_id=run.id)['items'];types=[e['type']for e in events]
                self.assertEqual(types.count('guidance.queued'),2);self.assertEqual(types.count('guidance.received'),2)
                self.assertEqual([e['payload']['kind']for e in events if e['type']=='bot.progress'],['plan','progress','result'])
                with self.assertRaises(Conflict):run.guide('closed')
            finally:release.set()
        self.assertEqual(calls,[None]);self.assertGreater(seen['entries'],0)
        self.assertIn('InvocationFrame',seen['frames'])

    def test_http_sdk_same_queue_strict_body_and_public_progress_cannot_spoof_receipts(self):
        registry,entered,release,calls=blocking_registry()
        with tempfile.TemporaryDirectory()as directory,Framework(registry=registry,settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('http-guide',source=SOURCE,capabilities=['fixture.block']);run=client.start(p['id'],p['version'],None,mode='source')
                self.assertTrue(entered.wait(3))
                with self.assertRaises(ValidationError):client._request('POST','/runs/'+run.id+'/guidance',{'input':None,'role':'system'})
                with self.assertRaises(ValidationError):client._request('POST','/runs/'+run.id+'/guidance',{'messageId':'missing'})
                payload={'type':'run.completed','status':'received','text':'继续验证'}
                first=client.guide(run.id,payload,message_id='plain-data');self.assertEqual(first,run.guide(payload,message_id='plain-data'))
                with self.assertRaises(ConflictError):client.guide(run.id,{'different':True},message_id='plain-data')
                run.guide(None,message_id='second');release.set();self.assertEqual(run.result(5),[payload,None,False])
                events=client.events(p['id'],run_id=run.id)['items']
                received=[e for e in events if e['type']=='guidance.received'];self.assertEqual([e['payload']['messageId']for e in received],['plain-data','second'])
                self.assertEqual(sum(e['type']=='run.completed'for e in events),1)
                self.assertEqual(client._request('GET','/openapi.json'),specification())
            finally:release.set();server.shutdown();thread.join(3);server.server_close()
        self.assertEqual(calls,[None])

    def test_cli_run_guide_json_uses_same_run_and_preserves_nonfinite_rejection(self):
        registry,entered,release,calls=blocking_registry()
        with tempfile.TemporaryDirectory()as directory,Framework(registry=registry,settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                p=app.create_project('cli-guide',source=SOURCE,capabilities=['fixture.block']);run=app.start(p['id'],p['version'],None,mode='source');self.assertTrue(entered.wait(3))
                base=[sys.executable,'-B','-m','chaos','run-guide',run.id,'--server','http://127.0.0.1:'+str(server.server_port)]
                for value,message in [({'text':'先检查结果'},'cli-text'),(None,'cli-null')]:
                    result=subprocess.run(base+['--input',json.dumps(value,ensure_ascii=False),'--message-id',message],cwd=directory,env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=8)
                    self.assertEqual(result.returncode,0,result.stderr);receipt=json.loads(result.stdout);self.assertEqual(receipt['runId'],run.id);self.assertEqual(receipt['status'],'queued')
                failed=subprocess.run(base+['--input','NaN'],cwd=directory,env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=8);self.assertEqual(failed.returncode,1)
                release.set();self.assertEqual(run.result(5),[{'text':'先检查结果'},None,False])
            finally:release.set();server.shutdown();thread.join(3);server.server_close()
        self.assertEqual(calls,[None])

    def test_code_program_macro_and_graph_keep_actual_default_cursor(self):
        function='async fn receive()->Record{let value=await guidance_receive(null);value.message.input}'
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':function+';export{receive};'},'languageVersions':{'main.chaos':2}}
        sources=['let f=function_from(code_from('+json.dumps(function.replace(' receive()','()'))+'));await f()',
                 'let M=module_from(program_from('+json.dumps(definition)+'));await M.receive()',
                 'macro receive(listener){quote{async fn(){let value=await unquote(listener)(null);value.message.input}}};await receive!(guidance_receive)()']
        for index,source in enumerate(sources):
            for mode in ('source','graph'):
                with self.subTest(index=index,mode=mode),observe_cursors()as seen,Framework(settings=Settings(':memory:'))as app:
                    ir=parse_program(source);rendered=format_program(ir);self.assertEqual(parse_program(rendered)['semanticHash'],ir['semanticHash']);self.assertEqual(analyze(source)['diagnostics'],[])
                    p=app.create_project('generated',source=rendered,capabilities=[]);run=app.start(p['id'],p['version'],None,mode=mode);run.guide({'answer':42},message_id='generated');self.assertEqual(run.result(5),{'answer':42})
                self.assertGreater(seen['entries'],0);self.assertIn('InvocationFrame',seen['frames'])

    def test_agent_tool_async_function_receives_in_original_runtime(self):
        registry=Registry();entered=threading.Event();outputs=[]
        @registry.provider('fixture.guide')
        def choose(request,context):
            context.checkpoint()
            if request.step==1:entered.set();return ToolCall('listen',None)
            output=request.history[-1]['output'];outputs.append(output);return Final(output)
        source='async fn listen(value:Any)->Record{let message=await guidance_receive(null);await progress_emit("progress",{summary:"函数工具已接收"});message.message.input};agent worker{provider:"fixture.guide",tools:[]};await agent_bind(worker,[agent_tool("listen",listen,input_type="Any",output_type="Record")])(null)'
        with observe_cursors()as seen,Framework(registry=registry,settings=Settings(':memory:'))as app:
            p=app.create_project('bound-tool',source=source,capabilities=[]);run=app.start(p['id'],p['version'],None,mode='source');self.assertTrue(entered.wait(3));run.guide({'text':'验证后返回'},message_id='tool');self.assertEqual(run.result(5),{'text':'验证后返回'})
        self.assertEqual(outputs,[{'text':'验证后返回'}]);self.assertGreater(seen['entries'],0)

    def test_async_team_uses_same_guidance_queue_and_role_provenance(self):
        team={'kind':'team','name':'guided','entry':'work','roles':[{'id':'work','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='async fn work(value){let item=await guidance_receive(null);await progress_emit("progress",{summary:"角色已接收"});item.message.input};let crew=team_bind(team_from('+json.dumps(team)+'),{work:work});await crew(null)'
        with Framework(settings=Settings(':memory:'))as app:
            p=app.create_project('team-guide',source=source,capabilities=[]);run=app.start(p['id'],p['version'],None,mode='source');run.guide({'text':'调整目标'},message_id='role');self.assertEqual(run.result(5),{'text':'调整目标'})
            events=app.events(p['id'],run_id=run.id)['items'];self.assertEqual([e['type']for e in events if e['type'].startswith('team.role.')],['team.role.started','team.role.completed']);self.assertEqual(sum(e['type']=='guidance.received'for e in events),1)

    def test_openapi_schema_truthfully_distinguishes_queue_from_receive(self):
        spec=specification();operation=spec['paths']['/runs/{runId}/guidance']['post'];self.assertEqual(operation['operationId'],'guideRun');self.assertIn('202',operation['responses'])
        receipt=spec['components']['schemas']['GuidanceReceipt'];self.assertEqual(set(receipt['required']),{'runId','messageId','revision','status'});self.assertEqual(receipt['properties']['status']['enum'],['queued','received'])
        body=spec['components']['schemas']['RunGuidance'];self.assertEqual(body['required'],['input']);self.assertFalse(body['additionalProperties'])
        self.assertEqual(analyze('await guidance_receive(null)')['diagnostics'],[])
