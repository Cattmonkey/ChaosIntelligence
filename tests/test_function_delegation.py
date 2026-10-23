"""Explicit language callbacks delegate effects through the original AST host.

Only deterministic registered adapters and fake model clients are used.  These
tests exercise actual effects, not a new controller or a capability mock.
"""
import json
import threading
import time
import unittest
from unittest.mock import patch

from chaos import (DeviceSpec, Final, Framework, ProviderSpec, Registry,
                   RobotSpec, Settings, ToolCall)
from tests.test_device_surfaces import Adapter as DeviceAdapter
from tests.test_harness_runtime import FakeClient, call, message
from tests.test_robot_surfaces import ACTUATORS, Adapter as RobotAdapter


PROCESS = {'kind': 'process', 'module': 'worker.chaos', 'export': 'step',
           'intervalSeconds': None, 'maxTurns': None, 'name': 'worker'}
FILES = {'worker.chaos': 'fn step(ctx){process_step(ctx.state)};export{step};'}


def choose_registry():
    registry = Registry()
    requests = []
    def choose(request, context):
        context.checkpoint()
        requests.append(request)
        return ToolCall('work', request.input) if request.step == 1 else Final(request.history[-1]['output'])
    registry.register_provider(ProviderSpec('fixture.delegate', choose))
    return registry, requests


def binding(handler, capabilities, *, provider='fixture.delegate', host_tools=()):
    return ('agent actor{provider:' + json.dumps(provider) + ',tools:' + json.dumps(list(host_tools)) + '};'
            'let equipped=agent_bind(actor,[agent_tool("work",' + handler + ',capabilities=' + json.dumps(list(capabilities)) + ')]);')


def register_device(registry):
    adapters = []
    def factory(context):
        context.checkpoint()
        value = DeviceAdapter()
        adapters.append(value)
        return value
    registry.register_device(DeviceSpec('desk', factory))
    return adapters


def register_robot(registry):
    adapters = []
    def factory(context):
        context.checkpoint()
        value = RobotAdapter()
        adapters.append(value)
        return value
    registry.register_robot(RobotSpec('mobile', factory, ACTUATORS,
                                    profile='fixture/delegation', resource_id='/private/fixture/mobile'))
    return adapters


DEVICE_HANDLER = ('async fn(x){async with await device_open("desk") as device{'
                  'let action=device_action(device,{action:"mouse_move",x:2,y:-3});'
                  'device_receipt_info(await device(action))}}')
ROBOT_HANDLER = ('async fn(x){async with await robot_open("mobile") as robot{'
                 'let observed=await robot_observe(robot);'
                 'let receipt=await robot(robot_command(observed,{left:0.2,right:0.4}));'
                 'robot_receipt_info(receipt)}}')


