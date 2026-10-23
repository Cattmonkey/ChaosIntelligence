"""Selective HTML work preserves fresh, globally validated documentation."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from markdown_it.renderer import RendererHTML

from chaos.documentation import Documentation, DocumentationError


def fixture(root):
    docs = root / 'docs'
    examples = root / 'examples'
    for directory in (docs, examples):
        directory.mkdir()
    entries = [
        {'id': identifier, 'path': filename, 'title': title,
         'section': 'Fixture', 'description': 'Fresh documentation'}
        for identifier, filename, title in (
            ('overview', 'README.md', 'Home'),
            ('language', 'LANGUAGE.md', 'Language'),
            ('http-api', 'HTTP_API.md', 'HTTP API'))]
    (docs / 'catalog.json').write_text(json.dumps({'schemaVersion': 1, 'pages': entries}), encoding='utf-8')
    (docs / 'README.md').write_text(
        '# Home\n\n[Language](LANGUAGE.md#functions)\n\n'
        '[Example](../examples/demo.chaos)\n\n[App](/control)\n\n'
        '[Remote](https://example.invalid/docs)\n\n'
        '![Inert image](https://example.invalid/image.png)\n\n'
        '```chaos\nfn answer(){42};answer()\n```\n', encoding='utf-8')
    (docs / 'LANGUAGE.md').write_text('# Language\n\n## Functions\n\n## Functions\n', encoding='utf-8')
    (docs / 'HTTP_API.md').write_text('# HTTP API\n', encoding='utf-8')
    (examples / 'demo.chaos').write_text('42', encoding='utf-8')
    return Documentation(docs, examples, root / 'web')


@contextmanager
def rendered_pages():
    titles = []
    original = RendererHTML.render

    def render(renderer, tokens, options, env):
        titles.append(next(token.content for token in tokens if token.type == 'inline'))
        return original(renderer, tokens, options, env)

    with patch.object(RendererHTML, 'render', render):
        yield titles


class SelectiveDocumentationRendering(unittest.TestCase):
    def test_each_page_is_identical_to_full_snapshot_in_all_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            snapshot = docs.snapshot()
            for row in snapshot['pages']:
                with self.subTest(page=row['id']):
                    self.assertEqual(docs.page(row['id']), row)
            api = docs.page('http-api')
            self.assertIn('documentationCatalog', api['markdown'])
            self.assertIn('documentationCatalog', api['html'])

    def test_page_loads_all_documents_but_renders_only_its_target(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            with patch.object(docs, '_load', wraps=docs._load) as load, rendered_pages() as rendered:
                page = docs.page('language')
            self.assertEqual(rendered, ['Language'])
            self.assertEqual([call.args[0]['id'] for call in load.call_args_list],
                             ['overview', 'language', 'http-api'])
            self.assertIn('h-functions-2', page['html'])

    def test_catalog_has_identical_metadata_without_html_rendering(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            expected = docs.snapshot()
            expected['pages'] = [{key: value for key, value in row.items()
                                  if key not in ('markdown', 'html')}
                                 for row in expected['pages']]
            with rendered_pages() as rendered:
                catalog = docs.catalog()
            self.assertEqual(rendered, [])
            self.assertEqual(catalog, expected)

    def test_public_live_and_static_snapshots_still_render_every_page(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            for static in (False, True):
                with self.subTest(static=static), rendered_pages() as rendered:
                    snapshot = docs.snapshot(static=static)
                self.assertEqual(rendered, ['Home', 'Language', 'HTTP API'])
                self.assertTrue(all(row['html'] for row in snapshot['pages']))
                self.assertEqual(snapshot['mode'], 'static' if static else 'live')
                first = snapshot['pages'][0]
                self.assertIn('examples/demo.chaos' if static else '/docs/files/examples/demo.chaos', first['html'])
                self.assertIn('http://127.0.0.1:8765/control' if static else 'href="/control"', first['html'])
                self.assertNotIn('<img', first['html'])

    def test_unselected_bad_links_and_headings_are_still_rejected(self):
        bodies = (
            ('[bad](MISSING.md)', 'Unregistered'),
            ('[bad](README.md#absent)', 'Missing heading'),
            ('[bad](https://name:secret@example.invalid/docs)', 'Unsafe'),
            ('[bad](README.md?query=1)', 'query strings'))
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            for body, message in bodies:
                (docs.root / 'LANGUAGE.md').write_text('# Language\n## Functions\n' + body, encoding='utf-8')
                for request in (lambda: docs.page('overview'), docs.catalog, lambda: docs.page('missing')):
                    with self.subTest(body=body, request=request), self.assertRaisesRegex(DocumentationError, message):
                        request()

    def test_unselected_missing_or_unsafe_catalog_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            path = docs.root / 'catalog.json'
            catalog = json.loads(path.read_text(encoding='utf-8'))
            for invalid in ('MISSING.md', '../OUTSIDE.md'):
                catalog['pages'][1]['path'] = invalid
                path.write_text(json.dumps(catalog), encoding='utf-8')
                for request in (lambda: docs.page('overview'), docs.catalog):
                    with self.subTest(path=invalid), self.assertRaises(DocumentationError):
                        request()

    def test_unknown_identifier_validates_then_raises_keyerror_without_rendering(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            for identifier in ('missing', ['not-hashable']):
                with self.subTest(identifier=identifier), rendered_pages() as rendered:
                    with self.assertRaises(KeyError) as raised:
                        docs.page(identifier)
                self.assertEqual(raised.exception.args, (identifier,))
                self.assertEqual(rendered, [])

    def test_source_catalog_and_generated_reference_changes_are_read_immediately(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            original = docs.page('language')
            (docs.root / 'LANGUAGE.md').write_text('# Language\n## Functions\nFresh paragraph.\n', encoding='utf-8')
            changed = docs.page('language')
            self.assertNotEqual(changed, original)
            self.assertIn('Fresh paragraph.', changed['html'])
            self.assertIn('Fresh paragraph.', docs.catalog()['pages'][1]['searchText'])
            catalog_path = docs.root / 'catalog.json'
            catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
            catalog['pages'][1]['title'] = 'Updated title'
            catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
            self.assertEqual(docs.page('language')['title'], 'Updated title')
            with patch('chaos.documentation.api_reference', return_value='\nGenerated fresh A\n'):
                self.assertIn('Generated fresh A', docs.page('http-api')['html'])
            with patch('chaos.documentation.api_reference', return_value='\nGenerated fresh B\n'):
                self.assertIn('Generated fresh B', docs.page('http-api')['html'])
            (docs.root / 'LANGUAGE.md').write_text('# Language\n## Renamed\n', encoding='utf-8')
            with self.assertRaisesRegex(DocumentationError, 'Missing heading'):
                docs.page('http-api')

    def test_mutating_returned_objects_cannot_poison_future_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            expected_page = docs.page('language')
            expected_catalog = docs.catalog()
            page = docs.page('language')
            page['headings'][0]['title'] = 'Poisoned'
            page['headings'].clear()
            page['html'] = 'Poisoned'
            catalog = docs.catalog()
            catalog['pages'][1]['headings'][0]['title'] = 'Poisoned'
            catalog['pages'].clear()
            self.assertEqual(docs.page('language'), expected_page)
            self.assertEqual(docs.catalog(), expected_catalog)

    def test_work_scales_with_requested_html_even_beyond_presentation_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = fixture(Path(temp))
            path = docs.root / 'catalog.json'
            catalog = json.loads(path.read_text(encoding='utf-8'))
            for index in range(270):
                identifier = 'additional-' + str(index)
                filename = identifier + '.md'
                catalog['pages'].append({'id': identifier, 'path': filename, 'title': identifier,
                                         'section': 'Fixture', 'description': 'Additional code'})
                (docs.root / filename).write_text('# ' + identifier + '\n```chaos\n' + str(index) + '\n```\n', encoding='utf-8')
            path.write_text(json.dumps(catalog), encoding='utf-8')
            with rendered_pages() as rendered:
                self.assertEqual(len(docs.catalog()['pages']), 273)
                docs.page('language')
                docs.page('language')
            self.assertEqual(rendered, ['Language', 'Language'])


if __name__ == '__main__':
    unittest.main()
