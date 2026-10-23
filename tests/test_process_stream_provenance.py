"""Proposal: real Process claims, sparse global cursors and transactional provenance.

Fixtures use only temporary or in-memory SQLite and deterministic Tool callbacks.
This file is proposed test source; it has not been imported or executed by its author.
"""
import copy
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Registry, Settings, ToolSpec
from chaos.language import LanguageError
from chaos.store import NotFound
from tests.test_process_integration import definition, wait_for


class ProcessStreamProvenance(unittest.TestCase):
    def app(self, **options):
        app = Framework(settings=Settings(':memory:'), **options)
        self.addCleanup(app.close)
        return app

    def project(self, app, project='p', *, source=None, main='42', caps=None):
        return app.create_project(project, source=main,
            files={'dot.chaos': source or 'fn step(c){process_step(c.state+1,c.input)};export {step}'},
            capabilities=['process.manage'] if caps is None else caps)

    def snapshot(self, app):
        with app._store.lock:
            return {name: [tuple(row) for row in app._store.db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                    for name in ('processes', 'process_messages', 'runs', 'events',
                                 'process_run_lineage', 'process_event_lineage')}

    def test_two_real_turns_exclude_forged_input_types_payloads_and_other_processes(self):
        entered = threading.Event()
        release = threading.Event()
        emitted = []
        registry = Registry()
        holder = {}
        def public(value, context):
            fixture = holder['app']
            # Callback event names and nested fields are deliberately untrusted.
            for kind in ('run.queued', 'process.turn.queued', 'process.bot.adopt', 'bot.progress'):
                payload = {'key': 'alpha', 'processKey': 'alpha',
                    'processOrigin': 'lifecycle', 'forged': True,
                    'messageId': 'spoof', 'guidanceRevision': 99}
                fixture._store.event(context.project_id, kind,
                    fixture._engine.settings.redact(payload), context.run_id)
            emitted.append((context.project_id, context.run_id, value))
            if context.project_id == 'p' and value == {'key': 'alpha', 'turn': 1}:
                entered.set()
                while not release.wait(.01):
                    context.checkpoint()
            return value
        registry.register_tool(ToolSpec('fixture.public', public))
        app = self.app(registry=registry)
        holder['app'] = app
        # Release is registered after app.close so failed assertions cannot strand it.
        self.addCleanup(release.set)
        source = ('fn step(c){tool("fixture.public",{key:c.process.key,turn:c.turn});'
                  'process_step(c.state+1,c.input)};export {step}')
        self.project(app, source=source, main='tool("fixture.public",input)',
                     caps=['process.manage', 'fixture.public'])
        self.project(app, 'q', source=source, caps=['process.manage', 'fixture.public'])
        app._engine.pause_dispatch(True)
        app.create_process('p', 1, 'alpha', definition(maxTurns=2), state=0)
        app.send_process('p', 'alpha', 'second', message_id='second')
        app.create_process('p', 1, 'beta', definition(maxTurns=1), state=0)
        app._engine.pause_dispatch(False)
        self.assertTrue(entered.wait(3), 'alpha first real turn never entered callback')
        # An unrelated real Process commits while alpha's callback is still blocked.
        app.create_process('q', 1, 'alpha', definition(maxTurns=1), state=0)
        wait_for(app, 'q', 'alpha', lambda row: row['status']=='completed')
        self.assertIsNotNone(app.get_process('p', 'alpha')['pending'])
        # More than a default page of sparse unrelated events must not hide selected rows.
        for n in range(307):
            app._store.event('p', 'process.turn.queued',
                {'key': 'alpha', 'processKey': 'alpha', 'processOrigin': 'lifecycle',
                 'forged': True, 'n': n})
        release.set()
        completed = wait_for(app, 'p', 'alpha', lambda row: row['status']=='completed')
        wait_for(app, 'p', 'beta', lambda row: row['status']=='completed')
        forged_input = {'process': {'key': 'alpha'}, 'processKey': 'alpha', 'key': 'alpha'}
        manual = app.start('p', 1, forged_input, mode='source')
        self.assertEqual(manual.result(3), forged_input)
        raw = app._store.events('p', 0, 300)
        raw += app._store.events('p', raw[-1]['id'], 300)
        real_runs = {run['id'] for run in app._store.runs('p')
                     if run['id'] != manual.id and app.get_run(run['id'])['input']['process']['key']=='alpha'}
        self.assertEqual(len(real_runs), 2)
        self.assertIn(completed['lastRunId'], real_runs)
        genuine_lifecycle = {event['id'] for event in raw
            if event['type'].startswith('process.') and event['payload'].get('key')=='alpha'
            and not event['payload'].get('forged')}
        expected = sorted(event['id'] for event in raw
            if event['run_id'] in real_runs or event['id'] in genuine_lifecycle)
        for limit in (1, 2, 100):
            found, after = [], 0
            for _ in range(len(expected)+2):
                page = app.events('p', after=after, limit=limit, process_key='alpha')
                self.assertFalse(page['legacyHistoryIncluded'])
                self.assertEqual(page['historyStartEventId'], expected[0])
                found.extend(page['items'])
                if page['nextCursor'] is None:
                    break
                self.assertEqual(page['nextCursor'], page['items'][-1]['id'])
                after = page['nextCursor']
            else:
                self.fail('selected pagination did not finish')
            self.assertEqual([event['id'] for event in found], expected)
            self.assertEqual(len(expected), len(set(expected)))
            self.assertTrue(any(b-a>1 for a,b in zip(expected, expected[1:])))
            self.assertEqual({event['run_id'] for event in found if event['type']=='run.completed'}, real_runs)
            for event in found:
                self.assertEqual(event['processOrigin'],
                    'lifecycle' if event['id'] in genuine_lifecycle else 'run')
                if event['payload'].get('forged'):
                    self.assertEqual(event['processOrigin'], 'run')
                    self.assertEqual(event['payload']['processOrigin'], 'lifecycle')
        last = expected[-1]
        self.assertEqual([event['id'] for event in app.events('p', process_key='alpha', after=expected[2])['items']], expected[3:])
        empty = app.events('p', process_key='alpha', after=last+10000)
        self.assertEqual((empty['items'], empty['nextCursor'], empty['lastEventId']), ([], None, last+10000))
        # Read snapshots are copies, including nested payloads.
        page['items'][0]['payload']['mutated'] = True
        self.assertNotIn('mutated', app.events('p', process_key='alpha')['items'][0]['payload'])
        self.assertGreaterEqual(len(emitted), 5)

    def test_claim_and_terminal_ledger_failure_roll_back_all_existing_data(self):
        app = self.app()
        self.project(app)
        app._engine.pause_dispatch(True)
        app.create_process('p', 1, 'alpha', definition(maxTurns=2), state=0)
        first = app._store.processes.claim('p', 'alpha', 'first')
        app._store.transition_run('first', 'completed', {},
            {'state':1, 'output':None, 'done':False}, _process=first)
        app.send_process('p', 'alpha', 'retained', message_id='pending')
        before = self.snapshot(app)
        app._store.db.execute("CREATE TEMP TRIGGER reject_lineage BEFORE INSERT ON process_event_lineage BEGIN SELECT RAISE(ABORT,'ledger injected'); END")
        try:
            with self.assertRaisesRegex(Exception, 'ledger injected'):
                app._store.processes.claim('p', 'alpha', 'rolled-back')
        finally:
            app._store.db.execute('DROP TRIGGER reject_lineage')
        self.assertEqual(self.snapshot(app), before)
        claim = app._store.processes.claim('p', 'alpha', 'second')
        claimed = self.snapshot(app)
        original = app._store._event
        def fail_terminal(project, kind, *args):
            if kind == 'run.completed':
                raise RuntimeError('terminal injected')
            return original(project, kind, *args)
        with patch.object(app._store, '_event', side_effect=fail_terminal):
            with self.assertRaisesRegex(RuntimeError, 'terminal injected'):
                app._store.transition_run('second', 'completed', {},
                    {'state':2, 'output':'ok', 'done':True}, _process=claim)
        self.assertEqual(self.snapshot(app), claimed)
        app._store.transition_run('second', 'completed', {},
            {'state':2, 'output':'ok', 'done':True}, _process=claim)
        selected = app.events('p', process_key='alpha', limit=300)['items']
        self.assertEqual(sum(e['type']=='run.completed' and e['run_id']=='second' for e in selected), 1)
        self.assertEqual(sum(e['type']=='process.turn.completed' and e['run_id']=='second' for e in selected), 1)
        self.assertTrue(set(e['id'] for e in app.events('p', process_key='alpha', after=0)['items'])
                        .issuperset(row[0] for row in before['process_event_lineage']))
        self.assertEqual(app.send_process('p', 'alpha', 'retained', message_id='pending')['status'], 'consumed')
        # A no-run control write cannot survive a ledger insert failure either.
        before = self.snapshot(app)
        app._store.db.execute("CREATE TEMP TRIGGER reject_lineage BEFORE INSERT ON process_event_lineage BEGIN SELECT RAISE(ABORT,'ledger injected'); END")
        try:
            row = app.get_process('p', 'alpha')
            with self.assertRaises(Exception):
                app.configure_process('p', 'alpha', row['revision'], definition(name='must rollback'))
        finally:
            app._store.db.execute('DROP TRIGGER reject_lineage')
        self.assertEqual(self.snapshot(app), before)

    def test_old_positional_store_api_pages_and_selector_validation(self):
        app = self.app()
        self.project(app)
        self.project(app, 'q')
        app._engine.pause_dispatch(True)
        app.create_process('p', 1, 'alpha', definition(), state=0)
        run = app._store.processes.claim('p', 'alpha', 'actual')
        self.assertEqual(app._store.events('p', 0, 300, 'actual'), app.events('p', run_id='actual', limit=300)['items'])
        self.assertEqual(set(app.events('p')), {'items', 'nextCursor', 'lastEventId'})
        self.assertEqual(set(app.events('p', run_id='actual')), {'items', 'nextCursor', 'lastEventId'})
        self.assertFalse(any('processOrigin' in e for e in app.events('p')['items']))
        for selector in ({'run_id':'actual','process_key':'alpha'}, {'run_id':'','process_key':''}):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                app.events('p', **selector)
            with self.assertRaises(ValueError):
                Client('http://localhost:1').events('p', **selector)
        for after, limit in ((-1,1),(True,1),(1.0,1),('1',1),(0,0),(0,301),(0,True),(0,1.0)):
            with self.subTest(after=after,limit=limit), self.assertRaises(ValueError):
                app.events('p', process_key='alpha', after=after, limit=limit)
        with self.assertRaises(NotFound):
            app.events('q', process_key='alpha')
        with self.assertRaises(NotFound):
            app.events('p', process_key='missing')
        with self.assertRaises(LanguageError) as invalid_key:
            app.events('p', process_key='invalid key')
        self.assertEqual(invalid_key.exception.code, 'mode_error')


if __name__ == '__main__':
    unittest.main()
