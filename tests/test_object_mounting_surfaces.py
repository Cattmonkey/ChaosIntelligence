"""Independent SDK/CLI composition of source-free libraries on the original host."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import chaos
from chaos import Framework, Registry, Settings, ToolCall, Final, LanguageError
from chaos.executable_image import compile_objects, compile_executable, link_objects, load_executable, mount_objects
from chaos.language import serializable
from test_executable231 import no_frontend
from test_separate_compilation234 import no_library_parser, evaluate

ROOT = Path(__file__).resolve().parents[1]
MAIN = '''import "std/math" as Math;import "util.chaos" as U;
var calls=0;fn answer(x){calls+=1;Math.abs(U.add(x))};fn count(){calls};
macro step(x){quote{U.plus!(unquote(x))}};export{answer,count,step}'''
UTIL = '''macro hidden(x){quote{unquote(x)+1}};
macro plus(x){quote{hidden!(unquote(x))}};fn add(x){x+1};export{add,plus}'''
CLIENT = '''import "vendor/left/main.chaos" as L;import "vendor/right/main.chaos" as R;
[L.answer(40),R.answer(40),L.step!(40),R.step!(40),L.count(),R.count(),"main.chaos"]'''
EXPECTED = [41,42,41,42,1,1,'main.chaos']


def publish(delta=1):
    return compile_objects(MAIN, {'util.chaos':UTIL.replace('+1','+'+str(delta))}, macro_interfaces=True)



class ObjectMountingSurfaces(unittest.TestCase):
    def test_public_alias_and_Framework_share_pure_transform_and_preserve_inputs(self):
        self.assertIs(chaos.mount_objects, mount_objects)
        original=publish();before=copy.deepcopy(original)
        with no_frontend(), patch('chaos.macros.Expander.expand', side_effect=AssertionError('Mount expanded macros')):
            one=mount_objects(original,'vendor/left');two=Framework.mount_objects(original,'vendor/left')
        self.assertEqual(one,two);self.assertEqual(original,before);self.assertIsNot(one,original)
        old_std={o['module']:o for o in original if o['module'].startswith('std/')}
        self.assertEqual({o['module']:o for o in one if o['module'].startswith('std/')},old_std)
        self.assertEqual({o['module'] for o in one if not o['module'].startswith('std/')},{'vendor/left/main.chaos','vendor/left/util.chaos'})
        for old in original:
            new=next(o for o in one if o['module']==(old['module'] if old['module'].startswith('std/') else 'vendor/left/'+old['module']))
            self.assertEqual(new['sourceHash'],old['sourceHash'])
        with self.assertRaises(LanguageError) as caught:compile_executable('42',libraries=publish(1)+publish(2))
        self.assertEqual(caught.exception.code,'executable_error','The original flat-list conflict contract remains strict')

    def test_same_named_runtime_and_macro_libraries_match_source_composition(self):
        left,right=publish(1),publish(2)
        with no_frontend(),patch('chaos.macros.Expander.expand',side_effect=AssertionError('Mount ran expander')):
            libs=mount_objects(left,'vendor/left')+mount_objects(right,'vendor/right')
        with no_library_parser({'util.chaos','vendor/left/main.chaos','vendor/left/util.chaos','vendor/right/main.chaos','vendor/right/util.chaos'}):
            image=compile_executable(CLIENT,libraries=libs)
        source_files={'vendor/left/main.chaos':MAIN,'vendor/left/util.chaos':UTIL,'vendor/right/main.chaos':MAIN,'vendor/right/util.chaos':UTIL.replace('+1','+2')}
        baseline=serializable(Framework.evaluate(CLIENT,files=source_files))
        self.assertEqual(baseline,EXPECTED);self.assertEqual(evaluate(image),baseline)
        with no_frontend():
            self.assertEqual(Framework.evaluate(Framework.load_executable(Framework.link_objects(image['objects']))),EXPECTED)

    def test_module_shaped_literal_payload_is_not_rewritten_as_provenance(self):
        source='fn text(){["main.chaos",{file:"main.chaos",macro_module:"main.chaos",definitionModule:"main.chaos",sourceHash:"original"}]};export{text}'
        original=compile_objects(source,libraries=[])
        with no_frontend():objects=mount_objects(original,'vendor/data')
        image=compile_executable('import "vendor/data/main.chaos" as L;L.text()',libraries=objects)
        self.assertEqual(evaluate(image),['main.chaos',{'file':'main.chaos','macro_module':'main.chaos','definitionModule':'main.chaos','sourceHash':'original'}])

    def test_module_initialization_once_inside_each_mount_and_independent_Cells(self):
        main='import "a.chaos" as A;import "b.chaos" as B;fn next(){[A.next(),B.next()]};export{next}'
        files={'a.chaos':'import "base.chaos" as B;fn next(){B.next()};export{next}',
               'b.chaos':'import "base.chaos" as B;fn next(){B.next()};export{next}',
               'base.chaos':'tool("init","base.chaos");var n=0;fn next(){n+=1;n};export{next}'}
        library=compile_objects(main,files,libraries=[]);calls=[]
        with no_frontend():libs=mount_objects(library,'vendor/left')+mount_objects(library,'vendor/right')
        image=compile_executable('import "vendor/left/main.chaos" as L;import "vendor/right/main.chaos" as R;[L.next(),R.next(),L.next()]',libraries=libs)
        self.assertEqual(calls,[])
        self.assertEqual(evaluate(image,tool_handler=lambda n,v:calls.append((n,v)) or v),[[1,2],[1,2],[3,4]])
        self.assertEqual(calls,[('init','base.chaos'),('init','base.chaos')])

    def test_definition_site_numerics_conditions_and_keyword_hygiene_survive_mount(self):
        library='''macro number(){syntax_literal(complex(fraction(1,3),1))};
macro situation(){syntax_literal(condition("missing",data={n:decimal("0.10")}))};
macro make(x){quote{let saved=1;fn(value,*,bonus=1){unquote(x)+value+bonus+saved}}};export{number,situation,make}'''
        original=compile_objects(library,macro_interfaces=True)
        with no_frontend():objects=mount_objects(original,'vendor/library')
        source='''import "vendor/library/main.chaos" as L;let expected=complex(fraction(1,3),1);
let fraction=fn(){0};let complex=fn(){0};let decimal=fn(){0};let condition=fn(){0};let saved=40;let value=99;
let c=L.situation!();[L.number!()==expected,c.kind,decimal_text(c.["data"].n),L.make!(saved)(value=0),saved,value]'''
        self.assertEqual(evaluate(compile_executable(source,libraries=objects)),[True,'missing','0.10',42,40,99])

    def test_legacy_mount_upgrades_without_frontend_and_keeps_all_four_flavors_cold(self):
        library=compile_objects('''var hits=[];fn f(x){hits=append(hits,"fn");x+1};async fn a(x){hits=append(hits,"async");await sleep(0);x+2};fn* g(x){hits=append(hits,"gen");yield x;return_values(x+3,x+4)};async fn* ag(x){hits=append(hits,"asyncgen");await sleep(0);yield x;return_values(x+5,x+6)};fn seen(){hits};export{f,a,g,ag,seen}''')
        with no_frontend():objects=mount_objects(library,'vendor/legacy')
        self.assertTrue(all(o['schema']=='chaos.module-object/2' for o in objects))
        source='''import "vendor/legacy/main.chaos" as L;let a=L.a(40);let g=L.g(42);let ag=L.ag(42);let cold=L.seen();let out=[L.f(41),await a,collect(g),await acollect(ag)];[cold,out,multiple_value_list(g),multiple_value_list(ag),L.seen()]'''
        self.assertEqual(evaluate(compile_executable(source,libraries=objects)),[[],[42,42,[42],[42]],[45,46],[47,48],['fn','async','gen','asyncgen']])

    def test_mounted_function_is_real_AgentTool_on_original_persistent_host_once(self):
        calls=[];steps=[];registry=Registry()
        @registry.tool('fixture.mount.audit',effect='write')
        def audit(value,ctx):ctx.checkpoint();calls.append(value);return value
        @registry.provider('fixture.mount')
        def provider(request,ctx):
            ctx.checkpoint();steps.append(request.step)
            return ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])
        library=compile_objects('fn work(x:Int)->Int{tool("fixture.mount.audit",x);x+2};export{work}',libraries=[])
        with no_frontend():objects=mount_objects(library,'vendor/worker')
        source='''import "vendor/worker/main.chaos" as W;agent helper{provider:"fixture.mount",tools:[]};let bound=agent_bind(helper,[agent_tool("work",W.work,input_type="Int",output_type="Int",capabilities=["fixture.mount.audit"])]);let t=bound(input);[await t,await t]'''
        import_statement,body=source.split(';',1)
        rejected_image=compile_executable(import_statement+';try{'+body+'}catch error{error.code}',libraries=objects)
        authorized=source.replace('tools:[]','tools:["fixture.mount.audit"]',1)
        image=compile_executable(authorized,libraries=objects)
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('mounted-agent',source='999',capabilities=['fixture.mount.audit'])
            with no_frontend():
                rejected=app.start_executable(p['id'],p['version'],rejected_image,40)
                self.assertEqual(rejected.result(15),'capability_error')
            self.assertEqual(calls,[]);self.assertEqual(steps,[])
            with no_frontend():run=app.start_executable(p['id'],p['version'],image,40);self.assertEqual(run.result(15),[42,42])
            self.assertEqual(run.get()['snapshot']['execution']['image'],image);self.assertFalse(app._engine.active)
        self.assertEqual(calls,[40]);self.assertEqual(steps,[1,2])

    def test_mounted_async_function_keeps_software_Robot_command_once(self):
        import test_robot_values as robots
        from chaos.robots import RobotSpec
        log=[];registry=Registry()
        def factory(ctx):ctx.checkpoint();log.append(('open',{}));return robots.Adapter(log)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:object-mount'))
        source='async fn execute(){async with await robot_open("rig") as r{let o=await robot_observe(r);let receipt=await r(robot_command(o,{pan:0,tilt:1}));receipt.status}};export{execute}'
        library=compile_objects(source,libraries=[])
        with no_frontend():objects=mount_objects(library,'vendor/robot')
        image=compile_executable('import "vendor/robot/main.chaos" as R;let t=R.execute();[await t,await t]',libraries=objects)
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('mounted-robot',source='999',capabilities=['robot.control'])
            with no_frontend():run=app.start_executable(p['id'],p['version'],image,None);self.assertEqual(run.result(15),['sent','sent'])
            self.assertFalse(app._engine.active)
        self.assertEqual([name for name,_ in log],['open','observe','act','close'])

    def test_fresh_process_CLI_publish_mount_delete_sources_build_link_load_and_run(self):
        bootstrap=r'''import json,runpy,socket,sys
from pathlib import Path
root,mode,*args=sys.argv[1:];sys.path[:0]=[root,str(Path(root)/'tests')]
assert sys.flags.isolated and sys.flags.no_site
def denied(*a,**k):raise AssertionError('Offline mounted-library fixture has no network authority')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied
from contextlib import ExitStack
from unittest.mock import patch
with ExitStack() as stack:
 if mode in ('mount','load','sdk'):
  from test_executable231 import no_frontend
  stack.enter_context(no_frontend())
  stack.enter_context(patch('chaos.macros.Expander.expand',side_effect=AssertionError('Mount/link/load expanded macros')))
 if mode=='client':
  from chaos.language import Parser
  original=Parser.__init__;source=Path(args[2]).read_text()
  def guard(parser,text,filename='main.chaos',**kwargs):
   if not filename.startswith('std/'):
    assert filename=='main.chaos' and text==source,'Client accessed publisher source or a stub'
   return original(parser,text,filename,**kwargs)
  stack.enter_context(patch.object(Parser,'__init__',guard))
 if mode=='sdk':
  from chaos import Framework
  print(json.dumps(Framework.evaluate(Framework.load_executable(json.loads(Path(args[0]).read_text())))))
 else:
  sys.argv=['chaos']+args;runpy.run_module('chaos',run_name='__main__')'''
        with tempfile.TemporaryDirectory(prefix='object-mount-') as directory:
            work=Path(directory);client=work/'client';client.mkdir();published=[];mounted=[]
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','TMPDIR','TMP','TEMP','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
            env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',CHAOS_CONFIG=str(work/'settings.json'))
            def command(mode,*args):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(ROOT),mode,*map(str,args)],cwd=client,env=env,text=True,capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0,(mode,args,result.stdout,result.stderr));return json.loads(result.stdout)
            for name,delta in [('left',1),('right',2)]:
                publisher=work/name;publisher.mkdir();main=publisher/'main.chaos';util=publisher/'util.chaos';main.write_text(MAIN);util.write_text(UTIL.replace('+1','+'+str(delta)))
                rows=command('publisher','executable','build',main,'--root',publisher,'--objects','--macro-interfaces');path=work/(name+'.json');path.write_text(json.dumps(rows));published.append((path,rows))
                main.unlink();util.unlink();publisher.rmdir()
                mounted_path=work/(name+'.mounted.json');mounted_path.write_text(json.dumps(command('mount','executable','mount',path,'--prefix','vendor/'+name)));mounted.append(mounted_path)
            main=client/'main.chaos';main.write_text(CLIENT)
            objects=command('client','executable','build',main,'--root',client,'--objects','--library',mounted[0],'--library',mounted[1]);objects_file=work/'objects.json';objects_file.write_text(json.dumps(objects));main.unlink()
            image=command('load','executable','link',objects_file);image_file=work/'image.json';image_file.write_text(json.dumps(image))
            self.assertTrue(command('load','executable','validate',image_file)['valid'])
            self.assertEqual(command('load','executable','run',image_file),EXPECTED);self.assertEqual(command('sdk',image_file),EXPECTED)
            self.assertEqual(list(client.iterdir()),[])
            for path,rows in published:self.assertEqual(json.loads(path.read_text()),rows)

    def test_CLI_mount_uses_bounded_JSON_and_original_error_channel(self):
        from contextlib import redirect_stderr,redirect_stdout
        import io
        from chaos.executable_cli import main
        with tempfile.TemporaryDirectory(prefix='mount-errors-') as directory:
            path=Path(directory)/'objects.json';path.write_text(json.dumps(publish()));before=path.read_bytes()
            for prefix in ('','../escape','std','std/private'):
                out=io.StringIO();err=io.StringIO()
                with self.subTest(prefix=prefix),redirect_stdout(out),redirect_stderr(err),no_frontend():status=main(['mount',str(path),'--prefix',prefix])
                self.assertEqual(status,1);self.assertEqual(out.getvalue(),'');self.assertIn('error',json.loads(err.getvalue()))
            self.assertEqual(path.read_bytes(),before)
            path.write_text('{"duplicate":1,"duplicate":2}')
            out=io.StringIO();err=io.StringIO()
            with redirect_stdout(out),redirect_stderr(err):status=main(['mount',str(path),'--prefix','vendor/lib'])
            self.assertEqual(status,1);self.assertEqual(out.getvalue(),'');self.assertIn('Duplicate JSON',json.loads(err.getvalue())['error'])
