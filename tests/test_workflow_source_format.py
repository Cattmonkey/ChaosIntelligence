"""Generated workflow text is one versioned source across authoring surfaces."""
import copy
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings, Workflow, Workspace
from chaos.language import LanguageError, Parser, ResourceLimit
from chaos.program import extract_workflow, parse_program, plain, workflow_source
from chaos.server import make_server
from chaos.source_format import _format_generated_source, format_source
from chaos.store import Conflict


def payload(graph):
    value = copy.deepcopy(graph)
    for node in value['nodes']:
        node.pop('x', None)
        node.pop('y', None)
    return value


def compact(graph):
    return 'workflow(' + json.dumps(payload(graph), ensure_ascii=False,
                                   allow_nan=False, separators=(',', ':')) + ', input)\n'


class WorkflowSourceFormatting(unittest.TestCase):
    def app(self):
        app = Framework(settings=Settings(':memory:'))
        self.addCleanup(app.close)
        return app

    def graph(self):
        return (Workflow().input().function('compute', 'input + 1 // 原文  \n')
                .output().connect('input', 'compute').connect('compute', 'output').build())

    def assert_mirrors(self, project):
        self.assertEqual(project['source'], project['files'][project['entry']])
        self.assertEqual(parse_program(project['source'], project['entry'])['semanticHash'],
                         project['program']['semanticHash'])

    def test_readable_generation_preserves_graph_literals_and_never_executes(self):
        graph = self.graph()
        graph['nodes'][1]['config']['data'] = {'变量': [90071992547409939999, 1.0, -0.0, True],
                                               'text': '中文 "quoted" \\ // #\n'}
        before = copy.deepcopy(graph)
        with patch('chaos.language.Evaluator.run', side_effect=AssertionError('executed')):
            source = workflow_source(graph)
        self.assertGreater(len(source.splitlines()), 12)
        self.assertEqual(plain(Parser(source).program()), plain(Parser(compact(graph)).program()))
        self.assertEqual(json.dumps(extract_workflow(source), ensure_ascii=False),
                         json.dumps(payload(graph), ensure_ascii=False))
        self.assertEqual(graph, before)
        self.assertEqual(format_source(source), source)
        self.assertEqual(extract_workflow(source)['nodes'][1]['config']['source'],
                         graph['nodes'][1]['config']['source'])
        values = extract_workflow(source)['nodes'][1]['config']['data']['变量']
        self.assertIs(type(values[1]), float)
        self.assertLess(math.copysign(1, values[2]), 0)

    def test_create_save_patch_generate_once_and_commit_one_version(self):
        app = self.app()
        with patch('chaos.source_format._format_generated_source', wraps=_format_generated_source) as layout:
            project = app.create_project('generated', graph=self.graph(), entry='flow.chaos',
                                         files={'lib.chaos': '# keep\n42'}, capabilities=[])
            self.assertEqual(layout.call_count, 1)
            project['graph']['nodes'][1]['config']['source'] = 'input + 2'
            project = app.save_project(project, from_view='graph')
            self.assertEqual(layout.call_count, 2)
            project = app.patch('generated', 2, [{'op': 'update_node', 'id': 'compute',
                'changes': {'config': {'source': 'input + 3', 'tools': []}}}], reason='definition')
            self.assertEqual(layout.call_count, 3)
        self.assertEqual(project['version'], 3)
        self.assert_mirrors(project)
        self.assertEqual(project['files']['lib.chaos'], '# keep\n42')
        self.assertEqual([row['kind'] for row in app.program_history('generated')['items']],
                         ['created', 'saved', 'patched'])
        self.assertEqual([e['type'] for e in app.events('generated')['items']],
                         ['project.created', 'graph.saved', 'graph.revised'])
        self.assertEqual(app.start('generated', 3, 39, mode='source').result(), 42)

    def test_coordinate_save_and_patch_preserve_saved_text_ir_and_hash(self):
        app = self.app()
        original = '// hand-written header  \n' + compact(self.graph()) + '# footer\n'
        project = app.create_project('layout', source=original, capabilities=[])
        before_ir = copy.deepcopy(project['program'])
        before_hash = app.program_snapshot('layout')['definitionHash']
        project['graph']['nodes'][0]['x'] = 81
        project['source'] = '// forged replacement\n' + format_source(original)
        project['files'][project['entry']] = project['source']
        with patch('chaos.source_format._format_generated_source', side_effect=AssertionError('layout only')):
            project = app.save_project(project, from_view='graph')
            self.assertEqual(project['source'], original)
            self.assertEqual(project['program'], before_ir)
            project = app.patch('layout', 2, [{'op': 'update_node', 'id': 'input',
                                 'changes': {'y': 91}}], reason='layout')
        self.assertEqual(project['source'], original)
        self.assertEqual(project['files'][project['entry']], original)
        self.assertEqual(project['program'], before_ir)
        self.assertEqual(app.program_snapshot('layout')['definitionHash'], before_hash)
        self.assertEqual(project['graph']['nodes'][0]['y'], 91)
        self.assertEqual([r['version'] for r in app.program_history('layout')['items']], [1, 2, 3])

    def test_semantic_graph_change_ignores_supplied_source_and_keeps_function_text(self):
        app = self.app()
        project = app.create_project('authority', graph=self.graph(), capabilities=[])
        project['source'] = 'tool("forged", input)'
        project['files'][project['entry']] = project['source']
        body = 'input + 2 // body stays verbatim  \n'
        project['graph']['nodes'][1]['config']['source'] = body
        project = app.save_project(project, from_view='graph')
        self.assert_mirrors(project)
        self.assertEqual(extract_workflow(project['source'])['nodes'][1]['config']['source'], body)
        self.assertNotIn('forged', project['source'])
        self.assertEqual(app.start('authority', 2, 40, mode='source').result(), 42)

    def test_typed_values_order_and_nested_coordinates_are_definition_changes(self):
        pairs = [(True, 1), (1, 1.0), (-0.0, 0.0),
                 ({'first': 1, 'second': 2}, {'second': 2, 'first': 1}),
                 ({'x': 1, 'y': 2}, {'x': 3, 'y': 2})]
        for old, new in pairs:
            for explicit in (True, False):
                with self.subTest(old=old, new=new, explicit=explicit):
                    app = self.app()
                    graph = Workflow.identity().build()
                    graph['nodes'][0]['config']['data'] = old
                    project = app.create_project('typed', graph=graph, capabilities=[])
                    before = app.program_snapshot('typed')['definitionHash']
                    project['graph']['nodes'][0]['config']['data'] = new
                    project = app.save_project(project, from_view='graph' if explicit else None)
                    actual = extract_workflow(project['source'])['nodes'][0]['config']['data']
                    self.assertEqual(json.dumps(actual), json.dumps(new))
                    self.assertNotEqual(app.program_snapshot('typed')['definitionHash'], before)

    def test_ordinary_source_preserved_and_explicit_graph_conversion_still_works(self):
        app = self.app()
        source = '# keep spelling\nlet answer=40;answer+2'
        project = app.create_project('ordinary', source=source, capabilities=[])
        project = app.save_project(project, from_view='source')
        self.assertEqual(project['source'], source)
        project['graph'] = Workflow.identity().build()
        project = app.save_project(project, from_view='graph')
        self.assertIsNotNone(extract_workflow(project['source']))
        self.assert_mirrors(project)

    def test_capacity_retention_is_only_for_generated_whitespace_expansion(self):
        graph = Workflow.identity().build()
        graph['nodes'][0]['config']['prompt'] = ''
        graph['nodes'][0]['config']['prompt'] = 'x' * (100000 - len(compact(graph)))
        raw = compact(graph)
        self.assertEqual(len(raw), 100000)
        Parser(raw).program()
        with self.assertRaises(ResourceLimit):
            format_source(raw)
        self.assertEqual(workflow_source(graph), raw)
        app = self.app()
        project = app.create_project('capacity', graph=graph, capabilities=[])
        self.assertEqual(project['source'], raw)
        self.assert_mirrors(project)
        for bad in ('x' * 100001, '(' * 1000 + '42' + ')' * 1000):
            with self.subTest(length=len(bad)), self.assertRaises(ResourceLimit):
                _format_generated_source(bad)
        with self.assertRaises(LanguageError):
            _format_generated_source('let =')

    def test_oversized_semantic_save_and_patch_rollback_all_state(self):
        app = self.app()
        project = app.create_project('rollback', graph=self.graph(), capabilities=[])
        history = app.program_history('rollback')
        events = app.events('rollback')
        draft = copy.deepcopy(project)
        draft['graph']['nodes'][0]['config']['prompt'] = 'x' * 100000
        with self.assertRaises(ResourceLimit):
            app.save_project(draft, from_view='graph')
        with self.assertRaises(ResourceLimit):
            app.patch('rollback', 1, [{'op': 'update_node', 'id': 'input',
                       'changes': {'config': {'prompt': 'x' * 100000}}}], reason='oversize')
        self.assertEqual(app.get_project('rollback'), project)
        self.assertEqual(app.program_history('rollback'), history)
        self.assertEqual(app.events('rollback'), events)
        app.save_project(project, from_view='graph')
        with self.assertRaises(Conflict):
            app.save_project(draft, from_view='graph')

    def test_bundle_byte_capacity_retains_compact_entry_without_waiving_limits(self):
        from chaos.modules import MAX_BYTES, bundle_files
        graph = self.graph()
        graph['nodes'][1]['config']['source'] = 'input + 2'
        graph['nodes'][0]['config']['caption'] = '多语言🙂'
        raw = compact(graph)
        remaining = MAX_BYTES - len(raw.encode('utf-8'))
        files = {}
        while remaining:
            size = min(100000, remaining)
            files['padding' + str(len(files)) + '.chaos'] = '#' + 'x' * (size - 3) + '\n0'
            remaining -= size
        bundle_files(raw, files)
        with self.assertRaises(ResourceLimit):
            bundle_files(format_source(raw), files)
        self.assertEqual(workflow_source(graph, files=files), raw)
        app = self.app()
        before = copy.deepcopy(graph)
        before['nodes'][1]['config']['source'] = 'input'
        project = app.create_project('bundle', graph=before, files=files, capabilities=[])
        project['graph'] = graph
        project = app.save_project(project, from_view='graph')
        self.assertEqual(project['source'], raw)
        self.assert_mirrors(project)
        project = app.patch('bundle', 2, [{'op': 'update_node', 'id': 'compute',
            'changes': {'config': {'source': 'input + 3', 'tools': []}}}], reason='same-size')
        self.assertEqual(project['source'], compact(project['graph']))
        events = app.events('bundle')
        history = app.program_history('bundle')
        with self.assertRaises(ResourceLimit):
            app.patch('bundle', 3, [{'op': 'update_node', 'id': 'compute',
                'changes': {'config': {'source': 'input + 33', 'tools': []}}}], reason='too-large')
        self.assertEqual(app.get_project('bundle'), project)
        self.assertEqual(app.events('bundle'), events)
        self.assertEqual(app.program_history('bundle'), history)

    def test_http_sdk_workspace_observe_the_same_readable_committed_source(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as folder:
            server = make_server(port=0, framework=app, state_dir=Path(folder)/'control',
                                 update_home=Path(folder)/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Client('http://127.0.0.1:' + str(server.server_port))
                project = client.create_project('remote', graph=self.graph(), capabilities=[])
                self.assertGreater(len(project['source'].splitlines()), 12)
                workspace = Workspace.checkout(client, 'remote', Path(folder)/'checkout')
                self.assertEqual((workspace.directory/project['entry']).read_text(), project['source'])
                project['graph']['nodes'][1]['config']['source'] = 'input + 2'
                project = client.save_project(project, from_view='graph')
                project = client.patch('remote', 2, [{'op': 'update_node', 'id': 'compute',
                    'changes': {'config': {'source': 'input + 3', 'tools': []}}}])
                self.assertEqual(workspace.sync(), {'status': 'pulled', 'version': 3})
                actual = (workspace.directory/project['entry']).read_text()
                self.assertEqual(actual, project['source'])
                self.assertEqual(client.get_project('remote'), app.get_project('remote'))
                self.assert_mirrors(project)
                self.assertEqual(client.start('remote', 3, 39, mode='source').result(), 42)
                self.assertEqual(len(client.program_history('remote')['items']), 3)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)
