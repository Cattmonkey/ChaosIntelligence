"""Opt-in fused compiler contracts, isolation failures, and real MPS evidence."""
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from chaos.language import Evaluator,LanguageError,Node
from chaos.suspension import TaskCancelled
from chaos import gpu_compute as gpu
from chaos import gpu_metal as metal
from test_gpu_compute import MPS

I=('input',)
L=lambda value:('literal',float(value))
B=lambda op,left,right:('binary',op,left,right)
FIELDS={'requestedCompiler','compiler','status','irHash','sourceHash','sourceCacheHit','dispatches','isolated','fallbackReason'}
RESULT={'values','device','backend','dtype','fallbackReason'}


class GPUCompilerPortableTests(unittest.TestCase):
    def test_legacy_result_and_default_compiler_are_preserved(self):
        result=Evaluator().run('let t=gpu_map(fn(x){x+2},[40],device="cpu");let r=await t;[r,gpu_kernel_info(t)]')
        self.assertEqual(set(result[0]),RESULT);self.assertEqual(result[0]['values'],[42.0])
        info=result[1];self.assertEqual(set(info),FIELDS);self.assertEqual(info['compiler'],'tensor')
        self.assertEqual(info['status'],'completed');self.assertEqual(info['dispatches'],0);self.assertFalse(info['isolated'])
        self.assertIsNone(info['sourceHash']);self.assertIsNone(info['sourceCacheHit'])

    def test_cold_info_is_detached_nonobserving_and_hardware_lazy(self):
        with mock.patch('chaos.gpu_compute._load_torch',side_effect=AssertionError('cold')):
            e=Evaluator();result=e.run('let job=gpu_map(fn(x){x+2},[1],compiler="auto");[gpu_kernel_info(job),task_info(job)]')
        self.assertEqual(result[0]['status'],'created');self.assertIsNone(result[0]['compiler'])
        self.assertFalse(result[1]['observed']);self.assertNotIn('sourceHash',result[1])
        self.assertIsNone(e.suspension.pool)
        snapshot=Evaluator().run('let t=gpu_map(fn(x){x},[1],device="cpu");let before=gpu_kernel_info(t);await t;[before,gpu_kernel_info(t)]')
        self.assertEqual(snapshot[0]['status'],'created');self.assertIsNone(snapshot[0]['compiler'])
        self.assertEqual(snapshot[1]['status'],'completed')

    def test_query_owner_epoch_recording_and_original_map_handle(self):
        e=Evaluator();fn=e.run('fn(x){x}');e.suspension.begin()
        try:
            t=gpu.gpu_map(e,fn,[1],device='cpu')
            self.assertEqual(gpu.gpu_kernel_info(e,t)['status'],'created');self.assertFalse(t.observed)
            t.recording_scope='other'
            with self.assertRaises(LanguageError) as caught:gpu.gpu_kernel_info(e,t)
            self.assertEqual(caught.exception.code,'mode_error');t.recording_scope=None
            other=Evaluator();other.suspension.begin()
            try:
                with self.assertRaises(LanguageError):gpu.gpu_kernel_info(other,t)
            finally:other.suspension.finish()
            for invalid in (gpu.gpu_devices(e),gpu.gpu_matmul(e,[[1]],[[1]],device='cpu'),42):
                with self.assertRaises(LanguageError):gpu.gpu_kernel_info(e,invalid)
        finally:e.suspension.finish()
        e.suspension.begin()
        try:
            with self.assertRaises(LanguageError):gpu.gpu_kernel_info(e,t)
        finally:e.suspension.finish()
        self.assertEqual(Evaluator().run('let t=gpu_map(fn(x){x},[1],device="cpu");let alias=t;gpu_kernel_info(alias)')['requestedCompiler'],'tensor')

    def test_compiler_keyword_only_validation_and_strict_metal(self):
        for source in ('gpu_map(fn(x){x},[1],compiler="llvm")','gpu_map(fn(x){x},[1],compiler=true)',
                       'gpu_map(fn(x){x},[1],"cpu","metal")','gpu_matmul([[1]],[[1]],compiler="metal")',
                       'await gpu_map(fn(x){x},[1],device="cpu",compiler="metal")'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)

    def test_auto_cpu_fallback_separates_device_and_compiler_reasons(self):
        with mock.patch('chaos.gpu_compute._load_torch',return_value=None):
            result=Evaluator().run('let t=gpu_map(fn(x){x+2},[40],compiler="auto");let r=await t;[r,gpu_kernel_info(t)]')
        self.assertEqual(result[0]['fallbackReason'],'backend_unavailable')
        self.assertEqual(result[1]['fallbackReason'],'non_mps_device');self.assertEqual(result[1]['compiler'],'tensor')
        self.assertEqual(result[1]['dispatches'],0);self.assertFalse(result[1]['isolated'])

    def test_canonical_ir_uses_bits_and_does_not_retain_names_values_or_handles(self):
        self.assertNotEqual(metal.identity(L(0.0)),metal.identity(L(-0.0)))
        self.assertNotEqual(metal.source_identity(L(0.0)),metal.source_identity(L(-0.0)))
        a=Evaluator().run('gpu_kernel_info(gpu_map(fn(first){first+2},[1],device="cpu"))')
        b=Evaluator().run('gpu_kernel_info(gpu_map(fn(other){other+2},[40],device="cpu"))')
        self.assertEqual(a['irHash'],b['irHash'])
        self.assertNotEqual(a['irHash'],Evaluator().run('gpu_kernel_info(gpu_map(fn(x){x+3},[1],device="cpu"))')['irHash'])
        text=metal.source(B('+',B('*',I,I),L(2)))
        self.assertIn('volatile float',text);self.assertIn('contract(off)',text);self.assertIn('reassociate(off)',text)
        self.assertIn('as_type<float>(0x40000000u)',text)
        for invalid in (('call','tool'),('literal',True),('literal',float('inf')),('binary','/',I,L(2)),('name','x')):
            with self.subTest(ir=invalid),self.assertRaises(ValueError):metal.source(invalid)
        deep=I
        for _ in range(65):deep=('unary','+',deep)
        with self.assertRaises(ValueError):metal.identity(deep)

    def test_worker_request_is_bounded_fixed_numeric_json(self):
        def request(ir,values):return {'schema':1,'ir':ir,'values':values,'irHash':metal.identity(ir),'sourceHash':metal.source_identity(ir)}
        valid=request(I,[1.0]);decoded=metal._request(io.BytesIO(json.dumps(valid).encode()+b'\n'))
        self.assertEqual(decoded[1],[1.0])
        cases=[{**valid,'source':'arbitrary shader'},{**valid,'values':[True]},
               {**valid,'irHash':'wrong'},{**valid,'schema':True},{**valid,'values':[1.0]*10001}]
        for packet in cases:
            with self.assertRaises(ValueError):metal._request(io.BytesIO(json.dumps(packet).encode()+b'\n'))
        with self.assertRaises(ValueError):metal._request(io.BytesIO(b'x'*(metal.MAX_BYTES+1)))

    def test_base_initialization_and_analysis_do_not_import_torch_or_launch_worker(self):
        script="import sys;from chaos import Evaluator,Framework;Evaluator();Framework.analyze('let t=gpu_map(fn(x){x},[1],compiler=\"metal\");gpu_kernel_info(t)');assert 'torch' not in sys.modules"
        p=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_queued_cancel_prevents_child_spawn(self):
        event=threading.Event();event.set()
        with mock.patch('chaos.gpu_metal.subprocess.Popen',side_effect=AssertionError('must stay cold')):
            with self.assertRaises(metal.MetalCancelled):metal.execute(I,[1.0],event)

    def test_timeout_covers_large_initial_pipe_and_reaps_child(self):
        original=subprocess.Popen;children=[]
        def hung(command,**kwargs):
            children.append(original([sys.executable,'-c','import time;time.sleep(10)'],**kwargs));return children[-1]
        with mock.patch('chaos.gpu_metal.subprocess.Popen',side_effect=hung),mock.patch.object(metal,'WORKER_TIMEOUT',.15):
            start=time.monotonic()
            with self.assertRaises(metal.MetalFailure) as caught:metal.execute(I,[3.1415927410125732]*10000,threading.Event())
        self.assertLess(time.monotonic()-start,2);self.assertIn('drain is unverified',str(caught.exception))
        self.assertIsNotNone(children[0].poll())

    def test_crash_malformed_and_oversized_response_reap_without_replay(self):
        original=subprocess.Popen
        for script in ('raise SystemExit(7)','print("not-json")','print("x"*1100000)'):
            children=[]
            def child(command,**kwargs):
                children.append(original([sys.executable,'-c',script],**kwargs));return children[-1]
            with self.subTest(script=script),mock.patch('chaos.gpu_metal.subprocess.Popen',side_effect=child):
                with self.assertRaises(metal.MetalFailure):metal.execute(I,[1.0],threading.Event())
            self.assertIsNotNone(children[0].poll())

    def test_auto_compile_failure_fallback_releases_lock_before_tensor(self):
        info=gpu._pending('auto',I);payload={'operation':'map','ir':I,'values':(1.0,),'device':'mps','compiler':'auto'}
        with mock.patch('chaos.gpu_metal.execute',side_effect=metal.MetalFailure('compiler failure',phase='compile',dispatches=0)):
            self.assertIsNone(gpu._metal_result(payload,threading.Event(),info))
        self.assertFalse(gpu._DEVICE_LOCKS['mps'].locked());self.assertEqual(info['fallbackReason'],'metal_compile_failed')
        for phase,dispatches in (('unknown',None),('execute',0),('execute',1)):
            with mock.patch('chaos.gpu_metal.execute',side_effect=metal.MetalFailure('no replay',phase=phase,dispatches=dispatches)):
                with self.assertRaises(LanguageError):gpu._metal_result(payload,threading.Event(),gpu._pending('auto',I))
            self.assertFalse(gpu._DEVICE_LOCKS['mps'].locked())


