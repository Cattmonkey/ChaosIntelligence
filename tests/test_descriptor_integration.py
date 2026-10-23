"""User descriptor protocols compose with real agents, tasks and versioned editing."""
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


FIELD = '''class RemoteField {
  var action:Function=fn(x){x};var key:String="";
  fn __init__(self,action){self.action=action;null}
  fn __set_name__(self,owner,name){
    self.key="_"+name;
    self.action({operation:"name",owner:owner,name:name});null
  }
  fn __get__(self,object,owner){
    if object==null{self}else{
      self.action({operation:"get",value:getattr(object,self.key),owner:owner})
    }
  }
  fn __set__(self,object,value){
    setattr(object,self.key,self.action({operation:"set",value:value}));null
  }
  fn __delete__(self,object){
    self.action({operation:"delete",value:getattr(object,self.key)});
    delattr(object,self.key);null
  }
};export{RemoteField}'''


class DescriptorIntegration(unittest.TestCase):
    def test_imported_descriptor_names_reads_writes_deletes_in_current_task(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input)
            data=request.input
            return Final(data['value']*2 if data['operation']=='set' else data.get('value'))
        registry.register_provider(ProviderSpec('field',provider))
        source='''import "field.chaos" as F;
agent worker{provider:"field",tools:[],maxSteps:1};
async fn exercise(){
  class Box{let value=F.RemoteField(worker)}
  class Child(Box){}
  let b=Child();b.value=21;let result=b.value;
  let descriptor:Descriptor=Child.value;
  delattr(b,"value");
  [result,vars(b),type_of(descriptor),isinstance(descriptor,F.RemoteField)]
}
await exercise()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('protocol_effects',source=source,files={'field.chaos':FIELD},capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            self.assertEqual(run.result(timeout=8),[42,{},'Instance',True])
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),4)
        self.assertEqual([x['operation'] for x in seen],['name','set','get','delete'])
        self.assertEqual(seen[0]['owner'],{'$class':'Box'})
        self.assertEqual(seen[2]['owner'],{'$class':'Child'})

    def test_async_and_stream_getters_share_agent_runtime(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input);return Final(request.input*2)
        registry.register_provider(ProviderSpec('double',provider))
        source='''agent worker{provider:"double",tools:[],maxSteps:1};
