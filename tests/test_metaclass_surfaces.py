"""Metaclasses are shared native syntax across editors, IR, SDK and agents."""
from workbench_actions import button as workbench_button, view as workbench_view
import copy, hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys, tempfile, threading, unittest
from types import SimpleNamespace
from unittest.mock import patch
from chaos import Client, Framework, Settings, Registry, ProviderSpec, ToolCall, Final
from chaos.ide_language import analyze, format_source
from chaos.language import Evaluator, LanguageError, Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import spec_for
from chaos.program import SHAPES, parse_program, format_program, templates, decode_program, plain, patch_program, project_program
from chaos.server import make_server
from chaos.symbols import rename, references
from chaos.debugger import preview
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None
ROOT=Path(__file__).resolve().parent.parent
SOURCE='''// Native metaclasses generate agent component classes in the language.
class Meta(Type){fn __new__(mcls,name,bases,namespace,**options){
  Type.__new__(mcls,name,bases,merge(namespace,{stamp:class_member(40)}),**options)
}}
class Component(metaclass=Meta){fn __call__(self,value:Int)->Int{value+self.stamp}}
let Dynamic=new_class("Dynamic",[],class_members(Component),{metaclass:Meta});
[Component()(2),Dynamic()(2),class_of(Component)==Meta,class_of(Meta)==Type,
 isinstance(Component,Meta),issubclass(Meta,Type),type_of(Meta),class_members(Component).stamp.value]'''
EXPECTED=[42,42,True,True,True,True,'Class',40]
STRUCTURE='''class First(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,merge(ns,{answer:41}))}}
class Second(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,merge(ns,{answer:42}))}}
class Product(metaclass=First){};Product().answer'''

