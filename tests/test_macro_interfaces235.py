"""Source-free macro libraries use the original bounded Expander and evaluator."""
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
from chaos import Evaluator, Framework, Registry, Settings, ToolCall, Final, LanguageError
from chaos.executable_image import compile_objects, compile_executable, link_objects, load_executable
from chaos.language import ResourceLimit, serializable
from test_executable231 import no_frontend
from test_separate_compilation234 import no_library_parser, evaluate, digest

ROOT=Path(__file__).resolve().parents[1]
LIBRARY='''macro hidden(x){quote{unquote(x)+1}};macro twice(x){quote{let temporary=unquote(x);temporary+temporary}};macro answer(x){quote{hidden!(hidden!(unquote(x)))}};export{twice,answer}'''
CLIENT='import "lib.chaos" as L;[L.answer!(40),L.twice!(21)]'
EXPECTED=[42,42]


def publish(source=LIBRARY,name='lib.chaos',files=None):
    return compile_objects(source,files,entry=name,macro_interfaces=True)


def rehash(row):
    row['objectHash']=digest({k:v for k,v in row.items() if k!='objectHash'})
    return row


class MacroInterfaces235(unittest.TestCase):
    def test_explicit_v3_publication_and_automatic_client_selection_preserve_old_defaults(self):
        self.assertEqual(compile_objects('42')[0]['schema'],'chaos.module-object/1')
        self.assertEqual(compile_objects('42',libraries=[])[0]['schema'],'chaos.module-object/2')
        library=publish();before=copy.deepcopy(library)
        with no_library_parser({'lib.chaos'}):image=compile_executable(CLIENT,libraries=library)
        self.assertEqual(image['schema'],'chaos.executable/3');self.assertEqual(image['abi'],'chaos.expanded-ast/3')
        self.assertEqual(evaluate(image),EXPECTED);self.assertEqual(library,before)
        self.assertIs(chaos.compile_objects,compile_objects)
        self.assertEqual(evaluate(Framework.compile_executable(CLIENT,libraries=Framework.compile_objects(LIBRARY,entry='lib.chaos',macro_interfaces=True))),EXPECTED)

    def test_exported_macro_private_helper_and_caller_macro_have_original_defsite_semantics(self):
        source='import "lib.chaos" as L;macro hidden(x){quote{unquote(x)+100}};L.answer!(hidden!(40))'
        baseline=serializable(Framework.evaluate(source,files={'lib.chaos':LIBRARY}))
        with no_library_parser({'lib.chaos'}):image=compile_executable(source,libraries=publish())
        self.assertEqual(baseline,142);self.assertEqual(evaluate(image),baseline)

    def test_hygiene_single_evaluation_keyword_labels_and_pure_closure_composition(self):
        source='''macro make(x){quote{let saved=1;fn(value,*,bonus=1){unquote(x)+value+bonus+saved}}};macro addTwo(value){fn compose(f:Function,g:Function)->Function{fn(x){f(g(x))}};fn addOne(x){quote{unquote(x)+1}};let composed=compose(addOne,addOne);composed(value)};macro twice(x){quote{let temporary=unquote(x);temporary+temporary}};export{make,addTwo,twice}'''
        client='''import "lib.chaos" as L;let saved=40;let value=99;let temporary=100;[L.make!(saved)(value=0),L.addTwo!(40),L.twice!(tool("probe",21)),temporary]'''
        calls=[];image=compile_executable(client,libraries=publish(source))
        self.assertEqual(evaluate(image,tool_handler=lambda n,v:calls.append((n,v)) or v),[42,42,42,100]);self.assertEqual(calls,[('probe',21)])

    def test_transitive_imported_macro_alias_uses_definition_module_after_source_deletion(self):
        base='macro inc(x){quote{unquote(x)+1}};export{inc}'
        library='import "base.chaos" as H;macro outer(x){quote{H.inc!(unquote(x))}};export{outer}'
        client='import "lib.chaos" as L;macro inc(x){quote{unquote(x)+100}};do{let H={inc:0};let L=0;L.outer!(inc!(40))}'
        objects=publish(library,files={'base.chaos':base})
        with no_library_parser({'lib.chaos','base.chaos'}):image=compile_executable(client,libraries=objects)
        self.assertEqual(evaluate(image),141)

    def test_defsite_numeric_and_condition_literals_survive_caller_shadowing(self):
        library='''macro number(){syntax_literal(complex(fraction(1,3),1))};macro situation(){syntax_literal(condition("missing",data={n:decimal("0.10")}))};export{number,situation}'''
        client='''import "lib.chaos" as L;let expected=complex(fraction(1,3),1);let fraction=fn(){0};let complex=fn(){0};let decimal=fn(){0};let condition=fn(){0};let c=L.situation!();[L.number!()==expected,c.kind,decimal_text(c.["data"].n)]'''
        self.assertEqual(evaluate(compile_executable(client,libraries=publish(library))),[True,'missing','0.10'])

    def test_client_macros_are_republishable_when_v3_was_selected_by_input(self):
        middle='import "lib.chaos" as L;macro forwarded(x){quote{L.answer!(unquote(x))}};export{forwarded}'
        objects=compile_objects(middle,entry='middle.chaos',libraries=publish())
        row=next(o for o in objects if o['module']=='middle.chaos');self.assertIsNotNone(row['phaseCapsule'])
        with no_library_parser({'lib.chaos','middle.chaos'}):image=compile_executable('import "middle.chaos" as M;M.forwarded!(40)',libraries=objects)
        self.assertEqual(evaluate(image),42)

    def test_unused_unsupported_pure_macro_publishes_but_invocation_keeps_original_error(self):
        library='macro unused(){tool("never",null)};export{unused}'
        self.assertEqual(Framework.evaluate(library+';42'),42)
        objects=publish(library);self.assertEqual(evaluate(compile_executable('import "lib.chaos" as L;42',libraries=objects)),42)
        records=[]
        for action in (lambda:Framework.evaluate('import "lib.chaos" as L;L.unused!()',files={'lib.chaos':library}),lambda:compile_executable('import "lib.chaos" as L;L.unused!()',libraries=objects)):
            with self.assertRaises(LanguageError) as caught:action()
            records.append((caught.exception.code,caught.exception.message))
        self.assertEqual(records[0],records[1]);self.assertEqual(records[0][0],'macro_error')

    def test_private_runtime_and_legacy_macro_names_do_not_gain_phase_access(self):
        library='macro hidden(x){quote{unquote(x)}};fn normal(x){x};macro visible(x){quote{hidden!(unquote(x))}};export{normal,visible}'
        for name in ('hidden','normal'):
            with self.subTest(name=name),self.assertRaises(LanguageError) as caught:compile_executable('import "lib.chaos" as L;L.'+name+'!(42)',libraries=publish(library))
            self.assertEqual(caught.exception.code,'macro_error')
        for objects in (compile_objects(LIBRARY,entry='lib.chaos'),compile_objects(LIBRARY,entry='lib.chaos',libraries=[])):
            with self.assertRaises(LanguageError) as caught:compile_executable(CLIENT,libraries=objects)
            self.assertEqual(caught.exception.code,'macro_error')

    def test_recursive_external_macro_uses_original_resource_budget(self):
        library=publish('macro forever(){quote{forever!()}};export{forever}')
        with self.assertRaises(ResourceLimit):compile_executable('import "lib.chaos" as L;L.forever!()',libraries=library)

    def test_link_load_do_not_expand_or_execute_published_macro_bodies(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('Construction executed user code')):
            objects=compile_objects('import "lib.chaos" as L;L.answer!(40)',libraries=publish())
            with no_frontend(),patch('chaos.macros.Expander.pure',side_effect=AssertionError('Link/load executed macro')):
                image=link_objects(objects);bundle=load_executable(json.loads(json.dumps(image)))
        self.assertEqual(bundle.summary()['entry'],'main.chaos');self.assertEqual(evaluate(image),42)

    def test_malformed_rehashed_phase_capsules_reject_before_client_frontend(self):
        for mode in ('schema','authority','runtime-tree','unknown-kind','missing-definition'):
            bad=copy.deepcopy(publish());obj=next(o for o in bad if o['module']=='lib.chaos');cap=obj['phaseCapsule']
            if mode=='schema':cap['schema']='chaos.macro-phase/999'
            elif mode=='authority':cap['owner']='host'
            elif mode=='runtime-tree':cap['definitions']=obj['tree']
            elif mode=='unknown-kind':cap['definitions']=['native-owner','host']
            else:obj['macroExports'].append('absent')
            rehash(obj)
            with self.subTest(mode=mode),patch('chaos.language.Parser.program',side_effect=AssertionError('Invalid phase entered parser')),self.assertRaises(LanguageError) as caught:compile_objects('42',libraries=bad)
            self.assertEqual(caught.exception.code,'executable_error')
        from chaos.language import Node
        from chaos.executable_ast import encode_tree
        literal=('literal',1)
        malformed=(
            ('unquote-outside',('unquote',literal),['x']),
            ('quote-nonblock',('quote',literal),['x']),
            ('duplicate-parameters',('quote',('block',[literal])),['x','x']),
            ('declaration-operand',('binary','+',('let','fresh','Any',literal),literal),['x']),
            ('macro-keyword',('macrocall','callee',[('keyword','x',literal)]),['x']),
            ('macro-spread',('macrocall','callee',[('spread',('list',[literal]))]),['x']),
        )
        for mode,body,parameters in malformed:
            bad=copy.deepcopy(publish());obj=next(o for o in bad if o['module']=='lib.chaos')
            valid=('quote',('block',[literal]))
            raw=Node(('block',[
                ('macro','hidden',['x'],('block',[valid])),
                ('macro','twice',['x'],('block',[valid])),
                ('macro','answer',parameters,('block',[body])),
            ]))
            self.assertEqual(set(obj['macroExports']),{'twice','answer'})
            obj['phaseCapsule']['definitions']=encode_tree(raw,frozenset(o['module'] for o in bad))
            rehash(obj)
            with self.subTest(context=mode),patch('chaos.language.Parser.program',side_effect=AssertionError('Malformed phase entered parser')),self.assertRaises(LanguageError) as caught:
                compile_objects('42',libraries=bad)
            self.assertEqual(caught.exception.code,'executable_error')

    def test_missing_transitive_phase_dependency_and_stale_macro_hash_cannot_link(self):
        source='import "base.chaos" as B;macro outer(x){quote{B.inc!(unquote(x))}};export{outer}'
        library=publish(source,files={'base.chaos':'macro inc(x){quote{unquote(x)+1}};export{inc}'})
        objects=compile_objects('import "lib.chaos" as L;L.outer!(41)',libraries=library)
        for bad in ([o for o in objects if o['module']!='base.chaos'],[o for o in objects if o['module']!='lib.chaos']+publish(source.replace('B.inc!(unquote(x))','B.inc!(B.inc!(unquote(x)))'),files={'base.chaos':'macro inc(x){quote{unquote(x)+1}};export{inc}'})):
            with no_frontend(),self.assertRaises(LanguageError) as caught:link_objects(bad)
            self.assertEqual(caught.exception.code,'executable_error')

    def test_mixed_legacy_shared_standard_closures_normalize_without_invented_macros(self):
        modern=publish('import "std/math" as M;macro answer(x){quote{unquote(x)+1}};export{answer}',name='macros.chaos')
        for legacy in (compile_objects('import "std/math" as M;fn two(){2};export{two}',entry='old.chaos'),compile_objects('import "std/math" as M;fn two(){2};export{two}',entry='old.chaos',libraries=[])):
            before=copy.deepcopy(modern+legacy)
            with no_library_parser({'macros.chaos','old.chaos','std/math'}):image=compile_executable('import "macros.chaos" as M;import "old.chaos" as O;M.answer!(40)+O.two()',libraries=modern+legacy)
            self.assertEqual(evaluate(image),43);self.assertEqual(modern+legacy,before)
            self.assertIsNone(next(o for o in image['objects'] if o['module']=='std/math')['phaseCapsule'])
        with self.assertRaises(LanguageError):compile_objects('42',libraries=publish()+compile_objects(LIBRARY,entry='lib.chaos',libraries=[]))

    def test_macro_emitted_four_flavors_are_cold_and_keep_multiple_values(self):
        library='''macro define(adding,waiting,returning,collecting,acollecting,values){quote{var hits=[];fn f(x){hits=unquote(adding)(hits,"fn");x+1};async fn a(x){hits=unquote(adding)(hits,"async");await unquote(waiting)(0);x+2};fn* g(x){hits=unquote(adding)(hits,"gen");yield x;unquote(returning)(x+3,x+4)};async fn* ag(x){hits=unquote(adding)(hits,"asyncgen");yield x;unquote(returning)(x+5,x+6)};let a0=a(40);let g0=g(42);let ag0=ag(42);let cold=hits;[cold,[f(41),await a0,unquote(collecting)(g0),await unquote(acollecting)(ag0)],unquote(values)(g0),unquote(values)(ag0),hits]}};export{define}'''
        self.assertEqual(evaluate(compile_executable('import "lib.chaos" as L;L.define!(append,sleep,return_values,collect,acollect,multiple_value_list)',libraries=publish(library))),[[],[42,42,[42],[42]],[45,46],[47,48],['fn','async','gen','asyncgen']])

    def test_macro_generated_agent_uses_original_persistent_host_once(self):
        calls=[];steps=[];registry=Registry()
        @registry.tool('fixture.macro235.echo',effect='write')
        def echo(value,ctx):ctx.checkpoint();calls.append(value);return value+2
        @registry.provider('fixture.macro235')
        def provider(request,ctx):
            ctx.checkpoint();steps.append(request.step)
            return ToolCall('fixture.macro235.echo',request.input) if request.step==1 else Final(request.history[-1]['output'])
        source='macro execute(x){quote{agent worker{provider:"fixture.macro235",tools:["fixture.macro235.echo"]};worker(unquote(x))}};export{execute}'
        image=compile_executable('import "lib.chaos" as L;L.execute!(input)',libraries=publish(source));self.assertEqual(calls,[]);self.assertEqual(steps,[])
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('macro-host',source='999',capabilities=['fixture.macro235.echo'])
            with no_frontend():run=app.start_executable(p['id'],p['version'],image,40);self.assertEqual(run.result(10),42)
            self.assertFalse(app._engine.active)
        self.assertEqual(calls,[40]);self.assertEqual(steps,[1,2])

    def test_macro_generated_async_resource_uses_software_robot_once(self):
        import test_robot_values as robots
        from chaos.robots import RobotSpec
        log=[];registry=Registry()
        def factory(ctx):ctx.checkpoint();log.append(('open',{}));return robots.Adapter(log)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:macro235'))
        source='''macro operate(opening,observing,commanding){quote{async with await unquote(opening)("rig") as r{let o=await unquote(observing)(r);let receipt=await r(unquote(commanding)(o,{pan:0,tilt:1}));receipt.status}}};export{operate}'''
        image=compile_executable('import "lib.chaos" as L;L.operate!(robot_open,robot_observe,robot_command)',libraries=publish(source));self.assertEqual(log,[])
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('macro-robot',source='999',capabilities=['robot.control'])
            with no_frontend():run=app.start_executable(p['id'],p['version'],image,None);self.assertEqual(run.result(10),'sent')
            self.assertFalse(app._engine.active)
        self.assertEqual([name for name,_ in log],['open','observe','act','close'])

    def test_real_HTTP_and_OpenAPI_admit_only_matched_v1_v2_v3_images(self):
        from test_executable_surfaces232 import ExecutableHTTP232
        from chaos.client import ValidationError
        host=ExecutableHTTP232('test_OpenAPI_executable_admission_is_documented_without_new_authority_field')
        try:
            host.setUp();image=compile_executable('import "lib.chaos" as L;L.answer!(input)',libraries=publish())
            schema=host.client._request('GET','/openapi.json')['components']['schemas']['ExecutableImage']
            pairs={(r['properties']['schema']['const'],r['properties']['abi']['const']) for r in schema['oneOf']}
            self.assertEqual(pairs,{('chaos.executable/'+str(i),'chaos.expanded-ast/'+str(i)) for i in (1,2,3)})
            p=host.project
            with no_frontend():run=host.client.start_executable(p['id'],p['version'],image,40);self.assertEqual(run.result(10),42)
            before=host.app.runs(p['id']);bad=copy.deepcopy(image);bad['abi']='chaos.expanded-ast/2';bad['imageHash']=digest({k:v for k,v in bad.items() if k!='imageHash'})
            with self.assertRaises(ValidationError):host.client.start_executable(p['id'],p['version'],bad,40)
            self.assertEqual(host.app.runs(p['id']),before);self.assertEqual(host.effects,[])
        finally:host.doCleanups()

    def test_unused_async_comprehension_quote_defers_insertion_scope_to_original_expander(self):
        library='macro unused(xs){quote{[x async for x in unquote(xs)]}};macro stop(){quote{break}};macro returner(){quote{return 1}};export{unused,stop,returner}'
        self.assertEqual(Framework.evaluate(library+';42'),42)
        objects=publish(library)
        self.assertEqual(evaluate(compile_executable('import "lib.chaos" as L;42',libraries=objects)),42)
        records=[]
        source='import "lib.chaos" as L;fn sync(){L.unused!([1])};42'
        for action in (lambda:Framework.evaluate(source,files={'lib.chaos':library}),lambda:compile_executable(source,libraries=objects)):
            with self.assertRaises(LanguageError) as caught:action()
            records.append((caught.exception.code,caught.exception.message))
        self.assertEqual(records[0],records[1]);self.assertEqual(records[0][0],'syntax_error')

    def test_raw_phase_cannot_forge_definition_site_macro_module_metadata(self):
        bad=copy.deepcopy(publish());obj=next(o for o in bad if o['module']=='lib.chaos')
        encoded=obj['phaseCapsule']['definitions']
        self.assertEqual(encoded[0],'node');encoded[2]['macro_module']='lib.chaos';rehash(obj)
        with patch('chaos.language.Parser.program',side_effect=AssertionError('Invalid raw phase entered frontend')),self.assertRaises(LanguageError) as caught:
            compile_objects('42',libraries=bad)
        self.assertEqual(caught.exception.code,'executable_error')

    def test_fresh_three_process_publish_client_link_run_without_any_library_source(self):
        bootstrap=r'''import json,runpy,socket,sys
from pathlib import Path
root=Path(sys.argv[1]);mode=sys.argv[2];args=sys.argv[3:];sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
def denied(*a,**k):raise AssertionError('Macro fixture forbids network')
socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
from chaos.language import Parser
original=Parser.program
def guard(parser,*a,**k):
 if parser.filename=='lib.chaos':raise AssertionError('Publisher source parsed in client')
 return original(parser,*a,**k)
if mode=='client':Parser.program=guard
if mode=='load':
 from test_executable231 import no_frontend
 context=no_frontend();context.__enter__()
sys.argv=['chaos']+args;runpy.run_module('chaos',run_name='__main__')'''
        with tempfile.TemporaryDirectory(prefix='macro235-') as directory:
            work=Path(directory);publisher=work/'publisher';client=work/'client';publisher.mkdir();client.mkdir()
            source=publisher/'lib.chaos';source.write_text(LIBRARY);app=client/'main.chaos';app.write_text(CLIENT)
            library=work/'library.json';objects_file=work/'objects.json';image_file=work/'image.json'
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','TMPDIR','TMP','TEMP','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
            env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',CHAOS_CONFIG=str(work/'settings.json'))
            def command(mode,*args):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(ROOT),mode,*map(str,args)],cwd=client,env=env,text=True,capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0,(args,result.stdout,result.stderr));return json.loads(result.stdout)
            published=command('publisher','executable','build',source,'--root',publisher,'--objects','--macro-interfaces');library.write_text(json.dumps(published));source.unlink();publisher.rmdir()
            objects=command('client','executable','build',app,'--root',client,'--library',library,'--objects');objects_file.write_text(json.dumps(objects));app.unlink()
            image=command('load','executable','link',library,objects_file);image_file.write_text(json.dumps(image))
            self.assertTrue(command('load','executable','validate',image_file)['valid']);self.assertEqual(command('load','executable','run',image_file),EXPECTED)
            self.assertEqual(json.loads(library.read_text()),published)
