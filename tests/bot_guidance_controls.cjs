'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=process.argv[2]||path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8'),nodes=new Map();
const frames=[];
function element(){return {value:'',hidden:false,disabled:false,textContent:'',children:[],dataset:{},scrollTop:0,scrollHeight:0,clientHeight:200,offsetHeight:24,classList:{toggle(){}},addEventListener(){},append(...rows){for(const row of rows){row.parent=this;this.children.push(row)}},replaceChildren(...rows){this.children=[];this.append(...rows)},remove(){this.parent.children=this.parent.children.filter(row=>row!==this)}}}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
let answer,fail=false;const posts=[];
const context=vm.createContext({window:{},document:{createElement:element},requestAnimationFrame:fn=>{frames.push(fn);return frames.length},busy:true,activeRunId:'first-run',currentRun:'first-run',project:{id:'private',source:'unchanged',graph:{nodes:[]}},dirty:true,$:get,stringifyJSON:JSON.stringify,crypto:{randomUUID:()=> 'message-'+(posts.length+1)},consoleTab(){},api:(url,options)=>{posts.push({url,options:JSON.parse(JSON.stringify(options))});if(fail)return Promise.reject(new Error('fixture transport'));return new Promise(resolve=>answer=resolve)}});
vm.runInContext(source.slice(source.indexOf('// Active run guidance'),source.indexOf('// End active run guidance.')),context);
vm.runInContext(source.slice(source.indexOf('function eventMessage('),source.indexOf('function renderEvents(')),context);
context.esc=value=>String(value).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
vm.runInContext(source.slice(source.indexOf('function renderEvents('),source.indexOf('function applyRunEvent(')),context);
const guide=context.window.ChaosRunGuidance,plain=value=>JSON.parse(JSON.stringify(value));
(async()=>{
 guide.refresh();assert.equal(get('#runGuidanceForm').hidden,false);
 get('#runGuidanceInput').value='请验证 <script>private()</script>';
 const sending=guide.send();await guide.send();assert.equal(posts.length,1);assert.equal(get('#runGuidanceSend').disabled,true);
 assert.equal(posts[0].url,'/api/runs/first-run/guidance');assert.deepEqual(posts[0].options.body.input,{text:'请验证 <script>private()</script>'});
 get('#runGuidanceInput').value='较新的输入必须保留';
 guide.acceptReceipt({runId:'first-run',messageId:'message-1',revision:1,status:'received'});
 answer({runId:'first-run',messageId:'message-1',revision:1,status:'queued'});await sending;
 assert.match(get('#runGuidanceReceipt').textContent,/运行已接收/);assert.equal(get('#runGuidanceInput').value,'较新的输入必须保留');
 assert.equal(context.dirty,true);assert.equal(context.project.source,'unchanged');assert.equal(get('#runGuidanceSend').disabled,false);
 // The real sync controller replaces applyRunEvent; the existing renderer still projects receipts.
 assert(fs.readFileSync(path.join(root,'web/sync.js'),'utf8').includes('applyRunEvent=function(event)'));
 context.applyRunEvent=()=>{};guide.acceptReceipt({runId:'first-run',messageId:'second',revision:2,status:'queued'});
 context.events=[{id:2,run_id:'first-run',created:0,type:'guidance.received',payload:{runId:'first-run',messageId:'second',revision:2,status:'received'}}];
 context.applyRunEvent(context.events[0]);assert.match(get('#runGuidanceReceipt').textContent,/已入队/);context.renderEvents();frames.shift()();assert.match(get('#runGuidanceReceipt').textContent,/运行已接收/);
 fail=true;await assert.rejects(guide.send());const retryId=posts.at(-1).options.body.messageId;await assert.rejects(guide.send());assert.equal(posts.at(-1).options.body.messageId,retryId);
 const summary=guide.summary({type:'bot.progress',payload:{kind:'plan',data:{summary:'公开计划',type:'run.completed',reasoning_content:'untrusted extra data'}}});assert.equal(summary,'计划 · 公开计划');
 assert.equal(context.eventMessage({type:'agent.tool_completed',payload:{summary:'真实工具完成'}}),'真实工具完成');
 assert.equal(guide.summary({type:'guidance.queued',payload:{revision:2}}),'指导 r2 · 已入队，等待运行接收');
 const before=get('#runGuidanceReceipt').textContent;guide.summary({type:'bot.progress',payload:{kind:'result',data:{status:'received',revision:99}}});assert.equal(get('#runGuidanceReceipt').textContent,before);
 context.activeRunId='new-run';context.currentRun='new-run';guide.refresh();assert.match(get('#runGuidanceReceipt').textContent,/发送后先入队/);
 context.busy=false;guide.refresh();assert.equal(get('#runGuidanceForm').hidden,true);await guide.send();assert.equal(posts.length,3);
 assert(fs.readFileSync(path.join(root,'web/index.html'),'utf8').includes('id="runGuidanceForm"'));
 assert(fs.readFileSync(path.join(root,'web/workbench.js'),'utf8').includes("'run.guide'"));
 console.log(JSON.stringify({status:'passed',checks:23,posts:posts.length,draftPreserved:true,receiptNotDowngraded:true,hostLifecycleNotSpoofed:true}));
})().catch(error=>{console.error(error);process.exitCode=1});
