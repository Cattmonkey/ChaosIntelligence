"""Original mutable/access protocols through actual compiled public entrypaths."""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from chaos import Framework, Registry, Settings, ToolCall, Final, compile_artifact, load_artifact
from chaos.execution_access import ObjectAccessEvaluationFrame, ItemKeyFrame
from chaos.execution_class_creation import ClassSyntaxEvaluationFrame
from chaos.flow_abi import EventTable
from chaos.language import Evaluator
from chaos.native_jit import BackendUnavailable
from chaos.program import parse_program, format_program
from chaos.source_format import format_source
from chaos.ide_language import analyze
from chaos.executable_image import compile_executable, load_executable
from test_executable231 import no_frontend
import _generator_flow208 as flows
import test_robot_values as robots

ROOT=Path(__file__).resolve().parents[1]
BOX='''class Box{var value:Any=0;fn __getitem__(self,key){if type_of(key)=="Tuple"{[key[0].start,key[0].stop,key[0].step,key[1]]}else{default_getattr(self,key)}};fn __setitem__(self,key,item){default_setattr(self,key,item);null}};'''
SOURCE='''class Box{var value:Any=0;fn __getitem__(self,key){if type_of(key)=="Tuple"{[key[0].start,key[0].stop,key[0].step,key[1]]}else{default_getattr(self,key)}};fn __setitem__(self,key,item){default_setattr(self,key,item);null}};
var entered=[];
fn plain(flag){entered=append(entered,"fn");var n=0;n=if flag{40}else{0};let b=Box();b.value=if flag{n}else{0};b[if flag{"value"}else{"other"}]+=2;b["value"]=if flag{b.value}else{0};[b.value,b.["value"],b[if flag{0}else{1}:2,1]]};
async fn waiting(flag){entered=append(entered,"async");let b=Box();b.value=if flag{await sleep(0);40}else{0};b["value"]+=2;b.value};
fn* stream(flag){entered=append(entered,"gen");var n=0;n=if flag{yield 40;40}else{0};let b=Box();b.value=n;b["value"]+=2;yield b.value;return_values(b.value,99)};
async fn* both(flag){entered=append(entered,"asyncgen");var n=0;n=if flag{await sleep(0);yield 40;40}else{0};let b=Box();b.value=n;b["value"]+=2;yield b.value;return_values(b.value,99)};
let f=compile_function(plain,target="auto-flow");let a=compile_function(waiting,target="auto-flow");let g=compile_function(stream,target="auto-flow");let ag=compile_function(both,target="auto-flow");let t=a(true);let s=g(true);let as_=ag(true);let cold=entered;
[cold,f(true),await t,collect(s),await acollect(as_),multiple_value_list(s),multiple_value_list(as_),entered]'''
EXPECTED=[[],[42,42,[0,2,None,1]],42,[40,42],[40,42],[42,99],[42,99],['fn','async','gen','asyncgen']]
ACCESS_KINDS={'assign','index','subscript','set_member','set_item','aug_assign'}
ACCESS_PROBE_SOURCE='class ProbeBox{var value:Any=0;fn __getitem__(s,k){if type_of(k)=="Tuple"{[k[0].start,k[0].stop,k[1]]}else{default_getattr(s,k)}};fn __setitem__(s,k,v){default_setattr(s,k,v);null}};fn original(flag){var saved=0;saved=if flag{40}else{0};let b=ProbeBox();b.value=saved;b["value"]+=2;b[if flag{"value"}else{"other"}]=b.value;[b.[if flag{"value"}else{"other"}],b[if flag{0}else{1}:2,1]]};let f=compile_function(original,target="auto-flow");[f(true),compile_flow_info(f)]'
ACCESS_PROBE_EXPECTED=[42,[0,2,1]]

@contextmanager
def portable():
    with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('access-surface-portable246')):yield

@contextmanager
def observe_access():
    events=[];frames=[];keys=[];resolve_original=EventTable.resolve
    def resolve(table,packet,owner,epoch):
        value=resolve_original(table,packet,owner,epoch)
        if value is not None and value[0]=='access':events.append(value[1][0])
        return value
    def observe(original):
        def advance(frame,cursor,value,error):
            if getattr(frame,'_flow_access_region',None) is not None:frames.append((frame.node[0],frame in cursor.frames,frame.owner is cursor.owner))
            if isinstance(frame,ItemKeyFrame) and getattr(frame,'_flow_access_parent',None) is not None:keys.append((frame.node[0],frame in cursor.frames,frame.owner is cursor.owner))
            return original(frame,cursor,value,error)
        return advance
    with flows.observe_states() as states,ExitStack() as changes:
        changes.enter_context(patch.object(EventTable,'resolve',resolve))
        for cls in (ObjectAccessEvaluationFrame,ItemKeyFrame,ClassSyntaxEvaluationFrame):changes.enter_context(patch.object(cls,'advance',observe(cls.advance)))
        yield {'states':states,'events':events,'frames':frames,'keys':keys}

