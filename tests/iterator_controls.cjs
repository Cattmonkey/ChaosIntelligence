'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=process.argv[2]||path.resolve(__dirname,'..');
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(){},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)}});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run(`syntaxInfo={syntax:{class:[['name','str'],['bases','nodes'],['body','node']],block:[['statements','nodes']],def:[['name','str'],['function','node']],fn:[['parameters','params'],['returns','str'],['body','node'],['name','str']],let:[['name','str'],['type','str'],['value','node']],var:[['name','str'],['type','str'],['value','node']],literal:[['value','literal']],name:[['name','str']],set_member:[['object','node'],['index','node'],['value','node']]},templates:{literal:{kind:'literal',id:'zero',value:0},name:{kind:'name',id:'ref',name:'Base'},let:{kind:'let',id:'field',name:'field',type:'Any',value:{kind:'literal',id:'fv',value:0}},var:{kind:'var',id:'field',name:'field',type:'Any',value:{kind:'literal',id:'fv',value:0}},def:{kind:'def',id:'method',name:'method',function:{kind:'fn',id:'fn',name:'method',parameters:[{name:'value',type:'Any'}],returns:'Any',body:{kind:'block',id:'fnbody',statements:[]}}}}};programDraft={root:{kind:'block',id:'root',statements:[{kind:'class',id:'class',name:'Example',bases:[],body:{kind:'block',id:'body',statements:[]}}]}};renderSyntax=()=>{};syntaxSelection='body';renderSyntaxFields();`);
const descendants=x=>[x,...x.children.flatMap(descendants)],controls=()=>descendants(nodes.get('#syntaxFields'));
const parameters={__get__:['self','instance','owner'],__set__:['self','instance','value'],__delete__:['self','instance'],__set_name__:['self','owner','name']};
for(const [name,params]of Object.entries(parameters)){
 const method=json('freshDescriptorMethod('+JSON.stringify(name)+')');
 assert.strictEqual(method.kind,'def');assert.strictEqual(method.name,name);assert.strictEqual(method.function.name,name);
 assert.deepStrictEqual(method.function.parameters.map(row=>row.name),params);
 assert.strictEqual(method.function.returns,name==='__get__'?'Any':'Null');assert.strictEqual(method.function.body.statements[0].value,null);
}
const select=controls().find(x=>x.tagName==='SELECT'&&x.attributes['aria-label']==='描述符协议方法');assert(select);select.value='__delete__';
controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加协议方法').onclick();
assert.strictEqual(run('programDraft.root.statements[0].body.statements[0].name'),'__delete__');
controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加协议方法').onclick();
assert.strictEqual(run('programDraft.root.statements[0].body.statements.length'),1);
assert(!run("Object.hasOwn(syntaxInfo.syntax,'delete_member')"));

const operators=json('operatorMethodParameters');assert.strictEqual(Object.keys(operators).length,48);
for(const [name,params]of Object.entries(operators)){
 const method=json('freshDescriptorMethod('+JSON.stringify(name)+')');assert.strictEqual(method.kind,'def');assert.strictEqual(method.function.kind,'fn');assert.deepStrictEqual(method.function.parameters.map(p=>p.name),params);
 const body=method.function.body.statements[0];
 if(['__bool__','__contains__'].includes(name)){assert.strictEqual(method.function.returns,'Bool');assert.strictEqual(body.value,false)}
 else if(name==='__len__'){assert.strictEqual(method.function.returns,'Int');assert.strictEqual(body.value,0)}
 else{assert.strictEqual(method.function.returns,'Any');assert.strictEqual(body.kind,'name');assert.strictEqual(body.name,'NotImplemented')}
 assert(select.children.some(option=>option.value===name));
}
select.value='__add__';controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加协议方法').onclick();
assert.strictEqual(run('programDraft.root.statements[0].body.statements[1].name'),'__add__');

run("syntaxInfo.templates.record={kind:'record',id:'record',fields:[]}");
const iterators=json('iteratorMethodParameters');assert.strictEqual(Object.keys(iterators).length,10);
for(const [name,params]of Object.entries(iterators)){
 const method=json('freshDescriptorMethod('+JSON.stringify(name)+')');assert.strictEqual(method.kind,'def');assert.deepStrictEqual(method.function.parameters.map(p=>p.name),params);
 assert.strictEqual(method.function.kind,['__anext__','__asend__','__athrow__','__aclose__'].includes(name)?'async_fn':'fn');
 const body=method.function.body.statements[0];
 if(['__iter__','__aiter__'].includes(name)){assert.strictEqual(method.function.returns,'Any');assert.strictEqual(body.kind,'name');assert.strictEqual(body.name,'self')}
 else if(['__close__','__aclose__'].includes(name)){assert.strictEqual(method.function.returns,'Null');assert.strictEqual(body.value,null)}
 else{assert.strictEqual(method.function.returns,'Record');assert.strictEqual(body.kind,'record');assert.deepStrictEqual(body.fields.map(f=>[f.key,f.value.value]),[['done',true],['value',null]])}
 assert(select.children.some(option=>option.value===name));
}
select.value='__anext__';controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加协议方法').onclick();
assert.strictEqual(run('programDraft.root.statements[0].body.statements[2].function.kind'),'async_fn');
controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加协议方法').onclick();assert.strictEqual(run('programDraft.root.statements[0].body.statements.length'),3);
console.log(JSON.stringify({status:'passed',checks:15,details:['old descriptor and operator helpers retained','10 iterator helpers remain ordinary def nodes','factories synchronous by default, four asynchronous step/send/throw/close defaults','exact done/value record defaults','close returns Null','factories return self without fake handle literals','actual async method add and duplicate prevention']}));
