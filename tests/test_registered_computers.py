"""Registered visual devices compose through the original language and host.

Fixtures are deterministic trusted drivers: no physical inputs, browsers,
network requests or model APIs are used in these language integration tests.
"""
import copy
import struct
import zlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall
from chaos.computer_adapters import ComputerSpec, prepare_computer_image
from chaos.computer_config import load_computer_registry
from chaos.extensions import DEFAULT_CAPABILITIES, language_permissions
from chaos.language import Evaluator, LanguageError, Native, Parser
from chaos.program import decode_program, format_program, parse_program, plain


ACTIONS = ('navigate', 'click', 'input', 'button', 'launch', 'swipe')
SOURCE = '''async with await computer_attach("phone") as device {
 let before=await computer_observe(device,false);
 let action=computer_action(before,{action:"click",ref:"e1"});
 let after=await device(action);
 [observation_info(after).receipt.status,action_info(action).used,
  computer_info(device).backend]
}'''


class Driver:
    def __init__(self, calls, *, malformed=False, repeat=False, fail_close_once=False):
        self.calls = calls
        self.sequence = 0
        self.closed = False
        self.malformed = malformed
        self.repeat = repeat
        self.fail_close_once = fail_close_once
        self.close_count = 0
    def call(self, command, checkpoint):
        checkpoint()
        self.calls.append(('driver', copy.deepcopy(command)))
        if self.closed:
            raise AssertionError('host called a closed driver')
        if not self.repeat or not self.sequence:
            self.sequence += 1
        packet = {'sessionId': 'fixture-session', 'controlEpoch': 1, 'snapshotId': self.sequence,
                  'url': '', 'title': 'Fixture device', 'viewport': {'width': 320, 'height': 240},
                  'cursor': {'x': 0, 'y': 0}, 'truncated': {'text': False, 'targets': False},
                  'snapshot': 'Button Go', 'targets': [{'ref': 'e1', 'tag': 'button', 'text': 'Go',
                    'bbox': {'x': 10, 'y': 20, 'width': 40, 'height': 30}}]}
        if self.malformed:
            packet['viewport']['width'] = True
        if command.get('image', True):
            def chunk(kind, data):
                return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
            raw = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
                   + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))
            packet['_images'] = prepare_computer_image(raw, 'image/png', checkpoint=checkpoint)
        return packet
    def close(self):
        self.close_count += 1
        self.calls.append(('close', {}))
        if self.fail_close_once and self.close_count == 1:
            raise RuntimeError('remote operation retirement is not confirmed')
        self.closed = True


def registered(*, malformed=False, repeat=False, fail_close_once=False, actions=ACTIONS):
    registry = Registry()
    calls, drivers = [], []
    def factory(context):
        context.checkpoint()
        calls.append(('open', {}))
        driver = Driver(calls, malformed=malformed, repeat=repeat, fail_close_once=fail_close_once)
        drivers.append(driver)
        return driver
    for name in ('phone', 'alias'):
        registry.register_computer(ComputerSpec(name, factory, profile='fixture/mobile',
            resource_id='physical:fixture-phone', actions=actions))
    return registry, calls, drivers


