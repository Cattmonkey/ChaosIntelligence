"""Existing native recovery operations share the actual language cursor."""
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
from chaos.execution_conditions import (ConditionRequestFrame, RecoveryCallbackFrame,
    ConditionSignalFrame, ConditionDebuggerFrame, RestartComputeFrame,
    RestartInvokeFrame, RestartCaseFrame)
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
from chaos.store import Conflict

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_FRAMES = (ConditionRequestFrame, RecoveryCallbackFrame, ConditionSignalFrame,
    ConditionDebuggerFrame, RestartComputeFrame, RestartInvokeFrame, RestartCaseFrame)
PREVIOUS_FRAMES = (ContextFrame, PromptScopeFrame, CancelShieldFrame, TaskGroupFrame,
    GroupBodyFrame, ControlCallbackFrame, PromptHandlerFrame, PromptCallFrame,
    ControlBarrierFrame, DynamicWindFrame, BotRunFrame, BotHostFrame, BotCallbackFrame,
    BotPhaseFrame, BotPhaseBodyFrame, BotCloseOwnedFrame, BotCleanupFrame,
    BotDecisionFrame, BotDrainFrame, BotOutcomeFrame, BotCommitFrame)
RECOVERY_NAMES = {cls.__name__ for cls in RECOVERY_FRAMES}
ALL_NAMES = RECOVERY_NAMES | {cls.__name__ for cls in PREVIOUS_FRAMES}
OPERATIONS = {'signal', 'error', 'debugger', 'compute', 'find', 'invoke', 'case'}

RECOVERY_SOURCE = '''var masks=[];
with handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,c.kind)}}]) as outer{
 with handler_bind([{predicate:fn(c){true},handler:fn(c){
  with handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,"new")}}]) as fresh{signal_condition(condition("nested"))};
  masks=append(masks,"inner")
 }}]) as inner{signal_condition(condition("root"))}
};
let inline=with restart_bind([{name:"inline",test:fn(c){c!=null&&c.kind=="missing"},function:fn(x){x+1}}]) as scope{
 let offered=compute_restarts(condition("missing"));
 let chosen=find_restart("inline",condition("missing"));
 [length(offered),restart_info(chosen).mode,invoke_restart(chosen,[40])]
};
var cleanup=[];
let captured=multiple_value_list(fn(){restart_case(async fn(){
 try{with handler_bind([{predicate:async fn(c){await sleep(0);true},handler:async fn(c){await sleep(0);invoke_restart("use",[c.["data"].fallback])}}]) as policy{
  error_condition(condition("missing",data={fallback:41}))
 }}finally{cleanup=append(cleanup,"cleanup")}
},[{name:"use",function:async fn(x){await sleep(0);cleanup=append(cleanup,"restart");return_values(x+1,find_restart("use")==null)}}])});
let unhandled=try{error_condition(condition("unhandled"))}catch e{e.code};
let noHost=try{invoke_debugger(condition("explicit"))}catch e{e.code};
let recovered=[inline,captured,masks,cleanup,unhandled,noHost];
'''
RECOVERY_EXPECTED = [[1, 'inline', 41], [42, True],
    ['new', 'nested', 'inner', 'root'], ['cleanup', 'restart'],
    'condition_error', 'condition_error']

SOURCE = RECOVERY_SOURCE + '''import "std/context" as C;
var events=[];
@C.asynccontextmanager
async fn* resource(){try{await sleep(0);yield {value:41}}finally{await sleep(0);events=append(events,"closed")}}
async fn* remaining(){try{yield 0}finally{events=append(events,"streamClosed")}}
fn plan(s){s.input}
async fn execute(p,s){
 let held=remaining();await anext(held);
 with cancel_shield() as shield{async with task_group() as group{
  async with resource() as {value:value}{let tag=prompt_tag("package");with prompt_scope(tag) as scope{
   control_barrier(async fn(){
    let answer=call_escape(async fn(exit){dynamic_wind(fn(){null},async fn(){await sleep(0);exit(value+1)},fn(){null})});
    call_prompt(tag,fn(){abort_prompt(tag,answer)},fn(result){result})
   })
  }}
 }}
}
fn verify(s){{done:true,context:{retired:length(events)},output:s.evidence[0]}}
let worker=bot("typed recovery deployment",plan,execute,verify,context={retired:0});
let pending=worker(null);let before=events;
let actual=await pending;
[actual.output,events,actual.status,actual.state.context,before,recovered]
'''
EXPECTED = [42, ['closed', 'streamClosed'], 'achieved', {'retired': 2}, [], RECOVERY_EXPECTED]

