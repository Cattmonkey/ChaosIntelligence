"""Shared protected-control semantics across real public execution entries."""
from contextlib import contextmanager
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
from chaos.execution_frames import ExecutionCursor, TryFrame, FinallyFrame, TryCompletionFrame, GroupTryFrame
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native
from chaos.modules import STDLIB
from chaos.native_jit import BackendUnavailable
from chaos.native_specs import all_specs
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.source_format import format_source

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '''var clean=0;
let normal=try{20}catch e{0}else{42}finally{clean+=1};
let matched=try{throw 20}catch {value:n,.._} if do{await sleep(0);true}{n+22};
let grouped=try{throw error_group("root",[error(20),error(22)])}catch* _ if do{await sleep(0);true} as g{length(g.exceptions)};
[normal,matched,grouped,clean]'''
EXPECTED = [42, 42, 2, 1]


@contextmanager
def observe_protected():
    from contextlib import ExitStack
    seen = {'frames': set(), 'kinds': set(), 'owners': set()}
    with ExitStack() as stack:
        for cls in (TryFrame, FinallyFrame, TryCompletionFrame, GroupTryFrame):
            original = cls.step
            def step(frame, cursor, value, error, original=original):
                seen['frames'].add(type(frame).__name__)
                seen['kinds'].add(frame.node[0])
                seen['owners'].add(type(cursor.owner).__name__)
                return original(frame, cursor, value, error)
            stack.enter_context(patch.object(cls, 'step', step))
        yield seen