class RegisteredComputerIntegrationTests(unittest.TestCase):
    def app(self, registry=None):
        settings = Settings(':memory:')
        # Registered devices do not depend on the browser plugin or browser flag.
        settings.update({'runtime': {'browserEnabled': False}}, 1)
        app = Framework(registry=registry, settings=settings)
        self.addCleanup(app.close)
        return app
    def run_source(self, app, source, *, capabilities=('computer.control',), mode='source'):
        project = app.create_project('p', source=source, capabilities=list(capabilities))
        return app.start('p', project['version'], None, mode=mode)

    def test_cold_attach_task_never_opens_or_journals_without_await(self):
        registry, calls, drivers = registered()
        app = self.app(registry)
        result = self.run_source(app, 'let pending=computer_attach("phone");42').result(5)
        self.assertEqual(result, 42)
        self.assertEqual(calls, [])
        self.assertEqual(app.computer_history('p')['items'], [])

    def test_computer_permission_is_separate_from_browser_permission(self):
        for capabilities in ((), ('browser.control',)):
            registry, calls, drivers = registered()
            app = self.app(registry)
            run = self.run_source(app, 'await computer_attach("phone")', capabilities=capabilities)
            self.assertEqual(run.wait(5)['status'], 'failed')
            self.assertEqual(calls, [])
            self.assertEqual(app.computer_history('p')['items'], [])
        registry, calls, drivers = registered()
        app = self.app(registry)
        result = self.run_source(app, 'let c=await computer_attach("phone");computer_info(c).backend').result(5)
        self.assertEqual(result, 'registered')
        self.assertEqual([row[0] for row in calls], ['open', 'close'])

    def test_capability_inventory_is_cold_redacted_and_not_ordinary_model_tool(self):
        registry, calls, drivers = registered()
        app = self.app(registry)
        inventory = app.capabilities()
        descriptor = inventory['tools']['computer.control']
        self.assertEqual(descriptor['scope'], 'language')
        self.assertFalse(descriptor['callable'])
        self.assertNotIn('computer.control', DEFAULT_CAPABILITIES)
        self.assertIn('computer.control', language_permissions())
        self.assertEqual(inventory['computers']['phone']['actions'], list(ACTIONS))
        self.assertNotIn('physical:fixture-phone', json.dumps(inventory))
        inventory['computers']['phone']['actions'].clear()
        self.assertEqual(app.capabilities()['computers']['phone']['actions'], list(ACTIONS))
        self.assertEqual(calls, [])

    def test_native_ir_roundtrip_source_and_graph_have_same_effects_and_close(self):
        for version in (1, 2):
            ir = parse_program(SOURCE, language_version=version)
            formatted = format_program(ir)
            self.assertEqual(plain(decode_program(ir)), plain(Parser(formatted, legacy_items=version == 1).program()))
            self.assertEqual(parse_program(formatted, language_version=version)['semanticHash'], ir['semanticHash'])
            for mode in ('source', 'graph'):
                registry, calls, drivers = registered()
                app = self.app(registry)
                self.assertEqual(self.run_source(app, formatted, mode=mode).result(5), ['completed', True, 'registered'])
                self.assertEqual(drivers[0].close_count, 1)
                self.assertEqual([row[1]['action'] for row in calls if row[0] == 'driver'], ['observe', 'click'])
                rows = app.computer_history('p')['items']
                self.assertEqual([row['kind'] for row in rows], ['lifecycle', 'observation', 'action', 'observation', 'lifecycle'])
                self.assertTrue(all(row['sourceVersion'] == 1 and row['location'] for row in rows))
                self.assertEqual([row['status'] for row in rows if row['kind'] == 'action'], ['completed'])

    def test_physical_alias_stays_exclusive_until_confirmed_close(self):
        registry, calls, drivers = registered()
        app = self.app(registry)
        source = '''let c=await computer_attach("phone");
let denied=try{await computer_attach("alias")}catch e{e.code};
await computer_close(c);let second=await computer_attach("alias");
[denied,computer_info(second).binding]'''
        self.assertEqual(self.run_source(app, source).result(5), ['computer_busy', 'alias'])
        self.assertEqual([row[0] for row in calls], ['open', 'close', 'open', 'close'])

    def test_failed_retirement_keeps_alias_quarantined_until_retry(self):
        registry, calls, drivers = registered(fail_close_once=True)
        app = self.app(registry)
        source = '''let c=await computer_attach("phone");
let first=try{await computer_close(c);false}catch e{true};
let blocked=try{await computer_attach("alias")}catch e{e.code};
await computer_close(c);[first,blocked,computer_info(c).status]'''
        self.assertEqual(self.run_source(app, source).result(5), [True, 'computer_busy', 'closed'])
        self.assertEqual(len(drivers), 1)
        self.assertEqual(drivers[0].close_count, 2)

    def test_malformed_driver_packet_never_becomes_an_observation(self):
        registry, calls, drivers = registered(malformed=True)
        app = self.app(registry)
        run = self.run_source(app, 'let c=await computer_attach("phone");await computer_observe(c,false)')
        self.assertEqual(run.wait(5)['status'], 'failed')
        rows = app.computer_history('p')['items']
        self.assertEqual(sum(row['kind'] == 'observation' and row['status'] == 'completed' for row in rows), 0)
        self.assertEqual(drivers[0].close_count, 1)

    def test_repeated_snapshot_cannot_refresh_current_observation(self):
        registry, calls, drivers = registered(repeat=True)
        app = self.app(registry)
        run = self.run_source(app, 'let c=await computer_attach("phone");await computer_observe(c,false);await computer_observe(c,false)')
        self.assertEqual(run.wait(5)['status'], 'failed')
        rows = app.computer_history('p')['items']
        self.assertEqual(sum(row['kind'] == 'observation' and row['status'] == 'completed' for row in rows), 1)
        self.assertEqual(drivers[0].close_count, 1)

    def test_binding_supported_actions_are_checked_before_admission_or_driver_call(self):
        registry, calls, drivers = registered(actions=('click',))
        app = self.app(registry)
        source = '''let c=await computer_attach("phone");let o=await computer_observe(c,false);
let code=try{computer_action(o,{action:"input",ref:"e1",text:"append"})}catch e{e.code};
let after=await c(computer_action(o,{action:"click",ref:"e1"}));
[code,observation_info(after).receipt.status]'''
        self.assertEqual(self.run_source(app, source).result(5), ['computer_error', 'completed'])
        self.assertEqual([row[1]['action'] for row in calls if row[0] == 'driver'], ['observe', 'click'])
        rows = app.computer_history('p')['items']
        self.assertEqual(len([row for row in rows if row['kind'] == 'action']), 1)

    def test_stale_action_does_not_consume_or_dispatch_and_close_is_idempotent(self):
        registry, calls, drivers = registered()
        app = self.app(registry)
        source = '''let c=await computer_attach("phone");let o=await computer_observe(c,false);
let action=computer_action(o,{action:"click",ref:"e1"});await computer_observe(c,false);
let code=try{await c(action)}catch e{e.code};await computer_close(c);await computer_close(c);
[code,action_info(action).used,computer_info(c).status]'''
        self.assertEqual(self.run_source(app, source).result(5), ['computer_error', False, 'closed'])
        self.assertEqual([row[1]['action'] for row in calls if row[0] == 'driver'], ['observe', 'observe'])
        self.assertEqual(drivers[0].close_count, 1)

    def test_registered_effect_is_delegated_through_existing_function_agent_tool(self):
        for mode in ('source', 'graph'):
            registry, calls, drivers = registered()
            def choose(request, context):
                context.checkpoint()
                return ToolCall('operate', None) if request.step == 1 else Final(request.history[-1]['output'])
            registry.register_provider(ProviderSpec('fixture.operate', choose))
            app = self.app(registry)
            source = '''agent actor{provider:"fixture.operate",tools:[]};
let equipped=agent_bind(actor,[agent_tool("operate",async fn(x){
 async with await computer_attach("phone") as c {
  let o=await computer_observe(c,false);
  observation_info(await c(computer_action(o,{action:"click",ref:"e1"}))).receipt.status
 }
},capabilities=["computer.control"])]);await equipped(null)'''
            self.assertEqual(self.run_source(app, source, mode=mode).result(5), 'completed')
            self.assertEqual([row[1]['action'] for row in calls if row[0] == 'driver'], ['observe', 'click'])
            self.assertEqual(drivers[0].close_count, 1)


