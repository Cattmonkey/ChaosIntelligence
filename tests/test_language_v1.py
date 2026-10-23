"""Independent language acceptance probes; proposed outside the repository.

These tests exercise user-visible semantics, rather than mirroring implementation.
Run with the candidate checkout on PYTHONPATH.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import chaos
from chaos import Framework, Registry, RunFailed
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.modules import ProgramBundle, load_file_bundle
from chaos.program import (parse_program, format_program, decode_program, plain,
                           patch_program, project_program)
from chaos.session import Session


class LanguageCoreAcceptance(unittest.TestCase):
    def run_source(self, source, **kwargs):
        return Evaluator(step_limit=150000, **kwargs).run(source)

    def test_values_control_flow_and_lexical_capture(self):
        cases = [
            ('var x=0; while x<5 {x=x+1; if x==3 {continue}else{null}; if x==4 {break}else{null}}; x', 4),
            ('var xs=[];for x in [1,2,3]{xs=append(xs,fn(){x})};map(fn(f){f()},xs)', [1,2,3]),
            ('var x=1;fn inc(){x=x+1};inc();x', 2),
            ('var x=1;let y=x;fn f(){x=x+1};f();[x,y]', [2,1]),
            ('fn f(){while true{try{return 42}catch e{0}}};f()', 42),
            ('for x in []{throw 1}', None),
            ('var a=[];for c in "中🙂"{a=append(a,c)};a', ['中','🙂']),
            ('var a=0;for x in [1,2]{for y in [1,2]{a=a+1;break}};a', 2),
            ('fn f(){let g=fn(){return 1};g();return 2};f()', 2),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                actual = self.run_source(source)
                self.assertEqual(actual, expected)
                self.assertIs(type(actual), type(expected))

    def test_mutable_contract_failure_does_not_write_cell(self):
        self.assertEqual(self.run_source('var x:Int=1;try{x="wrong"}catch e{null};x'), 1)
        with self.assertRaises(LanguageError):
            self.run_source('var x=1;fn f(){let x=2;x=3};f()')

    def test_control_boundaries_are_checked_before_effects(self):
        invalid = [
            'while true{fn bad(){break};bad()}',
            'while true{fn bad(){continue};bad()}',
            'if false{return 1}else{null}',
            'for x in [1]{x=2}',
            'for x in {a:1}{x}',
            'while 1{break}',
        ]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(LanguageError):
                self.run_source(source)
        calls=[]
        with self.assertRaises(LanguageError):
            self.run_source('tool("write",1);while true{fn bad(){break};bad()}',
                            tool_handler=lambda *args:calls.append(args))
        self.assertEqual(calls, [])

    def test_return_and_mutual_tail_calls(self):
        sources = [
            ('fn f(n){if n==0{return 42}else{return f(n-1)}};f(2000)',42),
            ('rec{fn even(n)->Bool{if n==0{true}else{odd(n-1)}};fn odd(n)->Bool{if n==0{false}else{even(n-1)}}};even(2000)',True),
            ('fn f(n){match n {0=>42,_=>f(n-1)}};f(2000)',42),
        ]
        for source, expected in sources:
            with self.subTest(source=source): self.assertEqual(self.run_source(source),expected)
        for source in ['fn f()->String{return 1};f()',
                       'rec{fn f()->String{g()};fn g(){1}};f()']:
            with self.subTest(source=source), self.assertRaises(LanguageError): self.run_source(source)

    def test_rec_scope_and_atomic_declaration(self):
        self.assertEqual(self.run_source('let x=1;rec{fn a(){x};fn b(){a()}};do{let x=2;b()}'),1)
        for source in ['rec{fn a(){1};fn a(){2}};a()',
                       'let a=1;rec{fn a(){2};fn b(){a()}};b()',
                       'fn f(){g()};fn g(){42};f()']:
            with self.subTest(source=source), self.assertRaises(LanguageError): self.run_source(source)

    def test_match_structure_and_binding_scope(self):
        cases=[
            ('match true {1=>"int",true=>"bool"}','bool'),
            ('match {x:1,y:2} {{x:a,..r}=>[a,r],_=>null}',[1,{'y':2}]),
            ('match [1,2,3] {[h,..t]=>[h,t],_=>null}',[1,[2,3]]),
            ('let f=match [42]{[x]=>fn(){x}};f()',42),
            ('match {x:1,y:2}{{x:a}=>false,_=>true}',True),
        ]
        for source,expected in cases:
            with self.subTest(source=source):self.assertEqual(self.run_source(source),expected)
        for source in ['match [1,2]{[x,x]=>x}', 'match 1{x if 1=>x}',
                       'match [1]{[x]=>x};x']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_source(source)

    def test_match_guards_and_iterations_preserve_effect_order(self):
        calls=[]
        def tool(name,value):
            calls.append([name,value])
            return [1,2] if name=='items' else value
        source='var n=0;for x in tool("items",null){n=n+1};match 42{x if false=>tool("bad",x),x=>tool("ok",x)}'
        self.assertEqual(self.run_source(source,tool_handler=tool),42)
        self.assertEqual(calls,[['items',None],['ok',42]])
        self.assertEqual(self.run_source('var n=0;fn condition(){n=n+1;n<3};while condition(){null};n'),3)

    def test_catch_values_and_host_arithmetic(self):
        cases=[
            ('try{throw false}catch e{e.value}',False),
            ('try{throw null}catch e{e.value}',None),
            ('try{throw 0}catch e{e.value}',0),
            ('try{1/0}catch e{e.code}','ZeroDivisionError'),
            ('try{match 2{1=>1}}catch e{e.code}','match_error'),
            ('try{try{throw 1}catch e{throw e.value+1}}catch e{e.value}',2),
            ('fn bad(){throw 1};fn f(){try{return bad()}catch e{return 42}};f()',42),
        ]
        for source,expected in cases:
            with self.subTest(source=source):self.assertEqual(self.run_source(source),expected)

    def test_try_tail_result_contracts_are_not_lost(self):
        for source in [
            'fn one(){1};fn f(){try{true&&one()}catch e{false}};f()',
            'fn one(){1};fn f(){try{return true&&one()}catch e{return false}};f()',
            'fn one(){1};try{false||one()}catch e{false}',
        ]:
            with self.subTest(source=source):self.assertIs(self.run_source(source),False)

    def test_catch_does_not_swallow_limits_or_cancellation(self):
        with self.assertRaises(ResourceLimit):
            Evaluator(step_limit=200).run('try{while true{}}catch e{42}')
        with self.assertRaises(ResourceLimit):
            Evaluator(cancelled=lambda:True).run('try{42}catch e{0}')
        with self.assertRaises(ResourceLimit):
            Evaluator(timeout=-1).run('try{42}catch e{0}')
        with self.assertRaises(ResourceLimit):
            self.run_source('try{range(0,pow(2,100))}catch e{42}')
        source='try{str({'+','.join('k'+str(i)+':0' for i in range(10001))+'})}catch e{42}'
        with self.assertRaises(ResourceLimit): self.run_source(source)

    def test_std_json_stringify_is_json_for_scalars_and_containers(self):
        for value in ['hello\n"中"', False, None, 0, [1,{'key':'value'}]]:
            with self.subTest(value=value):
                encoded=Evaluator().run('import "std/json" as J;J.stringify(input)',{'input':value})
                self.assertEqual(json.loads(encoded),value)
                self.assertEqual(Evaluator().run('import "std/json" as J;J.parse(J.stringify(input))',{'input':value}),value)
        self.assertEqual(self.run_source('str("plain")'),'plain')

    def test_standard_library_type_contracts(self):
        for source in ['remove({},1)','remove({x:1},1)','get({x:1},1)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_source(source)
        self.assertEqual(self.run_source('import "std/list" as L;L.reverse(L.range(0,5,2))'),[4,2,0])
        self.assertEqual(self.run_source('import "std/option" as O;match O.map(fn(x){x+1},O.Some(2)){O.Some(x)=>x}'),3)
        self.assertEqual(self.run_source('import "std/result" as R;match R.map(fn(x){x+1},R.Err("x")){R.Err(e)=>e}'),'x')

    def test_datatype_declaration_identity_restrictions(self):
        for source in ['do{data X{A};A()}', 'data Int{A};A()']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_source(source)

    def test_module_diagnostics_point_to_imported_file(self):
        with self.assertRaises(LanguageError) as captured:
            Evaluator().run('import "lib.chaos" as L;L.bad()',files={'lib.chaos':'fn bad(){\n  1 / 0\n};export{bad}'})
        self.assertEqual(captured.exception.file,'lib.chaos')
        self.assertEqual(captured.exception.line,2)


class ModuleAndSessionAcceptance(unittest.TestCase):
    def test_diamond_initializes_once_and_aliases_share_module_cell(self):
        calls=[]
        files={
            'a.chaos':'import "base.chaos" as B;let value=B.value;export{value}',
            'b.chaos':'import "base.chaos" as B;let value=B.value;export{value}',
            'base.chaos':'let value=tool("once",null);export{value}',
        }
        got=Evaluator(tool_handler=lambda *args:(calls.append(args),7)[1]).run('import "a.chaos" as A;import "b.chaos" as B;[A.value,B.value]',files=files)
        self.assertEqual(got,[7,7]);self.assertEqual(len(calls),1)
        self.assertEqual(Evaluator().run('import "./counter.chaos" as A;import "counter.chaos" as B;A.inc();B.get()',files={'counter.chaos':'var n=0;fn inc(){n=n+1};fn get(){n};export{inc,get}'}),1)

    def test_private_exports_and_relative_modules(self):
        files={'dir/a.chaos':'import "../base.chaos" as B;fn f(){B.secret};export{f}',
               'base.chaos':'let secret=42;export{secret}'}
        self.assertEqual(Evaluator().run('import "dir/a.chaos" as A;A.f()',files=files),42)
        with self.assertRaises(LanguageError):Evaluator().run('import "dir/a.chaos" as A;A.B',files=files)

    def test_invalid_bundles_fail_before_effects(self):
        invalid=[
            ('import "../a.chaos" as A;A',{}),
            ('import "/a.chaos" as A;A',{}),
            ('import "std/unknown" as A;A',{}),
            ('import "a.chaos" as A;A',{'a.chaos':'import "main.chaos" as M;export{M}'}),
            ('import "a.chaos" as A;A',{'a.chaos':'var x=1;export{x}'}),
            ('import "a.chaos" as A;A',{'a.chaos':'export{missing}'}),
            ('42',{'unused.chaos':'let x=@'}),
            ('42',{'std/list':'42'}),
        ]
        for source,files in invalid:
            calls=[]
            with self.subTest(source=source,files=files),self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run('tool("write",1);'+source,files=files)
            self.assertEqual(calls,[])

    def test_qualified_patterns_contracts_and_cross_module_identity(self):
        files={'a.chaos':'data X{Item(value)};export{X,Item}',
               'b.chaos':'data X{Item(value)};export{X,Item}'}
        self.assertEqual(Evaluator().run('import "a.chaos" as A;fn f(x:A.X)->A.X{x};match f(A.Item(42)){A.Item(v)=>v}',files=files),42)
        with self.assertRaises(LanguageError):Evaluator().run('import "a.chaos" as A;import "b.chaos" as B;fn f(x:A.X){x};f(B.Item(42))',files=files)

    def test_filesystem_adapter_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'pkg').mkdir();(root/'outside.chaos').write_text('42')
            (root/'pkg'/'main.chaos').write_text('import "outside.chaos" as O;O')
            (root/'pkg'/'outside.chaos').symlink_to(root/'outside.chaos')
            with self.assertRaises(LanguageError):load_file_bundle(root/'pkg'/'main.chaos')

    def test_session_cells_closures_and_failed_assignment(self):
        session=Session()
        session.evaluate('var x:Int=1;let initial=x;fn inc(){x=x+1}')
        session.evaluate('inc()');self.assertEqual(session.evaluate('[x,initial]'),[2,1])
        with self.assertRaises(LanguageError):session.evaluate('x="bad"')
        self.assertEqual(session.evaluate('x'),2)
        session.evaluate('let read=fn(){x}');session.evaluate('inc()')
        self.assertEqual(session.evaluate('read()'),3)
        with self.assertRaises(LanguageError):session.evaluate('let initial=99')
        self.assertEqual(session.bindings()['initial'],1)

    def test_session_fresh_budget_and_persistent_module_cache(self):
        calls=[]
        session=Session(step_limit=100,files={'lib.chaos':'let answer=tool("once",null);export{answer}'},tool_handler=lambda *args:(calls.append(args),42)[1])
        session.evaluate('import "lib.chaos" as A')
        session.evaluate('import "./lib.chaos" as B')
        self.assertEqual(session.evaluate('[A.answer,B.answer]'),[42,42]);self.assertEqual(len(calls),1)
        with self.assertRaises(ResourceLimit):session.evaluate('while true{}')
        self.assertEqual(session.evaluate('42'),42)


class ProgramIRAcceptance(unittest.TestCase):
    def test_roundtrip_preserves_control_flow_and_effect_order(self):
        sources=[
            '-3 + 2 * 4',
            'try{(throw 1)+2}catch e{e.value}',
            'var n=0;while n<3{n=n+1};n',
            'var xs=[];for x in [1,2]{xs=append(xs,x)};xs',
            'fn f(n){if n==0{return 42}else{return f(n-1)}};f(5)',
            'data Option{None,Some(value)};match Some(3){Some(x) if x>0=>x,None()=>0}',
            'rec{fn a(n){if n==0{42}else{b(n-1)}};fn b(n){a(n)}};a(3)',
            'tool("first",1);try{tool("second",2);throw 3}catch e{tool("third",e.value)}',
        ]
        for source in sources:
            with self.subTest(source=source):
                ir=parse_program(source);formatted=format_program(ir)
                self.assertEqual(plain(decode_program(ir)),plain(decode_program(parse_program(formatted))))
                records=[]
                for text in (source,formatted):
                    calls=[]
                    result=Evaluator(tool_handler=lambda name,value:(calls.append([name,value]),value)[1]).run(text)
                    records.append((result,calls))
                self.assertEqual(records[0],records[1])

    def test_negative_literal_patch_roundtrip(self):
        original=parse_program('1');node=original['root']['statements'][0]
        updated=patch_program(original,[{'op':'set','id':node['id'],'field':'value','value':-42}])
        self.assertEqual(Evaluator().run(format_program(updated)),-42)
        self.assertEqual(original['root']['statements'][0]['value'],1)
        self.assertEqual(updated['root']['statements'][0]['id'],node['id'])

    def test_patch_failure_is_atomic(self):
        original=parse_program('let x=1;x+2');before=copy.deepcopy(original)
        target=original['root']['statements'][0]['value']['id']
        with self.assertRaises(LanguageError):
            patch_program(original,[{'op':'set','id':target,'field':'value','value':42},
                                    {'op':'remove','id':'missing_node'}])
        self.assertEqual(original,before)

    def test_invalid_ir_cannot_change_grammar_or_duplicate_identity(self):
        original=parse_program('1+2')
        invalid=copy.deepcopy(original)
        invalid['root']['statements'][0]['right']['id']=invalid['root']['statements'][0]['left']['id']
        with self.assertRaises(LanguageError):format_program(invalid)
        with self.assertRaises(LanguageError):patch_program(original,[{'op':'set','id':original['root']['statements'][0]['id'],'field':'operator','value':'@'}])
        bad_break=parse_program('while true{break}')['root']['statements'][0]['body']['statements'][0]
        with self.assertRaises(LanguageError):patch_program(parse_program('null'),[{'op':'append','id':parse_program('null')['root']['id'],'field':'statements','node':bad_break}])

    def test_layout_has_no_semantic_effect(self):
        ir=parse_program('fn f(x){x+1};f(41)');before=project_program(ir)['semanticHash']
        ir['layout']={ir['root']['id']:{'x':123,'y':456}}
        self.assertEqual(project_program(ir)['semanticHash'],before)
        self.assertEqual(Evaluator().run(format_program(ir)),42)


class FrameworkLanguageAcceptance(unittest.TestCase):
    def test_module_snapshot_is_immutable_during_active_run(self):
        registry=Registry();entered=threading.Event();release=threading.Event()
        @registry.tool('test.pause',effect='read')
        def pause(value,context):
            entered.set()
            if not release.wait(3):raise RuntimeError('test release did not arrive')
            context.checkpoint();return None
        with Framework(registry=registry) as app:
            try:
                project=app.create_project('snapshot',source='tool("test.pause",null);import "lib.chaos" as L;L.value',files={'lib.chaos':'let value=1;export{value}'},capabilities=['test.pause'])
                first=app.start('snapshot',project['version'],None,mode='source');self.assertTrue(entered.wait(1))
                updated=app.edit_program('snapshot',project['version'],file='lib.chaos',source='let value=2;export{value}')
                release.set()
                self.assertEqual(first.result(),1)
                self.assertEqual(first.get()['snapshot']['files']['lib.chaos'],'let value=1;export{value}')
                self.assertEqual(app.start('snapshot',updated['version'],None,mode='source').result(),2)
            finally:release.set()

    def test_imported_tool_cannot_add_project_capability(self):
        registry=Registry();calls=[]
        @registry.tool('test.secret')
        def secret(value,context):calls.append(value);return 42
        with Framework(registry=registry) as app:
            project=app.create_project('deny',source='import "lib.chaos" as L;L.read()',files={'lib.chaos':'fn read(){tool("test.secret",null)};export{read}'},capabilities=[])
            with self.assertRaises(RunFailed):app.start('deny',project['version'],None,mode='source').result()
            self.assertEqual(calls,[])

    def test_ir_saved_identity_supports_consecutive_edits(self):
        with Framework() as app:
            project=app.create_project('edit',source='41')
            node_id=project['program']['root']['statements'][0]['id']
            project=app.edit_program('edit',project['version'],operations=[{'op':'set','id':node_id,'field':'value','value':42}])
            self.assertEqual(app.program('edit')['program']['root']['statements'][0]['id'],node_id)
            project=app.edit_program('edit',project['version'],operations=[{'op':'set','id':node_id,'field':'value','value':43}])
            self.assertEqual(app.start('edit',project['version'],None,mode='source').result(),43)

    def test_rejected_module_edit_preserves_snapshot_version_and_events(self):
        with Framework() as app:
            project=app.create_project('rollback',source='import "lib.chaos" as L;L.x',files={'lib.chaos':'let x=42;export{x}'})
            events=app.events('rollback')['items']
            with self.assertRaises(LanguageError):app.edit_program('rollback',project['version'],file='lib.chaos',source='let x=@')
            self.assertEqual(app.get_project('rollback'),project)
            self.assertEqual(app.events('rollback')['items'],events)

    def test_cli_multifile_and_repl_without_server(self):
        env=dict(os.environ);package_root=str(Path(chaos.__file__).resolve().parent.parent)
        env['PYTHONPATH']=package_root+os.pathsep+env.get('PYTHONPATH','')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'lib.chaos').write_text('fn double(x){x*2};export{double}')
            (root/'main.chaos').write_text('import "lib.chaos" as L;L.double(input)')
            run=subprocess.run([sys.executable,'-m','chaos','run',str(root/'main.chaos'),'--input-json','21'],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(json.loads(run.stdout),42)
            check=subprocess.run([sys.executable,'-m','chaos','check',str(root/'main.chaos')],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(check.returncode,0,check.stderr);self.assertIn('lib.chaos',json.loads(check.stdout)['files'])
            repl=subprocess.run([sys.executable,'-m','chaos','repl'],input='var n=1\nn=n+1\nn\n.quit\n',cwd=folder,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(repl.returncode,0,repl.stderr);self.assertEqual([json.loads(line)for line in repl.stdout.splitlines()],[1,2,2])


class HTTPLanguageAcceptance(unittest.TestCase):
    def test_multifile_program_edit_and_workflow_share_one_language(self):
        from chaos import Client, Workflow
        from chaos.client import ConflictError, ValidationError
        from chaos.server import make_server
        with Framework() as app:
            server=make_server('127.0.0.1',0,framework=app)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]))
                project=client.create_project('remote_language',source='import "../lib.chaos" as L;L.double(input)',entry='app/main.chaos',files={'lib.chaos':'fn double(x){x*2};export{double}'})
                self.assertEqual(client.start(project['id'],project['version'],21,mode='source').result(),42)
                self.assertEqual(client.program(project['id'],file='lib.chaos')['program']['file'],'lib.chaos')
                project=client.edit_program(project['id'],project['version'],file='lib.chaos',source='fn double(x){x*3};export{double}')
                self.assertEqual(client.start(project['id'],project['version'],21,mode='graph').result(),63)
                with self.assertRaises(ConflictError):client.edit_program(project['id'],1,source='0')
                before=client.get_project(project['id'])
                with self.assertRaises(ValidationError):client.edit_program(project['id'],project['version'],file='lib.chaos',source='let x=@')
                self.assertEqual(client.get_project(project['id']),before)
                graph=Workflow().input('i').function('double','import "lib.chaos" as L;L.double(input)').output('o').connect('i','double').connect('double','o')
                project=client.create_project('workflow_language',graph=graph,files={'lib.chaos':'fn double(x){x*2};export{double}'})
                self.assertEqual(project['authoring'],'workflow')
                for mode in ('graph','source'):
                    self.assertEqual(client.start(project['id'],project['version'],21,mode=mode).result(),42)
                semantic_hash=project['program']['semanticHash'];project['graph']['nodes'][0]['x']=987
                project=client.save_project(project,from_view='graph')
                self.assertEqual(project['program']['semanticHash'],semantic_hash)
            finally:
                server.shutdown();server.server_close();worker.join(2)


if __name__=='__main__':unittest.main()
