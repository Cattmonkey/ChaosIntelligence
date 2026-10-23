"""Cleanup semantics through the language, IR and public execution surfaces."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Framework, Registry, Session, Settings, Workflow
from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.program import decode_program, format_program, parse_program, patch_program, plain, project_program, templates
from chaos.server import make_server


class FinallySemantics(unittest.TestCase):
    def check(self, source, expected):
        # Formatting must retain both values and effects, not just valid syntax.
        effects = []
        for text in (source, format_program(parse_program(source))):
            calls = []
            actual = Evaluator(tool_handler=lambda n, v: (calls.append([n, v]), v)[1]).run(text)
            self.assertEqual(actual, expected)
            effects.append(calls)
        self.assertEqual(effects[0], effects[1])
        return effects[0]

    def test_success_and_catch_results_do_not_become_cleanup_result(self):
        self.check('try {42} finally {99}', 42)
        self.check('try {throw 1} catch e {e.value+41} finally {99}', 42)
        self.check('try {} finally {99}', None)
        self.check('try {false} finally {null}', False)

    def test_nested_cleanup_effect_order(self):
        calls = self.check('try {try {tool("body",1); throw 2} finally {tool("inner",3)}} catch e {tool("catch",e.value)} finally {tool("outer",4)}', 2)
        self.assertEqual(calls, [['body',1],['inner',3],['catch',2],['outer',4]])

    def test_return_call_is_resolved_before_cleanup(self):
        source = 'var n=1;fn read(){tool("read",n)};fn f(){try{return read()}finally{n=2;tool("cleanup",n)}};[f(),n]'
        self.assertEqual(self.check(source,[1,2]), [['read',1],['cleanup',2]])
        source = 'var seen=[];fn bad(){seen=append(seen,"call");throw 7};fn f(){try{return bad()}catch e{seen=append(seen,"catch");return e.value}finally{seen=append(seen,"cleanup")}};[f(),seen]'
        self.check(source, [7,['call','catch','cleanup']])

    def test_handler_return_call_is_resolved_before_cleanup(self):
        self.check('var x=1;fn read(){x};fn f(){try{throw null}catch e{return read()}finally{x=2}};[f(),x]', [1,2])
        self.check('var x=[];fn bad(){x=append(x,"bad");throw 7};let result=try{try{throw 1}catch e{bad()}finally{x=append(x,"cleanup")}}catch e{e.value};[result,x]', [7,['bad','cleanup']])

    def test_break_continue_and_nested_loops_cleanup(self):
        self.check('var seen=[];for n in [1,2,3]{try{if n==1{continue}else{break}}finally{seen=append(seen,n)}};seen', [1,2])
        self.check('var n=0;while true{try{n=n+1;break}finally{if n<3{continue}else{null}}};n', 3)
        self.check('var n=0;for a in [1,2]{for b in [1,2]{try{break}finally{n=n+1}}};n', 2)

    def test_cleanup_transfer_overrides_ordinary_pending_transfer(self):
        self.check('fn f(){try{return 1}finally{return 42}};f()', 42)
        self.check('fn f(){try{throw 1}finally{return 42}};f()', 42)
        self.check('fn f(){try{return 1}finally{throw 42}};try{f()}catch e{e.value}', 42)
        self.check('try{try{throw 1}finally{throw 2}}catch e{e.value}', 2)
        self.check('var x=0;for n in [1]{try{continue}finally{x=42;break}};x', 42)

    def test_cleanup_failure_bypasses_associated_catch(self):
        self.check('var count=0;let result=try{try{1}catch e{count=count+1}finally{throw 42}}catch e{e.value};[result,count]', [42,0])
        self.check('try{try{1}finally{1/0}}catch e{e.code}', 'ZeroDivisionError')

    def test_scope_isolated_and_contextual_keyword_compatible(self):
        self.check('let x=42;try{let x=7;x}finally{let x=9};x', 42)
        self.check('let finally=42;fn f(finally){finally};f(finally)', 42)
        self.check('fn finally(){42};try{1}catch e{2} finally()', 42)
        self.check('let finally=42;try{1}catch e{2} finally', 42)
        self.check('try{try{let private=1}finally{private}}catch e{e.code}', 'runtime_error')
        self.check('try{try{throw 1}catch e{e.value}finally{e}}catch outer{outer.code}', 'runtime_error')

    def test_hard_control_skips_pending_user_cleanup(self):
        for failure in (ResourceLimit('stop'), ExecutionControl('cancel')):
            calls=[]
            def tool(name,value):
                calls.append(name)
                if name=='stop':raise failure
                return value
            with self.subTest(failure=type(failure).__name__), self.assertRaises(type(failure)):
                Evaluator(tool_handler=tool).run('fn f(){try{try{tool("stop",null)}catch e{return 42}finally{tool("inner",null);return 43}}finally{tool("outer",null);return 44}};f()')
            self.assertEqual(calls,['stop'])
        calls=[]
        with self.assertRaises(ResourceLimit):
            Evaluator(step_limit=100,tool_handler=lambda n,v:calls.append(n)).run('try{while true{}}finally{tool("cleanup",null)}')
        self.assertEqual(calls,[])

    def test_cleanup_has_no_extra_budget_and_control_cannot_be_caught(self):
        calls=[]
        with self.assertRaises(ResourceLimit):
            Evaluator(step_limit=120,tool_handler=lambda n,v:calls.append(n)).run('try{try{42}finally{while true{}}}catch e{tool("bad",null)}finally{tool("outer",null)}')
        self.assertEqual(calls,[])

    def test_cleanup_cannot_gain_effect_authority(self):
        with self.assertRaisesRegex(LanguageError,'工具效果'):
            Framework.evaluate('try{42}finally{tool("write",null)}')

    def test_bad_grammar_rejected_before_any_effect(self):
        for invalid in ('try{1}', 'try{1}finally 2', 'try{1}finally{return 42}',
                        'try{1}finally{break}', 'try{1}finally{continue}',
                        'while true{fn f(){try{1}finally{break}}}',
                        'try{1}finally{}catch e{}'):
            calls=[]
            with self.subTest(invalid=invalid), self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *a:calls.append(a)).run('tool("bad",null);'+invalid)
            self.assertEqual(calls,[])

    def test_module_location_and_session_cells(self):
        with self.assertRaises(LanguageError) as failure:
            Evaluator().run('import "lib.chaos" as L;L.f()',files={'lib.chaos':'fn f(){try{42}finally{\n  1/0\n}};export{f}'})
        self.assertEqual((failure.exception.file,failure.exception.line),('lib.chaos',2))
        session=Session();session.evaluate('var n=0;fn f(){try{throw 1}finally{n=n+1}}')
        with self.assertRaises(LanguageError):session.evaluate('f()')
        self.assertEqual(session.evaluate('n'),1)


class FinallyIntegration(unittest.TestCase):
    def test_legacy_ir_and_structural_cleanup_edit(self):
        legacy=parse_program('try{1}catch e{2}')
        self.assertEqual(set(legacy['root']['statements'][0])-{'id','span'}, {'kind','body','name','handler'})
        source='var n=0;try{42}finally{n=1};n'
        ir=parse_program(source);protected=ir['root']['statements'][1]
        self.assertEqual(protected['kind'],'finally')
        target=protected['cleanup']['statements'][0]['value']['id']
        edited=patch_program(ir,[{'op':'set','id':target,'field':'value','value':7}])
        self.assertEqual(Evaluator().run(format_program(edited)),7)
        self.assertEqual(edited['root']['statements'][1]['cleanup']['statements'][0]['value']['id'],target)
        self.assertTrue(any(edge['slot']=='cleanup' for edge in project_program(edited)['edges']))
        self.assertIn('finally',templates())
        broken=copy.deepcopy(edited);broken['root']['statements'][1]['body']=copy.deepcopy(templates()['literal'])
        with self.assertRaises(LanguageError):format_program(broken)
        self.assertEqual(plain(decode_program(edited)),plain(decode_program(parse_program(format_program(edited)))))

    def test_http_sdk_workflow_trace_and_committed_versions(self):
        registry=Registry();calls=[]
        @registry.tool('test.record',effect='write')
        def record(value,context):calls.append(value);return value
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(Path(folder)/'settings.json')
            with Framework(registry=registry,settings=settings) as app:
                server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'update')
                worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
                try:
                    client=Client('http://127.0.0.1:'+str(server.server_address[1]))
                    source='try{input+1}finally{tool("test.record",7)}'
                    p=client.create_project('cleanup',source=source,capabilities=['test.record'])
                    run=client.start('cleanup',p['version'],41,trace=True)
                    self.assertEqual(run.result(),42);self.assertEqual(calls,[7])
                    events=client.events('cleanup',run_id=run.id)['items']
                    self.assertTrue(any(e['type']=='program.step' and e['payload']['kind']=='finally' for e in events))
                    snapshot=client.snapshot('cleanup')
                    ir=app.program('cleanup')['program'];target=ir['root']['statements'][0]['cleanup']['statements'][0]['arguments'][1]['id']
                    updated=app.edit_program('cleanup',p['version'],operations=[{'op':'set','id':target,'field':'value','value':8}])
                    self.assertEqual(client.get_project('cleanup')['source'],updated['source'])
                    self.assertEqual(client.program('cleanup')['program']['semanticHash'],updated['program']['semanticHash'])
                    later=client.events('cleanup',after=snapshot['eventCursor'])['items']
                    self.assertTrue(any(e['payload'].get('version')==updated['version'] for e in later))
                    self.assertEqual(client.start('cleanup',updated['version'],41).result(),42);self.assertEqual(calls,[7,8])
                    self.assertEqual(run.get()['snapshot']['source'],source)
                    workflow=Workflow().input('i').function('f','try{input+1}finally{null}').output('o').connect('i','f').connect('f','o')
                    graph=app.create_project('graph_cleanup',graph=workflow)
                    self.assertEqual(app.start('graph_cleanup',graph['version'],41).result(),42)
                finally:server.shutdown();server.server_close();worker.join(2)

    def test_cli_check_format_run_and_repl(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'main.chaos';p.write_text('var n=0;let answer=try{input+1}finally{n=7};[answer,n]')
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)
            for args in (['check',str(p)],['fmt',str(p)],['ir',str(p)],['run',str(p),'--input-json','41']):
                result=subprocess.run([sys.executable,'-m','chaos',*args],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if args[0]=='run':self.assertEqual(json.loads(result.stdout),[42,7])
            result=subprocess.run([sys.executable,'-m','chaos','repl'],input='var n=0\ntry{42}finally{n=7}\nn\n.quit\n',cwd=folder,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],[0,42,7])


if __name__=='__main__':unittest.main()
