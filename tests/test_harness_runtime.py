import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from chaos.background import BackgroundManager, BackgroundCancelled
from chaos.execution import CodeExecutor, BrowserSession, ExecutionError, ExecutionTimeout, run_process
from chaos.harness import NativeHarness, CodexHarness, HarnessError


def message(content=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


def call(name='tool_0', args='{}', ident='a'):
    return SimpleNamespace(id=ident, function=SimpleNamespace(name=name, arguments=args))


class FakeClient:
    def __init__(self, responses):
        self.responses, self.requests, self.closed = iter(responses), [], False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result()
        return result
    def close(self):
        self.closed = True


CONFIG = {'provider': 'test', 'baseURL': 'https://example.invalid/v1', 'apiKey': 'test-key',
          'model': 'model-a', 'timeout': 5, 'maxSteps': 4}


class NativeHarnessTests(unittest.TestCase):
    def harness(self, responses, **kwargs):
        client = FakeClient(responses)
        harness = NativeHarness({**CONFIG, **kwargs.pop('config', {})}, client_factory=lambda config: client, **kwargs)
        return harness, client

    def test_plain_text_is_never_interpreted_as_magic_action(self):
        text = '{"action":"tool","name":"danger"}\n```python\nprint(1)\n```'
        harness, client = self.harness([message(text)])
        self.assertEqual(harness.run('Do a task', 'Input'), text)
        self.assertTrue(client.closed)

    def test_native_calls_alias_and_preserve_arbitrary_json(self):
        harness, client = self.harness([message(calls=[call(args='{"input":[1,true,null]}')]), message('done')])
        observed, events = [], []
        result = harness.run('Task', 10, [{'name': 'custom.tool', 'wrapped': True,
            'parameters': {'type': 'object', 'properties': {'input': {}}}, 'category': 'code'}],
            lambda name, value: observed.append((name, value)) or {'ok': True},
            event=lambda kind, payload: events.append((kind, payload)))
        self.assertEqual(result, 'done')
        self.assertEqual(observed, [('custom.tool', [1, True, None])])
        self.assertEqual(client.requests[0]['tools'][0]['function']['name'], 'tool_0')
        self.assertEqual(client.requests[1]['messages'][-1]['role'], 'tool')
        started = [p for k, p in events if k == 'agent.tool_started'][0]
        self.assertEqual((started['callId'], started['category']), ('a', 'code'))

    def test_all_tool_calls_validated_before_effects(self):
        harness, _ = self.harness([message(calls=[call(), call('missing', ident='b')])])
        effects = []
        with self.assertRaises(HarnessError):
            harness.run('', '', [{'name': 'x'}], lambda *args: effects.append(args))
        self.assertEqual(effects, [])

    def test_invalid_json_and_duplicate_ids_rejected(self):
        for calls in ([call(args='{"x":NaN}')], [call(), call()]):
            with self.subTest(calls=calls):
                harness, _ = self.harness([message(calls=calls)])
                with self.assertRaises(HarnessError):
                    harness.run('', '', [{'name': 'x'}], lambda *args: None)

    def test_fallback_does_not_replay_successful_tool_effect(self):
        unavailable = RuntimeError('secret transport body')
        unavailable.status_code = 503
        failures, effects = [], []
        harness, client = self.harness([message(calls=[call()]), unavailable, message('done')],
            config={'candidates': ['model-b']}, report_failure=lambda p, m: failures.append((p, m)))
        self.assertEqual(harness.run('', '', [{'name': 'x'}], lambda *args: effects.append(args)), 'done')
        self.assertEqual(len(effects), 1)
        self.assertEqual(failures, [('test', 'model-a')])
        self.assertEqual([r['model'] for r in client.requests], ['model-a', 'model-a', 'model-b'])
        self.assertEqual(client.requests[-1]['messages'][-1]['role'], 'tool')

    def test_authentication_error_is_sanitized_and_not_retried(self):
        bad = RuntimeError('Authorization Bearer sk-do-not-log')
        bad.status_code = 401
        harness, client = self.harness([bad], config={'candidates': ['b']})
        with self.assertRaises(HarnessError) as context:
            harness.run('', '')
        self.assertNotIn('sk-do-not-log', str(context.exception))
        self.assertEqual(len(client.requests), 1)

    def test_cancel_pending_request_without_waiting_for_model(self):
        release = threading.Event()
        cancel = threading.Event()
        def response():
            release.wait(1)
            return message('later')
        harness, _ = self.harness([response])
        def check():
            if cancel.is_set():
                raise BackgroundCancelled()
        timer = threading.Timer(.08, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(BackgroundCancelled):
                harness.run('', '', checkpoint=check)
            self.assertLess(time.monotonic() - started, .5)
        finally:
            release.set()
            timer.join()

    def test_explicit_image_output_uses_multimodal_input(self):
        harness, client = self.harness([message(calls=[call()]), message('saw it')])
        image = 'data:image/jpeg;base64,YWJj'
        harness.run('', '', [{'name': 'browser', 'imageOutput': True}], lambda *args: {'image': image})
        self.assertEqual(client.requests[1]['messages'][-1]['content'][1]['image_url']['url'], image)
        self.assertNotIn(image, client.requests[1]['messages'][-2]['content'])

    def test_recoverable_tool_error_returns_observation_then_model_repairs(self):
        harness, client = self.harness([message(calls=[call(args='{"timeout":120}')]),
                                       message(calls=[call(args='{"timeout":1}', ident='b')]), message('done')])
        attempts = []
        def tool(name, value):
            attempts.append(value['timeout'])
            if value['timeout'] > 60:
                raise ValueError('Tool timeout must not exceed 60')
            return {'exitCode': 0}
        self.assertEqual(harness.run('', '', [{'name': 'code.execute'}], tool), 'done')
        self.assertEqual(attempts, [120, 1])
        observation = json.loads(client.requests[1]['messages'][-1]['content'])
        self.assertEqual(observation['error']['type'], 'ValueError')
        self.assertIn('60', observation['error']['message'])

    def test_tool_cancellation_is_not_converted_to_observation(self):
        harness, client = self.harness([message(calls=[call()])])
        def cancel(*args):
            raise BackgroundCancelled()
        with self.assertRaises(BackgroundCancelled):
            harness.run('', '', [{'name': 'code.execute'}], cancel)
        self.assertEqual(len(client.requests), 1)

    def test_step_budget_enforced(self):
        harness, _ = self.harness([message(calls=[call()])], config={'maxSteps': 1})
        with self.assertRaisesRegex(HarnessError, 'budget'):
            harness.run('', '', [{'name': 'x'}], lambda *args: None)


class ExecutionTests(unittest.TestCase):
    def test_python_and_shell_output_and_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = CodeExecutor(directory)
            result = executor.run({'language': 'python', 'code': 'from pathlib import Path\nPath("artifact.txt").write_text("ok")\nprint(6 * 7)'})
            self.assertEqual((result['exitCode'], result['stdout'].strip()), (0, '42'))
            self.assertEqual(Path(directory, 'artifact.txt').read_text(), 'ok')
            self.assertEqual(result['isolation'], 'trusted-host')
            if os.name == 'posix':
                self.assertEqual(executor.run({'language': 'shell', 'code': 'printf hello'})['stdout'], 'hello')

    def test_output_is_bounded_and_both_pipes_drained(self):
        with tempfile.TemporaryDirectory() as directory:
            result = CodeExecutor(directory, max_output=4096).run({'code': 'import sys\nprint("x" * 200000)\nsys.stderr.write("y" * 200000)'})
            self.assertTrue(result['truncated'])
            self.assertLessEqual(len(result['stdout']) + len(result['stderr']), 4096)
            self.assertEqual(result['exitCode'], 0)

    def test_timeout_and_checkpoint_cancel_kill_process(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = CodeExecutor(directory, timeout=.1)
            with self.assertRaises(ExecutionTimeout):
                executor.run({'code': 'import time\ntime.sleep(5)'})
            count = [0]
            def check():
                count[0] += 1
                if count[0] >= 3:
                    raise BackgroundCancelled()
            started = time.monotonic()
            with self.assertRaises(BackgroundCancelled):
                CodeExecutor(directory).run({'code': 'import time\ntime.sleep(5)'}, check)
            self.assertLess(time.monotonic() - started, .5)

    def test_streaming_lines_emitted_before_exit(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            seen = []
            started = time.monotonic()
            run_process([sys.executable, '-u', '-c', 'import time; print("ready"); time.sleep(.3)'], directory,
                        on_line=lambda line: seen.append((line, time.monotonic() - started)))
            self.assertEqual(seen[0][0], 'ready')
            self.assertLess(seen[0][1], .25)

    def test_codex_is_standalone_and_provider_key_is_not_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            observed = {}
            def execute(argv, cwd, **kwargs):
                observed.update(argv=argv, cwd=cwd, **kwargs)
                kwargs['on_line'](json.dumps({'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'private'}}))
                kwargs['on_line'](json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}))
                return {'exitCode': 0}
            with patch('chaos.harness.shutil.which', return_value='/bin/codex'), patch('chaos.harness.run_process', side_effect=execute):
                events = []
                result = CodexHarness({**CONFIG, 'workspace': directory}).run('task', 'input', event=lambda *args: events.append(args))
            self.assertEqual(result, 'done')
            self.assertIn('--json', observed['argv'])
            self.assertIn('--ignore-user-config', observed['argv'])
            self.assertNotIn(CONFIG['apiKey'], ' '.join(observed['argv']))
            self.assertEqual(observed['env']['CHAOS_PROVIDER_KEY'], CONFIG['apiKey'])
            self.assertFalse(Path(observed['env']['CODEX_HOME']).exists())
            self.assertNotIn('private', json.dumps(events))

    def test_codex_rejects_unbridged_host_tools(self):
        with self.assertRaisesRegex(HarnessError, 'callback'):
            CodexHarness(CONFIG).run('', '', tools=[{'name': 'x'}])


class BrowserTests(unittest.TestCase):
    def test_independent_headless_session_snapshot_and_virtual_cursor(self):
        class Target:
            def count(self): return 1
            def element_handle(self, **kwargs): return self
            def evaluate(self, source, original=None): return {'x':0,'y':0} if 'clientLeft' in source else True
            def dispose(self): pass
            def scroll_into_view_if_needed(self, **kwargs): pass
            def bounding_box(self): return {'x': 10, 'y': 20, 'width': 40, 'height': 10}
            def click(self, **kwargs): pass
            def fill(self, text, **kwargs): pass
            def aria_snapshot(self, **kwargs): return '- button "Run"'
            def evaluate_all(self, source): return [{'selector': '[data-chaos-target="0"]', 'tag': 'button', 'text': 'Run', 'candidates': [{'kind': 'css', 'value': '#run'}]}]
        class Page:
            viewport_size = {'width':1280,'height':800}
            url = 'https://example.invalid/'
            def title(self): return 'Example'
            def locator(self, selector): return Target()
        with tempfile.TemporaryDirectory() as directory:
            session = BrowserSession(directory)
            session._page = Page()
            result = session.call({'action': 'click', 'selector': '[data-chaos-target="0"]'})
            self.assertEqual(result['cursor'], {'x': 30, 'y': 25})
            self.assertEqual(result['isolation'], 'headless-context')
            self.assertEqual(result['targets'][0]['text'], 'Run')
            self.assertIsNone(BrowserSession(directory)._page)

    def test_browser_rejects_cross_thread_and_unsafe_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            session = BrowserSession(directory)
            session._page = object()
            with self.assertRaises(ValueError):
                session.call({'action': 'navigate', 'url': 'file:///etc/passwd'})
            errors = []
            def foreign():
                try: session.call({'action': 'snapshot'})
                except ExecutionError as exc: errors.append(exc)
            thread = threading.Thread(target=foreign)
            thread.start(); thread.join()
            self.assertEqual(len(errors), 1)

    def test_screenshot_is_retained_and_inline_data_is_bounded(self):
        class Page:
            url = 'https://example.invalid/'
            def title(self): return 'Example'
            def screenshot(self, **kwargs): return b'fake-jpeg'
        with tempfile.TemporaryDirectory() as directory:
            session = BrowserSession(directory)
            session._page = Page()
            result = session.call({'action': 'screenshot'})
            self.assertEqual(Path(result['artifactPath']).read_bytes(), b'fake-jpeg')
            self.assertTrue(result['image'].startswith('data:image/jpeg;base64,'))


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name, 'jobs.sqlite3'))
        self.managers = []
    def tearDown(self):
        for manager in self.managers:
            manager.close(wait=True)
        self.directory.cleanup()
    def manager(self, worker, **kwargs):
        manager = BackgroundManager(self.path, worker, **kwargs)
        self.managers.append(manager)
        return manager
    def wait(self, manager, task_id, statuses=('completed', 'failed', 'cancelled'), timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = manager.get(task_id)
            if job['status'] in statuses:
                return job
            time.sleep(.01)
        self.fail('Task did not reach expected state: ' + str(manager.get(task_id)))

    def test_start_is_async_and_iterations_are_durable(self):
        gate = threading.Event()
        def work(task, agent, previous, checkpoint, event):
            while not gate.wait(.01):
                checkpoint()
            return (previous or 0) + 1
        manager = self.manager(work)
        started = time.monotonic()
        job = manager.start({'prompt': 'iterate'}, iterations=3, interval=0)
        self.assertLess(time.monotonic() - started, .1)
        gate.set()
        final = self.wait(manager, job['id'])
        self.assertEqual((final['iteration'], final['output'], final['status']), (3, 3, 'completed'))
        events = manager.events(job['id'])
        self.assertEqual([e['payload']['iteration'] for e in events if e['type'] == 'task.iteration'], [1, 2, 3])
        self.assertGreater(manager.events(after=events[0]['id'])[0]['id'], events[0]['id'])

    def test_dynamic_concurrency_is_enforced(self):
        active, peak, lock, gate = [0], [0], threading.Lock(), threading.Event()
        def work(task, agent, previous, checkpoint, event):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                while not gate.wait(.01):
                    checkpoint()
            finally:
                with lock:
                    active[0] -= 1
            return 'ok'
        manager = self.manager(work, max_workers=1)
        jobs = [manager.start('task', iterations=1) for _ in range(3)]
        self.wait(manager, jobs[0]['id'], ('running',))
        time.sleep(.05)
        self.assertEqual(peak[0], 1)
        manager.configure(2)
        self.wait(manager, jobs[1]['id'], ('running',))
        self.assertEqual(peak[0], 2)
        gate.set()
        for job in jobs:
            self.wait(manager, job['id'])
        self.assertEqual(peak[0], 2)

    def test_cancel_resume_keeps_completed_iteration(self):
        blocker = threading.Event()
        def work(task, agent, previous, checkpoint, event):
            if previous == 1:
                while not blocker.wait(.01):
                    checkpoint()
            return (previous or 0) + 1
        manager = self.manager(work)
        job = manager.start('task', iterations=2, interval=0)
        deadline = time.monotonic() + 2
        while manager.get(job['id'])['iteration'] < 1 and time.monotonic() < deadline:
            time.sleep(.01)
        manager.cancel(job['id'])
        final = self.wait(manager, job['id'])
        self.assertEqual((final['iteration'], final['status']), (1, 'cancelled'))
        blocker.set()
        manager.resume(job['id'])
        final = self.wait(manager, job['id'])
        self.assertEqual((final['iteration'], final['output']), (2, 2))

    def test_restart_recovers_interrupted_job(self):
        started = threading.Event()
        def work(task, agent, previous, checkpoint, event):
            started.set()
            while True:
                checkpoint()
                time.sleep(.01)
        manager = self.manager(work)
        job = manager.start('task', iterations=1)
        self.assertTrue(started.wait(1))
        manager.close(wait=True)
        replacement = self.manager(lambda *args: 'resumed')
        self.assertEqual(replacement.get(job['id'])['status'], 'interrupted')
        replacement.resume(job['id'])
        self.assertEqual(self.wait(replacement, job['id'])['output'], 'resumed')

    def test_provider_event_private_fields_not_persisted(self):
        def work(task, agent, previous, checkpoint, event):
            event('agent.thinking', {'summary': 'Working', 'reasoning': 'private', 'apiKey': 'secret', 'callId': 'a'})
            return 'done'
        manager = self.manager(work)
        job = manager.start('task', iterations=1)
        self.wait(manager, job['id'])
        encoded = json.dumps(manager.events())
        self.assertNotIn('private', encoded)
        self.assertNotIn('secret', encoded)
        self.assertIn('callId', encoded)

    def test_failure_can_resume_without_leaking_exception_body(self):
        calls = []
        def work(*args):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('private provider response')
            return 'done'
        manager = self.manager(work)
        job = manager.start('task', iterations=1)
        failed = self.wait(manager, job['id'])
        self.assertEqual(failed['error'], 'RuntimeError')
        manager.resume(job['id'])
        self.assertEqual(self.wait(manager, job['id'])['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
