"""Persisted tool contracts describe callbacks without persisting authority.

Sessions retain their original launch ceiling for direct host tools; explicit
language callbacks receive only the current live binding's actual authority.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chaos import Framework, Settings
from chaos.session_protocol import tool_alias
from tests.test_harness_runtime import FakeClient, call, message
from tests.test_function_delegation import FILES, PROCESS


def response(value, ident='callback'):
    return message(calls=[call(tool_alias('guide_worker'), json.dumps({'input': value}), ident)])


GUIDE_HANDLER = 'async fn(x){await process_guide("worker",x,message_id="guidance_"+input)}'


def source(handler=GUIDE_HANDLER, *, invoke='await equipped("direction",message_id=input)', caps=('process.manage', 'session.manage')):
    return ('agent actor{provider:"model",tools:[]};let retained=await session_open("chat",actor);'
            'let equipped=session_bind(retained,[agent_tool("guide_worker",' + handler + ',capabilities=' + json.dumps(list(caps)) + ')]);'
            + invoke)


class SessionFunctionDelegationTests(unittest.TestCase):
    def settings(self, responses):
        settings = Settings(':memory:')
        settings.update({'providers': {'text': {'baseURL': 'https://fixture.invalid/v1',
                                                'apiKey': 'fixture-only'}}}, 1)
        client = FakeClient(responses)
        guard = patch.object(settings, 'client', return_value=client)
        guard.start()
        self.addCleanup(guard.stop)
        return settings, client

    def project(self, app, code=None, capabilities=('session.manage', 'process.manage')):
        app._engine.pause_dispatch(True)
        app.create_project('p', source=source() if code is None else code,
                           files=FILES, capabilities=list(capabilities))
        row = app.create_process('p', 1, 'worker', PROCESS, state={'count': 0})
        app.control_process('p', 'worker', row['revision'], 'pause')

    def test_reserved_permissions_never_become_session_host_tools(self):
        for permission in ('device.control', 'robot.control', 'process.manage', 'session.manage', 'program.adopt'):
            with self.subTest(permission=permission):
                settings, client = self.settings([])
                with Framework(settings=settings) as app:
                    code = 'agent actor{provider:"model",tools:[' + json.dumps(permission) + ']};await session_open("chat",actor)'
                    with self.assertRaises(Exception):
                        project = app.create_project('p', source=code, capabilities=sorted({'session.manage', permission}))
                        app.start('p', project['version'], None, mode='source').result(8)
                    self.assertEqual(app._store.db.execute('SELECT count(*) FROM agent_sessions').fetchone()[0], 0)
                    self.assertEqual(client.requests, [])

    def test_legacy_stored_reserved_definition_is_rejected_before_new_turn(self):
        from chaos.agent_values import definition_hash
        for permission in ('device.control', 'robot.control'):
            with self.subTest(permission=permission):
                settings, client = self.settings([])
                with Framework(settings=settings) as app:
                    code = 'agent actor{provider:"model",tools:[]};await session_open("chat",actor)'
                    app.create_project('p', source=code, capabilities=['session.manage', permission])
                    app.start('p', 1, None, mode='source').result(8)
                    with app._store.write():
                        retained = app._store.sessions._read('p', 'chat')
                        retained['agent']['config']['tools'] = [permission]
                        retained['definitionHash'] = definition_hash(retained['agent'])
                        retained['launchCapabilities'] = sorted(['session.manage', permission])
                        row = app._store.sessions._write(retained, 'fixture.legacy.definition')
                    with self.assertRaises(Exception):
                        app.start_session_turn('p', 1, 'chat', row['revision'], None, message_id='new-turn').result(8)
                    self.assertEqual(app.agent_session('p', 'chat')['status'], 'idle')
                    self.assertEqual(app.agent_session('p', 'chat')['turn'], 0)
                    self.assertEqual(app._store.db.execute('SELECT count(*) FROM session_turns').fetchone()[0], 0)
                    self.assertEqual(client.requests, [])

    def test_live_callback_authority_crosses_session_reopen_without_expanding_launch(self):
        settings, client = self.settings([response('first'), message('first done'), response('second', 'second'), message('second done')])
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'state.sqlite3'
            with Framework(database, settings=settings) as app:
                self.project(app)
                first = app.start('p', 1, 'one', mode='source').result(8)
                self.assertEqual(first['output'], 'first done')
                row = app.agent_session('p', 'chat')
                self.assertEqual(row['launchCapabilities'], ['session.manage'])
                self.assertEqual(row['lastToolBinding']['tools'][0]['capabilities'], ['process.manage', 'session.manage'])
                self.assertEqual(app.get_process('p', 'worker')['guidanceRevision'], 1)
                first_binding = row['lastToolBinding']['bindingHash']
            with Framework(database, settings=settings) as app:
                app._engine.pause_dispatch(True)
                second = app.start('p', 1, 'two', mode='graph').result(8)
                self.assertEqual(second['output'], 'second done')
                row = app.agent_session('p', 'chat')
                self.assertEqual(row['turn'], 2)
                self.assertEqual(row['launchCapabilities'], ['session.manage'])
                self.assertEqual(row['lastToolBinding']['bindingHash'], first_binding)
                self.assertEqual(app.get_process('p', 'worker')['guidanceRevision'], 2)
                history = app.agent_session_history('p', 'chat')['items']
                outputs = [item['message']['content'] for item in history if item['message']['role'] == 'tool']
                self.assertEqual([item['messageId'] for item in outputs], ['guidance_one', 'guidance_two'])
                self.assertEqual([item['status'] for item in outputs], ['queued', 'queued'])
        self.assertEqual(len(client.requests), 4)
        self.assertTrue(all(len(item['tools']) == 1 for item in client.requests))

    def test_matching_completed_message_replays_without_another_callback_or_model(self):
        settings, client = self.settings([response('first'), message('done')])
        with Framework(settings=settings) as app:
            self.project(app)
            first = app.start('p', 1, 'one', mode='source').result(8)
            second = app.start('p', 1, 'one', mode='source').result(8)
            self.assertFalse(first['replayed'])
            self.assertTrue(second['replayed'])
            self.assertEqual(second['output'], first['output'])
            self.assertEqual(app.get_process('p', 'worker')['guidanceRevision'], 1)
            self.assertEqual(app.agent_session('p', 'chat')['turn'], 1)
        self.assertEqual(len(client.requests), 2)

    def test_revoked_project_capability_blocks_new_binding_before_claim_or_model(self):
        settings, client = self.settings([response('first'), message('done')])
        with Framework(settings=settings) as app:
            self.project(app)
            app.start('p', 1, 'one', mode='source').result(8)
            project = app.edit_program('p', 1, source=app.get_project('p')['source'], capabilities=['session.manage'])
            with self.assertRaises(Exception):
                app.start('p', project['version'], 'two', mode='source').result(8)
            self.assertEqual(app.agent_session('p', 'chat')['turn'], 1)
            self.assertEqual(app.get_process('p', 'worker')['guidanceRevision'], 1)
        self.assertEqual(len(client.requests), 2)

    def test_narrow_team_cannot_use_wide_live_session_callback_contract(self):
        settings, client = self.settings([])
        team = {'kind': 'team', 'name': 'narrow', 'entry': 'worker', 'roles': [
            {'id': 'worker', 'parent': None, 'capabilities': ['session.manage'],
             'agent': {'kind': 'agent', 'name': 'fallback', 'config': {'provider': 'local', 'tools': []}}}]}
        code = source(invoke='let narrow=team_bind(team_from(' + json.dumps(team) + '),{worker:equipped});await narrow("direction",message_id=input)')
        with Framework(settings=settings) as app:
            self.project(app, code)
            with self.assertRaises(Exception):
                app.start('p', 1, 'one', mode='source').result(8)
            self.assertEqual(app.agent_session('p', 'chat')['turn'], 0)
            self.assertNotIn('guidanceRevision', app.get_process('p', 'worker'))
        self.assertEqual(client.requests, [])

    def test_persisted_contract_does_not_make_callback_callable_in_unbound_sdk_turn(self):
        settings, client = self.settings([response('first'), message('bound done'), response('attempted direct', 'direct')])
        with Framework(settings=settings) as app:
            self.project(app)
            app.start('p', 1, 'one', mode='source').result(8)
            row = app.agent_session('p', 'chat')
            with self.assertRaises(Exception):
                app.start_session_turn('p', 1, 'chat', row['revision'], 'direct', message_id='direct').result(8)
            self.assertEqual(app.get_process('p', 'worker')['guidanceRevision'], 1)
            self.assertEqual(app.agent_session('p', 'chat')['status'], 'needs_reconciliation')
            self.assertEqual(client.requests[-1].get('tools', []), [])
        self.assertEqual(len(client.requests), 3)

    def test_changed_explicit_permission_contract_conflicts_for_same_message_id(self):
        settings, client = self.settings([response('first'), message('done')])
        code = source(invoke=('let first=await equipped("direction",message_id=input);'
            'let changed=session_bind(retained,[agent_tool("guide_worker",fn(x){null})]);'
            'let rejected=try{await changed("direction",message_id=input);"incorrect replay"}catch e{e.code};[first,rejected]'))
        with Framework(settings=settings) as app:
            self.project(app, code)
            first, rejected = app.start('p', 1, 'one', mode='source').result(8)
            self.assertEqual(first['output'], 'done')
            self.assertNotEqual(rejected, 'incorrect replay')
            self.assertEqual(app.get_process('p', 'worker')['guidanceRevision'], 1)
            self.assertEqual(app.agent_session('p', 'chat')['turn'], 1)
        self.assertEqual(len(client.requests), 2)


if __name__ == '__main__':
    unittest.main()
