"""Class creation protocols remain native across source, structures and agents."""
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
from chaos.program import SHAPES as SYNTAX_SHAPES, parse_program, format_program, templates, decode_program, plain, patch_program, project_program
from chaos.server import make_server
from chaos.symbols import rename, references
from chaos.debugger import preview
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None
ROOT=Path(__file__).resolve().parent.parent
OWNED=['chaos/program.py','chaos/macros.py','chaos/symbols.py','chaos/comprehensions.py','chaos/native_specs.py','chaos/ide_language.py','chaos/debugger.py','web/language.js','tests/test_class_protocol_surfaces.py','tests/class_protocol_controls.cjs']
SOURCE='''// 🧠 Native classes compose agent functions without exporting closures.
var created=[];
class Base{fn __init_subclass__(cls,*,label:Int=1,**rest){created=append(created,label);super(Base,cls).__init_subclass__(**rest);null}}
class Component(Base,label=2){let worker=fn(x:Int)->Int{x+1};fn __call__(self,x:Int)->Int{self.worker(x)}}
let copy=new_class("Copy",[Base],class_members(Component),{label:3});
let instance=copy();
{declared:Component()(10),dynamic:instance(10),labels:created,method:class_members(Component).__call__.method,contract:class_members(Component).worker.contract}'''
def expected(n=2):return {'declared':11,'dynamic':11,'labels':[n,3],'method':True,'contract':'Any'}

