"""Real loopback MCP HTTP -> native Computer -> journal/images/public SSE.

No mocked transport, hardware, browser, model or production state is used.
"""
import base64
import copy
import hashlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from chaos import Client, Framework, Registry, Settings
from chaos.client import ValidationError
from chaos.mobile_mcp import ELEMENTS_PREFIX, mobile_mcp_computer
from chaos.server import make_server


SOURCE = '''let device = await computer_attach("phone");
let before = await computer_observe(device);
let target = observation_info(before).targets[0];
let after = await computer_act(device, computer_action(before, {action:"click",ref:target.ref}));
await computer_close(device);
{before:observation_info(before),after:observation_info(after)}'''


class MobileHTTPFixture:
    def __init__(self, png):
        self.png = base64.b64encode(png).decode('ascii')
        self.mode = 'json'
        self.calls = []
        self.clicks = 0
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with fixture.lock:
                    fixture.calls.append({'request': copy.deepcopy(request),
                                          'authorization': self.headers.get('Authorization')})
                if self.path != '/mcp':
                    self.send_error(404)
                    return
                if request['method'] == 'notifications/initialized':
                    self.send_response(202)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if request['method'] == 'initialize':
                    result = {'protocolVersion': '2025-11-25',
                              'serverInfo': {'name': 'mobile-mcp', 'version': 'isolated-fixture'}}
                elif request['method'] == 'tools/call':
                    params = request['params']
                    result = fixture.tool(params['name'], params['arguments'])
                else:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': 'unsupported method'}]}
                raw = json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}).encode()
                content_type = 'application/json'
                if fixture.mode == 'sse':
                    content_type = 'text/event-stream'
                    raw = b': loopback fixture\n\nevent: message\ndata: ' + raw + b'\n\n'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = 'http://127.0.0.1:%d/mcp' % self.server.server_port

    def tool(self, name, arguments):
        def text(value):
            return {'content': [{'type': 'text', 'text': value}]}
        if name == 'mobile_list_available_devices':
            return text(json.dumps({'devices': [{'id': 'fixture-phone-a', 'state': 'online',
                         'platform': 'android', 'type': 'emulator'}]}))
        if arguments.get('device') != 'fixture-phone-a':
            return {'isError': True, 'content': [{'type': 'text', 'text': 'foreign device'}]}
        if name == 'mobile_get_screen_size':
            return text('Screen size is 390x844 pixels')
        if name == 'mobile_list_elements_on_screen':
            return text(ELEMENTS_PREFIX + json.dumps([{'ref': '@native-go', 'type': 'Button',
                'text': 'Clicked once' if self.clicks else 'Go',
                'coordinates': {'x': 10, 'y': 20, 'width': 100, 'height': 40}}]))
        if name == 'mobile_take_screenshot':
            return {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': self.png}]}
        if name == 'mobile_click_on_screen_at_coordinates' and arguments == {
                'ref': '@native-go', 'device': 'fixture-phone-a'}:
            with self.lock:
                self.clicks += 1
            return text('Fixture click completed; this text is not parsed as a command')
        return {'isError': True, 'content': [{'type': 'text', 'text': 'unsupported tool/arguments'}]}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'optional Pillow image extra unavailable')