class Answer{async fn __get__(self,object,owner){worker(object.n)}}
class Values{async fn* __get__(self,object,owner){yield worker(object.n);yield worker(object.n+1)}}
class Box{var n=20;let answer=Answer();let values=Values()}
let b=Box();[await b.answer,await acollect(b.values)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('protocol_streams',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[40,[40,42]])
        self.assertEqual(seen,[20,20,21])

    def test_each_protocol_effect_cancels_and_drains_worker(self):
        bodies={
            'get':'fn __get__(self,object,owner){worker(1)}',
            'set':'fn __set__(self,object,value){worker(1);null}',
            'delete':'fn __delete__(self,object){worker(1);null}',
            'name':'fn __set_name__(self,owner,name){worker(1);null}',
        }
        actions={'get':'b.x','set':'b.x=1','delete':'delattr(b,"x")','name':'null'}
        for label,body in bodies.items():
            with self.subTest(protocol=label):
                entered=threading.Event();exited=threading.Event();registry=Registry()
                def provider(request,context):
                    entered.set()
                    try:
                        for _ in range(500):context.checkpoint();threading.Event().wait(.01)
                        raise AssertionError('Protocol effect ignored cancellation')
                    finally:exited.set()
                registry.register_provider(ProviderSpec('cooperative',provider))
                source='agent worker{provider:"cooperative",tools:[],maxSteps:1};class D{'+body+'};async fn work(){class Box{let x=D()};let b=Box();'+actions[label]+'};await work()'
                with Framework(registry=registry,settings=Settings(':memory:')) as app:
                    p=app.create_project('cancel_'+label,source=source,capabilities=[])
                    run=app.start(p['id'],p['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(3));run.cancel()
                        self.assertEqual(run.wait(timeout=5)['status'],'cancelled')
                        self.assertTrue(exited.is_set())
                    finally:run.cancel()

    def test_protocol_does_not_add_agent_tool_capabilities(self):
        registry=Registry();effects=[]
        @registry.tool('secret',input_type='Any',output_type='Int')
        def secret(value,context):effects.append(value);return 42
        registry.register_provider(ProviderSpec('asking',lambda request,context:ToolCall('secret',request.input)))
        source='''agent worker{provider:"asking",tools:[],maxSteps:2};
class D{fn __get__(self,object,owner){worker(1)}}
class Box{let x=D()};try{Box().x}catch error{"denied"}'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('protocol_denied',source=source,capabilities=['secret'])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),'denied')
        self.assertEqual(effects,[])

    def test_failed_name_hook_keeps_completed_effect_evidence(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(None)
        registry.register_provider(ProviderSpec('record',provider))
        source='''agent worker{provider:"record",tools:[],maxSteps:1};
class D{fn __set_name__(self,owner,name){worker(name);if name=="bad"{throw "stop"}else{null}}}
async fn work(){try{class Hidden{let first=D();let bad=D();let later=D()}}catch error{"failed"}}
await work()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('name_failure',source=source,capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            self.assertEqual(run.result(timeout=8),'failed')
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),2)
        self.assertEqual(seen,['first','bad'])

    def test_property_deletion_calls_agent_inside_task(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(None)
        registry.register_provider(ProviderSpec('erase',provider))
        source='''agent worker{provider:"erase",tools:[],maxSteps:1};
class Box{var _n=0;
@property
fn value(self){self._n}
@value.setter
fn value(self,x){self._n=x;null}
@value.deleter
fn value(self){worker(self._n);delattr(self,"_n");null}
};let b=Box();b.value=42;async fn erase(){delattr(b,"value");b.value};[await erase(),vars(b)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('property_delete',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[0,{}])
        self.assertEqual(seen,[42])

    def test_http_descriptor_structural_edit_keeps_old_run(self):
        main='import "fields.chaos" as F;F.Box().answer'
        library='class D{fn __get__(self,object,owner)->Int{42}};class Box{let answer=D()};export{Box}'
        with tempfile.TemporaryDirectory() as temp,Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                p=client.create_project('protocol_edit',source=main,files={'fields.chaos':library},capabilities=[])
                old=client.start(p['id'],p['version'],None,mode='source');self.assertEqual(old.result(),42)
                program=client.program(p['id'],file='fields.chaos')['program']
                def find_literal(node):
                    if isinstance(node,dict):
                        if node.get('kind')=='literal' and node.get('value')==42:return node
                        for value in node.values():
                            found=find_literal(value)
                            if found:return found
                    elif isinstance(node,list):
                        for value in node:
                            found=find_literal(value)
                            if found:return found
                literal=find_literal(program);self.assertIsNotNone(literal)
                p=client.edit_program(p['id'],p['version'],file='fields.chaos',operations=[{'op':'set','id':literal['id'],'field':'value','value':43}])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['output'],42)
                self.assertEqual(old.get()['snapshot']['files']['fields.chaos'],library)
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_cli_sdk_macro_generated_descriptor_and_delete(self):
        source='''// user protocols are ordinary macro-generated methods
import "forms.chaos" as F;
let Box=F.component!();let b=Box();b.value=40;
let first=b.value;delattr(b,"value");[first,b.value] // preserve this
'''
        files={'forms.chaos':'''macro component(){quote{
class D{fn __get__(self,object,owner)->Int{42}}
class Box{let value=D()};Box
}};export{component}'''}
        formatted=format_source(source);self.assertIn('// preserve this',formatted)
        for text in (source,formatted,format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text,files=files),[40,42])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'main.chaos';path.write_text(source)
            for name,text in files.items():(Path(temp)/name).write_text(text)
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parent.parent))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=temp,env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),[40,42])


if __name__=='__main__':unittest.main()
