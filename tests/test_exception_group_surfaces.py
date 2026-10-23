"""Grouped error branches share source, structural edits and lexical tooling."""
import copy
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework
from chaos.compiler import compile_program
from chaos.ide_language import analyze
from chaos.language import Evaluator, LanguageError, Parser
from chaos.program import SHAPES,decode_program,format_program,parse_program,plain,templates
from chaos.source_format import format_source
from chaos.symbols import references,rename

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''// subgroup handling preserves source comments
var seen=[];try{throw error_group("batch",[error(41),error(42)])}
catch* {value:item,.._} if item==41 as selected {seen=append(seen,selected.exceptions[0].value)}
catch* _ as rest {seen=append(seen,rest.exceptions[0].value)}else{seen=[0]}finally{null};seen'''


class ExceptionGroupSurfaces(unittest.TestCase):
    def test_source_ir_formatting_and_template_roundtrip(self):
        for width in (40,88):
            formatted=format_source(SOURCE,line_width=width)
            self.assertEqual(format_source(formatted,line_width=width),formatted)
            self.assertIn('catch* {',formatted)
            self.assertIn('// subgroup',formatted)
            self.assertEqual(plain(Parser(SOURCE).program()),plain(Parser(formatted).program()))
            self.assertEqual(Framework.evaluate(formatted),[41,42])
        program=parse_program(SOURCE);printed=format_program(program)
        self.assertEqual(plain(decode_program(program)),plain(Parser(printed).program()))
        self.assertEqual(Framework.evaluate(printed),[41,42])
        values=templates();self.assertEqual(values['group_case']['kind'],'group_case')
        self.assertEqual(values['try_group']['cases'][0]['kind'],'group_case')

    def test_ir_enforces_group_case_context_and_closed_fields(self):
        original=parse_program('try{0}catch* _ as failures{0}')
        for mutate in (
            lambda n:n.update(cases=[]),
            lambda n:n['cases'][0].update(name='return'),
            lambda n:n['cases'][0].update(extra=True),
            lambda n:n['cases'][0].update(body={'id':'bad_body','kind':'literal','value':0}),
            lambda n:n.update(cases=[{'id':'bad_case','kind':'literal','value':0}]),
        ):
            value=copy.deepcopy(original);mutate(value['root']['statements'][0])
            with self.assertRaises(LanguageError):decode_program(value)
        value=copy.deepcopy(original)
        value['root']['statements']=[value['root']['statements'][0]['cases'][0]]
        with self.assertRaises(LanguageError):decode_program(value)
        value=copy.deepcopy(original)
        value['root']['statements'][0]['body']['statements']=[copy.deepcopy(value['root']['statements'][0]['cases'][0])]
        value['root']['statements'][0]['body']['statements'][0]['id']='outside_case'
        with self.assertRaises(LanguageError):decode_program(value)

    def test_guard_and_subgroup_bindings_have_separate_identities(self):
        source='let item=7;try{throw error(41)}catch* {value:item,.._} if item==41 as item{item.value};item'
        self.assertEqual(analyze(source)['diagnostics'],[])
        guard=source.index('value:item')+6
        report=references(source,position=guard)
        self.assertTrue(report['complete']);self.assertEqual(len(report['references']),2)
        changed=rename(source,position=guard,new_name='leaf')['files']['main.chaos']
        self.assertIn('value:leaf',changed);self.assertIn('if leaf==41 as item{item.value}',changed)
        handler=source.index('as item')+3
        report=references(source,position=handler)
        self.assertTrue(report['complete']);self.assertEqual(len(report['references']),2)
        changed=rename(source,position=handler,new_name='subgroup')['files']['main.chaos']
        self.assertIn('as subgroup{subgroup.value}',changed);self.assertTrue(changed.endswith(';item'))
        names={row['name']:row['type']for row in analyze(SOURCE)['symbols']}
        self.assertEqual(names['selected'],'Error');self.assertEqual(names['rest'],'Error')
        for source in ('try{0}catch* {value:leaf,.._} as group{leaf}',
                       'try{0}catch* _ if group==null as group{0}',
                       'try{0}catch* _ as group{0};group'):
            self.assertIn('undefined_name',[row['code']for row in analyze(source)['diagnostics']])
        bad='try{0}catch* _ if 1 as group{0}'
        self.assertIn('type_mismatch',[row['code']for row in analyze(bad)['diagnostics']])

    def test_header_name_token_does_not_capture_nested_as(self):
        source='try{0}catch* _ if do{let group=true;group} as group{group.code}'
        position=source.rindex('as group')+3
        report=references(source,position=position)
        self.assertTrue(report['complete']);self.assertEqual(len(report['references']),2)
        changed=rename(source,position=position,new_name='batch')['files']['main.chaos']
        self.assertIn('let group=true;group',changed)
        self.assertIn('as batch{batch.code}',changed)

    def test_macro_hygiene_separates_leaf_and_group_with_same_spelling(self):
        source='''macro recover(action,collect){quote{
try{unquote(action)()}catch* {value:item,.._} if item==41 as item{unquote(collect)(item)}}};
let item=7;var result=0;
recover!(fn(){throw error(41)},fn(value){result=value.exceptions[0].value});[result,item]'''
        for text in (source,format_source(source),format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text),[41,7])

    def test_error_completion_and_compilation_are_inert(self):
        source='try{throw error(1)}catch* {code:code,.._} if code=="user_throw" as selected{selected.code}'
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated')):
            report=analyze(source);self.assertEqual(report['diagnostics'],[])
            self.assertEqual(next(row['type']for row in report['symbols']if row['name']=='code'),'String')
            format_source(source);self.assertTrue(references(source,position=source.index('as selected')+3)['complete'])
            compile_program(source)
        draft='try{0}catch* _ as selected{selected.'
        labels={row['label']for row in analyze(draft,position=len(draft))['completions']}
        self.assertTrue({'code','message','value','location','exceptions'}<=labels)

    @unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
    def test_structural_controls_group_context_reorder_and_last_case_guard(self):
        source='try{0}catch* {code:"never",.._} as one{1}catch* _ as two{2}'
        fixture={'info':{'syntax':SHAPES,'templates':templates()},'program':parse_program(source)}
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),notices=[];
const context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(t){notices.push(t)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync('web/language.js','utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[0].id;renderSyntaxFields()');
const options=context=>json('chooseKind("literal",()=>{},'+JSON.stringify(context)+').children.map(x=>x.value)');
assert.deepStrictEqual(options('groupCases'),['group_case']);assert(!options(false).includes('group_case'));assert(options('tryRoot').includes('try_group'));assert(options('finallyBody').includes('try_group'));
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[0].cases[0].id))'),'groupCases');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[0].cases[0].pattern.id))'),true);
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[0].cases[0].body.id))'),'blockOnly');
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)]}
const buttons=()=>descendants(nodes.get('#syntaxFields')).filter(x=>x.tagName==='BUTTON');
buttons().filter(x=>x.textContent==='上移处理分支')[1].onclick();assert.strictEqual(run('programDraft.root.statements[0].cases[0].name'),'two');
run('renderSyntaxFields()');buttons().find(x=>x.textContent==='删除').onclick();assert.strictEqual(run('programDraft.root.statements[0].cases.length'),1);
run('renderSyntaxFields()');buttons().find(x=>x.textContent==='删除').onclick();assert.strictEqual(run('programDraft.root.statements[0].cases.length'),1);assert(notices.length);
console.log(JSON.stringify({status:'passed',program:json('programDraft')}));
'''
        result=subprocess.run([shutil.which('node'),'-e',script],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout);self.assertEqual(report['status'],'passed');format_program(report['program'])


if __name__=='__main__':unittest.main()
