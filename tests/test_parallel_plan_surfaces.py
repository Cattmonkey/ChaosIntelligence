"""Plan concurrency stays versioned across authoring, source, hints and live UI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Framework, Settings
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, LanguageError, Parser
from chaos.mode_schema import PLAN_MAX_CONCURRENCY, plan_definition_schema
from chaos.modes import definition_hash, mode_source_definition, validate_definition
from chaos.openapi import specification
from chaos.program import format_program, parse_program, plain
from chaos.source_format import format_source

ROOT = Path(__file__).resolve().parent.parent
PLAN = {'kind':'plan','name':'parallel work','max_concurrency':2,'steps':[
    {'id':'left','call':'work'},{'id':'right','call':'work'}]}
SOURCE = '''async fn work(ctx){await sleep(2);ctx.input};
let template=plan([{id:"left",call:"work"},{id:"right",call:"work"}],name="parallel work",max_concurrency=2);
let saved=await load_mode("parallel");await gather([saved(21,{work:work}),saved(42,{work:work})])'''


class ParallelPlanSurfaces(unittest.TestCase):
    def test_shared_plan_schema_documents_bound_and_all_authoring_paths(self):
        schema=plan_definition_schema();concurrency=schema['properties']['max_concurrency']
        self.assertEqual((concurrency['type'],concurrency['minimum'],concurrency['maximum'],concurrency['default']),('integer',1,PLAN_MAX_CONCURRENCY,1))
        self.assertNotIn('max_concurrency',schema['required'])
        public=specification()['components']['schemas']
        self.assertEqual(public['PlanDefinition'],schema)
        for name in ('ModeArtifact','ModeSave','ModeValidation','ValidatedMode'):
            self.assertEqual(public[name]['properties']['definition'],{'$ref':'#/components/schemas/ModeDefinition'})
        for invalid in (True,False,0,129,2.5,'2',None):
            with self.subTest(invalid=invalid),self.assertRaises(LanguageError):validate_definition(dict(PLAN,max_concurrency=invalid))
        schema['properties']['max_concurrency']['maximum']=999
        self.assertEqual(plan_definition_schema()['properties']['max_concurrency']['maximum'],128)

    def test_persistent_versions_keep_concurrency_hash_and_source_semantics(self):
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            app.create_project('parallel',source='null',capabilities=[])
            initial=app.save_mode('parallel','work',PLAN)
            serial=app.save_mode('parallel','work',dict(PLAN,max_concurrency=1),revision=initial['revision'])
            self.assertEqual(app.mode('parallel','work',revision=1)['definition']['max_concurrency'],2)
            self.assertNotIn('max_concurrency',serial['definition'])
            legacy=dict(PLAN);del legacy['max_concurrency']
            self.assertEqual(definition_hash(legacy),definition_hash(serial['definition']))
            self.assertNotEqual(definition_hash(PLAN),definition_hash(serial['definition']))
            for revision in (1,2):
                exported=app.mode_source('parallel','work',revision=revision,format='value')
                result=Evaluator().run('import "saved.chaos" as M;mode_info(M.saved_mode)',files={'saved.chaos':exported['source']})
                self.assertEqual(result,app.mode('parallel','work',revision=revision)['definition'])
                self.assertEqual(definition_hash(result),exported['definitionHash'])

    def test_constructor_signature_alias_tooltip_and_types_are_inert(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('editor must not evaluate')):
            info=builtin_symbols()['plan'];help=analyze('plan([],max_concurrency=')['signatureHelp']
            alias=analyze('let build=plan;build([],max_concurrency=')['signatureHelp']
            rendered=render_code('let p=plan([{id:"a",call:"work"}],max_concurrency=2);','chaos')
        self.assertEqual(info.parameters,[('steps','List','positional',False),('name','String','positional',True),('max_concurrency','Int','positional',True)])
        for value in (help,alias):
            self.assertEqual(value['activeParameter'],2)
            self.assertEqual(value['parameters'][2]['type'],'Int')
            self.assertIn('max_concurrency: Int = 1',value['label'])
        self.assertIn('max_concurrency: Int = 1',rendered)
        self.assertIn('chaos-token-function',rendered)
        self.assertTrue(any(row['code']=='type_mismatch' for row in analyze('plan([],max_concurrency=true)')['diagnostics']))

    def test_formatting_and_export_roundtrip_remain_readable_and_semantic(self):
        source='// parallel ready steps\nlet p=plan([{id:"a",call:"work"},{id:"b",call:"work"}],name="named plan",max_concurrency=2);mode_info(p)'
        formatted=format_source(source,line_width=50)
        self.assertGreater(formatted.count('\n'),4)
        self.assertIn('max_concurrency = 2',formatted)
        self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
        self.assertEqual(format_source(formatted,line_width=50),formatted)
        self.assertEqual(Evaluator().run(formatted)['max_concurrency'],2)
        exported=mode_source_definition(PLAN)
        self.assertGreater(exported.count('\n'),4)
        self.assertEqual(parse_program(exported)['semanticHash'],parse_program(format_program(parse_program(exported)))['semanticHash'])
        self.assertIn('chaos-token-function',render_code(exported,'chaos'))

    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_concurrent_invocation_controls_and_draft_races(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/parallel_plan_controls.cjs'),str(ROOT)],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')

    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_real_browser_parallel_plan_authoring_hints_export_and_live_runs(self):
        node=shutil.which('node')
        available=subprocess.run([node,'-e',"require.resolve('playwright')"],capture_output=True,cwd=ROOT,timeout=10)
        if available.returncode:self.skipTest('optional Node Playwright extra unavailable')
        from chaos.server import make_server
        with tempfile.TemporaryDirectory(prefix='chaos-parallel-browser-') as folder, Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('parallel',source=SOURCE,capabilities=[])
            app.save_mode('parallel','parallel',PLAN)
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port)
            script=r"""
