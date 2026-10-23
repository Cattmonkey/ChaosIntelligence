"""Public persistent dot evolution across model proposals, restart, SDK/HTTP/CLI and fixtures."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, ProviderSpec, Registry, Settings
from chaos.program_values import canonical_hash
from chaos.server import make_server

ROOT=Path(__file__).resolve().parents[1]

def files():
    return {p.name:p.read_text() for p in (ROOT/'examples/evolving-dot').glob('*.chaos')}

def process_definition():
    return {'kind':'process','name':'atomic dot','module':'dot.chaos','export':'step','intervalSeconds':None,'maxTurns':10}

def wait_for(app,project,turn,status='waiting'):
    end=time.monotonic()+8
    while time.monotonic()<end:
        record=app.get_process(project,'dot')
        if record['turn']==turn and record['status']==status:return record
        if record['status']=='needs_reconciliation':raise AssertionError(record)
        time.sleep(.01)
    raise AssertionError(record)

def registry(calls):
    result=Registry()
    def respond(request,context):
        context.checkpoint();calls.append((request.name,copy.deepcopy(request.input)))
        if request.name=='reviewer':return Final({'approve':True,'reason':'explicit review fixture'})
        if request.name!='programmer':raise AssertionError(request.name)
        candidate=copy.deepcopy(request.input['definition'])
        candidate['files']['behavior.chaos']='fn answer(value:Int)->Int{value+'+str(request.input['increment'])+'};export {answer};'
        return Final(candidate)
    result.register_provider(ProviderSpec('app.evolution',respond))
    return result

class ProcessEvolutionIntegration(unittest.TestCase):
    def settings(self):
        settings=Settings(':memory:')
        guard=patch.object(settings,'client',side_effect=AssertionError('Unexpected production model request'))
        guard.start();self.addCleanup(guard.stop)
        return settings

    def create(self,app,project='dot'):
        modules=files()
        app.create_project(project,source=modules['main.chaos'],files=modules,capabilities=['process.manage','program.adopt'])
        app.start(project,1,{'provider':'app.evolution','intervalSeconds':None},mode='source').result(8)
        return wait_for(app,project,1)

    def test_actual_multirole_dot_two_adoptions_three_turns_and_two_restarts(self):
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'workspace.sqlite'
            with Framework(database,registry=registry(calls),settings=self.settings()) as app:
                first=self.create(app)
                self.assertEqual(first['state']['generation'],1)
                self.assertEqual(first['output'],{'before':41,'sameFunction':41,'executedVersion':1})
                self.assertEqual(first['lastVersion'],1)
                self.assertEqual(first['lastEvolution']['version'],2)
                self.assertEqual(first['lastEvolution']['runId'],first['lastRunId'])
                self.assertEqual(app.get_project('dot')['version'],2)
                self.assertEqual(app.get_run(first['lastRunId'])['snapshot']['version'],1)
                app._engine.pause_dispatch(True)
                app.send_process('dot','dot',{'continue':True},message_id='generation-two')
            with Framework(database,registry=registry(calls),settings=self.settings()) as app:
                second=wait_for(app,'dot',2)
                self.assertEqual(second['state']['generation'],2)
                self.assertEqual(second['output'],{'before':42,'sameFunction':42,'executedVersion':2})
                self.assertEqual(second['lastEvolution']['version'],3)
                self.assertEqual(app.send_process('dot','dot',{'continue':True},message_id='generation-two')['status'],'consumed')
                app._engine.pause_dispatch(True)
                app.send_process('dot','dot',None,message_id='observe-final')
            with Framework(database,registry=registry(calls),settings=self.settings()) as app:
                done=wait_for(app,'dot',3,'completed')
                self.assertEqual(done['output'],{'value':43,'version':3})
                self.assertEqual(done['lastVersion'],3)
                self.assertEqual(done['lastEvolution'],second['lastEvolution'])
                self.assertEqual(app.get_project('dot')['version'],3)
                self.assertEqual([row['kind'] for row in app.program_history('dot')['items']],['created','adopted','adopted'])
                adopted=[e for e in app.events('dot',limit=300)['items'] if e['type']=='program.adopted']
                self.assertEqual([e['payload']['version'] for e in adopted],[2,3])
                self.assertEqual([app.get_run(e['run_id'])['version'] for e in adopted],[1,2])
                self.assertEqual(app.send_process('dot','dot',None,message_id='observe-final')['status'],'consumed')
        self.assertEqual([name for name,_ in calls],['programmer','reviewer','programmer','reviewer'])

    def test_http_cli_source_and_receipt_are_the_same_persisted_result(self):
        with tempfile.TemporaryDirectory() as folder, Framework(registry=registry([]),settings=self.settings()) as app:
            server=make_server(port=0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port)
            try:
                client=Client(origin);modules=files()
                client.create_project('remote',source=modules['main.chaos'],files=modules,capabilities=['process.manage','program.adopt'])
                client.start('remote',1,{'provider':'app.evolution','intervalSeconds':None},mode='source').result(8)
                expected=wait_for(app,'remote',1)
                self.assertEqual(client.get_process('remote','dot'),expected)
                self.assertEqual(client.processes('remote')['items'],[expected])
                cli=subprocess.run([sys.executable,'-m','chaos','process','get','remote','dot','--server',origin],cwd=ROOT,capture_output=True,text=True,timeout=10)
                self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout),expected)
                snapshot=client.program_snapshot('remote')
                self.assertEqual(snapshot['version'],expected['lastEvolution']['version'])
                self.assertEqual(snapshot['definitionHash'],expected['lastEvolution']['definitionHash'])
                self.assertEqual(client.program('remote'),app.program('remote'))
                analysis=client.analyze(modules['dot.chaos'],file='dot.chaos',files=modules)
                self.assertEqual(analysis,Framework.analyze(modules['dot.chaos'],file='dot.chaos',files=modules))
                self.assertTrue(analysis['complete']);self.assertEqual(analysis['diagnostics'],[])
                schema=client._request('GET','/openapi.json')['components']['schemas']['ProcessRecord']
                self.assertIn('lastEvolution',schema['properties']);self.assertNotIn('lastEvolution',schema['required'])
            finally:
                server.shutdown();thread.join(3);server.server_close()

    def test_atomic_evolution_runs_real_agent_tool_fixture_before_adoption(self):
        fixture={'agents':[{'definition':{'kind':'agent','name':'worker','config':{'provider':'model','tools':[]}},'input':40,
            'actions':[{'tool':'answer','input':40,'expect':42},{'final':'accepted'}]}]}
        cases=[{'entry':'tests.chaos','input':40,'expect':'accepted','fixtures':fixture}]
        module='''fn step(c){let base=current_program();let d=program_info(base);
let candidate=program_from(merge(d,{files:set(d.files,"behavior.chaos","fn answer(x:Int)->Int{x+2};export {answer};")}));
process_evolve(base,candidate,c.state.cases,{generation:1},"checked",true)};export {step}'''
        test='''import "behavior.chaos" as B;agent worker{provider:"model",tools:[]};
let bound=agent_bind(worker,[agent_tool("answer",B.answer,input_type="Int",output_type="Int")]);await bound(input)'''
        with Framework(settings=self.settings()) as app:
            app.create_project('fixture',source='null',files={'dot.chaos':module,'tests.chaos':test,'behavior.chaos':'fn answer(x){x+1};export {answer};'},capabilities=['process.manage','program.adopt'])
            app.create_process('fixture',1,'dot',process_definition(),state={'cases':cases})
            record=wait_for(app,'fixture',1,'completed')
            self.assertEqual(record['state'],{'generation':1});self.assertEqual(record['output'],'checked')
            self.assertEqual(record['lastEvolution']['casesHash'],canonical_hash(cases))
            self.assertEqual(record['lastEvolution']['testCount'],1)
            self.assertEqual(app.get_project('fixture')['version'],2)
            self.assertFalse(any(e['type']=='tool.started' for e in app.events('fixture',limit=300)['items']))