class FunctionDelegationTests(unittest.TestCase):
    def app(self, *, registry=None, responses=None):
        settings = Settings(':memory:')
        settings.update({'providers': {'text': {'baseURL': 'https://fixture.invalid/v1',
                                                'apiKey': 'fixture-only'}}}, 1)
        client = None
        if responses is not None:
            client = FakeClient(responses)
            guard = patch.object(settings, 'client', return_value=client)
            guard.start()
            self.addCleanup(guard.stop)
        app = Framework(registry=registry, settings=settings)
        self.addCleanup(app.close)
        return app, client

    def start(self, app, source, capabilities, *, mode='source', value=None, files=None):
        project = app.create_project('p', source=source, capabilities=list(capabilities), files=files)
        return app.start('p', project['version'], value, mode=mode)

    def test_registered_callback_starts_actual_process_source_and_graph(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode):
                registry, requests = choose_registry()
                app, _ = self.app(registry=registry)
                app._engine.pause_dispatch(True)
                handler = 'async fn(x){await process_start("worker",process_from(' + json.dumps(PROCESS) + '),{count:0})}'
                run = self.start(app, binding(handler, ['process.manage']) + 'await equipped(null)',
                                 ['process.manage'], mode=mode, files=FILES)
                result = run.result(8)
                self.assertEqual(result['key'], 'worker')
                self.assertEqual(app.get_process('p', 'worker')['state'], {'count': 0})
                self.assertEqual(set(requests[0].tools), {'work'})
                self.assertNotIn('process.manage', requests[0].tools)
                self.assertTrue(any(row['type'] == 'process.created' for row in app.events('p', limit=200)['items']))

    def test_native_model_callback_drives_device_without_exposing_permission(self):
        registry = Registry()
        adapters = register_device(registry)
        app, client = self.app(registry=registry, responses=[message(calls=[call('tool_0', '{"input":null}')]), message('done')])
        run = self.start(app, binding(DEVICE_HANDLER, ['device.control'], provider='model') + 'await equipped(null)',
                         ['device.control'])
        self.assertEqual(run.result(8), 'done')
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].actions, [{'action': 'mouse_move', 'x': 2, 'y': -3, 'wheel': 0}])
        self.assertTrue(adapters[0].closed)
        self.assertEqual(len(client.requests[0]['tools']), 1)
        receipt = json.loads(client.requests[1]['messages'][-1]['content'])
        self.assertEqual(receipt['status'], 'acknowledged')
        self.assertFalse(receipt['targetVerified'])

    def test_registered_callback_drives_robot_source_and_graph(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode):
                registry, requests = choose_registry()
                adapters = register_robot(registry)
                app, _ = self.app(registry=registry)
                result = self.start(app, binding(ROBOT_HANDLER, ['robot.control']) + 'await equipped(null)',
                                    ['robot.control'], mode=mode).result(8)
                self.assertEqual(result['status'], 'sent')
                self.assertFalse(result['targetVerified'])
                self.assertEqual(adapters[0].actions[0]['channels'], {'left': 0.2, 'right': 0.4})
                self.assertTrue(adapters[0].closed)
                self.assertEqual(set(requests[0].tools), {'work'})

    def test_registered_callback_opens_actual_session_without_model_request(self):
        registry, requests = choose_registry()
        app, client = self.app(registry=registry, responses=[])
        handler = 'async fn(x){agent inner{provider:"model",tools:[]};let opened=await session_open("inner",inner);session_info(opened)}'
        result = self.start(app, binding(handler, ['session.manage']) + 'await equipped(null)',
                            ['session.manage']).result(8)
        self.assertEqual(result['key'], 'inner')
        self.assertEqual(app.agent_session('p', 'inner')['turn'], 0)
        self.assertEqual(client.requests, [])
        self.assertEqual(set(requests[0].tools), {'work'})

    def test_registered_callback_adopts_tested_program_and_future_run_uses_it(self):
        registry, _ = choose_registry()
        app, _ = self.app(registry=registry)
        candidate = {'kind': 'program', 'entry': 'main.chaos',
                     'files': {'main.chaos': '99'}, 'languageVersions': {'main.chaos': 2}}
        handler = ('async fn(x){let base=current_program();let candidate=program_from(' + json.dumps(candidate) + ');'
                   'await program_adopt(base,candidate,[{entry:"main.chaos",input:null,expect:99}],reason="explicit callback adoption")}')
        source = binding(handler, ['program.adopt']) + 'await equipped(null)'
        run = self.start(app, source, ['program.adopt'])
        receipt = run.result(8)
        self.assertEqual(receipt['version'], 2)
        self.assertEqual(app.get_project('p')['source'], '99')
        self.assertEqual(app.start('p', 2, None, mode='source').result(8), 99)
        self.assertEqual(run.get()['snapshot']['source'], source)

    def test_missing_project_permission_rejects_before_provider_or_adapter(self):
        registry, requests = choose_registry()
        adapters = register_device(registry)
        app, _ = self.app(registry=registry)
        run = self.start(app, binding(DEVICE_HANDLER, ['device.control']) + 'await equipped(null)', [])
        with self.assertRaises(Exception):
            run.result(8)
        self.assertEqual(requests, [])
        self.assertEqual(adapters, [])

    def test_undeclared_callback_permission_never_borrows_project_authority(self):
        registry, requests = choose_registry()
        adapters = register_device(registry)
        app, _ = self.app(registry=registry)
        run = self.start(app, binding(DEVICE_HANDLER, []) + 'await equipped(null)', ['device.control'])
        with self.assertRaises(Exception):
            run.result(8)
        self.assertEqual(len(requests), 1)
        self.assertEqual(adapters, [])

    def test_reserved_permissions_remain_rejected_as_direct_agent_tools(self):
        for permission in ('device.control', 'robot.control', 'process.manage', 'session.manage', 'program.adopt'):
            with self.subTest(permission=permission):
                app, client = self.app(responses=[])
                source = 'agent actor{provider:"model",tools:[' + json.dumps(permission) + ']};actor(null)'
                with self.assertRaises(Exception):
                    self.start(app, source, [permission]).result(8)
                self.assertEqual(client.requests, [])

    def test_ordinary_host_capability_still_requires_agent_tools_declaration(self):
        registry, requests = choose_registry()
        app, _ = self.app(registry=registry)
        source = binding('fn(x){tool("memory.put",{key:"secret",value:x})}', ['memory.put']) + 'await equipped(42)'
        with self.assertRaises(Exception):
            self.start(app, source, ['memory.put']).result(8)
        self.assertEqual(requests, [])
        self.assertIsNone(app._store.memory_get('p', 'secret'))

    def test_team_narrowing_prevents_callback_effect_from_wide_binding(self):
        registry, _ = choose_registry()
        adapters = register_device(registry)
        app, _ = self.app(registry=registry)
        team = {'kind': 'team', 'name': 'narrow', 'entry': 'worker', 'roles': [
            {'id': 'worker', 'parent': None, 'capabilities': [],
             'agent': {'kind': 'agent', 'name': 'fallback', 'config': {'provider': 'local', 'tools': []}}}]}
        source = binding(DEVICE_HANDLER, ['device.control']) + 'let narrow=team_bind(team_from(' + json.dumps(team) + '),{worker:equipped});await narrow(null)'
        with self.assertRaises(Exception):
            self.start(app, source, ['device.control']).result(8)
        self.assertEqual(adapters, [])

    def test_callback_alias_and_cold_task_do_not_restore_permission_after_narrowing(self):
        registry, _ = choose_registry()
        adapters = register_device(registry)
        app, _ = self.app(registry=registry)
        team = {'kind': 'team', 'name': 'narrow', 'entry': 'worker', 'roles': [
            {'id': 'worker', 'parent': None, 'capabilities': [],
             'agent': {'kind': 'agent', 'name': 'fallback', 'config': {'provider': 'local', 'tools': []}}}]}
        source = binding(DEVICE_HANDLER, ['device.control']) + ('let pending=equipped(null);'
            'let narrow=team_bind(team_from(' + json.dumps(team) + '),{worker:fn(x){pending}});'
            'let blocked=try{await narrow(null);"escaped"}catch e{e.code};'
            '[blocked,task_info(pending).state]')
        result = self.start(app, source, ['device.control']).result(8)
        self.assertNotEqual(result[0], 'escaped')
        self.assertEqual(result[1], 'created')
        self.assertEqual(adapters, [])

    def test_bot_completed_callback_prefix_is_not_replayed_after_guidance(self):
        registry, _ = choose_registry()
        adapters = register_device(registry)
        app, _ = self.app(registry=registry)
        handler = ('async fn(x){async with await device_open("desk") as d{'
                   'await d(device_action(d,{action:"mouse_move",x:1,y:0}))};'
                   'await progress_emit("progress",{marker:"delegated-prefix"});await sleep(0.3);'
                   'async with await device_open("desk") as d{await d(device_action(d,{action:"mouse_move",x:9,y:0}))};42}')
        source = binding(handler, ['device.control']) + ('let worker=bot("guided",fn(s){null},'
            'async fn(p,s){await equipped(p)},fn(s){{done:true,context:null,output:s.evidence}});await worker()')
        run = self.start(app, source, ['device.control'])
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            events = app.events('p', run_id=run.id, limit=300)['items']
            if any(row['type'] == 'bot.progress' and row['payload'].get('data', {}).get('marker') == 'delegated-prefix' for row in events):
                break
            time.sleep(0.005)
        else:
            self.fail('actual delegated callback prefix marker was absent')
        run.guide({'text': 'change the remaining work'}, message_id='change')
        result = run.result(8)
        self.assertEqual(result['status'], 'needs_reconciliation')
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].actions, [{'action': 'mouse_move', 'x': 1, 'y': 0, 'wheel': 0}])
        events = app.events('p', run_id=run.id, limit=300)['items']
        self.assertTrue(any(row['type'] == 'bot.action.admitted' and row['payload']['kind'] == 'device' for row in events))

    def test_bot_guidance_before_delegated_effect_never_opens_device(self):
        registry = Registry()
        entered, release = threading.Event(), threading.Event()
        def choose(request, context):
            context.checkpoint()
            if request.step == 1:
                entered.set()
                while not release.wait(0.01):
                    context.checkpoint()
                return ToolCall('work', None)
            return Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('fixture.delegate', choose))
        adapters = register_device(registry)
        app, _ = self.app(registry=registry)
        source = binding(DEVICE_HANDLER, ['device.control']) + ('let worker=bot("guided",fn(s){null},'
            'async fn(p,s){await equipped(p)},fn(s){{done:true,context:null,output:s.evidence}});await worker()')
        run = self.start(app, source, ['device.control'])
        try:
            self.assertTrue(entered.wait(3))
            run.guide('new direction', message_id='new')
            release.set()
            self.assertEqual(run.result(8)['status'], 'needs_reconciliation')
        finally:
            release.set()
        self.assertEqual(adapters, [])


if __name__ == '__main__':
    unittest.main()
