"""The real Bot lifecycle frames share the original public execution entries."""
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
from chaos.execution_bot import (BotRunFrame, BotHostFrame, BotCallbackFrame,
                                 BotPhaseFrame, BotPhaseBodyFrame, BotCloseOwnedFrame,
                                 BotCleanupFrame, BotDecisionFrame, BotDrainFrame,
                                 BotOutcomeFrame, BotCommitFrame)
from chaos.execution_control import (ControlCallbackFrame, PromptHandlerFrame,
                                     PromptCallFrame, ControlBarrierFrame, DynamicWindFrame)
from chaos.execution_frames import (ExecutionCursor, ContextFrame, PromptScopeFrame,
                                    CancelShieldFrame, TaskGroupFrame, GroupBodyFrame)
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native
from chaos.modules import STDLIB
from chaos.native_jit import BackendUnavailable
from chaos.native_specs import all_specs
from chaos.program import format_program, parse_program
from chaos.runtime import Context
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.store import Conflict

ROOT = Path(__file__).resolve().parents[1]
BOT_FRAMES = (BotRunFrame, BotHostFrame, BotCallbackFrame, BotPhaseFrame,
              BotPhaseBodyFrame, BotCloseOwnedFrame, BotCleanupFrame,
              BotDecisionFrame, BotDrainFrame, BotOutcomeFrame, BotCommitFrame)
OLD_FRAMES = (ContextFrame, PromptScopeFrame, CancelShieldFrame, TaskGroupFrame,
              GroupBodyFrame, ControlCallbackFrame, PromptHandlerFrame,
              PromptCallFrame, ControlBarrierFrame, DynamicWindFrame)
BOT_NAMES = {cls.__name__ for cls in BOT_FRAMES}
CORE_NAMES = BOT_NAMES - {'BotCleanupFrame'}
SOURCE = '''import "std/context" as C;
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
fn verify(s){{done:true,context:{retired:length(events)},output:s.evidence[-1]}}
let worker=bot("typed Bot deployment",plan,execute,verify,context={retired:0});
let pending=worker(null);let before=events;
let actual=await pending;
[actual.output,events,actual.status,actual.state.context,before]
'''
EXPECTED = [42, ['closed', 'streamClosed'], 'achieved', {'retired': 2}, []]
DOCUMENTED = '''fn plan(s){s.context.next}
fn execute(p,s){p*2}
fn verify(s){
 let next=s.context.next+1;
 {done:next>2,context:{next:next},output:s.evidence}
}
let worker=bot({objective:"two checks"},plan,execute,verify,context={next:1});
let pending=worker();let before=bot_info(worker).busy;
let actual=await pending;
[before,actual.status,actual.output,actual.state.context]
'''
SIMPLE = '''fn plan(s){s.input};fn execute(p,s){p+1};
fn verify(s){{done:true,context:s.context,output:s.evidence[-1]}};
let worker=bot("one checked step",plan,execute,verify);
let actual=await worker(41);actual.output'''


@contextmanager
def observe_bot_frames():
    """Observe actual steps on every original logical cursor, including parents."""
    seen = {'frames': set(), 'owners': set(), 'cursors': set(),
            'operations': set(), 'steps': {}, 'phases': {}}
    with ExitStack() as stack:
        for cls in BOT_FRAMES + OLD_FRAMES:
            original = cls.step
            def step(frame, cursor, value, error, original=original):
                name = type(frame).__name__
                seen['frames'].add(name)
                seen['owners'].add(type(cursor.owner).__name__)
                seen['cursors'].add(id(cursor))
                seen['steps'][name] = seen['steps'].get(name, 0) + 1
                seen['phases'].setdefault(name, set()).add(frame.phase)
                if type(frame) is BotHostFrame:
                    seen['operations'].add(frame.operation)
                return original(frame, cursor, value, error)
            stack.enter_context(patch.object(cls, 'step', step))
        yield seen


def await_release(release, context):
    deadline = time.monotonic() + 12
    while not release.wait(.01):
        context.checkpoint()
        if time.monotonic() > deadline:
            raise AssertionError('isolated Bot fixture was not released')
    context.checkpoint()


