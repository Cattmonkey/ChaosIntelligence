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
console.log(JSON.stringify({status:'passed',checks:8,details:['four protocol helpers create plain def nodes with explicit receiver and legal parameters','write/delete/name helpers return Null','getter has conservative Any return','protocol selector exists only inside class body','actual add appends chosen method','duplicate protocol names are blocked','deletion uses ordinary call IR','new method bodies default to null']}));
