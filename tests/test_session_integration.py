"""Durable native messages through the real OpenAI HTTP transport and Engine."""
import copy
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from chaos import Framework,Client,Registry,ToolSpec
from chaos.settings import Settings
from chaos.runtime import Context
from chaos.store import Conflict
from chaos.extensions import DEFAULT_CAPABILITIES
from chaos.language import LanguageError


def agent(tools=None,**config):
    return {'kind':'agent','name':'worker','config':{'provider':'model','prompt':'Use authorized tools, then answer.','tools':['text.normalize'] if tools is None else tools,**config}}


class NativeFixture:
    """Local completion server used by the actual installed OpenAI client."""
    def __init__(self,respond=None):
        self.requests=[];self.respond=respond
        fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));fixture.requests.append(copy.deepcopy(body))
                message=fixture.respond(body) if fixture.respond else fixture.message(body)
                payload={'id':'fixture','object':'chat.completion','created':1,'model':body['model'],'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop'}]}
                raw=json.dumps(payload).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.settings=Settings(':memory:');self.settings.update({'providers':{'text':{'baseURL':'http://127.0.0.1:%d/v1'%self.server.server_port,'apiKey':'SESSION_TEST_SECRET'}},'runtime':{'timeout':10}},1)
    def message(self,body):
        messages=body['messages'];last=max(i for i,m in enumerate(messages) if m['role']=='user')
        if body.get('tools') and not any(m['role']=='tool' for m in messages[last:]):
            return {'role':'assistant','content':None,'tool_calls':[{'id':'call_'+str(len(self.requests)),'type':'function','function':{'name':body['tools'][0]['function']['name'],'arguments':json.dumps({'input':'  value  '})}}]}
        return {'role':'assistant','content':'reply-'+str(sum(m['role']=='user' for m in messages))}
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)


