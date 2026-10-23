"""Native robots compose in original language, tooling and public runtime paths."""
import contextlib
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, RobotSpec, Settings, ToolCall, compile_program, load_robot_registry
from chaos.code_presentation import render_code
from chaos.documentation import Documentation
from chaos.extensions import DEFAULT_CAPABILITIES, language_permissions
from chaos.ide_language import analyze
from chaos.language import Evaluator, LanguageError, Native, Parser
from chaos.modules import ProgramBundle, STDLIB
from chaos.native_specs import SHAPES, all_specs, call_spec_for, member_specs_for, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.suspension import Scheduler

ROOT=Path(__file__).resolve().parents[1]
NAMES=('robot_open','robot_info','robot_observe','robot_observation_info','robot_command','robot_command_info','robot_act','robot_stop','robot_close','robot_receipt_info')
ACTUATORS={name:{'minimum':-1,'maximum':1,'unit':'normalized'}for name in ('left','right')}
SOURCE='''// 🙂 native observations feed an ordinary policy
import "std/robot" as R;
fn policy(observation:RobotObservation)->Record {
 let sonar=R.robot_observation_info(observation).readings.sonar;
 if sonar.unit=="cm" && sonar.value!=null && sonar.value>=30 {
  {left:0.2,right:0.4}
 } else { {left:0,right:0} }
};
async with await R.robot_open("mobile") as robot {
 let observation=await R.robot_observe(robot);
 let command=R.robot_command(observation,policy(observation));
 let receipt=R.robot_receipt_info(await robot(command));
 let stopped=R.robot_receipt_info(await R.robot_stop(robot));
 {channels:R.robot_command_info(command).channels,status:receipt.status,stop:stopped.status,
  used:R.robot_command_info(command).used,targetVerified:receipt.targetVerified}
}'''
EXPECTED={'channels':{'left':0.2,'right':0.4},'status':'sent','stop':'stopSent','used':True,'targetVerified':False}


