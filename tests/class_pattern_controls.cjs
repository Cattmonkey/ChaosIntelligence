'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),notices=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(t){notices.push(t)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[2].pattern.id;renderSyntaxFields()');
const options=name=>json('chooseKind("literal",()=>{},'+JSON.stringify(name)+').children.map(x=>x.value)');
for(const context of ['structuralPattern','targetPattern','catchPattern'])assert(options(context).includes('p_class'));
assert(!options('expression').includes('p_class'));assert(!options('structuralPattern').includes('p_bind'));assert(!options('catchPattern').includes('p_any'));
assert.deepStrictEqual(options('patternRest').sort(),['p_any','p_bind']);
assert.strictEqual(run('syntaxContext({parent:fixture.info.templates.p_class,node:fixture.info.templates.p_class.rest,slot:"rest"})'),'patternRest');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[2].pattern.id))'),'targetPattern');
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
const fields=nodes.get('#syntaxFields').children.find(x=>x.children[0]?.textContent==='fields');
const controls=descendants(fields);const input=controls.find(x=>x.tagName==='INPUT');input.value='right';controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='应用').onclick();
assert.strictEqual(run('programDraft.root.statements[2].pattern.fields[0].key'),'right');
assert.strictEqual(run('programDraft.root.statements[2].pattern.fields[0].value.name'),'value');
assert.deepStrictEqual(notices,[]);
console.log(JSON.stringify({status:'passed',checks:12,program:json('programDraft')}));
