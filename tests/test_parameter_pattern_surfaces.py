"""Parameter patterns share IR, source layout and inert editor analysis."""
import copy
from html import unescape
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos.code_presentation import render_code
from chaos.ide_language import Analysis, analyze
from chaos.language import Evaluator, LanguageError, Parser
from chaos.program import SHAPES, children, decode_program, format_program, parse_program, patch_program, plain, project_program, templates
from chaos.source_format import format_source

ROOT = Path(__file__).resolve().parent.parent


def units(value):
    return len(value.encode('utf-16-le')) // 2


class ParameterPatternSurfaces(unittest.TestCase):
    def checked(self, source, position=None):
        result = analyze(source, position=position)
        self.assertTrue(result['complete'], result['diagnostics'])
        self.assertFalse(result['diagnostics'], result['diagnostics'])
        return result

    def test_ir_all_flavors_and_parameter_modes_roundtrip(self):
        sources = [
            'fn f(record as {x:value,..rest}:Record={x:20,y:22}){[record,value,rest]};f()',
            'fn f(*items as [first,..tail],only as {x:second}:Record,**options as {flag:flag,..extra}){[first,tail,second,flag,extra]};f(20,21,only={x:22},flag=true)',
            'fn* f(items as [first,..tail]:List){yield first};collect(f([42]))',
            'async fn f(record as {x:value}:Record)->Int{value};await f({x:42})',
            'async fn* f(record as {x:value}:Record){yield value};await acollect(f({x:42}))',
            'fn f(value as alias:Int){alias};f(42)',
            'fn f(value as 42){value};f(42)',
            'fn f(value as _){value};f(42)',
        ]
        for source in sources:
            with self.subTest(source=source):
                program = parse_program(source)
                self.assertEqual(plain(decode_program(program)), plain(Parser(source).program()))
                self.assertEqual(parse_program(format_program(program))['semanticHash'], program['semanticHash'])
                self.assertEqual(Evaluator().run(format_program(program)), Evaluator().run(source))
                fn = program['root']['statements'][0]['function']
                self.assertTrue(all('pattern' in row for row in fn['parameters']))
                self.assertTrue(any(slot.endswith('.pattern') for slot, _ in children(fn)))

    def test_legacy_ir_shapes_and_semantic_hash_are_unchanged(self):
        program = parse_program('fn f(x:Int,y:Int=2,*rest,flag:Bool=true,**options){x};f(1)')
        self.assertEqual(program['semanticHash'],'a52cfd3f4f1babd94a2883ba86b1e98f65bffa80ac381d556985af6e564000e5')
        rows = program['root']['statements'][0]['function']['parameters']
        self.assertEqual(set(rows[0]), {'name','type'})
        self.assertTrue(all('pattern' not in row for row in rows))
        self.assertEqual([len(p) for p in decode_program(program)[1][0][2][1]], [2,4,4,4,4])
        self.assertEqual(parse_program(format_program(program))['semanticHash'],program['semanticHash'])

    def test_pattern_can_extend_compact_legacy_parameter_ir(self):
        program=parse_program('fn f(slot as [leaf]:List){leaf};f([42])')
        row=program['root']['statements'][0]['function']['parameters'][0]
        del row['mode'];del row['default']
        self.assertEqual(len(decode_program(program)[1][0][2][1][0]),5)
        self.assertEqual(Evaluator().run(format_program(program)),42)

    def test_pattern_ir_validation_rejects_malformed_children_and_collisions(self):
        original = parse_program('fn f(value as [left,right]:List){left+right}')
        for mode in ('expression', 'duplicate', 'slot_collision', 'null', 'missing_mode'):
            bad = copy.deepcopy(original)
            row = bad['root']['statements'][0]['function']['parameters'][0]
            if mode == 'expression': row['pattern'] = templates()['list']
            elif mode == 'duplicate': row['pattern']['items'][1]['name'] = 'left'
            elif mode == 'slot_collision': row['pattern']['items'][1]['name'] = 'value'
            elif mode == 'null': row['pattern'] = None
            else: del row['mode']
            with self.subTest(mode=mode), self.assertRaises(LanguageError): decode_program(bad)

    def test_pattern_nodes_are_projected_and_patchable(self):
        program = parse_program('fn f(value as {x:42,.._}:Record){value};f({x:42,y:1})')
        pattern = program['root']['statements'][0]['function']['parameters'][0]['pattern']
        self.assertTrue(any(edge['slot']=='parameters.0.pattern' for edge in project_program(program)['edges']))
        changed = patch_program(program,[{'op':'set','id':pattern['fields'][0]['value']['id'],'field':'value','value':43}])
        self.assertIn('43',format_program(changed))
        self.assertNotEqual(changed['semanticHash'],program['semanticHash'])
        self.assertEqual(next(row for row in project_program(changed)['nodes'] if row['id']==pattern['id'])['kind'],'p_record')

    def test_formatter_retains_comments_spelling_and_ast(self):
        source = '// 🙂 参数\nfn f(whole as[head,{"field":value,..extra},..tail]:List= [1,{"field":2},3], // default\n *,options as{flag:flag,.._}:Record={flag:true}){[whole,head,value,extra,tail,flag]};f()'
        formatted = format_source(source,line_width=40)
        self.assertIn('whole as [',formatted)
        self.assertIn('// 🙂 参数',formatted)
        self.assertIn('// default',formatted)
        self.assertIn('"field"',formatted)
        self.assertEqual(plain(Parser(formatted).program()),plain(Parser(source).program()))
        self.assertEqual(format_source(formatted,line_width=40),formatted)

    def test_leaf_semantics_actual_spans_types_and_definition_identity(self):
        source='// 🙂\nfn read(record as {x:value,nested:[first,..tail],..rest}:Record){[record,value,first,tail,rest]};read({x:1,nested:[2,3]})'
        at=source.index('[record,value')+len('[record,va')
        result=self.checked(source,units(source[:at]))
        symbols={item['name']:item for item in result['symbols']}
        self.assertEqual({name:symbols[name]['type'] for name in ('record','value','first','tail','rest')},{'record':'Record','value':'Any','first':'Any','tail':'List','rest':'Record'})
        self.assertTrue(all(symbols[name]['kind']=='parameter' for name in ('record','value','first','tail','rest')))
        self.assertEqual(symbols['value']['from'],units(source[:source.index(':value')+1]))
        self.assertEqual(result['hover']['contents'],'value: Any · 解构参数')
        self.assertIn('value',{item['label'] for item in result['completions']})
        analysis=Analysis(source,'main.chaos',{},'main.chaos',units(source[:at]));analysis.run()
        reference=next(info for start,end,info in analysis.references if start==source.index('value,first'))
        self.assertEqual(reference.start,source.index(':value')+1)
        self.assertFalse(reference.mutable)
        raw=source.encode('utf-16-le')
        tokens=[(raw[token['from']*2:token['to']*2].decode('utf-16-le'),token['type']) for token in result['tokens']]
        self.assertIn(('as','keyword'),tokens)
        self.assertIn(('nested','property'),tokens)
        self.assertIn(('Record','type'),tokens)
        self.assertEqual([item['code'] for item in analyze('fn f(slot as [leaf]){leaf=2}')['diagnostics']],['immutable_binding'])
        self.assertIn('chaos-token-parameter',render_code(source,'chaos'))
        self.assertIn('value: Any · 解构参数',render_code(source,'chaos'))

    def test_defaults_and_constructors_resolve_outer_scope_without_leaks(self):
        source='data Option{Some(value)};let fallback=Some(42);fn read(Some as Some(value)=fallback, next=value){value};value'
        result=analyze(source,position=source.index('next=value')+7)
        self.assertEqual([item['code'] for item in result['diagnostics']],['undefined_name','undefined_name'])
        self.assertNotIn('value',{item['label'] for item in result['completions']})
        constructor=analyze(source,position=source.index('as Some')+4)
        self.assertIn('Some(',constructor['hover']['contents'])
        no_default_inference='fn f(record as {callback:callback}:Record={callback:fn(x:Int)->Int{x}}){callback(42)};f()'
        result=self.checked(no_default_inference,no_default_inference.index('callback(42)')+len('callback('))
        self.assertIsNone(result['signatureHelp'])
        self.assertEqual(next(item['type'] for item in result['symbols'] if item['name']=='callback'),'Any')

    def test_signatures_keywords_receivers_and_incomplete_body_use_public_slots(self):
        source='class Reader{fn read(self,record as {x:value}:Record,*,options as {flag:flag}:Record={flag:true}){value}};Reader().read(record={x:42}, options={flag:false})'
        result=self.checked(source,source.index(' options=')+1)
        help=result['signatureHelp']
        self.assertEqual([p['name'] for p in help['parameters']],['record','options'])
        self.assertTrue({'record=','options='} & {item['label'] for item in result['completions']})
        self.assertNotIn('value=',{item['label'] for item in result['completions']})
        self.assertNotIn('flag=',{item['label'] for item in result['completions']})
        source='fn f(whole as {value:leaf}:Record){lea'
        result=analyze(source,position=len(source))
        self.assertFalse(result['complete'])
        self.assertIn('leaf',{item['label'] for item in result['completions']})

    def test_analysis_and_presentation_never_execute_parameter_expressions(self):
        source='fn f(record as {x:leaf}:Record=tool("forbidden",null)){leaf};f({x:42})'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed')):
            self.checked(source)
            self.assertIn('chaos-token-parameter',render_code(source,'chaos'))


    def test_pattern_signatures_follow_function_aliases_and_safe_code_hints(self):
        source='fn read(payload as {point:[x,y],..rest}:Record={point:[20,22]}){x+y};let alias=read;alias(payload={point:[20,22]})'
        position=source.rindex('alias(')+len('alias(')
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed')):
            result=self.checked(source,position)
            expected='payload as {"point": [x, y], ..rest}: Record = …'
            self.assertIn(expected,result['signatureHelp']['label'])
            self.assertEqual(result['signatureHelp']['parameters'][0]['label'],expected)
            labels={item['label'] for item in result['completions']}
            self.assertIn('payload=',labels)
            self.assertFalse({'x=','y=','rest='}&labels)
            hover=self.checked(source,source.rindex('alias(')+2)['hover']['contents']
            self.assertIn(expected,hover)
            self.assertIn(expected,unescape(render_code(source,'chaos')))
            escaped=render_code('fn f(payload as {"<script>":value}){value};f', 'chaos')
            self.assertIn('&lt;script&gt;',escaped)
            self.assertNotIn('<script>',escaped)

    def test_bound_method_and_constructor_signatures_filter_receiver_patterns(self):
        cases=[
            ('class C{fn read(self as receiver,payload as {x:value}:Record){value}};let alias=C().read;alias(payload={x:42})','alias('),
            ('class C{\n@classmethod\nfn read(cls as receiver,payload as {x:value}:Record){value}};let alias=C.read;alias(payload={x:42})','alias('),
            ('class C{fn __init__(self as receiver,payload as {x:value}:Record){null}};C(payload={x:42})','C('),
        ]
        for source,call in cases:
            with self.subTest(source=source):
                result=self.checked(source,source.rindex(call)+len(call))
                signature=result['signatureHelp']
                self.assertEqual([p['name'] for p in signature['parameters']],['payload'])
                self.assertIn('payload as {"x": value}: Record',signature['label'])
                self.assertEqual(signature['parameters'][0]['label'],'payload as {"x": value}: Record')
                self.assertNotIn('receiver',signature['label'])
                self.assertIn('payload=',{item['label'] for item in result['completions']})
                self.assertNotIn('value=',{item['label'] for item in result['completions']})
                analysis=Analysis(source,'main.chaos',{},'main.chaos',source.rindex(call)+len(call));analysis.run()
                target=analysis.call_context(analysis.environment())[0]
                self.assertEqual(set(target.parameter_patterns),{'payload'})

    def test_signature_labels_cover_all_modes_and_function_flavors(self):
        source='fn f(items as [head,..tail]:List,*args as [first,..others],options as {flag:flag}:Record={flag:true},**kwargs as {x:x,..extra}){head};f([1],2,options={flag:false},x=3)'
        result=self.checked(source,source.rindex('f(')+2)
        labels=[p['label'] for p in result['signatureHelp']['parameters']]
        self.assertEqual(labels,['items as [head, ..tail]: List','*args as [first, ..others]: Any','options as {"flag": flag}: Record = …','**kwargs as {"x": x, ..extra}: Any'])
        for prefix,body in [('fn','value'),('async fn','value'),('fn*','yield value'),('async fn*','yield value')]:
            source=prefix+' f(payload as {x:value}:Record){'+body+'};f({x:42})'
            result=self.checked(source,source.rindex('f(')+2)
            self.assertIn('payload as {"x": value}: Record',result['signatureHelp']['label'])

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_actual_parameter_controls_keep_modes_defaults_and_pattern_nodes(self):
        fixture={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program('fn f(slot as {x:value,..rest}:Record={x:42}){value};f()')}
        script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){throw Error(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync('web/language.js','utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[0].function.id;renderSyntaxFields()');
const descendants=x=>[x,...x.children.flatMap(descendants)];
const controls=()=>descendants(nodes.get('#syntaxFields'));
const row=()=>json('programDraft.root.statements[0].function.parameters[0]');
const button=text=>controls().find(x=>x.tagName==='BUTTON'&&x.textContent===text);
assert(json('syntaxChildren(programDraft.root.statements[0].function).map(([slot])=>slot)').includes('parameters.0.pattern'));
assert.strictEqual(json('newParameter(programDraft.root.statements[0].function.parameters)').name,'value2');
const patternId=row().pattern.id;
run('syntaxSelection=programDraft.root.statements[0].function.parameters[0].pattern.id;renderSyntaxFields()');
let chooser=controls().find(x=>x.tagName==='SELECT');assert(chooser.children.every(x=>x.value.startsWith('p_')));
assert(chooser.children.some(x=>x.value==='p_bind'));
run('syntaxSelection=programDraft.root.statements[0].function.id;renderSyntaxFields()');
button('移除默认值').onclick();assert(row().pattern.id===patternId);assert.strictEqual(row().mode,'positional');assert.strictEqual(row().default,null);
run('renderSyntaxFields()');
let mode=controls().find(x=>x.attributes['aria-label']==='参数 slot 模式');mode.value='varargs';mode.onchange();assert.strictEqual(row().mode,'varargs');assert.strictEqual(row().default,null);assert.strictEqual(row().pattern.id,patternId);
run('renderSyntaxFields()');button('移除解构模式').onclick();assert(!('pattern' in row()));assert.strictEqual(row().mode,'varargs');
run('renderSyntaxFields()');button('添加解构模式').onclick();assert.strictEqual(row().pattern.kind,'p_any');assert.strictEqual(row().mode,'varargs');
run('renderSyntaxFields()');mode=controls().find(x=>x.attributes['aria-label']==='参数 slot 模式');mode.value='positional';mode.onchange();assert.strictEqual(row().mode,'positional');
run('renderSyntaxFields()');button('移除解构模式').onclick();assert.deepStrictEqual(Object.keys(row()).sort(),['name','type']);
console.log(JSON.stringify({status:'passed',checks:16}));
"""
        result=subprocess.run([shutil.which('node'),'-e',script],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')

    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_real_browser_parameter_pattern_edit_format_save_reload_and_run(self):
        node=shutil.which('node')
        available=subprocess.run([node,'-e',"require.resolve('playwright')"],capture_output=True,cwd=ROOT,timeout=10)
        if available.returncode: self.skipTest('optional Node Playwright extra unavailable')
        from chaos import Framework, Settings
        from chaos.server import make_server
        source='fn read(record as {x:value,.._}:Record={x:42}){value};read()'
        with tempfile.TemporaryDirectory(prefix='chaos-parameter-browser-') as folder, Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('parameters',source=source,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port)
            script=r"""
const {command:workbenchCommand,button:workbenchButton,view:workbenchView}=require(process.cwd()+'/tests/workbench_actions.cjs');
const {chromium}=require('playwright'),assert=require('assert'),fs=require('fs');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const context=await browser.newContext({viewport:{width:1400,height:1000}}),errors=[],external=[];
 if(process.env.CHAOS_PARAMETER_AUDIT)fs.mkdirSync(process.env.CHAOS_PARAMETER_AUDIT,{recursive:true});
 await context.route('http**://**',route=>{if(route.request().url().startsWith(process.env.PARAMETER_ORIGIN+'/'))return route.continue();external.push(route.request().url());return route.abort();});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
 await page.goto(process.env.PARAMETER_ORIGIN+'/?project=parameters');await page.locator('.cm-editor').waitFor({state:'attached'});await workbenchView(page,'source');
 await workbenchButton(page,'formatSource');await page.waitForFunction(()=>ChaosIDE.editor().state.doc.lines>1);await workbenchButton(page,'saveButton');await page.waitForFunction(()=>!dirty&&!saveTask);
 await page.waitForFunction(()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新'));
 assert(await page.locator('.ide-token-parameter').filter({hasText:'value'}).count()>=2);
 await page.evaluate(()=>{const editor=ChaosIDE.editor();editor.dispatch({selection:{anchor:editor.state.doc.toString().lastIndexOf('read(')+5}});});await workbenchButton(page,'ideSignatureButton');
 await page.waitForFunction(()=>document.querySelector('#ideSignatureHelp')?.textContent.includes('record as {"x": value, .._}: Record'));
 if(process.env.CHAOS_PARAMETER_AUDIT)await page.screenshot({path:process.env.CHAOS_PARAMETER_AUDIT+'/parameter-signature.png',fullPage:true});
 await page.locator('#runButton').click();await page.waitForFunction(()=>!busy&&!launching&&document.querySelector('#resultPanel')?.textContent.trim()==='42');
 await workbenchView(page,'structure');await page.waitForFunction(()=>programDraft?.root.statements[0].function.parameters[0].pattern);
 const pattern=await page.evaluate(()=>programDraft.root.statements[0].function.parameters[0].pattern.id);
 await page.locator('[data-syntax="'+pattern+'"]').click();
 if(process.env.CHAOS_PARAMETER_AUDIT)await page.screenshot({path:process.env.CHAOS_PARAMETER_AUDIT+'/parameter-pattern-inspector.png',fullPage:true});
 const key=page.locator('#syntaxFields .syntax-field').filter({has:page.locator('label',{hasText:'fields'})}).locator('input').first();await key.fill('y');await key.locator('..').getByRole('button',{name:'应用',exact:true}).click();
 const literal=await page.evaluate(()=>programDraft.root.statements[0].function.parameters[0].default.id);await page.locator('[data-syntax="'+literal+'"]').click();
 const defaultKey=page.locator('#syntaxFields .syntax-field').filter({has:page.locator('label',{hasText:'fields'})}).locator('input').first();await defaultKey.fill('y');await defaultKey.locator('..').getByRole('button',{name:'应用',exact:true}).click();
 await workbenchButton(page,'applyProgram');await page.waitForFunction(()=>!syntaxDirty&&!dirty);
 await workbenchView(page,'source');await page.waitForFunction(()=>ChaosIDE.editor().state.doc.toString().includes('"y": value'));
 await page.reload();await page.locator('.cm-editor').waitFor({state:'attached'});await workbenchView(page,'source');
 assert((await page.evaluate(()=>ChaosIDE.editor().state.doc.toString())).includes('"y": value'));
 await page.locator('#runButton').click();await page.waitForFunction(()=>!busy&&!launching&&document.querySelector('#resultPanel')?.textContent.trim()==='42');
 await page.waitForFunction(()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新'));
 if(process.env.CHAOS_PARAMETER_AUDIT){await page.screenshot({path:process.env.CHAOS_PARAMETER_AUDIT+'/parameter-source-result.png',fullPage:true});}
 assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(external,[]);console.log(JSON.stringify({status:'passed',checks:['format','save','patternSignature','parameterHighlight','run','patternEdit','defaultEdit','apply','reload','runAfterReload'],errors,external}));
 }finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1});
"""
            try:
                result=subprocess.run([node,'-e',script],text=True,capture_output=True,cwd=ROOT,env=dict(os.environ,PARAMETER_ORIGIN=origin),timeout=75)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'],'passed')
                saved=app.get_project('parameters')
                self.assertIn('"y": value',saved['source'])
                self.assertEqual(Evaluator().run(saved['source']),42)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':
    unittest.main()