class MobileComputerHTTPIntegration(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        output = BytesIO()
        with Image.new('RGB', (80, 120), (20, 80, 160)) as image:
            image.save(output, format='PNG')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = MobileHTTPFixture(output.getvalue())
        self.addCleanup(self.fixture.close)
        self.env = patch.dict(os.environ, {'CHAOS_MOBILE_HTTP_FIXTURE_TOKEN': 'fixture-only-token'})
        self.env.start()
        self.addCleanup(self.env.stop)
        registry = Registry()
        registry.register_computer(mobile_mcp_computer('phone', self.fixture.endpoint,
            'fixture-phone-a', token_env='CHAOS_MOBILE_HTTP_FIXTURE_TOKEN', timeout=3))
        self.assertEqual(self.fixture.calls, [])
        settings = Settings(':memory:')
        settings.update({'runtime': {'workspace': self.temp.name, 'timeout': 15}}, 1)
        self.app = Framework(settings=settings, registry=registry)
        self.addCleanup(self.app.close)
        self.server = make_server(port=0, framework=self.app,
            state_dir=Path(self.temp.name) / 'control', update_home=Path(self.temp.name) / 'updates')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = 'http://127.0.0.1:%d' % self.server.server_port
        self.client = Client(self.origin, timeout=10)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def check_pipeline(self, mode):
        from PIL import Image
        self.fixture.mode = mode
        project = self.client.create_project('mobile_http', source=SOURCE, capabilities=['computer.control'])
        run = self.client.start(project['id'], project['version'], None, mode='source')
        result = run.result(12)
        before, after = result['before'], result['after']
        self.assertEqual(self.fixture.clicks, 1)
        self.assertEqual(before['backend'], 'registered')
        self.assertEqual(before['binding'], 'phone')
        self.assertEqual(before['viewport'], {'width': 390, 'height': 844})
        self.assertEqual(before['targets'][0]['ref'], 'e1')
        self.assertEqual(after['targets'][0]['text'], 'Clicked once')
        self.assertGreater(after['snapshotId'], before['snapshotId'])
        self.assertEqual(after['computerId'], before['computerId'])
        self.assertEqual(after['receipt']['status'], 'completed')
        self.assertNotIn('fixture-phone-a', json.dumps(result))
        self.assertNotIn('fixture-only-token', json.dumps(result))
        history = self.client.computer_history(project['id'], run_id=run.id, limit=100)
        self.assertEqual(history, self.app.computer_history(project['id'], run_id=run.id, limit=100))
        observations = [row for row in history['items'] if row['kind'] == 'observation']
        actions = [row for row in history['items'] if row['kind'] == 'action']
        self.assertEqual([row['id'] for row in observations], [before['id'], after['id']])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['status'], 'completed')
        self.assertEqual(actions[0]['data']['resultObservationId'], after['id'])
        self.assertEqual(after['receipt']['id'], actions[0]['id'])
        for observation in (before, after):
            self.assertEqual(self.client.computer_record(project['id'], observation['id'])['data'], observation)
            for variant in ('preview', 'model'):
                encoded = self.client.computer_image(project['id'], observation['id'], variant=variant)
                self.assertEqual(encoded, self.app.computer_image(project['id'], observation['id'], variant=variant))
                raw = base64.b64decode(encoded['data'], validate=True)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), observation['image'][variant]['sha256'])
                with Image.open(BytesIO(raw)) as image:
                    image.load()
                    self.assertEqual(image.size, (encoded['width'], encoded['height']))
                    self.assertEqual(image.format, 'JPEG')
        query = urllib.parse.urlencode({'projectId': project['id'], 'runId': run.id, 'after': 0})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.origin + '/api/v1/events/stream?' + query, timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), 'text/event-stream')
            streamed = [json.loads(line[6:]) for line in response.read().decode().splitlines()
                        if line.startswith('data: ')]
        events = self.client.events(project['id'], run_id=run.id, limit=300)['items']
        self.assertEqual([row['id'] for row in streamed], [row['id'] for row in events])
        self.assertEqual(streamed[-1]['type'], 'run.completed')
        types = [row['type'] for row in streamed]
        for kind in ('computer.driver.started', 'computer.driver.completed', 'computer.observation', 'computer.completed'):
            self.assertIn(kind, types)
        self.assertTrue(all(row['run_id'] == run.id for row in streamed))
        self.assertFalse(any(row['type'].startswith('agent.') or row['type'] == 'tool.started' for row in events))
        calls = self.fixture.calls
        self.assertTrue(all(row['authorization'] == 'Bearer fixture-only-token' for row in calls))
        self.assertEqual([row['request']['method'] for row in calls[:2]], ['initialize', 'notifications/initialized'])
        tools = [row['request']['params'] for row in calls if row['request']['method'] == 'tools/call']
        clicks = [row for row in tools if row['name'] == 'mobile_click_on_screen_at_coordinates']
        self.assertEqual(clicks, [{'name': 'mobile_click_on_screen_at_coordinates',
                                  'arguments': {'ref': '@native-go', 'device': 'fixture-phone-a'}}])
        self.assertEqual(sum(row['name'] == 'mobile_take_screenshot' for row in tools), 2)

    def test_json_mcp_native_computer_journal_images_and_public_sse(self):
        self.check_pipeline('json')

    def test_sse_mcp_native_computer_journal_images_and_public_sse(self):
        self.check_pipeline('sse')

    def test_registered_sdk_and_cli_validation_use_real_http_without_device_io(self):
        spec = {'action': 'input', 'ref': 'e1', 'text': ' appended'}
        self.assertEqual(self.client.validate_computer_action(spec, registered=True), spec)
        with self.assertRaises(ValidationError):
            self.client.validate_computer_action(spec)
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                   CHAOS_CONFIG=str(Path(self.temp.name) / 'cli-settings.json'))
        base = [sys.executable, '-B', '-m', 'chaos', 'computer', 'validate',
                '--spec-json', json.dumps(spec), '--server', self.origin]
        result = subprocess.run(base + ['--registered'], cwd=root, env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), spec)
        denied = subprocess.run(base, cwd=root, env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(self.fixture.calls, [])
