"""One class/meta/decorator language across source, IR, agents and deployed consumers."""
from contextlib import ExitStack,contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from chaos import Client,Final,Framework,Registry,Settings,ToolCall,compile_program
from chaos.ide_language import analyze
from chaos.language import Evaluator
from chaos.program import parse_program,format_program
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.execution_class_creation import CLASS_FRAMES
from test_class_execution_frames import WITNESS,WITNESS_EXPECTED
import test_hash_set_frame_surfaces as previous

ROOT=Path(__file__).resolve().parents[1]
CLASS_NAMES={cls.__name__ for cls in CLASS_FRAMES}
ALL_NAMES=previous.ALL_NAMES|CLASS_NAMES
PREFIX='let classResult=do{\n'+WITNESS+'\n};\n'
CLASS_EXPECTED=WITNESS_EXPECTED
assert previous.SOURCE.endswith(']\n')
SOURCE=PREFIX+previous.SOURCE[:-2]+',classResult]\n'
EXPECTED=previous.EXPECTED+[CLASS_EXPECTED]
DOCUMENTED='''fn identity(value){value}
class Meta(Type){fn __new__(m,name,bases,namespace){
  Type.__new__(m,name,bases,set(namespace,"answer",class_member(42)))
}}
@identity
class Component(metaclass=Meta){fn own(self){Component}}
let Dynamic=new_class("Dynamic",[],class_members(Component),{metaclass:Meta});
[Component.answer,Dynamic.answer,Component().own()==Component,isinstance(Component,Meta)]
'''
DOCUMENTED_EXPECTED=[42,42,True,True]
FUNCTION='fn(values){class M(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,ns)}}class Box(metaclass=M){var value=40;fn answer(s,n){s.value+n}}Box().answer(values[1])}'

@contextmanager
def observe_class_frames():
    seen={'frames':set(),'steps':{},'phases':{},'live':[],'cursors':set()}
    with previous.observe_collection_frames()as old,ExitStack()as stack:
        seen['previous']=old
        for cls in CLASS_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                name=type(frame).__name__;seen['frames'].add(name);seen['live'].append(frame);seen['cursors'].add(id(cursor))
                seen['steps'][name]=seen['steps'].get(name,0)+1;seen['phases'].setdefault(name,set()).add(frame.phase)
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield seen


