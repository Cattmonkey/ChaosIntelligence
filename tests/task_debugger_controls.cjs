'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const snapshot=JSON.parse(fs.readFileSync(0,'utf8')),root=process.argv[2]||path.resolve(__dirname,'..');
const nodes=new Map();
function element(tag='div'){
 const classes=new Set();return {tagName:tag.toUpperCase(),hidden:true,value:'',checked:true,textContent:'',children:[],dataset:{},attributes:{},classList:{toggle(name,on){on?classes.add(name):classes.delete(name)},contains(name){return classes.has(name)}},setAttribute(key,value){this.attributes[key]=value},replaceChildren(...xs){this.children=xs},append(...xs){this.children.push(...xs)},querySelectorAll(){return []},querySelector(){return null},scrollIntoView(){}};
}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
const first=snapshot.tasks[0],second=snapshot.tasks[1];
const frames=[{id:'frame-1',taskId:first.id,name:'work',location:{file:'main.chaos',line:2},locals:[]},{id:'frame-2',taskId:second.id,name:'parent',location:{file:'main.chaos',line:1},locals:[]}];
const paused={runId:'r',version:1,status:'paused',pauseId:3,frames,taskSnapshot:snapshot,breakpoints:[],commands:['continue'],location:frames[0].location};
let data=paused;const calls=[];
const context=vm.createContext({document:{getElementById:get,createElement:element},window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},stringifyJSON:JSON.stringify,currentRun:'r',busy:false,launching:false,dirty:false,controlDraft:false,showRun(){},fetchRun:async()=>{},$:get,toast(){},api:async(url,options)=>{calls.push({url,options});if(options?.method==='POST')data={...paused,status:'running',frames:[],taskSnapshot:{tasks:[],total:0,truncated:false}};return JSON.parse(JSON.stringify(data))}});
vm.runInContext(fs.readFileSync(path.join(root,'web/debugger.js'),'utf8'),context);
const plain=x=>JSON.parse(JSON.stringify(x)),descendants=x=>[x,...x.children.flatMap(descendants)];
(async()=>{
 context.showRun({id:'r',version:1,snapshot:{files:{'main.chaos':'1\n2'}}});await new Promise(resolve=>setImmediate(resolve));
 const rows=get('debugTasks').children;assert.equal(rows.length,2);assert.equal(rows[0].dataset.taskId,first.id);assert.equal(rows[0].dataset.activeFrame,'true');
 assert.match(get('debugTaskSummary').textContent,/显示 2 \/ 103.*截断/);
 const text=descendants(rows[1]).map(x=>x.textContent).join('\n');assert(text.includes('gather'));assert(text.includes('成员（101）'));assert(text.includes('仅显示前 100 项'));assert(text.includes(first.id+', '+first.id));
 assert.equal(calls.filter(x=>x.options?.method==='POST').length,0);
 const frameLink=descendants(rows[1]).find(x=>x.tagName==='BUTTON');frameLink.onclick();assert.equal(get('debugTasks').children[1].dataset.activeFrame,'true');
 assert.equal(context.dirty,false);assert.equal(context.controlDraft,false);
 assert.deepEqual(plain(context.window.ChaosDebugger.state.data.taskSnapshot),snapshot);
 await context.window.ChaosDebugger.command('continue');assert.equal(get('debugTasks').children.length,0);assert.match(get('debugTaskSummary').textContent,/暂停时/);
 const posts=calls.filter(x=>x.options?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].options.body.pauseId,3);
 assert(!fs.readFileSync(path.join(root,'web/debugger.js'),'utf8').includes('innerHTML'));
 console.log(JSON.stringify({status:'passed',checks:8}));
})().catch(error=>{console.error(error);process.exitCode=1});
