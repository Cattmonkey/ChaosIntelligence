"""Observable activity SSE shares durable task events; no model/network backend."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from chaos import Framework, Settings
from chaos.server import make_server
from chaos.openapi import specification


class ActivityStreamHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.secret = 'activity-stream-fixture-secret'
        settings = Settings(':memory:')
        settings.update({'providers': {'text': {'baseURL': 'https://example.invalid/v1',
                                               'apiKey': self.secret}}}, 1)
        self.app = Framework(settings=settings)
        for name in ('p', 'q'):
            self.app.create_project(name, source='42')
        root = Path(self.temp.name)
        self.server = make_server(port=0, framework=self.app, state_dir=root/'control', update_home=root/'updates')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.releases = []

    def tearDown(self):
        for event in self.releases:
            event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.app.close()
        self.temp.cleanup()

    def release(self):
        event = threading.Event()
        self.releases.append(event)
        return event

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail('fixture did not reach requested state')

    def start(self, manager, *, blocked=False, assistant=False):
        release = self.release()
        def worker(task, agent_id, previous, checkpoint, emit):
            emit('model.request', {'summary': 'Public progress ' + self.secret,
                                   'apiKey': 'must not be carried'})
            if blocked:
                while not release.wait(.02): checkpoint()
            return {'answer': 42}
        manager.worker = worker
        job = (self.server.ide.start('p', 1, 'Explain the program') if assistant else
               manager.start({'task': 'fixture'}, iterations=1, interval=0))
        return job['id'], release

    def open(self, route, query=None, headers=None, legacy=False):
        url = self.origin + ('/api/' if legacy else '/api/v1/') + route
        if query: url += '?' + urllib.parse.urlencode(query)
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=4)

    def event(self, response):
        fields = {}
        while True:
            line = response.readline().decode('utf-8')
            if not line: return None
            if line == '\n':
                if 'data' not in fields:
                    fields = {}; continue
                event = json.loads(fields['data'])
                self.assertEqual(int(fields['id']), event['id'])
                return event
            if not line.startswith(':'):
                key, value = line.rstrip('\n').split(':', 1)
                fields[key] = value.lstrip()

    def all(self, response):
        rows = []
        while True:
            event = self.event(response)
            if event is None: return rows
            rows.append(event)

    def assert_error(self, route, query=None, headers=None):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.open(route, query, headers)
        with raised.exception as error:
            self.assertIn(error.code, (400, 404))
            self.assertEqual(error.headers.get_content_type(), 'application/json')
            value = json.loads(error.read())
            self.assertIn('error', value)

    def test_live_task_progress_is_flushed_before_completion_and_redacted(self):
        manager = self.server.control.manager
        task_id, release = self.start(manager, blocked=True)
        with self.open('tasks/events/stream', {'taskId': task_id}) as response:
            self.assertEqual(response.headers.get_content_type(), 'text/event-stream')
            self.assertEqual(response.headers['X-Accel-Buffering'], 'no')
            rows = []
            while not any(row['type'] == 'model.request' for row in rows):
                rows.append(self.event(response))
            self.assertEqual(manager.get(task_id)['status'], 'running')
            self.assertNotIn(self.secret, json.dumps(rows))
            self.assertNotIn('must not be carried', json.dumps(rows))
            release.set()
            rows.extend(self.all(response))
        self.assertEqual(rows[-1]['type'], 'task.completed')
        self.assertEqual([row['id'] for row in rows], [row['id'] for row in manager.events(task_id)])

    def test_task_resume_cursor_precedence_filter_and_legacy_route(self):
        manager = self.server.control.manager
        task_id, _ = self.start(manager)
        other, _ = self.start(manager)
        self.wait(lambda: manager.get(task_id)['status'] == 'completed' and manager.is_idle())
        expected = manager.events(task_id)
        with self.open('tasks/events/stream', {'taskId': task_id, 'after': 999999},
                       {'Last-Event-ID': str(expected[0]['id'])}, legacy=True) as response:
            rows = self.all(response)
        self.assertEqual([row['id'] for row in rows], [row['id'] for row in expected[1:]])
        self.assertTrue(all(row['taskId'] == task_id for row in rows))
        with self.open('tasks/events', {'taskId': task_id}) as response:
            self.assertEqual(response.headers.get_content_type(), 'application/json')
            self.assertEqual([row['id'] for row in json.load(response)['items']], [row['id'] for row in expected])

    def test_assistant_stream_ownership_cursor_and_json_compatibility(self):
        manager = self.server.ide.manager
        job_id, _ = self.start(manager, assistant=True)
        self.wait(lambda: manager.get(job_id)['status'] == 'completed' and manager.is_idle())
        route = 'projects/p/assistant/' + job_id + '/events'
        expected = self.server.ide.events('p', job_id)['items']
        with self.open(route + '/stream', {'after': 999999}, {'Last-Event-ID': str(expected[0]['id'])}) as response:
            rows = self.all(response)
        self.assertEqual(rows, expected[1:])
        self.assert_error('projects/q/assistant/' + job_id + '/events/stream')
        with self.open(route) as response:
            self.assertEqual(json.load(response)['items'], expected)

    def test_invalid_cursor_selector_and_duplicate_query_fail_before_headers(self):
        manager = self.server.control.manager
        task_id, _ = self.start(manager)
        job_id, _ = self.start(self.server.ide.manager, assistant=True)
        for route, selector in [('tasks/events/stream', {'taskId': task_id}),
                                ('projects/p/assistant/'+job_id+'/events/stream', {})]:
            for value in ('-1', 'true', '1.5', 'bad'):
                with self.subTest(route=route, cursor=value):
                    self.assert_error(route, {**selector, 'after': value})
                    self.assert_error(route, selector, {'Last-Event-ID': value})
            self.assert_error(route, list(selector.items()) + [('after', '0'), ('after', '1')])
            self.assert_error(route, {**selector, 'unexpected': 'value'})
        self.assert_error('tasks/events/stream', {'taskId': 'missing'})
        self.assert_error('projects/p/assistant/missing/events/stream')

    def test_terminal_state_gap_does_not_lose_the_final_event(self):
        manager = self.server.control.manager
        entered, finish = self.release(), self.release()
        original = manager._event
        def delayed(task_id, kind, payload):
            if kind == 'task.completed':
                entered.set(); finish.wait(3)
            return original(task_id, kind, payload)
        with patch.object(manager, '_event', side_effect=delayed):
            task_id, _ = self.start(manager)
            self.assertTrue(entered.wait(3))
            self.assertEqual(manager.get(task_id)['status'], 'completed')
            rows, errors, done = [], [], threading.Event()
            def observe():
                try:
                    with self.open('tasks/events/stream', {'taskId': task_id}) as response:
                        rows.extend(self.all(response))
                except Exception as error: errors.append(error)
                finally: done.set()
            observer = threading.Thread(target=observe, daemon=True); observer.start()
            self.assertFalse(done.wait(.3), 'stream closed before the final durable event')
            finish.set(); self.assertTrue(done.wait(3)); observer.join(3)
            self.assertFalse(errors)
            self.assertEqual(rows[-1]['type'], 'task.completed')

    def test_all_task_stream_and_disconnect_do_not_cancel_work(self):
        manager = self.server.control.manager
        task_id, release = self.start(manager, blocked=True)
        with self.open('tasks/events/stream') as response:
            row = self.event(response)
            self.assertEqual(row['taskId'], task_id)
        self.assertEqual(manager.get(task_id)['status'], 'running')
        release.set()
        self.wait(lambda: manager.get(task_id)['status'] == 'completed')

    def test_cancelled_job_drains_and_language_run_stream_still_terminates(self):
        manager = self.server.control.manager
        task_id, _ = self.start(manager, blocked=True)
        manager.cancel(task_id)
        with self.open('tasks/events/stream', {'taskId': task_id}) as response:
            rows = self.all(response)
        self.assertEqual(rows[-1]['type'], 'task.cancelled')
        run = self.app.start('p', 1, None, mode='source')
        self.assertEqual(run.result(3), 42)
        with self.open('events/stream', {'projectId': 'p', 'runId': run.id}) as response:
            rows = self.all(response)
        self.assertEqual(rows[-1]['type'], 'run.completed')

    def test_openapi_advertises_activity_streams_and_separate_cursor_domains(self):
        paths = specification()['paths']
        for route in ('/tasks/events/stream', '/projects/{projectId}/assistant/{jobId}/events/stream'):
            operation = paths[route]['get']
            self.assertIn('text/event-stream', operation['responses']['200']['content'])
            self.assertIn('Last-Event-ID', [value['name'] for value in operation['parameters']])
        self.assertIn('cannot be mixed', paths['/tasks/events/stream']['get']['description'])


if __name__ == '__main__':
    unittest.main()
