"""Real source/IR/SDK and function-based agent/robot entry proposals; no model network."""
import json,unittest
from unittest.mock import patch
from chaos import Framework,Registry,Settings,ToolCall,Final,compile_program
from chaos.language import Evaluator
from chaos.modules import ProgramBundle
from chaos.ide_language import analyze
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
from test_suspension_syntax_frames201 import WITNESS,EXPECTED,observe
import test_module_frame_surfaces as module_surfaces
import test_robot_values as robot_fixture

class SuspensionSyntaxSurfaces201(unittest.TestCase):
    run_source=module_surfaces.ModuleFrameSurfaces.run_source

    def test_source_ir_and_sdk_use_real_allfive_algorithms(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe()as seen:
                result,events=self.run_source(WITNESS,mode=mode);self.assertEqual(result,EXPECTED)
                self.assertEqual(set(seen['steps']),{'await','yield','yield_from','throw','rethrow'});self.assertTrue(all(seen['steps'].values()))
        ir=parse_program(WITNESS);self.assertEqual(parse_program(format_program(ir))['semanticHash'],ir['semanticHash'])

    def test_static_ide_format_compile_do_not_execute_suspension_or_robot(self):
        with patch.object(Evaluator,'_s_execute',side_effect=AssertionError('authoring must not execute runtime')),patch.object(Evaluator,'run',side_effect=AssertionError('authoring must not execute a program')):
            report=analyze(WITNESS);text=format_source(WITNESS);plan=compile_program(WITNESS)
        self.assertFalse([row for row in report['diagnostics']if row['severity']=='error']);self.assertTrue(plan)
        self.assertEqual(format_source(text),text);self.assertEqual(parse_program(text)['semanticHash'],parse_program(WITNESS)['semanticHash'])

    def test_agenttool_team_and_bot_actual_async_callbacks_keep_functional_decisions(self):
        calls=[];registry=Registry()
        @registry.provider('fixture.suspension201')
        def provider(request,context):
            context.checkpoint();calls.append(request.step)
            return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        team={'kind':'team','name':'suspension team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='async fn work(value){await sleep(0);value+2};agent helper{provider:"fixture.suspension201",tools:[]};let bound=agent_bind(helper,[agent_tool("work",work)]);let crew=team_bind(team_from(TEAM),{work:work});async fn execute(plan,state){[await bound(40),await crew(40)]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};let worker=bot("functional suspension",fn(state){state.input},execute,verify);(await worker(null)).output'.replace('TEAM',json.dumps(team))
        with observe()as seen:result,events=self.run_source(source,registry=registry,capabilities=[])
        self.assertEqual(result,[42,42]);self.assertEqual(calls,[1,2]);self.assertGreater(seen['steps'].get('await',0),0)
        self.assertEqual(sum(row['type']=='agent.language_tool'for row in events),1);self.assertEqual(sum(row['type']=='team.role.started'for row in events),1)

    def test_robot_software_entry_consumes_admission_once_without_claiming_target_verified(self):
        log=[]
        source='async with await robot_open("rig") as r{let o=await robot_observe(r);let command=robot_command(o,{pan:0,tilt:0});let receipt=await r(command);let retried=try{await r(command)}catch e{e.code};[receipt.status,receipt.targetVerified,command.used,retried]}'
        with robot_fixture.host(log)as app,observe()as seen:result=robot_fixture.run(app,source)
        self.assertEqual(result,['sent',False,True,'robot_command_used']);self.assertEqual(sum(row[0]=='act'for row in log),1)
        self.assertEqual([row[0]for row in log],['open','observe','act','close']);self.assertGreater(seen['steps'].get('await',0),0)

    def test_actual_public_process_export_awaits_and_validates_full_step_record(self):
        source='async fn solve(value){await sleep(0);process_step(value,value+2,true)};export{solve}'
        with observe()as seen:result=Evaluator().run_export(ProgramBundle(source),'main.chaos','solve',40)
        self.assertEqual(result,{'state':40,'output':42,'done':True});self.assertGreater(seen['steps'].get('await',0),0)
