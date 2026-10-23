'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert'),root=process.argv[2]||path.resolve(__dirname,'..');
function el(tag){return {tagName:tag.toUpperCase(),children:[],value:'',attributes:{},append(...xs){this.children.push(...xs);if(tag==='select')for(const x of xs)if(x.selected)this.value=x.value},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attributes[k]=v}}}
const nodes=new Map(),context=vm.createContext({document:{createElement:el},crypto:require('crypto').webcrypto,parseJSON:JSON.parse,stringifyJSON:JSON.stringify,clone:x=>JSON.parse(JSON.stringify(x)),markDirty(){},flushControls(){},toast(){},$:q=>{if(!nodes.has(q))nodes.set(q,el('div'));return nodes.get(q)}});
let source=fs.readFileSync(path.join(root,'web/language.js'),'utf8');source=source.slice(0,source.indexOf("$('#applyProgram')"));vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context),json=s=>JSON.parse(run('JSON.stringify('+s+')'));
run(`syntaxInfo={syntax:{class:[['name','str'],['bases','nodes'],['body','node']],block:[['statements','nodes']],def:[['name','str'],['function','node']],fn:[['parameters','params'],['returns','str'],['body','node'],['name','str']],let:[['name','str'],['type','str'],['value','node']],var:[['name','str'],['type','str'],['value','node']],literal:[['value','literal']],name:[['name','str']],set_member:[['object','node'],['index','node'],['value','node']]},templates:{literal:{kind:'literal',id:'zero',value:0},name:{kind:'name',id:'ref',name:'Base'},let:{kind:'let',id:'field',name:'field',type:'Any',value:{kind:'literal',id:'fv',value:0}},var:{kind:'var',id:'field',name:'field',type:'Any',value:{kind:'literal',id:'fv',value:0}},def:{kind:'def',id:'method',name:'method',function:{kind:'fn',id:'fn',name:'method',parameters:[{name:'value',type:'Any'}],returns:'Any',body:{kind:'block',id:'fnbody',statements:[]}}}}};programDraft={root:{kind:'block',id:'root',statements:[{kind:'class',id:'class',name:'Example',bases:[],body:{kind:'block',id:'body',statements:[]}}]}};renderSyntax=()=>{};syntaxSelection='body';renderSyntaxFields();`);
const descendants=x=>[x,...x.children.flatMap(descendants)],controls=()=>descendants(nodes.get('#syntaxFields'));
assert.deepStrictEqual(json("chooseKind('def',()=>{},'classBody').children.map(x=>x.value)"),['let','var','def']);
assert.strictEqual(run("syntaxContext({parent:programDraft.root.statements[0].body,node:{kind:'def'},slot:'statements.0'})"),'classBody');
assert(!controls().some(x=>x.tagName==='LABEL'&&x.textContent==='节点类型'));
controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加成员').onclick();
assert.deepStrictEqual(json('programDraft.root.statements[0].body.statements[0].function.parameters.map(p=>p.name)'),['self','value']);
run("syntaxSelection='class';renderSyntaxFields()");controls().find(x=>x.tagName==='BUTTON'&&x.textContent==='＋ 添加基类').onclick();
assert.strictEqual(run('programDraft.root.statements[0].bases[0].kind'),'name');
assert.deepStrictEqual(json('syntaxChildren(programDraft.root.statements[0]).map(([slot])=>slot)'),['bases.0','body']);
assert.deepStrictEqual(json('[languageNames.class,languageNames.set_member]'),['类定义','成员赋值']);
console.log(JSON.stringify({status:'passed',checks:7,details:['class member picker permits only field and method declarations','selected member retains class context','class body cannot be replaced with expression','new methods include explicit self','base uses expression node','base/body child traversal preserved','Chinese object labels']}));