@unittest.skipUnless(MPS,'optional actual MPS hardware unavailable')
class ActualMetalCompilerTests(unittest.TestCase):
    def test_real_language_shader_dispatch_empty_constant_and_snapshot(self):
        for body,values,expected in (('x*x+2','[1,2,3]',[3.,6.,11.]),('42','[1,2]',[42.,42.]),('-x','[]',[])):
            result=Evaluator().run('let t=gpu_map(fn(x){'+body+'},'+values+',device="mps",compiler="metal");let before=gpu_kernel_info(t);let result=await t;[before,result,gpu_kernel_info(t)]')
            before,value,info=result;self.assertIsNone(before['compiler']);self.assertEqual(before['status'],'created')
            self.assertEqual(set(value),RESULT);self.assertEqual(value['values'],expected);self.assertEqual(value['device'],'mps:0')
            self.assertEqual(info['compiler'],'metal');self.assertTrue(info['isolated']);self.assertFalse(info['sourceCacheHit'])
            self.assertEqual(info['dispatches'],int(bool(expected)));self.assertEqual(info['status'],'completed')
            self.assertEqual(len(info['sourceHash']),64)

    def test_actual_generated_float32_fma_negative_zero_and_subnormal_policy(self):
        ir=B('+',B('*',I,L(1.0000001192092896)),L(-1.0))
        values,device,count,sourcehash=metal.execute(ir,[1.0000001192092896],threading.Event())
        self.assertEqual(values,[2.384185791015625e-7]);self.assertEqual(device,'mps:0');self.assertEqual(count,1)
        zeros=metal.execute(B('*',I,L(-1)),[0.,-0.],threading.Event())[0]
        self.assertEqual([struct.pack('!f',v).hex() for v in zeros],['80000000','00000000'])
        tiny=metal.execute(B('*',I,L(.5)),[1.1754943508222875e-38],threading.Event())[0][0]
        self.assertTrue(math.isfinite(tiny));self.assertIn(tiny,(0.,5.877471754111438e-39))

    def test_actual_intermediate_overflow_is_error_not_optimized_to_zero(self):
        for body in ('(x*x)*0','(x*x)-(x*x)','(x+x)-x'):
            with self.subTest(body=body),self.assertRaises(LanguageError) as caught:
                Evaluator().run('await gpu_map(fn(x){'+body+'},['+('3e38' if body=='(x+x)-x' else '1e30')+'],device="mps",compiler="metal")')
            self.assertEqual(caught.exception.code,'gpu_error');self.assertFalse(gpu._DEVICE_LOCKS['mps'].locked())

    def test_real_auto_selects_metal_and_missing_api_falls_back_to_tensor(self):
        e=Evaluator();result=e.run('let t=gpu_map(fn(x){x+2},[40],device="mps",compiler="auto");await t;gpu_kernel_info(t)')
        self.assertEqual(result['compiler'],'metal');self.assertIsNone(result['fallbackReason'])
        import torch
        with mock.patch.object(torch.mps,'compile_shader',None):
            result=Evaluator().run('let t=gpu_map(fn(x){x+2},[40],device="mps",compiler="auto");let r=await t;[r,gpu_kernel_info(t)]')
            self.assertEqual(result[0]['values'],[42.]);self.assertEqual(result[1]['compiler'],'tensor');self.assertEqual(result[1]['fallbackReason'],'metal_unavailable')
            with self.assertRaises(LanguageError):Evaluator().run('await gpu_map(fn(x){x},[1],device="mps",compiler="metal")')

    def test_real_maximum_vector_and_mutated_function_snapshot(self):
        e=Evaluator(step_limit=100000);result=e.run('await gpu_map(fn(x){x+1},range(10000),device="mps",compiler="metal")')
        self.assertEqual(len(result['values']),10000);self.assertEqual(result['values'][-1],10000.)
        e=Evaluator();fn=e.run('fn(x){x+2}');e.suspension.begin()
        try:
            t=gpu.gpu_map(e,fn,[40],device='mps',compiler='metal');before=gpu.gpu_kernel_info(e,t)
            fn.body[1][0]=Node(('literal',9))
            value=e.suspension.drive(e._s_evaluate(Node(('await',Node(('literal',t)))),e.env))
            self.assertEqual(value['values'],[42.]);self.assertEqual(gpu.gpu_kernel_info(e,t)['irHash'],before['irHash'])
        finally:e.suspension.finish()

    def test_real_compile_dispatch_sync_interception_and_clean_environment(self):
        original=subprocess.Popen;children=[]
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)/'evidence.json'
            script='''import runpy,torch,json,os
from pathlib import Path
m=runpy.run_path(%r)
counts={'compile':0,'dispatch':0,'sync':0,'envKeys':sorted(os.environ)}
original_compile=torch.mps.compile_shader
original_sync=torch.mps.synchronize
class Library:
 def __init__(self,library):self.library=library
 @property
 def chaos_map(self):
  fn=self.library.chaos_map
  def call(*args,**kwargs):counts['dispatch']+=1;return fn(*args,**kwargs)
  return call
def compile(source):counts['compile']+=1;return Library(original_compile(source))
def sync():
 counts['sync']+=1
 original_sync()
 Path(%r).write_text(json.dumps(counts))
torch.mps.compile_shader=compile;torch.mps.synchronize=sync
m['worker']()
'''%(str(Path(metal.__file__).resolve()),str(evidence))
            def instrument(command,**kwargs):
                self.assertEqual(set(kwargs['env']),{'PATH','PYTHONNOUSERSITE','PYTHONDONTWRITEBYTECODE'})
                children.append(original([sys.executable,'-c',script],**kwargs));return children[-1]
            with mock.patch('chaos.gpu_metal.subprocess.Popen',side_effect=instrument):
                self.assertEqual(metal.execute(B('+',B('*',I,I),L(2)),[1.,2.],threading.Event())[0],[3.,6.])
            counts=json.loads(evidence.read_text());self.assertEqual([counts[k]for k in ('compile','dispatch','sync')],[1,1,1])
            self.assertNotIn('OPENAI_API_KEY',counts['envKeys']);self.assertNotIn('PYTORCH_MPS_FAST_MATH',counts['envKeys'])
            self.assertIsNotNone(children[0].poll())

    def test_cancel_during_compile_or_after_dispatch_drains_reaps_and_does_not_block_parent(self):
        original=subprocess.Popen
        for phase in ('compile','dispatch'):
            with tempfile.TemporaryDirectory() as directory:
                marker=Path(directory)/'marker';evidence=Path(directory)/'evidence';children=[];event=threading.Event()
                script='''import runpy,torch,time,json
from pathlib import Path
m=runpy.run_path(%r);counts={'dispatch':0,'sync':0}
oc=torch.mps.compile_shader;osync=torch.mps.synchronize
class Library:
 def __init__(self,lib):self.lib=lib
 @property
 def chaos_map(self):
  kernel=self.lib.chaos_map
  def call(*a,**k):
   counts['dispatch']+=1;value=kernel(*a,**k)
   if %r=='dispatch':Path(%r).write_text('ready');time.sleep(.2)
   return value
  return call
def compile(source):
 lib=oc(source)
 if %r=='compile':Path(%r).write_text('ready');time.sleep(.2)
 return Library(lib)
def sync():counts['sync']+=1;osync();Path(%r).write_text(json.dumps(counts))
torch.mps.compile_shader=compile;torch.mps.synchronize=sync
m['worker']()
if not Path(%r).exists():Path(%r).write_text(json.dumps(counts))
'''%(str(Path(metal.__file__).resolve()),phase,str(marker),phase,str(marker),str(evidence),str(evidence),str(evidence))
                def instrument(command,**kwargs):children.append(original([sys.executable,'-c',script],**kwargs));return children[-1]
                def cancel():
                    deadline=time.monotonic()+3
                    while not marker.exists() and time.monotonic()<deadline:time.sleep(.005)
                    event.set()
                thread=threading.Thread(target=cancel);thread.start()
                beats=0;done=threading.Event()
                def heartbeat():
                    nonlocal beats
                    while not done.is_set():beats+=1;time.sleep(.005)
                heartbeat_thread=threading.Thread(target=heartbeat);heartbeat_thread.start()
                try:
                    with mock.patch('chaos.gpu_metal.subprocess.Popen',side_effect=instrument):
                        with self.assertRaises(metal.MetalCancelled):metal.execute(B('+',I,L(2)),[40.],event)
                    self.assertTrue(marker.exists());counts=json.loads(evidence.read_text())
                    self.assertEqual(counts['dispatch'],int(phase=='dispatch'));self.assertEqual(counts['sync'],int(phase=='dispatch'))
                    self.assertGreater(beats,10);self.assertIsNotNone(children[0].poll())
                finally:done.set();thread.join(3);heartbeat_thread.join(1)


if __name__=='__main__':unittest.main()
