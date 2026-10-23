"""Real Registry, permissions and durable runtimes share the language join path."""
import threading
import time
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, ToolSpec
from chaos.settings import Settings


def team_definition(tools=()):
    tools = list(tools)
    return {'kind': 'team', 'name': 'join-team', 'entry': 'worker', 'roles': [
        {'id': 'worker', 'parent': None, 'capabilities': tools,
         'agent': {'kind': 'agent', 'name': 'worker',
                   'config': {'provider': 'fixture.worker', 'tools': tools}}}]}


def program_definition(source):
    return {'kind': 'program', 'entry': 'main.chaos', 'files': {'main.chaos': source},
            'languageVersions': {'main.chaos': 2}}


def process_definition():
    return {'kind': 'process', 'name': 'join-process', 'module': 'worker.chaos',
            'export': 'step', 'intervalSeconds': None, 'maxTurns': 10}


class CooperatingPair:
    """One ordinary failure; its peer exits only after actual host cancellation."""
    def __init__(self):
        self.entered = threading.Barrier(2)
        self.finished = threading.Event()
        self.cancelled = False
        self.calls = []

    def work(self, value, context):
        self.calls.append(value)
        self.entered.wait(timeout=3)
        if value == 'fail':
            raise ValueError('ordinary fixture failure')
        deadline = time.monotonic() + 3
        never_released = threading.Event()
        try:
            while not never_released.wait(.005):
                context.checkpoint()
                if time.monotonic() >= deadline:
                    raise AssertionError('peer never received cancellation')
        finally:
            self.cancelled = context.cancelled
            self.finished.set()

    def snapshot(self, value, context):
        return self.finished.is_set()