class ClassProtocolAnalysis(unittest.TestCase):
    def test_native_signatures_aliases_readonly_fields_and_binding_hints(self):
        for name, params, result in [('class_member',['value','mutable','method','contract'],'ClassMember'),('class_members',['klass'],'Record'),('new_class',['name','bases','members','options'],'Any'),('alloc_instance',['klass'],'Instance')]:
            spec=spec_for(name);self.assertEqual(spec.result_type,result);self.assertEqual([p.name for p in spec.parameters],params)
        self.assertTrue(all(p.mode=='keyword_only' for p in spec_for('class_member').parameters[1:]))
        source='let wrap=class_member(fn(x:Int)->Int{x+1});wrap.';result=analyze(source)
        self.assertEqual({c['label'] for c in result['completions']},{'value','mutable','method','contract'})
        result=analyze(source[:-1]+'.value(');self.assertIn('x: Int',result['signatureHelp']['label'])
        self.assertEqual(analyze('let create=new_class;create(')['signatureHelp']['parameters'][0]['name'],'name')
        self.assertTrue(any(d['code']=='immutable_binding' for d in analyze('let m=class_member(1);m.value=2')['diagnostics']))
        source='class C{fn __new__(cls,x:Int){alloc_instance(cls)}}let answer=C(1);answer'
        result=analyze(source);self.assertEqual(result['diagnostics'],[]);self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='answer'),'Any')
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='cls'),'Class')
        result=analyze('class C{fn __init_subclass__(cls,*,label:Int=1){null}};C.__init_subclass__(')
        self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['label'])
        lsp=convert('textDocument/signatureHelp',result,SimpleNamespace(name='main.chaos'),{'main.chaos':'x'})
        self.assertIn('label',lsp['signatures'][0]['label'])

    def test_old_class_ir_omission_new_options_and_structural_patches(self):
        old=parse_program('class Old{let value:Int=42};Old().value');node=old['root']['statements'][0]
        self.assertEqual(len(decode_program(old)[1][0]),4);self.assertEqual(set(node),{'id','kind','span','name','bases','body'})
        self.assertNotIn('options',templates()['class']);self.assertEqual(Framework.evaluate(format_program(old)),42)
        stable=node['id'];option={'kind':'keyword','id':'header_option','name':'flag','value':{'kind':'literal','id':'header_value','value':True}}
        added=patch_program(old,[{'op':'append','id':stable,'field':'options','node':option}]);self.assertEqual(added['root']['statements'][0]['id'],stable)
        self.assertEqual(len(decode_program(added)[1][0]),5);self.assertTrue(any(x['slot']=='options.0' for x in project_program(added)['nodes']))
        removed=patch_program(added,[{'op':'remove','id':'header_option'}]);self.assertNotIn('options',removed['root']['statements'][0]);self.assertEqual(removed['semanticHash'],old['semanticHash'])
        cleared=patch_program(added,[{'op':'set','id':stable,'field':'options','value':[]}]);self.assertEqual(cleared['semanticHash'],old['semanticHash'])
        for options in [None,False,0,'',{},[dict(option,kind='literal',value=True)],[dict(option,kind='spread',iterable=option['value'])],[option,dict(option,id='duplicate_option',value={'kind':'literal','id':'duplicate_value','value':2})]]:
            bad=copy.deepcopy(old);bad['root']['statements'][0]['options']=options
            with self.subTest(options=options),self.assertRaises(LanguageError):decode_program(bad)
        program=parse_program(SOURCE);self.assertEqual(len(decode_program(program)[1][1]),4);self.assertEqual(len(decode_program(program)[1][2]),5)
        self.assertEqual(Framework.evaluate(format_program(program)),expected())
        for version in (1,2):
            for width in (40,88):
                formatted=format_source(SOURCE,language_version=version,line_width=width)
                self.assertEqual(parse_program(formatted)['semanticHash'],program['semanticHash']);self.assertEqual(format_source(formatted,line_width=width),formatted)
        self.assertEqual(plain(decode_program(program)),plain(Parser(format_program(program)).program()))
        effects=[];engine=Evaluator(tool_handler=lambda name,value:effects.append(name) or value)
        with self.assertRaises(LanguageError) as rejected:engine.run('class C(metaclass=tool("never",0)){}')
        self.assertEqual(rejected.exception.code,'argument_error');self.assertEqual(effects,['never'])
        reserved=parse_program('class C(flag=tool("never",0)){}');reserved['root']['statements'][0]['options'][0]['name']='metaclass'
        self.assertEqual(decode_program(reserved)[1][0][4][0][1], 'metaclass')
        self.assertEqual(decode_program(parse_program(format_program(reserved)))[1][0][4][0][1], 'metaclass')
        with self.assertRaises(LanguageError) as rejected:engine.run(format_program(reserved))
        self.assertEqual(rejected.exception.code,'argument_error');self.assertEqual(effects,['never','never'])

        with_spread=SOURCE.replace('label=2)','label=2, // keep option order\n **{})')
        formatted=format_source(with_spread,line_width=40)
        self.assertIn('// keep option order',formatted);self.assertEqual(Framework.evaluate(formatted),expected());self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(with_spread)['semanticHash'])

    def test_hygienic_header_options_rename_async_context_and_inert_tools(self):
        source='''class Base{var seen:Int=0;fn __init_subclass__(cls,*,label){cls.seen=label;null}}
macro make(base,value){quote{let label=99;class Product(unquote(base),label=unquote(value)){let answer=42};[Product.seen,Product().answer,label]}}
let label=7;[make!(Base,label),label]'''
        self.assertEqual(Framework.evaluate(source),[[7,42,99],7]);self.assertEqual(Framework.evaluate(format_source(source)),[[7,42,99],7])
        source='class B{fn __init_subclass__(cls,*,label){null}}let label=7;class C(B,label=label){};label'
        changed=rename(source,position=source.index('let label')+4,new_name='amount')['files']['main.chaos']
        self.assertIn('label=amount',changed);self.assertEqual(Framework.evaluate(changed),7)
        self.assertEqual(len(references(source,position=source.index('let label')+4)['references']),3)
        self.assertEqual(Framework.evaluate('class B{fn __init_subclass__(cls,*,label){null}}async fn f(){class C(B,label=await sleep(0)){};42}await f()'),42)
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed source')):
            self.assertEqual(analyze(SOURCE)['diagnostics'],[]);format_source(SOURCE);parse_program(SOURCE);rename(source,position=source.index('let label')+4,new_name='amount')
        value=Evaluator().run('class_member("secret",contract="String")');shown=preview(value,redact=lambda x:x.replace('secret','redacted'))
        self.assertEqual(shown,{'type':'ClassMember','value':'redacted','mutable':False,'method':False,'contract':'String'});self.assertNotIn('owner',json.dumps(shown))
        value=Evaluator().run('class_member(fn(x){x})');self.assertNotIn('owner',json.dumps(preview(value)));self.assertTrue(preview(value,budget=[0])['truncated'])

    def test_native_classes_construct_inside_an_actual_agent_function(self):
        registry=Registry();registry.register_provider(ProviderSpec('choose',lambda req,ctx:ToolCall('work',req.input) if not req.history else Final(req.history[-1]['output'])))
        source='''fn work(x:Int){let method=class_member(fn(self,y:Int)->Int{x+y},method=true);let C=new_class("Local",members={__call__:method});C()(1)}
agent a{provider:"choose"};await agent_bind(a,[agent_tool("work",work,input_type="Int",output_type="Int")])(41)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('agent-class-protocol',source=source,capabilities=[]);self.assertEqual(app.start(p['id'],p['version'],None,mode='source').result(10),42)

    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_structural_class_options_and_protocol_method_templates(self):
        payload={'info':{'syntax':SYNTAX_SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run(['node',str(ROOT/'tests/class_protocol_controls.cjs')],input=json.dumps(payload),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);changed=json.loads(result.stdout);self.assertEqual(changed['status'],'passed');self.assertEqual(Framework.evaluate(format_program(changed['program'])),expected(4))

class ClassProtocolHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Framework(Path(self.tmp.name)/'data',settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.tmp.name)/'control',update_home=Path(self.tmp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
    def close_server(self):self.server.shutdown();self.thread.join(3);self.server.server_close()

    def test_sdk_http_cli_and_pure_source_are_the_same_language(self):
        project=self.client.create_project('class-protocol',source=SOURCE)
        self.assertEqual(self.client.start('class-protocol',project['version'],None,mode='source').result(),expected())
        self.assertEqual(self.client.program('class-protocol')['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
        self.assertEqual(self.client.analyze('new_class(')['signatureHelp']['parameters'][0]['name'],'name')
        result=subprocess.run([sys.executable,'-m','chaos','run','-'],input=SOURCE,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),expected())

    @unittest.skipUnless(sync_playwright,'Playwright unavailable')
    def test_real_browser_hints_structure_format_and_headless_consistency(self):
        project=self.app.get_project('workspace');project.update(source=SOURCE,files={'main.chaos':SOURCE},entry='main.chaos');self.app.save_project(project,from_view='source')
        folder=os.environ.get('CHAOS_CLASS_PROTOCOL_AUDIT');output=Path(folder) if folder else None
        if output:output.mkdir(parents=True,exist_ok=True)
        errors=[];external=[];checks=[]
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
                def run(n):
                    page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runStatus")?.textContent.includes("已完成")&&document.querySelector("#resultPanel")?.textContent.includes("declared")')
                    value=json.loads(page.locator('#resultPanel').inner_text());self.assertEqual(value,expected(n));checks.append(value);return value
                page.goto(self.origin);workbench_view(page,'source');page.wait_for_function('()=>!!window.ChaosIDE?.editor()')
                workbench_button(page,'formatSource');formatted=format_source(SOURCE);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=formatted)
                page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                self.assertTrue(page.locator('.ide-token-function',has_text='__init_subclass__').count())
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString();e.dispatch({selection:{anchor:s.indexOf("new_class(")+10},scrollIntoView:true})}')
                workbench_button(page,'ideSignatureButton');page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("name")');shot('class-signature')
                page.evaluate('()=>{const e=ChaosIDE.editor(),s=e.state.doc.toString();e.dispatch({selection:{anchor:s.indexOf("new_class(")+10},scrollIntoView:true});e.focus()}');page.locator('.cm-editor').press('Escape')
                workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');original=run(2);shot('class-result')
                current=self.client.get_project('workspace');self.assertEqual(self.client.start('workspace',current['version'],None,mode='source').result(),original);self.assertEqual(Framework.evaluate(current['source']),original)
                workbench_view(page,'structure');page.wait_for_function('()=>programDraft?.root?.statements?.[2]?.options')
                target=page.evaluate('programDraft.root.statements[2].options[0].value.id');page.locator('[data-syntax="'+target+'"]').click()
                field=page.locator('#syntaxFields .syntax-field').filter(has=page.locator('label',has_text='value'));field.locator('input').fill('4');field.get_by_role('button',name='应用',exact=True).click();shot('class-properties')
                workbench_button(page,'applyProgram');page.wait_for_function('()=>!syntaxDirty&&!dirty');changed=self.client.get_project('workspace');program=parse_program(changed['source'])
                self.assertEqual(program['root']['statements'][2]['options'][0]['value']['value'],4)
                workbench_view(page,'source');workbench_button(page,'formatSource');readable=format_source(changed['source']);page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=readable)
                self.assertEqual(parse_program(readable)['semanticHash'],program['semanticHash']);workbench_button(page,'saveButton');page.wait_for_function('()=>!dirty&&!saveTask');modified=run(4)
                changed=self.client.get_project('workspace');self.assertEqual(self.client.start('workspace',changed['version'],None,mode='source').result(),modified);self.assertEqual(Framework.evaluate(changed['source']),modified)
                page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runStatus")?.textContent.includes("已完成")');page.evaluate('()=>ChaosIDE.editor().dispatch({selection:{anchor:0},scrollIntoView:true})');page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")');self.assertTrue(page.locator('.ide-token-function',has_text='__init_subclass__').count());shot('class-edited-source')
                editor=page.locator('.cm-content[contenteditable="true"]');editor.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text('let wrapped=class_member(1);wrapped.');page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("编辑中")');page.keyboard.press('Control+Space');page.wait_for_selector('.cm-tooltip-autocomplete');self.assertEqual(set(page.locator('.cm-tooltip-autocomplete .cm-completionLabel').all_text_contents()),{'value','mutable','method','contract'});shot('class-completion');page.keyboard.press('Escape')
                page.evaluate('(text)=>{const e=ChaosIDE.editor();e.dispatch({changes:{from:0,to:e.state.doc.length,insert:text},selection:{anchor:0},scrollIntoView:true})}',readable);page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
                page.set_viewport_size({'width':390,'height':900});self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'));shot('class-mobile')
                self.assertEqual(errors,[]);self.assertEqual(external,[])
                if output:
                    (output/'browser-result.json').write_text(json.dumps({'status':'passed','sourceRoot':str(ROOT),'checks':checks,'sourceStructureSDKHTTPPureEqual':True,'signature':True,'completion':True,'formatSemanticHash':parse_program(formatted)['semanticHash'],'editedSemanticHash':program['semanticHash'],'mobileNoOverflow':True,'errors':errors,'externalRequests':external,'sourceHashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in OWNED+['chaos/language.py','chaos/objects.py','chaos/class_creation.py']},'screenshots':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.png')}},indent=2)+'\n')
            finally:browser.close()


if __name__=='__main__':unittest.main()
