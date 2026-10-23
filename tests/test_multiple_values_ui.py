"""The visible editor operates existing call IR and the same completion evaluator."""
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
from tests.test_multiple_values_surfaces import SOURCE, EXPECTED


class MultipleValueUIControls(unittest.TestCase):
    def test_actual_call_ir_controls_edit_primary_without_special_ui_semantics(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Node.js is required for structural UI acceptance')
        script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){throw new Error(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(fixture.root+'/web/language.js','utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context);
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};const literal=programDraft.root.statements[2].value.arguments[0];syntaxSelection=literal.id;renderSyntaxFields()');
assert.equal(run('syntaxContext(syntaxIndex().get(syntaxSelection))'),'arguments');
run('const field=$("#syntaxFields").children.find(x=>x.children[0]?.textContent==="value");const controls=field.children[1];controls.children[0].value="42";controls.children[1].onclick()');
assert.equal(run('programDraft.root.statements[2].value.arguments[0].value'),42);
assert.equal(run('programDraft.root.statements[3].value.callee.index.value'),'multiple_value_list');
console.log(JSON.stringify({status:'passed',program:JSON.parse(run('JSON.stringify(programDraft)'))}));'''
        fixture={'root':str(Path(__file__).resolve().parents[1]),'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(SOURCE)}
        result=subprocess.run([node,'-e',script],input=json.dumps(fixture),text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout)
        self.assertEqual(report['status'],'passed')
        self.assertEqual(Framework.evaluate(format_program(report['program'])),[43,EXPECTED[1]])


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class MultipleValueUI(unittest.TestCase):
    setUp=workbench.WorkbenchUI.setUp
    tearDown=workbench.WorkbenchUI.tearDown
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state

    def test_real_completion_signature_structure_edit_run_and_mobile(self):
        self.draft('return_v');self.page.keyboard.press('End');self.page.keyboard.press('Control+Space')
        self.page.locator('.cm-completionLabel').filter(has_text=re.compile('^return_values$')).click()
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'),'return_values')
        self.draft('multiple_value_bind(2,');self.page.keyboard.press('End');self.command('edit.signature')
        self.page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("producer")')
        self.assertIn('producer: Any',self.page.locator('#ideSignatureHelp').text_content())
        self.draft(SOURCE);self.command('edit.format')
        self.page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>3')
        self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        self.page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
        def run(expected):
            self.page.locator('#runButton').click()
            self.page.wait_for_function('(expected)=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()===JSON.stringify(expected,null,2)',arg=expected)
            self.assertEqual(self.app.get_run(self.page.evaluate('currentRun'))['output'],expected)
        run(EXPECTED)
        self.command('view.structure');self.page.wait_for_function('()=>programDraft?.root.statements[2].value.kind==="call"')
        target=self.page.evaluate('programDraft.root.statements[2].value.arguments[0].id')
        self.page.locator('[data-syntax="'+target+'"]').click()
        field=self.page.locator('#syntaxFields .syntax-field').filter(has=self.page.locator('label',has_text='value'))
        field.locator('input').fill('42');field.get_by_role('button',name='应用',exact=True).click()
        self.page.locator('#applyProgram').click();self.page.wait_for_function('()=>!syntaxDirty&&!dirty')
        saved=self.app.get_project('workbench');self.assertEqual(saved['program']['semanticHash'],parse_program(saved['source'])['semanticHash'])
        self.command('view.source');self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source',arg=saved['source'])
        run([43,EXPECTED[1]])
        self.page.reload();self.page.wait_for_function('(source)=>window.ChaosIDE?.editor()?.state.doc.toString()===source',arg=saved['source'])
        terminal_source='var calls=0;async fn work()->Int{calls+=1;return_values(42,"task")};fn* stream()->Int{calls+=1;yield 7;return_values(42,"generator")};let task=work();await task;let g=stream();next(g);next(g);[multiple_value_list(task),multiple_value_list(g),multiple_value_list(g),calls]'
        self.draft(terminal_source);self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        terminal_expected=[[42,'task'],[42,'generator'],[42,'generator'],2]
        run(terminal_expected)
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        output=os.environ.get('CHAOS_MULTIPLE_VALUES_AUDIT')
        if output:
            root=Path(output);root.mkdir(parents=True,exist_ok=True);shot=root/'multiple-values-mobile.png'
            self.page.screenshot(path=str(shot),full_page=True)
            (root/'browser-result.json').write_text(json.dumps({'status':'passed','sourceRoot':str(Path(__file__).resolve().parents[1]),'nativeCompletion':True,'producerSignature':True,'existingCallIREdit':True,'sourceIRHashEqual':True,'savedVersion':saved['version'],'outputs':[EXPECTED,[43,EXPECTED[1]],terminal_expected],'completedTaskGeneratorNoReplay':True,'mobileNoOverflow':True,'errors':self.errors,'externalHTTP':self.external,'screenshots':{shot.name:hashlib.sha256(shot.read_bytes()).hexdigest()}},ensure_ascii=False,indent=2)+'\n')

