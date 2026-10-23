"""Real program probes, Process restart, CLI and source-derived authoring contracts."""
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
from chaos.ide_language import analyze, format_source
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.modules import ProgramBundle, STDLIB
from chaos.server import make_server

ROOT=Path(__file__).resolve().parents[1]
EXAMPLES=ROOT/'examples/harness-evolution'

def modules(): return {p.name:p.read_text() for p in EXAMPLES.glob('*.chaos')}
def wait(app, turn):
    end=time.monotonic()+10
    while time.monotonic()<end:
        p=app.get_process('evolution','harness-learner')
        if p['turn']==turn and p['status']=='waiting': return p
        if p['status']=='needs_reconciliation': raise AssertionError(p)
        time.sleep(.01)
    raise AssertionError(p)

class EvolutionSurfaces(unittest.TestCase):
    def test_strict_json_copy_has_no_handle_projection_and_charges_budget(self):
        e=Evaluator()
        self.assertEqual(e.run('import "std/json" as J;J.copy({x:[42],tag:{"$agent":"inert"}})'),{'x':[42],'tag':{'$agent':'inert'}})
        for expr in ['fn(x){x}', 'current_program()', '(async fn(){1})()', 'tuple([1])']:
            with self.subTest(expr=expr), self.assertRaises(LanguageError):
                Evaluator().run('json_copy({nested:['+expr+']})')
        with self.assertRaises(ResourceLimit):
            Evaluator(step_limit=100).run('json_copy(input)',{'input':list(range(1000))})
        for value in (float('nan'), float('inf')):
            with self.assertRaises(LanguageError): Evaluator().run('json_copy(input)',{'input':value})
        shared={'a':[1]}; out=Evaluator().run('json_copy(input)',{'input':shared});out['a'].append(2)
        self.assertEqual(shared,{'a':[1]})

    def test_standard_module_authoring_and_exact_resource(self):
        self.assertEqual(STDLIB['std/evolve'],(ROOT/'chaos/stdlib/evolve.chaos').read_text())
        source='import "std/evolve" as E; E.create({instruction:"solve"})'
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('must not execute')):
            self.assertEqual(analyze(source)['diagnostics'],[])
            labels={c['label'] for c in analyze('import "std/evolve" as E;E.')['completions']}
            self.assertTrue({'create','run'}<=labels)
            signature=analyze('import "std/evolve" as E;E.run(')['signatureHelp']
            self.assertEqual([p['name'] for p in signature['parameters']],['snapshot','task','operators','mode','k','observe'])
            formatted=format_source(source)
        self.assertEqual(Framework.evaluate(source),Framework.evaluate(formatted))
        self.assertIn('std/evolve',ProgramBundle(source).compilation_units)

    def test_actual_cli_stream_has_all_probes_and_preserves_semantics(self):
        command=[sys.executable,'-m','chaos','run',str(EXAMPLES/'main.chaos'),'--stream']
        ran=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=20)
        self.assertEqual(ran.returncode,0,ran.stderr)
        rows=[json.loads(line) for line in ran.stdout.splitlines()]
        result=rows[-1]['output']
        self.assertEqual(rows[-1]['type'],'run.result')
        self.assertEqual(len(result['records']),4)
        self.assertEqual(result['records'][0]['output']['batches'],8)
        self.assertEqual(result['selected']['output'],{'squares':[1,4,9,16,25,36,49,64],'batches':2})
        self.assertEqual(result['snapshot']['base']['blockSize'],1)
        phases=[e['payload']['data']['phase'] for e in rows if e.get('type')=='bot.progress']
        self.assertEqual(phases[0],'reference.started'); self.assertEqual(phases[-1],'completed')

    def test_real_process_checkpoint_http_observation_and_restart(self):
        files=modules()
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'workspace.sqlite'
            with Framework(db,settings=Settings(':memory:')) as app:
                app.create_project('evolution',source=files['main.chaos'],files=files,capabilities=['process.manage'])
                app.start('evolution',1,{'persistent':True},mode='source').result(10)
                first=wait(app,1)
                self.assertEqual(first['state']['snapshot']['revision'],1)
                self.assertEqual(first['output']['executions'],4)
                self.assertEqual(len(first['output']['records']),4)
                app._engine.pause_dispatch(True)
                app.send_process('evolution','harness-learner',[3,5,7],message_id='next-task')
            with Framework(db,settings=Settings(':memory:')) as app:
                second=wait(app,2)
                self.assertEqual(second['state']['snapshot']['revision'],2)
                self.assertEqual(second['state']['snapshot']['base'],first['state']['snapshot']['base'])
                self.assertEqual(len(second['state']['snapshot']['lessons']),2)
                self.assertEqual(second['output']['selected']['output']['squares'],[9,25,49])
                self.assertEqual(second['output']['records'][0]['harness']['blockSize'],1)
                self.assertEqual(app.get_project('evolution')['version'],1)
                server=make_server(port=0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
                t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
                try:
                    client=Client('http://127.0.0.1:'+str(server.server_port))
                    events=[];cursor=0
                    while True:
                        page=client.events('evolution',run_id=second['lastRunId'],limit=300,after=cursor)
                        events.extend(page['items']);cursor=page.get('nextCursor')
                        if cursor is None: break
                    progress=[e for e in events if e['type']=='bot.progress']
                    self.assertEqual(progress[-1]['payload']['data']['phase'],'completed')
                    self.assertEqual(progress[-1]['payload']['data']['revision'],2)
                finally: server.shutdown();server.server_close();t.join(3)

    def test_real_model_adapter_uses_registered_provider_and_existing_roles(self):
        calls=[]; registry=Registry()
        def model(request,context):
            context.checkpoint(); calls.append((request.name,request.input))
            if request.name=='evolution_worker': return Final('observed answer for '+str(request.input))
            if request.name in ('evolution_proposer','evolution_composer'):
                return Final({'harness':{'instruction':'State evidence and assumptions.'},'reason':'test fixture proposal'})
            if request.name=='evolution_selector': return Final(0)
            if request.name=='evolution_learner':
                refs=[request.input['records'][0]['id'],request.input['selected']]
                return Final({'strategy':{'method':'inspect evidence'},'lessons':[{'cue':'bounded task','lesson':'Evidence is task-local.','evidence':refs}],'evidence':refs})
            raise AssertionError(request.name)
        registry.register_provider(ProviderSpec('app.fixture',model))
        files=modules();files['model.chaos']=files['model.chaos'].replace('provider:"model"','provider:"app.fixture"')
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('model',source=files['main.chaos'],files=files,capabilities=[])
            result=app.start('model',1,{'provider':'model','task':'check observations'},mode='source').result(10)
            self.assertEqual(result['selected']['output'],'observed answer for check observations')
            self.assertEqual([name for name,_ in calls].count('evolution_worker'),4)
            self.assertEqual(result['snapshot']['strategy'],{'method':'inspect evidence'})

    def test_oversized_persistent_report_leaves_old_checkpoint(self):
        payload = json.dumps('x'*7600)
        source = 'import "std/evolve" as E; let large=' + payload + ';' + '''
        fn propose(c){ {harness:1,reason:"observe"} }
        fn probe(c){ {output:large,evidence:null} }
        fn select(c){0}
        fn learn(c){ {strategy:c.strategy,lessons:[],evidence:[]} }
        async fn step(ctx){
          let result=await E.run(ctx.state,null,{propose:propose,probe:probe,compose:propose,select:select,learn:learn},k=2);
          process_step(result.snapshot,result)
        }; export {step};
        '''
        state={'base':0,'strategy':None,'lessons':[],'revision':0}
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('evolution',source=source,capabilities=['process.manage'])
            app.create_process('evolution',1,'harness-learner',{'kind':'process','name':'bounded learner','module':'main.chaos','export':'step','intervalSeconds':None,'maxTurns':2},state=state)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                record=app.get_process('evolution','harness-learner')
                if record['status']=='needs_reconciliation':break
                time.sleep(.01)
            self.assertEqual(record['status'],'needs_reconciliation')
            self.assertEqual(record['state'],state)
            self.assertEqual(record['turn'],0)
            self.assertEqual(app.get_project('evolution')['version'],1)

    def test_prefixed_example_entry_finds_process_module(self):
        files={'nested/'+name:source for name,source in modules().items()}
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('evolution',source=files['nested/main.chaos'],files=files,entry='nested/main.chaos',capabilities=['process.manage'])
            app.start('evolution',1,{'persistent':True},mode='source').result(10)
            state=wait(app,1)
            self.assertEqual(state['output']['executions'],4)
            self.assertEqual(state['state']['snapshot']['revision'],1)
