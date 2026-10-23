'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const snapshot=JSON.parse(fs.readFileSync(0,'utf8')),root=process.argv[2]||path.resolve(__dirname,'..'),nodes=new Map();
function element(tag='div'){
 const classes=new Set();return {tagName:tag.toUpperCase(),hidden:true,value:'',checked:true,textContent:'',children:[],dataset:{},attributes:{},classList:{toggle(name,on){on?classes.add(name):classes.delete(name)},contains(name){return classes.has(name)}},setAttribute(key,value){this.attributes[key]=value},replaceChildren(...xs){this.children=xs},append(...xs){this.children.push(...xs)},querySelectorAll(){return []},querySelector(){return null},scrollIntoView(){}};
}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},channel=snapshot.channels[0];
const paused={runId:'channel-run',version:2,status:'paused',pauseId:7,frames:[],taskSnapshot:snapshot,breakpoints:[],commands:['continue'],location:{file:'main.chaos',line:1}};
let data=paused;const calls=[];
const context=vm.createContext({document:{getElementById:get,createElement:element},window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},stringifyJSON:JSON.stringify,currentRun:'channel-run',busy:false,launching:false,dirty:false,controlDraft:false,showRun(){},fetchRun:async()=>{},$:get,toast(){},api:async(url,options)=>{calls.push({url,options});if(options?.method==='POST')data={...paused,status:'running',frames:[],taskSnapshot:{tasks:[],total:0,truncated:false}};return JSON.parse(JSON.stringify(data))}});
vm.runInContext(fs.readFileSync(path.join(root,'web/debugger.js'),'utf8'),context);
const plain=value=>JSON.parse(JSON.stringify(value)),descendants=value=>[value,...value.children.flatMap(descendants)];
(async()=>{
 context.showRun({id:'channel-run',version:2,snapshot:{files:{'main.chaos':'1'}}});await new Promise(resolve=>setImmediate(resolve));
 const rows=get('debugChannels').children;assert.equal(rows.length,100);assert.match(get('debugChannelSummary').textContent,/显示 100 \/ 105.*截断/);
 assert.equal(rows[0].dataset.channelId,channel.id);assert.equal(rows[0].dataset.activeFrame,'false');
 const text=descendants(rows[0]).map(row=>row.textContent).join('\n');assert(text.includes('等待发送（101）'));assert(text.includes('仅显示前 100 项'));assert(text.includes('容量：0 · 已缓冲：0'));
 const taskRow=get('debugTasks').children.find(row=>row.dataset.taskId===channel.sendTaskIds[0]);assert(taskRow);assert(descendants(taskRow).some(row=>row.textContent==='通道：'+channel.id));
 assert.equal(descendants(rows[0]).filter(row=>row.tagName==='BUTTON').length,0);assert.equal(calls.filter(row=>row.options?.method==='POST').length,0);
 assert(!text.includes('hidden-payload'));assert.deepEqual(plain(context.window.ChaosDebugger.state.data.taskSnapshot),snapshot);assert.equal(context.dirty,false);
 await context.window.ChaosDebugger.command('continue');assert.equal(get('debugChannels').children.length,0);assert.equal(get('debugTasks').children.length,0);assert.match(get('debugChannelSummary').textContent,/暂停时/);
 const posts=calls.filter(row=>row.options?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].options.body.pauseId,7);
 assert(!fs.readFileSync(path.join(root,'web/debugger.js'),'utf8').includes('innerHTML'));
 console.log(JSON.stringify({status:'passed',checks:10}));
})().catch(error=>{console.error(error);process.exitCode=1});
