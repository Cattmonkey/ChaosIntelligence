"""Structured parameter boundaries share real agent, Program and API execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec
from chaos.program import parse_program, format_program


ROOT = Path(__file__).resolve().parent.parent
SOURCE = '''fn sum(pair as [left, right]: List) { left + right }
fn report(request as {rows: rows, ..metadata}: Record) {
    {values: map(sum, rows), metadata: metadata, whole: request.rows}
}
report(input)'''
INPUT = {'rows': [[20, 22], [1, 2]], 'role': 'worker'}
EXPECTED = {'values': [42, 3], 'metadata': {'role': 'worker'}, 'whole': [[20, 22], [1, 2]]}


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from nodes(child)
    elif isinstance(value, list):
        for child in value: yield from nodes(child)


class ParameterPatternIntegration(unittest.TestCase):
    def execute(self, registry, source, value=None, capabilities=()):
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('parameters', source=source, capabilities=list(capabilities))
            return app.start(project['id'], 1, value, mode='source').result(timeout=8)

    def test_headless_sdk_ir_and_source_format_share_the_call_boundary(self):
        self.assertEqual(Framework.evaluate(SOURCE, {'input': INPUT}), EXPECTED)
        ir = parse_program(SOURCE)
        parameters = [p for n in nodes(ir) for p in n.get('parameters', [])]
        self.assertEqual(sum('pattern' in p for p in parameters), 2)
        self.assertEqual(Framework.evaluate(format_program(ir), {'input': INPUT}), EXPECTED)
        formatted = Framework.format_source('// kept\n'+SOURCE, line_width=55, indent_size=4)
        self.assertIn('// kept', formatted)
        self.assertEqual(Framework.format_source(formatted, line_width=55, indent_size=4), formatted)
        self.assertEqual(Framework.evaluate(formatted, {'input': INPUT}), EXPECTED)

    def test_cli_check_fmt_ir_run_and_repl(self):
        source = 'fn sum(pair as [left,right]:List){left+right};sum(pair=[20,22])'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.chaos'; path.write_text(source)
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            for command in ('check', 'fmt', 'ir', 'run'):
                with self.subTest(command=command):
                    result = subprocess.run([sys.executable, '-m', 'chaos', command, str(path)],
                                            cwd=directory, env=env, capture_output=True, text=True, timeout=12)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if command == 'run': self.assertEqual(json.loads(result.stdout), 42)
                    if command == 'ir':
                        self.assertTrue(any('pattern' in p for n in nodes(json.loads(result.stdout))
                                            for p in n.get('parameters', [])))
                    if command == 'fmt': self.assertEqual(result.stdout, Framework.format_source(source))
            result = subprocess.run([sys.executable, '-m', 'chaos', 'repl'], cwd=directory, env=env,
                                    input=source+'\n.quit\n', capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), 42)

    def test_real_agent_result_calls_a_structured_function_once(self):
        seen, registry = [], Registry()
        registry.register_provider(ProviderSpec('fixture.parameters', lambda r,c:
            seen.append(('agent', r.input)) or Final({'task': {'value': 41}, 'trace': 'fixture'})))
        registry.register_tool(ToolSpec('fixture.consume', lambda v,c: seen.append(('tool', v)) or v))
        source = '''agent author {provider:"fixture.parameters",tools:[],maxSteps:1};
fn consume(reply as {task:{value:n},trace:trace}:Record) {
    tool("fixture.consume",{answer:n+1,trace:trace})
}
map(consume,[author("request")])'''
        expected = {'answer': 42, 'trace': 'fixture'}
        self.assertEqual(self.execute(registry, source, capabilities=['fixture.consume']), [expected])
        self.assertEqual(seen, [('agent', 'request'), ('tool', expected)])

    def test_shape_failure_does_not_run_agent_body_or_dependent_tool(self):
        seen, registry = [], Registry()
        registry.register_provider(ProviderSpec('fixture.parameters', lambda r,c:
            seen.append('provider') or Final({'pair': [1]})))
        registry.register_tool(ToolSpec('fixture.consume', lambda v,c: seen.append('tool') or v))
        source = '''agent author {provider:"fixture.parameters",tools:[],maxSteps:1};
fn use(reply as {pair:[a,b]}:Record){tool("fixture.consume",a+b)}
async fn agent_role(request as {task:task}:Record){author(task)}
let first=try{agent_role({wrong:1});"bad"}catch e{e.code};
let second=try{use(author(null));"bad"}catch e{e.code};
[first,second]'''
        self.assertEqual(self.execute(registry, source, capabilities=['fixture.consume']), ['match_error']*2)
        self.assertEqual(seen, ['provider'])

    def test_structured_wrapper_never_expands_agent_capability(self):
        seen, registry = [], Registry()
        registry.register_tool(ToolSpec('fixture.denied', lambda v,c: seen.append(v)))
        registry.register_provider(ProviderSpec('fixture.parameters', lambda r,c: ToolCall('fixture.denied', r.input)))
        source = '''agent role {provider:"fixture.parameters",tools:[],maxSteps:2};
fn invoke(request as {value:value}:Record){role(value)}
try{invoke({value:42})}catch e{"denied"}'''
        self.assertEqual(self.execute(registry, source, capabilities=['fixture.denied']), 'denied')
        self.assertEqual(seen, [])

    def test_self_programming_adopts_pattern_source_and_old_run_stays_fixed(self):
        candidate_source = 'fn sum(pair as [left,right]:List){left+right};sum(input)'
        definition = {'kind':'program','entry':'main.chaos','files':{'main.chaos':candidate_source},
                      'languageVersions':{'main.chaos':2}}
        registry, seen = Registry(), []
        registry.register_provider(ProviderSpec('fixture.designer', lambda r,c:
            seen.append(r.input) or Final({'program': definition, 'reason': 'structured input'})))
        source = '''let base=current_program();
agent designer {provider:"fixture.designer",tools:[],maxSteps:1};
async fn install(reply as {program:definition,reason:reason}:Record){
    let candidate=program_from(definition);
    let probe=await program_probe(candidate,[{input:[20,22],expect:42}]);
    let receipt=await program_adopt(base,candidate,[{input:[20,22],expect:42}],reason);
    [probe.passed,receipt.version,await candidate([19,23])]
}
let result=await install(designer("structured sum"));
[result,program_metadata(current_program()).origin.version]'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project=app.create_project('self-parameters', source=source, capabilities=['program.adopt'])
            run=app.start(project['id'], 1, None, mode='source')
            self.assertEqual(run.result(timeout=8), [[True, 2, 42], 1])
            self.assertEqual(seen, ['structured sum'])
            self.assertEqual(app.get_run(run.id)['snapshot']['source'], source)
            self.assertEqual(app.get_project(project['id'])['source'], candidate_source)
            self.assertEqual(app.start(project['id'], 2, [18,24], mode='source').result(timeout=8), 42)

    def test_http_structure_edit_and_run_snapshot_remain_consistent(self):
        from chaos.server import make_server
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1', 0, framework=app, state_dir=Path(directory)/'control', update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                source='fn get(row as {value:x}:Record){x+1};get(input)'
                project=client.create_project('parameter-http', source=source, capabilities=[])
                first=client.start(project['id'], 1, {'value':41}); self.assertEqual(first.result(8),42)
                ir=client.program(project['id'])['program']
                literal=next(n['id'] for n in nodes(ir) if n.get('kind')=='literal' and n.get('value')==1)
                changed=client.edit_program(project['id'],1,operations=[{'op':'set','id':literal,'field':'value','value':2}])
                self.assertEqual(client.start(project['id'],changed['version'],{'value':41}).result(8),43)
                self.assertEqual(first.get()['snapshot']['source'],source)
                self.assertEqual(first.get()['output'],42)
                self.assertIn('as',changed['source'])
                self.assertEqual(client.get_project(project['id'])['source'],changed['source'])
            finally:
                server.shutdown(); server.server_close(); thread.join(3)

    def test_debugger_shows_whole_slot_and_leaf_bindings_in_the_actual_frame(self):
        for declaration, body, call in (
                ('fn', 'left + right', 'sum([20,22])'),
                ('async fn', 'left + right', 'await sum([20,22])'),
                ('fn*', 'yield left + right', 'next(sum([20,22])).value')):
            with self.subTest(declaration=declaration), Framework(settings=Settings(':memory:')) as app:
                source=declaration+' sum(pair as [left,right]:List){\n  '+body+'\n}\n'+call
                project=app.create_project('parameter-debug',source=source,capabilities=[])
                run=app.start_debug(project['id'],1,pause_on_entry=False,
                                    breakpoints=[{'file':'main.chaos','line':2}])
                try:
                    deadline=time.monotonic()+4
                    while time.monotonic()<deadline:
                        state=run.debugger()
                        if state['status']=='paused': break
                        time.sleep(.005)
                    self.assertEqual(state['status'],'paused')
                    self.assertEqual(state['location']['line'],2)
                    frame=next(f for f in state['frames'] if f['name']=='sum')
                    values={row['name']:row['value'] for row in frame['locals']}
                    self.assertEqual([values['left'],values['right']],[20,22])
                    self.assertEqual(values['pair'],[20,22])
                    run.debug_command('breakpoints',breakpoints=[])
                    run.debug_command('continue',pause_id=state['pauseId'])
                    self.assertEqual(run.result(4),42)
                finally: run.cancel()


if __name__ == '__main__': unittest.main()
