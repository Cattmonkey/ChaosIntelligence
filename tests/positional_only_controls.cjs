'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),toasts=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(message){toasts.push(message)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[0].function.id;renderSyntaxFields()');
const descendants=x=>[x,...x.children.flatMap(descendants)],controls=()=>descendants(nodes.get('#syntaxFields'));
const mode=name=>controls().find(x=>x.attributes['aria-label']==='参数 '+name+' 模式');
assert(mode('value').children.some(x=>x.value==='positional_only'));
mode('extra').value='positional_only';mode('extra').onchange();
assert.strictEqual(json('programDraft.root.statements[0].function.parameters[1].mode'),'positional_only');
const initial=json('programDraft.root.statements[0].function.parameters');
run('renderSyntaxFields()');mode('value').value='positional';mode('value').onchange();
assert.deepStrictEqual(json('programDraft.root.statements[0].function.parameters'),initial);assert.strictEqual(mode('value').value,'positional_only');assert.strictEqual(toasts.length,1);
for(const modes of [['positional','positional_only'],['keyword_only','positional'],['varargs','varargs'],['varkw','keyword_only']]){
 context.modes=modes;assert.strictEqual(run('validParameterOrder(modes.map(mode=>({name:"x",type:"Any",mode,default:null})))'),false);
}
run('renderSyntaxFields()');const adds=controls().filter(x=>x.tagName==='BUTTON'&&x.textContent==='添加默认值');adds[0].onclick();
assert.strictEqual(json('programDraft.root.statements[0].function.parameters[0].default.kind'),'literal');
run('renderSyntaxFields()');const clears=controls().filter(x=>x.tagName==='BUTTON'&&x.textContent==='移除默认值');clears[1].onclick();
assert.strictEqual(json('programDraft.root.statements[0].function.parameters[1].default.value'),2);assert.strictEqual(toasts.length,2);
run('renderSyntaxFields()');controls().filter(x=>x.tagName==='BUTTON'&&x.textContent==='移除默认值')[0].onclick();
assert.strictEqual(json('programDraft.root.statements[0].function.parameters[0].default'),null);
assert.strictEqual(run('validParameterOrder(programDraft.root.statements[0].function.parameters)'),true);
console.log(JSON.stringify({status:'passed',checks:17,program:json('programDraft')}));
