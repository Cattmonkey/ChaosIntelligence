"""Agent-backed user iterators share real Framework effects, epochs and editing."""
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


class IteratorIntegration(unittest.TestCase):
    def test_reusable_agent_source_sync_iterator_and_close(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input)
            return Final(request.input.get('value') if request.input['op']=='next' else None)
        registry.register_provider(ProviderSpec('source',provider))
        source='''agent worker{provider:"source",tools:[],maxSteps:1};
class Cursor{
 var n:Int=40;
 fn __iter__(self){self}
 fn __next__(self){if self.n<42{let value=worker({op:"next",value:self.n});self.n=self.n+1;{done:false,value:value}}else{{done:true,value:99}}}
 fn __close__(self){worker({op:"close"});null}
}
class Source{fn __iter__(self){Cursor()}}
async fn exercise(){let source=Source();let first=collect(source);let cursor=iter(source);let item=next(cursor);close(cursor);[first,item,next(cursor)]}
await exercise()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('iterator_effects',source=source,capabilities=[]);run=app.start(p['id'],p['version'],None,mode='source')
            self.assertEqual(run.result(timeout=8),[[40,41],{'done':False,'value':40},{'done':True,'value':None}])
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed'for e in events),5)
        self.assertEqual([item['op']for item in seen],['next','next','next','close','close'])

    def test_async_raw_and_wrapped_aliases_and_cold_operation(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input)
        registry.register_provider(ProviderSpec('echo',provider))
        source='''agent worker{provider:"echo",tools:[],maxSteps:1};
var started=0;
class Cursor{var n=40;fn __aiter__(self){self}
 async fn __anext__(self){started=started+1;if self.n<42{let value=worker(self.n);self.n=self.n+1;{done:false,value:value}}else{{done:true,value:99}}}
}
let raw=Cursor();let cursor=aiter(raw);let task=anext(cursor);let before=started;
let first=await task;let rest=await acollect(cursor);[before,first,rest,await anext(raw),started]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('async_alias',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[0,{'done':False,'value':40},[41],{'done':True,'value':99},3])
        self.assertEqual(seen,[40,41])

    def test_generator_factory_methods_feed_for_and_async_for(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input*2)
        registry.register_provider(ProviderSpec('double',provider))
        source='''agent worker{provider:"double",tools:[],maxSteps:1};
class Source{fn* __iter__(self){yield worker(20);yield worker(21)}async fn* __aiter__(self){yield worker(20);yield worker(21)}}
async fn exercise(){var values=[];for item in Source(){values=append(values,item)};async for item in Source(){values=append(values,item)};values}
await exercise()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('generator_factories',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[40,42,40,42])
        self.assertEqual(seen,[20,21,20,21])

    def test_distinct_sync_iterators_cooperate_inside_concurrent_tasks(self):
        registry=Registry();gate=threading.Barrier(2);threads=set();lock=threading.Lock()
        def provider(request,context):
            context.checkpoint()
            with lock:threads.add(threading.get_ident())
            gate.wait(timeout=3);context.checkpoint();return Final(request.input)
        registry.register_provider(ProviderSpec('parallel',provider))
        source='''agent worker{provider:"parallel",tools:[],maxSteps:1};
class Cursor{var n=0;var done=false;fn __init__(self,n){self.n=n;null}fn __iter__(self){self}
 fn __next__(self){if self.done{{done:true,value:null}}else{self.done=true;{done:false,value:worker(self.n)}}}}
