"""Actual MCJIT construction, callback isolation and live artifact lifetime."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

from chaos.jit import Expression, compile_function, compile_info
from chaos.jit_flow import FlowProgram
from chaos.language import Evaluator, LanguageError
from chaos.native_code_cache import NativeCodeCache
from chaos.native_jit import BackendUnavailable, compile_kernel
from chaos.native_flow import compile_flow
from test_native_jit import NATIVE


def expression(operator='+',constant=2,event=3):
    return Expression('binary',operator,(Expression('name',0,(),1),
                      Expression('literal',constant,(),2)),0,event)


def flow(flag=1,flag_count=1):
    return FlowProgram((('flag',0,flag),('branch',0,2,3),('eval',),('done',)),flag_count)


@contextmanager
def cache_scope(cache):
    def scalar(expr,prefix,**kwargs):return compile_kernel(expr,prefix,code_cache=cache)
    def control(program,**kwargs):return compile_flow(program,code_cache=cache)
    with patch('chaos.native_jit.compile_kernel',side_effect=scalar), \
         patch('chaos.native_flow.compile_flow',side_effect=control):
        yield


class NativeCodeCachePortable(unittest.TestCase):
    def test_cold_limits_info_copy_and_clear_need_no_backend(self):
        with patch('chaos.native_jit._backend',side_effect=AssertionError('loaded backend')):
            cache=NativeCodeCache(max_entries=2,max_ir_bytes=1000)
            first=cache.info();self.assertEqual(first['schema'],'chaos.native-code-cache/1')
            self.assertEqual(first['entries'],0);self.assertEqual(first['irBytes'],0)
            first['limits']['maxEntries']=999
            self.assertEqual(cache.info()['limits'],{'maxEntries':2,'maxIRBytes':1000})
            before=cache.info()['generation'];cache.clear()
            self.assertGreater(cache.info()['generation'],before)
        for options in ({'max_entries':True},{'max_entries':-1},{'max_entries':65},
                        {'max_ir_bytes':True},{'max_ir_bytes':-1},{'max_ir_bytes':4000001}):
            with self.subTest(options=options),self.assertRaises((ValueError,TypeError)):
                NativeCodeCache(**options)

    def test_unavailable_backend_keeps_scalar_fallback_and_portable_flow(self):
        cache=NativeCodeCache()
        with cache_scope(cache),patch('chaos.native_jit._backend',side_effect=BackendUnavailable('fixture unavailable')), \
             patch('chaos.native_flow._backend',side_effect=BackendUnavailable('fixture unavailable')):
            e=Evaluator();f=compile_function(e,e.run('fn(x){x+2}'),'auto')
            self.assertEqual(e.invoke(f,[40]),42)
            self.assertEqual(compile_info(e,f)['fallbackReason'],'fixture unavailable')
            plain=e.run('fn(callback){callback()}');g=compile_function(e,plain,'auto-flow')
            callback=e.run('fn(){42}');self.assertEqual(e.invoke(g,[callback]),42)
            with self.assertRaises(LanguageError):compile_function(e,plain,'native-flow')
        self.assertEqual(cache.info()['entries'],0)
        self.assertEqual(cache.info()['builds'],0)


@unittest.skipUnless(NATIVE,'requires optional actual llvmlite 0.45 backend')
class NativeCodeCacheActual(unittest.TestCase):
    def test_repeated_scalar_builds_one_actual_engine_with_fresh_wrappers(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler
        with patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds:
            kernels=[compile_kernel(expression(),(),code_cache=cache) for _ in range(3)]
            self.assertEqual(builds.call_count,1)
        self.assertEqual(len({id(k) for k in kernels}),3)
        self.assertEqual(len({id(k._engine) for k in kernels}),1)
        self.assertIsNot(kernels[0].optimization,kernels[1].optimization)
        observed=[]
        for index,kernel in enumerate(kernels):
            callbacks=[];self.assertEqual(kernel.invoke([40+index],callbacks.append),42+index)
            observed.append(callbacks)
        self.assertEqual(observed,[[0,1,2,3]]*3)
        failure=ValueError('this invocation only')
        def reject(event):raise failure
        with self.assertRaises(ValueError) as caught:kernels[0].invoke([40],reject)
        self.assertIs(caught.exception,failure)
        recovered=[]
        self.assertEqual(kernels[1].invoke([40],recovered.append),42)
        self.assertEqual(recovered,[0,1,2,3])
        with patch('chaos.native_jit._backend',side_effect=BackendUnavailable('warm unavailable')):
            with self.assertRaises(BackendUnavailable):compile_kernel(expression(),(),code_cache=cache)

    def test_operator_constant_checkpoint_tapes_and_disabled_requests_are_real_misses(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler
        variants=[(expression(),()),(expression(),()),(expression('-'),()),
                  (expression(constant=3),()),(expression(),(4,)),(expression(event=5),())]
        with patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds:
            kernels=[compile_kernel(expr,prefix,code_cache=cache) for expr,prefix in variants]
            self.assertEqual(builds.call_count,5)
            for _ in range(2):compile_kernel(expression(),(),code_cache=None)
            self.assertEqual(builds.call_count,7)
        self.assertEqual([k.invoke([40],lambda _:None) for k in kernels],[42,42,38,43,42,42])
        seen=[];kernels[-2].invoke([40],seen.append);self.assertEqual(seen,[4,0,1,2,3])
        seen=[];kernels[-1].invoke([40],seen.append);self.assertEqual(seen,[0,1,2,5])

    def test_flow_layout_reuses_engine_but_flags_pc_and_wrappers_are_local(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler
        with patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds:
            a=compile_flow(flow(),code_cache=cache);b=compile_flow(flow(),code_cache=cache)
            c=compile_flow(flow(flag=0),code_cache=cache)
            self.assertEqual(builds.call_count,2)
        self.assertIsNot(a,b);self.assertIs(a._engine,b._engine)
        left=[0];right=[0]
        self.assertEqual(a.resume(0,left),(3,3));self.assertEqual(left,[1]);self.assertEqual(right,[0])
        self.assertEqual(b.resume(0,right),(3,3));self.assertEqual(b.resume(3,right),(3,0))
        self.assertEqual(c.resume(0,[0]),(3,0))
        wide=compile_flow(flow(flag_count=2),code_cache=cache)
        with self.assertRaises(ValueError):wide.resume(0,[0])
        self.assertEqual(wide.resume(0,[0,0]),(3,3))

    def test_eviction_clear_and_oversize_never_invalidate_active_machine_code(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache(max_entries=1,max_ir_bytes=2000000);original=llvm.create_mcjit_compiler
        with patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds:
            first=compile_kernel(expression(),(),code_cache=cache)
            second=compile_kernel(expression('-'),(),code_cache=cache)
            self.assertEqual(cache.info()['entries'],1);self.assertGreaterEqual(cache.info()['evictions'],1)
            self.assertEqual(first.invoke([40],lambda _:None),42)
            cache.clear();self.assertEqual(cache.info()['entries'],0)
            self.assertEqual(second.invoke([40],lambda _:None),38)
            compile_kernel(expression(),(),code_cache=cache);self.assertEqual(builds.call_count,3)
        tiny=NativeCodeCache(max_entries=2,max_ir_bytes=0)
        with patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds:
            for _ in range(2):
                k=compile_kernel(expression(),(),code_cache=tiny)
                self.assertEqual(k.invoke([40],lambda _:None),42)
            self.assertEqual(builds.call_count,2);self.assertEqual(tiny.info()['entries'],0)
            self.assertGreaterEqual(tiny.info()['oversize'],2)

    def test_failed_build_retries_and_reentrant_clear_does_not_republish(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler;calls=[]
        def fail_once(*args,**kwargs):
            calls.append(1)
            if len(calls)==1:raise RuntimeError('controlled actual build failure')
            return original(*args,**kwargs)
        with patch.object(llvm,'create_mcjit_compiler',side_effect=fail_once):
            with self.assertRaises((RuntimeError,BackendUnavailable)):compile_kernel(expression(),(),code_cache=cache)
            self.assertEqual(cache.info()['entries'],0)
            second=compile_kernel(expression(),(),code_cache=cache)
            compile_kernel(expression(),(),code_cache=cache)
        self.assertEqual(len(calls),2);self.assertEqual(second.invoke([40],lambda _:None),42)
        cache.clear();generation=cache.info()['generation']
        def clear_during_real_build(*args,**kwargs):
            cache.clear();return original(*args,**kwargs)
        with patch.object(llvm,'create_mcjit_compiler',side_effect=clear_during_real_build):
            active=compile_kernel(expression(),(),code_cache=cache)
        self.assertGreater(cache.info()['generation'],generation)
        self.assertEqual(cache.info()['entries'],0)
        self.assertEqual(active.invoke([40],lambda _:None),42)

    def test_simultaneous_identical_requests_build_once_and_callbacks_do_not_mix(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler;barrier=threading.Barrier(4)
        def work(index):
            barrier.wait(timeout=5)
            kernel=compile_kernel(expression(),(),code_cache=cache);seen=[]
            result=kernel.invoke([index],lambda event:seen.append((index,event)))
            return kernel,result,seen
        with patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds,ThreadPoolExecutor(max_workers=4) as pool:
            values=list(pool.map(work,range(4)))
            self.assertEqual(builds.call_count,1)
        self.assertEqual(len({id(item[0]) for item in values}),4)
        for index,(_,result,seen) in enumerate(values):
            self.assertEqual(result,index+2);self.assertEqual(seen,[(index,event) for event in (0,1,2,3)])

    def test_shared_helper_separates_exact_abi_symbol_versions_target_and_layout(self):
        import ctypes
        from chaos.native_jit import _backend
        from chaos.native_code_cache import compile_generated_module
        llvm,ir,version=_backend();target=llvm.get_default_triple()
        machine=llvm.Target.from_default_triple().create_target_machine()
        module=ir.Module(name='cache_key_probe');module.triple=target
        module.data_layout=str(machine.target_data)
        for symbol in ('answer','alias'):
            function=ir.Function(module,ir.FunctionType(ir.IntType(64),[]),name=symbol)
            ir.IRBuilder(function.append_basic_block('entry')).ret(ir.Constant(ir.IntType(64),42))
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler
        def build(symbol='answer',abi='i64-noargs/1',backend_version=version,reported_target=target):
            # MCJIT takes ownership of its target machine; never hand the same
            # native target-machine pointer to two distinct execution engines.
            current_machine=llvm.Target.from_default_triple().create_target_machine()
            return compile_generated_module(llvm,module,current_machine,symbol,abi,backend_version,reported_target,code_cache=cache)
        with patch.object(llvm,'create_mcjit_compiler',wraps=original) as engines:
            first=build();self.assertIs(build(),first)
            artifacts=[first,build(abi='i64-noargs/2'),build(symbol='alias'),
                       build(backend_version=version+'-metadata-probe')]
            # Deliberate metadata perturbations verify key separation; the actual
            # native machine/module still targets this host. No foreign target claim.
            with patch.object(llvm,'llvm_version_info',llvm.llvm_version_info+(1,)):
                artifacts.append(build())
            artifacts.append(build(reported_target=target+'-metadata-probe'))
            # Add unused address-space layout information, keeping this no-arg
            # constant-return native function ABI safe and genuinely verifiable.
            module.data_layout=str(machine.target_data)+'-p1:64:64'
            artifacts.append(build())
            self.assertEqual(engines.call_count,7)
        for artifact in artifacts:
            self.assertEqual(ctypes.CFUNCTYPE(ctypes.c_int64)(artifact.address)(),42)
