"""Public native Session transport, CLI, closed payloads and shared Run state."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Framework,Client
from chaos.client import APIError,ValidationError,ConflictError
from chaos.server import make_server
from tests.test_session_integration import NativeFixture,agent

class SessionHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.fixture=NativeFixture();self.addCleanup(self.fixture.close)
        self.app=Framework(settings=self.fixture.settings);self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
        self.client.create_project('p',source='throw "Session SDK must not evaluate project entry"',capabilities=['session.manage','text.normalize'])
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def open(self,key='chat',definition=None):return self.client.open_agent_session('p',1,key,definition or agent())
    def turn(self,row,value='hello',id='one'):
        handle=self.client.start_session_turn('p',1,row['key'],row['revision'],value,message_id=id)
        return handle,handle.result(timeout=8)
    def cli(self,*args):
        r=subprocess.run([sys.executable,'-m','chaos','session',*args,'--server',self.origin],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=8)
        self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)

    def test_schema_open_and_shared_run_native_history(self):
        self.assertEqual(self.client.session_schema(),self.app.session_schema())
        row=self.open();self.assertEqual(row['status'],'idle');self.assertFalse(self.fixture.requests)
        handle,first=self.turn(row);self.assertEqual(first['output'],'reply-1')
        run=handle.get();self.assertEqual(run['status'],'completed');self.assertEqual(run['snapshot']['source'],'throw "Session SDK must not evaluate project entry"')
        row=self.client.agent_session('p','chat');_,second=self.turn(row,'next','two');self.assertEqual(second['output'],'reply-2')
        history=self.client.agent_session_history('p','chat');self.assertEqual(len(history['items']),8)
        self.assertEqual(history['items'][2]['message']['content'],'value')
        roles=[x['role']for x in self.fixture.requests[-2]['messages']]
        self.assertEqual(roles,['system','user','assistant','tool','assistant','user'])
        self.assertEqual(self.client.get_project('p')['version'],1)

    def test_idempotent_receipt_precedes_stale_revision_and_conflicts_do_not_dispatch(self):
        row=self.open();_,first=self.turn(row);count=len(self.fixture.requests)
        _,again=self.turn(row);self.assertTrue(again['replayed']);self.assertEqual(again['revision'],first['revision'])
        with self.assertRaises(ConflictError):self.client.start_session_turn('p',1,'chat',row['revision'],'hello',message_id='new')
        with self.assertRaises(ConflictError):self.client.start_session_turn('p',1,'chat',row['revision'],'different',message_id='one')
        self.assertEqual(len(self.fixture.requests),count)

    def test_compact_fork_close_and_history_pagination(self):
        row=self.open();self.turn(row);row=self.client.agent_session('p','chat')
        page=self.client.agent_session_history('p','chat',limit=1);self.assertIsNotNone(page['nextCursor'])
        tail=self.client.agent_session_history('p','chat',after=page['nextCursor']);self.assertEqual(len(tail['items']),3)
        row=self.client.compact_agent_session('p','chat',row['revision'],0)
        self.assertEqual(len(self.client.agent_session_history('p','chat')['items']),4)
        fork=self.client.fork_agent_session('p','chat',row['revision'],'fork',agent=agent([]))
        closed=self.client.close_agent_session('p','chat',row['revision']);self.assertEqual(closed['status'],'closed')
        with self.assertRaises(ConflictError):self.client.start_session_turn('p',1,'chat',closed['revision'],'no')
        _,out=self.turn(fork,'new','fork-one');self.assertEqual(out['output'],'reply-1')
        self.assertNotIn('tools',self.fixture.requests[-1])
        self.assertEqual(len(self.client.agent_sessions('p')['items']),2)

    def test_authority_and_closed_transport_cannot_inject_protocol(self):
        self.client.create_project('denied',source='null',capabilities=[])
        with self.assertRaises(APIError):self.client.open_agent_session('denied',1,'x',agent([]))
        with self.assertRaises(APIError):self.open('bad',agent([],provider='local'))
        with self.assertRaises(ConflictError):self.client.open_agent_session('p',99,'stale',agent([]))
        self.open()
        prefix='/projects/p/agent-sessions/chat/'
        for suffix,body in [('turns',{'version':1,'revision':1,'input':None,'messages':[]}),('fork',{'revision':1,'newKey':'x','capabilities':[]}),('compact',{'revision':1,'keepTurns':0,'history':[]}),('reconcile',{'revision':1,'evidence':'checked','output':'forged'})]:
            with self.subTest(suffix=suffix),self.assertRaises(ValidationError):self.client._request('POST',prefix+suffix,body)
        self.assertFalse(self.fixture.requests)
        with self.assertRaises(APIError):self.client.agent_sessions('denied')
        with self.assertRaises(APIError):self.client.agent_session_history('denied','chat')

    def test_failed_tool_journal_requires_reconcile_and_new_message_id(self):
        self.fixture.respond=lambda body:{'role':'assistant','content':None,'tool_calls':[{'id':'bad','type':'function','function':{'name':'unknown_alias','arguments':'{}'}}]}
        row=self.open()
        with self.assertRaises(Exception):self.turn(row)
        failed=self.client.agent_session('p','chat');self.assertEqual(failed['status'],'needs_reconciliation');self.assertEqual(failed['turn'],0)
        history=self.client.agent_session_history('p','chat');self.assertTrue(history['items']);self.assertEqual(history['items'][0]['status'],'uncertain')
        settled=self.client.reconcile_agent_session('p','chat',failed['revision'],'No authorized tool was executed; abandon failed turn')
        self.assertEqual(settled['status'],'idle')
        with self.assertRaises(ConflictError):self.client.start_session_turn('p',1,'chat',settled['revision'],'hello',message_id='one')
        self.fixture.respond=None;_,out=self.turn(settled,'next','new');self.assertEqual(out['output'],'reply-1')
        self.assertEqual(self.client.agent_session_history('p','chat')['items'][0]['status'],'abandoned')

    def test_cli_uses_same_service_and_preserves_large_integer_input(self):
        file=Path(self.temp.name)/'agent.json';file.write_text(json.dumps(agent([])))
        row=self.cli('open','p','cli','--version','1','--agent-file',str(file))
        run=self.cli('send','p','cli','--version','1','--revision',str(row['revision']),'--input-json','{"count":9007199254740993}','--message-id','cli-one')
        from chaos.client import RunHandle
        RunHandle(self.client,run).result(timeout=8)
        history=self.cli('history','p','cli');self.assertEqual(json.loads(history['items'][0]['message']['content'])['count'],9007199254740993)
        self.assertEqual(self.cli('list','p')['items'][0]['key'],'cli')
        row=self.cli('get','p','cli');closed=self.cli('close','p','cli','--revision',str(row['revision']));self.assertEqual(closed['status'],'closed')

    def test_openapi_static_assets_and_old_paths(self):
        spec=self.client._request('GET','/openapi.json');schemas=spec['components']['schemas'];paths=spec['paths']
        for name in ('OpenAgentSession','AgentSessionTurn','ForkAgentSession','CompactAgentSession','CloseAgentSession','ReconcileAgentSession'):self.assertFalse(schemas[name]['additionalProperties'])
        self.assertIn('202',paths['/projects/{projectId}/agent-sessions/{key}/turns']['post']['responses'])
        for path in ('/projects/{projectId}/processes','/projects/{projectId}/program-adopt','/agents/schema'):self.assertIn(path,paths)
        import urllib.request
        for path in ('/sessions.js','/sessions.css'):
            with urllib.request.urlopen(self.origin+path)as response:self.assertEqual(response.status,200)
