"""The same four pending pattern forms across actual public language surfaces."""
from contextlib import ExitStack,contextmanager
import json,os
from pathlib import Path
import subprocess,sys,tempfile,threading,unittest
from unittest.mock import patch
from chaos import Client,Final,Framework,Registry,Settings,ToolCall,compile_program
from chaos.ide_language import analyze
from chaos.language import Evaluator
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
from chaos.server import make_server
from chaos.execution_patterns import PATTERN_FRAMES
from test_pattern_execution_frames import WITNESS,WITNESS_EXPECTED
import test_callable_frame_surfaces as previous
ROOT=Path(__file__).resolve().parents[1]
PATTERN_NAMES={cls.__name__ for cls in PATTERN_FRAMES}
ALL_NAMES=previous.ALL_NAMES|PATTERN_NAMES
PREFIX='let patternResult=await program_from({"kind":"program","entry":"main.chaos","files":{"main.chaos":"var events=[];\\nfn note(label,value){events=append(events,label);value}\\nlet [left,{point:right,..rest},..tail]=note(\\"let\\",[20,{point:22,extra:9},7]);\\nvar {x:a,y:b}=note(\\"var\\",{x:1,y:2});\\n[a,b]=note(\\"assign\\",[b,a]);\\nlet answer=match note(\\"subject\\",{value:[left,right]}){\\n {value:[x,y]} if note(\\"guard0\\",false)=>0;\\n {value:[x,y]} if note(\\"guard1\\",x+y==42)=>note(\\"body\\",x+y);\\n _=>-1};\\n[answer,left,right,rest,tail,a,b,events]"},"languageVersions":{"main.chaos":2}})(null);\n'
PATTERN_EXPECTED=WITNESS_EXPECTED
assert previous.SOURCE.endswith(']\n')
SOURCE=PREFIX+previous.SOURCE[:-2]+',patternResult]\n'
EXPECTED=previous.EXPECTED+[PATTERN_EXPECTED]
DOCUMENTED='''var [left,right]=[20,22];
[left,right]=[right,left];
match {pair:[left,right]} {
  {pair:[a,b]} if a>0 => a+b;
  _ => 0
}
'''
FUNCTION='fn(values){var [a,b]=values;[a,b]=[b,a];match [a,b]{[x,y] if x>0=>x+y;_=>0}}'
@contextmanager
def observe_pattern_frames():
    seen={'frames':set(),'steps':{},'phases':{},'live':[]}
    with previous.observe_callable_frames()as old,ExitStack()as stack:
        seen['previous']=old
        for cls in PATTERN_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                name=type(frame).__name__;seen['frames'].add(name);seen['steps'][name]=seen['steps'].get(name,0)+1;seen['phases'].setdefault(name,set()).add(frame.phase);seen['live'].append(frame)
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield seen
class PatternFrameSurfaces(unittest.TestCase):
    run_source=previous.CallableFrameSurfaces.run_source
    def assert_all_frames(self,seen):
        previous.CallableFrameSurfaces.assert_all_frames(self,seen['previous'])
        self.assertEqual(seen['frames'],PATTERN_NAMES);self.assertEqual(PATTERN_NAMES,{'PatternSyntaxFrame'});self.assertEqual(len(ALL_NAMES),115)
        self.assertTrue(all(value>0 for value in seen['steps'].values()))

    def test_source_graph_all115_actual_classes_preserve_previous_thirteen_results(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_pattern_frames()as seen:
                value,events=self.run_source(SOURCE,mode=mode);self.assertEqual(value[:13],previous.EXPECTED);self.assertEqual(value[13],PATTERN_EXPECTED);self.assert_all_frames(seen)
                self.assertEqual(sum(e['type']=='tool.completed'and e['payload'].get('tool')=='text.normalize'for e in events),1)

    def test_ir_code_program_macros_and_jit_share_actual_pattern_frame(self):
        for version in (1,2):
            ir=parse_program(DOCUMENTED,language_version=version);text=format_program(ir);self.assertEqual(parse_program(text,language_version=version)['semanticHash'],ir['semanticHash'])
            definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':text},'languageVersions':{'main.chaos':version}}
            with self.subTest(version=version),observe_pattern_frames()as seen:
                self.assertEqual(self.run_source('let p=program_from('+json.dumps(definition)+');await p(null)')[0],42);self.assertEqual(seen['frames'],PATTERN_NAMES)
        macro='macro calculate(value){quote{match unquote(value){[a,b]=>a+b;_=>0}}};calculate!([20,22])'
        with observe_pattern_frames()as seen:self.assertEqual(self.run_source(macro)[0],42);self.assertEqual(seen['frames'],PATTERN_NAMES)
        code='let f=function_from(code_from('+json.dumps(FUNCTION)+'));f([20,22])'
        with observe_pattern_frames()as seen:self.assertEqual(self.run_source(code)[0],42);self.assertEqual(seen['frames'],PATTERN_NAMES)
        for target in ('interpreter','auto','auto-control','auto-flow'):
            source=FUNCTION.replace('fn(values','fn build(values')+';compile_function(build,target="'+target+'")([20,22])'
            with self.subTest(target=target),observe_pattern_frames()as seen:self.assertEqual(self.run_source(source)[0],42);self.assertEqual(seen['frames'],PATTERN_NAMES)

    def test_agent_tool_team_and_bot_use_patterns_as_native_function_capabilities(self):
        registry=Registry();effects=[];requests=[]
        @registry.tool('fixture.pattern.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.pattern.provider')
        def provider(request,context):context.checkpoint();requests.append((request.input,request.step));return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        team={'kind':'team','name':'pattern team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.pattern.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.pattern.audit']}}}]}
        source='''async fn work(value){let {pair:[a,b]}=value;var [x,y]=[a,b];[x,y]=[y,x];match [x,y]{[p,q] if p>0=>do{tool("fixture.pattern.audit",[p,q]);p+q};_=>0}}
agent helper{provider:"fixture.pattern.provider",tools:["fixture.pattern.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.pattern.audit"])]);
let crew=team_bind(team_from(TEAM),{work:work});
async fn execute(plan,state){let first=await bound({pair:[20,22]});let second=await crew({pair:[21,21]});[first,second]}
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}
let worker=bot("native pattern",fn(state){state.input},execute,verify);(await worker(null)).output'''.replace('TEAM',json.dumps(team))
        with observe_pattern_frames()as seen:value,events=self.run_source(source,registry=registry,capabilities=['fixture.pattern.audit'])
        self.assertEqual(value,[42,42]);self.assertEqual(effects,[[22,20],[21,21]]);self.assertEqual(requests,[({'pair':[20,22]},1),({'pair':[20,22]},2)])
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1);self.assertEqual(sum(e['type']=='team.role.started'for e in events),1);self.assertEqual(seen['frames'],PATTERN_NAMES)

    def test_pending_agent_rhs_finishes_or_cancels_without_replay_or_worker_leak(self):
        for cancelled in (False,True):
            entered=threading.Event();release=threading.Event();ended=threading.Event();calls=[];registry=Registry()
            @registry.provider('fixture.pattern.pending')
            def provider(request,context):
                calls.append(request.input);entered.set()
                try:
                    while not release.wait(.005):context.checkpoint()
                    context.checkpoint();return Final([42])
                finally:ended.set()
            source='agent worker{provider:"fixture.pattern.pending",tools:[],maxSteps:1};async fn execute(){let [answer]=worker(1);answer};await execute()'
            with self.subTest(cancelled=cancelled),Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app:
                project=app.create_project('pending-pattern',source=source,capabilities=[])
                with observe_pattern_frames()as seen:
                    run=app.start(project['id'],project['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(4));self.assertTrue(any(type(frame).__name__=='PatternSyntaxFrame'and frame.phase=='rhs'for frame in seen['live']))
                        if cancelled:run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled')
                        else:release.set();self.assertEqual(run.result(5),42)
                        self.assertTrue(ended.is_set())
                    finally:release.set();run.cancel()
                self.assertEqual(calls,[1])

    def test_http_sdk_cli_keep_fourteen_results_and_source_snapshot(self):
        with Framework(':memory:',settings=Settings(':memory:'))as app,tempfile.TemporaryDirectory()as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));project=client.create_project('pattern-http',source=SOURCE,capabilities=['text.normalize'])
                with observe_pattern_frames()as seen:self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(20),EXPECTED)
                self.assert_all_frames(seen);self.assertEqual(client.get_project(project['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE);env={k:v for k,v in os.environ.items()if not any(word in k.upper()for word in ('MODEL','PROVIDER','KEY','TOKEN'))};env.update(CHAOS_CONFIG=str(Path(folder)/'settings.json'),PYTHONPATH=str(ROOT))
                for command in ('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=20);self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command in ('fmt','ir'):self.assertEqual(parse_program(result.stdout)['semanticHash']if command=='fmt'else json.loads(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_ide_analysis_formatting_and_documented_example_remain_inert(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('authoring executed matcher or function')):report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'],[]);self.assertTrue(compiled);self.assertEqual(format_source(formatted),formatted);self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash']);self.assertEqual((ROOT/'examples/pattern_frames.chaos').read_text(),DOCUMENTED)
        with observe_pattern_frames()as seen:self.assertEqual(self.run_source(DOCUMENTED)[0],42);self.assertEqual(seen['frames'],PATTERN_NAMES)