def assert_access(test,seen,kinds=(),key=False):
    test.assertTrue(seen['states']);test.assertTrue(set(kinds)<=set(seen['events']),seen['events']);test.assertTrue(seen['frames'])
    test.assertTrue(all(row[1] and row[2] for row in seen['frames']+seen['keys']))
    if key:test.assertTrue(seen['keys'],'Original ItemKeyFrame must own compiled slice/multi-index children')
    for state in seen['states']:
        test.assertTrue(any(row[0]=='access' for row in state.program.instructions))
        test.assertGreater(state.program.resume_calls,0);test.assertTrue(state.closed)
        test.assertEqual(state.frames,[]);test.assertFalse(state._owned_activations);test.assertFalse(state._transfers)
        test.assertIsNone(state.pending_request);test.assertIsNone(state.pending_cursor)
        test.assertTrue(state.domain.closed);test.assertEqual(state.domain.live_count,0)

class AccessFlowSurfaces246(unittest.TestCase):
    def test_all_six_syntax_families_select_real_structural_children(self):
        cases={
            'assign':'var n=0;n=if flag{mark(42)}else{mark(0)};n',
            'index':'(if flag{b}else{other}).[mark("value")]',
            'subscript':'b[if flag{mark("value")}else{mark("other")}]',
            'set_member':'(if flag{b}else{other}).value=mark(42);b.value',
            'set_item':'b[if flag{mark("value")}else{mark("other")}]=42;b.value',
            'aug_assign':'b[if flag{mark("value")}else{mark("other")}]+=2;b.value',
        }
        for kind,body in cases.items():
            start=40 if kind=='aug_assign'else 42
            source=BOX+'var count=0;fn mark(x){count+=1;x};let b=Box();b.value='+str(start)+';let other=Box();fn original(flag){'+body+'};let f=compile_function(original,target="TARGET");[f(true),count]'
            raw=source.replace('let f=compile_function(original,target="TARGET")','let f=original')
            self.assertEqual(Framework.evaluate(raw),[42,1]);self.assertEqual(Framework.evaluate(source.replace('TARGET','interpreter')),[42,1])
            with self.subTest(kind=kind),portable(),observe_access() as seen:self.assertEqual(Framework.evaluate(source.replace('TARGET','auto-flow')),[42,1])
            assert_access(self,seen,{kind})
            self.assertTrue(any(row[0]=='enter' and row[1][0]=='if' for state in seen['states']for row in state.program.instructions))

    def test_augmented_assignment_target_key_read_rhs_write_order_and_failed_contract(self):
        source='''var events=[];fn note(label,value){events=append(events,label);value};class Box{var value:Int=40;fn __getitem__(s,k){events=append(events,"get");default_getattr(s,k)};fn __setitem__(s,k,v){events=append(events,"set");default_setattr(s,k,v);null}};fn original(flag){let b=Box();note("target",b)[if flag{note("key","value")}else{"other"}]+=if flag{note("rhs",2)}else{0};let failure=try{b.[note("bad-key","value")]=note("bad-value","wrong")}catch error{error.code};[b.value,failure,events]};compile_function(original,target="TARGET")(true)'''
        expected=[42,'runtime_error',['target','key','get','rhs','set','bad-key','bad-value']]
        for target in ('interpreter','auto-flow'):
            with self.subTest(target=target),portable(),observe_access() as seen:self.assertEqual(Framework.evaluate(source.replace('TARGET',target)),expected)
            if target=='auto-flow':assert_access(self,seen,{'aug_assign','set_member'})

    def test_scalar_slice_and_multikey_components_preserve_left_to_right_once(self):
        source=BOX+'''var events=[];fn note(label,value){events=append(events,label);value};fn original(flag){let b=Box();let key=b[if flag{note("start",0)}else{1}:if flag{note("stop",4)}else{5}:if flag{note("step",2)}else{1},if flag{note("index",3)}else{0}];[key,events]};compile_function(original,target="TARGET")(true)'''
        expected=[[0,4,2,3],['start','stop','step','index']]
        self.assertEqual(Framework.evaluate(source.replace('compile_function(original,target="TARGET")','original')),expected)
        with portable(),observe_access() as seen:self.assertEqual(Framework.evaluate(source.replace('TARGET','auto-flow')),expected)
        assert_access(self,seen,{'subscript'},key=True)

    def test_four_function_flavors_source_IR_and_cold_handles_preserve_full_values(self):
        raw=SOURCE
        for name,var in [('plain','f'),('waiting','a'),('stream','g'),('both','ag')]:raw=raw.replace('let '+var+'=compile_function('+name+',target="auto-flow")','let '+var+'='+name)
        self.assertEqual(Framework.evaluate(raw),EXPECTED)
        self.assertEqual(Framework.evaluate(SOURCE.replace('auto-flow','interpreter')),EXPECTED)
        for source in (SOURCE,format_program(parse_program(SOURCE))):
            with self.subTest(form='source' if source==SOURCE else 'IR'),portable(),observe_access() as seen:self.assertEqual(Framework.evaluate(source),EXPECTED)
            assert_access(self,seen,ACCESS_KINDS,key=True)
            self.assertEqual({s.flavor for s in seen['states']},{'fn','async_fn','gen_fn','async_gen_fn'})

    def test_saved_artifact_and_source_free_executable_use_real_access_plans(self):
        artifact=compile_artifact(SOURCE);bundle=load_artifact(json.loads(json.dumps(artifact,sort_keys=True)))
        with portable(),observe_access() as seen:self.assertEqual(Framework.evaluate(bundle),EXPECTED)
        assert_access(self,seen,ACCESS_KINDS,key=True)
        self.assertTrue(all(s.program._artifact_decoded and s.program._artifact_hash==artifact['artifactHash'] for s in seen['states']))
        image=json.loads(json.dumps(compile_executable(SOURCE)))
        with no_frontend(),portable(),observe_access() as seen:self.assertEqual(Framework.evaluate(load_executable(image)),EXPECTED)
        assert_access(self,seen,ACCESS_KINDS,key=True)

    def test_authoring_Code_and_macro_keep_same_access_syntax_and_inert_preparation(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('Authoring executed access')):
            report=analyze(SOURCE);pretty=format_source(SOURCE)
        self.assertEqual([d for d in report['diagnostics']if d['severity']=='error'],[])
        self.assertEqual(parse_program(pretty)['semanticHash'],parse_program(SOURCE)['semanticHash'])
        function='fn(x){let b=Box();b.value=if x>0{x}else{0};b["value"]+=2;b.value}'
        code=BOX+'let original=function_from(code_from('+json.dumps(function)+'),{Box:Box});compile_function(original,target="auto-flow")(40)'
        macro=BOX+'macro write(b,x){quote{unquote(b).value=if unquote(x)>0{unquote(x)}else{0}}};fn original(x){let b=Box();write!(b,x);b["value"]+=2;b.value};compile_function(original,target="auto-flow")(40)'
        for source in (code,macro):
            with self.subTest(source=source),portable(),observe_access() as seen:self.assertEqual(Framework.evaluate(source),42)
            assert_access(self,seen,{'set_member','aug_assign'})

    def test_AgentTool_Team_and_Bot_callbacks_keep_once_effects_and_distinct_denial_contracts(self):
        calls=[];steps=[];registry=Registry()
        @registry.tool('fixture.access.audit')
        def audit(value,ctx):ctx.checkpoint();calls.append(value);return value
        @registry.provider('fixture.access')
        def provider(request,ctx):
            ctx.checkpoint();steps.append(request.step)
            return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        caps=['fixture.access.audit'];team={'kind':'team','name':'access team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':caps,'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':caps}}}]}
        setup=BOX+'''async fn original(value){let b=Box();b.value=if value>0{tool("fixture.access.audit",value);await sleep(0);value}else{0};b["value"]+=2;b.value};let work=compile_function(original,target="auto-flow");agent helper{provider:"fixture.access",tools:["fixture.access.audit"]};let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.access.audit"])]);let crew=team_bind(team_from(TEAM),{work:work});'''.replace('TEAM',json.dumps(team))
        bot='''async fn execute(plan,state){let t=bound(40);[[await t,await t],await crew(40)]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};let worker=bot("access",fn(state){state.input},execute,verify);'''
        with Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app,portable(),observe_access()as seen:
            project=app.create_project('access-host',source=setup+bot+'(await worker(null)).output',capabilities=caps)
            self.assertEqual(app.start(project['id'],project['version'],None,mode='source').result(15),[[42,42],42]);self.assertFalse(app._engine.active)
        self.assertEqual(calls,[40,40]);self.assertEqual(steps,[1,2]);assert_access(self,seen,{'set_member','aug_assign','index'})
        for target in ('raw','compiled'):
            current=setup if target=='compiled'else setup.replace('let work=compile_function(original,target="auto-flow");','let work=original;')
            for binding,code in [('bound','capability_error'),('crew','runtime_error')]:
                source='try{'+current+'await '+binding+'(40)}catch error{error.code}'
                with Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app,portable():
                    p=app.create_project('access-direct-denied',source=source,capabilities=[])
                    self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(15),code)
            source=current+bot+'let result=await worker(null);let state=bot_info(worker);[result.status,result.done,result.output,state.busy,try{await worker(null)}catch error{error.code}]'
            with Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app,portable():
                p=app.create_project('access-bot-denied',source=source,capabilities=[])
                self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(15),['needs_reconciliation',False,None,False,'bot_state_error'])
            self.assertEqual(calls,[40,40]);self.assertEqual(steps,[1,2])

    def test_software_Robot_command_once_through_access_initializer_and_repeated_await(self):
        source=BOX+'''async fn original(){async with await robot_open("rig") as r{let o=await robot_observe(r);let c=robot_command(o,{pan:0,tilt:1});let b=Box();b.value=if c.used{null}else{(await r(c)).status};b["value"]}};let f=compile_function(original,target="auto-flow");let t=f();[await t,await t]'''
        log=[]
        with robots.host(log)as app,portable(),observe_access()as seen:self.assertEqual(robots.run(app,source),['sent','sent'])
        self.assertEqual([name for name,_ in log],['open','observe','act','close']);assert_access(self,seen,{'set_member','subscript'})
        denied=[]
        with robots.host(denied)as app:self.assertEqual(robots.run(app,'try{'+source+'}catch error{error.code}',caps=[]),'capability_error')
        self.assertEqual(denied,[])

    def test_persistent_source_and_graph_preserve_same_program_and_authority_snapshot(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),Framework(':memory:',settings=Settings(':memory:'))as app,portable(),observe_access()as seen:
                project=app.create_project('access-'+mode,source=SOURCE,capabilities=[])
                run=app.start(project['id'],project['version'],None,mode=mode)
                self.assertEqual(run.result(20),EXPECTED);self.assertEqual(app.get_project(project['id'])['source'],SOURCE)
                self.assertEqual(run.get()['snapshot']['capabilities'],[]);self.assertFalse(app._engine.active)
            assert_access(self,seen,ACCESS_KINDS,key=True)

    def test_fresh_isolated_SDK_and_CLI_execute_access_program_without_network(self):
        bootstrap=r'''import json,socket,sys
from pathlib import Path
root,mode,file=sys.argv[1:];sys.path[:0]=[root,str(Path(root)/'tests')]
assert sys.flags.isolated and sys.flags.no_site
def denied(*a,**k):raise AssertionError('Offline access test has no network authority')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied
from test_access_flow_surfaces246 import observe_access,ACCESS_KINDS
with observe_access()as seen:
 if mode=='sdk':
  from chaos import Framework
  print(json.dumps(Framework.evaluate(Path(file).read_text())))
 else:
  from chaos.cli import main
  sys.argv=['chaos','run',file,'--db',':memory:'];assert main()==0
assert ACCESS_KINDS<=set(seen['events']) and seen['keys']
assert all(s.closed and not s.frames and s.domain.closed and s.domain.live_count==0 for s in seen['states'])
'''
        with tempfile.TemporaryDirectory(prefix='access-flow-')as directory:
            work=Path(directory);file=work/'main.chaos';file.write_text(SOURCE)
            env={key:value for key,value in os.environ.items()if key in('PATH','HOME','USERPROFILE','TMPDIR','TMP','TEMP','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
            env.update(CHAOS_CONFIG=str(work/'settings.json'),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
            for mode in ('sdk','cli'):
                with self.subTest(mode=mode):
                    child=subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(ROOT),mode,str(file)],cwd=work,env=env,text=True,capture_output=True,timeout=40)
                    self.assertEqual(child.returncode,0,child.stderr);self.assertEqual(json.loads(child.stdout),EXPECTED)
