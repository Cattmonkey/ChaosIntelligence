import copy
import hashlib
import html
import json
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable
from chaos.ui_artifacts import (UIArtifact, catalog, compile_ui, artifact_definition,
    node, export_ui, validate_ui, program_ui, data, LIBRARY_HASH)


class UIArtifactTests(unittest.TestCase):
    def test_native_type_snapshot_and_readonly_fields(self):
        value = Evaluator().run('let a:UIArtifact=ui_artifact("page","Demo",ui_node("Text",["Hello"]));[type_of(a),a.kind,ui_definition(a),vars(a),dir(a)]')
        self.assertEqual(value[0:2], ['UIArtifact', 'page'])
        self.assertEqual(value[2], value[3]); self.assertEqual(value[4], sorted(value[2]))
        with self.assertRaises(LanguageError): Evaluator().run('let a=ui_artifact("page","",ui_node("Text",[""]));a.kind="report"')

    def test_detached_native_definition_and_wire(self):
        evaluator = Evaluator(); artifact = evaluator.run('ui_artifact("page","Demo",ui_node("Text",["Hello"]))')
        first = artifact.definition; first['root']['props']['text'] = 'changed'
        self.assertEqual(artifact.definition['root']['props']['text'], 'Hello')
        self.assertEqual(serializable(artifact), {'$ui': artifact.definition})
        with self.assertRaises(Exception): artifact.definition_json = '{}'

    def test_canonical_source_recreation_and_hash(self):
        first = compile_ui('root = Stack([label, Text("World")])\nlabel = Heading("Hello")\n', 'report', 'Title')
        self.assertEqual(compile_ui(first['source'], 'report', 'Title'), first)
        second = Evaluator().run(program_ui(first))
        self.assertEqual(second.definition, first)
        raw = dict(first); digest = raw.pop('hash')
        self.assertEqual(digest, hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest())

    def test_forward_shared_references_and_comment_strings(self):
        doc = compile_ui('root = Stack([a,a]) // end\na = Text("# not comment // either")\n')
        self.assertEqual(doc['root']['props']['children'][0], doc['root']['props']['children'][1])
        self.assertIn('# not comment // either', doc['source'])

    def test_exact_numeric_contract_and_integral_normalization(self):
        self.assertEqual(data([1.0, -0.0, 0.25]), [1, 0, .25])
        for value in (9007199254740992, float('nan'), float('inf'), 1e20):
            with self.subTest(value=value), self.assertRaises(LanguageError): data(value)
        self.assertEqual(node('BarChart', ['x', ['a'], [1.0]])['props']['values'], [1])

    def test_schema_types_defaults_and_constraints(self):
        self.assertEqual(node('Heading', ['x'])['props'], {'text': 'x', 'level': 2})
        cases = [('Heading', ['x', True]), ('Heading', ['x', 5]), ('Text', [1]),
                 ('Table', [['a'], [[1, 2]]]), ('BarChart', ['x', ['a'], []]),
                 ('Tabs', [[node('Text', ['x'])]]), ('Slides', [[node('Text', ['x'])]]),
                 ('Stack', [[], 'diagonal']), ('ActionButton', ['x', '', {}]), ('Text', ['x', 1])]
        for tag, args in cases:
            with self.subTest(tag=tag), self.assertRaises(LanguageError): node(tag, args)

    def test_all_catalog_components_construct_and_export(self):
        text = node('Text', ['text'])
        cases = {'Stack': [[text]], 'Heading': ['Title'], 'Text': ['Text'],
                 'Card': ['Card', [text]], 'Metric': ['Value', '42'], 'Table': [['n'], [[42]]],
                 'BarChart': ['Chart', ['n'], [42]], 'Tabs': [[node('Tab', ['T', [text]])]],
                 'Tab': ['T', [text]], 'Disclosure': ['D', [text]], 'Code': ['let x=1', 'chaos'],
                 'ActionButton': ['Go', 'go', {}], 'Slides': [[node('Slide', ['S', [text]])]],
                 'Slide': ['S', [text]],'NetworkGraph':['Network',[],[]],'RuntimeGraph':[]}
        self.assertEqual(set(cases), set(catalog()['$defs']))
        for tag, args in cases.items():
            with self.subTest(tag=tag):
                definition = artifact_definition('slides' if tag == 'Slides' else 'page', tag, node(tag, args))
                self.assertEqual(compile_ui(definition['source'], definition['kind'], tag), definition)
                self.assertIn('<!doctype html>', export_ui(definition))

    def test_formal_invalid_sources_rejected(self):
        cases = ['root = Text("x");', 'root = Text("x"', 'a = Text("x")',
                 'root = missing', 'root = a\na = root', 'root = Text("x")\nroot = Text("y")',
                 'root = Query("fetch",{})', 'root = Action("x")', 'root = Unknown("x")',
                 '$state = 1\nroot = Text("x")', 'root = Text(@Add(1,2))',
                 'root = Text("x") + Text("y")', 'root = Text(null)', 'root = Text("x")garbage',
                 'root = Text("x")\nunused = 1e999', 'root = Text("x")\nunused = missing',
                 'root = Text("x")\nunused = Unknown()']
        for source in cases:
            with self.subTest(source=source), self.assertRaises(LanguageError): compile_ui(source)

    def test_inert_payload_node_shape_has_no_component_semantics(self):
        fake = {'type': 'element', 'typeName': 'ActionButton', 'props': {'label': 'Fake', 'name': 'hidden', 'payload': {}}, 'partial': False}
        definition = artifact_definition('page', '', node('ActionButton', ['Real', 'real', {'nested': fake}]))
        self.assertEqual(compile_ui(definition['source']), definition)
        result = validate_ui(definition, [{'component': 'ActionButton', 'minimum': 2}])
        self.assertFalse(result['passed']); self.assertEqual(result['checks'][0]['actual'], 1)

    def test_reference_dag_is_bounded_before_expansion(self):
        declarations = ['a0 = []'] + ['a'+str(i)+' = [a'+str(i-1)+',a'+str(i-1)+']' for i in range(1, 31)]
        self.assertEqual(compile_ui('\n'.join(declarations)+'\nroot = Text("ok")')['root']['props']['text'], 'ok')
        with self.assertRaises(ResourceLimit): compile_ui('\n'.join(declarations)+'\nroot = Stack(a30)')

    def test_host_subclasses_cycles_and_live_values_rejected(self):
        seen = []
        class Host(dict):
            def items(self): seen.append('items'); return super().items()
        for value in (Host(x=1), Evaluator().run('fn(x){x}')):
            with self.assertRaises(LanguageError): data(value)
        self.assertEqual(seen, [])
        value = []; value.append(value)
        with self.assertRaises(LanguageError): data(value)
        with self.assertRaises(LanguageError): data({'__proto__': {}})

    def test_cross_evaluator_owner_and_nested_class_member(self):
        evaluator = Evaluator(); artifact = evaluator.run('ui_artifact("page","",ui_node("Text",["x"]))')
        for expression in ('ui_definition(input)', 'type_of(input)', 'class_member({nested:input})', 'json_stringify({nested:input})'):
            with self.subTest(expression=expression), self.assertRaises(LanguageError): Evaluator().run(expression, {'input': artifact})
        self.assertEqual(evaluator.run('let a=ui_artifact("page","",ui_node("Text",["x"]));let C=new_class("C",members={view:class_member(a,contract="UIArtifact")});C().view.hash'), artifact.definition['hash'])

    def test_metadata_getters_do_not_expose_private_runtime(self):
        source = 'let a=ui_artifact("page","",ui_node("Text",["x"]));'
        for name in ('owner', 'definition_json', 'capabilities'):
            with self.subTest(name=name), self.assertRaises(LanguageError): Evaluator().run(source+'getattr(a,"'+name+'")')

    def test_typed_requirement_verdict_and_path(self):
        definition = compile_ui('root = Stack([Heading("Revenue"),Table(["x"],[[1]])])')
        result = validate_ui(definition, [{'component': 'Table', 'minimum': 1}, {'path': '/props/children/0/props/text', 'equals': 'Revenue'}])
        self.assertTrue(result['passed'])
        self.assertFalse(validate_ui(definition, [{'path': '/missing', 'equals': None}])['passed'])
        self.assertFalse(validate_ui(definition, [{'path': '/props/children/1/props/rows/0/0', 'equals': True}])['passed'])
        nested = artifact_definition('page', '', node('ActionButton', ['Run', 'run', {'n': [True], 'label': 'x'}]))
        self.assertFalse(validate_ui(nested, [{'path': '/props/payload', 'equals': {'n': [1], 'label': 'x'}}])['passed'])
        self.assertTrue(validate_ui(nested, [{'path': '/props/payload', 'equals': {'label': 'x', 'n': [True]}}])['passed'])
        for requirements in ([{'words': 'table'}], [{'component': 'Table', 'minimum': True}], [{'path': '/bad~3', 'equals': 1}]):
            with self.assertRaises(LanguageError): validate_ui(definition, requirements)

    def test_unknown_or_tampered_definition_rejected(self):
        definition = compile_ui('root = Text("x")')
        for key in ('source', 'hash', 'libraryHash', 'schema'):
            value = copy.deepcopy(definition); value[key] = 'tampered'
            with self.subTest(key=key), self.assertRaises(LanguageError): export_ui(value)

    def test_export_formats_csp_and_injection(self):
        content = '</script><img src="https://bad.invalid/x" onerror="alert(1)">'
        definition = artifact_definition('report', content, node('Text', [content]))
        rendered = export_ui(definition)
        self.assertNotIn('<img', rendered); self.assertIn(html.escape(content), rendered)
        self.assertIn('Content-Security-Policy', rendered); self.assertIn('sha256-', rendered); self.assertIn("connect-src &#x27;none&#x27;", rendered)
        self.assertEqual(json.loads(export_ui(definition, 'json')), definition)
        self.assertEqual(export_ui(definition, 'openui'), definition['source'])
        self.assertEqual(Evaluator().run(export_ui(definition, 'chaos')).definition, definition)

    def test_source_tree_title_and_catalog_limits(self):
        with self.assertRaises(ResourceLimit): compile_ui('x'*100001)
        with self.assertRaises(ResourceLimit): data([0]*10001)
        with self.assertRaises(LanguageError): artifact_definition('slides', '', node('Text', ['x']))
        with self.assertRaises(LanguageError): artifact_definition('page', '\ud800', node('Text', ['x']))
        fresh = catalog(); fresh['$defs'].clear(); self.assertEqual(len(catalog()['$defs']), 16)
        self.assertEqual(len(LIBRARY_HASH), 64)

    def test_duplicate_actions_rejected_before_callbacks(self):
        with self.assertRaises(LanguageError): artifact_definition('page', '', node('Stack', [[node('ActionButton', ['A', 'same']), node('ActionButton', ['B', 'same'])]]))


if __name__ == '__main__': unittest.main()
