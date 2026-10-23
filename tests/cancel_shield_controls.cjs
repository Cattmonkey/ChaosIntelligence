'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const input=JSON.parse(fs.readFileSync(0,'utf8')),snapshot=input.pending,root=path.resolve(__dirname,'..'),nodes=new Map();
function element(tag='div'){
 const classes=new Set();return {tagName:tag.toUpperCase(),value:'',children:[],dataset:{},textContent:'',classList:{toggle(n,on){on?classes.add(n):classes.delete(n)},contains(n){return classes.has(n)}},setAttribute(){},replaceChildren(...xs){this.children=xs},append(...xs){this.children.push(...xs)},querySelectorAll(){return []},querySelector(){return null},scrollIntoView(){}};
}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
const shield=snapshot.shields[0],selected=snapshot.tasks.find(t=>t.id===shield.taskId);assert(selected);
const frames=[{taskId:null,name:'module',location:{file:'main.chaos',line:1},locals:[]},{taskId:selected.id,name:'work',location:{file:'main.chaos',line:3},locals:[]}];
const paused={runId:'shield-run',version:2,status:'paused',pauseId:5,frames,taskSnapshot:snapshot,breakpoints:[],commands:['continue'],location:frames[0].location},calls=[];
let data=paused,timer;
const context=vm.createContext({document:{getElementById:get,createElement:element},window:{addEventListener(){}},setTimeout:fn=>{timer=fn;return 1},clearTimeout(){},stringifyJSON:JSON.stringify,currentRun:'shield-run',busy:false,launching:false,dirty:false,controlDraft:false,showRun(){},fetchRun:async()=>{},$:get,toast(){},api:async(url,options)=>{calls.push({url,options});if(options?.method==='POST')data={...paused,pauseId:options.body.pauseId,status:'running',frames:[],taskSnapshot:{tasks:[],total:0,truncated:false}};return JSON.parse(JSON.stringify(data));}});
vm.runInContext(fs.readFileSync(path.join(root,'web/debugger.js'),'utf8'),context);
const plain=x=>JSON.parse(JSON.stringify(x)),desc=x=>[x,...x.children.flatMap(desc)],text=x=>desc(x).map(x=>x.textContent).join('\n');
(async()=>{
 context.showRun({id:'shield-run',version:2,snapshot:{files:{'main.chaos':'1\n2\n3'}}});await new Promise(setImmediate);
 let rows=get('debugShields').children;assert.equal(rows.length,100);assert.match(get('debugShieldSummary').textContent,/显示 100 \/ 105.*截断/);
 assert.equal(rows[0].dataset.shieldId,shield.id);assert.equal(rows[0].dataset.activeFrame,'false');
 assert(text(rows[0]).includes('CancelShield · active'));assert(text(rows[0]).includes('活动深度：1'));assert(text(rows[0]).includes('取消请求：是 · 取消延期：是'));
 const link=desc(rows[0]).find(x=>x.tagName==='BUTTON');assert(link);link.onclick();rows=get('debugShields').children;
 assert.equal(rows[0].dataset.activeFrame,'true');assert.equal(rows[1].dataset.activeFrame,'true');assert.equal(rows[2].dataset.activeFrame,'false');
 assert(!desc(rows[2]).some(x=>x.tagName==='BUTTON'));assert(text(rows[2]).includes('任务：尚未进入 · 活动深度：0'));
 const taskRow=get('debugTasks').children.find(x=>x.dataset.taskId===selected.id);assert.equal(taskRow.dataset.activeFrame,'true');assert(text(taskRow).includes('取消延期：是 · 屏蔽深度：2'));
 assert.deepEqual(plain(context.window.ChaosDebugger.state.data.taskSnapshot),snapshot);assert.equal(calls.filter(x=>x.options?.method==='POST').length,0);assert.equal(context.dirty,false);
 // A real later checkpoint, after lexical exit, replaces active flags and depths.
 data={...paused,pauseId:6,frames:[],taskSnapshot:input.completed};await timer();
 const closed=get('debugShields').children[0];assert(text(closed).includes('CancelShield · closed'));assert(text(closed).includes('活动深度：0'));assert(!text(closed).includes('取消延期：是'));
 await context.window.ChaosDebugger.command('continue');assert.equal(get('debugShields').children.length,0);assert.equal(get('debugTasks').children.length,0);assert.match(get('debugShieldSummary').textContent,/暂停时/);
 const posts=calls.filter(x=>x.options?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].options.body.pauseId,6);
 assert(!fs.readFileSync(path.join(root,'web/debugger.js'),'utf8').includes('innerHTML'));
 assert(fs.readFileSync(path.join(root,'web/index.html'),'utf8').includes('id="debugShields"'));
 console.log(JSON.stringify({status:'passed',checks:18}));
})().catch(e=>{console.error(e);process.exitCode=1});
