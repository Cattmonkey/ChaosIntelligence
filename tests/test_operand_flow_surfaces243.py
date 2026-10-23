"""Independent public compositions of operand CFG and the original runtime."""
from contextlib import contextmanager
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
from chaos.execution_frames import OperandFrame
from chaos.language import Evaluator
from chaos.native_jit import BackendUnavailable
from chaos.program import parse_program, format_program
from chaos.source_format import format_source
from chaos.ide_language import analyze
from chaos.executable_image import compile_executable, load_executable
import _generator_flow208 as flows
from test_executable231 import no_frontend
import test_robot_values as robots

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '''var entered=[];
fn plain(x){entered=append(entered,"fn");let value=if x>0{x+1}else{0};value+1};
async fn waiting(x){entered=append(entered,"async");let value=if x>0{await sleep(0);x+1}else{0};value+1};
fn* stream(x){entered=append(entered,"gen");let value=if x>0{yield x;x+1}else{0};yield value+1;return_values(value,99)};
async fn* both(x){entered=append(entered,"asyncgen");let value=if x>0{await sleep(0);yield x;x+1}else{0};yield value+1;return_values(value,99)};
let f=compile_function(plain,target="auto-flow");let a=compile_function(waiting,target="auto-flow");let g=compile_function(stream,target="auto-flow");let ag=compile_function(both,target="auto-flow");
let task=a(40);let gen=g(40);let agen=ag(40);let cold=entered;
[cold,f(40),await task,collect(gen),await acollect(agen),multiple_value_list(gen),multiple_value_list(agen),entered]'''
EXPECTED = [[],42,42,[40,42],[40,42],[41,99],[41,99],['fn','async','gen','asyncgen']]
OPERANDS = {'call','let','var','list','tuple','record','binary','unary','do'}

@contextmanager
def portable():
    with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('operand-surface-portable')):
        yield

@contextmanager
def observe_operands():
    seen=[];original=OperandFrame.step
    def step(frame,cursor,value,error):
        region=getattr(frame,'_flow_operand_region',None)
        if region is not None:
            seen.append((frame.node[0],frame in cursor.frames,frame.boundary.flow_state,getattr(frame,'_flow_operand_segment',None)))
        return original(frame,cursor,value,error)
    with flows.observe_states() as states,patch.object(OperandFrame,'step',step):
        yield states,seen

def assert_compiled(test,observation,kinds=()):
    states,seen=observation
    test.assertTrue(states,'An explicit flow compilation must execute actual FlowState')
    test.assertTrue(seen,'Original OperandFrame must own a selected operand region')
    test.assertTrue(all(row[1] for row in seen))
    test.assertTrue(set(kinds).issubset({row[0] for row in seen}))
    for state in states:
        test.assertTrue(any(row[0]=='operand' for row in state.program.instructions))
        test.assertTrue(any(row[0]=='enter' and row[1][0]=='if' for row in state.program.instructions))
        test.assertGreater(state.program.resume_calls,0)
        test.assertTrue(state.closed)
        test.assertEqual(state.frames,[])
        test.assertFalse(state._owned_activations)
        test.assertIsNone(state.pending_request)
        test.assertIsNone(state.pending_cursor)
        test.assertTrue(state.domain.closed)
        test.assertEqual(state.domain.live_count,0)

