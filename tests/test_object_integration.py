"""Object components share language, agent, and persisted project semantics."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry
from chaos.ide_language import format_source
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.settings import Settings


PIPELINE = '''class Pipeline {
    var worker:Function=fn(x){x};
    var total:Int=0;
    fn __init__(self,worker:Function){self.worker=worker;null}
    async fn __call__(self,value:Int)->Int {
        let answer=self.worker(value);self.total=self.total+answer;answer
    }
    async fn* stream(self,values){for x in values{yield await self(x)}}
};export{Pipeline}'''


class ObjectIntegration(unittest.TestCase):
    def test_callable_component_runs_concurrent_agents_and_streams(self):
        registry=Registry();barrier=threading.Barrier(2);seen=[]
        def provider(request,context):
            context.checkpoint()
            if request.input in (20,21):barrier.wait(timeout=3)
            seen.append(request.input);return Final(request.input*2)
        registry.register_provider(ProviderSpec('double',provider))
        source='''import "pipeline.chaos" as P;
agent worker{provider:"double",tools:[],maxSteps:1};
let pipeline=P.Pipeline(worker);
let work=pipeline.__call__;
let result=await gather([apply(work,[20]),partial(pipeline,21)()]);
let stream=await acollect(pipeline.stream([1,2]));
[result,stream,pipeline.total,isinstance(pipeline,P.Pipeline)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('components',source=source,files={'pipeline.chaos':PIPELINE},capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            self.assertEqual(run.result(timeout=10),[[40,42],[2,4],88,True])
            self.assertCountEqual(seen,[20,21,1,2])
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),4)
            self.assertTrue(any(e['type']=='program.task.completed' for e in events))

    def test_diamond_dispatch_wraps_actual_agent_in_c3_order(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input);return Final(request.input)
        registry.register_provider(ProviderSpec('identity',provider))
        source='''class Base {
 var worker:Function=fn(x){x};
 fn __init__(self,worker){self.worker=worker;null}
 fn __call__(self,x){self.worker(x)}
}
class Add(Base){fn __call__(self,x){super(Add,self).__call__(x+1)}}
class Multiply(Base){fn __call__(self,x){super(Multiply,self).__call__(x*2)}}
class Combined(Add,Multiply){}
agent worker{provider:"identity",tools:[],maxSteps:1};
let c=Combined(worker);[compose(fn(x){x},c)(20),issubclass(Combined,Base)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('c3_agents',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='graph').result(),[42,True])
        self.assertEqual(seen,[42])

    def test_state_projection_sends_data_without_live_instance_aliases(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();self.assertIsInstance(request.input,dict)
            self.assertEqual(json.loads(json.dumps(request.input)),{'n':41})
            seen.append(request.input['n']);request.input['n']=99
            return Final(42)
        registry.register_provider(ProviderSpec('state_reader',provider))
        source='''class Box{var n:Int=0};let b=Box();b.n=41;
agent inspect{provider:"state_reader",tools:[],maxSteps:1};
let first=inspect(vars(b));async fn read(){inspect(vars(b))};[first,await read(),b.n]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('state_projection',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),[42,42,41])
        self.assertEqual(seen,[41,41])

    def test_cancelling_component_waits_for_agent_effect_exit(self):
        registry=Registry();entered=threading.Event();exited=threading.Event()
        def provider(request,context):
            entered.set()
            try:
                for _ in range(500):
                    context.checkpoint();threading.Event().wait(.01)
                raise AssertionError('Cancellation did not reach agent')
            finally:exited.set()
        registry.register_provider(ProviderSpec('cooperative',provider))
        source='''import "pipeline.chaos" as P;
agent worker{provider:"cooperative",tools:[],maxSteps:1};await P.Pipeline(worker)(1)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('component_cancel',source=source,files={'pipeline.chaos':PIPELINE},capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            try:
                self.assertTrue(entered.wait(3));run.cancel()
                self.assertEqual(run.wait(timeout=5)['status'],'cancelled')
                self.assertTrue(exited.is_set())
            finally:run.cancel()

    def test_http_method_ir_edit_keeps_previous_run_snapshot(self):
        main='import "components.chaos" as C;C.Answer()()'
        library='class Answer{fn __call__(self){42}};export{Answer}'
        with tempfile.TemporaryDirectory() as temp,Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                p=client.create_project('method_edit',source=main,files={'components.chaos':library},capabilities=[])
                old=client.start(p['id'],p['version'],None,mode='source');self.assertEqual(old.result(),42)
                ir=client.program(p['id'],file='components.chaos')['program'];pending=[ir];literal=None
                while pending:
                    item=pending.pop()
                    if isinstance(item,dict):
                        if item.get('kind')=='literal' and item.get('value')==42:literal=item
                        pending.extend(item.values())
                    elif isinstance(item,list):pending.extend(item)
                self.assertIsNotNone(literal)
                p=client.edit_program(p['id'],p['version'],file='components.chaos',operations=[{'op':'set','id':literal['id'],'field':'value','value':43}])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['output'],42)
                self.assertEqual(old.get()['snapshot']['files']['components.chaos'],library)
                self.assertEqual(client.snapshot(p['id'])['project']['version'],p['version'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_cli_and_sdk_class_modules_match(self):
        source='import "types.chaos" as T;fn invoke(x:T.Adder){x(2)};invoke(T.Adder(40))'
        files={'types.chaos':'class Adder{var n:Int=0;fn __init__(self,n){self.n=n;null};fn __call__(self,x){self.n+x}};export{Adder}'}
        self.assertEqual(Framework.evaluate(source,files=files),42)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'main.chaos';path.write_text(source)
            for name,text in files.items():(Path(temp)/name).write_text(text)
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parent.parent))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=temp,env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)

    def test_class_macro_preserves_labels_and_source_roundtrip(self):
        source='''// component template
macro make(value){quote{class Component{let offset=unquote(value);fn __call__(self,x){self.offset+x}};Component}}
let self=99;let offset=40;let c=make!(offset)();c(2) // retained
'''
        formatted=format_source(source);self.assertIn('// retained',formatted)
        canonical=format_program(json.loads(json.dumps(parse_program(source))))
        for text in (source,formatted,canonical):self.assertEqual(Framework.evaluate(text),42)


if __name__=='__main__':unittest.main()
