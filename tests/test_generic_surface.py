"""Generic functions share inert metadata and the existing program/tool surfaces."""
import inspect
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import SHAPES, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.symbols import references, rename

NAMES = ('generic_function', 'generic_method', 'generic_remove', 'generic_methods', 'generic_info')
SOURCE = '''// Dispatch remains an ordinary call in source and Program IR.
let empty = generic_function("calculate");
let calculate = generic_method(empty, ["Int"], fn(value: Int) -> Int { value + 2 });
calculate(40)'''


class GenericSurfaces(unittest.TestCase):
    def test_native_metadata_matches_actual_keyword_api_and_info(self):
        evaluator = Evaluator()
        symbols = builtin_symbols()
        for name in NAMES:
            with self.subTest(name=name):
                actual = evaluator.env.get(name)
                spec = spec_for(name)
                self.assertEqual(actual.spec_id, spec.id)
                params = list(inspect.signature(actual.call).parameters.values())
                self.assertEqual([p.name for p in params], [p.name for p in spec.parameters])
                self.assertEqual([p.default is not inspect.Parameter.empty for p in params],
                                 [p.optional for p in spec.parameters])
                self.assertEqual(symbols[name].returns, spec.result_type)
        self.assertEqual([p.name for p in spec_for('generic_method').parameters],
                         ['function', 'specializers', 'implementation', 'qualifier', 'with_next'])
        fields = {f.name: f.type for f in SHAPES['GenericFunctionInfo']}
        self.assertEqual(fields, {'name': 'String', 'arity': 'Int', 'methodCount': 'Int',
                                  'qualifiers': 'List', 'precedence': 'String', 'nextArguments': 'String'})
        value = Framework.evaluate('generic_info(generic_function(name="solve",arity=2))')
        self.assertEqual(set(value), set(fields))
        self.assertEqual([value['arity'], value['methodCount'], value['precedence'], value['nextArguments']],
                         [2, 0, 'left-to-right', 'captured'])

    def test_aliases_module_exports_shadowing_and_conservative_results(self):
        source = '''import "std/generic" as G;
let make=G.generic_function;let extend=G.generic_method;
let first=make("value");let selected=extend(first,["Int"],fn(n:Int)->Int{n});
let changed=G.generic_remove(selected,["Int"]);let unknown=selected(42);'''
        report = analyze(source)
        self.assertEqual(report['diagnostics'], [])
        types = {s['name']: s['type'] for s in report['symbols']}
        self.assertEqual([types[n] for n in ('first', 'selected', 'changed', 'unknown')],
                         ['Function', 'Function', 'Function', 'Any'])
        help = analyze(source + 'selected(')['signatureHelp']
        self.assertEqual([p['mode'] for p in help['parameters']], ['varargs', 'varkw'])
        shadow = 'fn generic_function(name:String)->Int{42};let answer=generic_function("x")'
        self.assertEqual(next(s['type'] for s in analyze(shadow)['symbols'] if s['name'] == 'answer'), 'Int')

    def test_completion_signatures_and_record_fields(self):
        prefix = 'let g=generic_function("g");'
        self.assertEqual({c['label'] for c in analyze(prefix + 'generic_info(g).')['completions']},
                         {f.name for f in SHAPES['GenericFunctionInfo']})
        source = prefix + 'let metadata=generic_info(g);let count=metadata.methodCount;let policy=metadata.precedence;'
        types = {s['name']: s['type'] for s in analyze(source)['symbols']}
        self.assertEqual([types[n] for n in ('metadata', 'count', 'policy')], ['Record', 'Int', 'String'])
        help = analyze(prefix + 'generic_method(function=g,specializers=["Int"],implementation=fn(n){n},qualifier=')['signatureHelp']
        self.assertEqual(help['activeParameter'], 3)
        self.assertEqual([p['name'] for p in help['parameters']], ['function', 'specializers', 'implementation', 'qualifier', 'with_next'])
        self.assertTrue(set(NAMES) <= {c['label'] for c in analyze('generic_')['completions']})

    def test_source_ir_and_formatter_preserve_existing_calls(self):
        for source in (SOURCE, format_source(SOURCE, line_width=42), format_program(parse_program(SOURCE))):
            self.assertEqual(Framework.evaluate(source), 42)
            self.assertEqual(plain(decode_program(parse_program(source))), plain(Parser(SOURCE).program()))
        formatted = format_source(SOURCE)
        self.assertEqual(format_source(formatted), formatted)
        self.assertIn('// Dispatch remains', formatted)
        self.assertEqual(analyze(SOURCE)['diagnostics'], [])

    def test_macro_and_rename_preserve_nominal_specializers(self):
        source = '''macro specialize(factory,base,kind,implementation){quote{
 unquote(factory)(unquote(base),[unquote(kind)],unquote(implementation))}}
class Request{}
let selected=specialize!(generic_method,generic_function("request"),Request,fn(request){42});
selected(Request())'''
        for text in (source, format_source(source), format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text), 42)
        ordinary = 'class Request{};let f=generic_method(generic_function("request"),[Request],fn(x){42});f(Request())'
        position = ordinary.index('Request')
        result = references(ordinary, position=position)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['references']), 3)
        changed = rename(ordinary, position=position, new_name='WorkItem')['files']['main.chaos']
        self.assertIn('[WorkItem]', changed)
        self.assertEqual(Framework.evaluate(changed), 42)

    def test_analysis_lsp_highlighting_never_execute_methods(self):
        source = 'let f=generic_method(generic_function("effect"),["Int"],fn(n){tool("never",n)});generic_method('
        with patch.object(Evaluator, 'run', side_effect=AssertionError('ran user source')), \
             patch.object(Evaluator, 'evaluate', side_effect=AssertionError('evaluated user source')):
            report = analyze(source)
            help = convert('textDocument/signatureHelp', report, SimpleNamespace(name='main.chaos'), {'main.chaos': source})
            self.assertIn('specializers: List', help['signatures'][0]['label'])
            html = render_code(SOURCE, 'chaos')
            self.assertIn('generic_method', html)
            self.assertIn('specializers', html)

    def test_http_sdk_ir_edits_preserve_old_run_and_shared_analysis(self):
        with tempfile.TemporaryDirectory() as folder, Framework(settings=Settings(':memory:')) as app:
            root = Path(folder)
            server = make_server('127.0.0.1', 0, framework=app, state_dir=root/'control', update_home=root/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Client('http://127.0.0.1:' + str(server.server_port))
                project = client.create_project('generic-surface', source=SOURCE, capabilities=[])
                old = client.start(project['id'], project['version'], None)
                self.assertEqual(old.result(), 42)
                program = client.program(project['id'])['program']
                def literal(value):
                    if isinstance(value, dict):
                        if value.get('value') == 40 and 'id' in value:
                            return value
                        for item in value.values():
                            found = literal(item)
                            if found: return found
                    elif isinstance(value, list):
                        for item in value:
                            found = literal(item)
                            if found: return found
                target = literal(program['root'])
                self.assertIsNotNone(target)
                updated = client.edit_program(project['id'], project['version'], operations=[{'op':'set','id':target['id'],'field':'value','value':41}])
                self.assertEqual(client.start(project['id'], updated['version'], None, mode='graph').result(), 43)
                self.assertEqual(old.get()['snapshot']['source'], SOURCE)
                self.assertEqual(client.analyze(updated['source']), Framework.analyze(updated['source']))
            finally:
                server.shutdown()
                thread.join(3)
                server.server_close()


if __name__ == '__main__':
    unittest.main()
