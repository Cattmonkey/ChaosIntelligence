"""Actual temporary HTTP/SDK/CLI projections; no production state or model calls."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from chaos import Client, Framework, Settings
from chaos.server import make_server
from test_ui_network_contracts import LEGACY

ROOT = Path(__file__).resolve().parents[1]


class RuntimeViewAPI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.app = Framework(folder / 'state.sqlite3', settings=Settings(':memory:'))
        self.addCleanup(self.app.close)
        self.server = make_server('127.0.0.1', 0, framework=self.app,
            state_dir=folder / 'control', update_home=folder / 'updates')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = Client(self.origin)
        source = 'async fn work(x){await sleep(0.001);x*2};await gather([work(2),work(3)])'
        project = self.app.create_project('p', source=source, capabilities=[])
        run = self.app.start('p', project['version'], None, mode='source')
        self.assertEqual(run.result(5), [4, 6])
        self.run_id = run.id
    def stop_server(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
    def stable(self, value):
        return {key: item for key, item in value.items() if key != 'snapshotAt'}
    def request(self, suffix, method='GET'):
        request = urllib.request.Request(self.origin + suffix, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_graph_assets_are_served_with_exact_packaged_bytes_and_mime(self):
        for name, mime in (('runtime-network.js', 'text/javascript'), ('runtime-network.css', 'text/css')):
            with urllib.request.urlopen(self.origin + '/' + name, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertEqual(hashlib.sha256(response.read()).digest(), hashlib.sha256((ROOT / 'web' / name).read_bytes()).digest())

    def test_real_sdk_http_project_and_run_views_share_committed_identity_and_cursor(self):
        for run_id in (None, self.run_id):
            expected = self.app.runtime_view('p', run_id=run_id)
            actual = self.client.runtime_view('p', run_id=run_id)
            self.assertEqual(self.stable(actual), self.stable(expected))
            self.assertEqual(actual['projectId'], 'p')
            self.assertEqual(actual['runId'], run_id)
            self.assertGreater(actual['eventCursor'], 0)
            self.assertTrue(any(node['kind'] == 'run' and node['status'] == 'completed' for node in actual['nodes']))
            self.assertTrue(any(node['kind'] == 'task' for node in actual['nodes']))
            ids = {node['id'] for node in actual['nodes']}
            self.assertTrue(all(edge['source'] in ids and edge['target'] in ids for edge in actual['edges']))
        process = subprocess.run([sys.executable, '-m', 'chaos', 'ui', 'runtime', 'p',
            '--server', self.origin, '--run-id', self.run_id], cwd=ROOT,
            capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        cli = json.loads(process.stdout)
        self.assertEqual(self.stable(cli), self.stable(self.app.runtime_view('p', run_id=self.run_id)))

    def test_cross_project_closed_query_wrong_method_and_openapi_contract(self):
        self.app.create_project('other', source='42', capabilities=[])
        prefix = '/api/v1/projects/'
        code, _ = self.request(prefix + 'other/runtime-view?runId=' + self.run_id)
        self.assertIn(code, (403, 404))
        for suffix in ('?unexpected=1', '?runId=' + self.run_id + '&runId=' + self.run_id):
            code, _ = self.request(prefix + 'p/runtime-view' + suffix)
            self.assertEqual(code, 400)
        code, _ = self.request(prefix + 'p/runtime-view', method='POST')
        self.assertEqual(code, 405)
        code, specification = self.request('/api/v1/openapi.json')
        self.assertEqual(code, 200)
        self.assertIn('RuntimeView', specification['components']['schemas'])
        path = specification['paths']['/projects/{projectId}/runtime-view']
        self.assertEqual(path['get']['operationId'], 'runtimeView')

    def test_new_native_ui_and_legacy_definitions_share_actual_ui_api(self):
        source = 'root=Stack([RuntimeGraph("Actual state"),NetworkGraph("Plan",[{"id":"a","label":"One"}],[])])'
        definition = self.client.ui_compile(source, title='Runtime page')
        self.assertEqual(definition, Framework.ui_compile(source, title='Runtime page'))
        schema = self.client.ui_schema()
        self.assertEqual(definition['libraryHash'], schema['libraryHash'])
        self.assertIn('RuntimeGraph', schema['libraryJSONSchema']['$defs'])
        self.assertIn('NetworkGraph', schema['libraryJSONSchema']['$defs'])
        self.assertTrue(self.client.ui_validate(definition)['passed'])
        program = 'ui_from_openui(' + json.dumps(source) + ',title="Runtime page")'
        self.assertEqual(Framework.evaluate('ui_definition(' + program + ')'), definition)
        old = json.loads(json.dumps(LEGACY))
        self.assertTrue(self.client.ui_validate(old)['passed'])
        exported = self.client.ui_export(old, format='json')
        self.assertEqual(json.loads(exported['content']), old)
        self.assertEqual(self.client.ui_export(old, format='openui')['content'], old['source'])
        self.assertEqual(old, LEGACY)
