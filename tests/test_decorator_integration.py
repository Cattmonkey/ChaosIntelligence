"""Decorators and descriptors compose with actual project effects and snapshots."""
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


WRAPPERS = '''fn cached(f) {
  var cache={};
  fn(x) { let key=str(x); if has(cache,key){cache[key]} else {
    let value=f(x);cache=set(cache,key,value);value
  } }
}
fn retry(f) {
  async fn(x) {
    var attempts=0;
    while attempts<3 {
      attempts=attempts+1;
      try{return f(x)}catch error{if attempts==3{throw error}else{null}}
    }
  }
}
export{cached,retry}'''


class DecoratorIntegration(unittest.TestCase):
    def test_decorated_agent_retries_caches_and_runs_concurrently(self):
        registry=Registry();seen=[];lock=threading.Lock();barrier=threading.Barrier(2)
        def provider(request,context):
            context.checkpoint()
            with lock:
                first=request.input==20 and 20 not in seen;seen.append(request.input)
            if first:raise ValueError('transient controlled failure')
            barrier.wait(timeout=3)
            return Final(request.input*2)
        registry.register_provider(ProviderSpec('double',provider))
        source='''import "wrappers.chaos" as W;
@W.retry
@W.cached
agent worker{provider:"double",tools:[],maxSteps:1};
let first=await gather([worker(20),worker(21)]);
[first,await worker(20)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('decorated_agents',source=source,files={'wrappers.chaos':WRAPPERS},capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            self.assertEqual(run.result(timeout=10),[[40,42],40])
            self.assertCountEqual(seen,[20,20,21])
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),2)
            self.assertTrue(any(e['type']=='program.task.completed' for e in events))

    def test_property_effects_and_class_factory_use_current_task(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input);return Final(request.input*2)
        registry.register_provider(ProviderSpec('double',provider))
        source='''agent worker{provider:"double",tools:[],maxSteps:1};
class Box {
 var worker:Function=fn(x){x};var _n:Int=0;
 fn __init__(self,action,value){self.worker=action;self._n=value;null}
 @classmethod
 fn create(cls,action,value){cls(action,value)}
 @staticmethod
 fn twice(x){x*2}
 @property
 async fn answer(self){self.worker(self._n)}
 @property
 fn value(self){self._n}
 @value.setter
 fn value(self,v){self._n=self.worker(v);null}
}
class Child(Box){}
let b=Child.create(worker,21);
let answer=await b.answer;
async fn mutate(){b.value=20;b.value}
[answer,await mutate(),b.value,Child.twice(21),isinstance(b,Child),type_of(Child.answer)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('descriptor_agents',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[42,40,40,42,True,'Descriptor'])
        self.assertEqual(seen,[21,20])

    def test_cancelling_decorated_property_drains_provider(self):
        registry=Registry();entered=threading.Event();exited=threading.Event()
        def provider(request,context):
            entered.set()
            try:
                for _ in range(500):context.checkpoint();threading.Event().wait(.01)
                raise AssertionError('Cancellation did not reach getter effect')
            finally:exited.set()
        registry.register_provider(ProviderSpec('cooperative',provider))
        source='''agent worker{provider:"cooperative",tools:[],maxSteps:1};
fn pass_through(f){async fn(*args,**kwargs){await f(*args,**kwargs)}}
class Box {
 @property
 @pass_through
 async fn answer(self){worker(1)}
}
await Box().answer'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('getter_cancel',source=source,capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            try:
                self.assertTrue(entered.wait(3));run.cancel()
                self.assertEqual(run.wait(timeout=5)['status'],'cancelled')
                self.assertTrue(exited.is_set())
            finally:run.cancel()

    def test_decorated_provider_cannot_add_tool_capability(self):
        registry=Registry();effects=[]
        @registry.tool('secret',input_type='Any',output_type='Int')
        def secret(value,context):effects.append(value);return 42
        registry.register_provider(ProviderSpec('asking',lambda request,context:ToolCall('secret',request.input)))
        source='''fn wrapped(f){fn(x){f(x)}}
@wrapped
agent worker{provider:"asking",tools:[],maxSteps:2};
try{worker(1)}catch error{"denied"}'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('decorated_denied',source=source,capabilities=['secret'])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),'denied')
        self.assertEqual(effects,[])

    def test_http_decorator_ir_edit_preserves_old_run_and_source(self):
        main='import "lib.chaos" as L;L.answer(40)'
        library='fn add(n){fn(f){fn(x){f(x)+n}}}\n@add(2)\nfn answer(x){x};export{answer}'
        with tempfile.TemporaryDirectory() as temp,Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                p=client.create_project('decorator_edit',source=main,files={'lib.chaos':library},capabilities=[])
                old=client.start(p['id'],p['version'],None,mode='source');self.assertEqual(old.result(),42)
                program=client.program(p['id'],file='lib.chaos')['program']
                decorated=next(n for n in program['root']['statements']if n['kind']=='decorated')
                literal=decorated['decorators'][0]['arguments'][0]
                p=client.edit_program(p['id'],p['version'],file='lib.chaos',operations=[{'op':'set','id':literal['id'],'field':'value','value':3}])
                self.assertIn('@',p['files']['lib.chaos'])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['output'],42)
                self.assertEqual(old.get()['snapshot']['files']['lib.chaos'],library)
                self.assertEqual(client.snapshot(p['id'])['project']['version'],p['version'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_cli_sdk_macro_decorators_share_program(self):
        source='''// macro-generated decorated callable
import "forms.chaos" as F;
fn plus(f){fn(x){f(x)+2}}
F.make!(plus)(40) // preserved
'''
        files={'forms.chaos':'macro make(dec){quote{@unquote(dec)\nfn answer(x){x};answer}};export{make}'}
        formatted=format_source(source);self.assertIn('// preserved',formatted)
        for text in (source,formatted,format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text,files=files),42)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'main.chaos';path.write_text(source)
            for name,text in files.items():(Path(temp)/name).write_text(text)
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parent.parent))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=temp,env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)

    def test_macro_generated_property_calls_registered_agent(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input);return Final(request.input*2)
        registry.register_provider(ProviderSpec('double',provider))
        source='''macro component(prop,action){quote{
 class Component {
   let action=unquote(action);
   @unquote(prop)
   async fn answer(self){self.action(21)}
 };Component
}}
agent worker{provider:"double",tools:[],maxSteps:1};
let Component=component!(property,worker);await Component().answer'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('macro_descriptor_agent',source=source,capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            self.assertEqual(run.result(timeout=5),42)
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),1)
        self.assertEqual(seen,[21])


if __name__=='__main__':unittest.main()
