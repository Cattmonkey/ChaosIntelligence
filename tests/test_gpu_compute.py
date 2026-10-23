"""GPU hardware and cold-task ownership contracts, plus portable Float32."""
import sys
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from chaos.language import Evaluator,LanguageError,ResourceLimit,Node
from chaos.suspension import TaskCancelled,Scheduler
from chaos import gpu_compute as gpu


def mps_available():
    if sys.version_info<(3,10):return False
    try:
        import torch
        return torch.backends.mps.is_available()
    except ImportError:return False

MPS=mps_available()


class PortableGPUComputeTests(unittest.TestCase):
    def test_cpu_map_rounds_inputs_literals_and_every_primitive(self):
        source='await gpu_map(fn(x:Number)->Number{(x+0.1)*3.0},[0.1,0.2,-0.3],device="cpu")'
        with mock.patch('chaos.gpu_compute._load_torch',side_effect=AssertionError('CPU must not load Torch')):
            result=Evaluator().run(source)
        expected=[gpu._float32(gpu._float32(gpu._float32(x)+gpu._float32(.1))*gpu._float32(3))for x in (.1,.2,-.3)]
        self.assertEqual(result,{'values':expected,'device':'cpu','backend':'python','dtype':'float32','fallbackReason':None})
        self.assertTrue(all(type(x)is float for x in result['values']))

    def test_cpu_matmul_uses_explicit_float32_primitive_contract(self):
        result=Evaluator().run('await gpu_matmul([[1,2],[3,4]],[[5,6],[7,8]],device="cpu")')
        self.assertEqual(result['values'],[[19.0,22.0],[43.0,50.0]])
        self.assertEqual(result['backend'],'python')
        a=((gpu._float32(.1),gpu._float32(.2)),);b=((gpu._float32(.3),),(gpu._float32(.4),))
        self.assertEqual(gpu.run({'operation':'matmul','device':'cpu','left':a,'right':b},threading.Event())['values'],
                         [[gpu._float32(gpu._float32(a[0][0]*b[0][0])+gpu._float32(a[0][1]*b[1][0]))]])

    def test_unavailable_auto_and_strict_device_requests_are_truthful(self):
        with mock.patch('chaos.gpu_compute._load_torch',return_value=None):
            result=Evaluator().run('await gpu_map(fn(x){x+2},[40])')
            self.assertEqual(result['values'],[42.0]);self.assertEqual(result['fallbackReason'],'backend_unavailable')
            devices=Evaluator().run('await gpu_devices()')
            self.assertEqual(devices,{'backend':'python','version':None,'cpu':True,'mps':False,'cuda':False,'devices':['cpu'],'defaultDevice':'cpu'})
            for device in ('gpu','mps','cuda'):
                with self.subTest(device=device),self.assertRaises(LanguageError) as caught:
                    Evaluator().run('await gpu_map(fn(x){x},[1],device="'+device+'")')
                self.assertEqual(caught.exception.code,'gpu_error')
        with mock.patch('chaos.gpu_compute._load_torch',return_value=object()),mock.patch('chaos.gpu_compute._availability',return_value=(False,False)):
            result=Evaluator().run('await gpu_map(fn(x){x+2},[40])')
            self.assertEqual(result['fallbackReason'],'no_gpu');self.assertEqual(result['device'],'cpu')

    def test_base_initialization_and_ide_analysis_never_import_torch(self):
        script="import sys;from chaos import Evaluator,Framework;Evaluator();Framework.analyze('let t=gpu_devices();let v=(await t).devices');assert 'torch' not in sys.modules"
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_maximum_vector_and_output_matrix_are_accepted(self):
        result=Evaluator(step_limit=100000).run('await gpu_map(fn(x){x+1},range(10000),device="cpu")')
        self.assertEqual(len(result['values']),10000)
        self.assertEqual((result['values'][0],result['values'][-1]),(1.0,10000.0))
        e=Evaluator(step_limit=100000);e.suspension.begin()
        try:
            task=gpu.gpu_matmul(e,[[2]]*100,[[3]*100],device='cpu')
            result=e.suspension.drive(e._s_evaluate(Node(('await',Node(('literal',task)))),e.env))
            self.assertEqual(len(result['values']),100);self.assertEqual(result['values'],[[6.0]*100]*100)
        finally:e.suspension.finish()

    def test_cuda_selection_and_synchronize_adapter_without_cuda_hardware_claim(self):
        synchronized=[]
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,synchronize=lambda device:synchronized.append(device)),
                              backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda:True)),
                              device=lambda value:value)
        self.assertEqual(gpu._select(torch,'auto'),('cuda',None))
        self.assertEqual(gpu._select(torch,'gpu'),('cuda',None))
        self.assertEqual(gpu._select(torch,'cuda'),('cuda',None))
        gpu._synchronize(torch,'cuda')
        self.assertEqual(synchronized,['cuda:0'])

    def test_cold_unawaited_task_never_imports_or_enters_worker(self):
        with mock.patch('chaos.gpu_compute.run',side_effect=AssertionError('cold operation')):
            e=Evaluator();task=e.run('gpu_devices()')
        self.assertEqual(task.kind,'compute');self.assertTrue(task.compute_operation)
        self.assertEqual(task.state,'cancelled')
        self.assertIsNone(e.suspension.pool)

    def test_device_is_keyword_only_and_rejects_unknown_or_nonstring(self):
        for source in ('gpu_map(fn(x){x},[1],"cpu")','gpu_matmul([[1]],[[1]],"cpu")',
                       'gpu_map(fn(x){x},[1],device="metal")','gpu_map(fn(x){x},[1],device=true)'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)

    def test_rejects_int_contract_effects_protocols_and_free_variables(self):
        expressions=['fn(x:Int){x+2}','fn(x)->Int{x+2}','fn(x){tool("probe",x)}',
                     'fn(x){x/2}',
                     'async fn(x){x}','fn*(x){yield x}','fn(x,*,y){x+y}',
                     'fn(value as [x]){x}','fn(x){true}']
        calls=[]
        for expression in expressions:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:
                Evaluator(tool_handler=lambda n,v:calls.append(v)).run('gpu_map('+expression+',[1],device="cpu")')
            self.assertEqual(caught.exception.code,'gpu_error')
        self.assertEqual(calls,[])
        with self.assertRaises(LanguageError):Evaluator().run('let y=2;gpu_map(fn(x){x+y},[1],device="cpu")')

    def test_structured_if_and_local_let_are_now_supported(self):
        for body,expected in (('if x>0{x}else{0}',[1.0,0.0]),('let y=x;y',[1.0,-1.0])):
            with self.subTest(body=body):
                result=Evaluator().run('await gpu_map(fn(x){'+body+'},[1,-1],device="cpu")')
                self.assertEqual(result['values'],expected)

    def test_hygienic_parameter_alias_and_existing_compiled_function(self):
        source='macro build(){quote{fn(x:Number)->Number{x*2+2}}};let f=compile_function(build!());await gpu_map(f,[20],device="cpu")'
        self.assertEqual(Evaluator().run(source)['values'],[42.0])

    def test_frozen_ir_and_values_do_not_retain_function_or_env(self):
        e=Evaluator();fn=e.run('fn(x:Number)->Number{x+2}');e.suspension.begin()
        try:
            values=[40];task=gpu.gpu_map(e,fn,values,device='cpu')
            payload=task.runner.iterator.gi_frame.f_locals['payload']
            self.assertEqual(payload['values'],(40.0,));self.assertIs(type(payload['ir']),tuple)
            self.assertNotIn(fn,tuple(payload.values()))
            values[0]=1;fn.body[1][0]=Node(('literal',9))
            self.assertEqual(e.suspension.drive(e._s_evaluate(Node(('await',Node(('literal',task)))),e.env))['values'],[42.0])
        finally:e.suspension.finish()

    def test_cross_owner_function_and_stale_task_reject(self):
        first=Evaluator();fn=first.run('fn(x){x}');second=Evaluator();second.suspension.begin()
        try:
            with self.assertRaises(LanguageError):gpu.gpu_map(second,fn,[1],device='cpu')
        finally:second.suspension.finish()
        e=Evaluator();task=e.run('gpu_devices()');e.suspension.begin()
        try:
            with self.assertRaises(LanguageError):e.suspension.spawn(task)
        finally:e.suspension.finish()

    def test_finite_number_exact_type_and_float32_range_reject(self):
        class Float(float):pass
        for value in (True,float('inf'),float('nan'),1e100,10**100,Float(1)):
            e=Evaluator();fn=e.run('fn(x){x}');e.suspension.begin()
            try:
                with self.subTest(value=value),self.assertRaises(LanguageError):gpu.gpu_map(e,fn,[value],device='cpu')
            finally:e.suspension.finish()
        with self.assertRaises(LanguageError):Evaluator().run('await gpu_map(fn(x){x*x},[1e30],device="cpu")')

    def test_matrix_shape_size_and_output_bounds(self):
        cases=[([],[[1]]),([[1]],[]),([[1,2],[3]],[[1],[2]]),([[1,2]],[[1]]),
               ([[1]*257],[[1]]),([[1]]*257,[[1]]),([[1]*100]*101,[[1]*100]*100),
               ([[1]]*101,[[1]*100])]
        e=Evaluator();e.suspension.begin()
        try:
            for left,right in cases:
                with self.subTest(rows=len(left)),self.assertRaises(LanguageError):gpu.gpu_matmul(e,left,right,device='cpu')
        finally:e.suspension.finish()
        self.assertEqual(Evaluator().run('await gpu_map(fn(x){x+1},[],device="cpu")')['values'],[])

    def test_ast_limit_and_cycle_are_bounded(self):
        e=Evaluator();fn=e.run('fn(x){x}');e.suspension.begin()
        try:
            node=Node(('name','x'))
            for _ in range(65):node=Node(('unary','+',node))
            fn.body[1][0]=node
            with self.assertRaises(LanguageError):gpu.gpu_map(e,fn,[1],device='cpu')
            fn.body[1][0]=fn.body
            with self.assertRaises(LanguageError):gpu.gpu_map(e,fn,[1],device='cpu')
        finally:e.suspension.finish()

    def test_per_epoch_quota_counts_cold_cancelled_and_completed_tasks(self):
        for body in ('gpu_devices()','cancel(gpu_devices())','await gpu_map(fn(x){x},[1],device="cpu")'):
            e=Evaluator(step_limit=100000)
            with self.subTest(body=body),self.assertRaises(ResourceLimit):e.run('for n in range(65){'+body+'}')
            self.assertEqual(e.suspension.compute_operations,64)
            self.assertEqual(len([t for t in e.suspension.tasks if getattr(t,'compute_operation',False)]),64)
            self.assertEqual(e.run('await gpu_map(fn(x){x+2},[40],device="cpu")')['values'],[42.0])
            self.assertEqual(e.suspension.compute_operations,1)

    def test_recording_scope_access_is_enforced_for_compute_operations(self):
        e=Evaluator();e.suspension.begin()
        try:
            task=gpu.gpu_devices(e);task.recording_scope='recording-one'
            with self.assertRaises(LanguageError) as caught:e.suspension.spawn(task)
            self.assertEqual(caught.exception.code,'mode_error')
            task.recording_scope=None;e.suspension.spawn(task)
        finally:e.suspension.finish()

    def test_internal_gpu_worker_bypasses_external_effect_handler(self):
        e=Evaluator(effect_handler=lambda *args:(_ for _ in ()).throw(AssertionError('GPU cannot delegate to model or provider')))
        self.assertEqual(e.run('await gpu_map(fn(x){x+2},[40],device="cpu")')['values'],[42.0])

    def test_worker_receives_only_copied_numeric_ir_and_no_authority_callbacks(self):
        original=Scheduler._worker;seen=[]
        def worker(effect,event,handler,fallback):
            if effect.kind=='gpu':
                seen.append((effect.payload,handler,fallback))
                self.assertIsNone(handler);self.assertIsNone(fallback)
                self.assertEqual(set(effect.payload),{'operation','device','ir','values','compiler'})
                self.assertIs(type(effect.payload['ir']),tuple)
                self.assertIs(type(effect.payload['values']),tuple)
            return original(effect,event,handler,fallback)
        with mock.patch.object(Scheduler,'_worker',side_effect=worker):
            result=Evaluator().run('await gpu_map(fn(x){x+2},[40],device="cpu")')
        self.assertEqual(result['values'],[42.0]);self.assertEqual(len(seen),1)

    def test_per_device_lock_wait_is_cancellable(self):
        lock=threading.Lock();lock.acquire();event=threading.Event();finished=threading.Event();errors=[]
        def waiting():
            try:gpu._acquire(lock,event)
            except BaseException as error:errors.append(error)
            finally:finished.set()
        thread=threading.Thread(target=waiting);thread.start();event.set()
        try:
            self.assertTrue(finished.wait(1));self.assertIs(type(errors[0]),TaskCancelled)
            self.assertTrue(lock.locked())
        finally:lock.release();thread.join(1)

    def test_cold_await_cancellation_owns_compute_but_spawned_task_is_borrowed(self):
        for borrowed in (False,True):
            started=threading.Event();released=threading.Event();checked=[];events=[];original=gpu.run
            def blocked(payload,event):
                events.append(('started',event));started.set()
                if not released.wait(3):raise AssertionError('worker not drained')
                return original(payload,event)
            def tool(name,value):
                if name=='started':self.assertTrue(started.wait(2));return None
                checked.append(events[0][1].is_set());released.set();return checked[-1]
            e=Evaluator(tool_handler=tool)
            if borrowed:
                source='let shared=spawn(gpu_map(fn(x){x+2},[40],device="cpu"));var me=null;async fn work(){await shared};async fn stop(){tool("started",0);cancel(me);tool("check",0)};me=spawn(work());await stop();await shared'
            else:
                source='var me=null;async fn work(){await gpu_map(fn(x){x+2},[40],device="cpu")};async fn stop(){tool("started",0);cancel(me);await sleep(0);tool("check",0)};me=spawn(work());await stop()'
            try:
                with mock.patch('chaos.gpu_compute.run',side_effect=blocked):result=e.run(source)
                self.assertEqual(checked,[not borrowed])
                operation=next(t for t in e.suspension.tasks if getattr(t,'compute_operation',False))
                self.assertEqual(operation.state,'completed' if borrowed else 'cancelled')
                self.assertIsNone(operation.runner.future)
                if borrowed:self.assertEqual(result['values'],[42.0])
            finally:released.set()


