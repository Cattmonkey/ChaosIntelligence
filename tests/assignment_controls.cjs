'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),toasts=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){toasts.push(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{}');
const descendants=x=>[x,...x.children.flatMap(descendants)];
const menu=context=>json('chooseKind("p_list",()=>{},'+JSON.stringify(context)+').children.map(x=>x.value)');
assert.deepStrictEqual(menu('structuralPattern').sort(),['p_class','p_list','p_record','p_tag','p_tuple']);
assert.deepStrictEqual(menu('targetPattern').sort(),['p_class','p_list','p_literal','p_record','p_tag','p_tuple']);
assert.deepStrictEqual(menu('patternRest').sort(),['p_any','p_bind']);
for(const kind of ['assign_pattern','with_pattern','async_with_pattern']){
 assert(run('patternTargetKinds.has('+JSON.stringify(kind)+')'));
 assert(json('syntaxChildren(fixture.info.templates['+JSON.stringify(kind)+']).map(([slot])=>slot)').includes('pattern'));
 const node=fixture.info.templates[kind];
 assert.strictEqual(run('syntaxContext({node:fixture.info.templates.'+kind+'.pattern,parent:fixture.info.templates.'+kind+',slot:"pattern"})'),'structuralPattern');
}
assert(menu('expression').includes('with_pattern'));assert(menu('expression').includes('async_with_pattern'));assert(!menu('expression').includes('assign_pattern'));
assert(!menu('classBody').some(kind=>['assign_pattern','with_pattern','async_with_pattern'].includes(kind)));
run('syntaxSelection=programDraft.root.statements[2].id;renderSyntaxFields()');
const controls=descendants(nodes.get('#syntaxFields'));
const selectors=controls.filter(x=>x.tagName==='SELECT');
assert(selectors.some(s=>s.children.length===5&&s.children.every(o=>['p_class','p_list','p_record','p_tag','p_tuple'].includes(o.value))));
run('syntaxSelection=programDraft.root.statements[2].value.items[0].id;renderSyntaxFields()');
const edited=descendants(nodes.get('#syntaxFields'));edited.find(x=>x.tagName==='INPUT').value='21';edited.find(x=>x.tagName==='BUTTON'&&x.textContent==='应用').onclick();
assert.strictEqual(json('programDraft.root.statements[2].value.items[0].value'),21);
assert.deepStrictEqual(toasts,[]);
console.log(JSON.stringify({status:'passed',program:json('programDraft'),checks:19}));
