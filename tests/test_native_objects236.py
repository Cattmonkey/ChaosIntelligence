"""Real emitted native objects, authenticated host reuse and unchanged semantics."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import chaos
from chaos import Evaluator, Framework, Registry, Settings, Final, ToolCall, LanguageError
from chaos.jit import compile_function, compile_info
from chaos.jit_flow import info as flow_info
from chaos.language import Native, ResourceLimit, serializable
from chaos.native_code_cache import NativeCodeCache
from chaos.native_objects import NativeObjectCache
from chaos.native_jit import compile_kernel, BackendUnavailable
from chaos.native_flow import compile_flow
from test_native_code_cache import expression, flow
from test_native_jit import NATIVE
from _generator_flow208 import observe_states

ROOT=Path(__file__).resolve().parents[1]
FOUR='''var hits=[];
let f=choose(fn(x){hits=append(hits,"fn");x+1});
let a=choose(async fn(x){hits=append(hits,"async");await sleep(0);x+2});
let g=choose(fn*(x){hits=append(hits,"gen");let reply=yield x;yield reply;return_values(x+3,x+4)});
let ag=choose(async fn*(x){hits=append(hits,"asyncgen");await sleep(0);let reply=yield x;yield reply;return_values(x+5,x+6)});
let a0=a(40);let g0=g(42);let ag0=ag(42);let cold=hits;
let result=[f(41),await a0,next(g0),send(g0,7),next(g0),await anext(ag0),await asend(ag0,8),await anext(ag0)];
[cold,result,multiple_value_list(g0),multiple_value_list(ag0),hits]'''
FOUR_EXPECTED=[[],[42,42,{'done':False,'value':42},{'done':False,'value':7},{'done':True,'value':45},{'done':False,'value':42},{'done':False,'value':8},{'done':True,'value':47}],[45,46],[47,48],['fn','async','gen','asyncgen']]


def entries(directory):
    return sorted(Path(directory).glob('*.entry'))


def run_four(cache,target):
    evaluator=Evaluator(native_code_cache=cache,step_limit=1000000);functions=[]
    def choose(function):
        chosen=function if target=='raw' else compile_function(evaluator,function,target)
        functions.append(chosen);return chosen
    evaluator.env.bind('choose',Native('choose236',choose,evaluator,captures=()))
    with observe_states() as states:value=serializable(evaluator.run(FOUR))
    return value,evaluator,functions,states


class NativeObjectsPortable236(unittest.TestCase):
    def test_public_exports_private_store_construction_and_info_are_backend_lazy(self):
        self.assertIs(chaos.NativeObjectCache,NativeObjectCache);self.assertIs(chaos.NativeCodeCache,NativeCodeCache)
        with tempfile.TemporaryDirectory(prefix='native236-') as directory,patch('chaos.native_jit._backend',side_effect=AssertionError('Constructor loaded LLVM')):
            store=NativeObjectCache(Path(directory)/'private');cache=NativeCodeCache(object_cache=store)
            info=store.info();self.assertEqual(info['schema'],'chaos.native-object-cache/1');self.assertEqual(info['entries'],0);self.assertEqual(info['objectBytes'],0)
            info['limits']['maxEntries']=999;self.assertNotEqual(store.info()['limits']['maxEntries'],999)
            before=set(cache.info());self.assertEqual(before,{'schema','entries','irBytes','hits','misses','builds','evictions','oversize','generation','limits'})
            store.clear();self.assertEqual(store.info()['entries'],0)
        for options in ({'max_entries':True},{'max_entries':-1},{'max_object_bytes':True},{'max_object_bytes':-1},{'max_total_bytes':True},{'max_total_bytes':-1}):
            with tempfile.TemporaryDirectory() as directory,self.subTest(options=options),self.assertRaises((ValueError,TypeError)):
                NativeObjectCache(Path(directory)/'cache',**options)

    def test_untrusted_filesystem_paths_and_keys_reject_before_any_backend(self):
        for mode in ('symlink','directory-mode','key-mode','key-hardlink'):
            with tempfile.TemporaryDirectory(prefix='native236-') as directory,self.subTest(mode=mode),patch('chaos.native_jit._backend',side_effect=AssertionError('Untrusted storage loaded LLVM')):
                root=Path(directory);path=root/'cache'
                if mode=='symlink':
                    target=root/'target';target.mkdir(mode=0o700);path.symlink_to(target,target_is_directory=True)
                elif mode=='directory-mode':path.mkdir(mode=0o755);path.chmod(0o755)
                else:
                    valid=NativeObjectCache(path);valid.close()
                    if mode=='key-mode':(path/'secret').chmod(0o644)
                    else:os.link(path/'secret',root/'alias')
                with self.assertRaises(OSError):NativeObjectCache(path)
                if mode=='symlink':self.assertTrue(path.is_symlink());self.assertEqual(list(target.iterdir()),[])

    def test_missing_backend_is_honest_portable_and_never_writes_native_objects(self):
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            store=NativeObjectCache(Path(directory)/'cache');cache=NativeCodeCache(object_cache=store)
            with patch('chaos.native_jit._backend',side_effect=BackendUnavailable('fixture236-unavailable')),patch('chaos.native_flow._backend',side_effect=BackendUnavailable('fixture236-unavailable')):
                e=Evaluator(native_code_cache=cache);value=e.run('let f=compile_function(fn(x){x+2},"auto");[f(40),compile_info(f)]')
                self.assertEqual(value[0],42);self.assertEqual(value[1]['nativeCalls'],0);self.assertEqual(value[1]['fallbackReason'],'fixture236-unavailable')
                result,e,functions,states=run_four(cache,'auto-flow');self.assertEqual(result,FOUR_EXPECTED)
                self.assertTrue(states);self.assertTrue(all(s.closed for s in states));self.assertTrue(all(flow_info(e,f)['nativeResumes']==0 and flow_info(e,f)['portableResumes']>0 for f in functions))
                with self.assertRaises(LanguageError) as caught:compile_function(e,e.run('fn(x){x+2}'),'native')
                self.assertEqual(caught.exception.code,'compiler_error')
            self.assertEqual(store.info()['entries'],0);self.assertEqual(store.info()['writes'],0);self.assertEqual(entries(Path(directory)/'cache'),[])

    def test_host_cache_parameter_is_exact_type_without_granting_image_authority(self):
        for value in (False,{},object()):
            with self.subTest(value=type(value).__name__),self.assertRaises((TypeError,ValueError)):Evaluator(native_code_cache=value)
            with self.assertRaises((TypeError,ValueError)):Framework(':memory:',settings=Settings(':memory:'),native_code_cache=value)
        self.assertEqual(Framework.evaluate('42',native_code_cache=None),42)
        from chaos.executable_image import compile_executable,load_executable
        image=compile_executable('42');image['nativeCodeCache']={'trusted':True}
        with self.assertRaises(LanguageError) as caught:load_executable(image)
        self.assertEqual(caught.exception.code,'executable_error')


@unittest.skipUnless(NATIVE,'requires actual optional llvmlite0.45 backend; portable tests are separate')
class NativeObjectsActual236(unittest.TestCase):
    def test_real_scalar_and_flow_objects_warm_link_without_object_codegen(self):
        from llvmlite import binding as llvm
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            store=NativeObjectCache(Path(directory)/'cache');cold=NativeCodeCache(object_cache=store)
            original=llvm.TargetMachine.emit_object
            with patch.object(llvm.TargetMachine,'emit_object',autospec=True,side_effect=original) as emit:
                scalar=compile_kernel(expression(),(),code_cache=cold);control=compile_flow(flow(),code_cache=cold)
            self.assertGreaterEqual(emit.call_count,2);self.assertEqual(scalar.invoke([40],lambda _:None),42);self.assertEqual(control.resume(0,[0]),(3,3))
            stored=entries(Path(directory)/'cache');self.assertGreaterEqual(len(stored),2)
            for entry in stored:
                self.assertTrue((entry/'object.o').is_file());self.assertGreater((entry/'object.o').stat().st_size,0);self.assertIsInstance(json.loads((entry/'metadata.json').read_text()),dict)
            fresh_store=NativeObjectCache(Path(directory)/'cache');warm=NativeCodeCache(object_cache=fresh_store)
            parse=llvm.parse_assembly
            def empty_only(text,*args,**kwargs):
                self.assertEqual(str(text).strip(),'','Warm object parsed generated IR')
                return parse(text,*args,**kwargs)
            with patch.object(llvm.TargetMachine,'emit_object',side_effect=AssertionError('Warm object generated code')),patch.object(llvm,'parse_assembly',side_effect=empty_only):
                second=compile_kernel(expression(),(),code_cache=warm);other=compile_flow(flow(),code_cache=warm)
                self.assertEqual(second.invoke([40],lambda _:None),42);self.assertEqual(other.resume(0,[0]),(3,3))
            self.assertGreaterEqual(fresh_store.info()['hits'],2);self.assertIsNot(scalar._engine,second._engine);self.assertIsNot(control._engine,other._engine)

    def test_tampered_bytes_and_metadata_never_reach_the_native_object_loader(self):
        from llvmlite import binding as llvm
        for mode in ('object','metadata','missing-metadata'):
            with tempfile.TemporaryDirectory(prefix='native236-') as directory,self.subTest(mode=mode):
                path=Path(directory)/'cache';store=NativeObjectCache(path);compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=store));entry=entries(path)[0]
                file=entry/('object.o' if mode=='object' else 'metadata.json');original_bytes=file.read_bytes()
                if mode=='missing-metadata':file.unlink();damaged=None
                else:
                    damaged=(b'not a native object\0'+original_bytes) if mode=='object' else original_bytes.replace(b'{',b'{"untrusted":true,',1)
                    file.write_bytes(damaged)
                loaded=[];original=llvm.ObjectFileRef.from_data
                def inspect(data):loaded.append(bytes(data));return original(data)
                retry_store=NativeObjectCache(path)
                with patch.object(llvm.ObjectFileRef,'from_data',side_effect=inspect):
                    kernel=compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=retry_store))
                    self.assertEqual(kernel.invoke([40],lambda _:None),42)
                if damaged is not None:self.assertNotIn(damaged,loaded)
                self.assertGreaterEqual(retry_store.info()['invalidations'],1)
                self.assertEqual(retry_store.info()['hits'],0)

    def test_authenticated_entry_copied_under_another_exact_IR_key_is_not_linked(self):
        from llvmlite import binding as llvm
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            path=Path(directory)/'cache';store=NativeObjectCache(path)
            plus=compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=store));first=entries(path)[0]
            before=set(entries(path));minus=compile_kernel(expression('-'),(),code_cache=NativeCodeCache(object_cache=store));second=(set(entries(path))-before).pop()
            self.assertEqual(plus.invoke([40],lambda _:None),42);self.assertEqual(minus.invoke([40],lambda _:None),38)
            foreign_bytes=(first/'object.o').read_bytes();shutil.rmtree(second);shutil.copytree(first,second);loaded=[];original=llvm.ObjectFileRef.from_data
            def inspect(data):loaded.append(bytes(data));return original(data)
            fresh=NativeObjectCache(path)
            with patch.object(llvm.ObjectFileRef,'from_data',side_effect=inspect):
                kernel=compile_kernel(expression('-'),(),code_cache=NativeCodeCache(object_cache=fresh));self.assertEqual(kernel.invoke([40],lambda _:None),38)
            self.assertNotIn(foreign_bytes,loaded);self.assertEqual(fresh.info()['hits'],0);self.assertGreaterEqual(fresh.info()['invalidations'],1)

    def test_codegen_and_host_metadata_changes_miss_then_original_identity_relinks(self):
        from llvmlite import binding as llvm
        from chaos import native_code_cache as implementation
        for mode in ('codegen','host-release'):
            with tempfile.TemporaryDirectory(prefix='native236-') as directory,self.subTest(mode=mode):
                path=Path(directory)/'cache';original=llvm.TargetMachine.emit_object
                store=NativeObjectCache(path)
                with patch.object(llvm.TargetMachine,'emit_object',autospec=True,side_effect=original) as emit:
                    first=compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=store));self.assertEqual(emit.call_count,1)
                    changed=patch.object(implementation,'CODEGEN_ABI',implementation.CODEGEN_ABI+'-metadata-probe') if mode=='codegen' else patch.object(implementation.platform,'release',return_value=implementation.platform.release()+'-metadata-probe')
                    # Only key metadata is varied; both real modules still target this host.
                    with changed:second=compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=NativeObjectCache(path)))
                    self.assertEqual(emit.call_count,2)
                    restored_store=NativeObjectCache(path)
                    third=compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=restored_store));self.assertEqual(emit.call_count,2)
                self.assertEqual([k.invoke([40],lambda _:None) for k in (first,second,third)],[42,42,42]);self.assertEqual(restored_store.info()['hits'],1)

    def test_eviction_memory_clear_and_disk_clear_preserve_live_engine_lifetime(self):
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            store=NativeObjectCache(Path(directory)/'cache',max_entries=1);cache=NativeCodeCache(max_entries=1,object_cache=store)
            first=compile_kernel(expression(),(),code_cache=cache);second=compile_kernel(expression('-'),(),code_cache=cache)
            self.assertEqual(store.info()['entries'],1);self.assertGreaterEqual(store.info()['evictions'],1)
            self.assertEqual(first.invoke([40],lambda _:None),42);cache.clear();self.assertEqual(cache.info()['entries'],0);self.assertEqual(store.info()['entries'],1)
            store.clear();self.assertEqual(store.info()['entries'],0);self.assertEqual(second.invoke([40],lambda _:None),38);self.assertEqual(first.invoke([40],lambda _:None),42)
            compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=store));self.assertEqual(store.info()['entries'],1)

    def test_reopened_smaller_disk_limits_trim_existing_entries_without_invalidating_live_code(self):
        from llvmlite import binding as llvm
        for limits in ({'max_entries':1},{'max_object_bytes':1},{'max_total_bytes':0}):
            with tempfile.TemporaryDirectory(prefix='native236-') as directory,self.subTest(limits=limits):
                path=Path(directory)/'cache';original_store=NativeObjectCache(path)
                first=compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=original_store))
                second=compile_kernel(expression('-'),(),code_cache=NativeCodeCache(object_cache=original_store))
                self.assertEqual(original_store.info()['entries'],2)
                smaller=NativeObjectCache(path,**limits)
                capacity=1 if 'max_entries' in limits else 0
                self.assertLessEqual(smaller.info()['entries'],capacity)
                original=llvm.TargetMachine.emit_object
                with patch.object(llvm.TargetMachine,'emit_object',autospec=True,side_effect=original) as emit:
                    current=compile_kernel(expression(),(),code_cache=NativeCodeCache(object_cache=smaller))
                self.assertEqual(current.invoke([40],lambda _:None),42)
                self.assertLessEqual(smaller.info()['entries'],capacity)
                if capacity==0:
                    self.assertEqual(emit.call_count,1);self.assertEqual(smaller.info()['hits'],0);self.assertEqual(smaller.info()['writes'],0)
                self.assertEqual(first.invoke([40],lambda _:None),42);self.assertEqual(second.invoke([40],lambda _:None),38)

    def test_scalar_callback_abort_identity_and_checkpoint_tape_survive_disk_relink(self):
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            path=Path(directory)/'cache';compile_kernel(expression(),(8,),code_cache=NativeCodeCache(object_cache=NativeObjectCache(path)))
            kernel=compile_kernel(expression(),(8,),code_cache=NativeCodeCache(object_cache=NativeObjectCache(path)))
            for at in range(5):
                seen=[];fault=ResourceLimit('controlled native236 abort')
                def checkpoint(event):
                    seen.append(event)
                    if len(seen)==at+1:raise fault
                with self.subTest(at=at),self.assertRaises(ResourceLimit) as caught:kernel.invoke([40],checkpoint)
                self.assertIs(caught.exception,fault);self.assertEqual(seen,[8,0,1,2,3][:at+1])
            seen=[];self.assertEqual(kernel.invoke([40],seen.append),42);self.assertEqual(seen,[8,0,1,2,3])

    def test_warm_four_flavors_have_real_native_resumes_cold_handles_and_closed_roots(self):
        from llvmlite import binding as llvm
        original,raw,_,_=run_four(None,'raw');self.assertEqual(original,FOUR_EXPECTED)
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            path=Path(directory)/'cache';cold,first,_,_=run_four(NativeCodeCache(object_cache=NativeObjectCache(path)),'native-flow')
            store=NativeObjectCache(path)
            with patch.object(llvm.TargetMachine,'emit_object',side_effect=AssertionError('Warm four-flavor codegen')):
                warm,e,functions,states=run_four(NativeCodeCache(object_cache=store),'native-flow')
            self.assertEqual(cold,original);self.assertEqual(warm,original);self.assertEqual(e.steps,first.steps)
            self.assertEqual({s.flavor for s in states},{'fn','async_fn','gen_fn','async_gen_fn'})
            self.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers and s.pending_request is None and s.function is None and s.scope is None for s in states))
            self.assertTrue(all(flow_info(e,f)['nativeResumes']>0 and flow_info(e,f)['portableResumes']==0 for f in functions));self.assertGreaterEqual(store.info()['hits'],4)
            self.assertFalse(e.suspension.active);self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)

    def test_explicit_None_disables_memory_and_disk_without_changing_native_semantics(self):
        from llvmlite import binding as llvm
        original=llvm.TargetMachine.emit_object
        with patch.object(llvm.TargetMachine,'emit_object',autospec=True,side_effect=original) as emit:
            for _ in range(2):self.assertEqual(Framework.evaluate('let f=compile_function(fn(x:Int)->Int{x+2},"native");f(40)',native_code_cache=None),42)
        self.assertEqual(emit.call_count,0,'Disabled object caching keeps original direct MCJIT path, not a hidden disk cache')

    def test_fresh_process_reuses_real_objects_without_codegen_or_network(self):
        bootstrap=r'''import json,socket,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'));path=Path(sys.argv[2]);warm=sys.argv[3]=='warm'
def denied(*a,**k):raise AssertionError('Native object fixture has no network')
socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
from chaos import NativeCodeCache,NativeObjectCache
from chaos.native_jit import compile_kernel
from chaos.native_flow import compile_flow
from test_native_code_cache import expression,flow
from llvmlite import binding as llvm
if warm:
 llvm.TargetMachine.emit_object=denied
 original_parse=llvm.parse_assembly
 def empty_only(text,*a,**k):
  if str(text).strip():raise AssertionError('Warm process parsed generated IR')
  return original_parse(text,*a,**k)
 llvm.parse_assembly=empty_only
store=NativeObjectCache(path);cache=NativeCodeCache(object_cache=store)
k=compile_kernel(expression(),(),code_cache=cache);f=compile_flow(flow(),code_cache=cache)
seen=[];out={'scalar':k.invoke([40],seen.append),'events':seen,'flow':f.resume(0,[0]),'cache':store.info()}
print(json.dumps(out))'''
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            work=Path(directory);env=dict(os.environ);env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',CHAOS_CONFIG=str(work/'settings.json'))
            def run(mode):
                process=subprocess.run([sys.executable,'-I','-B','-c',bootstrap,str(ROOT),str(work/'cache'),mode],cwd=work,env=env,text=True,capture_output=True,timeout=30)
                self.assertEqual(process.returncode,0,(process.stdout,process.stderr));return json.loads(process.stdout)
            with ThreadPoolExecutor(max_workers=2) as pool:cold=list(pool.map(run,('cold','cold')))
            warm=run('warm')
            for result in cold+[warm]:self.assertEqual([result['scalar'],result['events'],result['flow']],[42,[0,1,2,3],[3,3]])
            self.assertEqual(sum(result['cache']['writes'] for result in cold),2)
            self.assertGreaterEqual(sum(result['cache']['hits'] for result in cold),2);self.assertGreaterEqual(warm['cache']['hits'],2)

    def test_Framework_host_injection_keeps_Agent_tool_and_software_Robot_effects_once(self):
        import test_robot_values as robots
        from chaos.robots import RobotSpec
        calls=[];steps=[];robot=[];registry=Registry()
        @registry.tool('fixture.native236.echo',effect='write')
        def echo(value,ctx):ctx.checkpoint();calls.append(value);return value
        @registry.provider('fixture.native236')
        def provider(request,ctx):
            ctx.checkpoint();steps.append(request.step)
            return ToolCall('fixture.native236.echo',request.input) if request.step==1 else Final(request.history[-1]['output'])
        def factory(ctx):ctx.checkpoint();robot.append(('open',{}));return robots.Adapter(robot)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:native236'))
        source='''let add=compile_function(fn(x:Int)->Int{x+2},"native");agent worker{provider:"fixture.native236",tools:["fixture.native236.echo"]};let answer=worker(add(input));let status=async with await robot_open("rig") as r{let observation=await robot_observe(r);let receipt=await r(robot_command(observation,{pan:0,tilt:1}));receipt.status};[answer,status]'''
        with tempfile.TemporaryDirectory(prefix='native236-') as directory:
            path=Path(directory)/'cache'
            for index in range(2):
                cache=NativeCodeCache(object_cache=NativeObjectCache(path))
                with Framework(':memory:',registry=registry,settings=Settings(':memory:'),native_code_cache=cache) as app:
                    p=app.create_project('native-host',source=source,capabilities=['fixture.native236.echo','robot.control'])
                    run=app.start(p['id'],p['version'],40,mode='source');self.assertEqual(run.result(15),[42,'sent']);self.assertFalse(app._engine.active)
                self.assertGreater(cache.info()['entries'],0)
            self.assertEqual(calls,[42,42]);self.assertEqual(steps,[1,2,1,2]);self.assertEqual([name for name,_ in robot],['open','observe','act','close']*2)