const {command:workbenchCommand,button:workbenchButton,view:workbenchView}=require(process.cwd()+'/tests/workbench_actions.cjs');
const {chromium}=require('playwright'),assert=require('assert'),fs=require('fs');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const context=await browser.newContext({viewport:{width:1440,height:1080}}),errors=[],external=[],checks=[];
 const out=process.env.CHAOS_PARALLEL_AUDIT;if(out)fs.mkdirSync(out,{recursive:true});
 await context.route('http**://**',route=>{if(route.request().url().startsWith(process.env.PARALLEL_ORIGIN+'/'))return route.continue();external.push(route.request().url());return route.abort();});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
 const shot=async name=>{if(out)await page.screenshot({path:out+'/'+name+'.png',fullPage:true})};
 await page.goto(process.env.PARALLEL_ORIGIN+'/?project=parallel');await page.locator('.cm-editor').waitFor({state:'attached'});await workbenchView(page,'source');
 await workbenchButton(page,'formatSource');await page.waitForFunction(()=>ChaosIDE.editor().state.doc.lines>8);await workbenchButton(page,'saveButton');await page.waitForFunction(()=>!dirty&&!saveTask);
 await page.waitForFunction(()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新'));
 assert(await page.locator('.ide-token-function').filter({hasText:/^plan$/}).count()>0);
 await page.evaluate(()=>{const editor=ChaosIDE.editor(),source=editor.state.doc.toString();editor.dispatch({selection:{anchor:source.indexOf('max_concurrency')+'max_concurrency'.length+3}});});
 await workbenchButton(page,'ideSignatureButton');await page.waitForFunction(()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('max_concurrency'));
 assert((await page.locator('#ideSignatureHelp').innerText()).includes('max_concurrency: Int = 1'));await shot('parallel-signature-format');checks.push('source formatting, typed max_concurrency hint and semantic function highlight');
 await workbenchCommand(page,'modes');await page.locator('.modes-list .mode-card').filter({hasText:'parallel'}).click();await page.locator('.modes-detail').getByRole('button',{name:'编辑定义',exact:true}).click();
 const editor=page.getByRole('dialog',{name:'模式定义编辑器'}),concurrency=editor.getByRole('spinbutton',{name:'最大并发步骤'});
 assert.equal(await concurrency.inputValue(),'2');await concurrency.fill('129');await editor.getByRole('button',{name:'验证与预览',exact:true}).click();assert.equal(await concurrency.evaluate(el=>el.checkValidity()),false);await concurrency.fill('3');
 let receiveSave,releaseSave;const pendingSave=new Promise(resolve=>receiveSave=resolve),release=new Promise(resolve=>releaseSave=resolve);
 await page.route('**/api/v1/projects/parallel/modes/parallel',async route=>{if(route.request().method()!=='PUT')return route.fallback();const response=await route.fetch();receiveSave();await release;return route.fulfill({response});},{times:1});
 await editor.getByRole('button',{name:'保存模式版本',exact:true}).click();await pendingSave;await concurrency.fill('4');releaseSave();
 await page.waitForFunction(()=>document.querySelector('.mode-draft-status')?.textContent.includes('新输入仍是草稿'));assert.equal(await concurrency.inputValue(),'4');checks.push('late save response preserves newly typed concurrency');
 await concurrency.fill('2');await editor.getByRole('button',{name:'保存模式版本',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.mode-draft-status')?.textContent.includes('已保存模式 r3'));
 await editor.getByRole('button',{name:'验证与预览',exact:true}).click();await editor.locator('.mode-source-preview[data-highlight="ready"]').waitFor();const preview=await editor.locator('.mode-source-preview code').innerText();assert(preview.includes('max_concurrency'));assert(preview.split('\n').length>6);assert(await editor.locator('.chaos-token-function').filter({hasText:'mode_from'}).count()>0);await shot('parallel-concurrency-editor');await editor.locator('.mode-source-preview').scrollIntoViewIfNeeded();await shot('parallel-saved-highlighted-preview');
 await editor.getByRole('button',{name:'关闭并保留草稿',exact:true}).click();
 await page.reload();await page.locator('.cm-editor').waitFor({state:'attached'});await workbenchView(page,'source');await workbenchCommand(page,'modes');await page.locator('.modes-list .mode-card').filter({hasText:'parallel'}).click();
 await page.waitForFunction(()=>document.querySelector('.modes-detail')?.textContent.includes('最大并发步骤 2'));await page.locator('.modes-detail').getByRole('button',{name:'导出源码',exact:true}).click();
 const exported=page.getByRole('dialog',{name:'模式源码导出'});await exported.locator('[data-highlight="ready"]').waitFor();const raw=await exported.locator('pre code').innerText();assert.equal(raw,preview);assert(await exported.locator('.chaos-token-function').filter({hasText:'mode_from'}).count()>0);checks.push('versioned concurrency reload plus multiline shared highlighted preview and identical source export');await shot('parallel-export');await exported.getByRole('button',{name:'关闭',exact:true}).click();
 await page.locator('#runButton').click();await page.waitForFunction(()=>document.querySelectorAll('.mode-execution[data-status="running"]').length===2&&document.querySelectorAll('.mode-execution-steps [data-status="running"]').length===4);
 const active=await page.evaluate(()=>ChaosModes.state.executions);assert.equal(new Set(active.map(row=>row.executionId)).size,2);assert.equal(new Set(active.map(row=>row.definitionHash)).size,1);assert(active.every(row=>row.maxConcurrency===2));assert.equal(new Set(active.flatMap(row=>row.steps.map(step=>step.taskId))).size,4);checks.push('two actual same-definition Plan invocations display four separate concurrent step tasks');await page.locator('.mode-executions').scrollIntoViewIfNeeded();await shot('parallel-live-invocations');
 await page.waitForFunction(()=>!busy&&!launching&&document.querySelector('#resultPanel')?.textContent.trim()===JSON.stringify([{left:21,right:21},{left:42,right:42}],null,2));
 await page.waitForFunction(()=>document.querySelectorAll('.mode-execution[data-status="completed"]').length===2);assert.equal(await page.locator('.mode-execution-steps [data-status="completed"]').count(),4);await shot('parallel-completed-invocations');checks.push('actual run completes and each invocation retains distinct finished steps');
 assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(external,[]);const report={status:'passed',checks,errors,external,executions:await page.evaluate(()=>ChaosModes.state.executions)};if(out)fs.writeFileSync(out+'/parallel-browser-result.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
 }finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1});
"""
            try:
                result=subprocess.run([node,'-e',script],text=True,capture_output=True,cwd=ROOT,env=dict(os.environ,PARALLEL_ORIGIN=origin),timeout=100)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'],'passed')
                self.assertEqual(app.mode('parallel','parallel',revision=2)['definition']['max_concurrency'],3)
                self.assertEqual(app.mode('parallel','parallel',revision=3)['definition']['max_concurrency'],2)
                self.assertEqual(app.mode('parallel','parallel')['revision'],3)
            finally:server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
