'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),notices=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(t){notices.push(t)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[2].options[0].value.id;renderSyntaxFields()');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[2].options[0].id))'),'classOptions');
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
let fields=nodes.get('#syntaxFields').children.find(x=>x.children[0]?.textContent==='name');
const controls=descendants(fields),input=controls.find(x=>x.tagName==='INPUT');input.value='Second';controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='应用').onclick();
const node=json('freshDescriptorMethod("__prepare__")');assert.strictEqual(node.name,'__prepare__');assert.strictEqual(node.function.kind,'fn');
assert.deepStrictEqual(node.function.parameters.map(p=>p.name),['mcls','name','bases','options']);assert.strictEqual(node.function.parameters.at(-1).mode,'varkw');
assert.strictEqual(node.function.returns,'Record');assert.strictEqual(node.function.body.statements[0].kind,'record');assert.deepStrictEqual(node.function.body.statements[0].fields,[]);
assert.deepStrictEqual(notices,[]);
console.log(JSON.stringify({status:'passed',checks:10,program:json('programDraft')}));
