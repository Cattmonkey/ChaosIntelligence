"""One module algorithm family at real authoring and execution boundaries."""
import json,os,subprocess,sys,tempfile,threading,time,unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from chaos import Client,Final,Framework,Registry,Settings,ToolCall,compile_program
from chaos.ide_language import analyze
from chaos.language import Evaluator
from chaos.modules import ProgramBundle
from chaos import module_values
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
from chaos.server import make_server
from chaos.execution_modules import MODULE_FRAMES
from test_module_execution_frames import (WITNESS,WITNESS_EXPECTED,definition,literal,observe_module_entries)
import test_pattern_frame_surfaces as previous
ROOT=Path(__file__).resolve().parents[1]
MODULE_NAMES={cls.__name__ for cls in MODULE_FRAMES}
ALL_NAMES=previous.ALL_NAMES|MODULE_NAMES
PREFIX='let moduleResult=await program_from('+json.dumps(definition({'entry.chaos':WITNESS}))+')(null);\n'
assert previous.SOURCE.endswith(']\n')
SOURCE=PREFIX+previous.SOURCE[:-2]+',moduleResult]\n'
EXPECTED=previous.EXPECTED+[WITNESS_EXPECTED]
EXPORT_SOURCE='fn solve(value){process_step(value,value+2,true)};export{solve}'
EXPORT_MODULE='main.chaos'
EXPORT_NAME='solve'
EXPORT_INPUT=40
EXPORT_EXPECTED={'state':40,'output':42,'done':True}
DOCUMENTED='let definition={"kind": "program", "entry": "main.chaos", "files": {"main.chaos": "import \\"lib.chaos\\" as L;fn solve(x){L.add(x)};export{solve}", "lib.chaos": "fn add(x){x+2};export{add}"}, "languageVersions": {"main.chaos": 2, "lib.chaos": 2}};\nlet library=module_from(program_from(definition));\nlibrary.solve(40)\n'
@contextmanager
def observe_module_frames():
    with previous.observe_pattern_frames()as old,observe_module_entries()as counts:
        yield {'previous':old,'steps':counts}
def export_probe():
    return Evaluator().run_export(ProgramBundle(EXPORT_SOURCE),EXPORT_MODULE,EXPORT_NAME,EXPORT_INPUT)

