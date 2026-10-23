'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.resolve(__dirname,'..'),fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function element(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}};}
const nodes=new Map(),context=vm.createContext({document:{createElement:element},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){throw new Error(message)},$:q=>{if(!nodes.has(q))nodes.set(q,element('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};');
const expression='programDraft.root.statements[0].function.body.statements[0]';
assert.equal(run(expression+'.kind'),'binary');assert.equal(run(expression+'.right.value'),2);
const untouched=json('programDraft.root.statements.slice(1)');
run('syntaxSelection='+expression+'.right.id;renderSyntaxFields()');assert(nodes.get('#syntaxTitle').textContent.includes('常量'));
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
const fields=descendants(nodes.get('#syntaxFields')),input=fields.find(n=>n.tagName==='INPUT'),apply=fields.find(n=>n.tagName==='BUTTON'&&n.textContent==='应用');
assert(input&&apply);input.value='3';apply.onclick();
assert.equal(run(expression+'.right.value'),3);assert.deepEqual(json('programDraft.root.statements.slice(1)'),untouched);assert.equal(run('syntaxDirty'),true);
console.log(JSON.stringify({status:'passed',checks:6,program:json('programDraft'),details:['original function AST edited by actual structural form','compiler/device arguments preserved','cold query, await and after-query remain unchanged']}));
