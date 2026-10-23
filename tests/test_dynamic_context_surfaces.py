import unittest,copy
from pathlib import Path
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator
from chaos.ide_language import analyze,format_source
from chaos.program import parse_program,format_program
from chaos.modules import ProgramBundle
from chaos.native_specs import spec_for

SOURCE='let v=context_var("trace",0);let c=context();let n=c.run(fn(){v.set(42);v.get()});[n,v.get(),c[v]]'
class DynamicSurfaces(unittest.TestCase):
    def test_source_ir_format_and_language_versions(self):
        for source in (SOURCE,format_source(SOURCE),format_program(parse_program(SOURCE))):self.assertEqual(Framework.evaluate(source),[42,0,42])
        for version in (1,2):self.assertEqual(Evaluator().run(ProgramBundle(SOURCE,language_versions={'main.chaos':version})),[42,0,42])
    def test_inert_analysis_and_method_completion(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed')):report=analyze(SOURCE)
        self.assertFalse(report['diagnostics'])
        for source,expected in [('let c=copy_context();let d=c.copy();d.','run'),('let v=context_var("x");let t=v.set(2);t.','old_value')]:
            self.assertIn(expected,{v['label'] for v in analyze(source)['completions']})
        item=next(v for v in analyze('let v=context_var("x");let t=v.set(2);t.')['completions'] if v['label']=='var')
        self.assertEqual(item['apply'],'["var"]')
    def test_complete_receiver_catalog_and_runtime_methods(self):
        import inspect
        from chaos.native_specs import SPECS,MEMBER_SPECS,all_specs,member_specs_for
        from chaos.language import Native
        factories={'ContextVar':'context_var("x",42)','Context':'context()'}
        self.assertEqual(set(MEMBER_SPECS),set(factories)|{'Type','Exception','TaskGroup','ExitStack','AsyncExitStack'})
        expected={'ContextVar':{'get','set','reset'},'Context':{'get','copy','run','has','items','keys','values'}}
        for typ,factory in factories.items():
            catalog=member_specs_for(typ);self.assertEqual(set(catalog),expected[typ])
            for member,spec in catalog.items():
                self.assertEqual(spec.receiver_type,typ)
                self.assertIs(spec_for(spec.id),spec);self.assertIs(all_specs()[spec.id],spec)
                self.assertNotIn(spec.id,SPECS)
                native=Evaluator().run(factory+'.'+member)
                self.assertIs(type(native),Native);self.assertEqual(native.spec_id,spec.id)
                parameters=list(inspect.signature(native.call).parameters.values())
                self.assertEqual([p.name for p in parameters],[p.name for p in spec.parameters])
                self.assertEqual([p.default is not p.empty for p in parameters],[p.optional for p in spec.parameters])
                modes={inspect.Parameter.POSITIONAL_ONLY:'positional_only',inspect.Parameter.POSITIONAL_OR_KEYWORD:'positional',inspect.Parameter.KEYWORD_ONLY:'keyword_only',inspect.Parameter.VAR_POSITIONAL:'varargs',inspect.Parameter.VAR_KEYWORD:'varkw'}
                self.assertEqual([modes[p.kind]for p in parameters],[p.mode for p in spec.parameters])
        for catalog in (MEMBER_SPECS,member_specs_for('Context'),all_specs()):
            with self.assertRaises(TypeError):catalog['fake']=None
        self.assertEqual(dict(member_specs_for('Unknown')), {})
    def test_token_variable_field_hydrates_original_native_type(self):
        source='let v=context_var("x",42);let t=v.set(2);let w=t.["var"];w.'
        labels={item['label'] for item in analyze(source)['completions']}
        self.assertEqual(labels,{'id','name','has_default','get','set','reset'})
        self.assertEqual(Framework.evaluate('let v=context_var("x",42);let t=v.set(2);let w=t.["var"];[w==v,w.get()]'),[True,2])
    def test_complete_catalog_rejects_duplicate_declarations(self):
        from chaos.native_specs import _validated_catalog
        spec=spec_for('context_var')
        with self.assertRaises(ValueError):_validated_catalog([spec,spec])
    def test_native_receiver_chained_completion(self):
        for source,expected in [('copy_context().copy().','run'),('context_var("x",42).set(2).','old_value')]:
            self.assertIn(expected,{v['label'] for v in analyze(source)['completions']})
    def test_signatures(self):
        for name in ('context_var','context_set','context_reset','context_run','copy_context','context_bind','context_get_in'):
            self.assertEqual([p.name for p in spec_for(name).parameters],[p['name'] for p in analyze(name+'(')['signatureHelp']['parameters']])
    def test_module_export(self):
        self.assertEqual(Framework.evaluate('import "std/context" as C;let v=C.context_var("x",42);C.context_get(v)'),42)
    def test_macro_generates_runtime_call_and_rejects_compile_time_effect(self):
        self.assertEqual(Framework.evaluate('macro x(get,create){quote{unquote(get)(unquote(create)("x",42))}};x!(context_get,context_var)'),42)
        with self.assertRaises(Exception):Framework.evaluate('macro x(){syntax_literal(context_var("x",42))};x!()')
    def test_context_metadata_does_not_expose_payload(self):
        result=Framework.evaluate('let v=context_var("private",fn(){throw "executed"});let c=copy_context();[parse_json(json_stringify(v)),parse_json(str(c))]')
        self.assertEqual(result[0]['$handle'],'ContextVar');self.assertNotIn('default',result[0]);self.assertNotIn('bindings',result[1])
    def test_executable_guide_and_file(self):
        from chaos.documentation import Documentation,markdown_parser
        docs=Documentation();page=docs.page('dynamic-contexts')
        snippets=[token.content for token in markdown_parser().parse(page['markdown']) if token.type=='fence' and token.info=='chaos']
        self.assertEqual(Framework.evaluate(snippets[0]),['root','request-42']);self.assertEqual(Framework.evaluate(snippets[1]),[['planner','executor'],'root'])
        path=docs.examples/'dynamic-contexts.chaos';self.assertEqual(Framework.evaluate(path.read_text()),{'results':[{'role':'planner','context':1},{'role':'executor','context':1}],'caller':'root'})
