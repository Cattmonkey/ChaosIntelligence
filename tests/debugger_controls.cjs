'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=process.argv[2]||path.resolve(__dirname,'..');
const elements=new Map();
function element(){return {hidden:true,value:'',checked:true,textContent:'',children:[],classList:{toggle(){}},setAttribute(){},replaceChildren(){this.children=[];},append(...v){this.children.push(...v);},querySelectorAll(){return [];},querySelector(){return null;},scrollIntoView(){}};}
const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
const calls=[],pause={runId:'r',projectId:'p',version:1,status:'paused',pauseId:7,location:{file:'main.chaos',line:2},frames:[],breakpoints:[],commands:['continue','step_into','step_over','cancel','breakpoints']};
const context=vm.createContext({console,document:{getElementById:get,createElement:element},window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},stringifyJSON:JSON.stringify,currentRun:'r',busy:false,launching:false,dirty:false,controlDraft:false,showRun(){},fetchRun:async()=>{},$:get,
 api:async(url,options)=>{calls.push({url,options});return JSON.parse(JSON.stringify(pause));},toast(){},save:async()=>{throw new Error('Save conflict; draft retained');}});
vm.runInContext(fs.readFileSync(path.join(root,'web/debugger.js'),'utf8'),context);
const api=context.window.ChaosDebugger,plain=x=>JSON.parse(JSON.stringify(x));
(async()=>{
 assert.deepStrictEqual(plain(api.parseBreakpoints('main.chaos:3\nlib/中文.chaos:12')), [{file:'main.chaos',line:3},{file:'lib/中文.chaos',line:12}]);
 assert.throws(()=>api.parseBreakpoints('no-line'));
 assert.deepStrictEqual(plain(api.parseBreakpoints('  ')),[]);
 context.showRun({id:'r',version:1,snapshot:{files:{'main.chaos':'1\n2'}}});await new Promise(resolve=>setImmediate(resolve));
 assert.strictEqual(api.state.data.pauseId,7);assert(!calls.some(c=>c.options?.method==='POST'));
 await api.command('continue');const command=calls.find(c=>c.options?.method==='POST');assert.strictEqual(command.options.body.pauseId,7);assert.strictEqual(command.options.body.command,'continue');
 get('debugBreakpoints').value='invalid';const before=calls.length;await api.command('breakpoints');assert.strictEqual(calls.length,before);assert.match(get('debugStatus').textContent,/断点格式/);
 // Complete an old command only after another run has attached.
 let resolveOld;
 const oldRun=plain(pause),newRun={...plain(pause),runId:'next',pauseId:21};
 context.api=async(url,options)=>{
  if(options?.method==='POST')return new Promise(resolve=>{resolveOld=resolve;});
  return url.includes('/next/')?newRun:oldRun;
 };
 const pendingCommand=api.command('continue');await new Promise(resolve=>setImmediate(resolve));
 context.currentRun='next';context.showRun({id:'next',version:1,snapshot:{files:{'main.chaos':'1'}}});await new Promise(resolve=>setImmediate(resolve));
 assert.strictEqual(api.state.data.pauseId,21);resolveOld({...oldRun,status:'running'});await pendingCommand;
 assert.strictEqual(api.state.runId,'next');assert.strictEqual(api.state.data.pauseId,21);assert.strictEqual(api.state.data.status,'paused');
 assert.strictEqual(context.dirty,false);assert.strictEqual(context.controlDraft,false);
 assert(!fs.readFileSync(path.join(root,'web/debugger.js'),'utf8').includes('innerHTML'));
 console.log(JSON.stringify({status:'passed',checks:8,details:['Unicode breakpoint paths','invalid syntax rejected','empty breakpoints clear','attaching paused run never resumes','resume carries exact pause generation','bad breakpoint update leaves state untouched','rendering preserves drafts and uses text nodes','late command response cannot overwrite another selected run']}));
})().catch(error=>{console.error(error);process.exitCode=1;});
