"""Shared native/OpenUI contracts for static networks and bound runtime views.

No devices, models, servers or browser are started by these pure contract tests.
Legacy fixture was captured from the actual 14-component library before expansion.
"""
import copy
import hashlib
import html
import json
import unittest
from unittest.mock import patch

from chaos import Framework
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.ui_artifacts import UIArtifact, node, prompt


LEGACY_HASH = '53877d77c738ba646bfd355647f489a9671f2bfadf6194a77c3a90888c2a6d1b'
LEGACY = {
 'schema': 'chaos.ui/1', 'kind': 'page', 'title': 'Legacy status',
 'source': 'root = Stack([Metric("Completed", "2", "Recorded snapshot"), Text("Legacy artifact")], "column", "medium")\n',
 'root': {'type': 'element', 'typeName': 'Stack', 'props': {'children': [
  {'type': 'element', 'typeName': 'Metric', 'props': {'label': 'Completed', 'value': '2', 'detail': 'Recorded snapshot'}, 'partial': False},
  {'type': 'element', 'typeName': 'Text', 'props': {'text': 'Legacy artifact'}, 'partial': False}
 ], 'direction': 'column', 'gap': 'medium'}, 'partial': False},
 'libraryHash': LEGACY_HASH,
 'hash': '7d0f22507371646b90239eaf37105a8b4971caa9b326af054c98a20b9155f338'}


def wire_hash(document):
    payload = {key: value for key, value in document.items() if key != 'hash'}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def graph_source(nodes=None, edges=None, *, layout='radial', theme='auto', title='Network'):
    nodes = [{'id': 'a', 'label': 'Planner'}, {'id': 'b', 'label': 'Executor'}] if nodes is None else nodes
    edges = [{'id': 'ab', 'source': 'a', 'target': 'b'}] if edges is None else edges
    return 'root = NetworkGraph(' + ', '.join(json.dumps(value, ensure_ascii=False)
        for value in (title, nodes, edges, layout, theme)) + ')'


def compile_graph(nodes=None, edges=None, **options):
    return Framework.ui_compile(graph_source(nodes, edges, **options), title='Network contract')