class BotFrameSurfaces(unittest.TestCase):
    def run_source(self, source, *, registry=None, capabilities=None, mode='source'):
        with Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('typed-bot', source=source,
                                         capabilities=[] if capabilities is None else capabilities)
            run = app.start(project['id'], project['version'], None, mode=mode)
            result = run.result(10)
            events = app.events(project['id'], run_id=run.id, limit=300)['items']
        return result, events

    def assert_bot_frames(self, seen, *, cleanup=False):
        self.assertTrue((BOT_NAMES if cleanup else CORE_NAMES) <= seen['frames'], seen)
        self.assertEqual(seen['owners'], {'Evaluator'})
        self.assertTrue({'bot_open', 'bot_receive', 'bot_decision', 'bot_outcome',
                         'bot_commit'} <= seen['operations'], seen)
        self.assertTrue(all(count > 0 for count in seen['steps'].values()))

    def test_source_graph_and_both_ir_dialects_execute_real_bot_and_resource_frames(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode), observe_bot_frames() as seen:
                self.assertEqual(self.run_source(SOURCE, mode=mode)[0], EXPECTED)
            self.assert_bot_frames(seen, cleanup=True)
            self.assertEqual(seen['frames'], BOT_NAMES | {cls.__name__ for cls in OLD_FRAMES})
        for version in (1, 2):
            # Legacy v1 sequence indexes require a nonnegative key.
            dialect_source = SOURCE if version == 2 else SOURCE.replace(
                's.evidence[-1]', 's.evidence[length(s.evidence)-1]')
            ir = parse_program(dialect_source, language_version=version)
            formatted = format_program(ir)
            self.assertEqual(parse_program(formatted, language_version=version)['semanticHash'], ir['semanticHash'])
            definition = {'kind': 'program', 'entry': 'main.chaos',
                          'files': {'main.chaos': formatted},
                          'languageVersions': {'main.chaos': version}}
            source = 'let program=program_from('+json.dumps(definition)+');await program(null)'
            with self.subTest(version=version), observe_bot_frames() as seen:
                self.assertEqual(self.run_source(source)[0], EXPECTED)
            self.assert_bot_frames(seen, cleanup=True)
            self.assertEqual(seen['frames'], BOT_NAMES | {cls.__name__ for cls in OLD_FRAMES})

    def test_code_program_macro_and_compiler_targets_share_original_bot_callbacks(self):
        function = '''fn(value){fn plan(s){s.input};fn execute(p,s){p+1};
          fn verify(s){{done:true,context:s.context,output:s.evidence[-1]}};
          let worker=bot("generated",plan,execute,verify);worker(value)}'''
        module = {'kind': 'program', 'entry': 'main.chaos',
                  'files': {'main.chaos': function.replace('fn(value', 'fn build(value')+';export{build};'},
                  'languageVersions': {'main.chaos': 2}}
        program = {'kind': 'program', 'entry': 'main.chaos',
                   'files': {'main.chaos': SIMPLE}, 'languageVersions': {'main.chaos': 2}}
        sources = ['let build=function_from(code_from('+json.dumps(function)+'));let actual=await build(41);actual.output',
                   'let M=module_from(program_from('+json.dumps(module)+'));let actual=await M.build(41);actual.output',
                   'let program=program_from('+json.dumps(program)+');await program(null)',
                   'macro invoke(f,x){quote{unquote(f)(unquote(x))}};let build=function_from(code_from('+json.dumps(function)+'));let actual=await invoke!(build,41);actual.output']
        for source in sources:
            with self.subTest(source=source), observe_bot_frames() as seen:
                self.assertEqual(self.run_source(source)[0], 42)
            self.assert_bot_frames(seen)
        for target in ('interpreter', 'auto', 'auto-flow', 'auto-control', 'portable'):
            selected = 'auto-flow' if target == 'portable' else target
            source = function.replace('fn(value', 'fn build(value')+';let compiled=compile_function(build,target="'+selected+'");let actual=await compiled(41);actual.output'
            with self.subTest(target=target), observe_bot_frames() as seen:
                if target == 'portable':
                    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('fixture portable')):
                        self.assertEqual(self.run_source(source)[0], 42)
                else:
                    self.assertEqual(self.run_source(source)[0], 42)
            self.assert_bot_frames(seen)

    def test_actual_agent_tool_team_and_nested_bot_callbacks_execute_once(self):
        registry = Registry()
        effects, provider_steps, outputs = [], [], []
        @registry.tool('fixture.bot.audit')
        def audit(value, context):
            context.checkpoint();effects.append(value);return value
        @registry.provider('fixture.bot.agent')
        def provider(request, context):
            context.checkpoint();provider_steps.append(request.step)
            if request.step == 1:
                return ToolCall('work', request.input)
            output = request.history[-1]['output'];outputs.append(output);return Final(output)
        team = {'kind': 'team', 'name': 'typed Bot crew', 'entry': 'work', 'roles': [
            {'id': 'work', 'parent': None, 'capabilities': ['fixture.bot.audit'],
             'agent': {'kind': 'agent', 'name': 'fallback', 'config': {'provider': 'local', 'tools': ['fixture.bot.audit']}}}]}
        source = '''fn verify(s){{done:true,context:s.context,output:s.evidence[-1]}};
async fn work(value:Int){let inner=bot("tool work",fn(s){s.input},
  fn(p,s){tool("fixture.bot.audit",["action",p]);p+1},verify);(await inner(value)).output};
agent helper{provider:"fixture.bot.agent",tools:["fixture.bot.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.bot.audit"])]);
async fn role(value){await sleep(0);tool("fixture.bot.audit",["team",value]);value+1};
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(p,s){let first=await bound(p);let second=await crew(first);
 let child=bot("child",fn(state){state.input},fn(value,state){tool("fixture.bot.audit",["child",value]);value+1},verify);
 [first,second,(await child(second)).output]};
let outer=bot("parent",fn(s){s.input},execute,verify);(await outer(41)).output'''.replace('TEAM', json.dumps(team))
        with observe_bot_frames() as seen:
            result, events = self.run_source(source, registry=registry, capabilities=['fixture.bot.audit'])
        self.assertEqual(result, [42, 43, 44]);self.assert_bot_frames(seen)
        self.assertEqual(effects, [['action', 41], ['team', 42], ['child', 43]])
        self.assertEqual(provider_steps, [1, 2]);self.assertEqual(outputs, [42])
        self.assertEqual(sum(e['type'] == 'agent.language_tool' for e in events), 1)
        self.assertEqual([e['type'] for e in events if e['type'].startswith('team.role.')],
                         ['team.role.started', 'team.role.completed'])
        audit_events = [e['type'] for e in events if e['payload'].get('tool') == 'fixture.bot.audit']
        self.assertEqual(audit_events, ['tool.started', 'tool.completed'] * 3)
        opened = [e['payload'] for e in events if e['type'] == 'bot.scope.opened']
        self.assertEqual(len(opened), 3)
        self.assertEqual(sum(row['parentScopeId'] is None for row in opened), 1)

    def test_cold_concurrent_contexts_and_scoped_guidance_keep_separate_authority(self):
        registry = Registry();release = threading.Event()
        entered = {value: threading.Event() for value in (2, 3)};provider_calls = []
        @registry.provider('fixture.bot.context')
        def provider(request, context):
            provider_calls.append(request.input);entered[request.input].set()
            await_release(release, context);return Final(request.input)
        source = '''let variable=context_var("Bot scope",0);agent gate{provider:"fixture.bot.context",tools:[]};
async fn worker(n){with context_bind([{variable:variable,value:n}]) as binding{
 var plans=0;
 async fn plan(s){plans+=1;if length(s.guidance)>0{s.guidance[-1].input.answer}else{
  await progress_emit("plan",{n:n});gate(s.input)}};
 let bot_value=bot("scoped",plan,fn(p,s){[p,variable.get()]},
  fn(s){{done:true,context:s.context,output:s.evidence[-1]}},mailbox="scoped");
 let pending=bot_value(n);let before=plans;let actual=await pending;let repeated=await pending;
 [before,actual.output,variable.get(),plans,repeated.output]
}};
[await gather([worker(2),worker(3)]),variable.get()]'''
        with observe_bot_frames() as seen, Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('contexts', source=source, capabilities=[])
            run = app.start(project['id'], project['version'], None, mode='source')
            try:
                self.assertTrue(entered[2].wait(4));self.assertTrue(entered[3].wait(4))
                scopes = app.guidance_scopes(run.id)['scopes'];self.assertEqual(len(scopes), 2)
                events = app.events(project['id'], run_id=run.id, limit=300)['items']
                targets = {e['payload']['data']['n']: {'kind': 'botScope', 'scopeId': e['payload']['scopeId']}
                           for e in events if e['type'] == 'bot.progress' and 'n' in e['payload'].get('data', {})}
                for n, answer in ((2, 20), (3, 30)):
                    receipt = run.guide({'answer': answer}, 'same', target=targets[n])
                    self.assertEqual(receipt['revision'], 1);self.assertEqual(receipt['target'], targets[n])
                release.set()
                self.assertEqual(run.result(10), [[[0, [20, 2], 2, 2, [20, 2]],
                                                  [0, [30, 3], 3, 2, [30, 3]]], 0])
                events = app.events(project['id'], run_id=run.id, limit=300)['items']
                self.assertEqual(app.guidance_scopes(run.id), {'runId': run.id, 'active': False, 'scopes': []})
            finally:
                release.set()
        self.assert_bot_frames(seen);self.assertGreater(len(seen['cursors']), 1)
        self.assertEqual(sorted(provider_calls), [2, 3])
        applied = [e['payload'] for e in events if e['type'] == 'bot.context.applied']
        self.assertEqual(len(applied), 2);self.assertEqual({row['scopeId'] for row in applied}, {t['scopeId'] for t in targets.values()})

    def test_mid_planning_guidance_replans_without_executing_the_old_proposal(self):
        registry = Registry();entered = threading.Event();release = threading.Event()
        thoughts, actions = [], []
        @registry.provider('fixture.bot.think')
        def think(request, context):
            thoughts.append(request.input);entered.set();await_release(release, context);return Final(0)
        @registry.tool('fixture.bot.act')
        def act(value, context):
            context.checkpoint();actions.append(value);return value
        source = '''agent thinker{provider:"fixture.bot.think",tools:[]};var plans=0;
async fn plan(s){plans+=1;await progress_emit("plan",{summary:"checking direction"});
 if length(s.guidance)>0{s.guidance[-1].input.value}else{thinker(s.input)}};
fn execute(p,s){tool("fixture.bot.act",p)};
fn verify(s){{done:true,context:s.context,output:s.evidence[-1]}};
let worker=bot("long goal",plan,execute,verify,context={kept:true});let actual=await worker(null);
[actual.status,actual.output,plans,actual.state.revision,map(fn(m){m.messageId},actual.state.guidance)]'''
        with observe_bot_frames() as seen, Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
            p = app.create_project('planning', source=source, capabilities=['fixture.bot.act'])
            run = app.start(p['id'], p['version'], None, mode='source')
            try:
                self.assertTrue(entered.wait(4))
                receipt = run.guide({'text': 'new direction', 'value': 42}, 'planning-change')
                self.assertEqual(receipt['status'], 'queued');release.set()
                self.assertEqual(run.result(10), ['achieved', 42, 2, 1, ['planning-change']])
                events = app.events(p['id'], run_id=run.id, limit=300)['items']
            finally:
                release.set()
        self.assert_bot_frames(seen);self.assertIn('bot_adopt', seen['operations'])
        self.assertEqual(thoughts, [None]);self.assertEqual(actions, [42])
        applied = [e['payload'] for e in events if e['type'] == 'bot.context.applied']
        self.assertEqual(len(applied), 1);self.assertEqual(applied[0]['messageId'], 'planning-change')
        self.assertEqual(applied[0]['revision'], receipt['revision'])
        self.assertEqual(sum(e['type'] == 'tool.completed' for e in events), 1)

    def test_mid_execute_guidance_preserves_completed_prefix_without_replay(self):
        registry = Registry();entered = threading.Event();release = threading.Event()
        completed, forbidden = [], []
        @registry.tool('fixture.bot.block')
        def block(value, context):
            completed.append(value);entered.set();await_release(release, context);return value
        @registry.tool('fixture.bot.after')
        def after(value, context):
            forbidden.append(value);return value
        source = '''var executions=0;fn execute(p,s){executions+=1;
 let first=tool("fixture.bot.block",p);tool("fixture.bot.after",first+1)};
let worker=bot("retain prefix",fn(s){41},execute,
 fn(s){{done:true,context:s.context,output:s.evidence[-1]}});
let actual=await worker();let remaining=await guidance_receive(0);
[actual.done,actual.status,executions,actual.state.evidence,remaining.received,remaining.message.input]'''
        with observe_bot_frames() as seen, Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
            p = app.create_project('execution', source=source, capabilities=['fixture.bot.block', 'fixture.bot.after'])
            run = app.start(p['id'], p['version'], None, mode='source')
            try:
                self.assertTrue(entered.wait(4));run.guide({'text': 'check completed work'}, 'execution-change');release.set()
                self.assertEqual(run.result(10), [False, 'needs_reconciliation', 1, [], True,
                                                 {'text': 'check completed work'}])
                events = app.events(p['id'], run_id=run.id, limit=300)['items']
            finally:
                release.set()
        self.assertTrue({'BotRunFrame', 'BotPhaseFrame', 'BotOutcomeFrame'} <= seen['frames'])
        self.assertEqual(completed, [41]);self.assertEqual(forbidden, [])
        self.assertEqual(sum(e['type'] == 'tool.completed' for e in events), 1)
        self.assertEqual([e for e in events if e['type'] == 'bot.context.applied'], [])
        actions = [e['payload'] for e in events if e['type'] == 'bot.action.completed']
        self.assertTrue(actions);self.assertTrue(all(e['status'] == 'completed' for e in actions))

    def test_terminal_commit_rejects_new_guidance_before_scope_retirement(self):
        entered = threading.Event();release = threading.Event()
        original = Context.guidance_operation
        def hold_commit(context, operation, payload, *args, **kwargs):
            packet = original(context, operation, payload, *args, **kwargs)
            if operation == 'bot_commit' and payload['terminal'] and packet['committed']:
                entered.set();deadline = time.monotonic() + 12
                while not release.wait(.01):
                    context.budget.tick()
                    if time.monotonic() > deadline:
                        raise AssertionError('isolated committed Bot fixture was not released')
            return packet
        with patch.object(Context, 'guidance_operation', hold_commit), observe_bot_frames() as seen:
            with Framework(':memory:', settings=Settings(':memory:')) as app:
                p = app.create_project('terminal', source=SIMPLE, capabilities=[])
                run = app.start(p['id'], p['version'], None, mode='source')
                try:
                    self.assertTrue(entered.wait(4))
                    with self.assertRaises(Conflict):
                        run.guide({'text': 'late'}, 'after-terminal')
                    release.set();self.assertEqual(run.result(10), 42)
                    events = app.events(p['id'], run_id=run.id, limit=300)['items']
                finally:
                    release.set()
        self.assert_bot_frames(seen)
        self.assertEqual(sum(e['type'] == 'bot.result.committed' for e in events), 1)
        self.assertEqual([e for e in events if e['type'] == 'guidance.queued'], [])
        self.assertEqual(sum(e['type'] == 'bot.scope.closed' for e in events), 1)

    def test_http_sdk_cli_and_inert_authoring_keep_the_same_bot_source(self):
        with patch.object(ExecutionCursor, 'send', side_effect=AssertionError('analysis executed Bot')), \
             patch.object(ExecutionCursor, '__next__', side_effect=AssertionError('analysis advanced Bot')):
            self.assertEqual(analyze(SOURCE)['diagnostics'], [])
            self.assertIn('call', compile_program(SOURCE)['summary']['effects'])
            formatted = format_source(SOURCE, line_width=56)
            self.assertEqual(format_source(formatted, line_width=56), formatted)
        with tempfile.TemporaryDirectory() as folder, Framework(':memory:', settings=Settings(':memory:')) as app:
            root = Path(folder)
            server = make_server('127.0.0.1', 0, framework=app, state_dir=root/'control', update_home=root/'updates')
            worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_port))
                p = client.create_project('typed-bot-http', source=SOURCE, capabilities=[])
                for mode in ('source', 'graph'):
                    with self.subTest(mode=mode), observe_bot_frames() as seen:
                        run = client.start(p['id'], p['version'], None, mode=mode)
                        self.assertEqual(run.result(10), EXPECTED)
                        self.assertEqual(run.get()['snapshot']['source'], SOURCE)
                    self.assert_bot_frames(seen, cleanup=True)
                with patch.object(ExecutionCursor, 'send', side_effect=AssertionError('HTTP analysis executed Bot')):
                    self.assertEqual(client.analyze(SOURCE), Framework.analyze(SOURCE))
                    self.assertEqual(client.format_source(SOURCE), Framework.format_source(SOURCE))
                self.assertEqual(client._request('GET', '/docs/native-bots'), Documentation().page('native-bots'))
            finally:
                server.shutdown();worker.join(3);server.server_close()
            path = root/'typed-bot.chaos';path.write_text(SOURCE)
            for action in ('check', 'fmt', 'ir', 'compile', 'run'):
                proc = subprocess.run([sys.executable, '-B', '-m', 'chaos', action, str(path)],
                                      cwd=folder, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                      capture_output=True, text=True, timeout=20)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                if action == 'run':
                    self.assertEqual(json.loads(proc.stdout), EXPECTED)
            self.assertEqual(path.read_text(), SOURCE)
        self.assertEqual(len([value for value in Evaluator().env.values.values() if type(value) is Native]), 335)
        self.assertEqual(len(all_specs()), 381);self.assertEqual(len(STDLIB), 21)

    def test_documented_context_and_deployment_example_execute_actual_all_twenty_one_frames(self):
        self.assertIn(DOCUMENTED.strip(), Documentation().page('native-bots')['markdown'])
        with observe_bot_frames() as seen:
            self.assertEqual(self.run_source(DOCUMENTED)[0], [False, 'achieved', [2, 4], {'next': 3}])
        self.assert_bot_frames(seen)
        with observe_bot_frames() as seen:
            self.assertEqual(self.run_source(SOURCE)[0], EXPECTED)
        self.assert_bot_frames(seen, cleanup=True)
        self.assertEqual(seen['frames'], BOT_NAMES | {cls.__name__ for cls in OLD_FRAMES})


if __name__ == '__main__':
    unittest.main()
