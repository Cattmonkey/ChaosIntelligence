import copy,json,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator,Parser
from chaos.ide_language import analyze,format_source
from chaos.native_specs import spec_for
from chaos.program import parse_program,format_program,plain
from chaos.symbols import rename
from chaos.modules import ProgramBundle

SOURCE='''let exact:Decimal=decimal("0.10")+decimal("0.20");
with decimal_context(precision=3,rounding="half_even") as local {
 [decimal_text(exact),decimal_text(decimal(1)/decimal(3)),
 decimal_text(decimal_quantize(decimal("2.345"),decimal("0.01")))]
}'''

class DecimalSurfaceTests(unittest.TestCase):
    def test_native_signatures_and_readonly_completions(self):
        names={'decimal':['value'],'decimal_text':['value'],'decimal_context':['precision','rounding','emin','emax','clamp','traps'],'get_decimal_context':[],'decimal_context_info':['context'],'decimal_clear_flags':['context'],'decimal_quantize':['value','quantum']}
        for name,params in names.items():
            self.assertEqual([p.name for p in spec_for(name).parameters],params)
            self.assertEqual([p['name'] for p in analyze(name+'(')['signatureHelp']['parameters']],params)
        self.assertEqual({x['label']for x in analyze('decimal("0.1").')['completions']},{'sign','coefficient','exponent'})
        self.assertEqual({x['label']for x in analyze('get_decimal_context().')['completions']},{'precision','rounding','emin','emax','clamp','traps','flags'})
    def test_inert_ide_format_rename(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('ran AST')),patch('chaos.decimal_values.make',side_effect=AssertionError('numeric kernel')):
            report=analyze(SOURCE);formatted=format_source(SOURCE);result=rename(SOURCE,position=SOURCE.index('exact:')+1,new_name='total')
        self.assertFalse(report['diagnostics']);self.assertTrue(report['tokens'])
        self.assertEqual(Framework.evaluate(formatted),['0.30','0.333','2.34'])
        self.assertEqual(Framework.evaluate(result['files']['main.chaos']),['0.30','0.333','2.34'])
    def test_original_ast_graph_edit_and_ir_versions(self):
        ir=parse_program(SOURCE);changed=copy.deepcopy(ir)
        def visit(v):
            if type(v) is dict:
                if v.get('kind')=='literal':
                    if v.get('value')=='0.10':v['value']='0.20'
                    elif v.get('value')=='half_even':v['value']='half_up'
                    elif type(v.get('value')) is int and v['value']==3:v['value']=6
                for x in v.values():visit(x)
            elif type(v) is list:
                for x in v:visit(x)
        visit(changed)
        self.assertEqual(Framework.evaluate(format_program(ir)),['0.30','0.333','2.34'])
        self.assertEqual(Framework.evaluate(format_program(changed)),['0.40','0.166667','2.35'])
        for version in (1,2):
            self.assertEqual(Evaluator().run(ProgramBundle(SOURCE,language_versions={'main.chaos':version})),['0.30','0.333','2.34'])
    def test_macro_alias_captured_constructor_and_context_inert(self):
        files={'value.chaos':'macro value(){syntax_literal(decimal("-0.00"))}export{value}'}
        source='import "value.chaos" as M;let decimal=fn(x){42};let d=M.value!();[d.sign,d.exponent]'
        self.assertEqual(Framework.evaluate(source,files=files),[1,-2])
        self.assertFalse(analyze(source,files=files)['diagnostics'])
    def test_closed_debugger_preview(self):
        from chaos.debugger import preview
        from chaos.decimal_values import Decimal,DecimalContext
        self.assertEqual(preview(Decimal(1,0,-2)),{'type':'Decimal','sign':1,'coefficient':0,'exponent':-2})
        result=preview(DecimalContext());self.assertEqual(result['type'],'DecimalContext');self.assertEqual(result['flags'],[])
        self.assertNotIn('owner',result)
    def test_std_math_and_packaged_example(self):
        self.assertEqual(Framework.evaluate('import "std/math" as M;with M.decimal_context(precision=3) as c {M.decimal_text(M.decimal(1)/M.decimal(7))}'),'0.143')
        source=(Path(__file__).resolve().parent.parent/'examples/decimal_contexts.chaos').read_text()
        value=Framework.evaluate(source);self.assertEqual(value['exact'],'0.30');self.assertEqual([x['value']for x in value['answers']],['0.143','0.142857'])
    def test_framework_wrapped_checkpoint_retains_precision(self):
        self.assertEqual(Framework.evaluate('with decimal_context(precision=4,rounding="half_up") as c {[decimal_text(decimal(1)/decimal(7)),decimal_text(decimal_quantize(decimal("2.345"),decimal("0.01"))),c.flags]}'),['0.1429','2.35',['Inexact','Rounded']])
