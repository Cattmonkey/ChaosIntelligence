'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=process.argv[2]||path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(){},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)},fixture});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run('syntaxInfo=fixture.info;programDraft=fixture.program;renderSyntax=()=>{}');
const descendants=x=>[x,...x.children.flatMap(descendants)],controls=()=>descendants(nodes.get('#syntaxFields'));
assert.strictEqual(run('languageNames.with'),'资源作用域');assert.strictEqual(run('languageNames.async_with'),'异步资源作用域');
for(const kind of ['with','async_with']){
 assert(run('expressionKinds.has('+JSON.stringify(kind)+')'));
 assert(json('chooseKind("literal",()=>{},"expression").children.map(x=>x.value)').includes(kind));
 const node=fixture.info.templates[kind];
 assert.deepStrictEqual(json('syntaxChildren(fixture.info.templates['+JSON.stringify(kind)+']).map(([slot])=>slot)'),['manager','body']);
 assert.strictEqual(run('syntaxContext({parent:fixture.info.templates['+JSON.stringify(kind)+'],slot:"manager",node:fixture.info.templates.literal})'),'expression');
}
const scope=fixture.program.root.statements[1];context.scopeId=scope.id;
run('syntaxSelection=scopeId;renderSyntaxFields()');
const manager=nodes.get('#syntaxFields').children.find(x=>x.children[0]?.textContent==='manager');assert(manager);
const choices=descendants(manager).find(x=>x.tagName==='SELECT').children.map(x=>x.value);
assert(choices.includes('call'));assert(choices.includes('with'));assert(!choices.includes('def'));assert(!choices.includes('let'));
const name=nodes.get('#syntaxFields').children.find(x=>x.children[0]?.textContent==='name');assert(name);
const input=descendants(name).find(x=>x.tagName==='INPUT');input.value='renamed';input.onchange();
assert.strictEqual(run('programDraft.root.statements[1].name'),'renamed');
run('programDraft.root.statements[1].body.statements[0].name="renamed"');
run('syntaxSelection=programDraft.root.statements[0].body.id;renderSyntaxFields()');
const select=controls().find(x=>x.tagName==='SELECT'&&x.attributes['aria-label']==='描述符协议方法');assert(select);
const helpers=[];
for(const [name,parameters] of Object.entries({__enter__:['self'],__exit__:['self','error'],__aenter__:['self'],__aexit__:['self','error']})){
 assert(select.children.some(x=>x.value===name));
 const node=json('freshDescriptorMethod('+JSON.stringify(name)+')');helpers.push(node);
 assert.deepStrictEqual(node.function.parameters.map(x=>x.name),parameters);
 assert.strictEqual(node.function.kind,name.startsWith('__a')?'async_fn':'fn');
 const enter=name==='__enter__'||name==='__aenter__';assert.strictEqual(node.function.returns,enter?'Any':'Null');
 assert.strictEqual(node.function.body.statements[0][enter?'name':'value'],enter?'self':null);
}
select.value='__aexit__';const add=controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加协议方法');add.onclick();add.onclick();
assert.strictEqual(run('programDraft.root.statements[0].body.statements.filter(x=>x.name==="__aexit__").length'),1);
console.log(JSON.stringify({status:'passed',checks:12,program:json('programDraft'),helpers,details:['context labels and expression choices','schema children','manager expression restriction','editable lexical binding','four protocol factories','async hook construction','duplicate prevention']}));