DOCUMENTED = '''var seen=[];
let answer=multiple_value_list(fn(){
 restart_case(fn(){
  with handler_bind([{predicate:fn(c){c.kind=="missing"},handler:fn(c){invoke_restart("use",[c.["data"].fallback])}}]) as policy{
   try{error_condition(condition("missing",data={fallback:41}))}
   finally{seen=append(seen,"cleanup")}
  }
 },[{name:"use",function:fn(value){
  seen=append(seen,"restart");return_values(value+1,find_restart("use")==null)
 }}])
});
[answer,seen]
'''
DOCUMENTED_EXPECTED = [[42, True], ['cleanup', 'restart']]
FUNCTION = '''fn(value){restart_case(fn(){
 with handler_bind([{predicate:fn(c){true},handler:fn(c){invoke_restart("use",[c.["data"].fallback])}}]) as policy{
  error_condition(condition("missing",data={fallback:value}))
 }
},[{name:"use",function:fn(x){x+1}}])}'''
DEBUG_SOURCE = '''restart_case(fn(){
 try{error_condition(condition("missing","choose a value",{fallback:41}))}
 finally{tool("fixture.condition.audit","cleanup")}
},[{name:"same",function:fn(value){tool("fixture.condition.audit","wrong");0}},
 {name:"same",test:fn(c){c.kind=="missing"},function:fn(value:Int,/,*,bonus:Int=1){
  tool("fixture.condition.audit","chosen");[value+bonus,find_restart("same")==null]
 }}])'''


@contextmanager
def observe_recovery_frames():
    seen = {'frames': set(), 'owners': set(), 'cursors': set(),
            'operations': set(), 'steps': {}, 'phases': {}}
    with ExitStack() as stack:
        for cls in RECOVERY_FRAMES + PREVIOUS_FRAMES:
            original = cls.step
            def observed(frame, cursor, value, error, original=original):
                name = type(frame).__name__
                seen['frames'].add(name);seen['owners'].add(type(cursor.owner).__name__)
                seen['cursors'].add(id(cursor))
                seen['steps'][name] = seen['steps'].get(name, 0) + 1
                seen['phases'].setdefault(name, set()).add(frame.phase)
                if type(frame) is ConditionRequestFrame:
                    seen['operations'].add(frame.operation)
                return original(frame, cursor, value, error)
            stack.enter_context(patch.object(cls, 'step', observed))
        yield seen


