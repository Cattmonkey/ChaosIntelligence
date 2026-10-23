"""Real stdio client verifies language-service and HTTP/SDK consistency."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Framework, Settings
from chaos.client import ConflictError
from chaos.documentation import Documentation
from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
from chaos.server import make_server


ROOT = Path(__file__).resolve().parent.parent


class StdioClient:
    def __init__(self, root):
        self.process = subprocess.Popen([sys.executable, '-m', 'chaos', 'lsp', '--stdio',
                                         '--root', str(root), '--entry', 'main.chaos'],
                                        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        self.messages = queue.Queue()
        self.sequence = 0
        self.notifications = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            result = self.request('initialize', {'processId': os.getpid(), 'rootUri': root.as_uri(),
                                                'capabilities': {}})
            self.capabilities = result['capabilities']
            self.send('initialized', {})
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=5)
            self.reader.join(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
            raise

    def _read(self):
        try:
            while True:
                headers = {}
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        raise EOFError('language server stdout closed')
                    if line == b'\r\n':
                        break
                    key, value = line.decode('ascii').split(':', 1)
                    headers[key.lower()] = value.strip()
                size = int(headers['content-length'])
                body = bytearray()
                while len(body) < size:
                    part = self.process.stdout.read(size - len(body))
                    if not part:
                        raise EOFError('incomplete language-server frame')
                    body.extend(part)
                self.messages.put(json.loads(body.decode('utf-8')))
        except BaseException as error:
            self.messages.put(error)

    def send(self, method, params=None, request_id=None):
        value = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            value['params'] = params
        if request_id is not None:
            value['id'] = request_id
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.process.stdin.write(('Content-Length: %d\r\n\r\n' % len(body)).encode() + body)
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.sequence += 1
        self.send(method, params, self.sequence)
        while True:
            message = self.messages.get(timeout=10)
            if isinstance(message, BaseException):
                raise message
            if message.get('id') == self.sequence:
                if 'error' in message:
                    raise AssertionError(message['error'])
                return message['result']
            self.notifications.append(message)

    def close(self):
        try:
            if self.process.poll() is None:
                self.request('shutdown')
                self.send('exit')
                self.process.wait(timeout=5)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
            self.reader.join(timeout=2)


class LanguageServerIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.main = 'import "lib.chaos" as L;L.answer(20)'
        self.lib = '// 🧠\r\nfn answer(value:Int)->Int{value+22};export{answer}'
        (self.root/'main.chaos').write_text(self.main)
        (self.root/'lib.chaos').write_text(self.lib)

    def client(self):
        client = StdioClient(self.root)
        self.addCleanup(client.close)
        return client

    def open(self, client, name, source, version=1):
        uri = (self.root/name).as_uri()
        client.send('textDocument/didOpen', {'textDocument': {
            'uri': uri, 'languageId': 'chaos', 'version': version, 'text': source}})
        return uri

    def test_stdio_definition_uses_unsaved_target_and_does_not_write_files(self):
        client = self.client()
        draft = '// draft 🧠\n\nfn answer(value:Int)->Int{value+22};export{answer}'
        self.open(client, 'lib.chaos', draft)
        uri = self.open(client, 'main.chaos', self.main)
        request = {'textDocument': {'uri': uri}, 'position': {'line': 0, 'character': self.main.index('answer')+2}}
        definition = client.request('textDocument/definition', request)
        if isinstance(definition, list):
            self.assertEqual(len(definition), 1)
            definition = definition[0]
        self.assertEqual(definition['uri'], (self.root/'lib.chaos').as_uri())
        self.assertEqual(definition['range'], {'start': {'line': 2, 'character': 3}, 'end': {'line': 2, 'character': 9}})
        self.assertEqual((self.root/'lib.chaos').read_bytes(), self.lib.encode())
        self.assertFalse((self.root/'.chaos-workspace.json').exists())
        self.assertEqual(client.capabilities['textDocumentSync']['change'], 2)

    def test_real_completion_hover_semantic_tokens_formatting_and_symbols(self):
        client = self.client()
        source = '// readable 🧠\nfn add(x:Int,by:Int=2)->Int{x+by};add(40)'
        uri = self.open(client, 'main.chaos', source)
        text_document = {'textDocument': {'uri': uri}}
        hover = client.request('textDocument/hover', dict(text_document, position={'line': 1, 'character': 36}))
        self.assertIsNotNone(hover)
        tokens = client.request('textDocument/semanticTokens/full', text_document)
        self.assertGreater(len(tokens['data']), 5)
        self.assertEqual(len(tokens['data']) % 5, 0)
        symbols = client.request('textDocument/documentSymbol', text_document)
        self.assertTrue(any(s['name'] == 'add' for s in symbols))
        edits = client.request('textDocument/formatting', dict(text_document, options={'tabSize': 2, 'insertSpaces': True}))
        self.assertEqual(len(edits), 1)
        formatted = edits[0]['newText']
        self.assertEqual(formatted, Framework.format_source(source))
        self.assertEqual(Framework.evaluate(formatted), 42)
        self.assertIn('// readable 🧠', formatted)
        source += ';ad'
        client.send('textDocument/didChange', {
            'textDocument': {'uri': uri, 'version': 2}, 'contentChanges': [{'text': source}]})
        completions = client.request('textDocument/completion', dict(text_document, position={'line': 1, 'character': len(source.split('\n')[1])}))
        items = completions.get('items', []) if isinstance(completions, dict) else completions
        self.assertTrue(any(item['label'] == 'add' for item in items))

    def test_http_sdk_definition_shared_and_project_revision_unchanged(self):
        app = Framework(settings=Settings(':memory:'))
        self.addCleanup(app.close)
        app.create_project('language', source=self.main, files={'lib.chaos': self.lib})
        server = make_server(port=0, framework=app, state_dir=self.root/'control', update_home=self.root/'updates')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown(); server.server_close(); thread.join(3)
        self.addCleanup(stop)
        client = Client('http://127.0.0.1:'+str(server.server_port))
        draft = '// draft 🧠\n\nfn answer(value:Int)->Int{value+22};export{answer}'
        files = {'main.chaos': self.main, 'lib.chaos': draft}
        position = self.main.index('answer')+2
        direct = Framework.analyze(self.main, files=files, position=position)
        remote = client.analyze(self.main, files=files, position=position)
        self.assertEqual(remote, direct)
        start = len(draft[:draft.index('answer')].encode('utf-16-le')) // 2
        self.assertEqual(remote['definition'], {'file': 'lib.chaos', 'from': start, 'to': start+6})
        self.assertEqual(app.get_project('language')['version'], 1)
        self.assertEqual(client.start('language', 1, None, mode='source').result(5), 42)
        newer = client.edit_files('language', 1, 'create', 'extra.chaos', source='42')
        self.assertEqual(newer['version'], 2)
        with self.assertRaises(ConflictError):
            client.edit_files('language', 1, 'delete', 'extra.chaos')

    def test_documentation_and_cli_help_are_installed_contracts(self):
        page = Documentation().page('language-server')
        self.assertIn('chaos lsp', page['markdown'])
        self.assertIn('language-server', LANGUAGE_REFERENCE_TOPICS)
        help_result = subprocess.run([sys.executable, '-m', 'chaos', 'lsp', '--help'], cwd=ROOT,
                                     capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for flag in ('--stdio', '--root', '--entry', '--language-version'):
            self.assertIn(flag, help_result.stdout)
