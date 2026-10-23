"""Visible editor and actual structural controls retain generic call IR."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from chaos import Framework
from chaos.program import SHAPES, format_program, parse_program, templates
from tests import test_workbench_ui as workbench
from tests.test_control_surfaces import SOURCE, EXPECTED


class ControlUIControls(unittest.TestCase):
    def test_real_node_call_controls_edit_payload_without_control_name_rules(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Node.js required for structural UI acceptance')
        script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){throw new Error(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(fixture.root+'/web/language.js','utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);const run=s=>vm.runInContext(s,context);
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};function find(n){if(n&&typeof n==="object"){if(n.kind==="literal"&&n.value===41)return n;for(const value of Object.values(n)){const result=find(value);if(result)return result}}};const literal=find(programDraft.root);syntaxSelection=literal.id;renderSyntaxFields()');
assert.equal(run('syntaxContext(syntaxIndex().get(syntaxSelection))'),'arguments');
run('const field=$("#syntaxFields").children.find(x=>x.children[0]?.textContent==="value");const controls=field.children[1];controls.children[0].value="42";controls.children[1].onclick()');assert.equal(run('literal.value'),42);
console.log(JSON.stringify({status:'passed',program:JSON.parse(run('JSON.stringify(programDraft)'))}));'''
        fixture={'root':str(Path(__file__).resolve().parents[1]),'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run([node,'-e',script],input=json.dumps(fixture),text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout)
        self.assertEqual(report['status'],'passed');self.assertEqual(Framework.evaluate(format_program(report['program'])),[[43,{'stage':'report'}],EXPECTED[1],False])


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ControlUI(unittest.TestCase):
    setUp=workbench.WorkbenchUI.setUp
    tearDown=workbench.WorkbenchUI.tearDown
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state

    def test_actual_completion_signature_structure_runtime_scope_and_mobile(self):
        self.draft('prompt_sc');self.page.keyboard.press('End');self.page.keyboard.press('Control+Space')
        self.page.locator('.cm-completionLabel').filter(has_text=re.compile('^prompt_scope$')).click()
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'),'prompt_scope')
        self.draft('call_prompt(input,');self.page.keyboard.press('End');self.command('edit.signature')
        self.page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("body")')
        self.assertIn('body: Function',self.page.locator('#ideSignatureHelp').text_content())
        self.draft(SOURCE);self.command('edit.format');self.page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>3')
        self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        def run(expected):
            self.page.locator('#runButton').click();self.page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=expected)
            self.assertEqual(self.app.get_run(self.page.evaluate('currentRun'))['output'],expected)
        run(EXPECTED);self.command('view.structure');self.page.wait_for_function('()=>programDraft?.root.statements.length===6')
        target=self.page.evaluate('()=>{function find(n){if(n&&typeof n==="object"){if(n.kind==="literal"&&n.value===41)return n.id;for(const v of Object.values(n)){const id=find(v);if(id)return id}}}return find(programDraft.root)}')
        self.page.locator('[data-syntax="'+target+'"]').click();field=self.page.locator('#syntaxFields .syntax-field').filter(has=self.page.locator('label',has_text='value'))
        field.locator('input').fill('42');field.get_by_role('button',name='应用',exact=True).click()
        self.page.locator('#applyProgram').click();self.page.wait_for_function('()=>!syntaxDirty&&!dirty');saved=self.app.get_project('workbench')
        self.assertEqual(saved['program']['semanticHash'],parse_program(saved['source'])['semanticHash'])
        self.command('view.source');self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=saved['source']);run([[43,{'stage':'report'}],EXPECTED[1],False])
        stream='let tag=prompt_tag("stream");var scope=null;fn* stream(){let own=prompt_scope(tag);scope=own;with own as entered{yield prompt_available(tag);abort_prompt(tag,42,"stream")}};let g=stream();let first=next(g);let outside=prompt_available(tag);let during=scope.active;next(g);[first.value,outside,during,multiple_value_list(g),scope.active]'
        self.draft(stream);self.page.locator('#saveButton').click()
        try:self.page.wait_for_function('()=>!dirty&&!saveTask')
        except Exception as error:
            details=self.page.evaluate('()=>({dirty,saveTask:!!saveTask,syntaxDirty,editFrom,source:project.source,editor:ChaosIDE.editor().state.doc.toString(),notice:document.querySelector("#toast")?.textContent,body:document.body.innerText})')
            raise AssertionError('stream save state: '+json.dumps(details,ensure_ascii=False)) from error
        stream_expected=[True,False,True,[42,'stream'],False];run(stream_expected)
        self.page.set_viewport_size({'width':390,'height':844});self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        output=os.environ.get('CHAOS_CONTROL_PROMPTS_AUDIT')
        if output:
            root=Path(output);root.mkdir(parents=True,exist_ok=True);shot=root/'control-prompts-mobile.png';self.page.screenshot(path=str(shot),full_page=True)
            (root/'browser-result.json').write_text(json.dumps({'passed':True,'sourceRoot':str(Path(__file__).resolve().parents[1]),'nativeScopeCompletion':True,'bodySignature':True,'existingCallIREdit':True,'sourceIRHashEqual':True,'scopeAcrossYield':True,'consumerTargetUnavailable':True,'mobileNoOverflow':True,'outputs':[EXPECTED,[[43,{'stage':'report'}],EXPECTED[1],False],stream_expected],'errors':self.errors,'externalHTTP':self.external,'screenshots':{shot.name:hashlib.sha256(shot.read_bytes()).hexdigest()}},ensure_ascii=False,indent=2)+'\n')

