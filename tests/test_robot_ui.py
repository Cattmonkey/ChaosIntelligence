"""Actual generic editor controls operate native Robot programs and static inventory."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest

from chaos import Framework, Settings
from chaos.program import SHAPES, format_program, parse_program, templates
from chaos.server import make_server
from tests import test_workbench_ui as workbench
from tests.test_robot_surfaces import SOURCE, EXPECTED, registered


class RobotUIControls(unittest.TestCase):
    def test_real_node_generic_scalar_controls_edit_native_policy(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Node.js required for generic structure acceptance')
        script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){throw new Error(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(fixture.root+'/web/language.js','utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);const run=s=>vm.runInContext(s,context);
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};function find(n){if(n&&typeof n==="object"){if(n.kind==="literal"&&n.value?.$number==="0.2")return n;for(const v of Object.values(n)){const found=find(v);if(found)return found}}};const literal=find(programDraft.root);syntaxSelection=literal.id;renderSyntaxFields()');
run('const field=$("#syntaxFields").children.find(x=>x.children[0]?.textContent==="value");const controls=field.children[1];controls.children[0].value="0.3";controls.children[1].onclick()');assert.equal(run('literal.value.$number'),'0.3');console.log(run('JSON.stringify(programDraft)'));'''
        fixture={'root':str(Path(__file__).resolve().parents[1]),'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run([node,'-e',script],input=json.dumps(fixture),text=True,capture_output=True,timeout=15);self.assertEqual(result.returncode,0,result.stderr)
        registry,adapters=registered()
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            project=app.create_project('edited',source=format_program(json.loads(result.stdout)),capabilities=['robot.control']);value=app.start(project['id'],project['version'],None).result(8)
        self.assertEqual(value,dict(EXPECTED,channels={'left':0.3,'right':0.4}));self.assertEqual(adapters[0].actions[0]['channels'],value['channels'])


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class RobotUI(unittest.TestCase):
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state
    tearDown=workbench.WorkbenchUI.tearDown

    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.tmp=tempfile.TemporaryDirectory(prefix='chaos-robot-ui-');root=Path(self.tmp.name);registry,self.adapters=registered();self.app=Framework(root/'runtime.sqlite',settings=Settings(':memory:'),registry=registry);self.source=SOURCE
        self.app.create_project('workbench',source=SOURCE,capabilities=[]);self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=root/'control',update_home=root/'updates');self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.pw=sync_playwright().start();self.browser=self.pw.chromium.launch(headless=True);self.context=self.browser.new_context(viewport={'width':1440,'height':1000});self.page=self.context.new_page();self.page.set_default_timeout(10000);self.errors=[];self.external=[];self.checks=[];self.screenshots={};self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.context.route('http**://**',lambda route:route.continue_()if route.request.url.startswith(self.origin+'/')else(self.external.append(route.request.url),route.abort()));self.page.goto(self.origin+'/?project=workbench');self.page.wait_for_function('()=>window.ChaosWorkbench&&window.ChaosIDE?.editor()&&project?.id==="workbench"');self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=SOURCE)

    def test_completion_receiver_signature_static_inventory_permission_and_generic_ir_run(self):
        self.draft('robot_ob');self.page.keyboard.press('End');self.page.keyboard.press('Control+Space');self.page.locator('.cm-completionLabel').filter(has_text=re.compile('^robot_observe$')).click();self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'),'robot_observe')
        self.draft('fn control(robot:Robot,command:RobotCommand){robot(');self.page.keyboard.press('End');self.command('edit.signature');self.page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("command")');self.assertIn('command: RobotCommand',self.page.locator('#ideSignatureHelp').text_content());self.assertEqual(self.adapters,[])
        self.draft(SOURCE);self.command('project.capabilities');self.page.wait_for_function('()=>!document.querySelector("#wbPermissionApply").disabled');self.assertIn('mobile',self.page.locator('#wbPermissionDevices').text_content());self.assertIn('Robot',self.page.locator('#wbPermissionDevices').text_content());self.assertEqual(self.adapters,[]);self.assertEqual(self.app.get_project('workbench')['capabilities'],[])
        permission=self.page.locator('input[data-project-capability="robot.control"]');self.assertFalse(permission.is_checked());permission.check();self.page.locator('#wbPermissionApply').click();self.page.locator('#wbPermissions').wait_for(state='hidden');self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        def run(expected):
            self.page.locator('#runButton').click();self.page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=expected);self.assertEqual(self.app.get_run(self.page.evaluate('currentRun'))['output'],expected)
        run(EXPECTED);self.assertEqual(len(self.adapters[0].actions),1);self.command('view.structure');self.page.wait_for_function('()=>programDraft?.root.statements.length===3');target=self.page.evaluate('()=>{function find(n){if(n&&typeof n==="object"){if(n.kind==="literal"&&n.value?.$number==="0.2")return n.id;for(const v of Object.values(n)){const id=find(v);if(id)return id}}}return find(programDraft.root)}');self.page.locator('[data-syntax="'+target+'"]').click();field=self.page.locator('#syntaxFields .syntax-field').filter(has=self.page.locator('label',has_text='value'));field.locator('input').fill('0.3');field.get_by_role('button',name='应用',exact=True).click();self.page.locator('#applyProgram').click();self.page.wait_for_function('()=>!syntaxDirty&&!dirty');saved=self.app.get_project('workbench');self.assertEqual(saved['program']['semanticHash'],parse_program(saved['source'])['semanticHash']);self.command('view.source');self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=saved['source']);expected=dict(EXPECTED,channels={'left':0.3,'right':0.4});run(expected);self.assertEqual(self.adapters[-1].actions[0]['channels'],expected['channels']);self.assertEqual(len(self.adapters),2)
        self.page.set_viewport_size({'width':390,'height':844});self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'));self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        output=os.environ.get('CHAOS_ROBOT_AUDIT')
        if output:
            root=Path(output);root.mkdir(parents=True,exist_ok=True);shot=root/'native-robot-mobile.png';self.page.screenshot(path=str(shot),full_page=True);(root/'browser-result.json').write_text(json.dumps({'passed':True,'sourceRoot':str(Path(__file__).resolve().parents[1]),'completion':True,'nativeReceiverSignature':True,'staticInventoryDidNotOpen':True,'permissionNotDefaultGranted':True,'explicitPermissionSave':True,'genericIREditExecutedOncePerRun':True,'sourceIRHashEqual':True,'targetVerified':False,'mobileNoOverflow':True,'outputs':[EXPECTED,expected],'errors':self.errors,'externalHTTP':self.external,'screenshotSha256':hashlib.sha256(shot.read_bytes()).hexdigest()},indent=2)+'\n')
