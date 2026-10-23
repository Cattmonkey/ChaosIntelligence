"""Shared original-AST outlines across SDK, HTTP, LSP and assistant snapshots."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.ide_context import editor_snapshot, semantic_context
from chaos.ide_language import analyze, SourceMap
from chaos.language import Evaluator
from chaos.lsp import Job, Server
from chaos.lsp_adapters import convert
from chaos.openapi import specification
from chaos.server import make_server


def text_range(source, span):
    raw = source.encode('utf-16-le')
    return raw[span['from']*2:span['to']*2].decode('utf-16-le')


def flattened(items):
    for item in items:
        yield item
        yield from flattened(item['children'])


class LanguageOutlineTests(unittest.TestCase):
    def outline(self, source):
        result = analyze(source)
        outline = result['outline']
        self.assertEqual(outline['file'], 'main.chaos')
        units = set(SourceMap(source).units)
        for node in flattened(outline['items']):
            self.assertEqual(set(node), {'name','kind','detail','range','selectionRange','children'})
            full, selected = node['range'], node['selectionRange']
            self.assertLessEqual(full['from'], selected['from'])
            self.assertLess(selected['from'], selected['to'])
            self.assertLessEqual(selected['to'], full['to'])
            for span in (full, selected):
                self.assertIn(span['from'], units); self.assertIn(span['to'], units)
            self.assertEqual(text_range(source, selected), node['name'])
            for child in node['children']:
                self.assertLessEqual(full['from'], child['range']['from'])
                self.assertLessEqual(child['range']['to'], full['to'])
        return outline

    def test_nested_same_name_scope_and_class_members(self):
        source = 'fn outer(){fn same(){1};class C{let x=1;fn same(self){let y=2;y}};same()};fn same(){3}'
        items = self.outline(source)['items']
        self.assertEqual([n['name'] for n in items], ['outer','same'])
        self.assertEqual([n['name'] for n in items[0]['children']], ['same','C'])
        klass = items[0]['children'][1]
        self.assertEqual([(n['name'],n['kind']) for n in klass['children']], [('x','property'),('same','method')])
        self.assertEqual(klass['children'][1]['children'][0]['name'], 'y')

    def test_agent_macro_data_are_declaration_shapes_not_runtime_types(self):
        source = 'agent worker{prompt:"work"};macro identity(x){x};data Choice{Some(Empty),Empty,Pair(Some,Empty)}'
        result = analyze(source); items = self.outline(source)['items']
        self.assertEqual([(n['name'],n['kind']) for n in items], [('worker','agent'),('identity','macro'),('Choice','data')])
        self.assertEqual(next(n for n in result['symbols'] if n['name']=='worker')['type'], 'Function')
        self.assertEqual([text_range(source,n['range']) for n in items[2]['children']], ['Some(Empty)','Empty','Pair(Some,Empty)'])

    def test_utf16_crlf_and_decorator_envelope(self):
        source = '// 🧠\r\n@unknown\r\nasync fn work(){let answer=42;answer}'
        outline = self.outline(source)
        self.assertTrue(outline['complete']); self.assertFalse(outline['truncated'])
        item = outline['items'][0]
        self.assertEqual(item['kind'], 'asyncFunction')
        self.assertEqual(text_range(source,item['range']), '@unknown\r\nasync fn work(){let answer=42;answer}')
        self.assertEqual(item['children'][0]['name'],'answer')

    def test_rec_flavors_have_real_full_ranges(self):
        source = 'rec{fn first(){second()};async fn second(){42};fn* rows(){yield 1};async fn* asyncrows(){yield 2}}'
        items = self.outline(source)['items']
        self.assertEqual([n['kind'] for n in items], ['function','asyncFunction','generator','asyncGenerator'])
        self.assertEqual([text_range(source,n['range']) for n in items], ['fn first(){second()}','async fn second(){42}','fn* rows(){yield 1}','async fn* asyncrows(){yield 2}'])

    def test_quote_anonymous_strings_and_record_labels_do_not_fabricate_declarations(self):
        source = 'macro make(){quote{fn template(){1}}};fn outer(){let callback=fn(){fn hidden(){1};hidden};let record_value={def:"fn fake(){}",agent:"agent bad{}"};1};/* fn comment(){} */'
        names = [n['name'] for n in flattened(self.outline(source)['items'])]
        self.assertEqual(names, ['make','outer','callback','record_value'])

    def test_pattern_bindings_and_import(self):
        source = 'import "std/math" as M;let [left,right]=[1,2];var mutable=1'
        items = self.outline(source)['items']
        self.assertEqual([(n['name'],n['kind']) for n in items], [('M','module'),('left','variable'),('right','variable'),('mutable','variable')])

    def test_recovery_retains_real_nodes_without_fabricating_failed_binding(self):
        source = 'fn outer(){let good=1;let broken=;fn child(){2}'
        outline = self.outline(source)
        self.assertFalse(outline['complete'])
        self.assertEqual([n['name'] for n in flattened(outline['items'])], ['outer','good','child'])

    def test_bounded_declarations_explicitly_report_truncation(self):
        source = ';'.join('let value%d=%d'%(i,i) for i in range(530))
        outline = self.outline(source)
        self.assertTrue(outline['complete']); self.assertTrue(outline['truncated'])
        self.assertEqual(len(outline['items']),512)

    def test_never_evaluates_unknown_decorators_macros_or_class_hooks(self):
        source = '@factory\nclass C(metaclass=unknown){fn __new__(self){throw "not called"}};macro make(){quote{agent hidden{}}};agent worker{prompt:"test"}'
        with patch.object(Evaluator,'evaluate',side_effect=AssertionError('editor executed source')):
            self.assertEqual([n['kind'] for n in self.outline(source)['items']], ['class','macro','agent'])

    def test_semantic_context_uses_frozen_outline_and_reports_its_own_budget(self):
        source = 'agent worker{prompt:"test"};fn call(){worker(1)}'
        files = {'main.chaos':source}
        detached, metadata = editor_snapshot({'files':files,'entry':'main.chaos','languageVersions':{'main.chaos':2},'activeFile':'main.chaos','selection':{'anchor':0,'head':0}},'project',1)
        summary = semantic_context(detached,metadata)
        self.assertEqual([n['name'] for n in summary['analysis']['outline']['items']], ['worker','call'])
        self.assertLess(len(json.dumps(summary)),200000)
        pressure = dict(detached, **{'main.chaos':';'.join('let x%d=%d'%(i,i) for i in range(530))})
        summary = semantic_context(pressure,metadata)
        self.assertTrue(summary['analysis']['truncated'])
        self.assertLess(len(json.dumps(summary)),200000)

    def test_declaration_child_expressions_keep_syntactic_containment(self):
        source = 'fn configured(value=do{fn default_factory(){1};1}){2};class C(do{fn base_factory(){1};unknown},metaclass=do{fn meta_factory(){1};unknown}){fn method(self){1}};agent worker{prompt:do{fn prompt_factory(){1};"work"}}'
        with patch.object(Evaluator,'evaluate',side_effect=AssertionError('must remain static')):
            items = self.outline(source)['items']
        self.assertEqual([n['name'] for n in items[0]['children']], ['default_factory'])
        self.assertEqual([n['name'] for n in items[1]['children']], ['base_factory','meta_factory','method'])
        self.assertEqual([n['name'] for n in items[2]['children']], ['prompt_factory'])

    def test_decorator_pattern_rhs_and_anonymous_defaults_visit_each_real_child_once(self):
        source = '@do{fn decorate(){1};unknown}\nfn target(){let [left,right]=do{fn obtain(){1};[1,2]};let callback=fn(arg=do{fn header(){1};1}){fn hidden(){1};1};left}'
        with patch.object(Evaluator,'evaluate',side_effect=AssertionError('must remain static')):
            items = self.outline(source)['items']
        target = items[0]
        self.assertEqual([n['name'] for n in target['children']], ['decorate','left','right','obtain','callback'])
        self.assertEqual([n['name'] for n in target['children'][-1]['children']], ['header'])
        self.assertEqual([n['name'] for n in flattened(items)], ['target','decorate','left','right','obtain','callback','header'])
        self.assertTrue(text_range(source,target['range']).startswith('@do{'))

    def test_openapi_describes_additive_outline(self):
        schemas = specification()['components']['schemas']
        self.assertEqual(schemas['SourceAnalysis']['properties']['outline'],{'$ref':'#/components/schemas/SourceOutline'})
        self.assertIn('agent',schemas['SourceOutlineItem']['properties']['kind']['enum'])


class OutlineSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def server(self, support):
        server = Server(io.BytesIO(),io.BytesIO(),root=self.root)
        server._initialize({'capabilities':{'textDocument':{'documentSymbol':{'hierarchicalDocumentSymbolSupport':support}}}})
        source = '// 🧠\nclass C{fn method(self){42}}'
        uri = (self.root/'main.chaos').as_uri()
        server.workspace.open({'uri':uri,'text':source,'languageId':'chaos','version':1})
        return server,source,server.workspace.snapshot(uri)

    def test_real_lsp_negotiates_only_actual_true(self):
        for value in (None,False,'true',1,True):
            server,source,snapshot = self.server(value)
            result = server._calculate(Job(1,'textDocument/documentSymbol',{'textDocument':{'uri':snapshot.uri}},snapshot))
            if value is True:
                self.assertEqual(result[0]['name'],'C')
                self.assertEqual(result[0]['children'][0]['name'],'method')
                self.assertIn('selectionRange',result[0]); self.assertNotIn('location',result[0])
            else:
                baseline = convert('textDocument/documentSymbol',analyze(source),snapshot,{'main.chaos':source})
                self.assertEqual(result,baseline)
                self.assertTrue(all('location' in item and 'children' not in item for item in result))

    def test_http_sdk_outline_same_unsaved_source_without_revision_write(self):
        app = Framework(settings=Settings(':memory:')); self.addCleanup(app.close)
        app.create_project('outline',source='42')
        server = make_server(port=0,framework=app,state_dir=self.root/'control',update_home=self.root/'updates')
        worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def stop(): server.shutdown();server.server_close();worker.join(3)
        self.addCleanup(stop)
        source = '// 🧠\nagent worker{prompt:"draft"};fn act(){worker(1)}'
        remote = Client('http://127.0.0.1:'+str(server.server_port)).analyze(source)
        self.assertEqual(remote,Framework.analyze(source))
        self.assertEqual(remote['outline']['items'][0]['kind'],'agent')
        self.assertEqual(app.get_project('outline')['source'],'42')
        self.assertEqual(app.get_project('outline')['version'],1)