def nodes(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from nodes(child)
    elif isinstance(value,list):
        for child in value:yield from nodes(child)


class Adapter:
    def __init__(self):self.operations=[];self.sequence=0;self.closed=False;self.close_count=0
    def handle(self,operation,payload,context):
        context.checkpoint();self.operations.append((operation,json.loads(json.dumps(payload))))
        if operation=='observe':
            self.sequence+=1
            return {'sequence':self.sequence,'receivedAtNs':time.monotonic_ns(),'readings':{'sonar':{'value':80,'unit':'cm'}}}
        if operation=='act':
            if payload['expiresAtNs']<=time.monotonic_ns():raise AssertionError('host admitted an expired command')
            return {'status':'sent'}
        if operation=='stop':return {'status':'stopSent'}
        raise AssertionError(operation)
    def close(self):self.closed=True;self.close_count+=1
    @property
    def actions(self):return [payload for operation,payload in self.operations if operation=='act']


def registered(adapters=None,actuators=None):
    adapters=[]if adapters is None else adapters
    def factory(context):
        context.checkpoint();adapter=Adapter();adapters.append(adapter);return adapter
    registry=Registry();registry.register_robot(RobotSpec('mobile',factory,actuators or ACTUATORS,profile='fixture/robot',description='Deterministic registered robot',resource_id='/private/robot/endpoint'))
    return registry,adapters


class RobotSurfaces(unittest.TestCase):
    def run_source(self,source=SOURCE,*,actuators=None,input=None,mode='source',capabilities=None):
        registry,adapters=registered(actuators=actuators)
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            p=app.create_project('robot',source=source,capabilities=['robot.control']if capabilities is None else capabilities)
            run=app.start(p['id'],p['version'],input,mode=mode);value=run.result(8);events=app.events(p['id'],run_id=run.id)['items']
        return value,adapters,events

    def test_exact_native_signatures_shapes_defaults_and_receiver(self):
        env=Evaluator().env.values
        declared={'robot_open':['name'],'robot_info':['robot'],'robot_observe':['robot','timeout_ms'],'robot_observation_info':['observation'],'robot_command':['observation','channels','ttl_ms'],'robot_command_info':['command'],'robot_act':['robot','command'],'robot_stop':['robot'],'robot_close':['robot'],'robot_receipt_info':['receipt']}
        for name in NAMES:
            with self.subTest(name=name):
                actual=list(inspect.signature(env[name].call).parameters.values());spec=spec_for(name)
                self.assertEqual(env[name].spec_id,name);self.assertEqual([p.name for p in actual],declared[name]);self.assertEqual([p.name for p in spec.parameters],declared[name])
                self.assertEqual([p.default is not p.empty for p in actual],[p.optional for p in spec.parameters])
                self.assertEqual([p['name']for p in analyze(name+'(')['signatureHelp']['parameters']],declared[name])
        self.assertEqual(spec_for('robot_observe').parameters[-1].default_display,'1000');self.assertEqual(spec_for('robot_command').parameters[-1].default_display,'250')
        expected={'RobotInfo':{'id','name','generation','profile','status','actuators','maxObservationAgeMs','latestObservationId'},'RobotObservationInfo':{'id','robotId','generation','sequence','receivedAtNs','readings'},'RobotCommandInfo':{'id','robotId','generation','observationId','channels','ttlMs','used'},'RobotReceiptInfo':{'schema','id','robotId','name','generation','operation','status','targetVerified','commandId','observationId'}}
        for shape,fields in expected.items():self.assertEqual({f.name for f in SHAPES[shape]},fields)
        receiver=call_spec_for('Robot');self.assertEqual(receiver.awaited_type,spec_for('robot_act').awaited_type);self.assertEqual([p.name for p in receiver.parameters],['command']);self.assertFalse(receiver.compatibility_export);self.assertNotIn('__call__',member_specs_for('Robot'))
        self.assertEqual(len([v for v in env.values()if type(v)is Native]),335);self.assertEqual(len(all_specs()),381)

    def test_inert_types_context_await_infos_aliases_and_shadowing(self):
        source='let robot=await robot_open("mobile");let packet=await robot_observe(robot);let command=robot_command(packet,{left:0,right:0});let cold=robot(command);let receipt=await cold;let info=robot_receipt_info(receipt);let held=async with await robot_open("mobile") as bound{bound};'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed robot analysis')),patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('invoked robot analysis')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('advanced robot task')):
            result=analyze(source);self.assertEqual(result['diagnostics'],[]);symbols={r['name']:r['type']for r in result['symbols']}
            self.assertEqual([symbols[n]for n in ('robot','packet','command','cold','receipt','held','bound')],['Robot','RobotObservation','RobotCommand','Task','RobotReceipt','Robot','Robot'])
            for function,typ,shape in [('robot_info','Robot','RobotInfo'),('robot_observation_info','RobotObservation','RobotObservationInfo'),('robot_command_info','RobotCommand','RobotCommandInfo'),('robot_receipt_info','RobotReceipt','RobotReceiptInfo')]:
                self.assertEqual({r['label']for r in analyze('fn read(value:'+typ+'){'+function+'(value).')['completions']},{f.name for f in SHAPES[shape]})
            self.assertEqual(analyze('fn use(robot:Robot){robot(')['signatureHelp']['parameters'][0]['type'],'RobotCommand')
            self.assertEqual(analyze('fn accept(value:Function){null};fn use(robot:Robot){accept(robot)}')['diagnostics'],[])
            self.assertEqual(analyze('let open=robot_open;let value=await open("mobile")')['symbols'][-1]['type'],'Robot')
            self.assertEqual(analyze('fn robot_open(name)->String{name};let value=robot_open("local")')['symbols'][-1]['type'],'String')
            self.assertEqual(analyze('var open=robot_open;let value=await open("mobile")')['symbols'][-1]['type'],'Any')
            for wrong in ['robot_command(null,{})','robot_observe(null,"slow")','robot_receipt_info(42)']:self.assertTrue(analyze(wrong)['diagnostics'])
            self.assertIn('call',compile_program(SOURCE)['summary']['effects']);formatted=format_source(SOURCE);self.assertIn('chaos-token-function',render_code(formatted,'chaos'))
        self.assertEqual(self.run_source(formatted)[0],EXPECTED)

    def test_original_ir_dialects_format_and_registered_policy_example(self):
        for version in (1,2):
            ir=parse_program(SOURCE,language_version=version);formatted=format_program(ir)
            self.assertEqual(plain(decode_program(ir)),plain(Parser(formatted,legacy_items=version==1).program()));self.assertEqual(parse_program(formatted,language_version=version)['semanticHash'],ir['semanticHash'])
            value,adapters,_=self.run_source(formatted);self.assertEqual(value,EXPECTED);self.assertEqual(len(adapters[0].actions),1);self.assertTrue(adapters[0].closed)
        value,adapters,_=self.run_source((ROOT/'examples/native-robot.chaos').read_text());self.assertEqual(value['receipt']['status'],'sent');self.assertFalse(value['receipt']['targetVerified']);self.assertEqual(adapters[0].actions[0]['channels'],{'left':0.2,'right':0.2})

    def test_profiles_channels_are_not_fixed_wheel_rules(self):
        actuators={'pan':{'minimum':-90,'maximum':90,'unit':'degrees'}}
        source='async with await robot_open("mobile") as r{let p=await robot_observe(r);let c=robot_command(p,{pan:42});let receipt=await apply(r,[c]);[robot_info(r).actuators,robot_receipt_info(receipt).status,robot_command_info(c).channels]}'
        value,adapters,_=self.run_source(source,actuators=actuators);self.assertEqual(value,[actuators,'sent',{'pan':42}]);self.assertEqual(adapters[0].actions[0]['channels'],{'pan':42})

    def test_capability_inventory_is_static_redacted_detached_and_not_granted(self):
        calls=[];registry=Registry();registry.register_robot(RobotSpec('mobile',lambda context:calls.append(context)or Adapter(),ACTUATORS,resource_id='/secret/port',description='Static robot'))
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            first=app.capabilities();self.assertEqual(calls,[]);self.assertEqual(first['tools']['robot.control']['scope'],'language');self.assertFalse(first['tools']['robot.control']['callable']);self.assertNotIn('robot.control',DEFAULT_CAPABILITIES);self.assertIn('robot.control',language_permissions())
            self.assertNotIn('/secret/port',str(first));first['robots']['mobile']['actuators']['left']['minimum']=42;self.assertEqual(app.capabilities()['robots']['mobile']['actuators']['left']['minimum'],-1)
            p=app.create_project('denied',source=SOURCE,capabilities=[])
            from chaos.framework import RunFailed
            with self.assertRaises(RunFailed):app.start(p['id'],p['version'],None).result(6)
            self.assertEqual(calls,[])
        from chaos.evaluation import validate_fixtures
        with self.assertRaisesRegex(LanguageError,'Reserved effects'):validate_fixtures({'tools':[{'name':'robot.control','input':{},'output':42}]})

    def test_std_robot_aliases_and_runtime_macro_ast_are_real_native_calls(self):
        self.assertEqual(len(STDLIB),21);self.assertEqual(Framework.evaluate('import "std/robot" as R;['+','.join('R.'+n+'=='+n for n in NAMES)+']'),[True]*10)
        for call in ['robot_open("mobile")','robot_command(null,{})','robot_observe(null)']:
            with self.assertRaises(LanguageError)as error:Framework.evaluate('macro bad(){syntax_literal('+call+')};bad!()')
            self.assertEqual(error.exception.code,'macro_error')
        source='macro make(run,packet,channels){quote{unquote(run)(unquote(packet),unquote(channels))}};import "std/robot" as R;let robot_command=fn(a,b){null};async with await R.robot_open("mobile") as r{let packet=await R.robot_observe(r);let command=make!(R.robot_command,packet,{left:0.2,right:0.4});R.robot_receipt_info(await r(command)).status}'
        self.assertEqual(self.run_source(source)[0],'sent')
        self.assertEqual(Framework.evaluate('macro local(){let robot_open=fn(name){name};syntax_literal(robot_open("local"))};local!()'),'local')
        from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
        self.assertIn('native-robots',LANGUAGE_REFERENCE_TOPICS);self.assertIn('targetVerified',Documentation().page('native-robots')['markdown'])

    def test_code_program_module_and_compiled_policy_use_same_native_runtime(self):
        function='fn(packet:RobotObservation)->RobotCommand{robot_command(packet,{left:0.2,right:0.4})}'
        constructors=['function_from(code_from('+json.dumps(function)+'))']
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':function.replace('fn(packet','fn choose(packet')+';export{choose};'},'languageVersions':{'main.chaos':2}}
        constructors.append('module_from(program_from('+json.dumps(definition)+')).choose')
        for constructor in constructors:
            source='let choose='+constructor+';async with await robot_open("mobile") as r{let packet=await robot_observe(r);let command=choose(packet);robot_receipt_info(await r(command)).status}'
            value,adapters,_=self.run_source(source);self.assertEqual(value,'sent');self.assertEqual(len(adapters[0].actions),1)
        for target in ('interpreter','auto','auto-flow'):
            source='let choose=compile_function('+function+',target="'+target+'");async with await robot_open("mobile") as r{let packet=await robot_observe(r);robot_receipt_info(await r(choose(packet))).status}'
            value,adapters,_=self.run_source(source);self.assertEqual(value,'sent');self.assertEqual(len(adapters[0].actions),1)

    def test_real_stdio_lsp_and_cli_registration_authority(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        from chaos.cli import main
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);path=root/'main.chaos';path.write_text(SOURCE)
            for action in ('check','fmt','ir'):
                proc=subprocess.run([sys.executable,'-B','-m','chaos',action,str(path)],cwd=root,env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15);self.assertEqual(proc.returncode,0,proc.stderr)
            client=StdioClient(root)
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':SOURCE}})
                at=SOURCE.index('R.robot_command(observation,')+len('R.robot_command(observation,');params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(len(SOURCE[:at].encode('utf-16-le'))//2)}
                self.assertIn('channels',client.request('textDocument/signatureHelp',params)['signatures'][0]['label']);self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
            for flags,expected in [([],1),(['--capability','robot.control'],0)]:
                registry,adapters=registered();output=io.StringIO();errors=io.StringIO()
                with patch.object(sys,'argv',['chaos','run',str(path),'--robots','fixture.json']+flags),patch('chaos.robot_config.load_robot_registry',return_value=registry),patch('chaos.settings.Settings',return_value=Settings(':memory:')),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):code=main()
                self.assertEqual(code,expected,errors.getvalue())
                if expected==0:self.assertEqual(json.loads(output.getvalue()),EXPECTED);self.assertEqual(len(adapters[0].actions),1)
                else:self.assertEqual(adapters,[])
            config=root/'robots.json';config.write_text((ROOT/'examples/openbot/robots.example.json').read_text());cold=root/'cold.chaos';cold.write_text('task_info(robot_open("mobile")).state')
            proc=subprocess.run([sys.executable,'-B','-m','chaos','run',str(cold),'--robots',str(config)],cwd=root,env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15);self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(json.loads(proc.stdout),'created')

    def test_config_loading_and_both_cli_flags_never_open_hardware(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'robots.json';path.write_text((ROOT/'examples/openbot/robots.example.json').read_text())
            with patch('chaos.robot_openbot.SerialOpenBot',side_effect=AssertionError('config opened serial')):registry=load_robot_registry(path)
            self.assertEqual(list(registry.robot_snapshot()),['mobile'])
            from chaos import DeviceSpec
            shared=Registry();shared.register_device(DeviceSpec('desk',lambda context:None));self.assertIs(load_robot_registry(path,shared),shared);self.assertIn('desk',shared.device_snapshot())

    def test_http_sdk_source_graph_ir_edit_and_snapshot_are_one_program(self):
        with tempfile.TemporaryDirectory()as folder:
            registry,adapters=registered();server=make_server(port=0,db=Path(folder)/'state.sqlite',settings=Settings(':memory:'),state_dir=Path(folder)/'control',registry=registry);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));self.assertIn('mobile',client.capabilities()['robots']);self.assertEqual(adapters,[]);p=client.create_project('robot-http',source=SOURCE,capabilities=['robot.control']);old=client.start(p['id'],p['version'],None);self.assertEqual(old.result(8),EXPECTED)
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(8),EXPECTED);ir=client.program(p['id'])['program'];target=next(n for n in nodes(ir['root'])if n.get('kind')=='literal'and n.get('value')=={'$number':'0.2'});saved=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':{'$number':'0.3'}}]);expected=dict(EXPECTED,channels={'left':0.3,'right':0.4});self.assertEqual(client.start(p['id'],saved['version'],None,mode='graph').result(8),expected);self.assertEqual(adapters[-1].actions[0]['channels'],expected['channels']);self.assertEqual(old.get()['snapshot']['source'],SOURCE);self.assertEqual(client.analyze(saved['source']),Framework.analyze(saved['source']));self.assertEqual(client._request('GET','/docs/native-robots'),Documentation().page('native-robots'))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_actual_agent_tool_closure_and_team_live_policy(self):
        registry,adapters=registered();outputs=[]
        @registry.provider('fixture.robot')
        def choose(request,context):
            context.checkpoint()
            if request.step==1:return ToolCall('drive',{'left':0.2,'right':0.4})
            value=request.history[-1]['output'];outputs.append(value);return Final(value)
        source='async with await robot_open("mobile") as r{let packet=await robot_observe(r);let readings=robot_observation_info(packet).readings;fn drive(channels:Record)->Record{if readings.sonar.unit=="cm" && readings.sonar.value>=30{channels}else{{left:0,right:0}}};agent planner{provider:"fixture.robot",tools:[]};let selected=await agent_bind(planner,[agent_tool("drive",drive,input_type="Record",output_type="Record")])(null);robot_receipt_info(await r(robot_command(packet,selected)))}'
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            p=app.create_project('tool',source=source,capabilities=['robot.control']);value=app.start(p['id'],p['version'],None).result(8)
        self.assertEqual(value['status'],'sent');self.assertFalse(value['targetVerified']);self.assertEqual(outputs,[{'left':0.2,'right':0.4}]);self.assertEqual(len(adapters[0].actions),1)
        team={'kind':'team','name':'robot policy','entry':'choose','roles':[{'id':'choose','parent':None,'capabilities':['robot.control'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='let crew=team_bind(team_from('+json.dumps(team)+'),{choose:fn(packet:RobotObservation)->RobotCommand{robot_command(packet,{left:0.2,right:0.4})}});async with await robot_open("mobile") as r{let packet=await robot_observe(r);let command=await crew(packet);robot_receipt_info(await r(command)).status}'
        value,adapters,events=self.run_source(source);self.assertEqual(value,'sent');self.assertEqual(len(adapters[0].actions),1);self.assertEqual([e['type']for e in events if e['type'].startswith('team.role.')],['team.role.started','team.role.completed'])

    def test_async_generator_policy_observes_and_consumes_each_fresh_packet(self):
        source='async fn* packets(robot:Robot){for i in range(2){yield await robot_observe(robot)}};async with await robot_open("mobile") as r{var sequences=[];async for packet in packets(r){let c=robot_command(packet,{left:0.2,right:0.4});await r(c);sequences=append(sequences,robot_observation_info(packet).sequence)};[sequences,robot_receipt_info(await robot_stop(r)).status]}'
        value,adapters,_=self.run_source(source);self.assertEqual(value,[[1,2],'stopSent']);self.assertEqual(len(adapters[0].actions),2);self.assertEqual([p['sequence']for p in adapters[0].actions],[1,2]);self.assertEqual(adapters[0].close_count,1)
