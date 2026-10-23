"""Async compiled functions retain language, Agent/Team/Bot and Robot contracts.

Deterministic local providers and software adapters only: no model or hardware.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from chaos import Framework, Registry, Settings, ToolCall, Final, compile_program
from chaos.language import Evaluator
from chaos.ide_language import analyze
from chaos.program import parse_program, format_program
from chaos.source_format import format_source
from chaos.compiled_flow_state import FlowState
import test_module_frame_surfaces as module_surfaces
import test_robot_values as robot_fixture

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '''async fn sum(n){var i=0;var total=0;while i<n{
    await sleep(0);total=total+i;i=i+1};return total};
let compiled=compile_function(sum,target="auto-flow");
let task=compiled(4);let cold=compile_flow_info(compiled).resumeCalls;
let result=await task;[result,cold,compile_flow_info(compiled).resumeCalls>0]'''
EXPECTED = [6, 0, True]


@contextmanager
def states():
    seen = []
    original = FlowState.__init__
    def enter(self, *args, **kwargs):
        original(self, *args, **kwargs)
        seen.append(self)
    with patch.object(FlowState, '__init__', enter):
        yield seen


class AsyncFlowSurfaces203(unittest.TestCase):
    run_source = module_surfaces.ModuleFrameSurfaces.run_source

    def assert_real_flow(self, seen):
        self.assertTrue(seen, 'Compiled async body must actually enter the shared CFG')
        self.assertTrue(all(item.closed for item in seen))
        self.assertTrue(all(item.program.resume_calls > 0 for item in seen))

    def test_source_graph_code_and_formatted_source_run_actual_async_flow(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode), states() as seen:
                result, _ = self.run_source(SOURCE, mode=mode, capabilities=[])
            self.assertEqual(result, EXPECTED)
            self.assert_real_flow(seen)
        for source in (format_source(SOURCE), format_program(parse_program(SOURCE))):
            with states() as seen:
                self.assertEqual(Framework.evaluate(source), EXPECTED)
            self.assert_real_flow(seen)
        code = 'let f=compile_function(function_from(code_from("async fn(x){await sleep(0);x+2}")),target="auto-flow");[await f(40),compile_flow_info(f).resumeCalls>0]'
        with states() as seen:
            self.assertEqual(Framework.evaluate(code), [42, True])
        self.assert_real_flow(seen)

    def test_authoring_is_inert_and_preserves_semantic_hash(self):
        with patch.object(Evaluator, 'run', side_effect=AssertionError('authoring executed source')), patch.object(FlowState, '__init__', side_effect=AssertionError('authoring started a compiled invocation')):
            report = analyze(SOURCE)
            pretty = format_source(SOURCE)
            plan = compile_program(SOURCE)
        self.assertFalse([row for row in report['diagnostics'] if row['severity'] == 'error'])
        self.assertTrue(plan)
        self.assertEqual(format_source(pretty), pretty)
        self.assertEqual(parse_program(pretty)['semanticHash'], parse_program(SOURCE)['semanticHash'])

    def test_agent_tool_team_bot_share_compiled_function_and_effect_authority(self):
        registry = Registry()
        calls, effects = [], []
        @registry.tool('fixture.asyncflow.audit')
        def audit(value, context):
            context.checkpoint(); effects.append(value); return None
        @registry.provider('fixture.asyncflow.provider')
        def provider(request, context):
            context.checkpoint(); calls.append(request.step)
            return ToolCall('work', request.input) if request.step == 1 else Final(request.history[-1]['output'])
        caps = ['fixture.asyncflow.audit']
        team = {'kind': 'team', 'name': 'compiled async team', 'entry': 'work', 'roles': [{'id': 'work', 'parent': None, 'capabilities': caps, 'agent': {'kind': 'agent', 'name': 'fallback', 'config': {'provider': 'local', 'tools': caps}}}]}
        source = '''async fn raw(value){tool("fixture.asyncflow.audit",value);await sleep(0);value+2};
let work=compile_function(raw,target="auto-flow");
agent helper{provider:"fixture.asyncflow.provider",tools:["fixture.asyncflow.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.asyncflow.audit"])]);
let crew=team_bind(team_from(TEAM),{work:work});
async fn execute(plan,state){[await bound(40),await crew(40)]};
let compiledExecute=compile_function(execute,target="auto-flow");
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};
let worker=bot("compiled function bot",fn(state){state.input},compiledExecute,verify);
[(await worker(null)).output,compile_flow_info(work).resumeCalls>0,compile_flow_info(compiledExecute).resumeCalls>0]'''.replace('TEAM', json.dumps(team))
        with states() as seen:
            result, events = self.run_source(source, registry=registry, capabilities=caps)
        self.assertEqual(result, [[42, 42], True, True])
        self.assertEqual(calls, [1, 2]); self.assertEqual(effects, [40, 40])
        self.assert_real_flow(seen)
        self.assertEqual(sum(row['type'] == 'agent.language_tool' for row in events), 1)
        self.assertEqual(sum(row['type'] == 'team.role.started' for row in events), 1)

    def test_robot_keeps_single_admission_and_software_receipt(self):
        source = '''async fn control(){async with await robot_open("rig") as r{
let observation=await robot_observe(r);let command=robot_command(observation,{pan:0,tilt:0});
let receipt=await r(command);await sleep(0);
let retried=try{await r(command)}catch e{e.code};
[receipt.status,receipt.targetVerified,command.used,retried]}};
let compiled=compile_function(control,target="auto-flow");
[await compiled(),compile_flow_info(compiled).resumeCalls>0]'''
        log = []
        with robot_fixture.host(log) as app, states() as seen:
            result = robot_fixture.run(app, source)
        self.assertEqual(result, [['sent', False, True, 'robot_command_used'], True])
        self.assertEqual([row[0] for row in log], ['open', 'observe', 'act', 'close'])
        self.assert_real_flow(seen)
        log = []
        with robot_fixture.host(log) as app:
            denied = source.replace('[await compiled(),compile_flow_info(compiled).resumeCalls>0]', 'try{await compiled()}catch e{e.code}')
            self.assertEqual(robot_fixture.run(app, denied, caps=[]), 'capability_error')
        self.assertEqual(log, [])

    def test_external_isolated_cli_sdk_no_model_configuration(self):
        with tempfile.TemporaryDirectory(prefix='chaos-async-flow-') as folder:
            work = Path(folder); source = work/'main.chaos'; source.write_text(SOURCE)
            env = {name: os.environ[name] for name in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT', 'WINDIR') if name in os.environ}
            env.update(HOME=str(work), USERPROFILE=str(work), CHAOS_CONFIG=str(work/'settings.json'), PYTHONDONTWRITEBYTECODE='1')
            bootstrap = 'import sys;sys.path.insert(0,sys.argv.pop(1));'
            scripts = [bootstrap+'from chaos.cli import main;raise SystemExit(main())', bootstrap+'import json;from chaos import Framework;from pathlib import Path;print(json.dumps(Framework.evaluate(Path(sys.argv[1]).read_text())))']
            arguments = [['run', str(source), '--db', ':memory:'], [str(source)]]
            for script, args in zip(scripts, arguments):
                result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(ROOT)] + args, cwd=work, env=env, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), EXPECTED)

