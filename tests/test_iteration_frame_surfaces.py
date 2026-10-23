"""Real iteration, stream and comprehension frames on the existing surfaces."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings, ToolCall, compile_program
from chaos.documentation import Documentation
from chaos.execution_iteration import (IterationAcquireFrame, IterationInvokeFrame,
    IterationAwaitFrame, IterationStepFrame, IterationCloseFrame, IterationCollectFrame,
    IterationConsumeFrame, IterationDelegateFrame, IterationResolveFrame,
    StreamResumeFrame, StreamCloseFrame)
from chaos.execution_comprehensions import ComprehensionFrame, ComprehensionWalkFrame
from chaos.execution_conditions import (ConditionRequestFrame, RecoveryCallbackFrame,
    ConditionSignalFrame, ConditionDebuggerFrame, RestartComputeFrame, RestartInvokeFrame,
    RestartCaseFrame)
from chaos.execution_bot import (BotRunFrame, BotHostFrame, BotCallbackFrame,
    BotPhaseFrame, BotPhaseBodyFrame, BotCloseOwnedFrame, BotCleanupFrame,
    BotDecisionFrame, BotDrainFrame, BotOutcomeFrame, BotCommitFrame)
from chaos.execution_control import (ControlCallbackFrame, PromptHandlerFrame,
    PromptCallFrame, ControlBarrierFrame, DynamicWindFrame)
from chaos.execution_frames import (ContextFrame, PromptScopeFrame, CancelShieldFrame,
    TaskGroupFrame, GroupBodyFrame)
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native
from chaos.modules import STDLIB
from chaos.native_jit import BackendUnavailable
from chaos.native_specs import all_specs
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.source_format import format_source

ROOT = Path(__file__).resolve().parents[1]
ITERATION_FRAMES = (IterationAcquireFrame, IterationInvokeFrame, IterationAwaitFrame,
    IterationStepFrame, IterationCloseFrame, IterationCollectFrame, IterationConsumeFrame,
    IterationDelegateFrame, IterationResolveFrame, StreamResumeFrame, StreamCloseFrame,
    ComprehensionFrame, ComprehensionWalkFrame)
PREVIOUS_FRAMES = (ContextFrame, PromptScopeFrame, CancelShieldFrame, TaskGroupFrame,
    GroupBodyFrame, ControlCallbackFrame, PromptHandlerFrame, PromptCallFrame,
    ControlBarrierFrame, DynamicWindFrame, BotRunFrame, BotHostFrame, BotCallbackFrame,
    BotPhaseFrame, BotPhaseBodyFrame, BotCloseOwnedFrame, BotCleanupFrame,
    BotDecisionFrame, BotDrainFrame, BotOutcomeFrame, BotCommitFrame,
    ConditionRequestFrame, RecoveryCallbackFrame, ConditionSignalFrame,
    ConditionDebuggerFrame, RestartComputeFrame, RestartInvokeFrame, RestartCaseFrame)
ITERATION_NAMES = {cls.__name__ for cls in ITERATION_FRAMES}
ALL_NAMES = ITERATION_NAMES | {cls.__name__ for cls in PREVIOUS_FRAMES}

ITERATION_SOURCE = '''var streamEvents=[];
class Cursor{
 var position=0;
 fn __iter__(self){self}
 fn __next__(self){self.position=self.position+1;streamEvents=append(streamEvents,self.position);
  if self.position<=3{{done:false,value:self.position}}else{{done:true,value:"finished"}}}
 fn __close__(self){streamEvents=append(streamEvents,"cursorClosed");null}
}
let cursor=iter(Cursor());let decisive=any(fn(value){value==2},cursor);
let tail=collect(cursor);close(cursor);close(cursor);
let eager=[x+y for x in [20] for y in [20,22] if y>20];
let record={key:key+key for key in ["a","b"]};
let distinct=length({x%2 for x in [1,2,3]});
let lazy=(x*2 for x in [20,21]);
let lazyValues=[next(lazy).value,next(lazy).value,next(lazy).done];
async fn* asyncSource(){try{yield 20;yield 21}finally{streamEvents=append(streamEvents,"asyncSourceClosed")}}
let asynchronous=[x*2 async for x in asyncSource()];
let asyncLazy=await acollect(async (x*2 for x in [20,21]));
class AsyncCursor{
 var position=20;
 fn __aiter__(self){self}
 async fn __anext__(self){await sleep(0);if self.position<22{let value=self.position;self.position=self.position+1;{done:false,value:value}}else{{done:true,value:84}}}
 async fn __aclose__(self){await sleep(0);streamEvents=append(streamEvents,"asyncCursorClosed");null}
}
let asyncCursor=aiter(AsyncCursor());let operation=anext(asyncCursor);let cold=task_info(operation).state;
let firstAsync=await operation;let tailAsync=await acollect(asyncCursor);await aclose(asyncCursor);await aclose(asyncCursor);
fn* delegated(){yield from [40,42]}
let delegation=delegated();let firstDelegated=next(delegation).value;close(delegation);
let consumers=[map(fn(x){x+1},[40,41]),filter(fn(x){x>40},[40,41]),fold(fn(a,x){a+x},0,[20,22]),
 apply(fn(a,b){a+b},[20,22]),(fn(a,b){a+b})(*[20,22]),length(tuple([1,2])),length(set_of([1,1,2])),all(fn(x){x>0},[1,2])];
let iterationResult={borrow:[decisive,tail],eager:eager,record:record,setCount:distinct,lazy:lazyValues,
 asynchronous:asynchronous,asyncLazy:asyncLazy,customAsync:[cold,firstAsync.value,tailAsync],
 delegated:firstDelegated,consumers:consumers,events:streamEvents};
'''
ITERATION_EXPECTED = {'borrow': [True, [3]], 'eager': [42],
    'record': {'a': 'aa', 'b': 'bb'}, 'setCount': 2, 'lazy': [40, 42, True],
    'asynchronous': [40, 42], 'asyncLazy': [40, 42],
    'customAsync': ['created', 20, [21]], 'delegated': 40,
    'consumers': [[41, 42], [41], 42, 42, 42, 2, 2, True],
    'events': [1, 2, 3, 4, 'cursorClosed', 'asyncSourceClosed', 'asyncCursorClosed']}

BASE_SOURCE = 'var masks=[];\nwith handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,c.kind)}}]) as outer{\n with handler_bind([{predicate:fn(c){true},handler:fn(c){\n  with handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,"new")}}]) as fresh{signal_condition(condition("nested"))};\n  masks=append(masks,"inner")\n }}]) as inner{signal_condition(condition("root"))}\n};\nlet inline=with restart_bind([{name:"inline",test:fn(c){c!=null&&c.kind=="missing"},function:fn(x){x+1}}]) as scope{\n let offered=compute_restarts(condition("missing"));\n let chosen=find_restart("inline",condition("missing"));\n [length(offered),restart_info(chosen).mode,invoke_restart(chosen,[40])]\n};\nvar cleanup=[];\nlet captured=multiple_value_list(fn(){restart_case(async fn(){\n try{with handler_bind([{predicate:async fn(c){await sleep(0);true},handler:async fn(c){await sleep(0);invoke_restart("use",[c.["data"].fallback])}}]) as policy{\n  error_condition(condition("missing",data={fallback:41}))\n }}finally{cleanup=append(cleanup,"cleanup")}\n},[{name:"use",function:async fn(x){await sleep(0);cleanup=append(cleanup,"restart");return_values(x+1,find_restart("use")==null)}}])});\nlet unhandled=try{error_condition(condition("unhandled"))}catch e{e.code};\nlet noHost=try{invoke_debugger(condition("explicit"))}catch e{e.code};\nlet recovered=[inline,captured,masks,cleanup,unhandled,noHost];\nimport "std/context" as C;\nvar events=[];\n@C.asynccontextmanager\nasync fn* resource(){try{await sleep(0);yield {value:41}}finally{await sleep(0);events=append(events,"closed")}}\nasync fn* remaining(){try{yield 0}finally{events=append(events,"streamClosed")}}\nfn plan(s){s.input}\nasync fn execute(p,s){\n let held=remaining();await anext(held);\n with cancel_shield() as shield{async with task_group() as group{\n  async with resource() as {value:value}{let tag=prompt_tag("package");with prompt_scope(tag) as scope{\n   control_barrier(async fn(){\n    let answer=call_escape(async fn(exit){dynamic_wind(fn(){null},async fn(){await sleep(0);exit(value+1)},fn(){null})});\n    call_prompt(tag,fn(){abort_prompt(tag,answer)},fn(result){result})\n   })\n  }}\n }}\n}\nfn verify(s){{done:true,context:{retired:length(events)},output:s.evidence[0]}}\nlet worker=bot("typed recovery deployment",plan,execute,verify,context={retired:0});\nlet pending=worker(null);let before=events;\nlet actual=await pending;\n[actual.output,events,actual.status,actual.state.context,before,recovered,iterationResult]\n'
SOURCE = ITERATION_SOURCE + BASE_SOURCE
EXPECTED = [42, ['closed', 'streamClosed'], 'achieved', {'retired': 2}, [], [[1, 'inline', 41], [42, True], ['new', 'nested', 'inner', 'root'], ['cleanup', 'restart'], 'condition_error', 'condition_error']] + [ITERATION_EXPECTED]

DOCUMENTED = '''var reads=0;
fn* source(){for value in [20,21]{reads=reads+1;yield value}}
let stream=(value*2 for value in source());
let before=reads;let first=next(stream).value;
let remaining=collect(stream);
[before,first,remaining,reads]
'''
DOCUMENTED_EXPECTED = [0, 40, [42], 2]
FUNCTION = '''fn(values){let projected=[value+1 for value in values];
 fold(fn(total,value){total+value},0,projected)}'''


@contextmanager
def observe_iteration_frames():
    seen = {'frames': set(), 'owners': set(), 'cursors': set(),
            'operations': set(), 'steps': {}, 'phases': {}}
    with ExitStack() as stack:
        for cls in ITERATION_FRAMES + PREVIOUS_FRAMES:
            original = cls.step
            def observed(frame, cursor, value, error, original=original):
                name = type(frame).__name__
                seen['frames'].add(name);seen['owners'].add(type(cursor.owner).__name__)
                seen['cursors'].add(id(cursor))
                seen['steps'][name] = seen['steps'].get(name, 0) + 1
                seen['phases'].setdefault(name, set()).add(frame.phase)
                if type(frame) is IterationResolveFrame:
                    seen['operations'].add(frame.operation)
                return original(frame, cursor, value, error)
            stack.enter_context(patch.object(cls, 'step', observed))
        yield seen


class IterationFrameSurfaces(unittest.TestCase):
    def run_source(self, source, *, registry=None, capabilities=None, mode='source'):
        with Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
            p = app.create_project('iteration-frames', source=source,
                capabilities=[] if capabilities is None else capabilities)
            run = app.start(p['id'], p['version'], None, mode=mode)
            result = run.result(12)
            events, after = [], 0
            while True:
                page = app.events(p['id'], run_id=run.id, after=after, limit=300)
                events.extend(page['items'])
                if page['nextCursor'] is None:break
                after = page['nextCursor']
            return result, events

    def assert_all_frames(self, seen):
        self.assertEqual(seen['frames'], ALL_NAMES, seen)
        self.assertEqual(seen['owners'], {'Evaluator'})
        self.assertTrue(all(count > 0 for count in seen['steps'].values()))
        self.assertGreater(len(seen['cursors']), 1)

    def test_source_and_graph_execute_all_forty_one_real_frame_classes(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode), observe_iteration_frames() as seen:
                self.assertEqual(self.run_source(SOURCE, mode=mode)[0], EXPECTED)
            self.assert_all_frames(seen)

    def test_ir_v1_and_v2_roundtrip_share_every_real_frame(self):
        for version in (1, 2):
            ir = parse_program(SOURCE, language_version=version)
            formatted = format_program(ir)
            self.assertEqual(parse_program(formatted, language_version=version)['semanticHash'], ir['semanticHash'])
            definition = {'kind': 'program', 'entry': 'main.chaos',
                'files': {'main.chaos': formatted}, 'languageVersions': {'main.chaos': version}}
            source = 'let p=program_from('+json.dumps(definition)+');await p(null)'
            with self.subTest(version=version), observe_iteration_frames() as seen:
                self.assertEqual(self.run_source(source)[0], EXPECTED)
            self.assert_all_frames(seen)

    def test_code_program_macros_and_compile_targets_share_real_consumers(self):
        module = {'kind':'program','entry':'main.chaos',
            'files':{'main.chaos':FUNCTION.replace('fn(values','fn build(values')+';export{build};'},
            'languageVersions':{'main.chaos':2}}
        program = {'kind':'program','entry':'main.chaos',
            'files':{'main.chaos':'let build='+FUNCTION+';build([20,20])'},
            'languageVersions':{'main.chaos':2}}
        sources = ['let build=function_from(code_from('+json.dumps(FUNCTION)+'));build([20,20])',
            'let M=module_from(program_from('+json.dumps(module)+'));M.build([20,20])',
            'let p=program_from('+json.dumps(program)+');await p(null)',
            'macro double(xs){quote{[x*2 for x in unquote(xs)]}};fold(fn(a,x){a+x},0,double!([10,11]))']
        required = {'ComprehensionFrame','ComprehensionWalkFrame','IterationConsumeFrame','IterationStepFrame'}
        for source in sources:
            with self.subTest(source=source), observe_iteration_frames() as seen:
                self.assertEqual(self.run_source(source)[0], 42)
            self.assertTrue(required <= seen['frames'], seen)
        for target in ('interpreter','auto','auto-flow','auto-control','portable'):
            selected = 'auto-flow' if target == 'portable' else target
            source = FUNCTION.replace('fn(values','fn build(values')+';let compiled=compile_function(build,target="'+selected+'");compiled([20,20])'
            with self.subTest(target=target), observe_iteration_frames() as seen:
                if target == 'portable':
                    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('fixture portable')):
                        self.assertEqual(self.run_source(source)[0], 42)
                else:self.assertEqual(self.run_source(source)[0], 42)
            self.assertTrue(required <= seen['frames'], seen)

    def test_actual_agent_tool_team_and_bot_stream_effects_execute_once(self):
        registry, effects, provider_steps = Registry(), [], []
        @registry.tool('fixture.iter.audit')
        def audit(value, context):
            context.checkpoint();effects.append(value);return value
        @registry.provider('fixture.iter.provider')
        def provider(request, context):
            context.checkpoint();provider_steps.append((request.input, request.step))
            if request.step == 1:return ToolCall('work', request.input)
            return Final(request.history[-1]['output'])
        team = {'kind':'team','name':'iteration team','entry':'work','roles':[
            {'id':'work','parent':None,'capabilities':['fixture.iter.audit'],
             'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.iter.audit']}}}]}
        source = '''async fn work(value:Int){tool("fixture.iter.audit",["agent",value]);value+1};
agent helper{provider:"fixture.iter.provider",tools:["fixture.iter.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.iter.audit"])]);
async fn role(value){tool("fixture.iter.audit",["team",value]);fold(fn(a,x){a+x},0,[x for x in [value,value]])};
let crew=team_bind(team_from(TEAM),{work:role});
async fn agentWork(value){await bound(value)};
async fn execute(p,s){let jobs=[agentWork(value) for value in p];
 let cold=map(fn(task){task_info(task).state},jobs);let first=await gather(jobs);
 let second=[await crew(value) for value in first];[cold,first,second]};
fn verify(s){{done:true,context:s.context,output:s.evidence[0]}};
let worker=bot("iteration work",fn(s){s.input},execute,verify);(await worker([20,21])).output'''.replace('TEAM',json.dumps(team))
        with observe_iteration_frames() as seen:
            result, events = self.run_source(source, registry=registry, capabilities=['fixture.iter.audit'])
        self.assertEqual(result, [['created','created'],[21,22],[42,44]])
        self.assertCountEqual(effects, [['agent',20],['agent',21],['team',21],['team',22]])
        self.assertCountEqual(provider_steps, [(20,1),(20,2),(21,1),(21,2)])
        self.assertEqual(sum(e['type']=='agent.language_tool' for e in events), 2)
        self.assertEqual(sum(e['type']=='team.role.started' for e in events), 2)
        self.assertTrue({'BotCommitFrame','ComprehensionFrame','ComprehensionWalkFrame',
                         'IterationConsumeFrame','IterationStepFrame'} <= seen['frames'],seen)

    def test_custom_async_step_is_cold_and_suspend_then_close_runs_once(self):
        entered, release, effects = threading.Event(), threading.Event(), []
        registry = Registry()
        @registry.tool('fixture.iter.wait')
        def wait(value, context):
            entered.set()
            while not release.wait(.01):context.checkpoint()
            context.checkpoint();effects.append(['step',value]);return value
        @registry.tool('fixture.iter.close')
        def close(value, context):
            context.checkpoint();effects.append(['close',value]);return None
        source = '''class Source{fn __aiter__(self){self}
async fn __anext__(self){{done:false,value:tool("fixture.iter.wait",42)}}
async fn __aclose__(self){await sleep(0);tool("fixture.iter.close",42);null}}
let cursor=aiter(Source());let pending=anext(cursor);let before=task_info(pending).state;
let first=await pending;await aclose(cursor);await aclose(cursor);[before,first]'''
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('async-step',source=source,capabilities=['fixture.iter.wait','fixture.iter.close'])
            with observe_iteration_frames() as seen:
                run=app.start(p['id'],p['version'],None)
                try:
                    self.assertTrue(entered.wait(5));self.assertEqual(effects,[])
                    self.assertTrue({'IterationStepFrame','IterationInvokeFrame','IterationAwaitFrame'} <= seen['frames'],seen)
                    release.set();self.assertEqual(run.result(5),['created',{'done':False,'value':42}])
                finally:release.set();run.cancel()
            self.assertEqual(effects,[['step',42],['close',42]])
            self.assertIn('IterationCloseFrame',seen['frames'])

    def test_cancel_suspended_async_step_and_projection_drain_without_user_close(self):
        for phase in ('step','projection'):
            with self.subTest(phase=phase):
                entered, exited, effects = threading.Event(), threading.Event(), []
                registry=Registry()
                @registry.tool('fixture.iter.wait')
                def wait(value,context):
                    entered.set()
                    try:
                        while True:context.checkpoint();threading.Event().wait(.01)
                    finally:exited.set()
                @registry.tool('fixture.iter.close')
                def close(value,context):effects.append(value);return None
                source = '''class Source{fn __aiter__(self){self}
async fn __anext__(self){{done:false,value:tool("fixture.iter.wait",42)}}
async fn __aclose__(self){tool("fixture.iter.close","forbidden");null}}
await anext(aiter(Source()))''' if phase=='step' else '''async fn work(value){tool("fixture.iter.wait",value)}
await acollect(async (await work(x) for x in [42]))'''
                with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
                    p=app.create_project('cancel-iteration',source=source,capabilities=['fixture.iter.wait','fixture.iter.close'])
                    with observe_iteration_frames() as seen:
                        run=app.start(p['id'],p['version'],None)
                        try:self.assertTrue(entered.wait(5));run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled')
                        finally:run.cancel()
                    self.assertTrue(exited.is_set());self.assertEqual(effects,[])
                    self.assertIn('IterationStepFrame',seen['frames'])
                    if phase=='projection':self.assertIn('ComprehensionWalkFrame',seen['frames'])

    def test_bidirectional_delegation_preserves_send_throw_and_cleanup_order(self):
        for flavor in ('fn*','async fn*'):
            source='var seen=[];'+flavor+''' inner(){try{let value=yield 1;yield value}catch e{yield e.value}
finally{seen=append(seen,"inner")};return 9}
async fn* outer(){try{return yield from inner()}finally{seen=append(seen,"outer")}}
let stream=outer();let values=[await anext(stream),await asend(stream,42),await athrow(stream,43)];
await aclose(stream);[values,seen]'''
            expected=[[{'done':False,'value':1},{'done':False,'value':42},{'done':False,'value':43}],['inner','outer']]
            with self.subTest(flavor=flavor), observe_iteration_frames() as seen:
                self.assertEqual(self.run_source(source)[0],expected)
            self.assertTrue({'IterationDelegateFrame','StreamResumeFrame','StreamCloseFrame'} <= seen['frames'],seen)

    def test_borrowed_cursor_short_circuit_and_callback_error_preserve_position(self):
        source='''class Cursor{var reads=0;var closes=0;fn __iter__(self){self}
fn __next__(self){self.reads=self.reads+1;{done:false,value:self.reads}}
fn __close__(self){self.closes=self.closes+1;null}}
let raw=Cursor();let cursor=iter(raw);let decisive=any(fn(x){x==2},cursor);
let afterShort=raw.reads;let failed=try{map(fn(x){throw "failed"},cursor)}catch e{e.value};
let afterError=raw.reads;let closesBefore=raw.closes;let nextValue=next(cursor).value;
close(cursor);close(cursor);[decisive,afterShort,failed,afterError,closesBefore,nextValue,raw.closes]'''
        with observe_iteration_frames() as seen:
            self.assertEqual(self.run_source(source)[0],[True,2,'failed',3,0,4,1])
        self.assertTrue({'IterationConsumeFrame','IterationStepFrame','IterationCloseFrame'} <= seen['frames'],seen)

    def test_http_sdk_and_cli_execute_exact_same_deployment_source(self):
        with Framework(':memory:',settings=Settings(':memory:')) as app,tempfile.TemporaryDirectory() as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]))
                p=client.create_project('iteration-http',source=SOURCE,capabilities=[])
                with observe_iteration_frames() as seen:
                    self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(12),EXPECTED)
                self.assert_all_frames(seen)
                self.assertEqual(client.get_project(p['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE)
                env={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('MODEL','PROVIDER','KEY','TOKEN'))}
                env['CHAOS_CONFIG']=str(Path(folder)/'settings.json');env['PYTHONPATH']=str(ROOT)
                for command in ('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],
                        cwd=folder,env=env,capture_output=True,text=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command=='fmt':self.assertEqual(parse_program(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                    if command=='ir':self.assertEqual(json.loads(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_inert_catalogue_documented_and_deployment_examples(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('authoring executed callback')):
            report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'],[]);self.assertTrue(compiled)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash'])
        self.assertEqual(len([v for v in Evaluator().env.values.values() if type(v) is Native]),335)
        self.assertEqual(len(all_specs()),381);self.assertEqual(len(STDLIB),21)
        self.assertIn(DOCUMENTED.strip(),Documentation().page('comprehensions')['markdown'])
        with observe_iteration_frames() as seen:
            self.assertEqual(self.run_source(formatted)[0],DOCUMENTED_EXPECTED)
        self.assertTrue({'ComprehensionFrame','ComprehensionWalkFrame','StreamResumeFrame'} <= seen['frames'],seen)
        with observe_iteration_frames() as seen:
            result,events=self.run_source(SOURCE)
        self.assertEqual(result,EXPECTED);self.assert_all_frames(seen)
        counts={kind:sum(e['type']==kind for e in events) for kind in
            ('bot.scope.opened','bot.scope.closed','bot.result.committed','bot.decision.created')}
        self.assertEqual(counts,{'bot.scope.opened':1,'bot.scope.closed':1,'bot.result.committed':1,'bot.decision.created':3})
