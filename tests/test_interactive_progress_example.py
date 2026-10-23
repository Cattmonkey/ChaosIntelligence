"""Execute the shipped tutorial with its actual Run host and HTTP guidance API."""
from pathlib import Path
import tempfile
import threading
import unittest

from chaos import Client, Framework, Settings
from chaos.server import make_server

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'examples/interactive-progress.chaos').read_text(encoding='utf-8')


class InteractiveProgressExample(unittest.TestCase):
    def test_http_guidance_preserves_data_and_progress_precedes_completion(self):
        with tempfile.TemporaryDirectory() as temporary, Framework(settings=Settings(':memory:')) as app:
            project = app.create_project('tutorial', source=SOURCE, capabilities=[])
            root = Path(temporary)
            server = make_server(port=0, framework=app, state_dir=root/'state', update_home=root/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Client('http://127.0.0.1:' + str(server.server_port))
                run = client.start(project['id'], project['version'], {'waitSeconds': 10}, mode='source')
                waiting = False
                for event in run.watch(timeout=5):
                    if event['type'] == 'bot.progress' and 'waitSeconds' in event['payload']['data']:
                        waiting = True
                        break
                self.assertTrue(waiting, 'The public waiting stage must be observable before final output')
                self.assertEqual(run.get()['status'], 'running')
                guidance = {'text': '先验收，再继续', 'opaque': ['no keyword execution', 42]}
                receipt = run.guide(guidance, message_id='tutorial-guide')
                self.assertEqual(receipt['runId'], run.id)
                self.assertIn(receipt['status'], ('queued', 'received'))
                self.assertEqual(run.result(timeout=5), {
                    'received': True, 'guidance': guidance, 'squares': [1, 4, 9]})
                events = client.events(project['id'], run_id=run.id, limit=300)['items']
                progress = [event for event in events if event['type'] == 'bot.progress']
                self.assertEqual([event['payload']['kind'] for event in progress],
                                 ['plan', 'progress', 'progress', 'progress', 'plan', 'result'])
                self.assertEqual([event['payload']['data']['squares'] for event in progress[1:4]],
                                 [[1], [1, 4], [1, 4, 9]])
                received = next(event for event in events if event['type'] == 'guidance.received')
                completed = next(event for event in events if event['type'] == 'run.completed')
                self.assertLess(received['id'], progress[-1]['id'])
                self.assertLess(progress[-1]['id'], completed['id'])
                self.assertEqual(app.get_project(project['id'])['source'], SOURCE)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)

    def test_no_guidance_returns_explicit_empty_result(self):
        with Framework(settings=Settings(':memory:')) as app:
            project = app.create_project('tutorial', source=SOURCE, capabilities=[])
            run = app.start(project['id'], project['version'], {'waitSeconds': 0}, mode='source')
            self.assertEqual(run.result(timeout=5), {
                'received': False, 'guidance': None, 'squares': [1, 4, 9]})
            events = app.events(project['id'], run_id=run.id, limit=300)['items']
            self.assertFalse(any(event['type'].startswith('guidance.') for event in events))


if __name__ == '__main__':
    unittest.main()