class OperandFlowSurfaces243(unittest.TestCase):
    def test_all_nine_operand_families_execute_nested_structural_children(self):
        cases={
            'call':('(if flag{fn(x){x+1}}else{fn(x){x-1}})(bump(41))',42),
            'let':('do{let selected=if flag{bump(42)}else{bump(0)};selected}',42),
            'var':('do{var selected=if flag{bump(42)}else{bump(0)};selected}',42),
            'list':('[if flag{bump(42)}else{bump(0)}]',[42]),
            'tuple':('(if flag{bump(42)}else{bump(0)},)',{'$tuple':[42]}),
            'record':('{answer:if flag{bump(42)}else{bump(0)}}',{'answer':42}),
            'binary':('(if flag{bump(40)}else{bump(0)})+2',42),
            'unary':('-(if flag{bump(-42)}else{bump(0)})',42),
            'do':('do{if flag{bump(42)}else{bump(0)}}',42),
        }
        for kind,(expression,expected) in cases.items():
            source='var count=0;fn bump(x){count+=1;x};fn original(flag){'+expression+'};let f=compile_function(original,target="TARGET");[f(true),count]'
            raw=source.replace('let f=compile_function(original,target="TARGET")','let f=original')
            self.assertEqual(Framework.evaluate(raw),[expected,1])
            self.assertEqual(Framework.evaluate(source.replace('TARGET','interpreter')),[expected,1])
            with self.subTest(kind=kind),portable(),observe_operands() as observed:
                self.assertEqual(Framework.evaluate(source.replace('TARGET','auto-flow')),[expected,1])
            assert_compiled(self,observed,{kind})

    def test_source_and_structural_IR_keep_four_flavors_cold_and_full_terminal_values(self):
        raw=SOURCE
        for name,var in [('plain','f'),('waiting','a'),('stream','g'),('both','ag')]:
            raw=raw.replace('let '+var+'=compile_function('+name+',target="auto-flow")','let '+var+'='+name)
        self.assertEqual(Framework.evaluate(raw),EXPECTED)
        self.assertEqual(Framework.evaluate(SOURCE.replace('auto-flow','interpreter')),EXPECTED)
        for version in (1,2):
            source=format_program(parse_program(SOURCE,language_version=version))
            with self.subTest(version=version),portable(),observe_operands() as observed:
                self.assertEqual(Framework.evaluate(source),EXPECTED)
            assert_compiled(self,observed,{'let','call','binary'})
            self.assertEqual({s.flavor for s in observed[0]},{'fn','async_fn','gen_fn','async_gen_fn'})

    def test_source_authoritative_artifact_decodes_real_operand_plans(self):
        artifact=compile_artifact(SOURCE)
        bundle=load_artifact(json.loads(json.dumps(artifact,sort_keys=True)))
        with portable(),observe_operands() as observed:
            self.assertEqual(Framework.evaluate(bundle),EXPECTED)
        assert_compiled(self,observed,{'let'})
        self.assertTrue(all(s.program._artifact_decoded and s.program._artifact_hash==artifact['artifactHash'] for s in observed[0]))

    def test_source_free_executable_uses_same_explicit_compiler_after_frontend_is_disabled(self):
        image=json.loads(json.dumps(compile_executable(SOURCE)))
        with no_frontend(),portable(),observe_operands() as observed:
            self.assertEqual(Framework.evaluate(load_executable(image)),EXPECTED)
        assert_compiled(self,observed,{'let'})
        plain='fn f(x){let value=if x>0{x+1}else{0};value+1};f(40)'
        # Compile before the tripwire: loading a function never opts it into JIT.
        plain_image=compile_executable(plain)
        with no_frontend(),observe_operands() as uncompiled:
            self.assertEqual(Framework.evaluate(load_executable(plain_image)),42)
        self.assertEqual(uncompiled,([],[]))

    def test_authoring_and_hygienic_macro_Code_paths_keep_original_explicit_opt_in(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('Authoring executed program')):
            diagnostics=analyze(SOURCE)['diagnostics'];formatted=format_source(SOURCE)
        self.assertEqual([d for d in diagnostics if d['severity']=='error'],[])
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
        function='fn(x){let y=if x>0{x+1}else{0};y+1}'
        code='let original=function_from(code_from('+json.dumps(function)+'));compile_function(original,target="auto-flow")(40)'
        macro='macro choose(x){quote{if unquote(x)>0{unquote(x)+1}else{0}}};fn original(x){let y=choose!(x);y+1};compile_function(original,target="auto-flow")(40)'
        for source in (code,macro):
            with self.subTest(source=source),portable(),observe_operands() as observed:
                self.assertEqual(Framework.evaluate(source),42)
            assert_compiled(self,observed,{'let'})

    def test_AgentTool_Team_Bot_share_original_authority_and_once_effects(self):
        calls=[];steps=[];registry=Registry()
        @registry.tool('fixture.operand.audit')
        def audit(value,ctx):ctx.checkpoint();calls.append(value);return value
        @registry.provider('fixture.operand')
        def provider(request,ctx):
            ctx.checkpoint();steps.append(request.step)
            return ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])
        caps=['fixture.operand.audit']
        team={'kind':'team','name':'operand team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':caps,'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':caps}}}]}
        source='''async fn original(value){let selected=if value>0{tool("fixture.operand.audit",value);await sleep(0);value+2}else{0};selected};let work=compile_function(original,target="auto-flow");agent helper{provider:"fixture.operand",tools:["fixture.operand.audit"]};let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.operand.audit"])]);let crew=team_bind(team_from(TEAM),{work:work});async fn execute(plan,state){let t=bound(40);[[await t,await t],await crew(40)]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};let worker=bot("operand",fn(state){state.input},execute,verify);(await worker(null)).output'''.replace('TEAM',json.dumps(team))
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app,portable(),observe_operands() as observed:
            project=app.create_project('operand-host',source=source,capabilities=caps)
            run=app.start(project['id'],project['version'],None,mode='source')
            self.assertEqual(run.result(15),[[42,42],42])
            self.assertFalse(app._engine.active)
        self.assertEqual(calls,[40,40]);self.assertEqual(steps,[1,2])
        assert_compiled(self,observed,{'let'})
        # A Bot reports ordinary execution denial as reconciliation. Inspect
        # its full result and the original action receipt rather than projecting
        # only .output and expecting a propagated error.
        projections=[]
        for target in ('raw','compiled'):
            current=source if target=='compiled' else source.replace('let work=compile_function(original,target="auto-flow");','let work=original;')
            suffix='(await worker(null)).output'
            self.assertTrue(current.endswith(suffix))
            denied=current[:-len(suffix)]+'''let outcome=await worker(null);let state=bot_info(worker);let retry=try{await worker(null);null}catch error{error.code};{outcome:outcome,state:state,retry:retry}'''
            before_calls,before_steps=len(calls),len(steps)
            with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app,portable():
                project=app.create_project('operand-denied',source=denied,capabilities=[])
                result=app.start(project['id'],project['version'],None,mode='source').result(15)
                self.assertFalse(app._engine.active)
            outcome,state=result['outcome'],result['state']
            self.assertEqual(outcome['status'],'needs_reconciliation');self.assertFalse(outcome['done']);self.assertIsNone(outcome['output'])
            self.assertEqual(state['status'],'needs_reconciliation');self.assertFalse(state['busy']);self.assertTrue(state['needsReconciliation'])
            self.assertEqual(result['retry'],'bot_state_error')
            actions=[action for entry in outcome['state']['history'] for action in entry.get('actions',[])]
            self.assertTrue(any(action.get('kind')=='agent_transition' and action.get('status')=='uncertain' and action.get('errorCode')=='capability_error' for action in actions),outcome['state']['history'])
            self.assertEqual(len(calls)-before_calls,0);self.assertEqual(len(steps)-before_steps,0)
            projections.append((outcome['done'],outcome['status'],outcome['output'],state['status'],state['busy'],result['retry'],[(a['kind'],a['status'],a.get('errorCode'))for a in actions]))
            # Direct bound operations still propagate the actual permission
            # denial, independently of the Bot's explicit result contract.
            setup=current.split('async fn execute(plan,state)',1)[0]
            for binding in ('bound','crew'):
                direct='try{'+setup+'let task='+binding+'(40);await task}catch error{error.code}'
                with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app,portable():
                    project=app.create_project('operand-direct-denied',source=direct,capabilities=[])
                    self.assertEqual(app.start(project['id'],project['version'],None,mode='source').result(15),'runtime_error' if binding=='crew' else 'capability_error')
                    self.assertFalse(app._engine.active)
                self.assertEqual(len(calls)-before_calls,0);self.assertEqual(len(steps)-before_steps,0)
        self.assertEqual(projections[0],projections[1])
        self.assertEqual(calls,[40,40]);self.assertEqual(steps,[1,2])

    def test_software_Robot_nested_operand_wait_does_not_replay_borrowed_task(self):
        source='''async fn original(){async with await robot_open("rig") as r{let observed=await robot_observe(r);let command=robot_command(observed,{pan:0,tilt:1});let receipt=if command.used{null}else{await r(command)};receipt.status}};let execute=compile_function(original,target="auto-flow");let t=execute();[await t,await t]'''
        log=[]
        with robots.host(log) as app,portable(),observe_operands() as observed:
            self.assertEqual(robots.run(app,source),['sent','sent'])
        self.assertEqual([name for name,_ in log],['open','observe','act','close'])
        assert_compiled(self,observed,{'let'})
        denied=[]
        with robots.host(denied) as app:
            self.assertEqual(robots.run(app,'try{'+source+'}catch error{error.code}',caps=[]),'capability_error')
        self.assertEqual(denied,[])

    def test_fresh_isolated_SDK_and_CLI_execute_same_operand_program_without_network(self):
        bootstrap=r'''import json,runpy,socket,sys
from pathlib import Path
root,mode,file=sys.argv[1:];sys.path[:0]=[root,str(Path(root)/'tests')]
assert sys.flags.isolated and sys.flags.no_site
def denied(*a,**k):raise AssertionError('Offline operand fixture has no network authority')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied
from test_operand_flow_surfaces243 import observe_operands
with observe_operands() as observed:
 if mode=='sdk':
  from chaos import Framework
  result=Framework.evaluate(Path(file).read_text());print(json.dumps(result))
 else:
  from chaos.cli import main
  sys.argv=['chaos','run',file,'--db',':memory:'];status=main();assert status==0
assert observed[0] and observed[1]
assert all(s.closed and not s.frames and s.pending_request is None and s.domain.closed for s in observed[0])
assert all(any(row[0]=='operand' for row in s.program.instructions) for s in observed[0])
'''
        with tempfile.TemporaryDirectory(prefix='operand-surfaces-') as directory:
            work=Path(directory);file=work/'main.chaos';file.write_text(SOURCE)
            env={key:value for key,value in os.environ.items() if key in ('PATH','HOME','USERPROFILE','TMPDIR','TMP','TEMP','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
            env.update(CHAOS_CONFIG=str(work/'settings.json'),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
            for mode in ('sdk','cli'):
                with self.subTest(mode=mode):
                    child=subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(ROOT),mode,str(file)],cwd=work,env=env,text=True,capture_output=True,timeout=35)
                    self.assertEqual(child.returncode,0,child.stderr)
                    self.assertEqual(json.loads(child.stdout),EXPECTED)