class ProtectedFrameSurfaces(unittest.TestCase):
    def test_source_graph_and_ir_observe_all_five_real_control_kinds(self):
        with observe_protected() as seen:
            for mode in ('source', 'graph'):
                with Framework(settings=Settings(':memory:')) as app:
                    p = app.create_project('protected-'+mode, source=SOURCE, capabilities=[])
                    self.assertEqual(app.start(p['id'], p['version'], None, mode=mode).result(8), EXPECTED)
            ir = parse_program(SOURCE)
            formatted = format_program(ir)
            self.assertEqual(parse_program(formatted)['semanticHash'], ir['semanticHash'])
            self.assertEqual(Framework.evaluate(formatted), EXPECTED)
        self.assertEqual(seen['kinds'], {'try', 'try_cases', 'try_group', 'try_else', 'finally'})
        self.assertEqual(seen['owners'], {'Evaluator'})
        self.assertEqual(seen['frames'], {'TryFrame', 'FinallyFrame', 'TryCompletionFrame', 'GroupTryFrame'})

    def test_code_program_macro_and_compile_targets_keep_full_results(self):
        function = 'fn(value:Int){try{return_values(value+1,"protected")}finally{null}}'
        definition = {'kind':'program', 'entry':'main.chaos', 'files':{'main.chaos':function.replace('fn(value','fn work(value')+';export{work};'}, 'languageVersions':{'main.chaos':2}}
        sources = ['let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){f(41)})',
                   'let M=module_from(program_from('+json.dumps(definition)+'));multiple_value_list(fn(){M.work(41)})',
                   'macro invoke(f,x){quote{unquote(f)(unquote(x))}};let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){invoke!(f,41)})']
        for source in sources:
            with self.subTest(source=source), observe_protected() as seen:
                self.assertEqual(Framework.evaluate(source), [42, 'protected'])
                self.assertIn('FinallyFrame', seen['frames'])
        for target in ('interpreter', 'auto', 'auto-flow', 'portable'):
            source = function.replace('fn(value','fn work(value')+';let f=compile_function(work,target="'+('auto-flow' if target=='portable' else target)+'");multiple_value_list(fn(){f(41)})'
            with self.subTest(target=target), observe_protected() as seen:
                if target == 'portable':
                    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('fixture portable')):
                        self.assertEqual(Framework.evaluate(source), [42, 'protected'])
                else:
                    self.assertEqual(Framework.evaluate(source), [42, 'protected'])
                self.assertIn('FinallyFrame', seen['frames'])

    def test_actual_agent_tool_team_and_bot_callbacks_cleanup_once(self):
        registry = Registry()
        effects, outputs = [], []
        @registry.tool('fixture.protected.audit')
        def audit(value, context):
            effects.append(value)
            return value
        @registry.provider('fixture.protected')
        def provider(request, context):
            context.checkpoint()
            if request.step == 1:
                return ToolCall('work', request.input)
            output = request.history[-1]['output']
            outputs.append(output)
            return Final(output)
        team = {'kind':'team','name':'protected crew','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.protected.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.protected.audit']}}}]}
        source = '''fn work(value:Int){try{tool("fixture.protected.audit",value);value+1}finally{tool("fixture.protected.audit","cleanup")}};
agent worker{provider:"fixture.protected",tools:["fixture.protected.audit"]};
let first=await agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.protected.audit"])])(41);
async fn role(value){try{await sleep(0);work(value)}finally{null}};
let crew=team_bind(team_from(TEAM),{work:role});let second=await crew(first);
fn plan(s){try{s.input}catch e{throw}};
fn execute(p,s){try{p+1}finally{tool("fixture.protected.audit","bot cleanup")}};
fn verify(s){{done:true,context:s.context,output:s.evidence[-1]}};
let b=bot("protected goal",plan,execute,verify);let result=await b(second);[first,second,result.output]'''.replace('TEAM',json.dumps(team))
        with observe_protected() as seen, Framework(registry=registry, settings=Settings(':memory:')) as app:
            p = app.create_project('protected-callbacks', source=source, capabilities=['fixture.protected.audit'])
            run = app.start(p['id'], p['version'], None)
            self.assertEqual(run.result(8), [42, 43, 44])
            events = [event['type'] for event in app.events(p['id'], run_id=run.id)['items'] if event['type'].startswith('team.role.')]
        self.assertEqual(outputs, [42])
        self.assertEqual(effects, [41, 'cleanup', 42, 'cleanup', 'bot cleanup'])
        self.assertEqual(events, ['team.role.started', 'team.role.completed'])
        self.assertTrue({'TryFrame', 'FinallyFrame'}.issubset(seen['frames']))

    def test_http_sdk_cli_and_inert_editor_share_source_and_metadata(self):
        with patch.object(ExecutionCursor, 'send', side_effect=AssertionError('analysis executed source')), patch.object(ExecutionCursor, '__next__', side_effect=AssertionError('analysis advanced source')):
            self.assertEqual(analyze(SOURCE)['diagnostics'], [])
            compile_program(SOURCE)
            formatted = format_source(SOURCE, line_width=54)
            self.assertEqual(format_source(formatted, line_width=54), formatted)
        with tempfile.TemporaryDirectory() as folder, Framework(settings=Settings(':memory:')) as app:
            root = Path(folder)
            server = make_server('127.0.0.1', 0, framework=app, state_dir=root/'control', update_home=root/'updates')
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_port))
                p = client.create_project('protected-http', source=SOURCE, capabilities=[])
                for mode in ('source', 'graph'):
                    run = client.start(p['id'], p['version'], None, mode=mode)
                    self.assertEqual(run.result(8), EXPECTED)
                    self.assertEqual(run.get()['snapshot']['source'], SOURCE)
                self.assertEqual(client.analyze(SOURCE), Framework.analyze(SOURCE))
            finally:
                server.shutdown();worker.join(3);server.server_close()
            path = root/'protected.chaos'
            path.write_text(SOURCE)
            for action in ('check', 'fmt', 'ir', 'compile', 'run'):
                result = subprocess.run([sys.executable, '-B', '-m', 'chaos', action, str(path)], cwd=folder, env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                if action == 'run':
                    self.assertEqual(json.loads(result.stdout), EXPECTED)
            self.assertEqual(path.read_text(), SOURCE)
        self.assertEqual(len([value for value in Evaluator().env.values.values() if type(value) is Native]), 335)
        self.assertEqual(len(all_specs()), 381)
        self.assertEqual(len(STDLIB), 21)


if __name__ == '__main__':
    unittest.main()