class ModuleFrameSurfaces(unittest.TestCase):
    run_source=previous.PatternFrameSurfaces.run_source
    def assert_all_frames(self,seen):
        previous.PatternFrameSurfaces.assert_all_frames(self,seen['previous'])
        self.assertEqual(set(seen['steps']),MODULE_NAMES);self.assertEqual(len(MODULE_NAMES),6);self.assertEqual(len(ALL_NAMES),121);self.assertTrue(all(seen['steps'].values()))

    def test_composite_source_graph_and_real_export_entry_observe121_classes_and_fifteen_results(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_module_frames()as seen:
                result,events=self.run_source(SOURCE,mode=mode);self.assertEqual(result[:14],previous.EXPECTED);self.assertEqual(result[14],WITNESS_EXPECTED)
                self.assertEqual(export_probe(),EXPORT_EXPECTED);self.assert_all_frames(seen)
                self.assertEqual(sum(row['type']=='tool.completed'and row['payload'].get('tool')=='text.normalize'for row in events),1)

    def test_ir_code_program_hygienic_module_macros_and_jit_keep_same_algorithms(self):
        for version in (1,2):
            ir=parse_program(DOCUMENTED,language_version=version);text=format_program(ir)
            self.assertEqual(parse_program(text,language_version=version)['semanticHash'],ir['semanticHash'])
            with observe_module_frames()as seen:self.assertEqual(self.run_source(text)[0],42);self.assertIn('ModuleConstructFrame',seen['steps']);self.assertIn('ModuleImportFrame',seen['steps'])
        macro='macro build(ctor,value){quote{unquote(ctor)(unquote(value))}};let d='+json.dumps(definition({'entry.chaos':'let answer=42;export{answer}'}))+';build!(module_from,program_from(d)).answer'
        with observe_module_frames()as seen:self.assertEqual(self.run_source(macro)[0],42);self.assertIn('ModuleLoadFrame',seen['steps'])
        source='fn load(p){module_from(p).answer};compile_function(load,target=TARGET)(program_from('+json.dumps(definition({'entry.chaos':'let answer=42;export{answer}'}))+'))'
        for target in ('interpreter','auto','auto-control','auto-flow'):
            with self.subTest(target=target),observe_module_frames()as seen:self.assertEqual(self.run_source(source.replace('TARGET',json.dumps(target)))[0],42);self.assertIn('ModuleConstructFrame',seen['steps'])
        code='let f=function_from(code_from("fn(p){module_from(p).answer}"));f(program_from('+json.dumps(definition({'entry.chaos':'let answer=42;export{answer}'}))+'))'
        with observe_module_frames()as seen:self.assertEqual(self.run_source(code)[0],42);self.assertIn('ModuleLoadFrame',seen['steps'])

    def test_actual_agent_tool_team_bot_callbacks_use_exported_module_functions(self):
        registry=Registry();effects=[];requests=[]
        @registry.tool('fixture.module.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.module.provider')
        def provider(request,context):context.checkpoint();requests.append(request.step);return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        files={'entry.chaos':'import "lib.chaos" as L;async fn work(value){tool("fixture.module.audit",value);L.add(value)};export{work}','lib.chaos':'fn add(value){value+2};export{add}'}
        team={'kind':'team','name':'module team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.module.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.module.audit']}}}]}
        source='let library=module_from('+literal(files)+');'+'''agent helper{provider:"fixture.module.provider",tools:["fixture.module.audit"]};let bound=agent_bind(helper,[agent_tool("work",library.work,capabilities=["fixture.module.audit"])]);let crew=team_bind(team_from(TEAM),{work:library.work});async fn execute(plan,state){let first=await bound(40);let second=await crew(40);[first,second]}fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}let worker=bot("module callback",fn(state){state.input},execute,verify);(await worker(null)).output'''.replace('TEAM',json.dumps(team))
        with observe_module_frames()as seen:result,events=self.run_source(source,registry=registry,capabilities=['fixture.module.audit'])
        self.assertEqual(result,[42,42]);self.assertEqual(effects,[40,40]);self.assertEqual(requests,[1,2]);self.assertIn('ModuleConstructFrame',seen['steps']);self.assertIn('ModuleImportFrame',seen['steps'])
        self.assertEqual(sum(row['type']=='agent.language_tool'for row in events),1);self.assertEqual(sum(row['type']=='team.role.started'for row in events),1)

    def test_framework_process_real_host_calls_module_export_frame_and_keeps_selected_module(self):
        files={'turn.chaos':'import "lib.chaos" as L;async fn step(c){await sleep(0);process_step(c.state+1,L.answer,true)};export{step}','lib.chaos':'let answer=42;export{answer}'}
        process={'kind':'process','name':'module turn','module':'turn.chaos','export':'step','intervalSeconds':None,'maxTurns':1}
        with Framework(':memory:',settings=Settings(':memory:'))as app,observe_module_frames()as seen:
            project=app.create_project('module-process',source='throw "entry initializer must not run"',files=files,capabilities=['process.manage'])
            app.create_process(project['id'],project['version'],'turn',process,state=0)
            deadline=time.monotonic()+5;result=None
            while time.monotonic()<deadline:
                result=app.get_process(project['id'],'turn')
                if result['status']in('completed','failed','paused'):break
                time.sleep(.01)
            self.assertEqual(result['status'],'completed',result);self.assertEqual(result['state'],1);self.assertEqual(result['output'],42)
            self.assertGreater(seen['steps'].get('ModuleExportFrame',0),0);self.assertGreater(seen['steps'].get('LegacyModuleLoadFrame',0),0)
            self.assertEqual(app.get_project(project['id'])['source'],'throw "entry initializer must not run"')

    def test_http_sdk_cli_run_same_closed_source_without_mutating_snapshot(self):
        with Framework(':memory:',settings=Settings(':memory:'))as app,tempfile.TemporaryDirectory()as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));project=client.create_project('module-http',source=SOURCE,capabilities=['text.normalize'])
                with observe_module_frames()as seen:self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(20),EXPECTED);self.assertEqual(export_probe(),EXPORT_EXPECTED)
                self.assert_all_frames(seen);self.assertEqual(client.get_project(project['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE);env={k:v for k,v in os.environ.items()if not any(word in k.upper()for word in('MODEL','PROVIDER','KEY','TOKEN'))};env.update(CHAOS_CONFIG=str(Path(folder)/'settings.json'),PYTHONPATH=str(ROOT))
                for command in('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=20);self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command=='fmt':self.assertEqual(parse_program(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                    if command=='ir':self.assertEqual(json.loads(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_ide_lsp_format_compile_and_documented_example_do_not_initialize_modules(self):
        with patch.object(Evaluator,'load_module',side_effect=AssertionError('authoring executed module')),patch.object(module_values.Scope,'load',side_effect=AssertionError('authoring executed module')):
            report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertFalse([row for row in report['diagnostics']if row['severity']=='error']);self.assertTrue(compiled)
        self.assertEqual(format_source(formatted),formatted);self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash']);self.assertEqual((ROOT/'examples/module_frames.chaos').read_text(),DOCUMENTED)
        with observe_module_frames()as seen:self.assertEqual(self.run_source(DOCUMENTED)[0],42);self.assertIn('ModuleConstructFrame',seen['steps'])