class RegisteredComputerConfigTests(unittest.TestCase):
    def load(self, document, registry=None, raw=False):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'computers.json'
            path.write_text(document if raw else json.dumps(document))
            return load_computer_registry(path, registry)

    def test_config_registration_is_cold_redacted_and_atomic(self):
        row = {'name': 'phone', 'endpoint': 'http://127.0.0.1:3000/mcp', 'device': 'private-device', 'token_env': 'PRIVATE_TOKEN'}
        with patch('chaos.mobile_mcp.MCPHttpClient', side_effect=AssertionError('configuration must remain cold')):
            registry = self.load({'computers': [row]})
        metadata = registry.computer_snapshot()['phone'].describe()
        self.assertEqual(metadata['actions'], list(ACTIONS))
        for private in ('127.0.0.1', 'private-device', 'PRIVATE_TOKEN'):
            self.assertNotIn(private, json.dumps(metadata))
        for rows in ([row, {**row, 'name': 'other', 'unknown': True}], [row, dict(row)]):
            target = Registry()
            with self.assertRaises(ValueError):
                self.load({'computers': rows}, target)
            self.assertEqual(dict(target.computer_snapshot()), {})

    def test_duplicate_json_fields_and_unknown_transport_cannot_register(self):
        for document in ('{"computers":[],"computers":[]}',
                         '{"computers":[{"name":"phone","name":"alias","endpoint":"http://127.0.0.1/mcp","device":"d"}]}'):
            with self.assertRaises(ValueError):
                self.load(document, raw=True)
        with self.assertRaises(ValueError):
            self.load({'computers': [{'name': 'p', 'endpoint': 'http://127.0.0.1/mcp', 'device': 'd', 'transport': 'shell'}]})

    def test_registry_snapshot_is_immutable_and_detached_from_later_registration(self):
        registry, calls, drivers = registered()
        snapshot = registry.computer_snapshot()
        with self.assertRaises(TypeError):
            snapshot['third'] = None
        with self.assertRaises(ValueError):
            registry.register_computer(snapshot['phone'])
        registry.register_computer(ComputerSpec('third', lambda ctx: Driver([]), actions=('click',)))
        self.assertEqual(set(snapshot), {'phone', 'alias'})
        self.assertEqual(set(registry.computer_snapshot()), {'phone', 'alias', 'third'})
        self.assertEqual(calls, [])


