"use strict";
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),toasts=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){toasts.push(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{}');
const descendants=x=>[x,...x.children.flatMap(descendants)];
for(const kind of ['list_comp','record_comp','gen_comp','async_gen_comp']){
 assert(run('expressionKinds.has('+JSON.stringify(kind)+')'));
 assert(json('chooseKind("literal",()=>{},"expression").children.map(x=>x.value)').includes(kind));
 assert(json('syntaxChildren(fixture.info.templates['+JSON.stringify(kind)+']).map(([slot])=>slot)').includes('clauses.0'));
}
const clauses=json('chooseKind("comp_for",()=>{},"clauses").children.map(x=>x.value)');
assert.deepStrictEqual(clauses.sort(),['comp_async_for','comp_async_for_pattern','comp_for','comp_for_pattern','comp_if']);
assert(!json('chooseKind("comp_for",()=>{},"syncClauses").children.map(x=>x.value)').some(kind=>['comp_async_for','comp_async_for_pattern'].includes(kind)));
assert(!json('chooseKind("literal",()=>{},"expression").children.map(x=>x.value)').includes('comp_if'));
run('syntaxSelection=programDraft.root.statements[0].id;renderSyntaxFields()');
let panel=nodes.get('#syntaxFields'),area=panel.children.find(x=>x.children[0]?.textContent==='clauses');assert(area);
let controls=descendants(area),deletes=controls.filter(x=>x.tagName==='BUTTON'&&x.textContent==='删除');
deletes[0].onclick();assert.strictEqual(run('programDraft.root.statements[0].clauses.length'),2);assert(toasts.length);
const chooser=controls.find(x=>x.tagName==='SELECT'&&x.attributes['aria-label']==='新增推导子句');assert(chooser);chooser.value='comp_if';chooser.onchange();
controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加子句').onclick();assert.strictEqual(run('programDraft.root.statements[0].clauses.length'),3);
run('syntaxSelection=programDraft.root.statements[0].clauses[0].id;renderSyntaxFields()');
controls=descendants(nodes.get('#syntaxFields'));
const kindChooser=controls.find(x=>x.tagName==='SELECT');kindChooser.value='comp_if';kindChooser.onchange();
assert.strictEqual(run('programDraft.root.statements[0].clauses[0].kind'),'comp_for');
assert.strictEqual(run('validClauses([fixture.info.templates.comp_if,fixture.info.templates.comp_for])'),false);
assert.strictEqual(run('validClauses([fixture.info.templates.comp_async_for],true)'),false);
console.log(JSON.stringify({status:'passed',program:json('programDraft'),checks:19}));
