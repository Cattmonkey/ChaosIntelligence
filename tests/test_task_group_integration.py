"""Task groups use real Registry, recording, Program and durable Process paths."""
import threading
import time
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, ToolSpec
from chaos.settings import Settings
from test_task_join_integration import (
    CooperatingPair, process_definition, program_definition, team_definition,
)


class TaskGroupIntegrationTests(unittest.TestCase):
    def app(self, registry):
        app = Framework(':memory:', settings=Settings(':memory:'), registry=registry)
        self.addCleanup(app.close)
        return app

    def events(self, app, project, run=None):
        rows, after = [], 0
        while True:
            page = app.events(project, run_id=run, after=after, limit=300)
            rows.extend(page['items'])
            if page['nextCursor'] is None: return rows
            after = page['nextCursor']

    def assert_drained(self, result, pair):
        self.assertIsInstance(result[0], str)
        self.assertNotEqual(result[0], 'resource_limit')
        self.assertEqual(result[1:], [True, 'cancelled', 'closed'])
        self.assertTrue(pair.finished.is_set())
        self.assertTrue(pair.cancelled)
        self.assertCountEqual(pair.calls, ['peer', 'fail'])

    def test_registry_agent_group_retains_permissions_and_drains(self):
        pair, registry, offered = CooperatingPair(), Registry(), []

        def provider(request, context):
            offered.append(request.tools)
            return Final(pair.work(request.input, context))

        registry.register_provider(ProviderSpec('fixture.worker', provider))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        source = '''agent worker{provider:"fixture.worker",tools:[]};
async fn call(v){worker(v)}let peer=call("peer");let bad=call("fail");let group=task_group();
let code=try{async with group as g{g.spawn(peer);g.spawn(bad)}}catch e{e.code};
[code,tool("fixture.finished",null),task_info(peer).state,task_group_info(group).state]'''
        app = self.app(registry)
        app.create_project('agent', source=source, capabilities=['fixture.finished'])
        run = app.start('agent', 1, None, mode='source')
        self.assert_drained(run.result(5), pair)
        self.assertEqual(offered, [{}, {}])
        self.assertEqual(app.get_project('agent')['version'], 1)
        self.assertTrue(any(row['type'] == 'agent.observing' for row in self.events(app, 'agent', run.id)))

    def test_team_group_preserves_role_provenance_and_authority(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                                                 Final(pair.work(request.input, context))))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        source = '''let team=team_from(input);let peer=team("peer");let bad=team_role(team,"worker")("fail");
let group=task_group();let code=try{async with group as g{g.spawn(peer);g.spawn(bad)}}catch e{e.code};
[code,tool("fixture.finished",null),task_info(peer).state,task_group_info(group).state]'''
        app = self.app(registry)
        app.create_project('team', source=source, capabilities=['fixture.finished'])
        run = app.start('team', 1, team_definition(), mode='source')
        self.assert_drained(run.result(5), pair)
        roles = [row['payload'] for row in self.events(app, 'team', run.id)
                 if row['type'] == 'team.role.started']
        self.assertEqual(len(roles), 2)
        self.assertTrue(all(row['rolePath'] == ['worker'] and row['roleId'] == 'worker' for row in roles))
        self.assertEqual(len({row['definitionHash'] for row in roles}), 1)

    def test_group_does_not_widen_team_capability_grants(self):
        registry, providers, tools = Registry(), [], []
        registry.register_tool(ToolSpec('fixture.denied', lambda value, context: tools.append(value)))
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                                                 providers.append(request) or Final(42)))
        app = self.app(registry)
        source = '''let team=team_from(input);
try{async with task_group() as g{g.spawn(team(1));g.spawn(team(2))}}catch e{e.code}'''
        app.create_project('denied', source=source, capabilities=[])
        result = app.start('denied', 1, team_definition(['fixture.denied']), mode='source').result(5)
        self.assertIsInstance(result, str)
        self.assertEqual(providers, [])
        self.assertEqual(tools, [])

    def test_nested_program_group_preserves_versions_and_shared_cancellation(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_tool(ToolSpec('fixture.work', pair.work))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        source = '''let program=program_from(input);let peer=program("peer");let bad=program("fail");
let group=task_group();let code=try{async with group as g{g.spawn(peer);g.spawn(bad)}}catch e{e.code};
[code,tool("fixture.finished",null),task_info(peer).state,task_group_info(group).state]'''
        app = self.app(registry)
        app.create_project('program', source=source, capabilities=['fixture.work', 'fixture.finished'])
        run = app.start('program', 1, program_definition('tool("fixture.work",input)'), mode='source')
        self.assert_drained(run.result(5), pair)
        starts = [row['payload'] for row in self.events(app, 'program', run.id)
                  if row['type'] == 'program.execution.started']
        self.assertEqual(len(starts), 2)
        self.assertEqual(len({row['callId'] for row in starts}), 2)
        self.assertTrue(all(row['version'] == 1 for row in starts))
        self.assertEqual(app.get_project('program')['source'], source)

    def test_recording_rejects_foreign_gather_closure_before_any_dispatch(self):
        for cancellation in ('', 'cancel(foreign);'):
            with self.subTest(cancelled=bool(cancellation)):
                registry, calls = Registry(), []
                registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value))
                app = self.app(registry)
                source = '''async fn child(v){tool("fixture.hit",v)}let foreign=child("outside");''' + cancellation + '''
async fn action(x){async with task_group() as g{
    let local=child("inside");g.spawn(gather([local,foreign]))
};42}await record_skill(action)'''
                app.create_project('record', source=source, capabilities=['fixture.hit'])
                run = app.start('record', 1, None, mode='source')
                self.assertEqual(run.wait(5)['status'], 'failed')
                self.assertEqual(calls, [])
                self.assertFalse(any(row['type'] == 'tool.started' for row in self.events(app, 'record', run.id)))
                self.assertTrue(all(row['status'] != 'ready' for row in app.modes('record')['items']))

    def test_recording_sequential_groups_keep_receipts_in_order(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value))
        app = self.app(registry)
        source = '''async fn child(v){tool("fixture.hit",v)}
async fn action(x){async with task_group() as first{first.spawn(child("first"))};
async with task_group() as second{second.spawn(child("second"))};42}
let saved=await record_skill(action);[saved.output,mode_info(saved.skill).actions]'''
        app.create_project('record', source=source, capabilities=['fixture.hit'])
        result = app.start('record', 1, None, mode='source').result(5)
        self.assertEqual(result, [42, [{'tool':'fixture.hit', 'input':'first'},
                                      {'tool':'fixture.hit', 'input':'second'}]])
        self.assertEqual(calls, ['first', 'second'])

    def test_recording_group_failure_drains_before_abort_and_never_seals(self):
        registry, calls, ended = Registry(), [], threading.Event()

        def work(value, context):
            calls.append(value)
            deadline = time.monotonic()+3
            try:
                while not ended.wait(.005):
                    context.checkpoint()
                    if time.monotonic() > deadline:
                        raise AssertionError('recording failure did not cancel member')
            finally:
                ended.set()

        registry.register_tool(ToolSpec('fixture.work', work))
        app = self.app(registry)
        source = '''async fn child(n){tool("fixture.work",n)}
async fn action(x){try{async with task_group() as g{g.spawn(child(1));g.spawn(child(2))}}
catch error{null};42}await record_skill(action)'''
        app.create_project('record', source=source, capabilities=['fixture.work'])
        run = app.start('record', 1, None, mode='source')
        self.assertEqual(run.wait(5)['status'], 'failed')
        self.assertEqual(len(calls), 1)
        self.assertTrue(ended.is_set())
        self.assertTrue(all(row['status'] != 'ready' for row in app.modes('record')['items']))

    def test_process_turn_commits_only_after_group_members_exit(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_tool(ToolSpec('fixture.work', pair.work))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        app = self.app(registry)
        worker = '''async fn work(v){tool("fixture.work",v)}
async fn step(ctx){let peer=work("peer");let bad=work("fail");let group=task_group();
let code=try{async with group as g{g.spawn(peer);g.spawn(bad)}}catch e{e.code};
process_step(ctx.state+1,[code,tool("fixture.finished",null),task_info(peer).state,task_group_info(group).state])};
export {step}'''
        app.create_project('process', source='null', files={'worker.chaos':worker},
                           capabilities=['process.manage', 'fixture.work', 'fixture.finished'])
        app.create_process('process', 1, 'worker', process_definition(), state=0)
        with app._engine.idle:
            completed = app._engine.idle.wait_for(
                lambda: app.get_process('process', 'worker')['turn'] == 1, timeout=5)
        self.assertTrue(completed, app.get_process('process', 'worker'))
        row = app.get_process('process', 'worker')
        self.assert_drained(row['output'], pair)
        self.assertEqual((row['state'], row['control']), (1, 'running'))
        self.assertEqual(app.get_run(row['lastRunId'])['status'], 'completed')

    def test_group_failure_does_not_close_or_rollback_durable_process(self):
        app = self.app(Registry())
        app._engine.pause_dispatch(True)
        source = '''await process_send("worker","retained","message");
try{async with task_group() as g{g.spawn(process_status("worker"));g.spawn(process_status("absent"))}}
catch error{error.code}'''
        app.create_project('process', source=source,
                           files={'worker.chaos':'fn step(c){process_step(c.state)};export {step}'},
                           capabilities=['process.manage'])
        app.create_process('process', 1, 'worker', process_definition(), state=41)
        self.assertIsInstance(app.start('process', 1, None, mode='source').result(5), str)
        row = app.get_process('process', 'worker')
        self.assertEqual((row['control'], row['state'], row['turn']), ('running', 41, 0))
        self.assertEqual(app.send_process('process', 'worker', 'retained', message_id='message')['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