@unittest.skipUnless(MPS,'optional actual MPS hardware unavailable')
class MPSGPUComputeTests(unittest.TestCase):
    def test_actual_mps_map_matmul_and_probe(self):
        probe=Evaluator().run('await gpu_devices()')
        self.assertTrue(probe['mps']);self.assertEqual(probe['defaultDevice'],'mps:0')
        for source,expected in (('await gpu_map(fn(x:Number)->Number{-x*x+2},[-2,0,2],device="mps")',[-2.0,2.0,-2.0]),
                                ('await gpu_map(fn(x){42},[1,2],device="mps")',[42.0,42.0]),
                                ('await gpu_map(fn(x){x+2},[],device="mps")',[]),
                                ('await gpu_matmul([[1,2],[3,4]],[[5,6],[7,8]],device="mps")',[[19.0,22.0],[43.0,50.0]])):
            result=Evaluator().run(source)
            self.assertEqual(result['values'],expected);self.assertEqual(result['device'],'mps:0')
            self.assertEqual(result['backend'],'torch');self.assertEqual(result['dtype'],'float32')
        self.assertEqual(Evaluator().run('await gpu_map(fn(x){x+2},[40],device="gpu")')['device'],'mps:0')

    def test_mps_values_match_portable_cpu_with_public_float32_tolerance(self):
        for operation in ('gpu_map(fn(x){(x+0.1)*3.0},[0.1,0.2,-0.3]',
                          'gpu_matmul([[0.1,0.2],[0.3,0.4]],[[0.2,0.1],[0.4,0.3]]'):
            a=Evaluator().run('await '+operation+',device="cpu")')['values']
            b=Evaluator().run('await '+operation+',device="mps")')['values']
            def compare(left,right):
                if type(left)is list:
                    for x,y in zip(left,right):compare(x,y)
                else:self.assertLessEqual(abs(left-right),1e-6+1e-5*abs(left))
            compare(a,b)

    def test_mps_overflow_is_error_and_cleanup_synchronizes(self):
        import torch
        with mock.patch('torch.mps.synchronize',wraps=torch.mps.synchronize) as synchronized:
            with self.assertRaises(LanguageError) as caught:Evaluator().run('await gpu_map(fn(x){x*x},[1e30],device="mps")')
        self.assertEqual(caught.exception.code,'gpu_error');self.assertGreaterEqual(synchronized.call_count,1)

    def test_cancel_after_gpu_submission_synchronizes_before_worker_exit(self):
        import torch
        event=threading.Event();original=torch.matmul
        def submitted(left,right):
            result=original(left,right);event.set();return result
        payload={'operation':'matmul','device':'mps','left':((1.,2.),),'right':((3.,),(4.,))}
        with mock.patch('torch.matmul',side_effect=submitted),mock.patch('torch.mps.synchronize',wraps=torch.mps.synchronize) as synchronized:
            with self.assertRaises(TaskCancelled):gpu.run(payload,event)
        self.assertEqual(synchronized.call_count,1)
        self.assertFalse(gpu._DEVICE_LOCKS['mps'].locked())

    def test_concurrent_mps_operations_use_original_effect_pool(self):
        e=Evaluator()
        result=e.run('await gather([gpu_map(fn(x){x+2},[40],device="mps"),gpu_matmul([[2]],[[21]],device="mps")])')
        self.assertEqual([v['device']for v in result],['mps:0','mps:0'])
        self.assertEqual([v['values']for v in result],[[42.0],[[42.0]]])
        self.assertTrue(all(t.runner.future is None for t in e.suspension.tasks))


if __name__=='__main__':unittest.main()
