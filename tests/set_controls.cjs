'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),notices=[],context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(t){notices.push(t)},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{};syntaxSelection=programDraft.root.statements[1].value.id;renderSyntaxFields()');
const options=name=>json('chooseKind("literal",()=>{},'+JSON.stringify(name)+').children.map(x=>x.value)');
assert(options('expression').includes('set'));assert(options('expression').includes('set_comp'));
assert(options('setItems').includes('spread'));assert(!options('setItems').includes('kwspread'));assert(!options('setItems').includes('keyword'));assert(!options('expression').includes('spread'));
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[1].value.items[0].id))'),'setItems');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[1].value.items[0].value.id))'),'expression');
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[2].value.element.id))'),'expression');
assert.strictEqual(run('comprehensionKinds.has("set_comp")'),true);
function descendants(node){return [node,...(node.children||[]).flatMap(descendants)];}
let controls=descendants(nodes.get('#syntaxFields'));const chooser=controls.find(x=>x.tagName==='SELECT'&&x.attributes['aria-label']==='新增集合元素类型');assert(chooser);chooser.value='spread';chooser.onchange();controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加节点').onclick();
assert.strictEqual(run('programDraft.root.statements[1].value.items.at(-1).kind'),'spread');
run('programDraft.root.statements[1].value.items.pop();syntaxSelection=programDraft.root.statements[2].value.element.right.id;renderSyntaxFields()');controls=descendants(nodes.get('#syntaxFields'));controls.find(x=>x.tagName==='INPUT').value='2';controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='应用').onclick();
assert.strictEqual(run('programDraft.root.statements[2].value.element.right.value'),2);
run('let savedProgram=programDraft;programDraft={root:freshSyntax(fixture.info.templates.block)};programDraft.root.statements=[freshSyntax(fixture.info.templates.set)];syntaxSelection=programDraft.root.statements[0].id;renderSyntaxFields()');controls=descendants(nodes.get('#syntaxFields'));controls.find(x=>x.tagName==='BUTTON'&&x.textContent==='删除').onclick();assert.strictEqual(run('programDraft.root.statements[0].items.length'),1);assert.strictEqual(notices.length,1);run('programDraft=savedProgram');
console.log(JSON.stringify({status:'passed',checks:16,program:json('programDraft')}));
