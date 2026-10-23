"""Actual native model calls share host contracts before any batch action."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Registry, Settings, ToolSpec
from chaos.server import make_server
from chaos.session_protocol import tool_alias
from tests.test_harness_runtime import FakeClient, call, message


KINDS = ('agent', 'session', 'bound_agent', 'bound_session')
STARTED = {'tool.started', 'agent.tool_started', 'agent.language_tool', 'session.language_tool'}


def setup(kind, *, callback_caps='["write"]', extra=False):
    code = 'agent worker{provider:"model",tools:["write"]};'
    target = 'worker'
    if 'session' in kind:
        code += 'let chat=await session_open("chat",worker);'
        target = 'chat'
    if kind.startswith('bound'):
        tools = '[agent_tool("zwork",fn(x){tool("write",x);x*2},input_type="Int",capabilities=' + callback_caps + ')'
        if extra:
            tools += ',agent_tool("alpha",fn(x){x},input_type="Int")'
        tools += ']'
        code += 'let bound=' + ('session_bind(chat,' if 'session' in kind else 'agent_bind(worker,') + tools + ');'
        target = 'bound'
    return code, target


def invoke(kind, *, target=None, message_id='once'):
    target = target or ('bound' if kind.startswith('bound') else 'chat' if 'session' in kind else 'worker')
    return ('' if kind == 'agent' else 'await ') + target + '(7' + (',"' + message_id + '"' if 'session' in kind else '') + ')'


def alias(kind, name=None):
    name = name or ('zwork' if kind.startswith('bound') else 'write')
    return tool_alias(name) if 'session' in kind else 'tool_1' if name == 'zwork' else 'tool_0'


def batch(kind, values):
    return message(calls=[call(alias(kind), json.dumps({'input': value}), 'call-' + str(i))
                          for i, value in enumerate(values)])


class ToolDeclarationSurfaces(unittest.TestCase):
    def app(self, responses, *, input_type='Int'):
        effects = []
        registry = Registry()
        registry.register_tool(ToolSpec('write', lambda value, ctx: effects.append(value) or value,
                                       effect='write', input_type=input_type, output_type=input_type))
        settings = Settings(':memory:')
        settings.update({'providers': {'text': {'baseURL': 'https://fixture.invalid/v1', 'apiKey': 'fixture-only'}},
                         'roles': {'executor': {'model': 'fixture', 'families': [], 'autoUpgrade': False}}}, 1)
        client = FakeClient(responses)
        guard = patch.object(settings, 'client', return_value=client)
        guard.start(); self.addCleanup(guard.stop)
        app = Framework(registry=registry, settings=settings)
        self.addCleanup(app.close)
        return app, client, effects

    def start(self, app, code, *, mode='source'):
        project = app.create_project('contracts', source=code, capabilities=['write', 'session.manage'])
        return app.start(project['id'], project['version'], None, mode=mode)

    def test_all_four_native_entries_reject_late_int_before_any_action(self):
        for kind in KINDS:
            for bad in (True, 42.0):
                with self.subTest(kind=kind, bad=bad):
                    app, client, effects = self.app([batch(kind, [1, bad])])
                    prefix, target = setup(kind)
                    run = self.start(app, prefix + invoke(kind, target=target))
                    with self.assertRaises(Exception): run.result(6)
                    self.assertEqual(effects, [])
                    self.assertEqual(len(client.requests), 1)
                    events = app.events('contracts', run_id=run.id, limit=300)['items']
                    self.assertFalse(STARTED.intersection(row['type'] for row in events))
                    self.assertTrue(client.closed)
                    if 'session' in kind:
                        history = app.agent_session_history('contracts', 'chat')['items']
                        self.assertEqual([row['message']['role'] for row in history], ['user'])
                        self.assertEqual(app.agent_session('contracts', 'chat')['status'], 'needs_reconciliation')

    def test_all_four_native_entries_execute_valid_batch_and_keep_wire_contract(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                app, client, effects = self.app([batch(kind, [1, 2]), message('done')])
                prefix, target = setup(kind)
                result = self.start(app, prefix + invoke(kind, target=target)).result(6)
                self.assertEqual(result['output'] if 'session' in kind else result, 'done')
                self.assertEqual(effects, [1, 2])
                self.assertEqual(len(client.requests), 2)
                declarations = client.requests[0]['tools']
                names = ['write', 'zwork'] if kind.startswith('bound') else ['write']
                self.assertEqual([d['function']['description'].split(':', 1)[0] for d in declarations], names)
                for index, declaration in enumerate(declarations):
                    fn = declaration['function']
                    self.assertEqual(fn['name'], tool_alias(names[index]) if 'session' in kind else 'tool_' + str(index))
                    self.assertEqual(fn['parameters'], {'type': 'object', 'properties': {'input': {'type': 'integer'}},
                                                       'required': ['input'], 'additionalProperties': False})
                events = app.events('contracts', limit=300)['items']
                starts = [e for e in events if e['type'] == 'agent.tool_started']
                self.assertEqual([e['payload']['category'] for e in starts], ['tools', 'tools'])
                self.assertTrue(client.closed)

    def test_agent_callback_insertion_order_and_session_canonical_order_are_preserved(self):
        for kind, expected in [('bound_agent', ['write', 'zwork', 'alpha']),
                               ('bound_session', ['alpha', 'write', 'zwork'])]:
            with self.subTest(kind=kind):
                app, client, effects = self.app([message('no tools needed')])
                prefix, target = setup(kind, extra=True)
                result = self.start(app, prefix + invoke(kind, target=target)).result(6)
                self.assertEqual(result['output'] if 'session' in kind else result, 'no tools needed')
                fns = [d['function'] for d in client.requests[0]['tools']]
                self.assertEqual([f['description'].split(':', 1)[0] for f in fns], expected)
                self.assertEqual([f['name'] for f in fns], [tool_alias(n) for n in expected] if 'session' in kind
                                 else ['tool_' + str(i) for i in range(3)])
                self.assertEqual(effects, [])

    def test_bound_session_same_id_replays_without_another_model_or_callback(self):
        kind = 'bound_session'
        app, client, effects = self.app([batch(kind, [21]), message('done')])
        prefix, target = setup(kind)
        body = 'let one=' + invoke(kind) + ';let two=' + invoke(kind) + ';[one,two]'
        one, two = self.start(app, prefix + body).result(6)
        self.assertEqual((one['output'], two['output']), ('done', 'done'))
        self.assertFalse(one['replayed']); self.assertTrue(two['replayed'])
        self.assertEqual(effects, [21]); self.assertEqual(len(client.requests), 2)
        history = app.agent_session_history('contracts', 'chat')['items']
        self.assertEqual([row['message']['role'] for row in history], ['user', 'assistant', 'tool', 'assistant'])
        self.assertEqual(history[2]['message']['content'], 42)

    def test_callback_declaration_never_grants_host_authority(self):
        for kind in ('bound_agent', 'bound_session'):
            with self.subTest(kind=kind):
                app, client, effects = self.app([batch(kind, [21]), message('unused')])
                prefix, target = setup(kind, callback_caps='[]')
                with self.assertRaises(Exception): self.start(app, prefix + invoke(kind)).result(6)
                self.assertEqual(effects, [])
                self.assertTrue(client.closed)

    def test_number_contract_also_rejects_bool_before_earlier_write(self):
        app, client, effects = self.app([batch('agent', [1.5, True])], input_type='Number')
        prefix, target = setup('agent')
        run = self.start(app, prefix + invoke('agent'))
        with self.assertRaises(Exception): run.result(6)
        self.assertEqual(effects, [])
        self.assertFalse(STARTED.intersection(e['type'] for e in app.events('contracts', limit=300)['items']))
        schema = client.requests[0]['tools'][0]['function']['parameters']['properties']['input']
        self.assertEqual(schema, {'type': 'number'})

    def test_native_agent_inside_real_bot_runs_through_graph_pipeline(self):
        app, client, effects = self.app([batch('agent', [42]), message('done')])
        prefix, target = setup('agent')
        source = prefix + '''let work=bot("contract",fn(s){s.input},async fn(p,s){worker(p)},
            fn(s){{done:true,context:null,output:s.evidence}});let r=await work(7);[r.status,r.done,r.output]'''
        self.assertEqual(self.start(app, source, mode='graph').result(6), ['achieved', True, ['done']])
        self.assertEqual(effects, [42]); self.assertEqual(len(client.requests), 2)
        events = app.events('contracts', limit=300)['items']
        self.assertTrue(any(e['type'] == 'agent.tool_completed' for e in events))

    def test_http_sdk_source_uses_same_real_native_preflight_and_success(self):
        app, fake, effects = self.app([batch('agent', [1, 42.0]), batch('agent', [21]), message('done')])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            server = make_server('127.0.0.1', 0, framework=app, state_dir=root/'control', update_home=root/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                client = Client('http://127.0.0.1:' + str(server.server_port))
                prefix, target = setup('agent')
                project = client.create_project('http-contract', source=prefix + invoke('agent'), capabilities=['write'])
                first = client.start(project['id'], project['version'], None, mode='source')
                with self.assertRaises(Exception): first.result(timeout=6)
                self.assertEqual(effects, [])
                second = client.start(project['id'], project['version'], None, mode='source')
                self.assertEqual(second.result(timeout=6), 'done')
                self.assertEqual(effects, [21]); self.assertEqual(len(fake.requests), 3)
                self.assertEqual(client.get_project(project['id'])['version'], project['version'])
            finally:
                server.shutdown(); thread.join(3); server.server_close()


if __name__ == '__main__': unittest.main()
