'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),notices=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(t){notices.push(t)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[1].id;renderSyntaxFields()');
assert.strictEqual(run('syntaxDirty'),false);assert.strictEqual(run('Object.hasOwn(programDraft.root.statements[1],"options")'),false);
const options=name=>json('chooseKind("keyword",()=>{},'+JSON.stringify(name)+').children.map(x=>x.value)');
assert.deepStrictEqual(options('classOptions').sort(),['keyword','kwspread']);assert(!options('expression').includes('keyword'));
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[2].options[0].id))'),'classOptions');
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
let fields=nodes.get('#syntaxFields').children.find(x=>x.children[0]?.textContent==='options');
let add=descendants(fields).find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加类选项');add.onclick();
assert.strictEqual(run('programDraft.root.statements[1].options[0].kind'),'keyword');
run('renderSyntaxFields()');fields=nodes.get('#syntaxFields').children.find(x=>x.children[0]?.textContent==='options');
descendants(fields).find(x=>x.tagName==='BUTTON'&&x.textContent==='删除').onclick();
assert.strictEqual(run('Object.hasOwn(programDraft.root.statements[1],"options")'),false);
run('syntaxSelection=programDraft.root.statements[2].options[0].value.id;renderSyntaxFields()');
fields=nodes.get('#syntaxFields').children.find(x=>x.children[0]?.textContent==='value');
const controls=descendants(fields),input=controls.find(x=>x.tagName==='INPUT');input.value='4';controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='应用').onclick();
for(const name of ['__new__','__init_subclass__']){
 const node=json('freshDescriptorMethod('+JSON.stringify(name)+')');assert.strictEqual(node.function.parameters[0].name,'cls');assert.strictEqual(node.function.parameters.at(-1).mode,'varkw');
 if(name==='__new__'){assert.strictEqual(node.function.body.statements[0].callee.name,'alloc_instance');assert.strictEqual(node.function.parameters[1].mode,'varargs');}
 else assert.strictEqual(node.function.body.statements[0].value,null);
}
assert.deepStrictEqual(notices,[]);
console.log(JSON.stringify({status:'passed',checks:16,program:json('programDraft')}));