class SessionIntegration(unittest.TestCase):
    def fixture(self,respond=None):
        value=NativeFixture(respond);self.addCleanup(value.close);return value
    def app(self,fixture=None,**kw):
        fixture=fixture or self.fixture();value=Framework(settings=fixture.settings,**kw);self.addCleanup(value.close);return value
    def project(self,app,source='input',caps=None):
        return app.create_project('p',source=source,capabilities=['session.manage','text.normalize'] if caps is None else caps)
    def open(self,app,definition=None,limit=100000):return app.open_agent_session('p',1,'chat',definition or agent(),context_limit=limit)
    def turn(self,app,row,value='hello',id='one',version=1):return app.start_session_turn('p',version,row['key'],row['revision'],value,message_id=id).result(timeout=6)

    def test_native_messages_restart_and_idempotent_receipt(self):
        f=self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'state.sqlite3'
            with Framework(db,settings=f.settings) as app:
                self.project(app);row=self.open(app);out=self.turn(app,row)
                self.assertEqual(out['output'],'reply-1');self.assertFalse(out['replayed'])
                history=app.agent_session_history('p','chat')['items']
                self.assertEqual([x['message']['role']for x in history],['user','assistant','tool','assistant'])
                self.assertEqual(history[1]['message']['tool_calls'][0]['name'],'text.normalize')
                self.assertEqual(history[2]['message']['content'],'value')
                requests=len(f.requests)
                replay=self.turn(app,row)
                self.assertTrue(replay['replayed']);self.assertEqual(replay['revision'],out['revision']);self.assertEqual(len(f.requests),requests)
                with self.assertRaises(Exception):self.turn(app,row,'different')
            with Framework(db,settings=f.settings) as app:
                row=app.agent_session('p','chat');out=self.turn(app,row,'second','two')
                self.assertEqual(out['output'],'reply-2')
                prior=f.requests[-2]['messages']
                self.assertEqual([m['role'] for m in prior],['system','user','assistant','tool','assistant','user'])
                self.assertEqual(prior[4]['content'],'reply-1')
                self.assertTrue(all(x['status']=='completed' for x in app.agent_session_history('p','chat')['items']))

    def test_language_handle_cold_calls_and_revision_replay(self):
        app=self.app();source='agent worker {provider:"model",tools:["text.normalize"]};let s=await session_open("chat",worker);let first=await s("hi","id");let replay=await s("hi","id");let next=await s("again","id2");[first.output,replay.replayed,next.output,session_info(s).turn]'
        self.project(app,source)
        self.assertEqual(app.start('p',1,None,mode='source').result(),['reply-1',True,'reply-2',2])

    def test_compact_fork_and_closed_original(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app)
        self.turn(app,row);row=app.agent_session('p','chat');self.turn(app,row,'second','two')
        row=app.agent_session('p','chat');row=app.compact_agent_session('p','chat',row['revision'],1)
        self.assertEqual(row['contextFrom'],1);self.assertEqual(len(app.agent_session_history('p','chat')['items']),8)
        fork=app.fork_agent_session('p','chat',row['revision'],'fork')
        closed=app.close_agent_session('p','chat',row['revision']);self.assertEqual(closed['status'],'closed')
        with self.assertRaises(Exception):self.turn(app,closed,'bad','third')
        self.assertEqual(self.turn(app,fork,'third','three')['output'],'reply-2')
        self.assertEqual(app.agent_session('p','chat')['turn'],2)
        with self.assertRaises(Conflict):app.compact_agent_session('p','chat',closed['revision'],0)

    def test_permissions_revocation_aliases_and_no_expansion(self):
        f=self.fixture();app=self.app(f);self.project(app,caps=['session.manage','text.normalize','text.stats']);row=self.open(app,agent(['text.normalize','text.stats']))
        self.turn(app,row)
        project=app.get_project('p');project['capabilities']=['session.manage','text.stats'];app.save_project(project)
        row=app.agent_session('p','chat');self.turn(app,row,'new','two',version=2)
        request=f.requests[-2]
        from chaos.session_protocol import tool_alias
        self.assertEqual([t['function']['name']for t in request['tools']],[tool_alias('text.stats')])
        self.assertEqual(request['messages'][2]['tool_calls'][0]['function']['name'],tool_alias('text.normalize'))
        self.assertNotEqual(request['messages'][2]['tool_calls'][0]['function']['name'],request['tools'][0]['function']['name'])
        with self.assertRaises(LanguageError):app._engine.session_operation('p','history',{'key':'chat','after':0,'limit':10},allowed={'session.manage'})
        with self.assertRaises(LanguageError):app.fork_agent_session('p','chat',app.agent_session('p','chat')['revision'],'bad',agent=agent(['text.normalize']))

    def test_route_hot_settings_and_native_plugin_boundary(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app);self.turn(app,row)
        f.settings.update({'roles':{'executor':{'model':'fixture-next','autoUpgrade':False}}},f.settings.public()['version'])
        row=app.agent_session('p','chat');self.turn(app,row,'second','two');self.assertEqual(f.requests[-1]['model'],'fixture-next')
        f.settings.update({'runtime':{'backend':'codex'}},f.settings.public()['version'])
        row=app.agent_session('p','chat');count=len(f.requests)
        with self.assertRaises(Exception):self.turn(app,row,'third','three')
        self.assertEqual(len(f.requests),count);self.assertEqual(app.agent_session('p','chat')['status'],'needs_reconciliation')

    def test_uncertain_tool_journal_reconcile_preserves_committed_context(self):
        registry=Registry();calls=[]
        def fail(value,ctx):calls.append(value);raise RuntimeError('after effect')
        registry.register_tool(ToolSpec('fixture.effect',fail))
        f=self.fixture();app=self.app(f,registry=registry);self.project(app,caps=['session.manage','fixture.effect']);row=self.open(app,agent(['fixture.effect']))
        with self.assertRaises(Exception):self.turn(app,row)
        row=app.agent_session('p','chat');self.assertEqual(row['status'],'needs_reconciliation');self.assertEqual(row['turn'],0)
        history=app.agent_session_history('p','chat')['items'];self.assertEqual([m['message']['role']for m in history],['user','assistant'])
        self.assertTrue(all(m['status']=='uncertain'for m in history));count=len(calls)
        row=app.reconcile_agent_session('p','chat',row['revision'],'verified effect externally')
        with self.assertRaises(Exception):self.turn(app,row)
        self.assertEqual(len(calls),count);self.assertEqual(app.agent_session_history('p','chat')['items'][0]['status'],'abandoned')

    def test_crash_after_real_tool_preserves_pending_and_never_replays(self):
        f=self.fixture()
        with tempfile.TemporaryDirectory()as directory:
            db=Path(directory)/'db';marker=Path(directory)/'effect'
            with Framework(db,settings=f.settings)as app:self.project(app,caps=['session.manage','fixture.effect']);app.open_agent_session('p',1,'chat',agent([]))
            # Fresh registry-bound Agent must be opened in the child, which then dies during its actual tool.
            code='''import os,sys,time\nfrom pathlib import Path\nfrom chaos import Framework,Settings,Registry,ToolSpec\ns=Settings(':memory:');s.update({'providers':{'text':{'baseURL':sys.argv[3],'apiKey':'CHILD_FIXTURE_KEY_2026'}},'runtime':{'timeout':10}},1)\nr=Registry()\ndef effect(v,c):\n Path(sys.argv[2]).write_text('done');os._exit(17)\nr.register_tool(ToolSpec('fixture.effect',effect))\na=Framework(sys.argv[1],settings=s,registry=r);d={'kind':'agent','name':'worker','config':{'provider':'model','tools':['fixture.effect']}}\nx=a.open_agent_session('p',1,'crash',d);a.start_session_turn('p',1,'crash',x['revision'],'hello',message_id='one').result()\n'''
            result=subprocess.run([sys.executable,'-c',code,str(db),str(marker),'http://127.0.0.1:%d/v1'%f.server.server_port],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,17,result.stderr);self.assertEqual(marker.read_text(),'done');requests=len(f.requests)
            with Framework(db,settings=f.settings)as app:
                row=app.agent_session('p','crash');self.assertEqual(row['status'],'needs_reconciliation')
                with self.assertRaises(Exception):self.turn(app,row)
                self.assertEqual(len(f.requests),requests)
                self.assertEqual(len(app.agent_session_history('p','crash')['items']),2)

    def test_cancellation_and_close_do_not_commit_inflight_reply(self):
        entered=threading.Event();release=threading.Event()
        def respond(body):entered.set();release.wait(4);return {'role':'assistant','content':'late'}
        f=self.fixture(respond);app=self.app(f);self.project(app);row=self.open(app)
        run=app.start_session_turn('p',1,'chat',row['revision'],'hi',message_id='one');self.assertTrue(entered.wait(3));self.assertTrue(run.cancel())
        release.set();self.assertEqual(run.wait(timeout=4)['status'],'cancelled')
        row=app.agent_session('p','chat');self.assertEqual(row['status'],'needs_reconciliation');self.assertEqual(row['turn'],0)

    def test_concurrent_claims_and_sdk_admission_are_singleflight(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app)
        claim=app._store.sessions.claim('p','chat',row['revision'],'hi','one',{'session.manage','text.normalize'},'run',1,f.settings.redact)
        with self.assertRaises(Conflict):app._store.sessions.claim('p','chat',row['revision'],'hi','two',{'session.manage','text.normalize'},'other',1,f.settings.redact)
        with self.assertRaises(Conflict):app._store.sessions.claim('p','chat',row['revision'],'hi','one',{'session.manage','text.normalize'},'other',1,f.settings.redact)
        self.assertFalse(f.requests);app._store.sessions.fail(claim,'cancelled')

    def test_failed_commit_preserves_uncertain_and_cleanup_before_commit(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app)
        original=app._store.sessions.complete
        with patch.object(app._store.sessions,'complete',side_effect=RuntimeError('persist failed')):
            with self.assertRaises(Exception):self.turn(app,row)
        row=app.agent_session('p','chat');self.assertEqual(row['turn'],0);self.assertEqual(row['status'],'needs_reconciliation')
        self.assertEqual(len(app.agent_session_history('p','chat')['items']),4)
        row=app.reconcile_agent_session('p','chat',row['revision'],'checked')
        with patch.object(Context,'_close_session_resources',side_effect=RuntimeError('cleanup failed')):
            with self.assertRaises(Exception):self.turn(app,row,'next','two')
        self.assertEqual(app.agent_session('p','chat')['turn'],0)

    def test_limits_identity_and_no_model_for_validation_failure(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app,limit=1000)
        with self.assertRaises(Conflict):self.open(app,agent(prompt='different'),limit=1000)
        with self.assertRaises(LanguageError):app.open_agent_session('p',1,'bad',agent(provider='local'))
        with self.assertRaises(Exception):self.turn(app,row,'x'*1001)
        self.assertEqual(app.agent_session('p','chat')['status'],'idle');self.assertFalse(f.requests)
        with self.assertRaises(ValueError):app.open_agent_session('p',1,'bad',agent(),context_limit=True)
        self.assertNotIn('session.manage',DEFAULT_CAPABILITIES)

    def test_process_failed_after_session_commit_replay_without_provider(self):
        f=self.fixture();app=self.app(f)
        src='async fn step(c){let s=await session_load("chat");let r=await s("work","stable");if c.state==0 {throw "after session commit"} else {process_step(2,r.replayed,true)}};export {step}'
        app.create_project('p',source='input',files={'dot.chaos':src},capabilities=['session.manage','process.manage','text.normalize'])
        self.open(app)
        d={'kind':'process','name':'dot','module':'dot.chaos','export':'step','intervalSeconds':None,'maxTurns':4}
        app.create_process('p',1,'dot',d,state=0)
        from tests.test_process_integration import wait_for
        process=wait_for(app,predicate=lambda r:r['status']=='needs_reconciliation')
        self.assertEqual(app.agent_session('p','chat')['turn'],1);requests=len(f.requests)
        app.reconcile_process('p','dot',process['revision'],1,evidence='session committed',resume=True)
        app.send_process('p','dot',None,message_id='next')
        done=wait_for(app,predicate=lambda r:r['status']=='completed');self.assertTrue(done['output']);self.assertEqual(len(f.requests),requests)

    def test_skill_poison_function_context_and_reserved_tools(self):
        f=self.fixture();app=self.app(f)
        src='await record_skill(async fn(x){try {await session_status("chat")} catch e {null};tool("text.normalize","x")})'
        self.project(app,src);self.open(app)
        with self.assertRaises(Exception):app.start('p',1,None,mode='source').result()
        ctx=Context(app._engine,None,app.get_project('p'),threading.Event())
        with self.assertRaises(LanguageError):ctx.session_operation('load',{'key':'chat'},allow_agents=False)
        self.assertEqual(ctx.session_operation('status',{'key':'chat'},allow_agents=False)['key'],'chat')
        with self.assertRaises(LanguageError):ctx.agent('bad',{'provider':'model','tools':['session.manage']},None,{'session.manage'})
        self.assertFalse(f.requests)

    def test_redaction_durable_history_and_pagination(self):
        f=self.fixture(lambda body:{'role':'assistant','content':'SESSION_TEST_SECRET answer'});app=self.app(f);self.project(app);row=self.open(app,agent([]))
        out=self.turn(app,row,'SESSION_TEST_SECRET hello');self.assertNotIn('SESSION_TEST_SECRET',json.dumps(out))
        history=app.agent_session_history('p','chat',limit=1);self.assertIsNotNone(history['nextCursor'])
        next_page=app.agent_session_history('p','chat',after=history['nextCursor']);self.assertEqual(len(next_page['items']),1)
        self.assertNotIn('SESSION_TEST_SECRET',json.dumps(history)+json.dumps(next_page))
        self.assertTrue(all('lease' not in json.dumps(x) for x in app.agent_sessions('p')['items']))

    def test_fork_does_not_launder_history_authority_and_narrows_tools(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app);self.turn(app,row)
        row=app.agent_session('p','chat');fork=app.fork_agent_session('p','chat',row['revision'],'narrow',agent=agent([]))
        self.assertEqual(fork['launchCapabilities'],['session.manage','text.normalize'])
        with self.assertRaises(LanguageError):app._engine.session_operation('p','history',{'key':'narrow','after':0,'limit':10},allowed={'session.manage'})
        self.turn(app,fork,'narrow turn','second');self.assertNotIn('tools',f.requests[-1])
        project=app.get_project('p');project['capabilities']=['session.manage'];app.save_project(project)
        row=app.agent_session('p','narrow');self.assertEqual(app.close_agent_session('p','narrow',row['revision'])['status'],'closed')

    def test_large_archived_history_paginates_without_losing_messages(self):
        f=self.fixture(lambda body:{'role':'assistant','content':'x'*39000});app=self.app(f);self.project(app);row=self.open(app,agent([]))
        for n in range(7):
            self.turn(app,row,'y'*39000,'id'+str(n))
            row=app.agent_session('p','chat');row=app.compact_agent_session('p','chat',row['revision'],0)
        page=app.agent_session_history('p','chat');self.assertIsNotNone(page['nextCursor'])
        collected=[]
        while True:
            from chaos.extensions import json_copy
            json_copy(page);self.assertLess(len(json.dumps(page)),250000);collected.extend(page['items'])
            if page['nextCursor'] is None:break
            page=app.agent_session_history('p','chat',after=page['nextCursor'])
        self.assertEqual(len(collected),14);self.assertEqual(len({r['seq']for r in collected}),14)

    def test_sdk_preflight_conflicts_and_failed_thread_admission_stays_idle(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app)
        with self.assertRaises(Conflict):app.start_session_turn('p',1,'chat',row['revision']+1,'hi')
        self.assertEqual(app.runs('p')['items'],[])
        with patch('chaos.runtime.threading.Thread.start',side_effect=RuntimeError('no thread')):
            with self.assertRaises(RuntimeError):app.start_session_turn('p',1,'chat',row['revision'],'hi')
        self.assertEqual(app.agent_session('p','chat')['status'],'idle');self.assertEqual(app.runs('p')['items'][0]['status'],'failed')
        self.assertFalse(app._engine.active);self.assertFalse(f.requests)

    def test_transaction_event_failure_rolls_back_receipt_then_marks_uncertain(self):
        f=self.fixture();app=self.app(f);self.project(app);row=self.open(app);event=app._store._event
        def fail_completion(project,kind,payload,run_id=None):
            if kind=='session.completed':raise RuntimeError('terminal event write failed')
            return event(project,kind,payload,run_id)
        with patch.object(app._store,'_event',side_effect=fail_completion):
            with self.assertRaises(Exception):self.turn(app,row)
        row=app.agent_session('p','chat');self.assertEqual((row['status'],row['turn']),('needs_reconciliation',0))
        with app._store.lock:
            turn=app._store.db.execute('SELECT output,revision,status FROM session_turns').fetchone()
        self.assertIsNone(turn['output']);self.assertIsNone(turn['revision']);self.assertEqual(turn['status'],'uncertain')

    def test_definition_prompt_and_reconciliation_redact_configured_key(self):
        f=self.fixture(lambda b:(_ for _ in ()).throw(ValueError()))
        # No provider dispatch is needed to test durable definition and evidence.
        app=self.app(f);self.project(app);row=self.open(app,agent([],prompt='Do not disclose SESSION_TEST_SECRET'))
        self.assertNotIn('SESSION_TEST_SECRET',json.dumps(row))
        from chaos.agent_values import definition_hash
        self.assertEqual(row['definitionHash'],definition_hash(row['agent']))
        claim=app._store.sessions.claim('p','chat',row['revision'],'hi','one',{'session.manage','text.normalize'},'test',1,f.settings.redact)
        app._store.sessions.fail(claim,'failed');row=app.agent_session('p','chat')
        row=app.reconcile_agent_session('p','chat',row['revision'],'checked SESSION_TEST_SECRET')
        self.assertNotIn('SESSION_TEST_SECRET',json.dumps(row));self.assertFalse(f.requests)

    def test_real_concurrent_turn_conflicts_before_second_native_request(self):
        entered=threading.Event();release=threading.Event()
        def respond(body):entered.set();release.wait(4);return {'role':'assistant','content':'one answer'}
        f=self.fixture(respond);app=self.app(f);self.project(app);row=self.open(app,agent([]));answers=[];errors=[]
        def call():
            ctx=Context(app._engine,'first',app.get_project('p'),threading.Event())
            try:answers.append(ctx.session_operation('turn',{'key':'chat','revision':row['revision'],'input':'same','messageId':'same'}))
            except BaseException as error:errors.append(error)
        worker=threading.Thread(target=call);worker.start()
        try:
            self.assertTrue(entered.wait(3));ctx=Context(app._engine,'second',app.get_project('p'),threading.Event())
            with self.assertRaises(Conflict):ctx.session_operation('turn',{'key':'chat','revision':row['revision'],'input':'same','messageId':'same'})
            self.assertEqual(len(f.requests),1)
        finally:release.set();worker.join(4)
        self.assertFalse(worker.is_alive());self.assertFalse(errors);self.assertEqual(answers[0]['receipt']['output'],'one answer')
        replay=self.turn(app,row,'same','same');self.assertTrue(replay['replayed']);self.assertEqual(len(f.requests),1)

    def test_framework_close_waits_for_actual_session_tool_and_keeps_storage(self):
        entered=threading.Event();release=threading.Event();registry=Registry()
        def tool(value,context):entered.set();release.wait(4);return value
        registry.register_tool(ToolSpec('fixture.wait',tool));f=self.fixture();app=self.app(f,registry=registry)
        self.project(app,caps=['session.manage','fixture.wait']);row=self.open(app,agent(['fixture.wait']))
        run=app.start_session_turn('p',1,'chat',row['revision'],'hi',message_id='one')
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(TimeoutError):app.close(timeout=.01)
            self.assertTrue(app._store.db.execute('SELECT 1').fetchone())
        finally:release.set()
        app.close(timeout=4)
        self.assertTrue(app._closed)

    def test_client_routes_keep_remote_run_contract(self):
        client=Client('http://localhost:1')
        with patch.object(client,'_request',return_value={'id':'r','project_id':'p'})as request:
            client.open_agent_session('p',1,'chat',agent());request.assert_called_with('POST','/projects/p/agent-sessions',{'version':1,'key':'chat','agent':agent(),'contextLimit':100000})
            run=client.start_session_turn('p',1,'chat',3,'hello',message_id='one');self.assertEqual(run.id,'r')
            request.assert_called_with('POST','/projects/p/agent-sessions/chat/turns',{'version':1,'revision':3,'input':'hello','messageId':'one'})
            client.reconcile_agent_session('p','chat',3,'checked');request.assert_called_with('POST','/projects/p/agent-sessions/chat/reconcile',{'revision':3,'evidence':'checked'})
