'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),notices=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(t){notices.push(t)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[1].value.body.body.id;renderSyntaxFields()');
const options=name=>json('chooseKind("literal",()=>{},'+JSON.stringify(name)+').children.map(x=>x.value)');
assert.deepStrictEqual(options('tryRoot').sort(),['try','try_cases','try_group']);assert.deepStrictEqual(options('finallyBody').sort(),['block','try','try_cases','try_group','try_else'].sort());
assert(options('expression').includes('rethrow'));assert(options('catchPattern').includes('p_bind'));assert(options('catchPattern').includes('p_record'));assert(!options('catchPattern').includes('p_any'));
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[1].value.body.body.cases[0].body.id))'),'blockOnly');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[1].value.body.body.cases[0].pattern.id))'),'catchPattern');
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
const buttons=descendants(nodes.get('#syntaxFields')).filter(x=>x.tagName==='BUTTON'),moves=buttons.filter(x=>x.textContent==='上移处理分支');
assert.strictEqual(moves.length,2);assert.strictEqual(moves[0].disabled,true);moves[1].onclick();
assert.strictEqual(run('programDraft.root.statements[1].value.body.body.cases[0].pattern.fields[0].key'),'code');
run('programDraft.root.statements[1].value.body.body.cases.reverse()');
assert.strictEqual(run('validCatchCases([{pattern:{kind:"p_bind",name:"error"},guard:null,body:{kind:"block",statements:[]}}])'),false);
assert.strictEqual(run('validCatchCases([])'),false);
console.log(JSON.stringify({status:'passed',checks:13,program:json('programDraft')}));
