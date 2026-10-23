"use strict";
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),toasts=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){toasts.push(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{}');
const descendants=x=>[x,...x.children.flatMap(descendants)];
for(const kind of ['let_pattern','var_pattern','for_pattern','async_for_pattern','comp_for_pattern','comp_async_for_pattern']){
 assert(run('patternTargetKinds.has('+JSON.stringify(kind)+')'));
 assert(json('syntaxChildren(fixture.info.templates['+JSON.stringify(kind)+']).map(([slot])=>slot)').includes('pattern'));
}
const menu=context=>json('chooseKind("p_list",()=>{},'+JSON.stringify(context)+').children.map(x=>x.value)');
assert.deepStrictEqual(menu('targetPattern').sort(),['p_class','p_list','p_literal','p_record','p_tag','p_tuple']);
assert.deepStrictEqual(menu('patternRest').sort(),['p_any','p_bind']);
assert(!menu('classBody').some(kind=>kind.endsWith('_pattern')));
assert(menu('clauses').includes('comp_for_pattern'));
assert(menu('clauses').includes('comp_async_for_pattern'));
assert(!menu('syncClauses').includes('comp_async_for_pattern'));
run('syntaxSelection=programDraft.root.statements[0].pattern.id;renderSyntaxFields()');
let controls=descendants(nodes.get('#syntaxFields'));
let chooser=controls.find(x=>x.tagName==='SELECT');
assert(!chooser.children.some(x=>x.value==='p_bind'||x.value==='p_any'));
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[0].pattern.id))'),'targetPattern');
run('syntaxSelection=programDraft.root.statements[0].value.items[0].id;renderSyntaxFields()');
controls=descendants(nodes.get('#syntaxFields'));
const input=controls.find(x=>x.tagName==='INPUT');assert(input);input.value='20';
controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='应用').onclick();
console.log(JSON.stringify({status:'passed',program:json('programDraft'),checks:22}));
