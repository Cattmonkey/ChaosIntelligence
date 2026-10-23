"""Language modes share real effects, revisions, snapshots and editor services."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch
from chaos import Client, Framework, Registry, ToolSpec, ProviderSpec, ToolCall, Final
from chaos.settings import Settings
from chaos.store import Conflict
from chaos.server import make_server
from chaos.ide_language import analyze, format_source
from chaos.debugger import preview
from chaos.language import Evaluator
from chaos.program import parse_program, format_program
from chaos.modes import skill_source_definition

GOAL='''fn planner(ctx){plan([{id:"advance",call:"advance"}],"increment")}
fn advance(ctx){ctx.input+1}
fn verify(ctx){{done:ctx.results.advance>=42,state:ctx.results.advance,evidence:{observed:ctx.results.advance}}}
let objective=goal("reach 42","planner","verify",4);
await pursue(objective,{planner:planner,advance:advance,verify:verify},40,key="answer")'''

class ModesIntegration(unittest.TestCase):
    def test_goal_persisted_terminal_not_replayed_and_bundle_identity(self):
        with Framework(settings=Settings(':memory:')) as app:
            p=app.create_project('goal',source=GOAL,capabilities=[])
            first=app.start('goal',1,None,mode='source');result=first.result()
            self.assertEqual(result,{'done':True,'state':42,'evidence':{'observed':42},'iterations':2,'status':'achieved'})
            artifact=app.mode('goal','answer');self.assertEqual(artifact['status'],'completed');self.assertNotIn('lease',artifact)
            again=app.start('goal',1,None,mode='source');self.assertEqual(again.result(),result)
            self.assertFalse(any(e['type']=='mode.plan.step.started' for e in app.events('goal',run_id=again.id)['items']))
            self.assertTrue(any(e['type']=='mode.goal.verified' for e in app.events('goal',run_id=first.id)['items']))
            p=app.save_project({**p,'source':GOAL+';null'},from_view='source')
            bad=app.start('goal',p['version'],None,mode='source').wait();self.assertEqual(bad['status'],'failed');self.assertIn('source bundle',bad['error'])

    def test_cancel_pending_goal_requires_reconciliation_without_budget_reset(self):
        entered=threading.Event();seen=[];registry=Registry()
        def step(value,context):
            seen.append(value);entered.set()
            if len(seen)==1:
                while True:context.checkpoint();time.sleep(.002)
            return value+1
        registry.register_tool(ToolSpec('app.advance',step))
        source=GOAL.replace('ctx.input+1','tool("app.advance",ctx.input)')
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('resume',source=source,capabilities=['app.advance']);run=app.start('resume',1,None,mode='source')
            self.assertTrue(entered.wait(3));run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled')
            row=app.mode('resume','answer');self.assertEqual(row['status'],'needs_reconciliation');self.assertEqual(row['checkpoint']['iteration'],1)
            self.assertEqual(app.start('resume',1,None,mode='source').wait()['status'],'failed');self.assertEqual(len(seen),1)
            with self.assertRaises(Conflict):app.reconcile_goal('resume','answer',row['revision']-1,41)
            app.reconcile_goal('resume','answer',row['revision'],41,evidence={'checked':True})
            result=app.start('resume',1,None,mode='source').result();self.assertEqual(result['iterations'],2);self.assertEqual(result['state'],42);self.assertEqual(seen,[40,41])

    def test_record_agent_tools_parameters_export_and_replay_current_permission(self):
        seen=[];registry=Registry()
        registry.register_tool(ToolSpec('app.echo',lambda value,context:(seen.append(value),value)[1],version='2'))
        def provider(request,context):
            if request.history:return Final(request.history[-1]['output'])
            return ToolCall('app.echo',request.input)
        registry.register_provider(ProviderSpec('recorder',provider))
        source='''agent worker{provider:"recorder",tools:["app.echo"],maxSteps:2};
async fn action(value){worker(value)}
let recorded=await record_skill(action,{text:"original"},[{name:"message",step:0,path:["text"]}]);
await save_mode(recorded.skill,"echo");
[recorded.output,await replay_skill(recorded.skill,{message:"changed"}),skill_source(recorded.skill),mode_info(recorded.skill)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('record',source=source,capabilities=['app.echo']);output=app.start('record',1,None,mode='source').result()
            self.assertEqual(output[:2],[{'text':'original'},[{'text':'changed'}]]);self.assertEqual(seen,[{'text':'original'},{'text':'changed'}])
            source=output[2].replace('export {recorded};','')+'recorded({message:"exported"})'
            app.create_project('export',source=source,capabilities=['app.echo']);self.assertEqual(app.start('export',1,None,mode='source').result(),[{'text':'exported'}])
            capture=next(row for row in app.modes('record')['items'] if row['key'].startswith('recording_'));self.assertEqual(capture['checkpoint']['receipts'][0]['toolVersion'],'2')
            definition=app.mode('record','echo')['definition'];self.assertEqual(definition,output[3]);self.assertEqual(definition['actions'][0]['tool'],'app.echo')
            app.create_project('denied',source='let s=await load_mode("echo");await replay_skill(s,{message:"blocked"})',capabilities=[])
            app.save_mode('denied','echo',definition);self.assertEqual(app.start('denied',1,None,mode='source').wait()['status'],'failed');self.assertEqual(len(seen),3)

    def test_record_failure_never_seals_and_receipts_are_real(self):
        registry=Registry();seen=[]
        def fail(value,context):seen.append(value);raise ValueError('actual failure')
        registry.register_tool(ToolSpec('app.fail',fail))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('fail',source='fn action(x){tool("app.fail",x)};await record_skill(action,42)',capabilities=['app.fail'])
            self.assertEqual(app.start('fail',1,None,mode='source').wait()['status'],'failed')
            rows=app.modes('fail')['items'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'incomplete');self.assertEqual(seen,[42])
            self.assertEqual(rows[0]['checkpoint']['receipts'][0]['status'],'failed')

    def test_caught_tool_admission_failure_poison_recording(self):
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('admission',source='fn action(x){try{tool("memory.put",{key:"x",value:1})}catch e{null};tool("text.normalize","safe")};await record_skill(action)',capabilities=['text.normalize'])
            run=app.start('admission',1,None,mode='source').wait()
            self.assertEqual(run['status'],'failed')
            rows=app.modes('admission')['items'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'incomplete')
            self.assertFalse(any(e['type']=='tool.completed'for e in app.events('admission')['items']))

    def test_receipt_and_log_failure_cannot_mask_hard_control(self):
        from chaos.language import ResourceLimit,ExecutionControl
        from chaos.mode_store import ModeHost
        from chaos.runtime import Context
        original_event=Context.event
        def event(ctx,kind,payload):
            if kind=='tool.failed':raise ValueError('logging unavailable')
            return original_event(ctx,kind,payload)
        for exception in (ResourceLimit,ExecutionControl):
            with self.subTest(exception=exception.__name__):
                registry=Registry()
                def stop(value,context):raise exception('original hard control')
                registry.register_tool(ToolSpec('app.stop',stop))
                with Framework(registry=registry,settings=Settings(':memory:')) as app:
                    app.create_project('hard',source='fn action(x){try{tool("app.stop",x)}catch e{"caught"}};await record_skill(action,42)',capabilities=['app.stop'])
                    with patch.object(ModeHost,'tool_finish',side_effect=ValueError('receipt unavailable')),patch.object(Context,'event',event):
                        row=app.start('hard',1,None,mode='source').wait()
                    self.assertEqual(row['status'],'failed');self.assertIn('original hard control',row['error'])

    def test_ir_format_ide_and_inert_debug_values(self):
        source='let p=plan([{id:"one",call:"one"}]);fn one(ctx){42};let answer=await p(null,{one:one});answer'
        self.assertEqual(Evaluator().run(source),{'one':42})
        self.assertEqual(Evaluator().run(format_program(parse_program(source))),{'one':42})
        analysis=analyze(source);self.assertEqual(analysis['diagnostics'],[])
        self.assertEqual(next(s['type'] for s in analysis['symbols'] if s['name']=='answer'),'Record')
        value=Evaluator().run('plan([{id:"x",call:"x"}])');safe=preview(value)
        self.assertEqual(safe['type'],'Plan');self.assertNotIn('owner',json.dumps(safe));self.assertEqual(safe['definition']['kind'],'plan')
        self.assertEqual(skill_source_definition({'kind':'skill','actions':[{'tool':'app.echo','input':{'text':'x";throw "bad'}}],'parameters':[]}).count('export'),1)

    def test_http_sdk_modes_cas_history_and_source(self):
        with tempfile.TemporaryDirectory() as folder,Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:'+str(server.server_port);client=Client(url);p=client.create_project('api_modes',source='42',capabilities=[])
                definition={'kind':'plan','name':'review','steps':[{'id':'one','call':'worker','needs':[]}]}
                row=client.save_mode('api_modes','draft',definition);self.assertEqual(row['revision'],1)
                self.assertEqual(client.mode('api_modes','draft'),app.mode('api_modes','draft'))
                self.assertEqual(len(client.modes('api_modes')['items']),1)
                row=client.save_mode('api_modes','draft',{**definition,'name':'approved'},revision=row['revision']);self.assertEqual(row['revision'],2)
                self.assertEqual(len(client.mode_history('api_modes','draft')['items']),2)
                self.assertEqual(client.mode('api_modes','draft',revision=1)['definition']['name'],'review')
                skill={'kind':'skill','actions':[{'tool':'memory.get','input':'key'}],'parameters':[]};client.save_mode('api_modes','skill',skill)
                with urllib.request.urlopen(url+'/api/v1/projects/api_modes/modes/skill/source') as response:source=json.load(response)['source']
                self.assertEqual(source,skill_source_definition(skill))
                with urllib.request.urlopen(url+'/api/v1/openapi.json') as response:spec=json.load(response)
                self.assertIn('/projects/{projectId}/modes/{key}/reconcile',spec['paths'])
            finally:server.shutdown();thread.join(3);server.server_close()

if __name__=='__main__':unittest.main()
