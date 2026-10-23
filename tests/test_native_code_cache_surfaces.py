"""Warm/disabled semantic equivalence and real language/API/Agent/CLI entries."""
from contextlib import redirect_stdout
import io
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings, ToolCall
from chaos.jit import compile_function, compile_info, compile_optimization_info
from chaos.jit_flow import info as flow_info
from chaos.language import Evaluator, LanguageError, Parser
from chaos.modules import ProgramBundle
from chaos.native_code_cache import NativeCodeCache
from chaos.server import make_server
from test_native_jit import NATIVE
from test_native_code_cache import cache_scope

ROOT=Path(__file__).resolve().parents[1]
SCALAR='fn(x){x*2+2}'


def compile_observed(cache,*,limit=100000,cancel=None):
    e=Evaluator();plain=e.run(SCALAR);e.steps=0;e.step_limit=limit
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    with cache_scope(cache):
        try:
            fn=compile_function(e,plain,'native');result=('compiled',compile_info(e,fn)['status'])
        except LanguageError as error:result=('error',error.record())
    return result,e.steps


@unittest.skipUnless(NATIVE,'requires optional actual llvmlite 0.45 backend')
class NativeCodeCacheSurfaces(unittest.TestCase):
    def test_compile_budget_cancel_runtime_trace_and_quota_keep_original_boundaries(self):
        warm=NativeCodeCache();compile_observed(warm)
        cold=NativeCodeCache();total=compile_observed(None)[1]
        for threshold in range(total+2):
            cold.clear()
            self.assertEqual(compile_observed(cold,limit=threshold),compile_observed(warm,limit=threshold))
            self.assertEqual(compile_observed(warm,limit=threshold),compile_observed(None,limit=threshold))
            cold.clear()
            self.assertEqual(compile_observed(cold,cancel=threshold),compile_observed(warm,cancel=threshold))
            self.assertEqual(compile_observed(warm,cancel=threshold),compile_observed(None,cancel=threshold))
        prepared=[]
        for cache in (NativeCodeCache(),warm,None):
            e=Evaluator();plain=e.run(SCALAR)
            with cache_scope(cache):fast=compile_function(e,plain,'native')
            prepared.append((e,fast))
        def invoke(e,fn,limit=100000,cancel=None,instrument=False):
            e.steps=0;e.step_limit=limit;e.cancelled=lambda:cancel is not None and e.steps>=cancel
            trace=[];debug=[];e.trace_handler=trace.append if instrument else None
            e.debug_hook=(lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))) if instrument else None
            try:result=('value',e.invoke(fn,[20]))
            except LanguageError as error:result=('error',error.record())
            return result,e.steps,trace,debug
        for instrument in (False,True):
            total=invoke(*prepared[-1],instrument=instrument)[1]
            for threshold in range(total+2):
                values=[invoke(e,f,limit=threshold,instrument=instrument) for e,f in prepared]
                self.assertEqual(values[0],values[1]);self.assertEqual(values[1],values[2])
                values=[invoke(e,f,cancel=threshold,instrument=instrument) for e,f in prepared]
                self.assertEqual(values[0],values[1]);self.assertEqual(values[1],values[2])
        for e,f in prepared:self.assertGreater(compile_info(e,f)['nativeCalls'],0)
        e=Evaluator();plain=e.run(SCALAR);e.native_kernel_count=32
        with cache_scope(warm):
            fallback=compile_function(e,plain,'auto')
            self.assertEqual(compile_info(e,fallback)['fallbackReason'],'kernel_limit')
            with self.assertRaises(LanguageError):compile_function(e,plain,'native')
        self.assertEqual(e.native_kernel_count,32)

    def test_agents_and_cross_owner_functions_share_only_physical_code(self):
        from llvmlite import binding as llvm
        original=llvm.create_mcjit_compiler;cache=NativeCodeCache();registry=Registry();outputs=[]
        @registry.provider('fixture.cache')
        def provider(request,context):
            context.checkpoint()
            if request.step==1:return ToolCall('work',request.input)
            value=request.history[-1]['output'];outputs.append(value);return Final(value)
        source='let work=compile_function(fn(value:Int)->Int{value+2},target="native");agent worker{provider:"fixture.cache",tools:[]};await agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int")])(input)'
        with cache_scope(cache),patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds,Framework(registry=registry,settings=Settings(':memory:')) as app:
            for index,value in enumerate((40,41)):
                project=app.create_project('cache-agent-'+str(index),source=source,capabilities=[])
                self.assertEqual(app.start(project['id'],project['version'],value,trace=False).result(8),value+2)
            self.assertEqual(builds.call_count,1)
        self.assertEqual(outputs,[42,43])
        first=Evaluator();second=Evaluator()
        with cache_scope(cache):
            a=compile_function(first,first.run('fn(x){x+2}'),'native')
            b=compile_function(second,second.run('fn(x){x+2}'),'native')
        reference_owner=Evaluator();reference_caller=Evaluator()
        with cache_scope(None):
            reference=compile_function(reference_owner,reference_owner.run('fn(x){x+2}'),'native')
        def rejected_invoke(caller,function):
            caller.steps=0
            with self.assertRaises(LanguageError) as error:caller.invoke(function,[40])
            return error.exception.record(),caller.steps
        # invoke rejects foreign callables with its existing runtime_error;
        # compile_function has a separate ownership_error contract. Compare
        # the complete original invoke error and logical cost with reuse off.
        rejection=rejected_invoke(second,a)
        self.assertEqual(rejection,rejected_invoke(reference_caller,reference))
        self.assertEqual(rejection[0]['code'],'runtime_error')
        self.assertEqual(first.invoke(a,[40]),42);self.assertEqual(second.invoke(b,[41]),43)
        self.assertEqual(compile_info(first,a)['nativeCalls'],1)
        self.assertEqual(compile_info(second,b)['nativeCalls'],1)

    def test_flow_machine_reuse_never_reuses_host_callbacks_or_invocation_state(self):
        from llvmlite import binding as llvm
        original=llvm.create_mcjit_compiler;cache=NativeCodeCache();prepared=[]
        with cache_scope(cache),patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds:
            for offset in (1,2):
                e=Evaluator();callback=e.run('fn(value){value+'+str(offset)+'}')
                fast=compile_function(e,e.run('fn(callback,value){callback(value)}'),'native-flow')
                prepared.append((e,fast,callback))
            self.assertEqual(builds.call_count,1)
        for (e,fast,callback),expected in zip(prepared,(41,42)):
            self.assertEqual(e.invoke(fast,[callback,40]),expected)
            self.assertGreater(flow_info(e,fast)['nativeResumes'],0)
        self.assertIsNot(prepared[0][1].compiled_plan.flow,prepared[1][1].compiled_plan.flow)

    def test_actual_http_sdk_and_cli_run_existing_language_with_real_build_reuse(self):
        from llvmlite import binding as llvm
        source='let f=compile_function(fn(x){x*2},target="native");let g=compile_function(fn(x){x*2},target="native");[f(20),g(21)]'
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler
        with tempfile.TemporaryDirectory() as directory,cache_scope(cache),patch.object(llvm,'create_mcjit_compiler',wraps=original) as builds,Framework(settings=Settings(':memory:')) as app:
            root=Path(directory);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('cache-http',source=source,capabilities=[])
                for mode in ('source','graph'):
                    run=client.start(project['id'],project['version'],None,mode=mode,trace=False)
                    self.assertEqual(run.result(8),[40,42])
                    self.assertEqual(run.get()['snapshot']['source'],source)
                self.assertEqual(Framework.evaluate(source),[40,42])
                self.assertEqual(builds.call_count,1)
            finally:server.shutdown();thread.join(3);server.server_close()
            path=root/'main.chaos';path.write_text(source)
            script='''import contextlib,io,json,sys
from unittest.mock import patch
from llvmlite import binding as llvm
from chaos.cli import main
sys.argv=["chaos","run",sys.argv[1]]
buffer=io.StringIO()
with patch.object(llvm,"create_mcjit_compiler",wraps=llvm.create_mcjit_compiler) as builds,contextlib.redirect_stdout(buffer):
    code=main()
print(json.dumps({"code":code,"result":json.loads(buffer.getvalue()),"engines":builds.call_count}))
'''
            result=subprocess.run([sys.executable,'-B','-c',script,str(path)],cwd=directory,
                env=dict(os.environ,PYTHONPATH=str(ROOT),CHAOS_CONFIG=str(root/'isolated-settings.json')),capture_output=True,text=True,timeout=25)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'code':0,'result':[40,42],'engines':1})

    def test_warm_machine_code_uses_each_current_file_line_parameter_and_error_span(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler;prepared=[]
        sources=[('first.chaos','fn(x){x*2+2}','x'),
                 ('nested/second.chaos','\n\nfn different(value){value*2+2};different','value')]
        with cache_scope(cache),patch.object(llvm,'create_mcjit_compiler',wraps=original) as engines:
            for filename,source,name in sources:
                owner=Evaluator();plain=owner.run(ProgramBundle(source,entry=filename))
                fast=compile_function(owner,plain,'native')
                self.assertEqual(compile_info(owner,fast)['parameterGuards'][0]['name'],name)
                prepared.append((filename,source,owner,fast))
            self.assertEqual(engines.call_count,1)
        def record(owner,function,*,limit=100000,cancel=None):
            owner.steps=0;owner.step_limit=limit
            owner.cancelled=lambda:cancel is not None and owner.steps>=cancel
            try:result=('value',owner.invoke(function,[20]))
            except LanguageError as error:result=('error',error.record())
            return result,owner.steps
        for filename,source,owner,fast in prepared:
            reference=Evaluator();plain=reference.run(ProgramBundle(source,entry=filename))
            with cache_scope(None):disabled=compile_function(reference,plain,'native')
            total=record(reference,disabled)[1];files=set()
            for threshold in range(total+2):
                for kind in ('limit','cancel'):
                    args={kind:threshold}
                    actual=record(owner,fast,**args);self.assertEqual(actual,record(reference,disabled,**args))
                    if actual[0][0]=='error' and actual[0][1]['location']['file']:
                        files.add(actual[0][1]['location']['file'])
            self.assertEqual(files,{filename})
            self.assertGreater(compile_info(owner,fast)['nativeCalls'],0)
            self.assertGreater(compile_info(reference,disabled)['nativeCalls'],0)

    def test_warm_guards_bigints_bool_ast_mutation_and_wrapper_optimization_stay_local(self):
        from llvmlite import binding as llvm
        cache=NativeCodeCache();original=llvm.create_mcjit_compiler;compiled=[]
        with cache_scope(cache),patch.object(llvm,'create_mcjit_compiler',wraps=original) as engines:
            for source in ('fn(x){x+0}','fn(x){x*1}'):
                owner=Evaluator();fast=compile_function(owner,owner.run(source),'native')
                compiled.append((source,owner,fast))
            self.assertEqual(engines.call_count,1)
        first=compile_optimization_info(compiled[0][1],compiled[0][2])['kernels'][0]
        second=compile_optimization_info(compiled[1][1],compiled[1][2])['kernels'][0]
        self.assertNotEqual(first['valueGraphHash'],second['valueGraphHash'])
        self.assertIsNot(compiled[0][2].compiled_plan.kernel,compiled[1][2].compiled_plan.kernel)
        self.assertIs(compiled[0][2].compiled_plan.kernel._engine,compiled[1][2].compiled_plan.kernel._engine)
        def record(owner,function,value):
            owner.steps=0
            try:return ('value',owner.invoke(function,[value])),owner.steps
            except LanguageError as error:return ('error',error.record()),owner.steps
        for source,owner,fast in compiled:
            reference=Evaluator()
            with cache_scope(None):disabled=compile_function(reference,reference.run(source),'native')
            guard=compile_info(owner,fast)['parameterGuards'][0]
            for value in (guard['minimum'],guard['maximum'],guard['maximum']+1,2**100,True):
                self.assertEqual(record(owner,fast,value),record(reference,disabled,value))
            self.assertGreater(compile_info(owner,fast)['interpreterCalls'],0)
            replacement=Parser('x+5','mutated.chaos').program()[1][0]
            fast.body[1][-1]=copy.deepcopy(replacement)
            disabled.body[1][-1]=copy.deepcopy(replacement)
            self.assertEqual(record(owner,fast,40),record(reference,disabled,40))
            self.assertEqual(record(owner,fast,40)[0],('value',45))
            self.assertEqual(compile_info(owner,fast)['lastFallbackReason'],'definition_changed')
