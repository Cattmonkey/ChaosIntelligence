"""Explicit stream consumption across existing language/agent/robot/public APIs."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,Registry,ToolCall,Final,compile_program
from chaos.language import Evaluator
from chaos.ide_language import analyze
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
from chaos.compiled_flow_state import FlowState
import test_module_frame_surfaces as module_surface
import test_robot_values as robot_fixture
import _generator_flow208 as fixture
ROOT=Path(__file__).resolve().parents[1]
SOURCE='''async fn* raw(n){var i=0;while i<n{await sleep(0);yield i;i=i+1};return 9};
let g=compile_function(raw,target="auto-flow");let s=g(3);let cold=compile_flow_info(g).resumeCalls;
let values=await acollect(s);[values,cold,compile_flow_info(g).resumeCalls>0]'''
EXPECTED=[[0,1,2],0,True]

class GeneratorFlowSurfaces208(unittest.TestCase):
    run_source=module_surface.ModuleFrameSurfaces.run_source
    def test_source_IR_Code_and_formatted_source_execute_real_stream_flow(self):
        for mode in('source','graph'):
            with self.subTest(mode=mode),fixture.observe_states()as states:result,_=self.run_source(SOURCE,mode=mode,capabilities=[])
            self.assertEqual(result,EXPECTED);self.assertTrue(states);self.assertTrue(all(s.generating and s.closed and s.program.resume_calls>0 for s in states))
        for text in(format_source(SOURCE),format_program(parse_program(SOURCE))):self.assertEqual(Framework.evaluate(text),EXPECTED)
        code='let g=compile_function(function_from(code_from("fn*(x){yield x;return 9}")),target="auto-flow");[collect(g(42)),compile_flow_info(g).resumeCalls>0]'
        self.assertEqual(Framework.evaluate(code),[[42],True])

    def test_authoring_stream_source_is_inert_and_preserves_semantic_hash(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('authoring executed')),patch.object(FlowState,'__init__',side_effect=AssertionError('authoring enteredflow')):
            report=analyze(SOURCE);pretty=format_source(SOURCE);plan=compile_program(SOURCE)
        self.assertFalse([x for x in report['diagnostics']if x['severity']=='error']);self.assertTrue(plan)
        self.assertEqual(parse_program(pretty)['semanticHash'],parse_program(SOURCE)['semanticHash']);self.assertEqual(format_source(pretty),pretty)

    def test_agent_tool_team_bot_explicit_consume_and_direct_generator_rejection(self):
        registry=Registry();calls=[];effects=[]
        @registry.tool('fixture.gen208.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.gen208.provider')
        def provider(request,context):
            context.checkpoint();calls.append(request.step);return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        caps=['fixture.gen208.audit'];team={'kind':'team','name':'compiledstreams','entry':'work','roles':[{'id':'work','parent':None,'capabilities':caps,'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':caps}}}]}
        source='''async fn* raw(value){tool("fixture.gen208.audit",value);yield value;await sleep(0);yield value+2};let g=compile_function(raw,target="auto-flow");
async fn consume(value){await acollect(g(value))};agent helper{provider:"fixture.gen208.provider",tools:["fixture.gen208.audit"]};
let bound=agent_bind(helper,[agent_tool("work",consume,capabilities=["fixture.gen208.audit"])]);let crew=team_bind(team_from(TEAM),{work:consume});
async fn execute(plan,state){[await bound(40),await crew(40)]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};
let b=bot("stream consumer",fn(state){state.input},execute,verify);[(await b(null)).output,compile_flow_info(g).resumeCalls>0]'''.replace('TEAM',json.dumps(team))
        with fixture.observe_states()as states:result,events=self.run_source(source,registry=registry,capabilities=caps)
        self.assertEqual(result,[[[40,42],[40,42]],True]);self.assertEqual(calls,[1,2]);self.assertEqual(effects,[40,40]);self.assertTrue(states)
        self.assertTrue(all(s.generating and s.closed for s in states))
        for flavor in('fn*','async fn*'):
            rejected=[]
            for target in('raw','auto-flow'):
                expression=flavor+'(state){yield 1}'
                if target!='raw':expression='compile_function('+expression+',target="auto-flow")'
                direct='let g='+expression+';let b=bot("bad",g,fn(p,s){p},fn(s){{done:true,context:null,output:null}});let code=try{await b(null)}catch e{e.code};[code,compile_flow_info(g).resumeCalls]'
                with self.subTest(flavor=flavor,rejectedTarget=target),fixture.observe_states()as rejected_states:
                    value,_=self.run_source(direct,capabilities=[])
                self.assertEqual(value,['bot_error',0]);self.assertEqual(rejected_states,[]);rejected.append(value)
            self.assertEqual(rejected[0],rejected[1])

    def test_robot_admission_and_cold_yielded_operation_are_never_replayed(self):
        source='''async fn* raw(){async with await robot_open("rig") as r{let observation=await robot_observe(r);let command=robot_command(observation,{pan:0,tilt:0});
let receipt=await r(command);yield ([receipt.status,receipt.targetVerified,command.used]);yield try{await r(command)}catch e{e.code}}};
let g=compile_function(raw,target="auto-flow");[await acollect(g()),compile_flow_info(g).resumeCalls>0]'''
        for target in('raw','auto-flow'):
            actual=source.replace('compile_function(raw,target="auto-flow")','raw')if target=='raw'else source
            log=[]
            with robot_fixture.host(log)as app,fixture.observe_states()as states:result=robot_fixture.run(app,actual)
            self.assertEqual(result,[[['sent',False,True],'robot_command_used'],target!='raw'])
            self.assertEqual([x[0]for x in log],['open','observe','act','close'])
            self.assertEqual(bool(states),target!='raw');self.assertTrue(all(s.generating and s.closed for s in states))
            log=[]
            with robot_fixture.host(log)as app:self.assertEqual(robot_fixture.run(app,'try{'+actual+'}catch e{e.code}',caps=[]),'capability_error')
            self.assertEqual(log,[])
            cold='''async fn* raw(){async with await robot_open("rig") as r{let observation=await robot_observe(r);let command=robot_command(observation,{pan:0,tilt:0});let operation=r(command);yield type_of(operation);cancel(operation);yield command.used}};
let g=CHOICE;await acollect(g())'''.replace('CHOICE','raw'if target=='raw'else'compile_function(raw,target="auto-flow")')
            log=[]
            with robot_fixture.host(log)as app:self.assertEqual(robot_fixture.run(app,cold),['Task',False])
            self.assertEqual([x[0]for x in log],['open','observe','close'])

    def test_outside_isolated_SDK_CLI_stream_consumers_use_no_model_or_hardware(self):
        with tempfile.TemporaryDirectory(prefix='chaos-generator-flow208-')as folder:
            work=Path(folder);path=work/'main.chaos';path.write_text(SOURCE)
            env={n:os.environ[n]for n in('PATH','LANG','LC_ALL','TMPDIR','SYSTEMROOT','WINDIR')if n in os.environ};env.update(HOME=str(work),USERPROFILE=str(work),CHAOS_CONFIG=str(work/'settings.json'),PYTHONDONTWRITEBYTECODE='1')
            prefix='import sys,socket;sys.path.insert(0,sys.argv.pop(1));\ndef deny(*a,**k):raise AssertionError("network forbidden")\nsocket.socket.connect=socket.socket.connect_ex=deny;\n'
            scripts=[prefix+'from chaos.cli import main;raise SystemExit(main())',prefix+'import json;from pathlib import Path;from chaos import Framework;print(json.dumps(Framework.evaluate(Path(sys.argv[1]).read_text())))']
            args=[['run',str(path),'--db',':memory:'],[str(path)]]
            for code,arguments in zip(scripts,args):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT)]+arguments,cwd=work,env=env,text=True,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),EXPECTED)
