"""Native Channels through real local agents, grants, recording and persistence."""
import threading
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, ToolSpec
from chaos.settings import Settings
from test_task_join_integration import process_definition, program_definition, team_definition


class ChannelIntegrationTests(unittest.TestCase):
    def app(self, registry):
        app = Framework(':memory:', settings=Settings(':memory:'), registry=registry)
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

    def test_registry_agents_exchange_results_without_new_tool_grants(self):
        registry, offered, inputs = Registry(), [], []
        together = threading.Barrier(2)

        def provider(request, context):
            inputs.append(request.input)
            offered.append(request.tools)
            together.wait(timeout=3)
            context.checkpoint()
            return Final(request.input + 1)

        registry.register_provider(ProviderSpec('fixture.worker', provider))
        app = self.app(registry)
        source = '''agent worker{provider:"fixture.worker",tools:[]};
let c=channel(1);async fn produce(n){await channel_send(c,worker(n))}
let values=async with task_group() as g{
 g.spawn(produce(20));g.spawn(produce(21));
 [(await channel_receive(c)).value,(await channel_receive(c)).value]
};channel_close(c);[values,await channel_receive(c)]'''
        app.create_project('agent', source=source, capabilities=[])
        run = app.start('agent', 1, None, mode='source')
        values, final = run.result(5)
        self.assertCountEqual(values, [21, 22])
        self.assertEqual(final, {'done': True, 'value': None})
        self.assertCountEqual(inputs, [20, 21])
        self.assertEqual(offered, [{}, {}])
        self.assertTrue(any(row['type'] == 'agent.observing' for row in self.events(app, 'agent', run.id)))
        self.assertFalse(any(row['type'] == 'tool.started' for row in self.events(app, 'agent', run.id)))

    def test_team_role_results_keep_authority_and_provenance_across_channel(self):
        registry = Registry()
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context: Final(request.input * 2)))
        app = self.app(registry)
        source = '''let team=team_from(input);let c=channel();
async fn produce(n){await channel_send(c,await team_role(team,"worker")(n))}
let values=async with task_group() as g{
 g.spawn(produce(20));g.spawn(produce(21));
 [(await channel_receive(c)).value,(await channel_receive(c)).value]
};channel_close(c);values'''
        app.create_project('team', source=source, capabilities=[])
        run = app.start('team', 1, team_definition(), mode='source')
        self.assertCountEqual(run.result(5), [40, 42])
        roles = [row['payload'] for row in self.events(app, 'team', run.id)
                 if row['type'] == 'team.role.started']
        self.assertEqual(len(roles), 2)
        self.assertTrue(all(row['roleId'] == 'worker' and row['rolePath'] == ['worker'] for row in roles))
        self.assertEqual(len({row['definitionHash'] for row in roles}), 1)

    def test_denied_team_cancels_channel_consumer_without_widening_grants(self):
        registry, providers, tools = Registry(), [], []
        registry.register_tool(ToolSpec('fixture.denied', lambda value, context: tools.append(value)))
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                                                providers.append(request) or Final(42)))
        app = self.app(registry)
        source = '''let team=team_from(input);let c=channel();
async fn produce(){await channel_send(c,await team(1))}
let code=try{async with task_group() as g{
 g.spawn(produce());await channel_receive(c)
}}catch e{e.code};[code,channel_info(c).waitingReceivers,channel_info(c).state]'''
        app.create_project('denied', source=source, capabilities=[])
        result = app.start('denied', 1, team_definition(['fixture.denied']), mode='source').result(5)
        self.assertIsInstance(result[0], str)
        self.assertEqual(result[1:], [0, 'open'])
        self.assertEqual(providers, [])
        self.assertEqual(tools, [])

    def test_nested_program_uses_its_own_channel_and_parent_transfers_data_only(self):
        app = self.app(Registry())
        source = '''let program=program_from(input);let c=channel();
async fn produce(n){await channel_send(c,await program(n))}
let values=async with task_group() as g{
 g.spawn(produce(20));g.spawn(produce(21));
 [(await channel_receive(c)).value,(await channel_receive(c)).value]
};channel_close(c);values'''
        child = '''let c=channel(1);await channel_send(c,input*2);channel_close(c);await acollect(c)'''
        app.create_project('program', source=source, capabilities=[])
        run = app.start('program', 1, program_definition(child), mode='source')
        self.assertCountEqual(run.result(5), [[40], [42]])
        starts = [row['payload'] for row in self.events(app, 'program', run.id)
                  if row['type'] == 'program.execution.started']
        self.assertEqual(len(starts), 2)
        self.assertEqual(len({row['callId'] for row in starts}), 2)
        self.assertTrue(all(row['version'] == 1 for row in starts))
        self.assertEqual(app.get_project('program')['source'], source)

    def test_live_channel_cannot_cross_program_json_boundary(self):
        app = self.app(Registry())
        source = '''let program=program_from(input);let c=channel(1);
await channel_send(c,42);let code=try{await program({nested:c});null}catch e{e.code};
[code,(await channel_receive(c)).value,channel_info(c).state]'''
        app.create_project('boundary', source=source, capabilities=[])
        run = app.start('boundary', 1, program_definition('input'), mode='source')
        result = run.result(5)
        self.assertIsInstance(result[0], str)
        self.assertEqual(result[1:], [42, 'open'])
        self.assertFalse(any(row['type'] == 'program.execution.started'
                             for row in self.events(app, 'boundary', run.id)))

    def test_recording_channel_pipeline_preserves_only_real_tool_receipts(self):
        registry, calls = Registry(), []
        registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value) or value + 1))
        app = self.app(registry)
        source = '''async fn action(x){let c=channel();
 async fn produce(){await channel_send(c,tool("fixture.hit",41))}
 let result=async with task_group() as g{g.spawn(produce());(await channel_receive(c)).value};
 channel_close(c);result
};let saved=await record_skill(action);[saved.output,mode_info(saved.skill).actions]'''
        app.create_project('record', source=source, capabilities=['fixture.hit'])
        result = app.start('record', 1, None, mode='source').result(5)
        self.assertEqual(result, [42, [{'tool': 'fixture.hit', 'input': 41}]])
        self.assertEqual(calls, [41])

    def test_recording_rejects_foreign_open_and_closed_channels_before_tool_dispatch(self):
        for closing in ('', 'channel_close(foreign);'):
            with self.subTest(closed=bool(closing)):
                registry, calls = Registry(), []
                registry.register_tool(ToolSpec('fixture.hit', lambda value, context: calls.append(value)))
                app = self.app(registry)
                source = '''let foreign=channel();''' + closing + '''
async fn action(x){channel_info(foreign);tool("fixture.hit",1);42};await record_skill(action)'''
                app.create_project('foreign', source=source, capabilities=['fixture.hit'])
                run = app.start('foreign', 1, None, mode='source')
                self.assertEqual(run.wait(5)['status'], 'failed')
                self.assertEqual(calls, [])
                self.assertTrue(all(row['status'] != 'ready' for row in app.modes('foreign')['items']))

    def test_recording_failure_does_not_seal_or_leave_channel_receivers(self):
        app = self.app(Registry())
        source = '''async fn action(x){let c=channel();
 async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "recorded failure"}
 async with task_group() as g{g.spawn(bad());await channel_receive(c)}
};await record_skill(action)'''
        app.create_project('record-fail', source=source, capabilities=[])
        run = app.start('record-fail', 1, None, mode='source')
        self.assertEqual(run.wait(5)['status'], 'failed')
        self.assertTrue(all(row['status'] != 'ready' for row in app.modes('record-fail')['items']))
        self.assertFalse(any(row['type'] == 'tool.started' for row in self.events(app, 'record-fail', run.id)))

    def test_send_start_revalidates_nested_live_task_recording_scope(self):
        self.live_scope_transfer('start')

    def test_buffered_delivery_revalidates_live_scope_without_rollback_of_sender(self):
        self.live_scope_transfer('buffered')

    def test_pending_delivery_revalidates_live_scope_and_rejects_both_operations(self):
        self.live_scope_transfer('pending')

    def live_scope_transfer(self, phase):
        registry = Registry()
        entered, release = threading.Event(), threading.Event()

        def gate(value, context):
            entered.set()
            self.assertTrue(release.wait(3))
            context.checkpoint()
            return 42

        def ready(value, context):
            self.assertTrue(entered.wait(3))

        registry.register_tool(ToolSpec('fixture.gate', gate))
        registry.register_tool(ToolSpec('fixture.ready', ready))
        registry.register_tool(ToolSpec('fixture.release', lambda value, context: release.set()))
        app = self.app(registry)
        capacity = 0 if phase == 'pending' else 1
        prepare = {'start': '', 'buffered': 'await pending;', 'pending': '''spawn(pending);
async fn queued(){while channel_info(c).waitingSenders!=1{1}};await queued();'''}[phase]
        operation = 'await pending' if phase == 'start' else 'await channel_receive(c)'
        source = '''let c=channel(CAPACITY);
async fn action(x){tool("fixture.gate",null)}
async fn ready(){tool("fixture.ready",null)}
let recording=record_skill(action);let pending=channel_send(c,{nested:[recording]});
PREPARE spawn(recording);await ready();
let code=try{OPERATION;null}catch e{e.code};
let before=channel_info(c);let sender=task_info(pending).state;
let senderCode=try{await pending;null}catch e{e.code};
tool("fixture.release",null);await recording;
[code,before.buffered,before.waitingSenders,before.waitingReceivers,sender,senderCode]'''.replace(
            'CAPACITY', str(capacity)).replace('PREPARE', prepare).replace('OPERATION', operation)
        app.create_project('live-scope', source=source,
                           capabilities=['fixture.gate', 'fixture.ready', 'fixture.release'])
        try:
            result = app.start('live-scope', 1, None, mode='source').result(5)
        finally:
            release.set()
        sender_state = 'completed' if phase == 'buffered' else 'failed'
        sender_code = None if phase == 'buffered' else 'mode_error'
        self.assertEqual(result, ['mode_error', 0, 0, 0, sender_state, sender_code])

    def test_process_turn_commits_channel_result_after_real_host_producer(self):
        registry, ended = Registry(), threading.Event()

        def produce(value, context):
            context.checkpoint()
            ended.set()
            return value + 41

        registry.register_tool(ToolSpec('fixture.produce', produce))
        app = self.app(registry)
        worker = '''async fn step(ctx){let c=channel();
 async fn produce(){await channel_send(c,tool("fixture.produce",ctx.state))}
 let value=async with task_group() as g{g.spawn(produce());(await channel_receive(c)).value};
 channel_close(c);process_step(ctx.state+1,[value,channel_info(c).state])
};export {step}'''
        app.create_project('process', source='null', files={'worker.chaos': worker},
                           capabilities=['process.manage', 'fixture.produce'])
        app.create_process('process', 1, 'worker', process_definition(), state=0)
        with app._engine.idle:
            completed = app._engine.idle.wait_for(
                lambda: app.get_process('process', 'worker')['turn'] == 1, timeout=5)
        self.assertTrue(completed, app.get_process('process', 'worker'))
        row = app.get_process('process', 'worker')
        self.assertEqual((row['state'], row['output'], row['control']), (1, [41, 'closed'], 'running'))
        self.assertTrue(ended.is_set())
        self.assertEqual(app.get_run(row['lastRunId'])['status'], 'completed')

    def test_channel_close_and_group_failure_do_not_close_durable_mailbox(self):
        app = self.app(Registry())
        app._engine.pause_dispatch(True)
        source = '''await process_send("worker","retained","message");let c=channel();channel_close(c);
let code=try{async with task_group() as g{g.spawn(channel_send(c,41))}}catch e{e.code};
[code,await process_status("worker")]'''
        app.create_project('durable', source=source,
                           files={'worker.chaos': 'fn step(c){process_step(c.state)};export {step}'},
                           capabilities=['process.manage'])
        app.create_process('durable', 1, 'worker', process_definition(), state=41)
        result = app.start('durable', 1, None, mode='source').result(5)
        self.assertIsInstance(result[0], str)
        row = app.get_process('durable', 'worker')
        self.assertEqual((row['control'], row['state'], row['turn']), ('running', 41, 0))
        self.assertEqual(app.send_process('durable', 'worker', 'retained', message_id='message')['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
