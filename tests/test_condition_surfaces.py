import copy,unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator
from chaos.ide_language import analyze,format_source
from chaos.native_specs import spec_for
from chaos.program import parse_program,format_program
from chaos.modules import ProgramBundle

SOURCE='''restart_case(fn(){with handler_bind([{predicate:fn(c){c.kind=="missing"},handler:fn(c){invoke_restart("use",[c.["data"].fallback])}}]) as policy {error_condition(condition("missing",data={fallback:41}))}},[{name:"use",function:fn(x:Int)->Int{x+1}}])'''


class ConditionSurfaceTests(unittest.TestCase):
    def test_native_signatures(self):
        names={'condition':['kind','message','data'],'condition_info':['value'],'handler_bind':['bindings'],'restart_bind':['bindings'],'restart_case':['body','bindings'],'signal_condition':['value'],'error_condition':['value'],'compute_restarts':['condition'],'find_restart':['name','condition'],'invoke_restart':['restart','args','kwargs'],'restart_info':['value']}
        for name,params in names.items():
            self.assertEqual([x.name for x in spec_for(name).parameters],params)
            self.assertEqual([x['name'] for x in analyze(name+'(')['signatureHelp']['parameters']],params)
    def test_members_and_type_annotation_completion(self):
        self.assertEqual({x['label'] for x in analyze('condition("x").')['completions']},{'kind','message','data'})
        self.assertEqual({x['label'] for x in analyze('fn f(r:Restart){r.')['completions']},{'id','name','report','mode','active'})
    def test_inert_analysis_formatter(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed')),patch('chaos.condition_values.make',side_effect=AssertionError('constructed')):
            report=analyze(SOURCE);formatted=format_source(SOURCE)
        self.assertFalse(report['diagnostics']);self.assertTrue(report['tokens'])
        self.assertEqual(Framework.evaluate(formatted),42)
    def test_original_ir_graph_literal_edit(self):
        ir=parse_program(SOURCE);changed=copy.deepcopy(ir)
        def visit(value):
            if type(value) is dict:
                if value.get('kind')=='literal' and type(value.get('value')) is int and value['value']==41:value['value']=99
                for item in value.values():visit(item)
            elif type(value) is list:
                for item in value:visit(item)
        visit(changed)
        self.assertEqual(Framework.evaluate(format_program(ir)),42)
        self.assertEqual(Framework.evaluate(format_program(changed)),100)
    def test_ir_language_versions_same(self):
        for version in (1,2):self.assertEqual(Evaluator().run(ProgramBundle(SOURCE,language_versions={'main.chaos':version})),42)
    def test_macro_condition_literal_hygienic(self):
        files={'condition.chaos':'macro value(){syntax_literal(condition("missing",data={n:decimal("0.10")}))}export{value}'}
        source='import "condition.chaos" as M;let condition=fn(kind,message,payload){42};let decimal=fn(x){99};let c=M.value!();[c.kind,decimal_text(c.["data"].n)]'
        self.assertEqual(Framework.evaluate(source,files=files),['missing','0.10'])
    def test_macro_condition_info_and_comparison(self):
        self.assertEqual(Framework.evaluate('macro x(){let c=condition("x",data=(1,2));syntax_literal([condition_info(c).kind,c==condition("x",data=(1,2))])};x!()'),['x',True])
    def test_macro_runtime_scopes_unavailable(self):
        with self.assertRaises(Exception):Framework.evaluate('macro x(){syntax_literal(handler_bind([]))};x!()')
    def test_debug_preview_inert(self):
        from chaos.debugger import preview
        from chaos.condition_values import make
        result=preview(make('missing','need',{'n':42}));self.assertEqual(result['kind'],'missing');self.assertEqual(result['data'],{'n':42})
        self.assertNotIn('owner',result)
    def test_standard_module_exports(self):
        self.assertEqual(Framework.evaluate('import "std/conditions" as C;C.condition_info(C.condition("x",data=42)).["data"]'),42)

    def test_reserved_member_completion_applies_valid_source_and_lsp(self):
        from types import SimpleNamespace
        from chaos.lsp_adapters import convert
        source='let c=condition("x",data=42);c.'
        result=analyze(source)
        item=next(x for x in result['completions'] if x['label']=='data')
        self.assertEqual(item['apply'],'["data"]')
        applied=source[:item['from']]+item['apply']+source[item['to']:]
        self.assertFalse(analyze(applied)['diagnostics'])
        self.assertEqual(Framework.evaluate(applied),42)
        output=convert('textDocument/completion',result,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
        edit=next(x for x in output['items'] if x['label']=='data')['textEdit']
        self.assertEqual(edit['newText'],'["data"]')
        self.assertEqual(edit['range']['start']['character'],len(source))
        self.assertEqual(Framework.evaluate(source+edit['newText']),42)

    def test_macro_condition_alias_analysis_is_inert(self):
        source='macro missing(){let create=condition;let describe=condition_info;let c=create("missing",data={value:decimal("0.10")});syntax_literal(describe(c).kind)};missing!()'
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed')),patch('chaos.condition_values.make',side_effect=AssertionError('constructed')):
            result=analyze(source)
        self.assertFalse(result['diagnostics'])
        self.assertEqual(Framework.evaluate(source),'missing')
