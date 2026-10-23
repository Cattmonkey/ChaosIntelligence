"""Resource CFG through existing authoring, Agent/Bot, Robot and public APIs."""
import json,unittest,os,sys,subprocess,tempfile
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,Registry,ToolCall,Final,compile_program
from chaos.language import Evaluator
from chaos.ide_language import analyze
from chaos.compiled_flow_state import FlowState
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
import _generator_flow208 as streams
import test_module_frame_surfaces as modules
from test_generator_flow_surfaces208 import ROOT
import test_async_iteration_boundaries210 as http_fixture
import test_robot_values as robot_fixture

SOURCE='''class Gate{var value=0;fn __init__(s,value){s.value=value;null}async fn __aenter__(s){await sleep(0);s.value}async fn __aexit__(s,error){await sleep(0);null}};async fn raw(value){async with Gate(value) as n{n+2}};let f=compile_function(raw,target="auto-flow");let cold=compile_flow_info(f).resumeCalls;[await f(40),await f(0),cold,compile_flow_info(f).resumeCalls>0]'''
EXPECTED=[42,2,0,True]

def real_resource(test,states):
    test.assertTrue(states);test.assertTrue(any(row[0]=='resource'for state in states for row in state.program.instructions))
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers and s.pending_cursor is None and s.pending_request is None for s in states))

