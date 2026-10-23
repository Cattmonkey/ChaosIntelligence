"""LSP text, path, package, and nonexecuting language-service contracts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chaos.ide_language import analyze
from chaos.language import Evaluator, Parser, LanguageError, tokenize
from chaos.program import plain
from chaos.lsp import Job, Server
from chaos.lsp_adapters import TOKEN_TYPES, convert
from chaos.lsp_workspace import TextMap, Workspace, changed_text, root_path, uri_path


class Positions(unittest.TestCase):
    def test_utf16_crlf_cr_and_astral_boundaries(self):
        mapping = TextMap('a🧠\r\nb\rc\n')
        self.assertEqual(mapping.offset({'line': 0, 'character': 3}), 2)
        self.assertEqual(mapping.offset({'line': 0, 'character': 500}), 2)
        self.assertEqual(mapping.offset({'line': 2, 'character': 1}), 7)
        self.assertEqual(mapping.position(5), {'line': 1, 'character': 0})
        self.assertEqual(mapping.position(9), {'line': 3, 'character': 0})
        with self.assertRaises(ValueError):
            mapping.offset({'line': 0, 'character': 2})
        for position in ({'line': -1, 'character': 0}, {'line': 0, 'character': True}, {'line': 9, 'character': 0}):
            with self.assertRaises(ValueError):
                mapping.offset(position)
        self.assertEqual(list(mapping.pieces(0, 9)), [(0, 0, 3), (1, 0, 1), (2, 0, 1)])

    def test_successive_edits_are_transactional_and_range_length_uses_utf16(self):
        source = 'a🧠\r\nb'
        edits = [{'range': {'start': {'line': 0, 'character': 1}, 'end': {'line': 0, 'character': 3}}, 'rangeLength': 2, 'text': 'xx'},
                 {'range': {'start': {'line': 0, 'character': 2}, 'end': {'line': 1, 'character': 1}}, 'rangeLength': 4, 'text': 'z'}]
        self.assertEqual(changed_text(source, edits), 'axz')
        with self.assertRaises(ValueError):
            changed_text(source, edits + [{'range': {'start': {'line': 1, 'character': 0}, 'end': {'line': 1, 'character': 0}}, 'text': '!'}])
        self.assertEqual(source, 'a🧠\r\nb')


class LanguageWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = Workspace([self.root])

    def open(self, name, text, version=1):
        uri = (self.root / name).as_uri()
        self.workspace.open({'uri': uri, 'languageId': 'chaos', 'version': version, 'text': text})
        return uri

    def test_dependency_closure_ignores_unrelated_files_and_overlays_win(self):
        for index in range(80):
            (self.root / ('unused%d.chaos' % index)).write_bytes(b'\xff')
        (self.root / 'lib.chaos').write_text('fn answer(){1};export{answer}')
        uri = self.open('main.chaos', 'import "lib.chaos" as L;L.answer()')
        self.open('lib.chaos', 'fn answer(){42};export{answer}')
        self.open('unrelated.chaos', 'import "symlink.chaos" as S;S')
        snapshot = self.workspace.snapshot(uri)
        files, entry = snapshot.load()
        self.assertEqual(set(files), {'main.chaos', 'lib.chaos'})
        self.assertIn('42', files['lib.chaos'])
        self.assertEqual(entry, 'main.chaos')
        self.workspace.close({'uri': (self.root / 'lib.chaos').as_uri()})
        files, _ = self.workspace.snapshot(uri).load()
        self.assertIn('{1}', files['lib.chaos'])

    def test_stale_invalid_changes_preserve_text_and_version(self):
        uri = self.open('main.chaos', 'a🧠')
        for version, edits in ((1, [{'text': 'old'}]), (2, [{'text': 'partial'}, {'range': {'start': {'line': 9, 'character': 0}, 'end': {'line': 9, 'character': 0}}, 'text': 'bad'}]), (3, [{'range': {'start': {'line': 0, 'character': 2}, 'end': {'line': 0, 'character': 3}}, 'text': 'bad'}])):
            with self.assertRaises(ValueError):
                self.workspace.change({'uri': uri, 'version': version}, edits)
            self.assertEqual(self.workspace.documents[uri].text, 'a🧠')
            self.assertEqual(self.workspace.documents[uri].version, 1)
        self.workspace.change({'uri': uri, 'version': 4}, [{'text': '42'}])
        self.assertEqual(self.workspace.documents[uri].text, '42')

    def test_uri_confinement_percent_encoding_and_symlinks(self):
        uri = self.open('space name.chaos', '42')
        self.assertIn('%20', uri)
        for invalid in ('https://example.com/main.chaos', 'file://remote/main.chaos', uri + '?query', uri + '#fragment', uri + '%XX', (self.root.parent / 'outside.chaos').as_uri()):
            with self.subTest(uri=invalid), self.assertRaises(ValueError):
                self.workspace.snapshot(invalid)
        (self.root / 'target.chaos').write_text('42')
        (self.root / 'link.chaos').symlink_to(self.root / 'target.chaos')
        with self.assertRaises(ValueError):
            self.workspace.snapshot((self.root / 'link.chaos').as_uri())
        with tempfile.TemporaryDirectory() as external:
            (Path(external) / 'secret.chaos').write_text('42')
            (self.root / 'linked').symlink_to(external, target_is_directory=True)
            imported = self.open('main.chaos', 'import "linked/secret.chaos" as S;S')
            with self.assertRaises(ValueError):
                self.workspace.snapshot(imported).load()

    def test_ancestor_aliases_and_longest_workspace_root(self):
        # macOS TemporaryDirectory usually exposes /var with canonical /private/var.
        alias = Path(self.temp.name)
        workspace = Workspace([root_path(alias)])
        uri = (alias / 'main.chaos').as_uri()
        self.assertEqual(workspace.locate(uri)[1], self.root)
        nested = self.root / 'nested'
        nested.mkdir()
        workspace = Workspace([self.root, nested])
        self.assertEqual(workspace.locate((nested / 'main.chaos').as_uri())[2], 'main.chaos')
        loop = self.root / 'loop'
        loop.symlink_to(loop)
        # This URI is outside the selected nested root, so alias discovery must
        # reject the loop without leaking pathlib's RuntimeError to the reader.
        with self.assertRaises(ValueError):
            Workspace([nested]).locate((loop / 'main.chaos').as_uri())

    def test_source_bundle_limits_and_unsupported_document_paths(self):
        with self.assertRaises(ValueError):
            self.open('main.chaos', ' ' * 100001)
        for name in ('main.txt', 'std/main.chaos', '.chaos/main.chaos'):
            with self.assertRaises(ValueError):
                self.open(name, '42')
        for index in range(65):
            following = 'import "%d.chaos" as N;N' % (index + 1) if index < 64 else '42'
            (self.root / ('%d.chaos' % index)).write_text(following)
        with self.assertRaises(ValueError):
            self.workspace.snapshot((self.root / '0.chaos').as_uri()).load()

    def test_utf8_byte_bundle_limit_and_invalid_disk_source(self):
        source = ''.join('import "m%d.chaos" as M%d;' % (i, i) for i in range(3))
        uri = self.open('main.chaos', source)
        for index in range(3):
            (self.root / ('m%d.chaos' % index)).write_text('//' + '🧠' * 90000)
        with self.assertRaises(ValueError):
            self.workspace.snapshot(uri).load()
        (self.root / 'm0.chaos').write_bytes(b'\xff')
        with self.assertRaises(ValueError):
            self.workspace.snapshot(uri).load()

    def test_real_signature_semantics_and_no_program_execution(self):
        source = '// 🧠\r\nfn add(a:Int,b:Int=2)->Int{a+b};add(1, '
        uri = self.open('main.chaos', source)
        snapshot = self.workspace.snapshot(uri)
        files, entry = snapshot.load()
        with patch.object(Evaluator, 'run', side_effect=AssertionError('must not execute')), patch.object(Evaluator, 'evaluate', side_effect=AssertionError('must not execute')):
            result = analyze(source, files=files, entry=entry)
            signature = convert('textDocument/signatureHelp', result, snapshot, files)
        self.assertEqual(signature['activeParameter'], 1)
        self.assertEqual(signature['signatures'][0]['parameters'][1]['label'], 'b')
        tokens = convert('textDocument/semanticTokens/full', result, snapshot, files)['data']
        self.assertEqual(tokens[:5], [0, 0, 5, TOKEN_TYPES.index('comment'), 0])
        self.assertEqual(len(tokens) % 5, 0)
        completion = convert('textDocument/completion', result, snapshot, files)
        self.assertTrue(all('textEdit' in item for item in completion['items']))
        unknown_source = 'fn f(a,b){a};f(unknown='
        unknown = analyze(unknown_source)
        signature = convert('textDocument/signatureHelp', unknown, snapshot, files)
        self.assertNotIn('parameters', signature['signatures'][0])
        self.assertNotIn('activeParameter', signature)

    def test_invalid_draft_formatting_returns_no_edit_and_valid_formatting_is_readonly(self):
        uri = self.open('main.chaos', 'let x=')
        server = Server(None, None)
        job = Job(1, 'textDocument/formatting', {'options': {'tabSize': 2, 'insertSpaces': True}}, self.workspace.snapshot(uri))
        with self.assertRaises(LanguageError):
            server._calculate(job)
        self.workspace.change({'uri': uri, 'version': 2}, [{'text': 'fn f(x){x+1};f(1)'}])
        job.snapshot = self.workspace.snapshot(uri)
        edits = server._calculate(job)
        self.assertEqual(len(edits), 1)
        self.assertIn('fn f(x)', edits[0]['newText'])
        self.assertFalse((self.root / 'main.chaos').exists())

    def test_tab_formatting_changes_only_whitespace_tokens(self):
        source = 'fn f(){let text="line\\n  literal"; // comment\ntext};f()'
        uri = self.open('main.chaos', source)
        server = Server(None, None)
        job = Job(1, 'textDocument/formatting', {'options': {'tabSize': 2, 'insertSpaces': False}}, self.workspace.snapshot(uri))
        formatted = server._calculate(job)[0]['newText']
        self.assertIn('\n\tlet text', formatted)
        self.assertIn('line\\n  literal', formatted)
        self.assertEqual(plain(Parser(source).program()), plain(Parser(formatted).program()))
        old = [(t.kind, t.value) for t in tokenize(source, trivia=True) if t.kind not in ('space', 'eof')]
        new = [(t.kind, t.value) for t in tokenize(formatted, trivia=True) if t.kind not in ('space', 'eof')]
        self.assertEqual(old, new)
