'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function element(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}};}
const nodes=new Map(),context=vm.createContext({document:{createElement:element},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){throw new Error(message)},$:q=>{if(!nodes.has(q))nodes.set(q,element('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};');
const first='programDraft.root.statements[0].function.body.statements[1]';
assert.deepEqual(json('syntaxChildren('+first+').map(row=>row[0])'),['condition','then','else']);
run('syntaxSelection='+first+'.id;renderSyntaxFields()');assert(nodes.get('#syntaxTitle').textContent.includes('条件分支'));
const index=json('[...syntaxIndex().values()].map(item=>item.node.kind)');assert.equal(index.filter(k=>k==='if').length,2);assert(index.filter(k=>k==='let').length>=5);
// Edit the selected branch using the real structure-property handler. The
// returned Program is parsed/evaluated by Python, not a JavaScript mock.
run('syntaxSelection='+first+'.then.statements[1].then.statements[0].right.id;renderSyntaxFields()');
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
const fields=descendants(nodes.get('#syntaxFields')),input=fields.find(n=>n.tagName==='INPUT'),apply=fields.find(n=>n.tagName==='BUTTON'&&n.textContent==='应用');assert(input&&apply);input.value='20';apply.onclick();
assert.equal(run(first+'.then.statements[1].then.statements[0].right.value'),20);
assert.equal(run(first+'.then.statements[1].condition.right.value'),10);
assert.equal(run(first+'.else.statements[0].name'),'delta');
assert.equal(run('syntaxDirty'),true);
console.log(JSON.stringify({status:'passed',checks:8,program:json('programDraft'),details:['existing if node has condition and both branches','nested lexical let nodes remain ordinary structure','actual literal form changes selected branch only','structural draft marked dirty']}));
