"""Independent library objects, never reconstructed source or a second runtime."""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import chaos
from chaos import Evaluator, Framework, Registry, Settings, ToolCall, Final, LanguageError
from chaos.executable_image import compile_objects, compile_executable, link_objects, load_executable
from chaos.language import Parser, serializable
from test_executable231 import no_frontend

ROOT=Path(__file__).resolve().parents[1]
LIBRARY='''macro increment(x){quote{unquote(x)+1}};
var calls=0;fn answer(x){calls+=1;increment!(x)};fn count(){calls};export{answer,count}'''
CLIENT='import "lib.chaos" as L;[L.answer(41),L.answer(42),L.count()]'


@contextmanager
def no_library_parser(names):
    original=Parser.program
    def checked(parser,*args,**kwargs):
        if parser.filename in names:raise AssertionError('Library source was parsed: '+parser.filename)
        return original(parser,*args,**kwargs)
    with patch.object(Parser,'program',checked):yield


def evaluate(image,**kwargs):
    with no_frontend():return serializable(Evaluator(timeout=20,**kwargs).run(load_executable(json.loads(json.dumps(image)))))


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class SeparateCompilation234(unittest.TestCase):
    def library(self,source=LIBRARY,name='lib.chaos'):
        return compile_objects(source,entry=name,libraries=[])

    def test_None_keeps_v1_and_explicit_empty_libraries_selects_v2(self):
        old=compile_objects('42');new=compile_objects('42',libraries=[])
        self.assertEqual(old[0]['schema'],'chaos.module-object/1');self.assertNotIn('requires',old[0])
        self.assertEqual(new[0]['schema'],'chaos.module-object/2');self.assertEqual(new[0]['requires'],{})
        self.assertEqual(compile_executable('42')['schema'],'chaos.executable/1')
        self.assertEqual(compile_executable('42',libraries=[])['schema'],'chaos.executable/2')
        self.assertEqual(evaluate(link_objects(old)),42);self.assertEqual(evaluate(link_objects(new)),42)

    def test_sdk_aliases_forward_libraries_to_the_one_compiler(self):
        libraries=self.library();before=copy.deepcopy(libraries)
        self.assertIs(chaos.compile_objects,compile_objects);self.assertIs(chaos.compile_executable,compile_executable)
        with no_library_parser({'lib.chaos'}):
            objects=Framework.compile_objects(CLIENT,libraries=libraries)
            image=Framework.compile_executable(CLIENT,libraries=libraries)
        self.assertEqual(evaluate(Framework.link_objects(objects)),[42,43,2]);self.assertEqual(evaluate(image),[42,43,2])
        self.assertEqual(libraries,before)

    def test_client_build_uses_absent_library_runtime_ast_and_internal_expanded_macro(self):
        libraries=self.library();before=copy.deepcopy(libraries)
        with no_library_parser({'lib.chaos'}):
            objects=compile_objects(CLIENT,libraries=json.loads(json.dumps(libraries,sort_keys=True)))
        self.assertTrue({'lib.chaos','main.chaos'} <= {o['module'] for o in objects})
        with no_frontend():image=link_objects(objects)
        self.assertEqual(evaluate(image),[42,43,2]);self.assertEqual(libraries,before)

    def test_client_own_macro_hygiene_is_available_without_library_macros(self):
        libraries=self.library()
        source='''import "lib.chaos" as L;macro twice(x){quote{let saved=unquote(x);saved+saved}};
let saved=100;[twice!(L.answer(20)),saved,L.count()]'''
        with no_library_parser({'lib.chaos'}):image=compile_executable(source,libraries=libraries)
        self.assertEqual(evaluate(image),[42,100,1])

    def test_external_exported_macro_is_explicitly_rejected_without_effects(self):
        library=self.library('macro inc(x){quote{unquote(x)+1}};tool("never",1);export{inc}')
        for source in ('import "lib.chaos" as L;L.inc!(41)',
                       'import "lib.chaos" as L;let Alias=L;Alias.inc!(41)',
                       'import "lib.chaos" as L;macro forward(x){quote{L.inc!(unquote(x))}};forward!(41)'):
            with self.subTest(source=source),no_library_parser({'lib.chaos'}),self.assertRaises(LanguageError) as caught:
                compile_executable(source,libraries=library)
            self.assertEqual(caught.exception.code,'macro_error')

    def test_publication_client_compile_link_and_load_are_effect_inert(self):
        source='tool("module-init",1);fn f(x=tool("default",2)){x};export{f}'
        with patch.object(Evaluator,'run',side_effect=AssertionError('Build executed module code')):
            libraries=self.library(source)
            objects=compile_objects('import "lib.chaos" as L;L.f()',libraries=libraries)
            with no_frontend():load_executable(link_objects(objects))

    def test_module_initialization_defaults_cells_and_closures_are_once_and_fresh(self):
        source='''tool("init",0);fn make(seed=tool("default",40)){
var n=seed;fn next(){n+=1;n};next};export{make}'''
        image=compile_executable('import "lib.chaos" as L;let a=L.make();let b=L.make();[a(),a(),b()]',libraries=self.library(source))
        for _ in range(2):
            calls=[];value=evaluate(image,tool_handler=lambda n,v:calls.append((n,v)) or v)
            self.assertEqual(value,[41,42,41]);self.assertEqual(calls,[('init',0),('default',40)])

    def test_diamond_libraries_initialize_shared_dependency_once(self):
        base=self.library('tool("init",40);fn answer(){40};export{answer}',name='base.chaos')
        left=compile_objects('import "base.chaos" as B;fn answer(){B.answer()+1};export{answer}',entry='left.chaos',libraries=base)
        right=compile_objects('import "base.chaos" as B;fn answer(){B.answer()+2};export{answer}',entry='right.chaos',libraries=base)
        image=compile_executable('import "left.chaos" as L;import "right.chaos" as R;[L.answer(),R.answer()]',libraries=left+right)
        calls=[];self.assertEqual(evaluate(image,tool_handler=lambda n,v:calls.append((n,v)) or v),[41,42]);self.assertEqual(calls,[('init',40)])

    def test_independent_stdlib_closures_link_and_requires_pin_actual_objects(self):
        a=self.library('import "std/math" as M;fn answer(){40};export{answer}',name='a.chaos')
        b=self.library('import "std/context" as C;fn answer(){2};export{answer}',name='b.chaos')
        with no_library_parser({'a.chaos','b.chaos','std/math','std/context'}):
            image=compile_executable('import "a.chaos" as A;import "b.chaos" as B;A.answer()+B.answer()',libraries=a+b)
        table={o['module']:o for o in image['objects']};self.assertTrue({'std/math','std/context'} <= set(table))
        for obj in image['objects']:
            for name,required in obj['requires'].items():self.assertEqual(required,table[name]['objectHash'])
        self.assertEqual(evaluate(image),42)

    def test_changed_library_object_cannot_silently_satisfy_old_client_dependency(self):
        library=self.library('fn answer(){42};export{answer}');objects=compile_objects('import "lib.chaos" as L;L.answer()',libraries=library)
        replacement=self.library('fn answer(){99};export{answer}');changed=[o for o in objects if o['module']!='lib.chaos']+replacement
        with no_frontend(),self.assertRaises(LanguageError) as caught:link_objects(changed)
        self.assertEqual(caught.exception.code,'executable_error')
        self.assertEqual(evaluate(link_objects(objects)),42)

    def test_conflicting_duplicate_module_and_missing_dependency_are_rejected(self):
        one=self.library('fn answer(){1};export{answer}');two=self.library('fn answer(){2};export{answer}')
        for libraries in (one+two,):
            with self.assertRaises(LanguageError) as caught:compile_objects(CLIENT,libraries=libraries)
            self.assertEqual(caught.exception.code,'executable_error')
        objects=compile_objects('import "lib.chaos" as L;L.answer()',libraries=one)
        with no_frontend(),self.assertRaises(LanguageError):link_objects([o for o in objects if o['module']!='lib.chaos'])

    def test_invalid_library_authority_or_recomputed_requirements_reject_before_build(self):
        library=self.library('import "std/math" as M;fn answer(){42};export{answer}')
        for mode in ('authority','dependency'):
            bad=copy.deepcopy(library);obj=next(o for o in bad if o['module']=='lib.chaos')
            if mode=='authority':obj['owner']='host'
            else:obj['requires']['std/math']='0'*64
            obj['objectHash']=digest({k:v for k,v in obj.items() if k!='objectHash'})
            with self.subTest(mode=mode),no_library_parser({'lib.chaos'}),self.assertRaises(LanguageError):compile_objects(CLIENT,libraries=bad)

    def test_four_exported_flavors_remain_cold_and_preserve_full_results(self):
        library=self.library('''var hits=[];fn f(x){hits=append(hits,"fn");x+1};async fn a(x){hits=append(hits,"async");await sleep(0);x+2};fn* g(x){hits=append(hits,"gen");yield x;return_values(x+3,x+4)};async fn* ag(x){hits=append(hits,"asyncgen");await sleep(0);yield x;return_values(x+5,x+6)};fn seen(){hits};export{f,a,g,ag,seen}''')
        source='''import "lib.chaos" as L;let a=L.a(40);let g=L.g(42);let ag=L.ag(42);let cold=L.seen();let out=[L.f(41),await a,collect(g),await acollect(ag)];[cold,out,multiple_value_list(g),multiple_value_list(ag),L.seen()]'''
        self.assertEqual(evaluate(compile_executable(source,libraries=library)),[[],[42,42,[42],[42]],[45,46],[47,48],['fn','async','gen','asyncgen']])

    def test_library_agent_and_registered_tool_still_run_on_original_persistent_host(self):
        calls=[];providers=[];registry=Registry()
        @registry.tool('fixture.library234.echo',effect='write')
        def echo(value,ctx):ctx.checkpoint();calls.append(value);return value+2
        @registry.provider('fixture.library234')
        def provider(request,ctx):
            ctx.checkpoint();providers.append(request.step)
            return ToolCall('fixture.library234.echo',request.input) if request.step==1 else Final(request.history[-1]['output'])
        library=self.library('agent worker{provider:"fixture.library234",tools:["fixture.library234.echo"]};fn execute(x){worker(x)};export{execute}')
        image=compile_executable('import "lib.chaos" as L;L.execute(input)',libraries=library)
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('library-host',source='999',capabilities=['fixture.library234.echo'])
            with no_frontend():run=app.start_executable(p['id'],p['version'],image,40);self.assertEqual(run.result(10),42)
            self.assertEqual(run.get()['snapshot']['execution']['image'],image);self.assertFalse(app._engine.active)
        self.assertEqual(calls,[40]);self.assertEqual(providers,[1,2])

    def test_real_HTTP_start_and_OpenAPI_support_matched_v2_without_library_source(self):
        from test_executable_surfaces232 import ExecutableHTTP232
        from chaos.client import ValidationError
        host=ExecutableHTTP232('test_OpenAPI_executable_admission_is_documented_without_new_authority_field')
        try:
            host.setUp();library=self.library('fn answer(x){x+2};export{answer}')
            image=compile_executable('import "lib.chaos" as L;L.answer(input)',libraries=library)
            schema=host.client._request('GET','/openapi.json')['components']['schemas']['ExecutableImage']
            self.assertEqual(set(schema['properties']['schema']['enum']),{'chaos.executable/1','chaos.executable/2','chaos.executable/3'})
            pairs={(row['properties']['schema']['const'],row['properties']['abi']['const']) for row in schema['oneOf']}
            self.assertEqual(pairs,{('chaos.executable/1','chaos.expanded-ast/1'),('chaos.executable/2','chaos.expanded-ast/2'),('chaos.executable/3','chaos.expanded-ast/3')})
            p=host.project
            with no_frontend():run=host.client.start_executable(p['id'],p['version'],image,40);self.assertEqual(run.result(10),42)
            self.assertEqual(run.get()['snapshot']['execution']['image'],image);self.assertEqual(host.effects,[])
            before=host.app.runs(p['id']);bad=copy.deepcopy(image);bad['schema']='chaos.executable/1'
            bad['imageHash']=digest({k:v for k,v in bad.items() if k!='imageHash'})
            with self.assertRaises(ValidationError):host.client.start_executable(p['id'],p['version'],bad,40)
            self.assertEqual(host.app.runs(p['id']),before)
        finally:host.doCleanups()

    def test_fresh_publisher_client_link_and_run_have_no_library_source(self):
        bootstrap=r'''import json,runpy,socket,sys
from pathlib import Path
root=Path(sys.argv[1]);mode=sys.argv[2];args=sys.argv[3:];sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
def denied(*args,**kwargs):raise AssertionError('Separate compilation fixture forbids network')
socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
from chaos.language import Parser
original=Parser.program
def guard(parser,*args,**kwargs):
 if parser.filename=='lib.chaos':raise AssertionError('Library source parsed in client')
 return original(parser,*args,**kwargs)
if mode=='client':Parser.program=guard
if mode in ('load','sdk'):
 from test_executable231 import no_frontend
 context=no_frontend();context.__enter__()
if mode=='sdk':
 from chaos import Framework
 print(json.dumps(Framework.evaluate(Framework.load_executable(json.loads(Path(args[0]).read_text())))));raise SystemExit(0)
sys.argv=['chaos']+args;runpy.run_module('chaos',run_name='__main__')'''
        with tempfile.TemporaryDirectory(prefix='separate234-') as directory:
            work=Path(directory);publisher=work/'publisher';client=work/'client';publisher.mkdir();client.mkdir()
            source=publisher/'lib.chaos';source.write_text(LIBRARY);empty=work/'empty.json';empty.write_text('[]')
            libraries=work/'library.json';client_objects=work/'client-objects.json';image_file=work/'image.json';app_source=client/'main.chaos';app_source.write_text(CLIENT)
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','TMPDIR','TMP','TEMP','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
            env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',CHAOS_CONFIG=str(work/'settings.json'))
            def command(mode,*args,success=True):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(ROOT),mode,*map(str,args)],cwd=client,env=env,text=True,capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0 if success else 1,(args,result.stdout,result.stderr))
                return json.loads(result.stdout) if success else result
            published=command('publisher','executable','build',source,'--root',publisher,'--library',empty,'--objects');libraries.write_text(json.dumps(published))
            source.unlink();publisher.rmdir();self.assertFalse((client/'lib.chaos').exists())
            objects=command('client','executable','build',app_source,'--root',client,'--library',libraries,'--objects');client_objects.write_text(json.dumps(objects))
            image=command('load','executable','link',libraries,client_objects,'--entry','main.chaos');image_file.write_text(json.dumps(image));app_source.unlink()
            self.assertTrue(command('load','executable','validate',image_file)['valid'])
            self.assertEqual(command('load','executable','run',image_file),[42,43,2]);self.assertEqual(command('sdk',image_file),[42,43,2])
            self.assertEqual(json.loads(libraries.read_text()),published)

    def test_two_legacy_v1_libraries_with_different_std_closures_upgrade_without_source(self):
        a=compile_objects('import "std/math" as M;fn answer(){40};export{answer}',entry='a.chaos')
        b=compile_objects('import "std/context" as C;fn answer(){2};export{answer}',entry='b.chaos')
        original=copy.deepcopy(a+b);self.assertTrue(all(o['schema']=='chaos.module-object/1' for o in original))
        with no_library_parser({'a.chaos','b.chaos','std/math','std/context'}):
            image=compile_executable('import "a.chaos" as A;import "b.chaos" as B;A.answer()+B.answer()',libraries=a+b)
        self.assertEqual(image['schema'],'chaos.executable/2');self.assertTrue(all(o['schema']=='chaos.module-object/2' for o in image['objects']))
        self.assertEqual(evaluate(image),42);self.assertEqual(a+b,original)

    def test_source_path_conflict_rejects_and_library_data_class_are_original_values(self):
        library=self.library('data Choice{Some(value)};class Counter{var n=40;fn next(self){self.n+=1;self.n}};fn make(){Counter()};export{Some,make}')
        for source,files,entry in (('42',None,'lib.chaos'),('import "lib.chaos" as L;42',{'lib.chaos':'999'},'main.chaos')):
            with self.subTest(entry=entry),self.assertRaises(LanguageError) as caught:compile_objects(source,files,entry,libraries=library)
            self.assertEqual(caught.exception.code,'executable_error')
        image=compile_executable('import "lib.chaos" as L;let c=L.make();[match L.Some(42){L.Some(value)=>value},c.next(),c.next()]',libraries=library)
        self.assertEqual(evaluate(image),[42,41,42])

    def test_library_async_function_uses_original_software_robot_handler_once(self):
        import test_robot_values as robots
        from chaos.robots import RobotSpec
        log=[];registry=Registry()
        def factory(ctx):ctx.checkpoint();log.append(('open',{}));return robots.Adapter(log)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:library234'))
        library=self.library('async fn execute(){async with await robot_open("rig") as r{let o=await robot_observe(r);let receipt=await r(robot_command(o,{pan:0,tilt:1}));receipt.status}};export{execute}')
        image=compile_executable('import "lib.chaos" as L;await L.execute()',libraries=library)
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('robot-library',source='999',capabilities=['robot.control'])
            with no_frontend():run=app.start_executable(p['id'],p['version'],image,None);self.assertEqual(run.result(10),'sent')
            self.assertFalse(app._engine.active)
        self.assertEqual([name for name,_ in log],['open','observe','act','close'])
