import unittest
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError
from chaos.jit import compile_function,compile_info,compile_optimization_info
from chaos.native_jit import _backend,BackendUnavailable
try:_backend();NATIVE=True
except BackendUnavailable:NATIVE=False

class OptimizerRuntime(unittest.TestCase):
    def test_uncompiled_inert_and_invalid_owner(self):
        e=Evaluator();f=e.run('fn(x){x+1}')
        with patch('chaos.native_jit.compile_kernel',side_effect=AssertionError('compile from inspection')):
            self.assertFalse(compile_optimization_info(e,f)['enabled']);self.assertEqual(compile_optimization_info(e,f)['status'],'not_compiled')
        with self.assertRaises(LanguageError):compile_optimization_info(Evaluator(),f)
        with self.assertRaises(LanguageError):compile_optimization_info(e,42)
    def test_backend_fallback_metadata_truthful(self):
        e=Evaluator();f=e.run('fn(x){x+1}')
        with patch('chaos.native_jit._backend',side_effect=BackendUnavailable('unavailable')):fast=compile_function(e,f)
        info=compile_optimization_info(e,fast);self.assertFalse(info['enabled']);self.assertEqual(info['kernelCount'],0)
        self.assertEqual(info['emittedOperations'],0);self.assertEqual(len(compile_info(e,fast)),12)
    def test_effect_context_callbacks_not_run_by_compile_or_inspection(self):
        e=Evaluator();result=e.run('let v=context_var("x",0);let f=compile_function(fn(x){v.set(x);v.get()});[compile_optimization_info(f).enabled,v.get(),compile_info(f).status]')
        self.assertEqual(result,[False,0,'unsupported'])
    @unittest.skipUnless(NATIVE,'native LLVM unavailable')
    def test_real_ir_cse_and_result(self):
        e=Evaluator();f=compile_function(e,e.run('fn(x){(x+1)*(x+1)}'),'native')
        text=str(f.compiled_plan.kernel._module);self.assertEqual(text.count('add i64'),1);self.assertEqual(text.count('mul i64'),1)
        self.assertEqual(e.invoke(f,[6]),49);self.assertEqual(compile_info(e,f)['nativeCalls'],1)
        self.assertEqual(len(compile_info(e,f)),12)
    @unittest.skipUnless(NATIVE,'native LLVM unavailable')
    def test_structured_original_control_flow(self):
        result=Evaluator().run('fn score(x){let y=(x+1)*(x+1);if x<2 {y+2}else{y+3}}let f=compile_function(score,"native");[f(1),f(2),compile_optimization_info(f).kernelCount,compile_optimization_info(f).commonSubexpressions]')
        self.assertEqual(result,[6,12,4,1])
    @unittest.skipUnless(NATIVE,'native LLVM unavailable')
    def test_large_integer_fallback_still_exact(self):
        e=Evaluator();f=compile_function(e,e.run('fn(x){(x+1)*(x+1)}'),'native');x=2**80
        self.assertEqual(e.invoke(f,[x]),(x+1)**2);self.assertEqual(compile_info(e,f)['lastFallbackReason'],'argument_guard')
    @unittest.skipUnless(NATIVE,'native LLVM unavailable')
    def test_debug_trace_fallback_preserved(self):
        e=Evaluator();f=compile_function(e,e.run('fn(x){(x+1)*(x+1)}'),'native');e.trace_handler=lambda event:None
        self.assertEqual(e.invoke(f,[6]),49);self.assertEqual(compile_info(e,f)['nativeCalls'],0)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'expression_trace')
    @unittest.skipUnless(NATIVE,'native LLVM unavailable')
    def test_info_never_exports_source_or_address_and_copy_is_defensive(self):
        e=Evaluator();f=compile_function(e,e.run('fn(privateMarker){(privateMarker+1)*(privateMarker+1)}'),'native')
        info=compile_optimization_info(e,f);self.assertNotIn('privateMarker',str(info));self.assertNotIn('address',info)
        info['kernels'][0]['emittedOperations']=999;self.assertEqual(compile_optimization_info(e,f)['emittedOperations'],2)

    @unittest.skipUnless(NATIVE,'native LLVM unavailable')
    def test_every_budget_threshold_preserves_value_error_and_span(self):
        for source,args in [('fn(x){(x+1)*(x+1)}',[6]),('fn(x){x+(2*3)+(2*3)}',[6]),('fn(a,b,c,d,e,f){(a+b)+(((c*d)*e)*f)}',[1,2,3,4,5,6]),('fn(x){let y=(x+1)*(x+1);if x<2{y+2}else{y+3}}',[2])]:
            left=Evaluator();right=Evaluator();a=compile_function(left,left.run(source),'native');b=compile_function(right,right.run(source),'interpreter')
            def run(e,f,limit):
                e.steps=0;e.step_limit=limit
                try:value=('value',e.invoke(f,args))
                except LanguageError as error:value=('error',error.record())
                return value,e.steps
            self.assertEqual(run(left,a,1000000),run(right,b,1000000));count=left.steps
            for limit in range(count+2):self.assertEqual(run(left,a,limit),run(right,b,limit),(source,limit))
    @unittest.skipUnless(NATIVE,'native LLVM unavailable')
    def test_every_cancel_threshold_keeps_original_logical_tape(self):
        source='fn(x){return (x+1)*(x+1)+(2*3)}'
        left=Evaluator();right=Evaluator();a=compile_function(left,left.run(source),'native');b=compile_function(right,right.run(source),'interpreter')
        def run(e,f,threshold):
            e.steps=0;e.cancelled=lambda:e.steps>=threshold
            try:value=('value',e.invoke(f,[6]))
            except LanguageError as error:value=('error',error.record())
            return value,e.steps
        self.assertEqual(run(left,a,1000000),run(right,b,1000000));count=left.steps
        for threshold in range(count+2):self.assertEqual(run(left,a,threshold),run(right,b,threshold),threshold)