class ResourceSurfaces217(unittest.TestCase):
    run_source=modules.ModuleFrameSurfaces.run_source
    def test_source_IR_Code_macro_formatter_and_authoring_keep_resource_semantics(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),streams.observe_states()as states:value,_=self.run_source(SOURCE,mode=mode,capabilities=[])
            self.assertEqual(value,EXPECTED);real_resource(self,states)
        for text in (format_source(SOURCE),format_program(parse_program(SOURCE))):self.assertEqual(Framework.evaluate(text),EXPECTED)
        code='class C{fn __enter__(s){42}fn __exit__(s,e){null}};let f=compile_function(function_from(code_from("fn(manager){with manager as x{x}}")),target="auto-flow");[f(C()),compile_flow_info(f).resumeCalls>0]'
        with streams.observe_states()as states:self.assertEqual(Framework.evaluate(code),[42,True])
        real_resource(self,states)
        macro='class C{fn __enter__(s){42}fn __exit__(s,e){null}};macro resource_value(manager){quote{with unquote(manager) as resource{resource}}};fn raw(){resource_value!(C())};let f=compile_function(raw,target="auto-flow");f()'
        with streams.observe_states()as states:self.assertEqual(Framework.evaluate(macro),42)
        real_resource(self,states)
        with patch.object(Evaluator,'run',side_effect=AssertionError('authoring executed')),patch.object(FlowState,'__init__',side_effect=AssertionError('authoring entered flow')):
            report=analyze(SOURCE);pretty=format_source(SOURCE);plan=compile_program(SOURCE)
        self.assertFalse([d for d in report['diagnostics']if d['severity']=='error']);self.assertTrue(plan)
        self.assertEqual(parse_program(pretty)['semanticHash'],parse_program(SOURCE)['semanticHash'])

    def test_agent_tool_team_bot_callbacks_keep_body_and_exit_effects_once(self):
        registry=Registry();calls=[];effects=[]
        @registry.tool('fixture.resource217.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.resource217.provider')
        def provider(request,context):context.checkpoint();calls.append(request.step);return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        caps=['fixture.resource217.audit'];team={'kind':'team','name':'resource','entry':'work','roles':[{'id':'work','parent':None,'capabilities':caps,'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':caps}}}]}
        source='''class Lease{var value=0;fn __init__(s,value){s.value=value;null}async fn __aenter__(s){s.value}async fn __aexit__(s,error){tool("fixture.resource217.audit",s.value+1);null}};async fn raw(value){async with Lease(value) as n{tool("fixture.resource217.audit",n);n+2}};let work=compile_function(raw,target="auto-flow");
agent helper{provider:"fixture.resource217.provider",tools:["fixture.resource217.audit"]};let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.resource217.audit"])]);
let crew=team_bind(team_from(TEAM),{work:work});async fn execute(plan,state){[await bound(40),await crew(40)]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};
let b=bot("resource",fn(state){state.input},execute,verify);[(await b(null)).output,compile_flow_info(work).resumeCalls>0]'''.replace('TEAM',json.dumps(team))
        with streams.observe_states()as states:value,events=self.run_source(source,registry=registry,capabilities=caps)
        self.assertEqual(value,[[42,42],True]);self.assertEqual(calls,[1,2]);self.assertEqual(effects,[40,41,40,41]);real_resource(self,states)
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1);self.assertEqual(sum(e['type']=='team.role.started'for e in events),1)

    def test_robot_admission_once_and_duplicate_command_rejection_survive_resource_callback(self):
        source='''class Lease{async fn __aenter__(s){0}async fn __aexit__(s,error){await sleep(0);null}};async fn raw(r,command){async with Lease() as n{try{await r(command)}catch e{e.code}}};let f=CHOICE;
async with await robot_open("rig") as r{let o=await robot_observe(r);let command=robot_command(o,{pan:0,tilt:0});let receipt=await f(r,command);let duplicate=await f(r,command);[[receipt.status,receipt.targetVerified,command.used],duplicate,compile_flow_info(f).resumeCalls>0]}'''
        for compiled in (False,True):
            actual=source.replace('CHOICE','compile_function(raw,target="auto-flow")'if compiled else'raw');log=[]
            with robot_fixture.host(log)as app,streams.observe_states()as states:value=robot_fixture.run(app,actual)
            self.assertEqual(value,[['sent',False,True],'robot_command_used',compiled]);self.assertEqual([x[0]for x in log],['open','observe','act','close'])
            if compiled:real_resource(self,states)
            else:self.assertEqual(states,[])
            denied=[]
            with robot_fixture.host(denied)as app:self.assertEqual(robot_fixture.run(app,'try{'+actual+'}catch e{e.code}',caps=[]),'capability_error')
            self.assertEqual(denied,[])

    def test_outside_isolated_SDK_and_CLI_execute_same_resource_program(self):
        # HOME and USERPROFILE retain their original caller values. The child
        # uses only explicit private config and an in-memory project store.
        with tempfile.TemporaryDirectory(prefix='chaos-resource-flow217-')as folder:
            work=Path(folder);source_path=work/'main.chaos';source_path.write_text(SOURCE)
            env={name:os.environ[name]for name in('PATH','LANG','LC_ALL','TMPDIR','SYSTEMROOT','WINDIR','HOME','USERPROFILE')if name in os.environ}
            env.update(CHAOS_CONFIG=str(work/'settings.json'),PYTHONDONTWRITEBYTECODE='1')
            prefix='import sys,socket;sys.path.insert(0,sys.argv.pop(1));\ndef deny(*a,**k):raise AssertionError("network forbidden")\nsocket.socket.connect=socket.socket.connect_ex=deny;\n'
            witness='from chaos.compiled_flow_state import FlowState\nstates=[];original_state=FlowState.__init__\ndef entered(self,*args,**kwargs):\n original_state(self,*args,**kwargs);states.append(self)\nFlowState.__init__=entered\ndef verify():\n assert states and all(s.closed and not s.frames and not s._owned_activations and not s._transfers and s.pending_cursor is None and s.pending_request is None for s in states)\n assert any(row[0]=="resource" for s in states for row in s.program.instructions)\n'
            scripts=[prefix+witness+'from chaos.cli import main\nresult=main();verify();raise SystemExit(result)',prefix+witness+'import json\nfrom pathlib import Path\nfrom chaos import Framework\nresult=Framework.evaluate(Path(sys.argv[1]).read_text());verify();print(json.dumps(result))']
            arguments=[['run',str(source_path),'--db',':memory:'],[str(source_path)]]
            for code,args in zip(scripts,arguments):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT)]+args,cwd=work,env=env,text=True,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),EXPECTED)

    def test_private_HTTP_executes_resource_program_in_memory_with_only_loopback(self):
        with patch.object(http_fixture,'SOURCE',SOURCE),patch.object(http_fixture,'EXPECTED',EXPECTED):
            http_fixture.AsyncIterationBoundaries210.test_private_HTTP_runs_same_async_consumer_without_project_database(self)