class TaskJoinIntegrationTests(unittest.TestCase):
    def app(self, registry, **options):
        app = Framework(':memory:', settings=Settings(':memory:'), registry=registry, **options)
        self.addCleanup(app.close)
        return app

    def events(self, app, project, run=None):
        rows, after = [], 0
        while True:
            page = app.events(project, run_id=run, after=after, limit=300)
            rows.extend(page['items'])
            if page['nextCursor'] is None:
                return rows
            after = page['nextCursor']

    def assert_drained_result(self, result, pair):
        self.assertIsInstance(result[0], str)
        self.assertNotEqual(result[0], 'resource_limit')
        self.assertEqual(result[1:], [True, 'cancelled'])
        self.assertTrue(pair.finished.is_set())
        self.assertTrue(pair.cancelled)
        self.assertCountEqual(pair.calls, ['peer', 'fail'])

    def test_registry_agent_peer_cancel_drains_before_catch(self):
        pair, registry, offered = CooperatingPair(), Registry(), []
        def provider(request, context):
            offered.append(request.tools)
            return Final(pair.work(request.input, context))
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        source = '''agent worker {provider:"fixture.worker",tools:[]};
async fn call(v){worker(v)}let peer=call("peer");let bad=call("fail");
let code=try{await gather([peer,bad])}catch e{e.code};
[code,tool("fixture.finished",null),task_info(peer).state]'''
        app = self.app(registry)
        app.create_project('agent', source=source, capabilities=['fixture.finished'])
        run = app.start('agent', 1, None, mode='source')
        self.assert_drained_result(run.result(5), pair)
        self.assertEqual(offered, [{}, {}])
        self.assertEqual(app.get_project('agent')['version'], 1)
        self.assertTrue(any(row['type'] == 'agent.observing'
                            for row in self.events(app, 'agent', run.id)))

    def test_team_peer_cancel_preserves_role_provenance_and_authority(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                                                 Final(pair.work(request.input, context))))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        source = '''let team=team_from(input);let peer=team("peer");let bad=team_role(team,"worker")("fail");
let code=try{await gather([peer,bad])}catch e{e.code};
[code,tool("fixture.finished",null),task_info(peer).state]'''
        app = self.app(registry)
        app.create_project('team', source=source, capabilities=['fixture.finished'])
        run = app.start('team', 1, team_definition(), mode='source')
        self.assert_drained_result(run.result(5), pair)
        roles = [row['payload'] for row in self.events(app, 'team', run.id)
                 if row['type'] == 'team.role.started']
        self.assertEqual(len(roles), 2)
        self.assertTrue(all(row['rolePath'] == ['worker'] and row['roleId'] == 'worker' for row in roles))
        self.assertEqual(len({row['definitionHash'] for row in roles}), 1)

    def test_team_batch_does_not_widen_project_grants(self):
        registry, providers, tools = Registry(), [], []
        registry.register_tool(ToolSpec('fixture.denied', lambda value, ctx: tools.append(value)))
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, ctx:
                                                 providers.append(request) or Final(42)))
        app = self.app(registry)
        app.create_project('denied', source='let t=team_from(input);try{await gather([t(1),t(2)])}catch e{e.code}',
                           capabilities=[])
        run = app.start('denied', 1, team_definition(['fixture.denied']), mode='source')
        self.assertIsInstance(run.result(5), str)
        self.assertEqual(providers, [])
        self.assertEqual(tools, [])

    def test_nested_program_peer_cancel_uses_shared_host_lifecycle(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_tool(ToolSpec('fixture.work', pair.work))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        source = '''let p=program_from(input);let peer=p("peer");let bad=p("fail");
let code=try{await gather([peer,bad])}catch e{e.code};
[code,tool("fixture.finished",null),task_info(peer).state]'''
        app = self.app(registry)
        app.create_project('program', source=source, capabilities=['fixture.work', 'fixture.finished'])
        run = app.start('program', 1, program_definition('tool("fixture.work",input)'), mode='source')
        self.assert_drained_result(run.result(5), pair)
        rows = self.events(app, 'program', run.id)
        starts = [row['payload'] for row in rows if row['type'] == 'program.execution.started']
        self.assertEqual(len(starts), 2)
        self.assertEqual(len({row['callId'] for row in starts}), 2)
        self.assertTrue(all(row['version'] == 1 for row in starts))
        self.assertEqual(app.get_project('program')['source'], source)

    def test_recording_preflight_checks_later_foreign_even_if_cancelled(self):
        for cancel_source in ('', 'cancel(foreign);'):
            with self.subTest(cancelled=bool(cancel_source)):
                registry, calls = Registry(), []
                registry.register_tool(ToolSpec('fixture.hit', lambda value, ctx: calls.append(value) or value))
                app = self.app(registry)
                source = '''async fn child(v){tool("fixture.hit",v)}let foreign=child("outside");''' + cancel_source + '''
async fn action(x){let local=child("inside");await gather([local,foreign]);42}
await record_skill(action)'''
                app.create_project('record', source=source, capabilities=['fixture.hit'])
                run = app.start('record', 1, None, mode='source')
                self.assertEqual(run.wait(5)['status'], 'failed')
                self.assertEqual(calls, [])
                self.assertFalse(any(row['type'] == 'tool.started' for row in self.events(app, 'record', run.id)))
                self.assertTrue(all(row['status'] != 'ready' for row in app.modes('record')['items']))

    def test_recording_valid_joins_keep_receipts_in_source_order(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, ctx: calls.append(value) or value))
        app = self.app(registry)
        source = '''async fn child(v){tool("fixture.hit",v)}
async fn action(x){await gather([child("first")]);await gather([child("second")]);42}
let saved=await record_skill(action);[saved.output,mode_info(saved.skill).actions]'''
        app.create_project('record', source=source, capabilities=['fixture.hit'])
        result = app.start('record', 1, None, mode='source').result(5)
        self.assertEqual(result, [42, [{'tool': 'fixture.hit', 'input': 'first'},
                                      {'tool': 'fixture.hit', 'input': 'second'}]])
        self.assertEqual(calls, ['first', 'second'])

    def test_recording_concurrent_rejection_drains_before_abort(self):
        registry, called, finished = Registry(), [], threading.Event()
        def wait(value, context):
            called.append(value)
            deadline = time.monotonic() + 3
            try:
                while not finished.wait(.005):
                    context.checkpoint()
                    if time.monotonic() > deadline:
                        raise AssertionError('recording rejection did not cancel active peer')
            finally:
                finished.set()
        registry.register_tool(ToolSpec('fixture.wait', wait))
        app = self.app(registry)
        source = '''async fn child(n){tool("fixture.wait",n)}
async fn action(x){try{await gather([child(1),child(2)])}catch e{null};42}await record_skill(action)'''
        app.create_project('record', source=source, capabilities=['fixture.wait'])
        run = app.start('record', 1, None, mode='source')
        self.assertEqual(run.wait(5)['status'], 'failed')
        self.assertEqual(len(called), 1)
        self.assertTrue(finished.is_set())
        self.assertTrue(all(row['status'] != 'ready' for row in app.modes('record')['items']))

    def test_process_turn_commits_only_after_its_task_join_drains(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_tool(ToolSpec('fixture.work', pair.work))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        app = self.app(registry)
        worker = '''async fn work(v){tool("fixture.work",v)}
async fn step(ctx){let peer=work("peer");let bad=work("fail");
let code=try{await gather([peer,bad])}catch e{e.code};
process_step(ctx.state+1,[code,tool("fixture.finished",null),task_info(peer).state])};export {step}'''
        app.create_project('process', source='null', files={'worker.chaos': worker},
                           capabilities=['process.manage', 'fixture.work', 'fixture.finished'])
        app.create_process('process', 1, 'worker', process_definition(), state=0)
        with app._engine.idle:
            completed = app._engine.idle.wait_for(
                lambda: app.get_process('process', 'worker')['turn'] == 1, timeout=5)
        self.assertTrue(completed, app.get_process('process', 'worker'))
        row = app.get_process('process', 'worker')
        self.assert_drained_result(row['output'], pair)
        self.assertEqual(row['state'], 1)
        self.assertEqual(row['control'], 'running')
        self.assertEqual(app.get_run(row['lastRunId'])['status'], 'completed')

    def test_process_operation_failure_does_not_stop_or_undo_durable_process(self):
        registry = Registry()
        app = self.app(registry)
        app._engine.pause_dispatch(True)
        source = '''await process_send("worker","retained","message");
try{await gather([process_status("worker"),process_status("absent")])}catch e{e.code}'''
        app.create_project('process', source=source,
                           files={'worker.chaos': 'fn step(c){process_step(c.state)};export {step}'},
                           capabilities=['process.manage'])
        app.create_process('process', 1, 'worker', process_definition(), state=41)
        result = app.start('process', 1, None, mode='source').result(5)
        self.assertIsInstance(result, str)
        row = app.get_process('process', 'worker')
        self.assertEqual((row['control'], row['state'], row['turn']), ('running', 41, 0))
        self.assertEqual(app.send_process('process', 'worker', 'retained', message_id='message')['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