class ValueHost:
    def __init__(self):
        self.calls = []
        self.sequence = 0
    def handle(self, operation, payload):
        self.calls.append(operation)
        metadata = {'id': 'fixture-computer', 'backend': 'registered', 'binding': 'phone',
                    'profile': 'fixture/mobile', 'status': 'open', 'actions': list(ACTIONS)}
        if operation == 'attach':
            return metadata
        if operation == 'close':
            return {**metadata, 'status': 'closed'}
        self.sequence += 1
        return {**metadata, 'id': 'observation-' + str(self.sequence), 'runId': 'run-1', 'sourceVersion': 1,
                'computerId': metadata['id'], 'sessionId': 'session-1', 'controlEpoch': 1,
                'snapshotId': self.sequence, 'targets': [{'ref': 'e1', 'tag': 'button', 'text': 'Go'}],
                'image': None, 'text': 'Go', 'receipt': None}
    def evaluator(self):
        evaluator = Evaluator(computer_handler=self.handle)
        evaluator.env.bind('limited', Native('limited', lambda f: evaluator.invoke(f, []), evaluator, capabilities=frozenset()))
        evaluator.env.bind('browser_scope', Native('browser_scope', lambda f: evaluator.invoke(f, []), evaluator, capabilities=frozenset({'browser.control'})))
        evaluator.env.bind('device_scope', Native('device_scope', lambda f: evaluator.invoke(f, []), evaluator, capabilities=frozenset({'computer.control'})))
        return evaluator


class RegisteredComputerAuthorityTests(unittest.TestCase):
    def test_narrow_alias_keeps_computer_permission_and_one_shared_admission(self):
        host = ValueHost()
        result = host.evaluator().run('''let c=await computer_attach("phone");let o=await computer_observe(c,false);
let action=computer_action(o,{action:"click",ref:"e1"});let denied=limited(fn(){c});
let code=try{await denied(action)}catch e{e.code};let unused=action_info(action).used;
let allowed=device_scope(fn(){c});await allowed(action);
[code,unused,action_info(action).used,c,allowed]''')
        self.assertEqual(result[:3], ['capability_error', False, True])
        self.assertIs(result[3].lease, result[4].lease)
        self.assertEqual(result[3].lease.required_capability, 'computer.control')
        self.assertEqual(host.calls, ['attach', 'observe', 'act'])

    def test_browser_scope_does_not_widen_registered_alias_or_cold_request(self):
        for expression in ('await computer_observe(browser_scope(fn(){c}),false)',
                           'let pending=browser_scope(fn(){computer_observe(c,false)});await pending'):
            host = ValueHost()
            result = host.evaluator().run('let c=await computer_attach("phone");try{' + expression + '}catch e{e.code}')
            self.assertEqual(result, 'capability_error')
            self.assertEqual(host.calls, ['attach'])

    def test_narrow_observation_cannot_mint_wider_registered_action(self):
        host = ValueHost()
        result = host.evaluator().run('''let c=await computer_attach("phone");let o=await computer_observe(c,false);
let narrow=limited(fn(){o});let action=computer_action(narrow,{action:"click",ref:"e1"});
[try{await c(action)}catch e{e.code},action_info(action).used]''')
        self.assertEqual(result, ['capability_error', False])
        self.assertEqual(host.calls, ['attach', 'observe'])