class ConditionFrameSurfaces(unittest.TestCase):
    def run_source(self, source, *, registry=None, capabilities=None, mode='source'):
        with Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
            p = app.create_project('recovery-frames', source=source,
                capabilities=[] if capabilities is None else capabilities)
            run = app.start(p['id'], p['version'], None, mode=mode)
            result = run.result(10)
            events = app.events(p['id'], run_id=run.id, limit=300)['items']
            return result, events

    def assert_recovery(self, seen, *, debugger=False):
        expected = RECOVERY_NAMES if debugger else RECOVERY_NAMES - {'ConditionDebuggerFrame'}
        self.assertTrue(expected <= seen['frames'], seen)
        self.assertEqual(seen['owners'], {'Evaluator'})
        self.assertTrue(all(count > 0 for count in seen['steps'].values()))

    def pause(self, run):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = run.debugger()
            if state['status'] == 'paused':
                return state
            if state['status'] not in ('running', 'paused'):
                self.fail(str(run.get()))
            time.sleep(.005)
        self.fail('real Condition debugger did not pause')

    def registry(self, effects):
        registry = Registry()
        @registry.tool('fixture.condition.audit')
        def audit(value, context):
            context.checkpoint();effects.append(value);return value
        return registry

    def test_source_graph_and_both_ir_dialects_execute_all_twenty_eight_frames(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode), observe_recovery_frames() as seen:
                self.assertEqual(self.run_source(SOURCE, mode=mode)[0], EXPECTED)
            self.assert_recovery(seen, debugger=True)
            self.assertEqual(seen['frames'], ALL_NAMES)
            self.assertEqual(seen['operations'], OPERATIONS)
        for version in (1, 2):
            # Every sequence index in this shared fixture is nonnegative for v1.
            ir = parse_program(SOURCE, language_version=version)
            formatted = format_program(ir)
            self.assertEqual(parse_program(formatted, language_version=version)['semanticHash'], ir['semanticHash'])
            definition = {'kind': 'program', 'entry': 'main.chaos',
                'files': {'main.chaos': formatted}, 'languageVersions': {'main.chaos': version}}
            source = 'let p=program_from('+json.dumps(definition)+');await p(null)'
            with self.subTest(version=version), observe_recovery_frames() as seen:
                self.assertEqual(self.run_source(source)[0], EXPECTED)
            self.assert_recovery(seen, debugger=True)
            self.assertEqual(seen['frames'], ALL_NAMES)
            self.assertEqual(seen['operations'], OPERATIONS)

    def test_code_program_macro_and_compile_targets_use_the_original_recovery(self):
        definition = {'kind': 'program', 'entry': 'main.chaos',
            'files': {'main.chaos': FUNCTION.replace('fn(value', 'fn build(value')+';export{build};'},
            'languageVersions': {'main.chaos': 2}}
        program = {'kind': 'program', 'entry': 'main.chaos',
            'files': {'main.chaos': 'let build='+FUNCTION+';build(41)'},
            'languageVersions': {'main.chaos': 2}}
        sources = ['let build=function_from(code_from('+json.dumps(FUNCTION)+'));build(41)',
            'let M=module_from(program_from('+json.dumps(definition)+'));M.build(41)',
            'let p=program_from('+json.dumps(program)+');await p(null)',
            'macro invoke(f,x){quote{unquote(f)(unquote(x))}};let build=function_from(code_from('+json.dumps(FUNCTION)+'));invoke!(build,41)']
        expected = {'ConditionRequestFrame', 'RecoveryCallbackFrame', 'ConditionSignalFrame',
                    'RestartComputeFrame', 'RestartInvokeFrame', 'RestartCaseFrame'}
        for source in sources:
            with self.subTest(source=source), observe_recovery_frames() as seen:
                self.assertEqual(self.run_source(source)[0], 42)
            self.assertTrue(expected <= seen['frames'], seen)
        for target in ('interpreter', 'auto', 'auto-flow', 'auto-control', 'portable'):
            selected = 'auto-flow' if target == 'portable' else target
            source = FUNCTION.replace('fn(value', 'fn build(value')+';let compiled=compile_function(build,target="'+selected+'");compiled(41)'
            with self.subTest(target=target), observe_recovery_frames() as seen:
                if target == 'portable':
                    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('fixture portable')):
                        self.assertEqual(self.run_source(source)[0], 42)
                else:
                    self.assertEqual(self.run_source(source)[0], 42)
            self.assertTrue(expected <= seen['frames'], seen)

    def test_actual_agent_tool_team_and_bot_recover_once_with_real_effects(self):
        effects, provider_steps = [], []
        registry = self.registry(effects)
        @registry.provider('fixture.condition.provider')
        def provider(request, context):
            context.checkpoint();provider_steps.append(request.step)
            if request.step == 1:return ToolCall('recover', request.input)
            return Final(request.history[-1]['output'])
        team = {'kind': 'team', 'name': 'recovery team', 'entry': 'work', 'roles': [
            {'id': 'work', 'parent': None, 'capabilities': ['fixture.condition.audit'],
             'agent': {'kind': 'agent', 'name': 'fallback', 'config': {'provider': 'local', 'tools': ['fixture.condition.audit']}}}]}
        source = 'let recover='+FUNCTION+''';
async fn work(value:Int){tool("fixture.condition.audit",["agent",value]);recover(value)};
agent helper{provider:"fixture.condition.provider",tools:["fixture.condition.audit"]};
let bound=agent_bind(helper,[agent_tool("recover",work,input_type="Int",output_type="Int",capabilities=["fixture.condition.audit"])]);
async fn role(value){await sleep(0);tool("fixture.condition.audit",["team",value]);recover(value)};
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(p,s){
 let first=restart_case(fn(){invoke_restart("agent",[p])},[{name:"agent",function:async fn(value){await bound(value)}}]);
 let second=restart_case(fn(){invoke_restart("team",[first])},[{name:"team",function:async fn(value){await crew(value)}}]);
 tool("fixture.condition.audit",["bot",second]);[first,second,recover(second)]};
fn verify(s){{done:true,context:s.context,output:s.evidence[0]}};
let worker=bot("recover explicitly",fn(s){s.input},execute,verify);(await worker(41)).output'''.replace('TEAM', json.dumps(team))
        with observe_recovery_frames() as seen:
            result, events = self.run_source(source, registry=registry,
                capabilities=['fixture.condition.audit'])
        self.assertEqual(result, [42, 43, 44])
        self.assertEqual(effects, [['agent', 41], ['team', 42], ['bot', 43]])
        self.assertEqual(provider_steps, [1, 2])
        self.assertEqual(sum(event['type'] == 'agent.language_tool' for event in events), 1)
        self.assertTrue({'RestartCaseFrame', 'RestartInvokeFrame', 'RecoveryCallbackFrame',
                         'BotCallbackFrame', 'BotCommitFrame'} <= seen['frames'], seen)

    def test_cold_tasks_and_generator_own_recovery_scopes_stay_separate(self):
        source = '''async fn child(){[find_restart("parent"),compute_restarts()]};
fn* stream(){with restart_bind([{name:"own",function:fn(x){x}}]) as own{
 yield find_restart("parent");yield invoke_restart("own",[42])
}};
let taskResult=with restart_bind([{name:"parent",function:fn(x){x+1}}]) as parent{
 let pending=child();let before=task_info(pending).state;
 [before,await pending,invoke_restart("parent",[41])]
};
let values=stream();let first=with restart_bind([{name:"parent",function:fn(){0}}]) as parent{next(values)};
let second=next(values);close(values);[taskResult,first.value,second.value]'''
        with observe_recovery_frames() as seen:
            self.assertEqual(self.run_source(source)[0], [['created', [None, []], 42], None, 42])
        self.assertTrue({'RestartComputeFrame', 'RestartInvokeFrame', 'RecoveryCallbackFrame'} <= seen['frames'])
        self.assertGreater(len(seen['cursors']), 2)

    def test_framework_pause_lists_exact_offered_identity_before_any_effect(self):
        effects = []
        with Framework(':memory:', registry=self.registry(effects), settings=Settings(':memory:')) as app:
            p = app.create_project('pause-recovery', source=DEBUG_SOURCE,
                capabilities=['fixture.condition.audit'])
            with observe_recovery_frames() as seen:
                run = app.start_debug(p['id'], p['version'], pause_on_entry=False, pause_on_condition=True)
                state = self.pause(run)
                self.assertEqual(state['reason'], 'condition')
                choices = state['conditionRecovery']['restarts']
                self.assertEqual([item['name'] for item in choices], ['same', 'same'])
                self.assertEqual(len({item['id'] for item in choices}), 2)
                self.assertEqual(effects, [])
                for _ in range(2):
                    self.assertEqual(run.debugger()['conditionRecovery'], state['conditionRecovery'])
                for pause_id, restart_id in ((state['pauseId']-1, choices[1]['id']), (state['pauseId'], 'same')):
                    with self.assertRaises(Conflict):
                        run.debug_command('restart', pause_id=pause_id, restart_id=restart_id, args=[41], kwargs={'bonus': 1})
                    self.assertEqual(run.debugger()['pauseId'], state['pauseId'])
                self.assertEqual(effects, [])
                run.debug_command('restart', pause_id=state['pauseId'], restart_id=choices[1]['id'], args=[41], kwargs={'bonus': 1})
                self.assertEqual(run.result(5), [42, True])
                self.assertEqual(effects, ['cleanup', 'chosen'])
                with self.assertRaises(Conflict):
                    run.debug_command('restart', pause_id=state['pauseId'], restart_id=choices[1]['id'], args=[0], kwargs={})
            self.assert_recovery(seen, debugger=True)

    def test_http_sdk_and_cli_inspect_and_choose_same_real_debugger_restart(self):
        effects = []
        with Framework(':memory:', registry=self.registry(effects), settings=Settings(':memory:')) as app, tempfile.TemporaryDirectory() as folder:
            server = make_server('127.0.0.1', 0, framework=app,
                state_dir=Path(folder)/'control', update_home=Path(folder)/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_address[1]))
                p = client.create_project('http-recovery', source=DEBUG_SOURCE,
                    capabilities=['fixture.condition.audit'])
                with observe_recovery_frames() as seen:
                    run = client.start_debug(p['id'], p['version'], pause_on_entry=False, pause_on_condition=True)
                    state = self.pause(run)
                    env = {k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('MODEL','PROVIDER','KEY','TOKEN'))}
                    env['CHAOS_CONFIG'] = str(Path(folder)/'settings.json')
                    prefix = [sys.executable, '-B', '-m', 'chaos', 'debug']
                    server_arg = ['--server', client.base_url.rsplit('/api', 1)[0]]
                    inspected = subprocess.run(prefix+['inspect', run.id]+server_arg,
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(inspected.returncode, 0, inspected.stderr)
                    self.assertEqual(json.loads(inspected.stdout)['conditionRecovery'], state['conditionRecovery'])
                    with self.assertRaises(Exception):
                        client.debug_command(run.id, 'restart', pause_id=state['pauseId'], restart_id='same', args=[41], kwargs={})
                    self.assertEqual(run.debugger()['status'], 'paused');self.assertEqual(effects, [])
                    selected = state['conditionRecovery']['restarts'][1]['id']
                    command = prefix+['restart', run.id]+server_arg+['--pause-id', str(state['pauseId']),
                        '--restart-id', selected, '--args-json', '[41]', '--kwargs-json', '{"bonus":1}']
                    restored = subprocess.run(command, cwd=ROOT, env=env,
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(restored.returncode, 0, restored.stderr)
                    self.assertEqual(run.result(5), [42, True]);self.assertEqual(effects, ['cleanup', 'chosen'])
                self.assert_recovery(seen, debugger=True)
            finally:
                server.shutdown();server.server_close();thread.join(2)

    def test_cancelling_real_paused_recovery_never_calls_restart_clause(self):
        effects = []
        source = '''with restart_bind([{name:"use",function:fn(){tool("fixture.condition.audit","forbidden");42}}]) as scope{
 error_condition(condition("missing"))
}'''
        with Framework(':memory:', registry=self.registry(effects), settings=Settings(':memory:')) as app:
            p = app.create_project('cancel-recovery', source=source, capabilities=['fixture.condition.audit'])
            with observe_recovery_frames() as seen:
                run = app.start_debug(p['id'], p['version'], pause_on_entry=False, pause_on_condition=True)
                state = self.pause(run);self.assertEqual(effects, [])
                self.assertTrue(run.cancel())
                self.assertEqual(run.wait(5)['status'], 'cancelled')
                with self.assertRaises(Conflict):
                    run.debug_command('restart', pause_id=state['pauseId'],
                        restart_id=state['conditionRecovery']['restarts'][0]['id'], args=[], kwargs={})
            self.assertEqual(effects, [])
            self.assertIn('ConditionDebuggerFrame', seen['frames'])

    def test_authoring_and_shared_catalogue_remain_inert_and_unchanged(self):
        with patch.object(Evaluator, '_s_invoke', side_effect=AssertionError('authoring invoked a callback')), \
             patch('chaos.condition_values.make', side_effect=AssertionError('authoring constructed a condition')):
            report = analyze(DOCUMENTED)
            formatted = format_source(DOCUMENTED)
            compiled = compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'], [])
        self.assertTrue(compiled)
        self.assertEqual(parse_program(formatted)['semanticHash'], parse_program(DOCUMENTED)['semanticHash'])
        self.assertEqual(len([value for value in Evaluator().env.values.values() if type(value) is Native]), 335)
        self.assertEqual(len(all_specs()), 381);self.assertEqual(len(STDLIB), 21)
        with observe_recovery_frames() as seen:
            self.assertEqual(self.run_source(formatted)[0], DOCUMENTED_EXPECTED)
        self.assertIn('RestartCaseFrame', seen['frames'])

    def test_documented_and_deployment_literals_have_independent_expected_results(self):
        markdown = Documentation().page('conditions')['markdown'].replace('\r\n', '\n')
        self.assertIn(DOCUMENTED.strip(), markdown)
        with observe_recovery_frames() as seen:
            self.assertEqual(self.run_source(DOCUMENTED)[0], DOCUMENTED_EXPECTED)
        self.assertIn('RestartCaseFrame', seen['frames'])
        with observe_recovery_frames() as seen:
            result, events = self.run_source(SOURCE)
        self.assertEqual(result, EXPECTED)
        self.assertEqual(seen['frames'], ALL_NAMES)
        self.assertEqual(seen['operations'], OPERATIONS)
        counts = {kind:sum(event['type'] == kind for event in events) for kind in
            ('bot.scope.opened','bot.scope.closed','bot.result.committed','bot.decision.created')}
        self.assertEqual(counts, {'bot.scope.opened':1,'bot.scope.closed':1,
            'bot.result.committed':1,'bot.decision.created':3})
