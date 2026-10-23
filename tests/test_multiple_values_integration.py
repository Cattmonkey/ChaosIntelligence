"""Actual tools, Team Tasks and public interfaces consume one shared completion."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from chaos import Client, Final, Framework, Registry, Settings, ToolCall
from chaos.program import parse_program
from chaos.server import make_server
from tests.test_multiple_values_surfaces import SOURCE, EXPECTED

TEAM={'kind':'team','name':'multiple crew','entry':'work','roles':[{'id':'work','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}


class MultipleValueIntegration(unittest.TestCase):
    def test_http_source_graph_ir_edit_snapshot_and_primary_json(self):
        with tempfile.TemporaryDirectory()as directory,Framework(settings=Settings(':memory:'))as app:
            root=Path(directory);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('multiple-http',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None,mode='source');self.assertEqual(old.result(5),EXPECTED)
                self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(5),EXPECTED)
                ir=client.program(project['id'])['program'];target=ir['root']['statements'][2]['value']['arguments'][0]
                saved=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':42}])
                self.assertEqual(client.start(project['id'],saved['version'],None,mode='graph').result(5),[43,EXPECTED[1]])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(saved['source']),Framework.analyze(saved['source']))
                self.assertEqual(parse_program(client.format_source(SOURCE))['semanticHash'],parse_program(SOURCE)['semanticHash'])
                p=client.create_project('primary-json',source='return_values(42,{proof:true})',capabilities=[])
                self.assertEqual(client.start(p['id'],p['version'],None).result(5),42)
                p=client.create_project('full-json',source='multiple_value_list(fn(){return_values(42,{proof:true})})',capabilities=[])
                self.assertEqual(client.start(p['id'],p['version'],None).result(5),[42,{'proof':True}])
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_real_agent_tool_primary_contract_and_explicit_capture(self):
        outputs=[];registry=Registry()
        @registry.provider('choose')
        def choose(request,context):
            context.checkpoint()
            if request.step==1:return ToolCall('work',request.input)
            value=request.history[-1]['output'];outputs.append(value);return Final(value)
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            for name,handler,contract,expected in [('primary','fn(x:Int)->Int{return_values(x+1,"secondary")}','Int',42),
                                                   ('capture','fn(x:Int)->List{multiple_value_list(fn(){return_values(x+1,"secondary")})}','List',[42,'secondary'])]:
                source='agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("work",'+handler+',input_type="Int",output_type="'+contract+'")]);await bound(41)'
                project=app.create_project(name,source=source,capabilities=[])
                run=app.start(project['id'],project['version'],None,mode='source')
                self.assertEqual(run.result(6),expected)
                self.assertTrue(any(event['type']=='agent.completed' for event in app.events(project['id'],run_id=run.id)['items']))
        self.assertEqual(outputs,[42,[42,'secondary']])

    def test_sync_and_async_team_task_capture_runs_once_and_keeps_event_identity(self):
        for flavor in ('fn','async fn'):
            with self.subTest(flavor=flavor),Framework(settings=Settings(':memory:'))as app:
                source='var calls=0;'+flavor+' work(value:Int)->Int{calls+=1;return_values(value+1,{proof:"team"})};let crew=team_bind(team_from('+json.dumps(TEAM)+'),{work:work});let cold=crew(41);let before=calls;let first=multiple_value_list(async fn(){await cold});let again=multiple_value_list(async fn(){await cold});[before,first,again,calls]'
                project=app.create_project('team-'+flavor.replace(' ','-'),source=source,capabilities=[]);run=app.start(project['id'],project['version'],None)
                self.assertEqual(run.result(6),[0,[42,{'proof':'team'}],[42,{'proof':'team'}],1])
                events=[e for e in app.events(project['id'],run_id=run.id)['items'] if e['type'].startswith('team.role.')]
                self.assertEqual([e['type'] for e in events],['team.role.started','team.role.completed'])
                self.assertEqual(len({e['payload']['invocationId'] for e in events}),1)
                self.assertEqual({e['payload']['implementationKind'] for e in events},{'Function'})
                self.assertNotIn('MultipleValues',json.dumps(events))

    def test_compiler_modes_preserve_full_result_cleanup_and_effect_order(self):
        for target in ('interpreter','auto','auto-flow'):
            source='var events=[];fn report(value:Int)->Int{try{events=append(events,"body");return_values(value,"proof")}finally{events=append(events,"cleanup")}};let fast=compile_function(report,target="'+target+'");let captured=multiple_value_list(fn(){fast(42)});[captured,events]'
            with self.subTest(target=target):self.assertEqual(Framework.evaluate(source),[[42,'proof'],['body','cleanup']])

    def test_public_completed_task_and_both_generator_terminals_without_replay(self):
        source='''var calls=0;
async fn work()->Int{calls+=1;return_values(20,"task")}
fn* stream()->Int{calls+=1;yield 7;return_values(22,"generator")}
async fn* async_stream()->Int{calls+=1;yield 8;return_values(0,"async-generator")}
let t=work();let task_primary=await t;
let g=stream();let first=next(g);let done=next(g);
let a=async_stream();let async_first=await anext(a);let async_done=await anext(a);
let joined=multiple_value_list(fn(){multiple_value_call(fn(*items){return_values(length(items),items[1],items[3],items[5])},t,g,a)});
let bound=multiple_value_list(fn(){multiple_value_bind(3,g,fn(x,tag,missing){return_values(x,tag,missing)})});
let kept=multiple_value_list(fn(){multiple_value_prog1(t,g,a)});
[multiple_value_list(t),multiple_value_list(g),multiple_value_list(a),multiple_value_list(t),joined,bound,kept,nth_value(1,g),calls,task_primary,done.value,async_done.value]'''
        self.assertEqual(Framework.evaluate(source),[[20,'task'],[22,'generator'],[0,'async-generator'],[20,'task'],[6,'task','generator','async-generator'],[22,'generator',None],[20,'task'],'generator',3,20,22,0])

    def test_receiver_rejects_uncompleted_failed_and_closed_handles_without_advancing(self):
        cases=[('async fn work(){42};let value=work();','created task'),
               ('fn* work(){yield 1;return_values(42,"done")};let value=work();','created generator'),
               ('fn* work(){yield 1;return_values(42,"done")};let value=work();next(value);','suspended generator'),
               ('fn* work(){yield 1;return_values(42,"done")};let value=work();close(value);','closed generator'),
               ('async fn work(){throw "failed"};let value=work();try{await value}catch e{null};','failed task')]
        for setup,label in cases:
            with self.subTest(label=label):
                source=setup+'try{multiple_value_list(value)}catch error{error.code}'
                self.assertEqual(Framework.evaluate(source),'multiple_value_state_error')

    def test_debugger_locals_are_ordinary_values_before_and_after_capture(self):
        source='\n'.join(['let primary=return_values(42,"hidden");','let captured=multiple_value_list(fn(){return_values(42,"shown")});','[primary,captured]'])
        with Framework(settings=Settings(':memory:'))as app:
            project=app.create_project('debug-multiple',source=source,capabilities=[])
            run=app.start_debug(project['id'],project['version'],pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
            deadline=time.monotonic()+5
            while True:
                state=run.debugger()
                if state['status']=='paused':break
                self.assertEqual(state['status'],'running',state);self.assertLess(time.monotonic(),deadline);time.sleep(.005)
            locals_={v['name']:v for frame in state['frames'] for v in frame['locals']}
            self.assertEqual(locals_['primary']['value'],42)
            self.assertEqual(locals_['primary']['type'],'int')
            self.assertEqual(locals_['captured']['value'],[42,'shown'])
            self.assertNotIn('MultipleValues',json.dumps(state))
            run.debug_command('continue',pause_id=state['pauseId'])
            self.assertEqual(run.result(5),[42,[42,'shown']])

