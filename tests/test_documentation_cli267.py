"""The installed reference is usable offline, including the minimal runtime."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chaos.documentation import Documentation, DocumentationError, main

ROOT = Path(__file__).resolve().parents[1]


class DocumentationCLITests(unittest.TestCase):
    def invoke_isolated(self, arguments):
        # -I -S disables installed libraries. Only the exact runtime is mounted.
        script = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from chaos.cli import main; raise SystemExit(main(sys.argv[1:]))'
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CHAOS_', 'OPENAI_'))}
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(ROOT), *arguments],
                                    env=env, cwd=directory, capture_output=True, text=True, timeout=20)
            self.assertEqual(list(Path(directory).iterdir()), [])
        return result

    def test_installed_list_and_show_need_neither_model_nor_html_dependencies(self):
        listed = self.invoke_isolated(['docs', 'list'])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        topics = json.loads(listed.stdout)['pages']
        self.assertIn('command-line', [row['id'] for row in topics])
        shown = self.invoke_isolated(['docs', 'show', 'command-line'])
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout, Documentation().source('command-line')['markdown'])

    def test_source_and_html_use_same_registered_reference_and_generated_api(self):
        docs = Documentation()
        for topic in ('command-line', 'http-api'):
            self.assertEqual(docs.source(topic)['markdown'], docs.page(topic)['markdown'])
        with patch('chaos.documentation.markdown_parser', side_effect=AssertionError('no rendering')):
            self.assertIn('Chaos', docs.source('command-line')['markdown'])

    def test_unregistered_and_path_escape_topics_fail_without_reading_files(self):
        for topic in ('no-such-topic', '../settings.json', '/etc/passwd'):
            with self.subTest(topic=topic):
                with self.assertRaises(DocumentationError): Documentation().source(topic)
                with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(main(['show', topic]), 1)
                self.assertEqual(out.getvalue(), '')
                self.assertIn('Unknown documentation topic', json.loads(err.getvalue())['error'])

    def test_usage_errors_validate_before_loading_documentation(self):
        for arguments in (['show'], ['list', 'language'], ['show', 'language', '--output', 'unexpected']):
            with self.subTest(arguments=arguments), patch('chaos.documentation.Documentation') as docs, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_: main(arguments)
                self.assertEqual(exit_.exception.code, 2)
                docs.assert_not_called()

    def test_runaway_documentation_example_is_still_bounded_and_identified(self):
        from test_documentation import fixture
        from chaos.language import ResourceLimit
        with tempfile.TemporaryDirectory() as directory:
            docs = fixture(Path(directory))
            page = docs.root / 'README.md'
            page.write_text(page.read_text().replace('input + 1', 'while true { 0 }'))
            with self.assertRaisesRegex(DocumentationError, 'Example increment in README.md failed') as failure:
                docs.check()
            self.assertIsInstance(failure.exception.__cause__, ResourceLimit)


if __name__ == '__main__': unittest.main()
