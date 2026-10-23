"""Multiple-value state through original language, agent, software robot and public surfaces."""
import json,os,socket,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Client,Final,Framework,Registry,Settings,ToolCall,compile_artifact,compile_program,load_artifact
from chaos.language import Evaluator,Native
from chaos.ide_language import analyze
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
import _multiple_value_surface220 as fixture
import _generator_flow208 as flows
import test_module_frame_surfaces as modules
import test_robot_values as robots
ROOT=Path(__file__).resolve().parents[1]
class MultipleValueSurfaces220(unittest.TestCase):
    run_source=modules.ModuleFrameSurfaces.run_source
    def test_source_IR_formatted_and_completed_handles_execute_both_actual_frames(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),fixture.observe_frames()as seen:value,_=self.run_source(fixture.SOURCE,mode=mode,capabilities=[])
            self.assertEqual(value,fixture.EXPECTED);fixture.assert_frames(self,seen)
        for text in (format_source(fixture.SOURCE),format_program(parse_program(fixture.SOURCE))):
            with fixture.observe_frames()as seen:self.assertEqual(Framework.evaluate(text),fixture.EXPECTED)
            fixture.assert_frames(self,seen)
    def test_Code_macro_explicit_flow_and_saved_artifact_use_same_receivers(self):
        source='fn raw(value){multiple_value_list(fn(){return_values(value,value+2)})};let f=compile_function(raw,target="auto-flow");f(40)'
        for target in ('interpreter','auto-flow'):
            with self.subTest(target=target),fixture.observe_frames()as seen:self.assertEqual(Framework.evaluate(source.replace('auto-flow',target)),[40,42])
            fixture.assert_frames(self,seen)
        artifact=compile_artifact(source);bundle=load_artifact(json.loads(json.dumps(artifact,sort_keys=True)))
        with fixture.observe_frames()as seen,flows.observe_states()as states:self.assertEqual(Framework.evaluate(bundle),[40,42])
        fixture.assert_frames(self,seen);self.assertTrue(states)
        self.assertTrue(all(state.program._artifact_decoded and state.program._artifact_hash==artifact['artifactHash']and state.program.resume_calls>0 and state.closed and not state.frames and not state._owned_activations and not state._transfers for state in states))
        code='let f=function_from(code_from("fn(x){multiple_value_bind(3,fn(){return_values(x,2)},fn(a,b,c){return_values(a+b,c)})}"));multiple_value_list(fn(){f(40)})'
        macro='macro capture(receiver,p){quote{unquote(receiver)(unquote(p))}};capture!(multiple_value_list,fn(){return_values(42,"macro")})'
        for text,expected in ((code,[42,None]),(macro,[42,'macro'])):
            with fixture.observe_frames()as seen:self.assertEqual(Framework.evaluate(text),expected)
            fixture.assert_frames(self,seen)
    def test_authoring_is_inert_and_public_native_catalog_is_unchanged(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('authoring executed')),patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('producer executed')):
            report=analyze(fixture.SOURCE);pretty=format_source(fixture.SOURCE);plan=compile_program(fixture.SOURCE)
        self.assertFalse([d for d in report['diagnostics']if d['severity']=='error']);self.assertTrue(plan)
        self.assertEqual(parse_program(pretty)['semanticHash'],parse_program(fixture.SOURCE)['semanticHash'])
        self.assertEqual(sum(type(value)is Native for value in Evaluator().env.values.values()),335)
        for name in ('return_values','values_list','multiple_value_list','nth_value','multiple_value_call','multiple_value_bind','multiple_value_prog1'):
            self.assertIsNotNone(analyze(name+'(')['signatureHelp'])
    def test_AgentTool_Team_and_Bot_use_explicit_full_values_and_once_effects(self):
        registry=Registry();effects=[];steps=[]
        @registry.tool('fixture.mv220.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.mv220.provider')
        def provider(request,context):context.checkpoint();steps.append(request.step);return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        caps=['fixture.mv220.audit'];team={'kind':'team','name':'mv','entry':'work','roles':[{'id':'work','parent':None,'capabilities':caps,'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':caps}}}]}
        source='''async fn work(value){tool("fixture.mv220.audit",value);await sleep(0);return_values(value+2,"proof")};async fn captured(value){multiple_value_list(async fn(){await work(value)})};agent helper{provider:"fixture.mv220.provider",tools:["fixture.mv220.audit"]};let bound=agent_bind(helper,[agent_tool("work",captured,capabilities=["fixture.mv220.audit"])]);let crew=team_bind(team_from(TEAM),{work:work});async fn execute(plan,state){[await bound(40),multiple_value_list(async fn(){await crew(40)})]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};let b=bot("mv",fn(state){state.input},execute,verify);(await b(null)).output'''.replace('TEAM',json.dumps(team))
        with fixture.observe_frames()as seen:value,events=self.run_source(source,registry=registry,capabilities=caps)
        self.assertEqual(value,[[42,'proof'],[42,'proof']]);fixture.assert_frames(self,seen)
        self.assertEqual(effects,[40,40]);self.assertEqual(steps,[1,2])
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1);self.assertEqual(sum(e['type']=='team.role.started'for e in events),1)
        self.assertNotIn('Completion',json.dumps(events))
    def test_software_Robot_admission_and_multiple_value_receipt_do_not_replay(self):
        source='''async fn capture(r,command){multiple_value_list(async fn(){let receipt=await r(command);return_values(receipt.status,receipt.targetVerified,command.used)})};async with await robot_open("rig") as r{let observed=await robot_observe(r);let command=robot_command(observed,{pan:0,tilt:0});let first=await capture(r,command);let duplicate=try{await capture(r,command)}catch e{e.code};[first,duplicate]}'''
        log=[]
        with robots.host(log)as app,fixture.observe_frames()as seen:value=robots.run(app,source)
        self.assertEqual(value,[['sent',False,True],'robot_command_used']);self.assertEqual([x[0]for x in log],['open','observe','act','close']);fixture.assert_frames(self,seen)
        denied=[]
        with robots.host(denied)as app:self.assertEqual(robots.run(app,'try{'+source+'}catch e{e.code}',caps=[]),'capability_error')
        self.assertEqual(denied,[])
    def test_outside_isolated_SDK_and_CLI_execute_actual_both_frames_without_network(self):
        with tempfile.TemporaryDirectory(prefix='chaos-mv220-')as folder:
            work=Path(folder);file=work/'main.chaos';file.write_text(fixture.SOURCE)
            env={key:os.environ[key]for key in ('PATH','LANG','LC_ALL','TMPDIR','SYSTEMROOT','WINDIR','HOME','USERPROFILE')if key in os.environ}
            env.update(CHAOS_CONFIG=str(work/'settings.json'),PYTHONDONTWRITEBYTECODE='1')
            prelude='import sys,socket;sys.path.insert(0,sys.argv.pop(1))\ndef deny(*a,**k):raise AssertionError("network forbidden")\nsocket.socket.connect=socket.socket.connect_ex=deny\nfrom chaos.execution_multiple_values import MULTIPLE_VALUE_FRAMES\nseen=set()\ndef wrap(original,name):\n def step(self,*args,**kwargs):\n  seen.add(name);return original(self,*args,**kwargs)\n return step\nfor cls in MULTIPLE_VALUE_FRAMES:cls.step=wrap(cls.step,cls.__name__)\ndef verify():assert seen=={"MultipleValueReceiveFrame","MultipleValueResolveFrame"}\n'
            scripts=[prelude+'from chaos.cli import main\nresult=main();verify();raise SystemExit(result)',prelude+'import json\nfrom pathlib import Path\nfrom chaos import Framework\nresult=Framework.evaluate(Path(sys.argv[1]).read_text());verify();print(json.dumps(result))']
            for code,args in zip(scripts,(['run',str(file),'--db',':memory:'],[str(file)])):
                child=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT)]+args,cwd=work,env=env,text=True,capture_output=True,timeout=35)
                self.assertEqual(child.returncode,0,child.stderr);self.assertEqual(json.loads(child.stdout),fixture.EXPECTED)
    def test_private_HTTP_in_memory_runs_both_frame_classes_and_authoring_stays_inert(self):
        with tempfile.TemporaryDirectory(prefix='chaos-mv-http220-')as folder:
            settings=Settings(':memory:');public=settings.public()
            settings.update({'providers':{name:{'baseURL':'http://127.0.0.1:1','clearKey':True}for name in public['providers']},'runtime':{'continuousEnabled':False,'codeEnabled':False,'browserEnabled':False,'workspace':str(Path(folder)/'tasks')}},version=public['version'])
            with Framework(':memory:',settings=settings)as app,fixture.observe_frames()as seen:
                from chaos.server import make_server
                server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True)
                allowed=server.server_address;connect=socket.socket.connect;connect_ex=socket.socket.connect_ex;outside=[];started=False
                def guarded(original):
                    def call(sock,address):
                        if tuple(address)!=tuple(allowed):outside.append(address);raise AssertionError('non-private HTTP connection')
                        return original(sock,address)
                    return call
                try:
                    with patch.object(socket.socket,'connect',guarded(connect)),patch.object(socket.socket,'connect_ex',guarded(connect_ex)):
                        thread.start();started=True;client=Client('http://127.0.0.1:'+str(server.server_port))
                        before=len(seen);self.assertEqual(client.analyze(fixture.SOURCE)['diagnostics'],[]);pretty=client.format_source(fixture.SOURCE);self.assertEqual(len(seen),before)
                        self.assertEqual(parse_program(pretty)['semanticHash'],parse_program(fixture.SOURCE)['semanticHash'])
                        for mode in ('source','graph'):
                            project=client.create_project('mv-'+mode,source=fixture.SOURCE,capabilities=[])
                            self.assertEqual(client.start(project['id'],project['version'],None,mode=mode).result(20),fixture.EXPECTED)
                finally:
                    if started:
                        stopper=threading.Thread(target=server.shutdown,daemon=True);stopper.start();stopper.join(3)
                    server.server_close()
                    if started:thread.join(3);self.assertFalse(stopper.is_alive());self.assertFalse(thread.is_alive())
                self.assertEqual(outside,[]);fixture.assert_frames(self,seen)
