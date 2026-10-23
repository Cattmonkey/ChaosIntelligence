"""Real native function tools, durable roles, source adoption and public evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Framework, Client
from chaos.server import make_server
from tests.test_session_integration import NativeFixture
from tests.test_process_evolution_integration import wait_for

ROOT=Path(__file__).resolve().parents[1]

def modules():
    return {p.name:p.read_text() for p in (ROOT/'examples/tool-session-dot').glob('*.chaos')}

def respond(body):
    messages=body['messages'];start=max(i for i,m in enumerate(messages) if m['role']=='user')
    results=[m for m in messages[start:] if m['role']=='tool']
    if not results:
        return {'role':'assistant','content':None,'tool_calls':[{'id':'call_'+str(start),'type':'function','function':{'name':body['tools'][0]['function']['name'],'arguments':json.dumps({'input':None})}}]}
    result=json.loads(results[-1]['content'])
    if 'definition' in result:
        candidate=copy.deepcopy(result['definition'])
        candidate['files']['behavior.chaos']='fn answer(value:Int)->Int {value+'+str(result['increment'])+'}; export {answer};'
        output=candidate
    else:
        if result['passed'] is not True:raise AssertionError(result)
        output={'approve':True,'reason':'actual program_probe passed'}
    return {'role':'assistant','content':json.dumps(output)}

class SessionToolsIntegration(unittest.TestCase):
    def fixture(self,handler=respond):
        f=NativeFixture(handler);self.addCleanup(f.close);return f

    def test_two_bound_roles_two_adoptions_three_turns_and_two_restarts(self):
        fixture=self.fixture();source=modules()
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'workspace.sqlite'
            with Framework(database,settings=fixture.settings) as app:
                app.create_project('dot',source=source['main.chaos'],files=source,capabilities=['session.manage','process.manage','program.adopt'])
                app.start('dot',1,{'intervalSeconds':None},mode='source').result(10)
                first=wait_for(app,'dot',1)
                self.assertEqual(first['state']['generation'],1)
                self.assertEqual(first['output']['before'],41)
                self.assertEqual(first['output']['sameFunction'],41)
                self.assertEqual(first['lastEvolution']['version'],2)
                first_binding=app.agent_session('dot','tool_programmer')['lastToolBinding']
                self.assertEqual(first_binding['sourceVersion'],1)
                self.assertEqual(first_binding['runId'],first['lastRunId'])
                app._engine.pause_dispatch(True)
                app.send_process('dot','dot',None,message_id='second')
            with Framework(database,settings=fixture.settings) as app:
                second=wait_for(app,'dot',2)
                self.assertEqual(second['state']['generation'],2)
                self.assertEqual(second['output']['before'],42)
                self.assertEqual(second['lastEvolution']['version'],3)
                for key,name in [('tool_programmer','program_snapshot'),('tool_reviewer','check_candidate')]:
                    row=app.agent_session('dot',key)
                    self.assertEqual(row['turn'],2)
                    self.assertEqual(row['lastToolBinding']['sourceVersion'],2)
                    self.assertEqual(row['lastToolBinding']['tools'][0]['name'],name)
                    history=app.agent_session_history('dot',key)['items']
                    self.assertEqual([x['message']['role'] for x in history],['user','assistant','tool','assistant']*2)
                    self.assertEqual([x['toolBinding']['sourceVersion'] for x in history],[1]*4+[2]*4)
                    self.assertTrue(all(x['status']=='completed' for x in history))
                    self.assertEqual(history[0]['toolBinding']['bindingHash'],history[4]['toolBinding']['bindingHash'])
                app._engine.pause_dispatch(True)
                app.send_process('dot','dot',None,message_id='final')
            with Framework(database,settings=fixture.settings) as app:
                done=wait_for(app,'dot',3,'completed')
                self.assertEqual(done['output'],{'value':43,'version':3})
                self.assertEqual(done['lastEvolution'],second['lastEvolution'])
                self.assertEqual(app.get_project('dot')['version'],3)
        self.assertEqual(len(fixture.requests),8)
        self.assertEqual(sum(m['role']=='user' for m in fixture.requests[4]['messages']),2)
        self.assertEqual(sum(m['role']=='tool' for m in fixture.requests[4]['messages']),1)

    def test_http_sdk_cli_read_same_binding_and_raw_api_cannot_replay_bound_turn(self):
        fixture=self.fixture(lambda body: {'role':'assistant','content':'done'})
        src='agent worker {provider:"model",tools:[]};let s=await session_open("chat",worker);let b=session_bind(s,[]);await b(input,message_id="one")'
        with tempfile.TemporaryDirectory() as folder, Framework(settings=fixture.settings) as app:
            server=make_server(port=0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port)
            try:
                client=Client(origin);client.create_project('remote',source=src,capabilities=['session.manage'])
                receipt=client.start('remote',1,40,mode='source').result(8)
                row=app.agent_session('remote','chat');history=app.agent_session_history('remote','chat')
                self.assertEqual(client.agent_session('remote','chat'),row)
                self.assertEqual(client.agent_session_history('remote','chat'),history)
                cli=subprocess.run([sys.executable,'-m','chaos','session','history','remote','chat','--server',origin],cwd=ROOT,capture_output=True,text=True,timeout=10)
                self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout),history)
                self.assertEqual(row['lastToolBinding']['tools'],[])
                self.assertEqual(row['lastToolBinding']['kind'],'language')
                self.assertEqual(history['items'][0]['toolBinding'],row['lastToolBinding'])
                before=len(fixture.requests)
                with self.assertRaises(Exception):
                    client.start_session_turn('remote',1,'chat',row['revision'],40,message_id='one').result(8)
                self.assertEqual(len(fixture.requests),before)
                replay=client.start('remote',1,40,mode='source').result(8)
                self.assertTrue(replay['replayed']);self.assertEqual(replay['output'],receipt['output'])
                self.assertEqual(len(fixture.requests),before)
                spec=client._request('GET','/openapi.json')['components']['schemas']
                self.assertIn('SessionToolBinding',spec)
                self.assertIn('lastToolBinding',spec['AgentSessionRecord']['properties'])
            finally:
                server.shutdown();thread.join(3);server.server_close()
