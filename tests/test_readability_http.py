"""The editor, HTTP/SDK and CLI use the same readable source contracts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from chaos import Client, Framework, Settings
from chaos.client import ValidationError
from chaos.server import make_server
from chaos.openapi import specification


class ReadabilityHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Framework(settings=Settings(':memory:'))
        self.addCleanup(self.app.close)
        self.server = make_server(port=0, framework=self.app,
                                  state_dir=Path(self.temp.name)/'control',
                                  update_home=Path(self.temp.name)/'updates')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.client = Client('http://127.0.0.1:' + str(self.server.server_port))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_shared_signature_dialect_and_format_options(self):
        source = '// 🙂 retain\nfn add(first:Int,second:Int=2)->Int{first+second};add(40)'
        position = len(source[:-1].encode('utf-16-le')) // 2
        kw = dict(position=position, language_versions={'main.chaos': 2})
        self.assertEqual(self.client.analyze(source, **kw), Framework.analyze(source, **kw))
        self.assertEqual(self.client.analyze(source, **kw)['signatureHelp']['activeParameter'], 0)
        kw = dict(line_width=40, indent_size=4)
        expected = Framework.format_source(source, **kw)
        self.assertEqual(self.client.format_source(source, **kw), expected)
        self.assertIn('// 🙂 retain', expected)
        self.assertIn('\n    ', expected)
        self.assertEqual(Framework.evaluate(expected), 42)
        legacy = 'let xs=[40,2];xs[1]'
        self.assertIn('.[', self.client.format_source(legacy, language_version=1))

    def test_invalid_options_and_draft_leave_project_history_unchanged(self):
        p = self.client.create_project('readability', source='42')
        for kw in ({'line_width': True}, {'line_width': 39}, {'line_width': 241}, {'indent_size': 0}, {'indent_size': 9}, {'language_version': True}):
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                self.client.format_source('42', **kw)
        with self.assertRaises(ValidationError):
            self.client.format_source('fn incomplete(')
        self.assertEqual(self.client.get_project('readability')['version'], p['version'])
        self.assertEqual(len(self.client.program_history('readability')['items']), 1)

    def test_cli_matches_sdk_preserves_comments_and_stdout_only(self):
        source = '# preserve\nfn answer(first,second,third){[first,second,third]};answer("abcdefghijklm", "nopqrstuvwxyz", 42)'
        kw = dict(line_width=40, indent_size=4)
        result = subprocess.run([sys.executable, '-m', 'chaos', 'fmt', '-', '--line-width', '40', '--indent-size', '4'], input=source, text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1], timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.client.format_source(source, **kw))
        self.assertIn('# preserve', result.stdout)

    def test_static_code_assets_and_openapi_describe_actual_optional_fields(self):
        for file in ('/code.js', '/code.css', '/docs/code.js', '/docs/code.css'):
            with urllib.request.urlopen(self.client.base_url + file) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                self.assertGreater(len(response.read()), 100)
        spec = specification()
        schemas = spec['components']['schemas']
        self.assertIn('signatureHelp', schemas['SourceAnalysis']['required'])
        self.assertEqual(schemas['SourceFormatRequest']['properties']['lineWidth']['default'], 88)
        self.assertEqual(schemas['SourceFormatRequest']['properties']['indentSize']['default'], 2)
        self.assertIn('languageVersions', schemas['SourceAnalysisRequest']['properties'])

    def test_cli_formats_single_source_without_import_linking_or_macro_expansion(self):
        for source in ('import "missing.chaos" as M;M.answer', 'macro forever(x){quote{forever!(unquote(x))}};forever!(42)', 'let x={value:42};x["value"]'):
            path = Path(self.temp.name)/'single.chaos'
            path.write_text(source)
            argv = [sys.executable,'-m','chaos','fmt',str(path),'--language-version','1']
            result = subprocess.run(argv,text=True,capture_output=True,cwd=Path(__file__).resolve().parents[1],timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,Framework.format_source(source,file='single.chaos',language_version=1))
            self.assertEqual(path.read_text(),source)
