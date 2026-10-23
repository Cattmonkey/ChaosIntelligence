"use strict";
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const snapshot=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(__dirname,'..'),nodes=new Map();
function element(tag='div'){const classes=new Set();return {tagName:tag.toUpperCase(),value:'',children:[],dataset:{},textContent:'',classList:{toggle(n,on){on?classes.add(n):classes.delete(n)},contains(n){return classes.has(n)}},setAttribute(){},replaceChildren(...xs){this.children=xs},append(...xs){this.children.push(...xs)},querySelectorAll(){return []},querySelector(){return null},scrollIntoView(){}};}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},paused={runId:'wait-run',version:2,status:'paused',pauseId:5,frames:[],taskSnapshot:snapshot,breakpoints:[],commands:['continue'],location:{file:'main.chaos',line:1}},calls=[];
let data=paused;
const context=vm.createContext({document:{getElementById:get,createElement:element},window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},stringifyJSON:JSON.stringify,currentRun:'wait-run',busy:false,launching:false,dirty:false,controlDraft:false,showRun(){},fetchRun:async()=>{},$:get,toast(){},api:async(url,options)=>{calls.push({url,options});if(options?.method==='POST')data={...paused,status:'running',frames:[],taskSnapshot:{tasks:[],total:0,truncated:false}};return JSON.parse(JSON.stringify(data));}});
vm.runInContext(fs.readFileSync(path.join(root,'web/debugger.js'),'utf8'),context);
const plain=x=>JSON.parse(JSON.stringify(x)),desc=x=>[x,...x.children.flatMap(desc)];
(async()=>{
 context.showRun({id:'wait-run',version:2,snapshot:{files:{'main.chaos':'1'}}});await new Promise(setImmediate);
 const sleep=snapshot.tasks.find(t=>t.kind==='sleep'),wait=snapshot.tasks.find(t=>t.kind==='wait');assert(sleep&&wait);
 for(const task of [sleep,wait]){
  const row=get('debugTasks').children.find(x=>x.dataset.taskId===task.id);assert(row);
  const text=desc(row).map(x=>x.textContent).join('\n');assert(text.includes(task.kind+' · '+task.state+' / '+task.phase));
  assert(text.includes('暂停时剩余：'+task.timing.remainingSeconds+' 秒'));
  if(task===sleep)assert(text.includes('计时：30 秒'));
  else {assert(text.includes('等待条件：all · 超时：20 秒'));assert(text.includes('成员（1）：'+sleep.id));}
  assert.equal(desc(row).filter(x=>x.tagName==='BUTTON').length,0);
 }
 assert.deepEqual(plain(context.window.ChaosDebugger.state.data.taskSnapshot),snapshot);assert.equal(context.dirty,false);assert.equal(calls.filter(x=>x.options?.method==='POST').length,0);
 await context.window.ChaosDebugger.command('continue');assert.equal(get('debugTasks').children.length,0);
 const posts=calls.filter(x=>x.options?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].options.body.pauseId,5);
 assert(!fs.readFileSync(path.join(root,'web/debugger.js'),'utf8').includes('innerHTML'));
 console.log(JSON.stringify({status:'passed',checks:8}));
})().catch(e=>{console.error(e);process.exitCode=1});
