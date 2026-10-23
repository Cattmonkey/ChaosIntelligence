"""Callable formation and method combination share every public language surface."""
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
from chaos.execution_callables import CALLABLE_FRAMES
from test_callable_execution_frames import WITNESS,WITNESS_EXPECTED
import test_class_frame_surfaces as previous
ROOT=Path(__file__).resolve().parents[1]
CALLABLE_NAMES={cls.__name__ for cls in CALLABLE_FRAMES}
ALL_NAMES=previous.ALL_NAMES|CALLABLE_NAMES
PREFIX='let callableResult=await program_from({"kind":"program","entry":"main.chaos","files":{"main.chaos":"var events=[];\\nfn note(label,value){events=append(events,label);value}\\nlet plain=fn(x:Int=note(\\"plain\\",40))->Int{x+2};\\nlet task=async fn(x=note(\\"async\\",41)){await sleep(0);x+1};\\nlet stream=fn*(x=note(\\"gen\\",42)){yield x};\\nlet asyncstream=async fn*(x=note(\\"asyncgen\\",42)){yield x};\\nfn named(x=note(\\"named\\",42)){x}\\nrec{fn even(n){if n==0{true}else{odd(n-1)}};fn odd(n){if n==0{false}else{even(n-1)}}};\\nagent declared{provider:\\"local\\",tools:[]};\\nlet built=function_from(code_from(\\"fn(x=seed){x+1}\\"),{seed:41});\\nvar work=generic_method(generic_function(\\"work\\"),[\\"Any\\"],fn(x){events=append(events,\\"base\\");x+1});\\nwork=generic_method(work,[\\"Int\\"],fn(next,x){events=append(events,\\"int\\");next()+1},with_next=true);\\nwork=generic_method(work,[\\"Any\\"],fn(x){events=append(events,\\"before\\");null},\\"before\\");\\nwork=generic_method(work,[\\"Any\\"],fn(x){events=append(events,\\"after\\");null},\\"after\\");\\nwork=generic_method(work,[\\"Any\\"],fn(next,x){events=append(events,\\"around\\");let value=next();events=append(events,\\"end\\");value},\\"around\\");\\n[plain(),await task(),collect(stream()),await acollect(asyncstream()),named(),even(4),built(),work(40),type_of(declared),events]"},"languageVersions":{"main.chaos":2}})(null);\n'
CALLABLE_EXPECTED=WITNESS_EXPECTED
assert previous.SOURCE.endswith(']\n')
SOURCE=PREFIX+previous.SOURCE[:-2]+',callableResult]\n'
EXPECTED=previous.EXPECTED+[CALLABLE_EXPECTED]
DOCUMENTED='''fn base(value=40){value+1}
let built=function_from(code_from("fn(value=seed){value+1}"),{seed:40});
let general=generic_method(generic_function("answer"),["Number"],base);
let answer=generic_method(general,["Int"],fn(next,value){next()+1},with_next=true);
[answer(40),built(),base()]
'''
DOCUMENTED_EXPECTED=[42,41,41]
FUNCTION='fn(values){let code=code_from("fn(value=seed){value+1}");let built=function_from(code,{seed:values[0]});let g=generic_method(generic_function("g"),["Int"],built);g(values[0])+values[1]}'
@contextmanager
def observe_callable_frames():
    seen={'frames':set(),'steps':{},'phases':{},'live':[]}
    with previous.observe_class_frames()as old,ExitStack()as stack:
        seen['previous']=old
        for cls in CALLABLE_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                name=type(frame).__name__;seen['frames'].add(name);seen['steps'][name]=seen['steps'].get(name,0)+1;seen['phases'].setdefault(name,set()).add(frame.phase);seen['live'].append(frame)
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield seen
class CallableFrameSurfaces(unittest.TestCase):
    run_source=previous.ClassFrameSurfaces.run_source
    def assert_all_frames(self,seen):
        previous.ClassFrameSurfaces.assert_all_frames(self,seen['previous'])
        self.assertEqual(seen['frames'],CALLABLE_NAMES);self.assertEqual(len(CALLABLE_NAMES),3);self.assertEqual(len(ALL_NAMES),114)
        self.assertTrue(all(value>0 for value in seen['steps'].values()))
    def test_source_graph_all114_actual_classes_preserve_original_twelve_results(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_callable_frames()as seen:
                value,events=self.run_source(SOURCE,mode=mode);self.assertEqual(value[:12],previous.EXPECTED);self.assertEqual(value[12],CALLABLE_EXPECTED);self.assert_all_frames(seen)
                self.assertEqual(sum(e['type']=='tool.completed'and e['payload'].get('tool')=='text.normalize'for e in events),1)
    def test_ir_code_program_macro_and_jit_keep_shared_callables(self):
        for version in (1,2):
            ir=parse_program(DOCUMENTED,language_version=version);text=format_program(ir)
            self.assertEqual(parse_program(text,language_version=version)['semanticHash'],ir['semanticHash'])
            definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':text},'languageVersions':{'main.chaos':version}}
            with self.subTest(version=version),observe_callable_frames()as seen:
                self.assertEqual(self.run_source('let p=program_from('+json.dumps(definition)+');await p(null)')[0],DOCUMENTED_EXPECTED);self.assertEqual(seen['frames'],CALLABLE_NAMES)
        macro='macro make(factory,code,value){quote{unquote(factory)(unquote(code),{seed:unquote(value)})}};let f=make!(function_from,code_from("fn(){seed+1}"),41);f()'
        with observe_callable_frames()as seen:self.assertEqual(self.run_source(macro)[0],42);self.assertIn('FunctionBuildFrame',seen['frames'])
        for target in ('interpreter','auto','auto-control','auto-flow'):
            source=FUNCTION.replace('fn(values','fn build(values')+';compile_function(build,target="'+target+'")([40,1])'
            with self.subTest(target=target),observe_callable_frames()as seen:self.assertEqual(self.run_source(source)[0],42);self.assertEqual(seen['frames'],CALLABLE_NAMES)
    def test_real_agent_tool_team_bot_use_formed_generic_functions_with_exact_effects(self):
        registry=Registry();effects=[];requests=[]
        @registry.tool('fixture.callable.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.callable.provider')
        def provider(request,context):context.checkpoint();requests.append((request.input,request.step));return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        team={'kind':'team','name':'callable team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.callable.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.callable.audit']}}}]}
        source='''let trace=context_var("trace","root");
let generic=generic_method(generic_function("work"),["Int"],async fn(value){with context_bind([{variable:trace,value:"agent"}])as scope{tool("fixture.callable.audit",[value,trace.get()]);value+1}});
agent helper{provider:"fixture.callable.provider",tools:["fixture.callable.audit"]};
let bound=agent_bind(helper,[agent_tool("work",generic,capabilities=["fixture.callable.audit"])]);
async fn role(value){with context_bind([{variable:trace,value:"team"}])as scope{tool("fixture.callable.audit",[value,trace.get()]);let f=function_from(code_from("fn(){seed+1}"),{seed:value});f()}}
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(plan,state){let first=await bound(plan);let second=await crew(first);[first,second,trace.get()]}
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}
let worker=bot("callable language",fn(state){state.input},execute,verify);(await worker(40)).output'''.replace('TEAM',json.dumps(team))
        with observe_callable_frames()as seen:value,events=self.run_source(source,registry=registry,capabilities=['fixture.callable.audit'])
        self.assertEqual(value,[41,42,'root']);self.assertEqual(effects,[[40,'agent'],[41,'team']]);self.assertEqual(requests,[(40,1),(40,2)])
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1);self.assertEqual(sum(e['type']=='team.role.started'for e in events),1);self.assertEqual(sum(e['type']=='tool.completed'for e in events),2);self.assertEqual(seen['frames'],CALLABLE_NAMES)
    def test_real_pending_agent_default_completes_or_cancels_without_leaked_worker(self):
        for cancelled in (False,True):
            entered=threading.Event();release=threading.Event();ended=threading.Event();calls=[];registry=Registry()
            @registry.provider('fixture.callable.pending')
            def pending_provider(request,context):
                calls.append(request.input);entered.set()
                try:
                    while not release.wait(.005):context.checkpoint()
                    context.checkpoint();return Final(42)
                finally:ended.set()
            source='agent worker{provider:"fixture.callable.pending",tools:[],maxSteps:1};async fn build(){fn selected(value=worker(1)){value};selected()};await build()'
            with self.subTest(cancelled=cancelled),Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app:
                project=app.create_project('pending-callable',source=source,capabilities=[])
                with observe_callable_frames()as seen:
                    run=app.start(project['id'],project['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(4));self.assertTrue(any(type(frame).__name__=='CallableSyntaxFrame'and frame.phase=='default'for frame in seen['live']))
                        if cancelled:run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled')
                        else:release.set();self.assertEqual(run.result(5),42)
                        self.assertTrue(ended.is_set())
                    finally:release.set();run.cancel()
                self.assertEqual(calls,[1])
    def test_http_sdk_cli_preserve_thirteen_results_and_current_program_snapshot(self):
        with Framework(':memory:',settings=Settings(':memory:'))as app,tempfile.TemporaryDirectory()as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));project=client.create_project('callable-http',source=SOURCE,capabilities=['text.normalize'])
                with observe_callable_frames()as seen:self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(20),EXPECTED)
                self.assert_all_frames(seen);self.assertEqual(client.get_project(project['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE);env={k:v for k,v in os.environ.items()if not any(word in k.upper()for word in ('MODEL','PROVIDER','KEY','TOKEN'))};env.update(CHAOS_CONFIG=str(Path(folder)/'settings.json'),PYTHONPATH=str(ROOT))
                for command in ('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=20);self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command in ('fmt','ir'):self.assertEqual(parse_program(result.stdout)['semanticHash']if command=='fmt'else json.loads(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)
    def test_editor_analysis_formatting_and_documentation_never_invoke_defaults_or_methods(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('authoring executed callable body')):report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'],[]);self.assertTrue(compiled);self.assertEqual(format_source(formatted),formatted);self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash']);self.assertEqual((ROOT/'examples/callable_frames.chaos').read_text(),DOCUMENTED)
        with observe_callable_frames()as seen:self.assertEqual(self.run_source(DOCUMENTED)[0],DOCUMENTED_EXPECTED);self.assertEqual(seen['frames'],CALLABLE_NAMES)
    def test_module_match_existing_class_and_cold_handles_share_same_callable_values(self):
        source='let p=program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"fn work(value=40){value+2};export{work}"},languageVersions:{"main.chaos":2}});let m=module_from(p);let g=generic_method(generic_function("work"),["Int"],m.work);[g(40),match m.work(){42=>true;_=>false}]'
        with observe_callable_frames()as seen:self.assertEqual(self.run_source(source)[0],[42,True]);self.assertTrue({'CallableSyntaxFrame','GenericResolveFrame'}<=seen['frames'])