class MetaclassSurfaceAnalysis(unittest.TestCase):
    def test_native_metadata_and_type_alias_signature(self):
        for name,params,result in [
            ('Type',['args','options'],'Any'),('class_of',['value'],'Class'),
            ('Type.__prepare__',['mcls','name','bases','options'],'Record'),
            ('Type.__new__',['mcls','name','bases','namespace','options'],'Any'),
            ('Type.__init__',['cls','name','bases','namespace','options'],'Null'),
            ('Type.__call__',['cls','args','options'],'Any'),
            ('new_class',['name','bases','members','options'],'Any')]:
            with self.subTest(name=name):
                spec=spec_for(name);self.assertIsNotNone(spec)
                self.assertEqual([p.name for p in spec.parameters],params);self.assertEqual(spec.result_type,result)
        result=analyze('let create=Type;create(')
        self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['args','options'])
        result=analyze('Type.__prepare__(')
        self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['name','bases','options'])
        lsp=convert('textDocument/signatureHelp',result,SimpleNamespace(name='main.chaos'),{'main.chaos':'Type.__prepare__('})
        self.assertIn('name',lsp['signatures'][0]['label'])

    def test_default_type_reflection_completion_and_nominal_contracts(self):
        for source in ['class C{}let reflect=class_of;reflect(C).','class C{}Type(C).','Type.']:
            with self.subTest(source=source):
                result=analyze(source)
                self.assertTrue({'__new__','__prepare__','__init__','__call__'}<={c['label'] for c in result['completions']})
        result=analyze('class Meta(Type){}let m:Type=Meta;class C{}let bad:Type=C')
        self.assertTrue(any(d['code']=='type_mismatch' for d in result['diagnostics']))
        self.assertEqual(analyze('data Option{None,Some(value)}let x=Some(1);x')['diagnostics'],[])
        self.assertEqual(next(s['type'] for s in analyze('data Option{None}')['symbols'] if s['name']=='Option'),'DataType')

    def test_custom_creation_result_is_conservative_without_running_hooks(self):
        source='class Meta(Type){fn __new__(m,n,b,ns){42}}class C(metaclass=Meta){}let result=C;result'
        result=analyze(source);self.assertEqual(result['diagnostics'],[])
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='C'),'Any')
        source='fn factory(name,bases,namespace){42}class C(metaclass=factory){}let result=C;result'
        self.assertEqual(analyze(source)['diagnostics'],[])
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='C'),'Any')
        self.assertEqual(next(s['type'] for s in analyze('let c=new_class("C");c')['symbols'] if s['name']=='c'),'Any')
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed source')):
            self.assertEqual(analyze(SOURCE)['diagnostics'],[])
            parse_program(SOURCE);format_source(SOURCE)

    def test_meta_header_uses_existing_options_ast_and_format(self):
        program=parse_program(SOURCE);node=program['root']['statements'][1]
        self.assertEqual(node['kind'],'class');self.assertEqual(node['options'][0]['kind'],'keyword')
        self.assertEqual(node['options'][0]['name'],'metaclass');self.assertEqual(node['options'][0]['value']['name'],'Meta')
        self.assertEqual(len(decode_program(program)[1][1]),5)
        self.assertIn('options.0',[n['slot'] for n in project_program(program)['nodes']])
        self.assertNotIn('options',templates()['class'])
        old=parse_program('class Old{}');self.assertEqual(len(decode_program(old)[1][0]),4)
        for version in (1,2):
            for width in (40,88):
                formatted=format_source(SOURCE,language_version=version,line_width=width)
                self.assertEqual(parse_program(formatted)['semanticHash'],program['semanticHash'])
                self.assertEqual(format_source(formatted,line_width=width),formatted)
        self.assertEqual(plain(decode_program(program)),plain(Parser(format_program(program)).program()))
        self.assertEqual(Framework.evaluate(format_program(program)),EXPECTED)

    def test_structural_meta_edit_and_roundtrip_change_native_execution(self):
        program=parse_program(STRUCTURE);option=program['root']['statements'][2]['options'][0]
        changed=patch_program(program,[{'op':'set','id':option['value']['id'],'field':'name','value':'Second'}])
        self.assertEqual(Framework.evaluate(STRUCTURE),41);self.assertEqual(Framework.evaluate(format_program(changed)),42)
        self.assertNotEqual(changed['semanticHash'],program['semanticHash'])
        self.assertEqual(parse_program(format_source(format_program(changed)))['semanticHash'],changed['semanticHash'])
        malformed=copy.deepcopy(program);malformed['root']['statements'][2]['options'].append(copy.deepcopy(option))
        malformed['root']['statements'][2]['options'][1]['id']='extra_option'
        with self.assertRaises(LanguageError):decode_program(malformed)

    def test_meta_reference_rename_and_macro_hygiene(self):
        changed=rename(SOURCE,position=SOURCE.index('class Meta')+6,new_name='FactoryMeta')['files']['main.chaos']
        self.assertIn('metaclass=FactoryMeta',changed);self.assertIn('class_of(Component)==FactoryMeta',changed)
        self.assertEqual(Framework.evaluate(changed),EXPECTED)
        refs=references(SOURCE,position=SOURCE.index('class Meta')+6)
        self.assertGreaterEqual(len(refs['references']),5)
        source='''macro build(meta){quote{let chosen=99;class Made(metaclass=unquote(meta)){let answer=42};Made().answer}}
class Meta(Type){}let chosen=Meta;[build!(chosen),chosen==Meta]'''
        self.assertEqual(Framework.evaluate(source),[42,True])

    def test_headers_allow_language_await_but_prepare_stays_sync(self):
        source='async fn build(){class C(metaclass=await (async fn(){Type})()){let answer=42};C().answer}await build()'
        self.assertEqual(Framework.evaluate(source),42)
        self.assertEqual(Framework.evaluate(format_source(source)),42)
        with self.assertRaises(LanguageError):Framework.evaluate('class Meta(Type){async fn __prepare__(m,n,b){ {}}}class C(metaclass=Meta){}')

    def test_debugger_metadata_is_bounded_redacted_and_hook_inert(self):
        effects=[];ev=Evaluator(tool_handler=lambda name,value:effects.append(name) or value)
        value=ev.run('class Meta(Type){@property\nfn never(cls){tool("danger",1)}}class C(metaclass=Meta){let token="secret"};C')
        shown=preview(value,redact=lambda text:text.replace('Meta','redacted-meta'))
        self.assertEqual(shown['type'],'Class');self.assertEqual(shown['metaclass'],'redacted-meta')
        self.assertEqual(effects,[]);self.assertNotIn('owner',json.dumps(shown));self.assertNotIn('secret',json.dumps(shown))
        self.assertTrue(preview(value,budget=[0])['truncated'])
        self.assertEqual(preview(ev.env.get('Type'))['metaclass'],'Type')

    def test_classes_are_created_inside_an_actual_agent_tool(self):
        registry=Registry();registry.register_provider(ProviderSpec('choose',lambda req,ctx:ToolCall('work',req.input) if not req.history else Final(req.history[-1]['output'])))
        source='''fn work(x:Int){class Meta(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,merge(ns,{answer:class_member(x+1)}))}}class Role(metaclass=Meta){};Role().answer}
agent a{provider:"choose"};await agent_bind(a,[agent_tool("work",work,input_type="Int",output_type="Int")])(41)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            project=app.create_project('agent-metaclass',source=source,capabilities=[])
            self.assertEqual(app.start(project['id'],project['version'],None,mode='source').result(10),42)

    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_graphical_prepare_method_template_is_real_function_ast(self):
        payload={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(STRUCTURE)}
        result=subprocess.run(['node',str(ROOT/'tests/metaclass_controls.cjs')],input=json.dumps(payload),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        output=json.loads(result.stdout);self.assertEqual(output['status'],'passed')
        self.assertEqual(Framework.evaluate(format_program(output['program'])),42)

class MetaclassHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Framework(Path(self.tmp.name)/'data',settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.tmp.name)/'control',update_home=Path(self.tmp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
    def close_server(self):self.server.shutdown();self.thread.join(3);self.server.server_close()

    def test_sdk_http_cli_and_pure_source_agree(self):
        project=self.client.create_project('metaclass',source=SOURCE)
        self.assertEqual(self.client.start(project['id'],project['version'],None,mode='source').result(),EXPECTED)
        self.assertEqual(self.client.program(project['id'])['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
        self.assertEqual(self.client.analyze('Type.__new__(')['signatureHelp']['parameters'][0]['name'],'mcls')
        result=subprocess.run([sys.executable,'-m','chaos','run','-'],input=SOURCE,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),EXPECTED)

    @unittest.skipUnless(sync_playwright,'Playwright unavailable')
    def test_real_browser_meta_rename_structure_format_and_headless_consistency(self):
        project=self.app.get_project('workspace');project.update(source=STRUCTURE,files={'main.chaos':STRUCTURE},entry='main.chaos');self.app.save_project(project,from_view='source')
        folder=os.environ.get('CHAOS_METACLASS_AUDIT');output=Path(folder) if folder else None
        if output:output.mkdir(parents=True,exist_ok=True)
        errors=[];external=[]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                def route(r):
                    if r.request.url.startswith(self.origin+'/'):r.continue_()
                    else:external.append(r.request.url);r.abort()
                context.route('**/*',route)
                def shot(name):
                    if output:page.screenshot(path=str(output/(name+'.png')))
                def run(expected):
                    page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runStatus")?.textContent.includes("已完成")')
                    self.assertEqual(json.loads(page.locator('#resultPanel').inner_text()),expected)
                page.goto(self.origin);workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                workbench_button(page,'formatSource');formatted=format_source(STRUCTURE)
                page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');run(41);shot('meta-result')
                workbench_view(page,'structure');page.wait_for_function('()=>programDraft?.root?.statements?.[2]?.options')
                target=page.evaluate('programDraft.root.statements[2].options[0].value.id');page.locator('[data-syntax="'+target+'"]').click()
                field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='name'))
                field.locator('input').fill('Second');field.get_by_role('button',name='应用',exact=True).click();shot('meta-properties')
                workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty');changed=self.client.get_project('workspace')
                self.assertEqual(parse_program(changed['source'])['root']['statements'][2]['options'][0]['value']['name'],'Second')
                workbench_view(page,'source');workbench_button(page,'formatSource');readable=format_source(changed['source'])
                page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=readable)
                workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');run(42);shot('meta-edited-result')
                current=self.client.get_project('workspace');self.assertEqual(self.client.start('workspace',current['version'],None,mode='source').result(),42)
                self.assertEqual(Framework.evaluate(current['source']),42)
                editor=page.locator('.cm-content[contenteditable="true"]');editor.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text('Type.')
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("编辑中")');page.keyboard.press('Control+Space');page.wait_for_selector('.cm-tooltip-autocomplete')
                self.assertTrue({'__new__','__prepare__','__init__','__call__'}<=set(page.locator('.cm-tooltip-autocomplete .cm-completionLabel').all_text_contents()));shot('meta-completion');page.keyboard.press('Escape')
                page.evaluate('(text)=>{const e=ChaosIDE.editor();e.dispatch({changes:{from:0,to:e.state.doc.length,insert:text},selection:{anchor:0},scrollIntoView:true})}',readable)
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                page.set_viewport_size({'width':390,'height':900});self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'));shot('meta-mobile')
                self.assertEqual(errors,[]);self.assertEqual(external,[])
                if output:
                    (output/'browser-result.json').write_text(json.dumps({'status':'passed','sourceRoot':str(ROOT),'sourceStructureSDKHTTPPureEqual':True,'completion':True,'formatSemanticHash':parse_program(formatted)['semanticHash'],'editedSemanticHash':parse_program(current['source'])['semanticHash'],'mobileNoOverflow':True,'errors':errors,'externalRequests':external,'screenshots':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.png')}},indent=2)+'\n')
            finally:browser.close()

if __name__=='__main__':unittest.main()
