import unittest,re
from pathlib import Path
from chaos import Framework
from chaos.language import Evaluator
from chaos.program import parse_program,format_program
from chaos.native_specs import SHAPES,spec_for
from chaos.ide_language import analyze
SOURCE='let fast=compile_function(fn(x){(x+1)*(x+1)});[fast(6),length(compile_info(fast)),compile_optimization_info(fast).schema]'
EXPECTED=[49,12,'chaos.compiled-optimization/1']
class OptimizerSurfaces(unittest.TestCase):
    def test_source_ir_format_parity(self):
        self.assertEqual(Framework.evaluate(SOURCE),EXPECTED)
        self.assertEqual(Framework.evaluate(format_program(parse_program(SOURCE))),EXPECTED)
    def test_metadata_runtime_signature(self):
        import inspect
        from chaos.jit import builtins
        self.assertEqual([p.name for p in spec_for('compile_optimization_info').parameters],list(inspect.signature(builtins(Evaluator())['compile_optimization_info']).parameters))
        info=Framework.evaluate('compile_optimization_info(fn(x){x})');self.assertEqual(set(info),{f.name for f in SHAPES['CompiledOptimizationInfo']})
    def test_ide_shape_completion_and_noexecution(self):
        source='compile_optimization_info(input).'
        result=analyze(source,position=len(source))
        labels={r['label']for r in result['completions']};self.assertTrue({'enabled','kernelCount','commonSubexpressions','checkpointCount','scheduled'}<=labels)
    def test_macro_generates_original_runtime_optimization(self):
        self.assertEqual(Framework.evaluate('macro make(){quote{fn(x){(x+1)*(x+1)}}};let f=compile_function(make!());f(6)'),49)
    def test_executable_guide_and_file(self):
        root=Path(__file__).resolve().parents[1];text=(root/'docs/SCALAR_OPTIMIZER.md').read_text()
        for block in re.findall(r'```chaos[^\n]*\n(.*?)```',text,re.S):self.assertEqual(Framework.evaluate(block),[49,12,'chaos.compiled-optimization/1'])
        self.assertEqual(Framework.evaluate((root/'examples/scalar-optimizer.chaos').read_text()),[49,12,'chaos.compiled-optimization/1'])
