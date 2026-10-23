"""Protected CFG through existing authoring, Agent/Bot, Robot and public APIs."""
import json,unittest
from unittest.mock import patch
from chaos import Framework,Registry,ToolCall,Final,compile_program
from chaos.language import Evaluator
from chaos.ide_language import analyze
from chaos.compiled_flow_state import FlowState
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
import _generator_flow208 as streams
import test_module_frame_surfaces as modules
import test_generator_flow_surfaces208 as outside
import test_async_iteration_boundaries210 as http_fixture
import test_robot_values as robot_fixture

SOURCE='''async fn raw(value){try{await sleep(0);if value<0{throw value}else{value}}catch e if e.value<0{0}else{42}finally{await sleep(0)}};
let f=compile_function(raw,target="auto-flow");let cold=compile_flow_info(f).resumeCalls;[await f(40),await f(-1),cold,compile_flow_info(f).resumeCalls>0]'''
EXPECTED=[42,0,0,True]

def real_protected(test,states):
    test.assertTrue(states);test.assertTrue(any(row[0]=='protect'for state in states for row in state.program.instructions))
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers and s.pending_cursor is None and s.pending_request is None for s in states))

class ProtectedSurfaces214(unittest.TestCase):
    run_source=modules.ModuleFrameSurfaces.run_source
    def test_source_IR_Code_macro_formatter_and_authoring_keep_protected_semantics(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),streams.observe_states()as states:value,_=self.run_source(SOURCE,mode=mode,capabilities=[])
            self.assertEqual(value,EXPECTED);real_protected(self,states)
        for text in (format_source(SOURCE),format_program(parse_program(SOURCE))):self.assertEqual(Framework.evaluate(text),EXPECTED)
        code='let f=compile_function(function_from(code_from("fn(x){try{x}catch e{0}finally{null}}")),target="auto-flow");[f(42),compile_flow_info(f).resumeCalls>0]'
        with streams.observe_states()as states:self.assertEqual(Framework.evaluate(code),[42,True])
        real_protected(self,states)
        macro='macro protected_value(value){quote{try{unquote(value)}catch e{0}finally{null}}};fn raw(){protected_value!(42)};let f=compile_function(raw,target="auto-flow");f()'
        with streams.observe_states()as states:self.assertEqual(Framework.evaluate(macro),42)
        real_protected(self,states)
        with patch.object(Evaluator,'run',side_effect=AssertionError('authoring executed')),patch.object(FlowState,'__init__',side_effect=AssertionError('authoring entered flow')):
            report=analyze(SOURCE);pretty=format_source(SOURCE);plan=compile_program(SOURCE)
        self.assertFalse([d for d in report['diagnostics']if d['severity']=='error']);self.assertTrue(plan)
        self.assertEqual(parse_program(pretty)['semanticHash'],parse_program(SOURCE)['semanticHash'])

    def test_agent_tool_team_bot_callbacks_keep_body_and_finally_effects_once(self):
        registry=Registry();calls=[];effects=[]
        @registry.tool('fixture.protect214.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.protect214.provider')
        def provider(request,context):context.checkpoint();calls.append(request.step);return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        caps=['fixture.protect214.audit'];team={'kind':'team','name':'protected','entry':'work','roles':[{'id':'work','parent':None,'capabilities':caps,'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':caps}}}]}
        source='''async fn raw(value){try{tool("fixture.protect214.audit",value);throw value}catch e if e.value==40{e.value+2}finally{tool("fixture.protect214.audit",value+1)}};let work=compile_function(raw,target="auto-flow");
agent helper{provider:"fixture.protect214.provider",tools:["fixture.protect214.audit"]};let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.protect214.audit"])]);
let crew=team_bind(team_from(TEAM),{work:work});async fn execute(plan,state){[await bound(40),await crew(40)]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};
let b=bot("protected",fn(state){state.input},execute,verify);[(await b(null)).output,compile_flow_info(work).resumeCalls>0]'''.replace('TEAM',json.dumps(team))
        with streams.observe_states()as states:value,events=self.run_source(source,registry=registry,capabilities=caps)
        self.assertEqual(value,[[42,42],True]);self.assertEqual(calls,[1,2]);self.assertEqual(effects,[40,41,40,41]);real_protected(self,states)
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1);self.assertEqual(sum(e['type']=='team.role.started'for e in events),1)

    def test_robot_admission_once_and_duplicate_command_rejection_survive_protected_callback(self):
        source='''async fn raw(r,command){try{await r(command)}catch e{e.code}finally{await sleep(0)}};let f=CHOICE;
async with await robot_open("rig") as r{let o=await robot_observe(r);let command=robot_command(o,{pan:0,tilt:0});let receipt=await f(r,command);let duplicate=await f(r,command);[[receipt.status,receipt.targetVerified,command.used],duplicate,compile_flow_info(f).resumeCalls>0]}'''
        for compiled in (False,True):
            actual=source.replace('CHOICE','compile_function(raw,target="auto-flow")'if compiled else'raw');log=[]
            with robot_fixture.host(log)as app,streams.observe_states()as states:value=robot_fixture.run(app,actual)
            self.assertEqual(value,[['sent',False,True],'robot_command_used',compiled]);self.assertEqual([x[0]for x in log],['open','observe','act','close'])
            if compiled:real_protected(self,states)
            else:self.assertEqual(states,[])
            denied=[]
            with robot_fixture.host(denied)as app:self.assertEqual(robot_fixture.run(app,'try{'+actual+'}catch e{e.code}',caps=[]),'capability_error')
            self.assertEqual(denied,[])

    def test_outside_isolated_SDK_and_CLI_execute_same_protected_program(self):
        with patch.object(outside,'SOURCE',SOURCE),patch.object(outside,'EXPECTED',EXPECTED):
            outside.GeneratorFlowSurfaces208.test_outside_isolated_SDK_CLI_stream_consumers_use_no_model_or_hardware(self)

    def test_private_HTTP_executes_protected_program_in_memory_with_only_loopback(self):
        with patch.object(http_fixture,'SOURCE',SOURCE),patch.object(http_fixture,'EXPECTED',EXPECTED):
            http_fixture.AsyncIterationBoundaries210.test_private_HTTP_runs_same_async_consumer_without_project_database(self)