async fn consume(n){collect(Cursor(n))};await gather([consume(40),consume(42)])'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('parallel_iterators',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[[40],[42]])
        self.assertEqual(len(threads),2)

    def test_step_and_collection_cancel_drain_without_language_cleanup(self):
        cases={'next':('fn __iter__(self){self}fn __next__(self){worker({op:"step"})}fn __close__(self){worker({op:"cleanup"});null}','next(iter(C()))'),
               'collect':('fn __iter__(self){self}fn __next__(self){worker({op:"step"})}fn __close__(self){worker({op:"cleanup"});null}','collect(C())'),
               'anext':('fn __aiter__(self){self}async fn __anext__(self){worker({op:"step"})}async fn __aclose__(self){worker({op:"cleanup"});null}','await anext(aiter(C()))'),
               'acollect':('fn __aiter__(self){self}async fn __anext__(self){worker({op:"step"})}async fn __aclose__(self){worker({op:"cleanup"});null}','await acollect(C())')}
        for label,(body,action) in cases.items():
            with self.subTest(operation=label):
                entered=threading.Event();exited=threading.Event();cleanup=[];registry=Registry()
                def provider(request,context):
                    if request.input['op']=='cleanup':cleanup.append(True);return Final(None)
                    entered.set()
                    try:
                        for _ in range(500):context.checkpoint();threading.Event().wait(.01)
                        raise AssertionError('Iterator effect ignored cancellation')
                    finally:exited.set()
                registry.register_provider(ProviderSpec('cooperative',provider))
                source='agent worker{provider:"cooperative",tools:[],maxSteps:1};class C{'+body+'};async fn work(){'+action+'};await work()'
                with Framework(registry=registry,settings=Settings(':memory:')) as app:
                    p=app.create_project('cancel_'+label,source=source,capabilities=[]);run=app.start(p['id'],p['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(3));run.cancel();self.assertEqual(run.wait(timeout=5)['status'],'cancelled');self.assertTrue(exited.is_set());self.assertEqual(cleanup,[])
                    finally:run.cancel()

    def test_optional_send_throw_and_delegate_close_execute_agent(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input['op']);return Final(request.input.get('value'))
        registry.register_provider(ProviderSpec('dialog',provider))
        source='''agent worker{provider:"dialog",tools:[],maxSteps:1};
class Dialog{fn __iter__(self){self}
 fn __next__(self){{done:false,value:worker({op:"next",value:40})}}
 fn __send__(self,value){{done:false,value:worker({op:"send",value:value})}}
 fn __throw__(self,value){{done:false,value:worker({op:"throw",value:value})}}
 fn __close__(self){worker({op:"close"});null}}
fn* delegate(source){yield from source}
async fn exercise(){let g=delegate(Dialog());let result=[next(g),send(g,41),throw_into(g,42)];close(g);result};await exercise()'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('iterator_dialog',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),[{'done':False,'value':40},{'done':False,'value':41},{'done':False,'value':42}])
        self.assertEqual(seen,['next','send','throw','close'])

    def test_scope_finish_closes_unconsumed_completed_and_failed_user_sources(self):
        registry=Registry();seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(None)
        registry.register_provider(ProviderSpec('close',provider))
        source='''agent worker{provider:"close",tools:[],maxSteps:1};
class C{var label="";fn __init__(self,label){self.label=label;null}fn __iter__(self){self}
 fn __next__(self){if self.label=="failed"{throw "step error"}else{{done:true,value:42}}}
 fn __close__(self){worker(self.label);null}}
let unused=iter(C("unused"));let done=iter(C("done"));let failed=iter(C("failed"));next(done);try{next(failed)}catch e{null};42'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('iterator_scope',source=source,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(timeout=8),42)
        self.assertCountEqual(seen,['unused','done','failed'])

    def test_iterator_protocol_does_not_grant_tool_capabilities(self):
        registry=Registry();effects=[]
        @registry.tool('secret',input_type='Any',output_type='Int')
        def secret(value,context):effects.append(value);return 42
        registry.register_provider(ProviderSpec('asking',lambda request,context:ToolCall('secret',request.input)))
        source='agent worker{provider:"asking",tools:[],maxSteps:2};class C{fn __next__(self){worker(1)}};try{next(C())}catch error{"denied"}'
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('iterator_denied',source=source,capabilities=['secret'])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),'denied')
        self.assertEqual(effects,[])

    def test_http_structural_edit_preserves_previous_iterator_run(self):
        main='import "source.chaos" as S;collect(S.Source())';library='class Source{fn* __iter__(self){yield 40;yield 42}};export{Source}'
        with tempfile.TemporaryDirectory() as temp,Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('iterator_edit',source=main,files={'source.chaos':library},capabilities=[]);old=client.start(p['id'],p['version'],None,mode='source');self.assertEqual(old.result(),[40,42])
                program=client.program(p['id'],file='source.chaos')['program']
                def find(node):
                    if isinstance(node,dict):
                        if node.get('kind')=='literal'and node.get('value')==40:return node
                        for value in node.values():
                            found=find(value)
                            if found:return found
                    elif isinstance(node,list):
                        for value in node:
                            found=find(value)
                            if found:return found
                p=client.edit_program(p['id'],p['version'],file='source.chaos',operations=[{'op':'set','id':find(program)['id'],'field':'value','value':41}])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(),[41,42]);self.assertEqual(old.get()['output'],[40,42]);self.assertEqual(old.get()['snapshot']['files']['source.chaos'],library)
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_cli_macro_generated_iterator_and_formatting(self):
        source='import "forms.chaos" as F;let C=F.source!();collect(C()) // retained comment\n'
        files={'forms.chaos':'macro source(){quote{class C{fn* __iter__(self){yield 40;yield 42}};C}};export{source}'}
        formatted=format_source(source);self.assertIn('// retained comment',formatted)
        for text in (source,formatted,format_program(parse_program(source))):self.assertEqual(Framework.evaluate(text,files=files),[40,42])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'main.chaos';path.write_text(source)
            for name,text in files.items():(Path(temp)/name).write_text(text)
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parent.parent))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=temp,env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),[40,42])


if __name__=='__main__':unittest.main()
