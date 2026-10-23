"""Terminal-state invariants for trusted extension and cleanup boundaries."""
import asyncio
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Framework, Registry, Settings, Workflow
from chaos.runtime import Context, Cancelled
from chaos.language import ResourceLimit
import chaos.runtime as runtime


class RuntimeLifecycleTests(unittest.TestCase):
    def assert_terminal(self, app, handle, status):
        run = handle.wait(2)
        self.assertEqual(run['status'], status)
        events = app.events(handle.project_id, run_id=handle.id)['items']
        terminal = [event['type'] for event in events
                    if event['type'] in ('run.completed', 'run.failed', 'run.cancelled', 'run.interrupted')]
        self.assertEqual(terminal, ['run.' + status])
        with app._engine.lock:
            self.assertNotIn(handle.id, app._engine.active)
        return run

    def test_terminal_errors_are_redacted_before_truncation_and_persistence(self):
        secret = 'AUDIT_SYNTHETIC_KEY_NEVER_REAL_20261007'
        settings = Settings(':memory:')
        settings.update({'version': settings.public()['version'],
                         'providers': {'text': {'apiKey': secret}}})
        registry = Registry()

        @registry.tool('audit.failure')
        def failure(value, context):
            raise ValueError('x' * value + secret + ' diagnostic suffix')

        @registry.provider('audit.provider')
        def provider(request, context):
            raise ValueError('x' * request.input + secret + ' diagnostic suffix')

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'state.sqlite3'
            run_ids = []
            with Framework(database, registry=registry, settings=settings) as app:
                for prefix in (960, 990):
                    for kind, source, capabilities in (
                        ('tool', 'tool("audit.failure", input)', ['audit.failure']),
                        ('provider', 'agent a {provider:"audit.provider"}; a(input)', []),
                    ):
                        with self.subTest(prefix=prefix, kind=kind):
                            name = kind + '_' + str(prefix)
                            app.create_project(name, source=source, capabilities=capabilities)
                            handle = app.start(name, 1, prefix)
                            run = self.assert_terminal(app, handle, 'failed')
                            run_ids.append((handle.id, prefix))
                            if prefix == 960:
                                self.assertIn('[redacted]', run['error'])
                            # At 990 the original key crosses the 1000-character
                            # event boundary; even a partial key must disappear.
                            self.assertNotIn(secret, run['error'])
                            self.assertNotIn('AUDIT_', run['error'])
                            self.assertLessEqual(len(run['error']), 1000)
                            events = app.events(name, run_id=handle.id)['items']
                            self.assertNotIn(secret, json.dumps(events))
                            self.assertNotIn('AUDIT_', json.dumps(events))
                            # Read the durable column as well as the public SDK view.
                            raw = app._store.db.execute('SELECT error FROM runs WHERE id=?', (handle.id,)).fetchone()[0]
                            self.assertEqual(raw, run['error'])
            settings.update({'version': settings.public()['version'],
                             'providers': {'text': {'clearKey': True}}})
            with Framework(database, registry=registry, settings=settings) as reopened:
                for run_id, prefix in run_ids:
                    record = reopened.get_run(run_id)
                    self.assertNotIn(secret, record['error'])
                    self.assertNotIn('AUDIT_', record['error'])
                    self.assertLessEqual(len(record['error']), 1000)
                    if prefix == 960:
                        self.assertIn('[redacted]', record['error'])
                    events = reopened.events(record['project_id'], run_id=run_id)['items']
                    self.assertNotIn(secret, json.dumps(events))
                    self.assertNotIn('AUDIT_', json.dumps(events))

    def test_abnormal_tool_and_provider_exits_reach_terminal_state(self):
        for kind in ('tool', 'provider'):
            for graph_mode in (False, True):
                for exception_type in (SystemExit, asyncio.CancelledError):
                    with self.subTest(kind=kind, graph=graph_mode, exception=exception_type.__name__):
                        registry = Registry()

                        def abort(*args):
                            raise exception_type()

                        registry.tool('audit.abort')(abort)
                        registry.provider('audit.provider')(abort)
                        capabilities = ['audit.abort'] if kind == 'tool' else []
                        if graph_mode:
                            flow = Workflow().input()
                            if kind == 'tool':
                                flow.tool('worker', 'audit.abort')
                            else:
                                flow.agent('worker', 'abort', provider='audit.provider')
                            flow.output().connect('input', 'worker').connect('worker', 'output')
                            options = {'graph': flow}
                        else:
                            options = {'source': 'tool("audit.abort", null)' if kind == 'tool'
                                       else 'agent a {provider:"audit.provider"}; a(null)'}
                        with tempfile.TemporaryDirectory() as directory:
                            database = Path(directory) / 'state.sqlite3'
                            with Framework(database, registry=registry) as app:
                                app.create_project('abnormal', capabilities=capabilities, **options)
                                handle = app.start('abnormal', 1, None)
                                run = self.assert_terminal(app, handle, 'failed')
                                self.assertIn(exception_type.__name__, run['error'])
                                if graph_mode:
                                    events = app.events('abnormal', run_id=handle.id)['items']
                                    self.assertIn('node.failed', [event['type'] for event in events])
                                self.assertIsNone(run['output'])
                                self.assertFalse(handle.cancel())
                            with Framework(database, registry=registry) as reopened:
                                self.assertEqual(reopened.get_run(handle.id)['status'], 'failed')

    def test_context_initialization_failure_is_terminal(self):
        with Framework() as app:
            app.create_project('setup', source='42')
            with patch('chaos.runtime.Context', side_effect=RuntimeError('context setup failed')):
                handle = app.start('setup', 1, None)
                run = self.assert_terminal(app, handle, 'failed')
            self.assertIn('context setup failed', run['error'])
            events = app.events('setup', run_id=handle.id)['items']
            self.assertEqual(events[-1]['payload']['steps'], 0)

    def test_error_rendering_failure_cannot_strand_the_worker(self):
        class BrokenMessage(Exception):
            def __str__(self):
                raise RuntimeError('message rendering failed')

        with Framework() as app:
            app.create_project('setup', source='42')
            with patch('chaos.runtime.Context', side_effect=BrokenMessage()):
                handle = app.start('setup', 1, None)
                run = self.assert_terminal(app, handle, 'failed')
            self.assertEqual(run['error'], 'BrokenMessage')

    def test_cleanup_tries_every_environment_and_preserves_original_failure(self):
        for source, expected in (('42', 'cleanup failed'), ('1 / 0', 'division by zero')):
            with self.subTest(source=source):
                closed = []

                class Environment:
                    def __init__(self, name, exception=None):
                        self.name, self.exception = name, exception

                    def close(self):
                        closed.append(self.name)
                        if self.exception is not None:
                            raise self.exception

                def context(*args, **kwargs):
                    ctx = Context(*args, **kwargs)
                    ctx.tool_environments.update({
                        'first': Environment('first', SystemExit('cleanup failed')),
                        'second': Environment('second', RuntimeError('later cleanup failure')),
                        'last': Environment('last'),
                    })
                    return ctx

                with Framework() as app:
                    app.create_project('cleanup', source=source)
                    with patch('chaos.runtime.Context', side_effect=context):
                        handle = app.start('cleanup', 1, None)
                        run = self.assert_terminal(app, handle, 'failed')
                    self.assertIn(expected, run['error'])
                    self.assertIsNone(run['output'])
                    self.assertEqual(closed, ['first', 'second', 'last'])

    def test_graph_node_cleanup_preserves_original_failure(self):
        for fail_body in (False, True):
            with self.subTest(fail_body=fail_body):
                contexts, closed = [], []
                registry = Registry()

                class Environment:
                    def close(self):
                        closed.append(True)
                        raise SystemExit('secondary cleanup failure')

                def context(*args, **kwargs):
                    ctx = Context(*args, **kwargs)
                    contexts.append(ctx)
                    return ctx

                @registry.tool('audit.node')
                def node(value, extension):
                    # Install a synthetic thread-owned environment exactly as
                    # the builtin browser/code tools do, without external I/O.
                    ctx = contexts[-1]
                    ctx.tool_environments[(threading.get_ident(), extension.node_id)] = Environment()
                    if fail_body:
                        raise ValueError('primary node failure')
                    return 42

                flow = (Workflow().input().tool('worker', 'audit.node').output()
                        .connect('input', 'worker').connect('worker', 'output'))
                with Framework(registry=registry) as app:
                    app.create_project('node_cleanup', graph=flow, capabilities=['audit.node'])
                    with patch('chaos.runtime.Context', side_effect=context):
                        handle = app.start('node_cleanup', 1, None)
                        run = self.assert_terminal(app, handle, 'failed')
                    if fail_body:
                        self.assertIn('primary node failure', run['error'])
                        self.assertNotIn('secondary cleanup failure', run['error'])
                    else:
                        self.assertIn('secondary cleanup failure', run['error'])
                    self.assertEqual(closed, [True])

    def test_node_cleanup_cannot_replace_hard_control_with_catchable_failure(self):
        for exception, expected in ((ResourceLimit('hard resource limit'), 'failed'),
                                    (Cancelled('hard cancellation'), 'cancelled')):
            with self.subTest(control=type(exception).__name__):
                contexts, closed = [], []
                registry = Registry()

                class Environment:
                    def close(self):
                        closed.append(True)
                        raise ValueError('ordinary cleanup failure')

                def context(*args, **kwargs):
                    ctx = Context(*args, **kwargs)
                    contexts.append(ctx)
                    return ctx

                @registry.tool('audit.control')
                def control(value, extension):
                    contexts[-1].tool_environments[(threading.get_ident(), extension.node_id)] = Environment()
                    raise exception

                flow = (Workflow().input().tool('worker', 'audit.control').output()
                        .connect('input', 'worker').connect('worker', 'output'))
                source = 'try { workflow(' + json.dumps(flow.build()) + ', null) } catch e { "caught" }'
                with Framework(registry=registry) as app:
                    app.create_project('control_cleanup', source=source, capabilities=['audit.control'])
                    with patch('chaos.runtime.Context', side_effect=context):
                        handle = app.start('control_cleanup', 1, None)
                        run = self.assert_terminal(app, handle, expected)
                    self.assertIsNone(run['output'])
                    self.assertNotIn('ordinary cleanup failure', run['error'])
                    if expected == 'failed':
                        self.assertIn('hard resource limit', run['error'])
                    self.assertEqual(closed, [True])

    def test_cancel_after_last_execution_checkpoint_discards_output(self):
        validating, release = threading.Event(), threading.Event()
        original_copy = runtime.json_copy

        def held_copy(value, *args, **kwargs):
            if type(value) is int and value == 42:
                validating.set()
                if not release.wait(2):
                    raise TimeoutError('test validation barrier timed out')
            return original_copy(value, *args, **kwargs)

        with Framework() as app:
            app.create_project('cancel_wins', source='42')
            with patch('chaos.runtime.json_copy', side_effect=held_copy):
                handle = app.start('cancel_wins', 1, None)
                try:
                    self.assertTrue(validating.wait(2))
                    self.assertTrue(handle.cancel())
                finally:
                    release.set()
                run = self.assert_terminal(app, handle, 'cancelled')
            self.assertIsNone(run['output'])

    def test_cancel_during_failure_normalization_wins_terminal_selection(self):
        rendering, release = threading.Event(), threading.Event()
        with Framework() as app:
            app.create_project('cancel_failure', source='1 / 0')
            original_error = app._engine._run_error

            def held_error(exception):
                rendering.set()
                if not release.wait(2):
                    raise TimeoutError('test error barrier timed out')
                return original_error(exception)

            with patch.object(app._engine, '_run_error', side_effect=held_error):
                handle = app.start('cancel_failure', 1, None)
                try:
                    self.assertTrue(rendering.wait(2))
                    self.assertTrue(handle.cancel())
                finally:
                    release.set()
                run = self.assert_terminal(app, handle, 'cancelled')
            self.assertIsNone(run['output'])
            self.assertEqual(run['error'], '执行已取消')

    def test_terminal_commit_wins_before_cancel_reports_acceptance(self):
        committing, release, cancel_called = threading.Event(), threading.Event(), threading.Event()
        with Framework() as app:
            app.create_project('commit_wins', source='42')
            original_transition = app._store.transition_run

            def held_transition(run_id, status, *args, **kwargs):
                if status == 'completed':
                    committing.set()
                    if not release.wait(2):
                        raise TimeoutError('test commit barrier timed out')
                return original_transition(run_id, status, *args, **kwargs)

            with patch.object(app._store, 'transition_run', side_effect=held_transition):
                handle = app.start('commit_wins', 1, None)
                try:
                    self.assertTrue(committing.wait(2))
                    with concurrent.futures.ThreadPoolExecutor(1) as pool:
                        def cancel():
                            cancel_called.set()
                            return handle.cancel()
                        future = pool.submit(cancel)
                        self.assertTrue(cancel_called.wait(2))
                        release.set()
                        self.assertFalse(future.result(timeout=2))
                finally:
                    release.set()
                run = self.assert_terminal(app, handle, 'completed')
            self.assertEqual(run['output'], 42)


if __name__ == '__main__':
    unittest.main()
