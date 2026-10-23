"""Item effects, module dialect snapshots, and GUI/headless storage consistency."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry, ToolCall
from chaos.ide_files import edit_project_files
from chaos.ide_language import analyze,format_source
from chaos.modules import ProgramBundle,project_language_versions
from chaos.program import format_program,parse_program,plain,decode_program
from chaos.server import make_server
from chaos.settings import Settings


class IndexingIntegration(unittest.TestCase):
    def execute(self,source,handler):
        registry=Registry();registry.register_provider(ProviderSpec('items',handler))
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            p=app.create_project('item_effects',source=source,capabilities=[]);run=app.start(p['id'],p['version'],None,mode='source')
            result=run.result(timeout=8);events=app.events(p['id'],run_id=run.id,limit=300)['items']
        return result,events

    def test_agent_reads_slices_write_delete_and_property_isolation(self):
        seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input.get('value'))
        source='''agent worker{provider:"items",tools:[],maxSteps:1};
class C{var items=[40,41,42];@property
fn x(self){7}
fn __getitem__(self,key){if type_of(key)=="String"{worker({op:"get",value:99})}else{worker({op:"get",value:self.items[key]})}}
fn __setitem__(self,key,item){worker({op:"set",value:item});self.items=self.items[:key]+[item]+self.items[key+1:];null}
fn __delitem__(self,key){worker({op:"delete",value:key});self.items=[];null}}
async fn work(){let c=C();let a=c.x;let b=c["x"];let d=c[::-1];c[1]=50;let e=c[1];delitem(c,1);[a,b,d,e,length(c.items)]};await work()'''
        result,events=self.execute(source,provider)
        self.assertEqual(result,[7,99,[42,41,40],50,0]);self.assertEqual([v['op']for v in seen],['get','get','set','get','delete'])
        self.assertEqual(sum(e['type']=='agent.completed'for e in events),5)

    def test_descriptor_binding_and_item_method_use_current_task(self):
        seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input)
        source='''agent worker{provider:"items",tools:[],maxSteps:1};
class D{fn __get__(self,obj,owner){worker("bind");fn(key){worker(key)}}}
class C{let __getitem__=D();var x=40}
async fn work(){let c=C();[c.x,c[42]]};await work()'''
        result,_=self.execute(source,provider);self.assertEqual(result,[40,42]);self.assertEqual(seen,['bind',42])

    def test_async_and_stream_getters_are_explicit_and_cold(self):
        seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input);return Final(request.input)
        source='''agent worker{provider:"items",tools:[],maxSteps:1};var started=0;
class Async{async fn __getitem__(self,key){started=started+1;worker(key)}}
class Stream{fn* __getitem__(self,key){yield worker(key);yield worker(key+1)}}
let task=Async()[40];let before=started;let value=await task;async fn consume(){collect(Stream()[41])};[before,value,await consume(),started]'''
        result,_=self.execute(source,provider);self.assertEqual(result,[0,40,[41,42],1]);self.assertEqual(seen,[40,41,42])

    def test_independent_item_effects_are_concurrent(self):
        gate=threading.Barrier(2);threads=set();lock=threading.Lock()
        def provider(request,context):
            context.checkpoint()
            with lock:threads.add(threading.get_ident())
            gate.wait(timeout=3);return Final(request.input)
        source='agent worker{provider:"items",tools:[],maxSteps:1};class C{fn __getitem__(self,key){worker(key)}}async fn work(n){C()[n]};await gather([work(40),work(42)])'
        result,_=self.execute(source,provider);self.assertEqual(result,[40,42]);self.assertEqual(len(threads),2)

    def test_cancellation_get_set_delete_and_descriptor_drains(self):
        cases=[('get','class C{fn __getitem__(self,key){worker(1)}}','C()[0]'),
               ('set','class C{fn __setitem__(self,key,item){worker(1);null}}','C()[0]=42'),
               ('delete','class C{fn __delitem__(self,key){worker(1);null}}','delitem(C(),0)'),
               ('descriptor','class D{fn __get__(self,obj,owner){worker(1);fn(key){42}}}class C{let __getitem__=D()}','C()[0]')]
        for label,declaration,expression in cases:
            with self.subTest(operation=label):
                entered=threading.Event();exited=threading.Event();registry=Registry()
                def provider(request,context):
                    entered.set()
                    try:
                        for _ in range(500):context.checkpoint();threading.Event().wait(.01)
                        raise AssertionError('Cancellation did not stop item worker')
                    finally:exited.set()
                registry.register_provider(ProviderSpec('cooperative',provider))
                source='agent worker{provider:"cooperative",tools:[],maxSteps:1};'+declaration+';async fn work(){'+expression+'};await work()'
                with Framework(registry=registry,settings=Settings(':memory:'))as app:
                    p=app.create_project('cancel_'+label,source=source,capabilities=[]);run=app.start(p['id'],p['version'],None,mode='source')
                    try:self.assertTrue(entered.wait(3));run.cancel();self.assertEqual(run.wait(timeout=5)['status'],'cancelled');self.assertTrue(exited.is_set())
                    finally:run.cancel()

    def test_capability_denial_inside_index_hook(self):
        registry=Registry();effects=[]
        @registry.tool('secret',input_type='Any',output_type='Int')
        def secret(value,context):effects.append(value);return 42
        registry.register_provider(ProviderSpec('asking',lambda request,context:ToolCall('secret',request.input)))
        source='agent worker{provider:"asking",tools:[],maxSteps:2};class C{fn __getitem__(self,key){worker(key)}}try{C()[1]}catch e{"denied"}'
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            p=app.create_project('index_denied',source=source,capabilities=['secret']);self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),'denied')
        self.assertEqual(effects,[])

    def test_target_bounds_rhs_once_and_ordered(self):
        seen=[]
        def provider(request,context):context.checkpoint();seen.append(request.input['label']);return Final(request.input['value'])
        source='''agent worker{provider:"items",tools:[],maxSteps:1};
fn mark(label,value){worker({label:label,value:value})}
class C{fn __setitem__(self,key,item){mark("hook",null);null}}
fn target(){mark("target",null);C()}
async fn work(){target()[mark("start",0):mark("stop",4):mark("step",2)]=mark("rhs",[40,42]);42};await work()'''
        result,_=self.execute(source,provider);self.assertEqual(result,42);self.assertEqual(seen,['target','start','stop','step','rhs','hook'])

    def legacy_project(self,app,name='legacy'):
        main='import "lib.chaos" as L;L.value()';library='class C{var x=40;fn __getitem__(self,key){42}}fn value(){C()["x"]};export{value}'
        p=app.create_project(name,source=main,files={'lib.chaos':library},capabilities=[])
        legacy=parse_program(main,language_version=1);legacy.pop('languageVersion',None)
        with app._store.write():app._store.db.execute('UPDATE projects SET program=? WHERE id=?',(json.dumps(legacy),name))
        return app.get_project(name),library

    def test_historical_module_execution_and_edit_only_upgrades_changed_file(self):
        with Framework(settings=Settings(':memory:'))as app:
            p,library=self.legacy_project(app);self.assertEqual(project_language_versions(p),{'main.chaos':1,'lib.chaos':1})
            old=app.start(p['id'],p['version'],None,mode='source');self.assertEqual(old.result(),40)
            p=app.edit_program(p['id'],p['version'],source=p['source']+' // changed entry\n')
            self.assertEqual(p['program']['sourceVersions'],{'main.chaos':2,'lib.chaos':1});self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),40)
            p=app.edit_program(p['id'],p['version'],file='lib.chaos',source=library+' // use current syntax\n')
            self.assertEqual(p['program']['sourceVersions'],{'main.chaos':2,'lib.chaos':2});self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),42)
            self.assertEqual(old.get()['output'],40);self.assertNotIn('languageVersion',old.get()['snapshot']['program'])

    def test_legacy_structure_edit_and_file_rename_preserve_semantics(self):
        with Framework(settings=Settings(':memory:'))as app:
            p,_=self.legacy_project(app);program=app.program(p['id'],file='lib.chaos')['program']
            self.assertEqual(program['languageVersion'],1)
            p=app.edit_program(p['id'],p['version'],file='lib.chaos',program=program)
            self.assertIn('.[',p['files']['lib.chaos']);self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),40)
            p,_=self.legacy_project(app,'rename_legacy')
            p=edit_project_files(app,p['id'],{'version':p['version'],'operation':'rename','path':'lib.chaos','newPath':'library.chaos'})
            self.assertEqual(p['program']['sourceVersions'],{'main.chaos':1,'library.chaos':1});self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),40)

    def test_old_ir_with_item_hooks_roundtrips_without_dispatch_change(self):
        source='class C{var x=40;fn __getitem__(self,key){99}}let c=C();c["x"]=42;[c.x,c["x"]]'
        ir=parse_program(source,language_version=1);ir.pop('languageVersion',None);canonical=format_program(ir)
        self.assertEqual(plain(decode_program(ir)),plain(decode_program(parse_program(canonical))))
        self.assertEqual(Framework.evaluate(canonical),[42,42]);self.assertEqual(Framework.evaluate(source),[42,99])

    def test_http_slice_patch_preserves_old_run_and_versions(self):
        with tempfile.TemporaryDirectory()as temp,Framework(settings=Settings(':memory:'))as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));source='[40,41,42][::2]';p=client.create_project('index_edit',source=source,capabilities=[]);old=client.start(p['id'],p['version'],None,mode='source');self.assertEqual(old.result(),[40,42])
                program=client.program(p['id'])['program'];node=program['root']['statements'][0]['index']['step'];p=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':node['id'],'field':'value','value':1}])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(),[40,41,42]);self.assertEqual(old.get()['snapshot']['source'],source);self.assertEqual(old.get()['output'],[40,42])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_cli_module_macro_and_formatting(self):
        source='import "forms.chaos" as F;F.pick!([40,41,42]) // keep\n';library='macro pick(xs){quote{unquote(xs)[::2]}};export{pick}'
        files={'forms.chaos':library};formatted=format_source(source);self.assertIn('// keep',formatted)
        for text in (source,formatted,format_program(parse_program(source))):self.assertEqual(Framework.evaluate(text,files=files),[40,42])
        with tempfile.TemporaryDirectory()as temp:
            path=Path(temp)/'main.chaos';path.write_text(source);(Path(temp)/'forms.chaos').write_text(library)
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=temp,env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parent.parent)),text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),[40,42])

    def test_module_version_validation_and_legacy_formatter(self):
        from chaos.language import LanguageError,Evaluator
        for versions in ({'main.chaos':True},{'missing.chaos':1},{'main.chaos':3}):
            with self.subTest(versions=versions),self.assertRaises(LanguageError):ProgramBundle('42',language_versions=versions)
        source='class C{var x=40;fn __getitem__(self,k){42}};C()["x"] // retained\n'
        formatted=format_source(source,language_version=1);self.assertIn('// retained',formatted);self.assertEqual(Evaluator().run(formatted),40)
        self.assertEqual(Evaluator().run(ProgramBundle(source,language_versions={'main.chaos':1})),40)


    def test_explicit_entry_dialect_change_invalidates_ir_without_source_change(self):
        source='class C{var x=40;fn __getitem__(self,key){42}};C()["x"]'
        with Framework(settings=Settings(':memory:'))as app:
            p=app.create_project('entry_dialect',source=source,capabilities=[])
            for version,result,kind in [(1,40,'index'),(2,42,'subscript')]:
                p=app.save_project({**p,'sourceVersions':{'main.chaos':version}},from_view='source')
                ir=app.program(p['id'])['program'];self.assertEqual(ir['languageVersion'],version)
                self.assertEqual(ir['root']['statements'][-1]['kind'],kind)
                self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),result)
                self.assertEqual(p['source'],source)

    def test_explicit_module_dialect_change_invalidates_cached_structural_ir(self):
        library='class C{var x=40;fn __getitem__(self,key){42}};fn read(){C()["x"]};export{read}'
        with Framework(settings=Settings(':memory:'))as app:
            p=app.create_project('module_dialect',source='import "lib.chaos" as L;L.read()',files={'lib.chaos':library},capabilities=[])
            ir=app.program(p['id'],file='lib.chaos')['program']
            p=app.edit_program(p['id'],p['version'],program=ir,file='lib.chaos')
            source=p['files']['lib.chaos']
            self.assertIsNotNone(app._store.db.execute('SELECT 1 FROM module_programs WHERE project_id=?',(p['id'],)).fetchone())
            for version,result,kind in [(1,40,'index'),(2,42,'subscript')]:
                p=app.save_project({**p,'sourceVersions':{'lib.chaos':version}},from_view='source')
                ir=app.program(p['id'],file='lib.chaos')['program'];self.assertEqual(ir['languageVersion'],version)
                read=next(n for n in ir['root']['statements'] if n.get('name')=='read')
                self.assertEqual(read['function']['body']['statements'][0]['kind'],kind)
                self.assertEqual(p['files']['lib.chaos'],source)
                self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(),result)

if __name__=='__main__':unittest.main()
