"""Select under actual Registry authority, recording and persistent runtimes."""
import threading
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, ToolSpec
from chaos.settings import Settings
from test_task_join_integration import process_definition, program_definition, team_definition


class SelectIntegrationTests(unittest.TestCase):
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

    def test_agents_choose_two_inboxes_without_consuming_the_losing_inbox(self):
        registry, calls, offered = Registry(), [], []
        together = threading.Barrier(2)
        def provider(request, context):
            calls.append(request.input);offered.append(request.tools)
            together.wait(timeout=3);context.checkpoint();return Final(request.input + 1)
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        app = self.app(registry)
        source = '''agent worker{provider:"fixture.worker",tools:[]};
let a=channel();let b=channel();async fn produce(c,n){await channel_select([send_case(c,worker(n))])}
let choices=[receive_case(a),receive_case(b)];async with task_group() as g{
g.spawn(produce(a,40));g.spawn(produce(b,41));
[(await channel_select(choices)).value,(await channel_select(choices)).value]
}'''
        app.create_project('agent', source=source, capabilities=[])
        run = app.start('agent', 1, None, mode='source')
        self.assertCountEqual(run.result(5), [41, 42]);self.assertCountEqual(calls, [40, 41])
        self.assertEqual(offered, [{}, {}])
        rows = self.events(app, 'agent', run.id)
        self.assertTrue(any(e['type'] == 'agent.observing' for e in rows))
        self.assertFalse(any(e['type'] == 'tool.started' for e in rows))

    def test_team_role_results_keep_actual_provenance_across_selection(self):
        registry = Registry()
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context: Final(request.input + 1)))
        app = self.app(registry)
        source = '''let team=team_from(input);let c=channel();let stop=channel();
async fn produce(){await channel_select([send_case(c,await team_role(team,"worker")(41))])}
async with task_group() as g{g.spawn(produce());
let r=await channel_select([receive_case(stop),receive_case(c)]);[r.index,r.value]}
'''
        app.create_project('team', source=source, capabilities=[])
        run = app.start('team', 1, team_definition(), mode='source')
        self.assertEqual(run.result(5), [1, 42])
        roles = [e['payload'] for e in self.events(app, 'team', run.id) if e['type'] == 'team.role.started']
        self.assertEqual(len(roles), 1)
        self.assertEqual((roles[0]['roleId'], roles[0]['rolePath']), ('worker', ['worker']))
        self.assertTrue(roles[0]['definitionHash'])

    def test_denied_team_aborts_owned_select_and_preserves_grants(self):
        registry, providers, tools = Registry(), [], []
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                                                 providers.append(request) or Final(42)))
        registry.register_tool(ToolSpec('fixture.denied', lambda value, context: tools.append(value)))
        app = self.app(registry)
        source = '''let team=team_from(input);let a=channel();let b=channel();
async fn produce(){await channel_send(a,await team(1))}
let code=try{async with task_group() as g{g.spawn(produce());
await channel_select([receive_case(a),send_case(b,99)])}}catch e{e.code};
[code,channel_info(a).waitingReceivers,channel_info(b).waitingSenders]'''
        app.create_project('denied', source=source, capabilities=[])
        result = app.start('denied', 1, team_definition(['fixture.denied']), mode='source').result(5)
        self.assertIsInstance(result[0], str);self.assertEqual(result[1:], [0, 0])
        self.assertEqual(providers, []);self.assertEqual(tools, [])

    def test_framework_cancel_waits_for_real_provider_and_retracts_select(self):
        registry = Registry()
        entered, release, ended = (threading.Event() for _ in range(3))
        cleanup, cancelled = [], []
        def provider(request, context):
            entered.set()
            try:
                self.assertTrue(release.wait(3));cancelled.append(context.cancelled)
                context.checkpoint();return Final(42)
            finally:
                ended.set()
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        registry.register_tool(ToolSpec('fixture.cleanup', lambda value, context: cleanup.append(True)))
        app = self.app(registry)
        source = '''agent worker{provider:"fixture.worker",tools:[]};let c=channel();
async fn work(){worker(41)}try{async with task_group() as g{
g.spawn(work());await channel_select([receive_case(c)])
}}finally{tool("fixture.cleanup",null)}'''
        app.create_project('cancel', source=source, capabilities=['fixture.cleanup'])
        run = app.start('cancel', 1, None, mode='source')
        try:
            self.assertTrue(entered.wait(3));run.cancel();release.set()
            self.assertEqual(run.wait(5)['status'], 'cancelled')
        finally:
            release.set()
        self.assertTrue(ended.is_set());self.assertEqual(cancelled, [True]);self.assertEqual(cleanup, [])

    def test_nested_program_selects_locally_and_parent_receives_only_data(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value + 1))
        app = self.app(registry)
        source = '''let program=program_from(input);let c=channel();
async fn produce(){await channel_send(c,await program(41))}
async with task_group() as g{g.spawn(produce());(await channel_select([receive_case(c)])).value}'''
        child = '''let c=channel(1);await channel_select([send_case(c,tool("fixture.hit",input))]);
(await channel_select([receive_case(c)])).value'''
        app.create_project('program', source=source, capabilities=['fixture.hit'])
        run = app.start('program', 1, program_definition(child), mode='source')
        self.assertEqual(run.result(5), 42);self.assertEqual(calls, [41])
        starts = [e['payload'] for e in self.events(app, 'program', run.id) if e['type'] == 'program.execution.started']
        self.assertEqual(len(starts), 1);self.assertEqual(starts[0]['version'], 1)
        self.assertTrue(starts[0]['callId'])

    def test_live_case_is_not_json_authority_for_nested_program(self):
        app = self.app(Registry())
        source = '''let program=program_from(input);let c=channel(1);let branch=send_case(c,42);
let code=try{await program({nested:branch});null}catch e{e.code};
await channel_select([branch]);[code,(await channel_receive(c)).value]'''
        app.create_project('boundary', source=source, capabilities=[])
        run = app.start('boundary', 1, program_definition('input'), mode='source')
        result = run.result(5)
        self.assertIsInstance(result[0], str);self.assertEqual(result[1], 42)
        self.assertFalse(any(e['type'] == 'program.execution.started' for e in self.events(app, 'boundary', run.id)))

    def test_recording_selection_adds_only_real_tool_receipts(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value + 1))
        app = self.app(registry)
        source = '''async fn action(x){let c=channel();let idle=channel();
async fn produce(){await channel_select([send_case(c,tool("fixture.hit",41))])}
async with task_group() as g{g.spawn(produce());
(await channel_select([receive_case(idle),receive_case(c)])).value}}
let saved=await record_skill(action);[saved.output,mode_info(saved.skill).actions]'''
        app.create_project('record', source=source, capabilities=['fixture.hit'])
        result = app.start('record', 1, None, mode='source').result(5)
        self.assertEqual(result, [42, [{'tool': 'fixture.hit', 'input': 41}]])
        self.assertEqual(calls, [41])

    def test_recording_preflight_rejects_foreign_case_without_partial_dispatch(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value)))
        app = self.app(registry)
        source = '''let foreign=receive_case(channel());
async fn action(x){let c=channel(1);let local=send_case(c,41);
await channel_select([local,foreign],timeout=0);tool("fixture.hit",42)}
await record_skill(action)'''
        app.create_project('foreign', source=source, capabilities=['fixture.hit'])
        run = app.start('foreign', 1, None, mode='source')
        self.assertEqual(run.wait(5)['status'], 'failed');self.assertEqual(calls, [])
        self.assertFalse(any(e['type'] == 'tool.started' for e in self.events(app, 'foreign', run.id)))
        self.assertTrue(all(r['status'] != 'ready' for r in app.modes('foreign')['items']))

    def live_recording_scope(self, phase):
        registry, entered, release = Registry(), threading.Event(), threading.Event()
        def gate(value, context):
            entered.set();self.assertTrue(release.wait(3));context.checkpoint();return 42
        def ready(value, context):
            self.assertTrue(entered.wait(3))
        registry.register_tool(ToolSpec('fixture.gate', gate))
        registry.register_tool(ToolSpec('fixture.ready', ready))
        registry.register_tool(ToolSpec('fixture.release', lambda value, context: release.set()))
        app = self.app(registry)
        before = {
            'capture': '',
            'start': 'let selected=channel_select([branch]);',
            'pending': 'let selected=spawn(channel_select([branch]));while channel_info(c).waitingSenders!=1{1};',
            'buffered': 'let selected=channel_select([branch]);await selected;',
        }[phase]
        attempt = {
            'capture': 'let code=try{channel_select([branch]);null}catch e{e.code};',
            'start': 'let code=try{await selected;null}catch e{e.code};',
            'pending': 'let received=try{await channel_receive(c);null}catch e{e.code};let code=[received,try{await selected;null}catch e{e.code}];',
            'buffered': 'let code=[try{await channel_receive(c);null}catch e{e.code},task_info(selected).state];',
        }[phase]
        source = '''async fn action(x){tool("fixture.gate",null)}
async fn ready(){tool("fixture.ready",null)}let recording=record_skill(action);
let c=channel(CAPACITY);let branch=send_case(c,{nested:[recording]});
BEFORE spawn(recording);await ready();ATTEMPT
tool("fixture.release",null);let saved=await recording;
[code,saved.output,channel_info(c).buffered,channel_info(c).waitingSenders]'''
        source = source.replace('CAPACITY', '1' if phase == 'buffered' else '0').replace('BEFORE', before).replace('ATTEMPT', attempt)
        app.create_project('scope', source=source, capabilities=['fixture.gate', 'fixture.ready', 'fixture.release'])
        try:
            result = app.start('scope', 1, None, mode='source').result(5)
        finally:
            release.set()
        expected = {'capture': 'mode_error', 'start': 'mode_error',
                    'pending': ['mode_error', 'mode_error'], 'buffered': ['mode_error', 'completed']}[phase]
        self.assertEqual(result, [expected, 42, 0, 0])

    def test_select_capture_revalidates_case_live_payload_scope(self):
        self.live_recording_scope('capture')

    def test_select_start_revalidates_cold_captured_payload_scope(self):
        self.live_recording_scope('start')

    def test_pending_select_delivery_revalidates_real_recording_scope(self):
        self.live_recording_scope('pending')

    def test_buffered_select_delivery_revalidates_without_rolling_back_sender(self):
        self.live_recording_scope('buffered')

    def test_case_captured_inside_recording_cannot_escape_its_scope(self):
        registry = Registry()
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: value))
        app = self.app(registry)
        source = '''var escaped=null;async fn action(x){escaped=receive_case(channel());tool("fixture.hit",42)}
let saved=await record_skill(action);[saved.output,try{channel_select([escaped],timeout=0);null}catch e{e.code}]'''
        app.create_project('escape', source=source, capabilities=['fixture.hit'])
        self.assertEqual(app.start('escape', 1, None, mode='source').result(5), [42, 'mode_error'])

    def test_process_turn_persists_only_committed_selection_output(self):
        registry, completed = Registry(), threading.Event()
        def work(value, context):
            context.checkpoint();completed.set();return value + 41
        registry.register_tool(ToolSpec('fixture.work', work))
        app = self.app(registry)
        worker = '''async fn step(ctx){let c=channel(1);let idle=channel();
await channel_select([send_case(c,tool("fixture.work",ctx.state))]);
let r=await channel_select([receive_case(idle),receive_case(c)]);
process_step(ctx.state+1,[r.index,r.value])};export{step}'''
        app.create_project('process', source='null', files={'worker.chaos': worker},
                           capabilities=['process.manage', 'fixture.work'])
        app.create_process('process', 1, 'worker', process_definition(), state=0)
        with app._engine.idle:
            done = app._engine.idle.wait_for(lambda: app.get_process('process', 'worker')['turn'] == 1, timeout=5)
        self.assertTrue(done, app.get_process('process', 'worker'))
        row = app.get_process('process', 'worker')
        self.assertEqual((row['state'], row['output'], row['control']), (1, [1, 41], 'running'))
        self.assertTrue(completed.is_set());self.assertEqual(app.get_run(row['lastRunId'])['status'], 'completed')

    def test_select_timeout_does_not_consume_durable_mailbox(self):
        app = self.app(Registry());app._engine.pause_dispatch(True)
        source = '''await process_send("worker","retained","message");let c=channel();
let r=await channel_select([receive_case(c)],timeout=0);
let status=await process_status("worker");[r.timedOut,status.state,channel_info(c).waitingReceivers]'''
        app.create_project('durable', source=source,
                           files={'worker.chaos': 'fn step(c){process_step(c.state)};export{step}'},
                           capabilities=['process.manage'])
        app.create_process('durable', 1, 'worker', process_definition(), state=41)
        self.assertEqual(app.start('durable', 1, None, mode='source').result(5), [True, 41, 0])
        row = app.get_process('durable', 'worker')
        self.assertEqual((row['state'], row['turn']), (41, 0))
        self.assertEqual(app.send_process('durable', 'worker', 'retained', message_id='message')['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
