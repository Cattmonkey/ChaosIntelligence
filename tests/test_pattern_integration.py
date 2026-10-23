"""Pattern targets retain public APIs, lexical state and real Registry effects."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec
from chaos.language import Cell, Evaluator, LanguageError
from chaos.program import format_program, parse_program
from chaos.session import Session

ROOT = Path(__file__).resolve().parent.parent
SOURCE = '''let {rows:rows,..metadata}=input;
var [total,count]:List=[0,0];
for [left,right] in rows{total=total+left+right;count=count+1};
async fn* incoming(){for row in rows{yield row}}
async for [left,right] in incoming(){total=total+left+right};
let eager=[left+right for [left,right] in rows];
let named={key:value for [key,value] in [["x",1],["y",2]]};
let lazy=(left*right for [left,right] in rows);
let stream=async (left+right async for [left,right] in incoming());
[total,count,eager,named,collect(lazy),await acollect(stream),metadata]'''
INPUT = {'rows': [[2, 3], [4, 5]], 'label': 'fixture'}
EXPECTED = [28, 2, [5, 9], {'x': 1, 'y': 2}, [6, 20], [5, 9], {'label': 'fixture'}]


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from walk(child)
    elif isinstance(value, list):
        for child in value: yield from walk(child)


class PatternIntegrationTests(unittest.TestCase):
    def run_source(self, registry, source, value=None, capabilities=()):
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project=app.create_project('pattern-fixture',source=source,capabilities=list(capabilities))
            return app.start(project['id'],project['version'],value,mode='source').result(timeout=8)

    def test_sdk_native_ir_and_formatter_round_trip_all_target_kinds(self):
        self.assertEqual(Framework.evaluate(SOURCE, {'input': INPUT}), EXPECTED)
        program=parse_program(SOURCE)
        kinds={n.get('kind') for n in walk(program)}
        self.assertTrue({'let_pattern','var_pattern','for_pattern','async_for_pattern',
                         'comp_for_pattern','comp_async_for_pattern'}<=kinds)
        self.assertEqual(Framework.evaluate(format_program(program), {'input':INPUT}), EXPECTED)
        formatted=Framework.format_source(SOURCE,line_width=55,indent_size=4)
        self.assertEqual(Framework.format_source(formatted,line_width=55,indent_size=4),formatted)
        self.assertEqual(Framework.evaluate(formatted, {'input':INPUT}), EXPECTED)

    def test_cli_check_format_ir_run_and_repl(self):
        source='let [left,right]=[20,22];left+right'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                with self.subTest(command=command):
                    result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,
                                          env=env,capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),42)
                    if command=='fmt':self.assertEqual(result.stdout,Framework.format_source(source))
                    if command=='ir':self.assertIn('let_pattern',{n.get('kind') for n in walk(json.loads(result.stdout))})
            result=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,
                                  input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()),42)

    def test_http_ir_edit_preserves_prior_result_and_pattern_source(self):
        from chaos.server import make_server
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                source='let [left,right]=input;left+right+1'
                project=client.create_project('pattern-http',source=source,capabilities=[])
                first=client.start(project['id'],1,[20,21]);self.assertEqual(first.result(5),42)
                original=client.program(project['id'])['program']
                target=next(n['id'] for n in walk(original) if n.get('kind')=='literal' and n.get('value')==1)
                updated=client.edit_program(project['id'],1,operations=[{'op':'set','id':target,'field':'value','value':2}])
                second=client.start(project['id'],updated['version'],[20,21]);self.assertEqual(second.result(5),43)
                self.assertEqual(first.get()['output'],42)
                self.assertEqual(first.get()['snapshot']['source'],source)
                self.assertIn('let_pattern',{n.get('kind') for n in walk(client.program(project['id'])['program'])})
                self.assertEqual(client.get_project(project['id'])['source'],updated['source'])
            finally:
                server.shutdown();server.server_close();thread.join(3)

    def test_real_agent_structured_result_is_evaluated_once_then_bound(self):
        registry,seen=Registry(),[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input)
            return Final({'user':{'name':'Ada'},'scores':[20,22,9],'trace':'trusted'})
        registry.register_provider(ProviderSpec('fixture.structured',provider))
        registry.register_tool(ToolSpec('fixture.consume',lambda value,context:seen.append(value) or value))
        source='''agent reader{provider:"fixture.structured",tools:[],maxSteps:1};
let {user:{name:who},scores:[first,second,..tail],..rest}:Record=reader("request");
tool("fixture.consume",[who,first+second,tail,rest])'''
        expected=['Ada',42,[9],{'trace':'trusted'}]
        self.assertEqual(self.run_source(registry,source,capabilities=['fixture.consume']),expected)
        self.assertEqual(seen,['request',expected])

    def test_agent_result_mismatch_does_not_run_dependent_effect(self):
        registry,seen=Registry(),[]
        registry.register_provider(ProviderSpec('fixture.structured',lambda r,c:seen.append('source') or Final({'pair':[1]})))
        registry.register_tool(ToolSpec('fixture.consume',lambda v,c:seen.append('dependent')))
        source='''agent reader{provider:"fixture.structured",tools:[],maxSteps:1};
try{let {pair:[left,right]}=reader(null);tool("fixture.consume",left)}catch e{e.code}'''
        self.assertEqual(self.run_source(registry,source,capabilities=['fixture.consume']),'match_error')
        self.assertEqual(seen,['source'])

    def test_pattern_does_not_expand_agent_tool_capability(self):
        registry,seen=Registry(),[]
        registry.register_tool(ToolSpec('fixture.denied',lambda value,context:seen.append(value)))
        registry.register_provider(ProviderSpec('fixture.structured',lambda r,c:ToolCall('fixture.denied',r.input)))
        source='''agent reader{provider:"fixture.structured",tools:[],maxSteps:2};
try{let [left,right]=reader([20,22]);left+right}catch e{"denied"}'''
        self.assertEqual(self.run_source(registry,source,capabilities=['fixture.denied']),'denied')
        self.assertEqual(seen,[])

    def test_session_failed_declaration_never_commits_a_prefix(self):
        for declaration in ('let [fresh,kept]=[1,2]', 'var [fresh,kept]=[1,2]',
                            'let [fresh,{x:other}]=[1,{wrong:2}]',
                            'var [fresh,{x:other}]=[1,{wrong:2}]'):
            with self.subTest(declaration=declaration):
                session=Session();session.evaluate('var kept=7')
                before=dict(session.bindings());old_cell=session.env.values['kept']
                with self.assertRaises(LanguageError):session.evaluate(declaration)
                self.assertEqual(session.bindings(),before)
                self.assertIs(session.env.values['kept'],old_cell)
                self.assertEqual(session.evaluate('kept=8;kept'),8)

    def test_each_var_leaf_has_independent_any_cell_shared_by_closures(self):
        session=Session();session.evaluate('var [left,{value:right},..rest]:List=[1,{value:2},3];fn update(){left="changed";right=42;rest=append(rest,4)}')
        cells=[session.env.values[n] for n in ('left','right','rest')]
        self.assertTrue(all(isinstance(cell,Cell) for cell in cells))
        self.assertEqual(len({id(cell) for cell in cells}),3)
        self.assertEqual(session.evaluate('update();[left,right,rest]'),['changed',42,[3,4]])
        self.assertTrue(all(session.env.values[n] is cell for n,cell in zip(('left','right','rest'),cells)))

    def test_module_exports_all_let_names_and_rejects_all_var_names_before_effects(self):
        files={'values.chaos':'let {pair:[left,right],..metadata}={pair:[20,22],label:"module"};export{left,right,metadata}'}
        self.assertEqual(Evaluator().run('import "values.chaos" as V;[V.left+V.right,V.metadata]',files=files),[42,{'label':'module'}])
        for exported in ('left','right','rest'):
            seen=[]
            with self.subTest(exported=exported),self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:seen.append(args)).run('import "values.chaos" as V;0',files={
                    'values.chaos':'tool("unexpected",null);var [left,right,..rest]=[1,2,3];export{'+exported+'}'})
            self.assertEqual(seen,[])

    def test_debugger_projection_has_each_binder_original_span_and_no_helper_frame(self):
        source='let rows=[[20,1],[21,2]];\nlet result=[\n  left + right\n  for [left,right] in rows\n];\nresult'
        with Framework(settings=Settings(':memory:')) as app:
            project=app.create_project('pattern-debugger',source=source)
            run=app.start_debug(project['id'],1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
            previous=0
            try:
                for left,right in ((20,1),(21,2)):
                    deadline=time.monotonic()+4
                    while time.monotonic()<deadline:
                        state=run.debugger()
                        if state['status']=='paused' and state['pauseId']>previous:break
                        time.sleep(.005)
                    self.assertEqual(state['status'],'paused');self.assertGreater(state['pauseId'],previous)
                    self.assertEqual((state['location']['file'],state['location']['line']),('main.chaos',3))
                    self.assertEqual([frame['name'] for frame in state['frames']],['<module>'])
                    values={row['name']:row['value'] for row in state['frames'][0]['locals']}
                    self.assertEqual([values['left'],values['right']],[left,right])
                    previous=state['pauseId'];run.debug_command('continue',pause_id=previous)
                self.assertEqual(run.result(4),[21,23])
            finally:run.cancel()

    def test_cancel_async_binding_rhs_drains_real_provider(self):
        registry,entered,exited=Registry(),threading.Event(),threading.Event()
        def provider(request,context):
            entered.set()
            try:
                for _ in range(500):context.checkpoint();threading.Event().wait(.01)
                raise AssertionError('provider cancellation was not delivered')
            finally:exited.set()
        registry.register_provider(ProviderSpec('fixture.wait',provider))
        source='''agent reader{provider:"fixture.wait",tools:[],maxSteps:1};
async fn work(){reader(null)}let [left,right]=await work();left+right'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            project=app.create_project('pattern-cancel',source=source,capabilities=[])
            run=app.start(project['id'],1,None,mode='source')
            try:
                self.assertTrue(entered.wait(3));run.cancel()
                self.assertEqual(run.wait(timeout=5)['status'],'cancelled')
                self.assertTrue(exited.is_set())
            finally:run.cancel()


if __name__=='__main__':unittest.main()