class UINetworkContracts(unittest.TestCase):
    def test_valid_network_source_root_hash_roundtrip_and_self_edge(self):
        nodes = [{'id': 'node/a~1', 'label': 'π Planner', 'kind': 'agent', 'status': 'running', 'detail': 'Committed state'}]
        edges = [{'id': 'self', 'source': 'node/a~1', 'target': 'node/a~1', 'kind': 'retry', 'status': 'observed'}]
        definition = compile_graph(nodes, edges, layout='layered', theme='dark')
        self.assertEqual(definition['hash'], wire_hash(definition))
        self.assertNotEqual(definition['libraryHash'], LEGACY_HASH)
        self.assertEqual(definition['root']['typeName'], 'NetworkGraph')
        self.assertEqual(definition['root']['props']['nodes'][0]['group'], '')
        self.assertEqual(definition['root']['props']['edges'][0]['source'], 'node/a~1')
        roundtrip = Framework.ui_compile(definition['source'], title='Network contract')
        self.assertEqual(roundtrip, definition)
        program = Framework.ui_export(definition, format='chaos')['content']
        self.assertEqual(Framework.evaluate('ui_definition(' + program.strip() + ')'), definition)

    def test_node_and_edge_structures_are_closed_and_required_fields_are_typed(self):
        bad_nodes = [
            [{'id': 'a', 'label': 'A', 'onclick': 'steal()'}],
            [{'id': 'a', 'label': 'A', 'coordinates': {'x': 0, 'y': 0}}],
            [{'label': 'A'}], [{'id': 'a'}], [{'id': True, 'label': 'A'}],
            [{'id': 'a', 'label': ['A']}], [{'id': 'a', 'label': 'A', 'detail': None}],
        ]
        for nodes in bad_nodes:
            with self.subTest(nodes=nodes), self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                compile_graph(nodes, [])
        for edge in ({'id': 'e', 'source': 'a', 'target': 'b', 'script': 'bad()'},
                     {'id': 'e', 'source': 'a'}, {'id': 'e', 'source': 1, 'target': 'b'}):
            with self.subTest(edge=edge), self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                compile_graph(edges=[edge])

    def test_node_edge_ids_are_unique_and_endpoints_must_exist(self):
        invalid = [
            ([{'id': 'a', 'label': 'one'}, {'id': 'a', 'label': 'two'}], []),
            (None, [{'id': 'same', 'source': 'a', 'target': 'b'}, {'id': 'same', 'source': 'b', 'target': 'a'}]),
            (None, [{'id': 'e', 'source': 'missing', 'target': 'b'}]),
            (None, [{'id': 'e', 'source': 'a', 'target': 'missing'}]),
        ]
        for nodes, edges in invalid:
            with self.subTest(nodes=nodes, edges=edges), self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                compile_graph(nodes, edges)

    def test_network_capacity_boundaries_are_explicit_not_silent_truncation(self):
        nodes = [{'id': 'n' + str(index), 'label': 'N'} for index in range(200)]
        edges = [{'id': 'e' + str(index), 'source': 'n0', 'target': 'n1'} for index in range(400)]
        full = compile_graph(nodes, edges)
        self.assertEqual(len(full['root']['props']['nodes']), 200)
        self.assertEqual(len(full['root']['props']['edges']), 400)
        for too_many_nodes, too_many_edges in ((nodes + [{'id': 'extra', 'label': 'X'}], []),
                                               (nodes, edges + [{'id': 'extra', 'source': 'n0', 'target': 'n1'}])):
            with self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                compile_graph(too_many_nodes, too_many_edges)

    def test_control_ids_field_lengths_and_non_json_values_are_rejected(self):
        for identity in ('', 'x' * 129, 'a\x00b', 'a\nb', 'a\x7fb'):
            with self.subTest(identity=identity), self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                compile_graph([{'id': identity, 'label': 'A'}], [])
        for field, value in (('label', 'x' * 257), ('kind', 'x' * 65), ('group', 'x' * 129),
                             ('status', 'x' * 65), ('detail', 'x' * 1001), ('detail', float('nan'))):
            row = {'id': 'a', 'label': 'A', field: value}
            with self.subTest(field=field), self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                node('NetworkGraph', ['Graph', [row], []])
        with self.assertRaises((LanguageError, ResourceLimit, ValueError)):
            node('NetworkGraph', ['Graph', [{'id': 'a', 'label': 'A', 'detail': lambda: 'effect'}], []])

    def test_layout_theme_and_runtime_scope_are_explicit_closed_enums(self):
        for layout in ('radial', 'layered'):
            for theme in ('auto', 'dark', 'light'):
                self.assertEqual(compile_graph(layout=layout, theme=theme)['root']['props']['theme'], theme)
                for scope in ('run', 'project'):
                    value = Framework.ui_compile('root = RuntimeGraph("State",' + json.dumps(layout) + ',' + json.dumps(scope) + ',' + json.dumps(theme) + ')')
                    self.assertEqual(value['root']['props']['scope'], scope)
        for source in ('root=NetworkGraph("G",[],[],"force")',
                       'root=NetworkGraph("G",[],[],"radial","custom-css")',
                       'root=RuntimeGraph("State","radial","any-project")',
                       'root=RuntimeGraph("State","radial","run","auto","https://attacker.invalid")'):
            with self.subTest(source=source), self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                Framework.ui_compile(source)

    def test_offline_network_svg_is_deterministic_escaped_and_has_no_external_fetch(self):
        attack = '<img src=x onerror=alert(1)>'
        definition = compile_graph([{'id': 'a', 'label': attack}], [])
        first = Framework.ui_export(definition, format='html')['content']
        second = Framework.ui_export(definition, format='html')['content']
        self.assertEqual(first, second)
        self.assertIn('<svg', first)
        self.assertNotIn(attack, first)
        self.assertIn(html.escape(attack), first)
        self.assertIn("connect-src 'none'", html.unescape(first))
        self.assertNotIn('Math.random(', first)

    def test_runtime_component_construction_is_cold_and_offline_is_honest_placeholder(self):
        evaluator = Evaluator()
        with patch.object(Evaluator, 'run', side_effect=AssertionError('compiler must not execute the language')), \
             patch('chaos.ui_generation.stream_ui', side_effect=AssertionError('runtime view construction must not call a model')):
            definition = Framework.ui_compile('root=RuntimeGraph()')
            value = UIArtifact(evaluator, json.dumps(definition))
            self.assertEqual(value.definition, definition)
            exported = Framework.ui_export(definition, format='html')['content']
        self.assertEqual(definition['root']['props']['scope'], 'run')
        self.assertNotIn('<svg', exported)
        self.assertRegex(exported, r'(?i)(离线|offline)')
        self.assertRegex(exported, r'(?i)(宿主|host|实时|live)')
        self.assertIn("connect-src 'none'", html.unescape(exported))

    def test_generation_prompt_schema_and_positional_order_share_one_catalog(self):
        schema = Framework.ui_schema()
        definitions = schema['libraryJSONSchema']['$defs']
        self.assertEqual(list(definitions['NetworkGraph']['properties']), ['title', 'nodes', 'edges', 'layout', 'theme'])
        self.assertEqual(list(definitions['RuntimeGraph']['properties']), ['title', 'layout', 'scope', 'theme'])
        text = prompt('page')
        self.assertIn('NetworkGraph(title, nodes, edges', text)
        self.assertIn('RuntimeGraph(title=', text)
        self.assertEqual(compile_graph()['libraryHash'], schema['libraryHash'])
        self.assertEqual(set(definitions), {'Stack', 'Heading', 'Text', 'Card', 'Metric', 'Table', 'BarChart', 'Tabs',
            'Tab', 'Disclosure', 'Code', 'ActionButton', 'Slides', 'Slide', 'NetworkGraph', 'RuntimeGraph'})

    def test_legacy_definition_keeps_exact_source_hash_and_rejects_forged_new_components(self):
        before = copy.deepcopy(LEGACY)
        self.assertEqual(LEGACY['hash'], wire_hash(LEGACY))
        self.assertTrue(Framework.ui_validate(LEGACY)['passed'])
        self.assertEqual(json.loads(Framework.ui_export(LEGACY, format='json')['content']), before)
        self.assertEqual(Framework.ui_export(LEGACY, format='openui')['content'], before['source'])
        value = UIArtifact(Evaluator(), json.dumps(LEGACY))
        self.assertEqual(value.definition, before)
        self.assertEqual(LEGACY, before)
        for current in (compile_graph(), Framework.ui_compile('root=RuntimeGraph()')):
            for library_hash in (LEGACY_HASH, '0' * 64):
                forged = copy.deepcopy(current)
                forged['libraryHash'] = library_hash
                forged['hash'] = wire_hash(forged)
                with self.subTest(tag=current['root']['typeName'], library=library_hash), self.assertRaises((LanguageError, ResourceLimit, ValueError)):
                    Framework.ui_validate(forged)
