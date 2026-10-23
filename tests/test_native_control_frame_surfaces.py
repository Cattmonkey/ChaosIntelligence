"""Native prompt, escape, barrier and wind frames across real public entries."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings, ToolCall, compile_program
from chaos.documentation import Documentation
from chaos.execution_control import (ControlCallbackFrame, PromptHandlerFrame,
                                     PromptCallFrame, ControlBarrierFrame,
                                     DynamicWindFrame)
from chaos.execution_frames import (ExecutionCursor, ContextFrame, PromptScopeFrame,
                                    CancelShieldFrame, TaskGroupFrame, GroupBodyFrame)
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native
from chaos.modules import ProgramBundle, STDLIB
from chaos.native_jit import BackendUnavailable
from chaos.native_specs import all_specs
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.source_format import format_source

ROOT = Path(__file__).resolve().parents[1]
CONTROL_FRAMES = (ControlCallbackFrame, PromptHandlerFrame, PromptCallFrame,
                  ControlBarrierFrame, DynamicWindFrame)
CONTROL_NAMES = {cls.__name__ for cls in CONTROL_FRAMES}
SOURCE = '''import "std/control" as C;
var events=[];
let tag=C.prompt_tag("native-frame");
let prompted=multiple_value_list(fn(){
  C.call_prompt(tag,async fn(){
    C.dynamic_wind(
      fn(){events=append(events,"before")},
      async fn(){await sleep(0);events=append(events,"body");C.abort_prompt(tag,40,2)},
      async fn(){await sleep(0);events=append(events,"after")}
    )
  },fn(a,b){events=append(events,["handler",C.prompt_available(tag)]);return_values(a+b,"prompt")})
});
let escaped=multiple_value_list(fn(){
  C.call_escape(fn(exit:EscapeContinuation){
    C.control_barrier(fn(){
      events=append(events,["barrier",exit.available,try{exit(0)}catch e{e.code}]);null
    });
    exit(42,"escape")
  })
});
let lexical=multiple_value_list(fn(){
  with C.prompt_scope(tag,fn(n){
    events=append(events,["lexical",C.prompt_available(tag)]);return_values(n+1,"scope")
  }) as scope{C.abort_prompt(tag,41)}
});
[prompted,escaped,lexical,events,C.prompt_available(tag)]'''
EXPECTED = [[42, 'prompt'], [42, 'escape'], [42, 'scope'],
            ['before', 'body', 'after', ['handler', False],
             ['barrier', False, 'control_barrier_error'], ['lexical', False]], False]
DOCUMENTED = '''var events=[];
let tag=prompt_tag("frames");
let result=multiple_value_list(fn(){
  call_prompt(tag,fn(){
    dynamic_wind(
      fn(){events=append(events,"before")},
      fn(){
        events=append(events,"body");
        call_escape(fn(exit){exit(41,2)});
        abort_prompt(tag,40,2)
      },
      fn(){events=append(events,"after")}
    )
  },fn(a,b){
    events=append(events,prompt_available(tag));
    return_values(a+b,"handled")
  })
});
[result,events]'''
DEPLOYMENT_SOURCE = '''import "std/context" as C;
var events=[];
@C.asynccontextmanager
async fn* resource(){try{await sleep(0);yield {value:41}}finally{await sleep(0);events=append(events,"closed")}}
async fn work(){with cancel_shield() as shield{async with task_group() as group{
 async with resource() as {value:value}{let tag=prompt_tag("package");with prompt_scope(tag) as scope{
  control_barrier(async fn(){
   let answer=call_escape(async fn(exit){dynamic_wind(fn(){null},async fn(){await sleep(0);exit(value+1)},fn(){null})});
   call_prompt(tag,fn(){abort_prompt(tag,answer)},fn(result){result})
  })
 }}
}}};
[await work(),events]
'''


@contextmanager
def observe_control_frames():
    """Record real frame transitions on every logical cursor without replay."""
    seen = {'frames': set(), 'operations': set(), 'owners': set(),
            'cursors': set(), 'handlers': set(), 'steps': {}}
    with ExitStack() as stack:
        for cls in CONTROL_FRAMES + (ContextFrame, PromptScopeFrame, CancelShieldFrame,
                                    TaskGroupFrame, GroupBodyFrame):
            original = cls.step
            def step(frame, cursor, value, error, original=original):
                name = type(frame).__name__
                seen['frames'].add(name)
                seen['owners'].add(type(cursor.owner).__name__)
                seen['cursors'].add(id(cursor))
                seen['steps'][name] = seen['steps'].get(name, 0) + 1
                if type(frame) is PromptCallFrame:
                    seen['operations'].add(frame.operation)
                elif type(frame) is ControlBarrierFrame:
                    seen['operations'].add('barrier')
                elif type(frame) is DynamicWindFrame:
                    seen['operations'].add('wind')
                elif type(frame) is PromptHandlerFrame:
                    seen['handlers'].add((frame.scope.state, frame.scope.handler is None))
                return original(frame, cursor, value, error)
            stack.enter_context(patch.object(cls, 'step', step))
        yield seen


class NativeControlFrameSurfaces(unittest.TestCase):
    def assert_native_frames(self, seen):
        self.assertTrue(CONTROL_NAMES <= seen['frames'], seen)
        self.assertEqual(seen['operations'], {'prompt', 'escape', 'barrier', 'wind'})
        self.assertEqual(seen['owners'], {'Evaluator'})
        self.assertEqual(seen['handlers'], {('closed', True)})
        self.assertTrue(all(count > 0 for count in seen['steps'].values()))

    def test_source_graph_and_ir_roundtrip_execute_all_native_frames(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode), observe_control_frames() as seen:
                with Framework(settings=Settings(':memory:')) as app:
                    project = app.create_project('native-control-'+mode, source=SOURCE, capabilities=[])
                    self.assertEqual(app.start(project['id'], project['version'], None, mode=mode).result(8), EXPECTED)
            self.assert_native_frames(seen)
            self.assertIn('PromptScopeFrame', seen['frames'])
        for version in (1, 2):
            with self.subTest(version=version), observe_control_frames() as seen:
                ir = parse_program(SOURCE, language_version=version)
                formatted = format_program(ir)
                self.assertEqual(parse_program(formatted, language_version=version)['semanticHash'], ir['semanticHash'])
                bundle = ProgramBundle(formatted, language_versions={'main.chaos': version})
                self.assertEqual(Evaluator().run(bundle), EXPECTED)
            self.assert_native_frames(seen)

    def test_code_program_macro_and_compile_targets_preserve_full_results(self):
        function = '''fn(value:Int){let tag=prompt_tag();call_prompt(tag,fn(){
          dynamic_wind(fn(){null},fn(){
            control_barrier(fn(){call_escape(fn(exit){exit(value)})});
            abort_prompt(tag,value+1,"generated")
          },fn(){null})
        },fn(n,label){return_values(n,label)})}'''
        definition = {'kind': 'program', 'entry': 'main.chaos',
                      'files': {'main.chaos': function.replace('fn(value', 'fn work(value')+';export{work};'},
                      'languageVersions': {'main.chaos': 2}}
        sources = ['let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){f(41)})',
                   'let M=module_from(program_from('+json.dumps(definition)+'));multiple_value_list(fn(){M.work(41)})',
                   'macro invoke(f,x){quote{unquote(f)(unquote(x))}};let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){invoke!(f,41)})']
        for source in sources:
            with self.subTest(source=source), observe_control_frames() as seen:
                self.assertEqual(Framework.evaluate(source), [42, 'generated'])
            self.assert_native_frames(seen)
        for target in ('interpreter', 'auto', 'auto-flow', 'auto-control', 'portable'):
            selected = 'auto-flow' if target == 'portable' else target
            source = function.replace('fn(value', 'fn work(value')+';let f=compile_function(work,target="'+selected+'");multiple_value_list(fn(){f(41)})'
            with self.subTest(target=target), observe_control_frames() as seen:
                if target == 'portable':
                    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('fixture portable')):
                        self.assertEqual(Framework.evaluate(source), [42, 'generated'])
                else:
                    self.assertEqual(Framework.evaluate(source), [42, 'generated'])
            self.assert_native_frames(seen)

    def test_actual_agent_tool_team_and_bot_callbacks_have_once_only_effects(self):
        registry = Registry()
        effects, outputs, provider_steps = [], [], []
        @registry.tool('fixture.control.audit')
        def audit(value, context):
            effects.append(value)
            return value
        @registry.provider('fixture.control')
        def provider(request, context):
            context.checkpoint()
            provider_steps.append(request.step)
            if request.step == 1:
                return ToolCall('work', request.input)
            output = request.history[-1]['output']
            outputs.append(output)
            return Final(output)
        team = {'kind': 'team', 'name': 'control crew', 'entry': 'work', 'roles': [
            {'id': 'work', 'parent': None, 'capabilities': ['fixture.control.audit'],
             'agent': {'kind': 'agent', 'name': 'fallback', 'config': {'provider': 'local', 'tools': ['fixture.control.audit']}}}]}
        source = '''fn work(value:Int){let tag=prompt_tag();call_prompt(tag,fn(){
  dynamic_wind(fn(){tool("fixture.control.audit",["enter",value])},fn(){
    control_barrier(fn(){call_escape(fn(exit){exit(value)})});abort_prompt(tag,value+1)
  },fn(){tool("fixture.control.audit",["exit",value])})
},fn(n){tool("fixture.control.audit",["handle",n]);n})};
agent worker{provider:"fixture.control",tools:["fixture.control.audit"]};
let first=await agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.control.audit"])])(41);
var calls=0;
async fn role(value){calls+=1;async with task_group() as group{
  let pending=group.spawn(async fn(){await sleep(0);null}());await pending;
  call_escape(async fn(exit){await sleep(0);exit(work(value),"team")})
}};
let crew=team_bind(team_from(TEAM),{work:role});let cold=crew(first);let before=calls;
let second=multiple_value_list(async fn(){await cold});let again=multiple_value_list(cold);
fn plan(s){let tag=prompt_tag();with prompt_scope(tag,fn(n){n}) as scope{abort_prompt(tag,s.input)}};
fn execute(p,s){dynamic_wind(fn(){tool("fixture.control.audit",["bot enter",p])},
  fn(){call_escape(fn(exit){exit(p+1)})},fn(){tool("fixture.control.audit",["bot exit",p])})};
fn verify(s){control_barrier(fn(){{done:true,context:s.context,output:s.evidence[-1]}})};
let b=bot("control goal",plan,execute,verify);let result=await b(second[0]);
[first,before,second,again,calls,result.output]'''.replace('TEAM', json.dumps(team))
        with observe_control_frames() as seen, Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('native-control-callbacks', source=source, capabilities=['fixture.control.audit'])
            run = app.start(project['id'], project['version'], None)
            self.assertEqual(run.result(8), [42, 0, [43, 'team'], [43, 'team'], 1, 44])
            events = app.events(project['id'], run_id=run.id)['items']
        self.assert_native_frames(seen)
        self.assertTrue({'PromptScopeFrame', 'TaskGroupFrame', 'GroupBodyFrame'} <= seen['frames'])
        self.assertEqual(provider_steps, [1, 2])
        self.assertEqual(outputs, [42])
        self.assertEqual(effects, [['enter', 41], ['exit', 41], ['handle', 42],
                                   ['enter', 42], ['exit', 42], ['handle', 43],
                                   ['bot enter', 43], ['bot exit', 43]])
        role_events = [event for event in events if event['type'].startswith('team.role.')]
        self.assertEqual([event['type'] for event in role_events], ['team.role.started', 'team.role.completed'])
        self.assertEqual(len({event['payload']['invocationId'] for event in role_events}), 1)
        self.assertEqual(sum(event['type'] == 'agent.language_tool' for event in events), 1)
        audit_events = [event['type'] for event in events if event['payload'].get('tool') == 'fixture.control.audit']
        self.assertEqual(audit_events, ['tool.started', 'tool.completed'] * 8)

    def test_cold_results_and_concurrent_contexts_keep_original_logical_cursors(self):
        source = '''var effects=[];let variable=context_var("control-surface",0);let tag=prompt_tag();
async fn task(){effects=append(effects,"task");return_values(42,"cold")}
fn* stream(){effects=append(effects,"stream");yield ([variable.get(),prompt_available(tag)]);return_values(43,"stream")}
let cold=call_escape(fn(exit){task()});let generator=call_prompt(tag,stream);let before=effects;
let result=multiple_value_list(async fn(){await cold});let first=next(generator);let last=next(generator);
async fn worker(n){with context_bind([{variable:variable,value:n}]) as bound{
  let own=prompt_tag();call_prompt(own,async fn(){await sleep(0);call_escape(fn(exit){exit([variable.get(),prompt_available(own)])})})
}};
let workers=await gather([worker(2),worker(3)]);
[before,result,multiple_value_list(cold),first.value,last.done,multiple_value_list(generator),effects,workers,variable.get()]'''
        with observe_control_frames() as seen:
            self.assertEqual(Framework.evaluate(source), [[], [42, 'cold'], [42, 'cold'],
                             [0, False], True, [43, 'stream'], ['task', 'stream'],
                             [[2, True], [3, True]], 0])
        self.assertTrue({'PromptCallFrame', 'ControlCallbackFrame', 'PromptHandlerFrame'} <= seen['frames'])
        self.assertGreater(len(seen['cursors']), 1)
        self.assertEqual(seen['owners'], {'Evaluator'})

    def test_http_sdk_cli_and_inert_tools_share_source_and_metadata(self):
        with patch.object(ExecutionCursor, 'send', side_effect=AssertionError('analysis executed source')), \
             patch.object(ExecutionCursor, '__next__', side_effect=AssertionError('analysis advanced source')):
            self.assertEqual(analyze(SOURCE)['diagnostics'], [])
            self.assertIn('call', compile_program(SOURCE)['summary']['effects'])
            formatted = format_source(SOURCE, line_width=54)
            self.assertEqual(format_source(formatted, line_width=54), formatted)
        with tempfile.TemporaryDirectory() as folder, Framework(settings=Settings(':memory:')) as app:
            root = Path(folder)
            server = make_server('127.0.0.1', 0, framework=app, state_dir=root/'control', update_home=root/'updates')
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_port))
                project = client.create_project('native-control-http', source=SOURCE, capabilities=[])
                for mode in ('source', 'graph'):
                    with self.subTest(mode=mode), observe_control_frames() as seen:
                        run = client.start(project['id'], project['version'], None, mode=mode)
                        self.assertEqual(run.result(8), EXPECTED)
                        self.assertEqual(run.get()['snapshot']['source'], SOURCE)
                    self.assert_native_frames(seen)
                with patch.object(ExecutionCursor, 'send', side_effect=AssertionError('HTTP analysis executed source')):
                    self.assertEqual(client.analyze(SOURCE), Framework.analyze(SOURCE))
                    self.assertEqual(client.format_source(SOURCE), Framework.format_source(SOURCE))
                self.assertEqual(client._request('GET', '/docs/control-prompts'), Documentation().page('control-prompts'))
            finally:
                server.shutdown();worker.join(3);server.server_close()
            path = root/'native-control.chaos'
            path.write_text(SOURCE)
            for action in ('check', 'fmt', 'ir', 'compile', 'run'):
                result = subprocess.run([sys.executable, '-B', '-m', 'chaos', action, str(path)],
                                        cwd=folder, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                if action == 'run':
                    self.assertEqual(json.loads(result.stdout), EXPECTED)
            self.assertEqual(path.read_text(), SOURCE)
        self.assertEqual(len([value for value in Evaluator().env.values.values() if type(value) is Native]), 335)
        self.assertEqual(len(all_specs()), 381)
        self.assertEqual(len(STDLIB), 21)

    def test_documented_native_control_example(self):
        self.assertIn(DOCUMENTED, Documentation().page('control-prompts')['markdown'])
        with observe_control_frames() as seen:
            self.assertEqual(Framework.evaluate(DOCUMENTED), [[42, 'handled'], ['before', 'body', 'after', False]])
        self.assertTrue({'PromptCallFrame', 'ControlCallbackFrame', 'PromptHandlerFrame', 'DynamicWindFrame'} <= seen['frames'])
        self.assertEqual(seen['operations'], {'prompt', 'escape', 'wind'})
        self.assertEqual(seen['handlers'], {('closed', True)})

    def test_deployment_mixed_control_resource_example(self):
        with observe_control_frames() as seen:
            self.assertEqual(Framework.evaluate(DEPLOYMENT_SOURCE), [42, ['closed']])
        self.assert_native_frames(seen)
        self.assertEqual(seen['frames'], CONTROL_NAMES | {
            'ContextFrame', 'PromptScopeFrame', 'CancelShieldFrame',
            'TaskGroupFrame', 'GroupBodyFrame'})


if __name__ == '__main__':
    unittest.main()
