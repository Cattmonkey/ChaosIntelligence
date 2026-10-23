"""Advanced syntax preserves one program across API, IR, editor and execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Framework, Client, Registry, ProviderSpec, ToolSpec, Final
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program, plain, decode_program, templates
from chaos.ide_language import analyze, format_source
from chaos.server import make_server
from chaos.settings import Settings

SOURCES = [
    ('fn* count(n){var i=0;while i<n{yield i;i=i+1}};collect(count(4))', [0,1,2,3]),
    ('fn* child(){yield 1;yield 2};fn* all(){yield from child();yield 3};collect(all())', [1,2,3]),
    ('async fn twice(x:Int)->Int{x*2};await gather([twice(20),twice(21)])', [40,42]),
    ('macro add(a,b){quote{unquote(a)+unquote(b)}};add!(20,22)', 42),
    ('macro identity(x){quote{unquote(x)}};async fn answer(){identity!(42)};await answer()', 42),
]

class AdvancedIR(unittest.TestCase):
    def test_source_ir_json_and_comment_formatter_keep_semantics(self):
        for source, expected in SOURCES:
            with self.subTest(source=source):
                original=parse_program(source)
                restored=json.loads(json.dumps(original))
                canonical=format_program(restored)
                self.assertEqual(plain(decode_program(original)),plain(decode_program(parse_program(canonical))))
                self.assertEqual(Evaluator().run(source),expected)
                self.assertEqual(Evaluator().run(canonical),expected)
                commented='// user comment\n'+source+' // trailing note\n'
                formatted=format_source(commented)
                self.assertIn('// user comment',formatted)
                self.assertIn('// trailing note',formatted)
                self.assertEqual(Evaluator().run(formatted),expected)

    def test_templates_cover_new_shapes(self):
        choices=templates()
        for kind in ('async_gen_fn','async_for','gen_fn','async_fn','await','yield','yield_from','macro','macrocall','quote','unquote'):
            self.assertEqual(choices[kind]['kind'],kind)

    def test_invalid_context_in_ir_is_rejected(self):
        original=parse_program('fn* f(){yield 42};collect(f())')
        bad=copy.deepcopy(original)
        bad['root']['statements'][0]['function']['kind']='fn'
        with self.assertRaises(LanguageError):format_program(bad)
        self.assertEqual(Evaluator().run(format_program(original)),[42])

    def test_analysis_is_non_executing_and_infers_handle_types(self):
        source='fn* items(n:Int){yield n};async fn work(x:Int)->Int{x+1};let stream=items(4);let pending=work(41);null'
        result=analyze(source)
        self.assertTrue(result['complete'])
        self.assertFalse(result['diagnostics'],result['diagnostics'])
        types={s['name']:s['type']for s in result['symbols']}
        self.assertEqual(types['stream'],'Generator');self.assertEqual(types['pending'],'Task')
        self.assertIn('async',next(s['detail']for s in result['symbols']if s['name']=='work'))
        macro=analyze('macro wrap(x){quote{unquote(x)}};wrap!(tool("forbidden",1))')
        self.assertTrue(macro['complete'])
        self.assertIn('wrap',{s['name']for s in macro['symbols']})

    def test_macro_editor_scopes_respect_compile_and_quote_phases(self):
        valid=analyze('macro m(value){let tree=syntax_literal(42);quote{let local=1;unquote(tree)+local}};m!(0)')
        self.assertFalse(valid['diagnostics'],valid['diagnostics'])
        invalid=analyze('let outer=42;macro m(){quote{outer}};m!()')
        self.assertTrue(any(d['code']=='undefined_name'for d in invalid['diagnostics']))
        effect=analyze('macro m(){tool("no",1)};m!()')
        self.assertTrue(any(d['code']=='undefined_name'for d in effect['diagnostics']))

    def test_new_runtime_handles_are_not_extension_data_types(self):
        for kind in ('Function','Generator','AsyncGenerator','Task','Syntax'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):ToolSpec('bad',lambda v,c:v,input_type=kind)

    def test_sdk_and_http_ir_edit_share_source_version_and_snapshot(self):
        with tempfile.TemporaryDirectory() as temp, Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                source='macro plus(a,b){quote{unquote(a)+unquote(b)}};async fn work(x){plus!(x,2)};await work(input)'
                project=client.create_project('effects',source=source,capabilities=[])
                run=client.start('effects',project['version'],40);self.assertEqual(run.result(),42)
                current=client.program('effects')['program']
                literal=current['root']['statements'][1]['function']['body']['statements'][0]['arguments'][1]
                project=client.edit_program('effects',project['version'],operations=[{'op':'set','id':literal['id'],'field':'value','value':3}])
                self.assertEqual(client.start('effects',project['version'],40).result(),43)
                self.assertEqual(run.get()['snapshot']['source'],source)
                self.assertEqual(client.get_project('effects')['program']['semanticHash'],parse_program(project['source'])['semanticHash'])
                self.assertTrue(any(e['type']=='graph.saved' and e['payload']['version']==project['version']for e in client.events('effects')['items']))
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_macros_generator_async_compose_real_agent_capabilities(self):
        registry=Registry();observed=[];barrier=threading.Barrier(2)
        def provider(request,context):
            context.checkpoint();barrier.wait(timeout=3)
            observed.append(request.input)
            return Final(request.input+1)
        registry.register_provider(ProviderSpec('increment',provider))
        source='''agent worker{provider:"increment",tools:[],maxSteps:1};
macro invoke(f,x){quote{unquote(f)(unquote(x))}};
fn* inputs(){yield 40;yield 41};
async fn process(x){invoke!(worker,x)};
await gather(map(process,collect(inputs())))'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('agents',source=source,capabilities=[])
            run=app.start('agents',p['version'],None,mode='source')
            self.assertEqual(run.result(),[41,42]);self.assertCountEqual(observed,[40,41])
            events=app.events('agents',run_id=run.id)['items']
            self.assertEqual(sum(e['type']=='agent.completed'for e in events),2)
            self.assertEqual(sum(e['type']=='agent.thinking'for e in events),2)

    def test_async_stream_ir_roundtrip_and_editing(self):
        source='async fn* stream(x:Int)->Int{let sent=yield x;yield sent;return 9};let s=stream(40);let a=await anext(s);let b=await asend(s,42);let c=await anext(s);[a,b,c]'
        program=parse_program(source)
        self.assertEqual(program['root']['statements'][0]['function']['kind'],'async_gen_fn')
        self.assertEqual(Evaluator().run(format_program(program)),[{'done':False,'value':40},{'done':False,'value':42},{'done':True,'value':9}])
        self.assertEqual(plain(decode_program(program)),plain(decode_program(parse_program(format_program(program)))))
        source='async fn* values(){yield 20;yield 22};var total=0;async for item in values(){total=total+item};total'
        program=parse_program(source);self.assertEqual(program['root']['statements'][2]['kind'],'async_for')
        self.assertEqual(Evaluator().run(format_program(program)),42)
        self.assertEqual(Evaluator().run(format_source('// stream note\n'+source)),42)

    def test_qualified_macro_ir_retains_surface_and_rejects_dynamic_names(self):
        files={'macros.chaos':'macro add(a,b){quote{unquote(a)+unquote(b)}};export{add}'}
        source='import "macros.chaos" as M;M.add!(20,22)'
        program=parse_program(source);call=program['root']['statements'][1]
        self.assertEqual(call['name'],'M.add');self.assertEqual(call['kind'],'macrocall')
        self.assertEqual(Evaluator().run(format_program(program),files=files),42)
        for name in ('A.B.add','A["add"]','A.add;42'):
            invalid=copy.deepcopy(program);invalid['root']['statements'][1]['name']=name
            with self.subTest(name=name),self.assertRaises(LanguageError):format_program(invalid)

    def test_openapi_references_resolve_and_debug_configuration_is_additive(self):
        from chaos.openapi import specification
        spec=specification();pending=[spec]
        while pending:
            value=pending.pop()
            if isinstance(value,dict):
                if '$ref' in value:
                    target=spec
                    for part in value['$ref'].removeprefix('#/').split('/'):
                        self.assertIn(part,target,value['$ref']);target=target[part]
                pending.extend(value.values())
            elif isinstance(value,list):pending.extend(value)
        schemas=spec['components']['schemas']
        self.assertIn('debug',schemas['StartRun']['properties'])
        self.assertIn('cancel',schemas['DebugCommand']['properties']['command']['enum'])
        self.assertIn('Snapshot',schemas);self.assertIn('AssistantHistory',schemas)

    def test_cli_all_advanced_forms_without_server(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'main.chaos';path.write_text(SOURCES[-1][0])
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=temp,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)

if __name__=='__main__':unittest.main()
