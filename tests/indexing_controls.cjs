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
for(const [name,params] of Object.entries(json('itemMethodParameters'))){
 const method=json('freshDescriptorMethod('+JSON.stringify(name)+')');
 assert.strictEqual(method.kind,'def');assert.strictEqual(method.function.kind,'fn');assert.deepStrictEqual(method.function.parameters.map(p=>p.name),params);
 assert.strictEqual(method.function.returns,name==='__getitem__'?'Any':'Null');assert.strictEqual(method.function.body.statements[0].value,null);assert(select.children.some(o=>o.value===name));
}
run(`syntaxInfo.syntax.subscript=[['object','node'],['index','node']];syntaxInfo.syntax.set_item=[['object','node'],['index','node'],['value','node']];syntaxInfo.syntax.slice=[['start','optional'],['stop','optional'],['step','optional']];syntaxInfo.templates.slice={kind:'slice',id:'slice',start:null,stop:null,step:null};syntaxInfo.templates.subscript={kind:'subscript',id:'item',object:{kind:'name',id:'target',name:'input'},index:{kind:'literal',id:'key',value:0}};syntaxInfo.templates.set_item={...syntaxInfo.templates.subscript,kind:'set_item',value:{kind:'literal',id:'assigned',value:42}};`);
for(const context of [false,'expression','arguments','classBody',true])assert(!run('chooseKind("literal",()=>{},'+JSON.stringify(context)+')').children.some(o=>o.value==='slice'));
assert(run('chooseKind("literal",()=>{},"itemKey")').children.some(o=>o.value==='slice'));
assert(!run('chooseKind("literal",()=>{},"itemKey")').children.some(o=>o.value==='let'));
run("programDraft={root:{kind:'block',id:'root',statements:[freshSyntax(syntaxInfo.templates.set_item)]}};syntaxSelection=programDraft.root.statements[0].id;renderSyntaxFields()");
const beforeObject=json('programDraft.root.statements[0].object'),beforeValue=json('programDraft.root.statements[0].value');
const keyControl=controls().filter(x=>x.tagName==='SELECT').find(x=>x.children.some(o=>o.value==='slice'));assert(keyControl);keyControl.value='slice';keyControl.onchange();
assert.deepStrictEqual(json('programDraft.root.statements[0].object'),beforeObject);assert.deepStrictEqual(json('programDraft.root.statements[0].value'),beforeValue);
assert.strictEqual(run('syntaxContext(syntaxIndex().get(programDraft.root.statements[0].index.id))'),'itemKey');
run('syntaxSelection=programDraft.root.statements[0].index.id;renderSyntaxFields()');
assert(controls().some(x=>x.tagName==='LABEL'&&x.textContent==='终点（不含，可省略）'));
const add=controls().filter(x=>x.tagName==='BUTTON'&&x.textContent==='添加');assert.strictEqual(add.length,3);add[0].onclick();
assert.strictEqual(run('programDraft.root.statements[0].index.start.kind'),'literal');
run('renderSyntaxFields()');assert.strictEqual(run('syntaxChildren(programDraft.root.statements[0].index).length'),1);
const clear=controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='省略，使用默认值');assert(clear);clear.onclick();assert.strictEqual(run('programDraft.root.statements[0].index.start'),null);
run('renderSyntaxFields()');assert(!controls().some(x=>x.textContent==='不捕获剩余项'));
assert.strictEqual(run('syntaxContext({parent:{kind:"slice"},slot:"start",node:{kind:"literal"}})'),'expression');
const ide=fs.readFileSync(path.join(root,'web/ide.js'),'utf8');
const versionFunction=ide.slice(ide.indexOf(' function languageVersions('),ide.indexOf(' function analysisBody('));
run('let state={refactorDialects:new Map()};let project={program:{languageVersion:1,sourceVersions:{"main.chaos":1,"lib.chaos":1}},files:{"main.chaos":"draft","lib.chaos":"saved"}};let baseProject={files:{"main.chaos":"saved","lib.chaos":"saved"}};'+versionFunction);
assert.deepStrictEqual(json('languageVersions("main.chaos","draft")'),{'main.chaos':2,'lib.chaos':1});
assert.deepStrictEqual(json('languageVersions("main.chaos","saved")'),{'main.chaos':1,'lib.chaos':1});
run('state.refactorDialects.set("main.chaos",{text:"draft",version:1})');
assert.deepStrictEqual(json('languageVersions("main.chaos","draft")'),{'main.chaos':1,'lib.chaos':1});
assert.deepStrictEqual(json('languageVersions("main.chaos","newer")'),{'main.chaos':2,'lib.chaos':1});
run('project.files["lib.chaos"]="renamed";state.refactorDialects.set("lib.chaos",{text:"renamed",version:1})');
assert.deepStrictEqual(json('languageVersions("main.chaos","draft")'),{'main.chaos':1,'lib.chaos':1});
run('project.files["lib.chaos"]="newer"');
assert.deepStrictEqual(json('languageVersions("main.chaos","draft")'),{'main.chaos':1,'lib.chaos':2});
run('state.refactorDialects.clear()');
assert.deepStrictEqual(json('languageVersions("main.chaos","draft")'),{'main.chaos':2,'lib.chaos':2});
console.log(JSON.stringify({status:'passed',checks:32,details:['legacy descriptor/operator/iterator controls retained','three ordinary item protocol templates','slice offered only as item key','key kind changes retain target and RHS','all optional slice boundaries can be added and cleared','slice labels match semantics','expression-only boundaries','per-file draft version tracks committed base','refactor dialect override requires exact current-module text','refactor dialect override requires exact other-module text','edited text or cleared overrides restore ordinary draft dialect rules']}));