class ClassFrameSurfaces(unittest.TestCase):
    run_source=previous.HashSetFrameSurfaces.run_source
    def assert_all_frames(self,seen):
        previous.HashSetFrameSurfaces.assert_all_frames(self,seen['previous'])
        self.assertEqual(seen['frames'],CLASS_NAMES);self.assertEqual(len(seen['frames']),21)
        self.assertEqual(seen['frames']|previous.ALL_NAMES,ALL_NAMES);self.assertEqual(len(ALL_NAMES),111)
        self.assertTrue(all(value>0 for value in seen['steps'].values()))

    def test_source_graph_and_deployment_execute_all111_actual_frame_classes(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_class_frames()as seen:
                value,events=self.run_source(SOURCE,mode=mode)
                self.assertEqual(value[:11],previous.EXPECTED);self.assertEqual(value[11],CLASS_EXPECTED)
                self.assert_all_frames(seen)
                self.assertEqual(sum(e['type']=='tool.completed'and e['payload'].get('tool')=='text.normalize'for e in events),1)

    def test_both_ir_dialects_code_program_macros_and_compilation_share_class_frames(self):
        for version in (1,2):
            ir=parse_program(DOCUMENTED,language_version=version);text=format_program(ir)
            self.assertEqual(parse_program(text,language_version=version)['semanticHash'],ir['semanticHash'])
            definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':text},'languageVersions':{'main.chaos':version}}
            with self.subTest(version=version),observe_class_frames()as seen:
                self.assertEqual(self.run_source('let p=program_from('+json.dumps(definition)+');await p(null)')[0],DOCUMENTED_EXPECTED)
                self.assertTrue({'ClassDeclarationFrame','DecoratedDeclarationFrame','TypeNewFrame','MetaclassCreateFrame'}<=seen['frames'])
        sources=['let build=function_from(code_from('+json.dumps(FUNCTION)+'));build([40,2])',
                 'macro make(value){quote{class Local{let answer=unquote(value)};Local.answer}};make!(42)']
        for source in sources:
            with self.subTest(source=source),observe_class_frames()as seen:
                self.assertEqual(self.run_source(source)[0],42);self.assertIn('ClassDeclarationFrame',seen['frames'])
        for target in ('interpreter','auto','auto-control','auto-flow'):
            source=FUNCTION.replace('fn(values','fn build(values')+';compile_function(build,target="'+target+'")([40,2])'
            with self.subTest(target=target),observe_class_frames()as seen:
                self.assertEqual(self.run_source(source)[0],42);self.assertIn('ClassCallFrame',seen['frames'])

    def test_real_agent_tool_team_bot_and_dynamic_meta_keep_context_and_exact_effects(self):
        registry=Registry();effects=[];provider_steps=[]
        @registry.tool('fixture.class.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.class.provider')
        def provider(request,context):
            context.checkpoint();provider_steps.append((request.input,request.step))
            return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        team={'kind':'team','name':'class team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.class.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.class.audit']}}}]}
        source='''let trace=context_var("trace","root");
async fn work(value){with context_bind([{variable:trace,value:"agent"}])as scope{
 class Meta(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,ns)}}
 class Box(metaclass=Meta){fn __call__(s,x){tool("fixture.class.audit",[x,trace.get()]);x+1}};Box()(value)}}
agent helper{provider:"fixture.class.provider",tools:["fixture.class.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.class.audit"])]);
async fn role(value){with context_bind([{variable:trace,value:"team"}])as scope{
 let Dynamic=new_class("Dynamic",[],{answer:class_member(value+1)});tool("fixture.class.audit",[value,trace.get()]);Dynamic.answer}}
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(plan,state){let first=await bound(plan);let second=await crew(first);[first,second,trace.get()]}
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}
let worker=bot("class language",fn(state){state.input},execute,verify);(await worker(40)).output'''.replace('TEAM',json.dumps(team))
        with observe_class_frames()as seen:value,events=self.run_source(source,registry=registry,capabilities=['fixture.class.audit'])
        self.assertEqual(value,[41,42,'root']);self.assertEqual(effects,[[40,'agent'],[41,'team']]);self.assertEqual(provider_steps,[(40,1),(40,2)])
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1);self.assertEqual(sum(e['type']=='team.role.started'for e in events),1)
        self.assertEqual(sum(e['type']=='tool.completed'for e in events),2)
        self.assertTrue({'ClassDeclarationFrame','MetaclassCreateFrame','TypeNewFrame'}<=seen['frames'])
        self.assertIn('BotCommitFrame',seen['previous']['previous']['previous']['frames'])

    def test_real_pending_metaclass_agent_completes_or_cancels_without_leaked_work(self):
        for cancelled in (False,True):
            entered=threading.Event();release=threading.Event();ended=threading.Event();calls=[];registry=Registry()
            @registry.provider('fixture.class.pending')
            def pending(request,context):
                calls.append(request.input);entered.set()
                try:
                    while not release.wait(.005):context.checkpoint()
                    context.checkpoint();return Final(42)
                finally:ended.set()
            source='agent worker{provider:"fixture.class.pending",tools:[],maxSteps:1};class M(Type){fn __new__(m,n,b,ns){worker(1);Type.__new__(m,n,b,ns)}}async fn work(){class C(metaclass=M){let answer=42};C.answer}await work()'
            with self.subTest(cancelled=cancelled),Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app:
                project=app.create_project('pending-class',source=source,capabilities=[])
                with observe_class_frames()as seen:
                    run=app.start(project['id'],project['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(4));self.assertTrue(any(type(frame).__name__=='ClassDeclarationFrame'for frame in seen['live']))
                        if cancelled:run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled')
                        else:release.set();self.assertEqual(run.result(5),42)
                        self.assertTrue(ended.is_set())
                    finally:release.set();run.cancel()
                self.assertEqual(calls,[1])

    def test_http_sdk_cli_and_twelve_result_fields_preserve_all_old_results(self):
        with Framework(':memory:',settings=Settings(':memory:'))as app,tempfile.TemporaryDirectory()as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));project=client.create_project('class-http',source=SOURCE,capabilities=['text.normalize'])
                with observe_class_frames()as seen:self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(20),EXPECTED)
                self.assert_all_frames(seen);self.assertEqual(client.get_project(project['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE)
                env={k:v for k,v in os.environ.items()if not any(word in k.upper()for word in ('MODEL','PROVIDER','KEY','TOKEN'))};env.update(CHAOS_CONFIG=str(Path(folder)/'settings.json'),PYTHONPATH=str(ROOT))
                for command in ('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=20)
                    self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command in ('fmt','ir'):
                        semantic=parse_program(result.stdout)['semanticHash']if command=='fmt'else json.loads(result.stdout)['semanticHash']
                        self.assertEqual(semantic,parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_editor_analysis_formatting_and_documented_example_never_run_hooks(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('authoring executed class callback')):
            report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'],[]);self.assertTrue(compiled);self.assertEqual(format_source(formatted),formatted)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash'])
        self.assertEqual((ROOT/'examples/class_frames.chaos').read_text(),DOCUMENTED)
        with observe_class_frames()as seen:self.assertEqual(self.run_source(DOCUMENTED)[0],DOCUMENTED_EXPECTED)
        self.assertTrue({'ClassDeclarationFrame','DecoratedDeclarationFrame','TypeNewFrame'}<=seen['frames'])

    def test_generators_channels_and_generic_combination_interoperate_without_claimed_migration(self):
        source='''class M(Type){}fn* classes(){yield new_class("First",[],{answer:class_member(40)},{metaclass:M});yield new_class("Second",[],{answer:class_member(42)},{metaclass:M})}
let values=collect(classes());let ch=channel(1);await channel_send(ch,values[1]);let selected=(await channel_receive(ch)).value;channel_close(ch);
let reflect=generic_method(generic_function("reflect"),[M],fn(value){value.answer});
[values[0].answer,selected.answer,isinstance(selected,M),class_of(selected)==M,reflect(selected)]'''
        with observe_class_frames()as seen:self.assertEqual(self.run_source(source)[0],[40,42,True,True,42])
        self.assertTrue({'MetaclassCreateFrame','ClassCallFrame','TypeCheckFrame'}<=seen['frames'])
