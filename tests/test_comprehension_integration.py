"""Native comprehension nodes retain public entry points and real effect execution."""
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
from chaos.program import format_program, parse_program


ROOT = Path(__file__).resolve().parent.parent
SOURCE = '''async fn double(value){value*2}
async fn* incoming(){for value in input{yield value}}
let eager=[value*2 for value in input if value>20];
let record={pair.key:pair.value for pair in [{key:"a",value:1},{key:"b",value:2},{key:"a",value:3}]};
let lazy=(value+1 for value in input);
let stream=async (await double(value) async for value in incoming() if value>20);
[eager,record,collect(lazy),await acollect(stream)]'''
EXPECTED = [[42, 44], {'a': 3, 'b': 2}, [21, 22, 23], [42, 44]]


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from walk(child)
    elif isinstance(value, list):
        for child in value: yield from walk(child)


class ComprehensionIntegrationTests(unittest.TestCase):
    def run_source(self, registry, source, value=None, capabilities=()):
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('comprehension', source=source, capabilities=list(capabilities))
            return app.start(project['id'], project['version'], value, mode='source').result(timeout=8)

    def test_sdk_formatter_and_version_two_ir_share_all_four_kinds(self):
        self.assertEqual(Framework.evaluate(SOURCE, {'input': [20, 21, 22]}), EXPECTED)
        program = parse_program(SOURCE)
        self.assertEqual(program['languageVersion'], 2)
        kinds = {node['kind'] for node in walk(program) if 'kind' in node}
        self.assertTrue({'list_comp', 'record_comp', 'gen_comp', 'async_gen_comp',
                         'comp_for', 'comp_async_for', 'comp_if'}.issubset(kinds))
        self.assertEqual(Framework.evaluate(format_program(program), {'input': [20, 21, 22]}), EXPECTED)
        formatted = Framework.format_source(SOURCE, line_width=50, indent_size=4)
        self.assertEqual(Framework.format_source(formatted, line_width=50, indent_size=4), formatted)
        self.assertEqual(Framework.evaluate(formatted, {'input': [20, 21, 22]}), EXPECTED)

    def test_cli_check_format_ir_run_and_repl_use_native_comprehensions(self):
        source = '[x*2 for x in [20,21] if x>20]'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.chaos'; path.write_text(source)
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            for command in ('check', 'fmt', 'ir', 'run'):
                with self.subTest(command=command):
                    result = subprocess.run([sys.executable, '-m', 'chaos', command, str(path)],
                        cwd=directory, env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if command == 'run': self.assertEqual(json.loads(result.stdout), [42])
                    if command == 'fmt': self.assertEqual(result.stdout, Framework.format_source(source))
                    if command == 'ir': self.assertIn('list_comp', {n.get('kind') for n in walk(json.loads(result.stdout))})
            result = subprocess.run([sys.executable, '-m', 'chaos', 'repl'], cwd=directory,
                env=env, input=source+'\n.quit\n', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), [42])

    def test_http_program_edit_preserves_original_run_and_source_revision(self):
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server = make_server_for_test(app, directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_port))
                source = '[x*2 for x in input if x>20]'
                project = client.create_project('comprehension-http', source=source, capabilities=[])
                first = client.start(project['id'], project['version'], [20, 21]); self.assertEqual(first.result(5), [42])
                original = client.program(project['id'])['program']
                target = next(n['id'] for n in walk(original) if n.get('kind') == 'literal' and n.get('value') == 2)
                revised = client.edit_program(project['id'], 1, operations=[{'op': 'set', 'id': target, 'field': 'value', 'value': 3}])
                second = client.start(project['id'], revised['version'], [20, 21])
                self.assertEqual(second.result(5), [63]); self.assertEqual(first.get()['output'], [42])
                self.assertEqual(first.get()['snapshot']['source'], source)
                self.assertEqual(client.get_project(project['id'])['source'], revised['source'])
                updated = client.program(project['id'])['program']
                self.assertEqual(updated['languageVersion'], original['languageVersion'])
                self.assertNotEqual(updated['semanticHash'], original['semanticHash'])
                self.assertTrue(any(e['type']=='graph.saved' and e['payload']['version']==2
                                    for e in client.events(project['id'])['items']))
            finally:
                server.shutdown(); server.server_close(); thread.join(3)

    def test_real_registry_effects_observe_factory_step_guard_projection_order(self):
        registry, seen = Registry(), []
        def provider(request, context):
            context.checkpoint(); seen.append(request.input)
            phase, value = request.input['phase'], request.input['value']
            return Final(value > 1 if phase == 'guard' else value * 10 if phase == 'head' else value)
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
class Source{var n=0;fn __iter__(self){worker({phase:"factory",value:0});self}
fn __next__(self){self.n=self.n+1;worker({phase:"step",value:self.n});{done:self.n>2,value:self.n}}}
[worker({phase:"head",value:x}) for x in Source() if worker({phase:"guard",value:x})]'''
        self.assertEqual(self.run_source(registry, source), [20])
        self.assertEqual([(row['phase'], row['value']) for row in seen],
                         [('factory', 0), ('step', 1), ('guard', 1), ('step', 2), ('guard', 2), ('head', 2), ('step', 3)])

    def test_async_calls_stay_cold_until_gather_or_task_group(self):
        registry, seen = Registry(), []
        def provider(request, context):
            context.checkpoint(); seen.append(request.input); return Final(request.input * 2)
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        registry.register_tool(ToolSpec('fixture.count', lambda value, context: len(seen)))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
async fn work(value){worker(value)}let jobs=[work(value) for value in [20,21]];
let cold=[tool("fixture.count",null),[task_info(job).state for job in jobs]];
let first=await gather(jobs);let more=[work(value) for value in [22,23]];
async with task_group() as group{for job in more{group.spawn(job)}};
[cold,first,await gather(more)]'''
        self.assertEqual(self.run_source(registry, source, capabilities=['fixture.count']),
                         [[0, ['created', 'created']], [40, 42], [44, 46]])
        self.assertCountEqual(seen, [20, 21, 22, 23])

    def test_parallel_async_comprehensions_cooperate_with_true_effect_workers(self):
        registry, barrier, seen, lock = Registry(), threading.Barrier(2), [], threading.Lock()
        def provider(request, context):
            context.checkpoint()
            with lock: seen.append((request.input, threading.get_ident()))
            barrier.wait(timeout=3); context.checkpoint(); return Final(request.input * 2)
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
async fn work(value){worker(value)}
await gather([acollect(async (await work(x) for x in [20])),acollect(async (await work(x) for x in [21]))])'''
        self.assertEqual(self.run_source(registry, source), [[40], [42]])
        self.assertEqual(len({thread for _, thread in seen}), 2)
        self.assertCountEqual([value for value, _ in seen], [20, 21])

    def test_real_agent_capabilities_remain_limited_inside_comprehension(self):
        registry, effects = Registry(), []
        registry.register_tool(ToolSpec('fixture.denied', lambda value, context: effects.append(value)))
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context: ToolCall('fixture.denied', request.input)))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:2};
try{[worker(x) for x in [1]]}catch e{"denied"}'''
        self.assertEqual(self.run_source(registry, source, capabilities=['fixture.denied']), 'denied')
        self.assertEqual(effects, [])

    def test_recording_scope_cannot_consume_outer_cursor_or_export_inner_cursor(self):
        registry, effects = Registry(), []
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context: effects.append(request.input) or Final(42)))
        registry.register_tool(ToolSpec('fixture.identity', lambda value, context: value))
        prefix = 'agent worker{provider:"fixture.worker",tools:[],maxSteps:1};'
        expressions = ('[worker(x) for x in cursor]', 'collect((worker(x) for x in cursor))',
                       'await acollect(async (worker(x) for x in cursor))')
        for expression in expressions:
            with self.subTest(expression=expression, direction='in'):
                source = prefix + 'let cursor=iter([1]);async fn action(x){' + expression + '};try{await record_skill(action)}catch e{e.code}'
                self.assertEqual(self.run_source(registry, source), 'mode_error')
            with self.subTest(expression=expression, direction='out'):
                source = prefix + 'var cursor=null;async fn action(x){cursor=iter([1]);tool("fixture.identity",42)};await record_skill(action);try{' + expression + '}catch e{e.code}'
                self.assertEqual(self.run_source(registry, source, capabilities=['fixture.identity']), 'mode_error')
        self.assertEqual(effects, [])

    def test_cancel_real_async_projection_drains_provider_without_source_close(self):
        registry, entered, exited, cleanup = Registry(), threading.Event(), threading.Event(), []
        def provider(request, context):
            if request.input == 'cleanup': cleanup.append(True); return Final(None)
            entered.set()
            try:
                for _ in range(500):
                    context.checkpoint(); threading.Event().wait(.01)
                raise AssertionError('comprehension effect ignored cancellation')
            finally: exited.set()
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
class Source{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}
fn __close__(self){worker("cleanup");null}}
async fn work(value){worker(value)}await acollect(async (await work(x) for x in Source()))'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('cancel-comprehension', source=source, capabilities=[])
            run = app.start(project['id'], project['version'], None, mode='source')
            try:
                self.assertTrue(entered.wait(3)); run.cancel()
                self.assertEqual(run.wait(timeout=5)['status'], 'cancelled')
                self.assertTrue(exited.is_set()); self.assertEqual(cleanup, [])
            finally: run.cancel()

    def test_debugger_pauses_each_projection_with_lexical_binder_and_original_span(self):
        source = 'let source=[20,21];\nlet result=[\n  value * 2\n  for value in source\n];\nresult'
        with Framework(settings=Settings(':memory:')) as app:
            project = app.create_project('debug-comprehension-projection', source=source)
            run = app.start_debug(project['id'], 1, pause_on_entry=False,
                                  breakpoints=[{'file': 'main.chaos', 'line': 3}])
            previous = 0
            try:
                for expected in (20, 21):
                    deadline = time.monotonic()+4
                    while time.monotonic() < deadline:
                        state = run.debugger()
                        if state['status'] == 'paused' and state['pauseId'] > previous: break
                        time.sleep(.005)
                    self.assertEqual(state['status'], 'paused'); self.assertGreater(state['pauseId'], previous)
                    self.assertEqual(state['location']['file'], 'main.chaos'); self.assertEqual(state['location']['line'], 3)
                    self.assertEqual([frame['name'] for frame in state['frames']], ['<module>'])
                    values = {row['name']: row['value'] for row in state['frames'][0]['locals']}
                    self.assertEqual(values['value'], expected)
                    previous = state['pauseId']; run.debug_command('continue', pause_id=previous)
                self.assertEqual(run.result(4), [40, 42])
            finally: run.cancel()

    def test_debugger_observes_cold_generator_without_advancing_or_exposing_owner(self):
        source = '''var reads=0;
fn mark(x){reads=reads+1;x}
let comp=(mark(x) for x in [20,21]);
let checkpoint=42;
collect(comp)'''
        with Framework(settings=Settings(':memory:')) as app:
            project = app.create_project('debug-comprehension', source=source)
            run = app.start_debug(project['id'], 1, pause_on_entry=False,
                                  breakpoints=[{'file': 'main.chaos', 'line': 4}])
            try:
                deadline = time.monotonic()+4
                while time.monotonic() < deadline:
                    state = run.debugger()
                    if state['status'] == 'paused': break
                    time.sleep(.005)
                self.assertEqual(state['status'], 'paused')
                values = {row['name']: row['value'] for frame in state['frames'] for row in frame['locals']}
                self.assertEqual(values['reads'], 0); self.assertEqual(values['comp']['type'], 'Generator')
                self.assertNotIn('owner', values['comp']); self.assertNotIn('runner', values['comp'])
                values['comp']['state'] = 'client changed'
                refreshed = {row['name']: row['value'] for frame in run.debugger()['frames'] for row in frame['locals']}
                self.assertEqual(refreshed['reads'], 0); self.assertNotEqual(refreshed['comp'].get('state'), 'client changed')
                run.debug_command('breakpoints', breakpoints=[])
                run.debug_command('continue', pause_id=state['pauseId'])
                self.assertEqual(run.result(4), [20, 21])
            finally: run.cancel()


def make_server_for_test(app, directory):
    from chaos.server import make_server
    return make_server('127.0.0.1', 0, framework=app,
                       state_dir=Path(directory)/'control', update_home=Path(directory)/'updates')


if __name__ == '__main__': unittest.main()
