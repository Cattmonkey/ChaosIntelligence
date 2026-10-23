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
    return {p.name:p.read_text() for p in (ROOT/'examples/function-team-dot').glob('*.chaos')}

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

class TeamFunctionsIntegration(unittest.TestCase):
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
                first_binding=app.agent_session('dot','team_programmer')['lastToolBinding']
                self.assertEqual(first_binding['sourceVersion'],1)
                self.assertEqual(first_binding['runId'],first['lastRunId'])
                events=app.events('dot',limit=300,run_id=first['lastRunId'])['items']
                starts=[e['payload'] for e in events if e['type']=='team.role.started']
                self.assertEqual({e['roleId'] for e in starts},{'programmer','reviewer','check'})
                self.assertEqual(len(starts),3)
                self.assertEqual({e['implementationKind'] for e in starts},{'BoundSession','Function'})
                self.assertEqual(len({e['invocationId'] for e in starts}),3)
                role_effects=[e for e in events if e['payload'].get('team')]
                self.assertTrue(role_effects)
                self.assertTrue(any(e['payload']['team']['roleId']=='check' and e['type']=='program.probe.completed' for e in role_effects))
                app._engine.pause_dispatch(True)
                app.send_process('dot','dot',None,message_id='second')
            with Framework(database,settings=fixture.settings) as app:
                second=wait_for(app,'dot',2)
                self.assertEqual(second['state']['generation'],2)
                self.assertEqual(second['output']['before'],42)
                self.assertEqual(second['lastEvolution']['version'],3)
                for key,name in [('team_programmer','program_snapshot'),('team_reviewer','check_candidate')]:
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

    def test_public_source_sdk_workspace_cli_and_real_role_events_agree(self):
        from chaos import Settings
        from unittest.mock import patch
        definition={'kind':'team','name':'public callable team','entry':'add','roles':[
            {'id':'add','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='let policy=team_from('+json.dumps(definition)+');fn add(value:Int,*,offset:Int=2)->Int{value+offset};let crew=team_bind(policy,{add:add});let members=team_bindings(crew);let cold=members.add(input,offset=2);[type_of(cold),await cold,team_binding_info(crew)]'
        settings=Settings(':memory:')
        with tempfile.TemporaryDirectory()as folder,patch.object(settings,'client',side_effect=AssertionError('Pure function role requested a model')),Framework(settings=settings)as app:
            server=make_server(port=0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port)
            try:
                client=Client(origin);client.create_project('remote',source=source,capabilities=[])
                run=client.start('remote',1,40,mode='source');result=run.result(8)
                self.assertEqual(result[:2],['Task',42])
                self.assertEqual(result[2]['team'],definition)
                self.assertEqual(result[2]['bindings'],{'add':{'kind':'Function'}})
                self.assertEqual([e for e in client.events('remote',run_id=run.id)['items'] if e['type'].startswith('team.role.')],[e for e in app.events('remote',run_id=run.id)['items'] if e['type'].startswith('team.role.')])
                events=client.events('remote',run_id=run.id)['items']
                self.assertEqual([e['type'] for e in events if e['type'].startswith('team.role.')],['team.role.started','team.role.completed'])
                checkout=Path(folder)/'workspace'
                command=[sys.executable,'-m','chaos','workspace','checkout',str(checkout),'--server',origin,'--project','remote']
                checked=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=10)
                self.assertEqual(checked.returncode,0,checked.stderr)
                cli=subprocess.run([sys.executable,'-m','chaos','workspace','run',str(checkout),'--server',origin,'--input-json','40'],cwd=ROOT,capture_output=True,text=True,timeout=10)
                self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout),result)
                formatted=client.format_source(source)
                self.assertEqual(Framework.evaluate(formatted,{'input':40}),result)
                self.assertEqual(client.analyze(source),Framework.analyze(source))
            finally:server.shutdown();thread.join(3);server.server_close()
