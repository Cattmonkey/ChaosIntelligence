"""Robot roles reuse the original callable, message, durable session and Program runtime."""
import json
from pathlib import Path
import tempfile
import unittest
from chaos import Framework, Registry, ProviderSpec, ToolSpec, ToolCall, Final, Settings
from tests.test_process_integration import wait_for, definition
from tests.test_session_integration import NativeFixture

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'examples/robot-composition.chaos').read_text()


def program(source):
    return {'kind': 'program', 'entry': 'main.chaos', 'files': {'main.chaos': source}, 'languageVersions': {'main.chaos': 2}}


class RobotComposition(unittest.TestCase):
    def test_structured_recipients_share_source_and_ir_runtime(self):
        self.assertEqual(Framework.evaluate(SOURCE), 42)
        with Framework(settings=Settings(':memory:')) as app:
            project = app.create_project('robot', source=SOURCE, capabilities=[])
            self.assertEqual(app.start('robot', 1, None, mode='graph').result(10), 42)
            run = app.start('robot', 1, None, mode='source')
            self.assertEqual(run.result(10), 42)
            events = app.events('robot', run_id=run.id)['items']
            self.assertEqual(sum(e['type'] == 'team.role.completed' for e in events), 2)

    def test_real_agent_tool_delegates_without_text_command_recognition(self):
        calls = []
        def provider(request, context):
            context.checkpoint(); calls.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('dispatch', request.input)
        registry = Registry(); registry.register_provider(ProviderSpec('fixture.robot', provider))
        source = SOURCE[:SOURCE.index('let inbox')] + '''agent planner {provider:"fixture.robot"};
await agent_bind(planner,[agent_tool("dispatch",dispatch)])({to:"worker",value:40,text:"@main [PASS]"})'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('robot', source=source, capabilities=[])
            self.assertEqual(app.start('robot', 1, None, mode='source').result(10), 42)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['text'], '@main [PASS]')

    def test_main_role_name_does_not_grant_authority(self):
        calls = []; registry = Registry()
        registry.register_tool(ToolSpec('fixture.secret', lambda value, context: calls.append(value) or 42))
        source = SOURCE[:SOURCE.index('let workers')] + '''let robot=team_bind(team,{main:fn(value){tool("fixture.secret",value)}});
try {await robot(40)} catch e {e.code}'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('robot', source=source, capabilities=['fixture.secret'])
            self.assertEqual(app.start('robot', 1, None, mode='source').result(10), 'capability_error')
        self.assertEqual(calls, [])

    def test_durable_robot_mailbox_and_memory_restart_without_another_scheduler(self):
        module = '''fn step(c) {
if c.input==null {process_step(c.state)} else {
if c.input.to!="worker" {throw ValueError("unknown recipient")} else {null};
let memory={count:c.state.count+1,last:c.input.value};
process_step(memory,c.input.value+2)
}};export{step};'''
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'robot.sqlite3'
            with Framework(database, settings=Settings(':memory:')) as app:
                app.create_project('p', source='null', files={'dot.chaos': module}, capabilities=['process.manage'])
                app.create_process('p', 1, 'dot', definition(), state={'count': 0, 'last': None})
                wait_for(app)
                app._engine.pause_dispatch(True)
                app.send_process('p', 'dot', {'to': 'worker', 'value': 40, 'text': '[PASS]'}, message_id='order1')
            with Framework(database, settings=Settings(':memory:')) as app:
                record = wait_for(app, predicate=lambda row: row['turn'] == 2)
                self.assertEqual(record['output'], 42)
                self.assertEqual(record['state'], {'count': 1, 'last': 40})
                receipt = app.send_process('p', 'dot', {'to': 'worker', 'value': 40, 'text': '[PASS]'}, message_id='order1')
                self.assertEqual(receipt['status'], 'consumed')
                self.assertEqual(app.get_process('p', 'dot')['turn'], 2)

    def test_private_and_group_sessions_share_agent_but_not_implicit_history(self):
        fixture = NativeFixture(lambda body: {'role': 'assistant', 'content': 'reply'})
        self.addCleanup(fixture.close)
        source = '''agent worker {provider:"model",tools:[]};
let session=await session_open(input.key,worker);
(await session(input.text,input.messageId)).output'''
        with Framework(settings=fixture.settings) as app:
            app.create_project('robot', source=source, capabilities=['session.manage'])
            private = {'key': 'private_worker', 'text': 'private-only detail', 'messageId': 'private1'}
            group = {'key': 'room_worker', 'text': 'explicit shared excerpt', 'messageId': 'group1'}
            self.assertEqual(app.start('robot', 1, private, mode='source').result(10), 'reply')
            self.assertEqual(app.start('robot', 1, group, mode='source').result(10), 'reply')
            messages = json.dumps(fixture.requests[-1]['messages'])
            self.assertIn('explicit shared excerpt', messages)
            self.assertNotIn('private-only detail', messages)
            self.assertEqual(app.agent_session('robot', 'private_worker')['turn'], 1)
            self.assertEqual(app.agent_session('robot', 'room_worker')['turn'], 1)

    def test_generated_robot_helpers_use_existing_code_module_and_program(self):
        source = '''let dispatch=function_from(code_from("fn(value){value+2}"));
let module=module_from(program_from(input),{dispatch:dispatch});module.run(40)'''
        self.assertEqual(Framework.evaluate(source, {'input': program('fn run(value){dispatch(value)};export{run};')}), 42)

    def test_robot_evolution_adopts_tested_program_and_keeps_old_snapshot(self):
        source = '''let base=current_program();let proposed=program_from(input);
let cases=[{input:40,expect:42}];let probe=await program_probe(proposed,cases);
let receipt=await program_adopt(base,proposed,cases,"robot behavior update");
[probe.passed,receipt.version,program_metadata(current_program()).origin.version]'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('robot', source=source, capabilities=['program.adopt'])
            original = app.start('robot', 1, program('input+2'), mode='source')
            self.assertEqual(original.result(10), [True, 2, 1])
            self.assertEqual(app.start('robot', 2, 40, mode='source').result(10), 42)
            self.assertEqual(original.get()['snapshot']['source'], source)


if __name__ == '__main__':
    unittest.main()
