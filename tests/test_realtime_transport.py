"""Exercise the shipped durable-event browser transport with exact JSON IDs."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
class RealtimeTransportTests(unittest.TestCase):
    def test_consumption_replay_and_lifecycle_contract(self):
        result = subprocess.run(
            [shutil.which('node'), str(ROOT / 'tests/realtime_transport_controls.cjs'), str(ROOT)],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['cases'], 8)


class LiveProgressExampleTests(unittest.TestCase):
    def test_example_publishes_actual_partial_values_and_final_output(self):
        from chaos import Framework, Settings
        source = (ROOT / 'examples/live-progress.chaos').read_text(encoding='utf-8')
        with Framework(settings=Settings(':memory:')) as app:
            project = app.create_project('progress', source=source)
            run = app.start(project['id'], project['version'], None, mode='source')
            self.assertEqual(run.result(timeout=5), {'results': [1, 4, 9], 'total': 3})
            events = app.events('progress', run_id=run.id)['items']
            progress = [event['payload'] for event in events if event['type'] == 'bot.progress']
            self.assertEqual([item['kind'] for item in progress], ['plan', 'progress', 'progress', 'progress', 'result'])
            self.assertEqual([item['data']['results'] for item in progress[1:4]], [[1], [1, 4], [1, 4, 9]])


if __name__ == '__main__':
    unittest.main()
