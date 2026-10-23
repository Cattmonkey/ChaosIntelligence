"""Operator methods are ordinary effectful language calls, including Agent work."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry, ToolCall
from chaos.ide_language import format_source
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.settings import Settings


class OperatorIntegration(unittest.TestCase):
    def test_agent_arithmetic_comparison_length_and_contains_in_current_task(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();data=request.input;seen.append(data['op'])
            return Final({'add':42,'eq':True,'len':2,'contains':True,'bool':True}[data['op']])
        registry.register_provider(ProviderSpec('calculate',provider))
        source='''agent worker{provider:"calculate",tools:[],maxSteps:1};
class Component{
 fn __add__(self,x){worker({op:"add",operand:x})}
 fn __eq__(self,x)->Bool{worker({op:"eq",operand:x})}
 fn __len__(self)->Int{worker({op:"len"})}
 fn __contains__(self,x)->Bool{worker({op:"contains",operand:x})}
 fn __bool__(self)->Bool{worker({op:"bool"})}
}
async fn work(){let c=Component();[c+2,[c]==[42],length(c),contains(c,42),bool(c)]}
await work()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('operator_effects',source=source,capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            self.assertEqual(run.result(timeout=8),[42,True,2,True,True])
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),5)
        self.assertEqual(seen,['add','eq','len','contains','bool'])

    def test_descriptor_bound_protocol_uses_same_agent_call_path(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input+40)
        registry.register_provider(ProviderSpec('add',provider))
        source='''agent worker{provider:"add",tools:[],maxSteps:1};
class Operation{fn __get__(self,object,owner){fn(other){worker(other)}}}
class Amount{let __add__=Operation()}
async fn work(){Amount()+2};await work()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('operator_binding',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),42)
        self.assertEqual(seen,[2])

    def test_concurrent_arithmetic_effects_really_yield_to_other_tasks(self):
        registry=Registry();gate=threading.Barrier(2);seen=[];lock=threading.Lock()
        def provider(request,context):
            context.checkpoint()
            with lock:seen.append(request.input)
            gate.wait(timeout=3);context.checkpoint();return Final(request.input*2)
        registry.register_provider(ProviderSpec('parallel',provider))
        source='''agent worker{provider:"parallel",tools:[],maxSteps:1};
class Amount{fn __add__(self,x){worker(x)}}
async fn work(x){Amount()+x};await gather([work(20),work(21)])'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('parallel_operators',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[40,42])
        self.assertEqual(sorted(seen),[20,21])

    def test_explicit_async_and_generator_arithmetic_handles(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input*2)
        registry.register_provider(ProviderSpec('double',provider))
        source='''agent worker{provider:"double",tools:[],maxSteps:1};
class AsyncAmount{async fn __add__(self,x){worker(x)}}
class StreamAmount{async fn* __add__(self,x){yield worker(x);yield worker(x+1)}}
let task=AsyncAmount()+20;let stream=StreamAmount()+20;
[type_of(task),type_of(stream),await task,await acollect(stream)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('operator_handles',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),['Task','AsyncGenerator',40,[40,42]])
        self.assertEqual(seen,[20,20,21])

    def test_operator_effects_cancel_and_drain_cooperative_worker(self):
        bodies={'add':('fn __add__(self,x){worker(x)}','c+1'),
                'eq':('fn __eq__(self,x){worker(x)}','[c]==[1]'),
                'len':('fn __len__(self){worker(1)}','length(c)'),
                'contains':('fn __contains__(self,x){worker(x)}','contains(c,1)')}
        for label,(body,action) in bodies.items():
            with self.subTest(protocol=label):
                entered=threading.Event();exited=threading.Event();registry=Registry()
                def provider(request,context):
                    entered.set()
                    try:
                        for _ in range(500):context.checkpoint();threading.Event().wait(.01)
                        raise AssertionError('Operator did not observe cancellation')
                    finally:exited.set()
                registry.register_provider(ProviderSpec('cooperative',provider))
                source='agent worker{provider:"cooperative",tools:[],maxSteps:1};class C{'+body+'};async fn work(){let c=C();'+action+'};await work()'
                with Framework(registry=registry,settings=Settings(':memory:')) as app:
                    p=app.create_project('cancel_'+label,source=source,capabilities=[]);run=app.start(p['id'],p['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(3));run.cancel()
                        self.assertEqual(run.wait(timeout=5)['status'],'cancelled');self.assertTrue(exited.is_set())
                    finally:run.cancel()

    def test_operator_does_not_grant_agent_tool_permissions(self):
        registry=Registry();effects=[]
        @registry.tool('secret',input_type='Any',output_type='Int')
        def secret(value,context):effects.append(value);return 42
        registry.register_provider(ProviderSpec('asking',lambda request,context:ToolCall('secret',request.input)))
        source='agent worker{provider:"asking",tools:[],maxSteps:2};class C{fn __add__(self,x){worker(x)}};try{C()+1}catch error{"denied"}'
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('operator_denied',source=source,capabilities=['secret'])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),'denied')
        self.assertEqual(effects,[])

    def test_agent_notimplemented_transport_stays_plain_data(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input)
        registry.register_provider(ProviderSpec('echo',provider))
        source='''agent worker{provider:"echo",tools:[],maxSteps:1};
class C{fn __add__(self,x){worker(NotImplemented)}}
async fn work(){let result=C()+1;[type_of(result),result==NotImplemented,result]};await work()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('inert_sentinel',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),['Record',False,{'$notImplemented':True}])
        self.assertEqual(seen,[{'$notImplemented':True}])

    def test_http_structural_operator_edit_preserves_run_snapshot(self):
        main='import "amount.chaos" as A;A.Amount()+2';library='class Amount{fn __add__(self,x:Int)->Int{40+x}};export{Amount}'
        with tempfile.TemporaryDirectory() as temp,Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('operator_edit',source=main,files={'amount.chaos':library},capabilities=[])
                old=client.start(p['id'],p['version'],None,mode='source');self.assertEqual(old.result(),42)
                program=client.program(p['id'],file='amount.chaos')['program']
                def find(node):
                    if isinstance(node,dict):
                        if node.get('kind')=='literal' and node.get('value')==40:return node
                        for value in node.values():
                            found=find(value)
                            if found:return found
                    elif isinstance(node,list):
                        for value in node:
                            found=find(value)
                            if found:return found
                p=client.edit_program(p['id'],p['version'],file='amount.chaos',operations=[{'op':'set','id':find(program)['id'],'field':'value','value':41}])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['files']['amount.chaos'],library);self.assertEqual(old.get()['output'],42)
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_cli_macro_generated_operator_roundtrip(self):
        source='import "forms.chaos" as F;let C=F.component!();C()+2 // retain this\n'
        files={'forms.chaos':'macro component(){quote{class C{fn __add__(self,x:Int)->Int{40+x}};C}};export{component}'}
        formatted=format_source(source);self.assertIn('// retain this',formatted)
        for text in (source,formatted,format_program(parse_program(source))):self.assertEqual(Framework.evaluate(text,files=files),42)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'main.chaos';path.write_text(source)
            for name,text in files.items():(Path(temp)/name).write_text(text)
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parent.parent))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=temp,env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)


if __name__=='__main__':unittest.main()
