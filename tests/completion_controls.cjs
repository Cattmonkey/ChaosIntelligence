'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(){},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[1].value.id;renderSyntaxFields()');
const options=name=>json('chooseKind("literal",()=>{},'+JSON.stringify(name)+').children.map(x=>x.value)');
assert.deepStrictEqual(options('loopRoot').sort(),['while','for','async_for','for_pattern','async_for_pattern'].sort());
assert.deepStrictEqual(options('tryRoot').sort(),['try','try_cases','try_group'].sort());assert.deepStrictEqual(options('blockOnly'),['block']);
assert.deepStrictEqual(options('finallyBody').sort(),['block','try','try_cases','try_group','try_else'].sort());
assert(options('expression').includes('loop_else'));assert(options('expression').includes('try_else'));
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[1].value.loop.id))'),'loopRoot');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[1].value.otherwise.id))'),'blockOnly');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[1].value.otherwise.statements[0].body.id))'),'tryRoot');
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
const selects=descendants(nodes.get('#syntaxFields')).filter(x=>x.tagName==='SELECT');
assert(selects.some(x=>x.children.map(v=>v.value).sort().join(',')===options('loopRoot').sort().join(',')));
assert(selects.some(x=>x.children.length===1&&x.children[0].value==='block'));
run('syntaxSelection=programDraft.root.statements[1].value.otherwise.statements[0].id;renderSyntaxFields()');
assert(nodes.get('#syntaxTitle').textContent.includes('异常保护成功分支'));
console.log(JSON.stringify({status:'passed',checks:12,program:json('programDraft')}));
