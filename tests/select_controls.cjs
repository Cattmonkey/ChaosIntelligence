'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const input=JSON.parse(fs.readFileSync(0,'utf8')),snapshot=input.pending,root=path.resolve(__dirname,'..'),nodes=new Map();
function element(tag='div'){const classes=new Set();return {tagName:tag.toUpperCase(),value:'',children:[],dataset:{},textContent:'',classList:{toggle(n,on){on?classes.add(n):classes.delete(n)},contains(n){return classes.has(n)}},setAttribute(){},replaceChildren(...xs){this.children=xs},append(...xs){this.children.push(...xs)},querySelectorAll(){return []},querySelector(){return null},scrollIntoView(){}};}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},selected=snapshot.tasks.find(t=>t.kind==='channel_select'),paused={runId:'select-run',version:2,status:'paused',pauseId:5,frames:[{taskId:selected.id,location:{file:'main.chaos',line:1},locals:[]}],taskSnapshot:snapshot,breakpoints:[],commands:['continue'],location:{file:'main.chaos',line:1}},calls=[];
let data=paused,timer;
const context=vm.createContext({document:{getElementById:get,createElement:element},window:{addEventListener(){}},setTimeout:fn=>{timer=fn;return 1},clearTimeout(){},stringifyJSON:JSON.stringify,currentRun:'select-run',busy:false,launching:false,dirty:false,controlDraft:false,showRun(){},fetchRun:async()=>{},$:get,toast(){},api:async(url,options)=>{calls.push({url,options});if(options?.method==='POST')data={...paused,pauseId:options.body.pauseId,status:'running',frames:[],taskSnapshot:{tasks:[],total:0,truncated:false}};return JSON.parse(JSON.stringify(data));}});
vm.runInContext(fs.readFileSync(path.join(root,'web/debugger.js'),'utf8'),context);
const plain=x=>JSON.parse(JSON.stringify(x)),desc=x=>[x,...x.children.flatMap(desc)];
(async()=>{
 context.showRun({id:'select-run',version:2,snapshot:{files:{'main.chaos':'1'}}});await new Promise(setImmediate);
 const row=get('debugTasks').children.find(x=>x.dataset.taskId===selected.id);assert(row);const text=desc(row).map(x=>x.textContent).join('\n');
 assert(text.includes('channel_select · running / channel_select'));assert(text.includes('通信候选（2）'));assert(text.includes('未提交候选'));assert(text.includes('选择超时：30 秒'));assert(text.includes('暂停时剩余：'+selected.timing.remainingSeconds+' 秒'));
 for(const item of selected.selection.cases){assert(text.includes('#'+item.index+' · '+item.op+' · '+item.channelId));assert(get('debugChannels').children.find(x=>x.dataset.channelId===item.channelId).classList.contains('selected'));}
 assert(!text.includes('成员（'));assert.deepEqual(plain(context.window.ChaosDebugger.state.data.taskSnapshot),snapshot);assert.equal(calls.filter(x=>x.options?.method==='POST').length,0);assert.equal(context.dirty,false);
 // A later genuine terminal snapshot must preserve index zero rather than treating it as false.
 const winner=input.completed.tasks.find(task=>task.selection?.index===0);assert(winner);
 data={...paused,pauseId:6,frames:[],taskSnapshot:input.completed};
 await timer();const committed=get('debugTasks').children.find(x=>x.dataset.taskId===winner.id);assert(desc(committed).map(x=>x.textContent).join('\n').includes('已提交候选：#0'));
 await context.window.ChaosDebugger.command('continue');assert.equal(get('debugTasks').children.length,0);assert.equal(get('debugChannels').children.length,0);
 const posts=calls.filter(x=>x.options?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].options.body.pauseId,6);
 assert(!fs.readFileSync(path.join(root,'web/debugger.js'),'utf8').includes('innerHTML'));
 console.log(JSON.stringify({status:'passed',checks:10}));
})().catch(e=>{console.error(e);process.exitCode=1});
