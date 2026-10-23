"""Waits compose with actual in-memory Framework authority and persistence paths."""
import threading
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, ToolSpec
from chaos.settings import Settings
from test_task_join_integration import process_definition, program_definition, team_definition


class WaitIntegrationTests(unittest.TestCase):
    def app(self, registry):
        app = Framework(':memory:', settings=Settings(':memory:'), registry=registry)
        self.addCleanup(app.close)
        return app

    def events(self, app, project, run):
        rows, after = [], 0
        while True:
            page = app.events(project, run_id=run, after=after, limit=300)
            rows.extend(page['items'])
            if page['nextCursor'] is None:
                return rows
            after = page['nextCursor']

    def test_agent_wait_starts_real_provider_and_preserves_unobserved_result(self):
        registry, calls, offered = Registry(), [], []
        def provider(request, context):
            context.checkpoint();calls.append(request.input);offered.append(request.tools)
            return Final(request.input + 1)
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        app = self.app(registry)
        source = '''agent worker{provider:"fixture.worker",tools:[]};
async fn call(n){worker(n)}let t=call(41);let result=await task_wait([t],until="all");
[length(result.done),result.timedOut,task_info(t).observed,await t]'''
        app.create_project('agent', source=source, capabilities=[])
        run = app.start('agent', 1, None, mode='source')
        self.assertEqual(run.result(5), [1, False, False, 42])
        self.assertEqual(calls, [41]);self.assertEqual(offered, [{}])
        self.assertTrue(any(e['type'] == 'agent.observing' for e in self.events(app, 'agent', run.id)))

    def test_team_role_wait_keeps_role_provenance_and_authority(self):
        registry = Registry()
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                                                 Final(request.input + 1)))
        app = self.app(registry)
        app.create_project('team', source='''let t=team_role(team_from(input),"worker")(41);
let result=await task_wait([t]);[length(result.done),task_info(t).observed,await t]''', capabilities=[])
        run = app.start('team', 1, team_definition(), mode='source')
        self.assertEqual(run.result(5), [1, False, 42])
        rows = [e['payload'] for e in self.events(app, 'team', run.id) if e['type'] == 'team.role.started']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['rolePath'], ['worker'])
        self.assertEqual(rows[0]['roleId'], 'worker')
        self.assertTrue(rows[0]['definitionHash'])

    def test_framework_run_cancel_drains_actual_provider_before_terminal_status(self):
        registry = Registry()
        entered, release, ended = threading.Event(), threading.Event(), threading.Event()
        cleanups, cancelled = [], []
        def provider(request, context):
            entered.set()
            try:
                self.assertTrue(release.wait(3))
                cancelled.append(context.cancelled)
                context.checkpoint()
                return Final(42)
            finally:
                ended.set()
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        registry.register_tool(ToolSpec('fixture.cleanup', lambda value, context: cleanups.append(True)))
        app = self.app(registry)
        app.create_project('cancel', source='''agent worker{provider:"fixture.worker",tools:[]};
async fn work(){worker(41)}try{await task_wait([work()])}finally{tool("fixture.cleanup",null)}''',
                           capabilities=['fixture.cleanup'])
        run = app.start('cancel', 1, None, mode='source')
        try:
            self.assertTrue(entered.wait(3))
            run.cancel()
            release.set()
            self.assertEqual(run.wait(5)['status'], 'cancelled')
        finally:
            release.set()
        self.assertTrue(ended.is_set())
        self.assertEqual(cancelled, [True])
        self.assertEqual(cleanups, [])

    def test_denied_team_error_is_reported_without_widening_permissions(self):
        registry, providers, tools = Registry(), [], []
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                                                 providers.append(request) or Final(42)))
        registry.register_tool(ToolSpec('fixture.denied', lambda value, context: tools.append(value)))
        app = self.app(registry)
        app.create_project('denied', source='''let t=team_from(input)(41);
let r=await task_wait([t],until="error");let code=try{await t}catch e{e.code};
[length(r.done),r.timedOut,code]''', capabilities=[])
        result = app.start('denied', 1, team_definition(['fixture.denied']), mode='source').result(5)
        self.assertEqual(result[:2], [1, False]);self.assertIsInstance(result[2], str)
        self.assertEqual(providers, []);self.assertEqual(tools, [])

    def test_nested_program_uses_same_wait_and_real_host_receipt(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value + 1))
        app = self.app(registry)
        app.create_project('program', source='''let p=program_from(input);let t=p(41);
let result=await task_wait([t],until="all");[length(result.done),task_info(t).observed,await t]''',
                           capabilities=['fixture.hit'])
        run = app.start('program', 1, program_definition('await sleep(0);tool("fixture.hit",input)'), mode='source')
        self.assertEqual(run.result(5), [1, False, 42]);self.assertEqual(calls, [41])
        rows = [e['payload'] for e in self.events(app, 'program', run.id)
                if e['type'] == 'program.execution.started']
        self.assertEqual(len(rows), 1);self.assertEqual(rows[0]['version'], 1)
        self.assertTrue(rows[0]['callId'])

    def test_recording_native_waits_add_no_fake_tool_receipts(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value))
        app = self.app(registry)
        app.create_project('record', source='''async fn child(n){await sleep(0);tool("fixture.hit",n)}
async fn action(x){let t=child(41);await task_wait([t]);await t}
let saved=await record_skill(action);[saved.output,mode_info(saved.skill).actions]''',
                           capabilities=['fixture.hit'])
        result = app.start('record', 1, None, mode='source').result(5)
        self.assertEqual(result, [41, [{'tool': 'fixture.hit', 'input': 41}]])
        self.assertEqual(calls, [41])

    def test_recording_preflight_checks_all_members_before_starting_any(self):
        for cancellation in ('', 'cancel(foreign);'):
            with self.subTest(cancelled=bool(cancellation)):
                registry, calls = Registry(), []
                registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value))
                app = self.app(registry)
                source = '''async fn child(n){tool("fixture.hit",n)}let foreign=child("outside");''' + cancellation + '''
async fn action(x){let local=child("inside");await task_wait([local,foreign],timeout=0)}
await record_skill(action)'''
                app.create_project('record', source=source, capabilities=['fixture.hit'])
                run = app.start('record', 1, None, mode='source')
                self.assertEqual(run.wait(5)['status'], 'failed')
                self.assertEqual(calls, [])
                self.assertFalse(any(e['type'] == 'tool.started' for e in self.events(app, 'record', run.id)))
                self.assertFalse(any(row['status'] == 'ready' for row in app.modes('record')['items']))

    def recording_scope_case(self, phase):
        registry, entered, release = Registry(), threading.Event(), threading.Event()
        def gate(value, context):
            entered.set();self.assertTrue(release.wait(3));context.checkpoint();return 42
        def ready(value, context):
            self.assertTrue(entered.wait(3))
        registry.register_tool(ToolSpec('fixture.gate', gate))
        registry.register_tool(ToolSpec('fixture.ready', ready))
        registry.register_tool(ToolSpec('fixture.release', lambda value, context: release.set()))
        app = self.app(registry)
        prepare = 'let watcher=task_wait([recording],timeout=0);' if phase == 'start' else 'let watcher=spawn(task_wait([recording]));'
        source = '''async fn action(x){tool("fixture.gate",null)}
async fn ready(){tool("fixture.ready",null)}let recording=record_skill(action);
PREPARE spawn(recording);await ready();
let code=try{await watcher;null}catch e{e.code};
tool("fixture.release",null);let saved=await recording;[code,saved.output]'''.replace('PREPARE', prepare)
        app.create_project('scope', source=source,
                           capabilities=['fixture.gate', 'fixture.ready', 'fixture.release'])
        try:
            result = app.start('scope', 1, None, mode='source').result(5)
        finally:
            release.set()
        self.assertEqual(result, ['mode_error', 42])

    def test_wait_start_rechecks_real_recording_task_scope(self):
        self.recording_scope_case('start')

    def test_registered_wait_rechecks_recording_member_scope_before_delivery(self):
        self.recording_scope_case('delivery')

    def test_recorded_timer_cannot_be_consumed_outside_its_scope(self):
        registry = Registry()
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: value))
        app = self.app(registry)
        app.create_project('timer-scope', source='''var escaped=null;
async fn action(x){escaped=sleep(0);tool("fixture.hit",42)}let saved=await record_skill(action);
[saved.output,try{await escaped;null}catch e{e.code}]''', capabilities=['fixture.hit'])
        self.assertEqual(app.start('timer-scope', 1, None, mode='source').result(5), [42, 'mode_error'])

    def test_process_turn_persists_wait_result_after_host_work(self):
        registry, completed = Registry(), threading.Event()
        def work(value, context):
            context.checkpoint();completed.set();return value + 41
        registry.register_tool(ToolSpec('fixture.work', work))
        app = self.app(registry)
        worker = '''async fn work(n){await sleep(0);tool("fixture.work",n)}
async fn step(ctx){let t=work(ctx.state);let r=await task_wait([t]);
process_step(ctx.state+1,[length(r.done),r.timedOut,await t])};export{step}'''
        app.create_project('process', source='null', files={'worker.chaos': worker},
                           capabilities=['process.manage', 'fixture.work'])
        app.create_process('process', 1, 'worker', process_definition(), state=0)
        with app._engine.idle:
            done = app._engine.idle.wait_for(lambda: app.get_process('process', 'worker')['turn'] == 1, timeout=5)
        self.assertTrue(done, app.get_process('process', 'worker'))
        row = app.get_process('process', 'worker')
        self.assertEqual((row['state'], row['output'], row['control']), (1, [1, False, 41], 'running'))
        self.assertTrue(completed.is_set())
        self.assertEqual(app.get_run(row['lastRunId'])['status'], 'completed')

    def test_native_wait_timeout_does_not_change_durable_mailbox(self):
        app = self.app(Registry());app._engine.pause_dispatch(True)
        source = '''await process_send("worker","retained","message");let c=channel();
let pending=channel_receive(c);let r=await task_wait([pending],timeout=0);
let status=await process_status("worker");cancel(pending);[r.timedOut,status.state]'''
        app.create_project('durable', source=source,
                           files={'worker.chaos': 'fn step(c){process_step(c.state)};export{step}'},
                           capabilities=['process.manage'])
        app.create_process('durable', 1, 'worker', process_definition(), state=41)
        self.assertEqual(app.start('durable', 1, None, mode='source').result(5), [True, 41])
        row = app.get_process('durable', 'worker')
        self.assertEqual((row['state'], row['turn'], row['control']), (41, 0, 'running'))
        self.assertEqual(app.send_process('durable', 'worker', 'retained', message_id='message')['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
