"use strict";
const fs=require("fs"),path=require("path"),vm=require("vm"),assert=require("assert");
const root=process.argv[2]||path.resolve(__dirname,".."),source=fs.readFileSync(path.join(root,"web/app.js"),"utf8"),elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,{value:"",textContent:"",innerHTML:"",disabled:false,hidden:false,classList:{toggle(){}}});return elements.get(id)};
const context=vm.createContext({window:{},busy:true,activeRunId:"run-real",currentRun:"run-real",project:{graph:{nodes:[]}},$:get,stringifyJSON:JSON.stringify,crypto:{randomUUID:()=>"message-real"},consoleTab(){},api(){throw Error("No request expected")},events:[]});
vm.runInContext(source.slice(source.indexOf("// Active run guidance"),source.indexOf("// End active run guidance.")),context);
vm.runInContext(source.slice(source.indexOf("function eventMessage("),source.indexOf("function applyRunEvent(")),context);
context.esc=value=>String(value).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const guide=context.window.ChaosRunGuidance;
const names={"bot.scope.opened":"Bot 开始","bot.context.applied":"Bot 上下文已应用","bot.action.admitted":"Bot 动作已准入","bot.action.completed":"Bot 动作有结果","bot.scope.closed":"Bot 范围已结束"};
let checks=0;
for(const [type,title]of Object.entries(names)){
 const event={type,payload:{scopeId:"scope-real",decisionId:"decision-real",revision:7,actionId:"action-real",status:"uncertain"}};
 const summary=guide.summary(event);assert(summary.startsWith(title));assert(summary.includes("r7"));assert(summary.includes("scope-real"));assert(summary.includes("decision-real"));assert(summary.includes("action-real"));assert(summary.includes("uncertain"));checks+=6;
}
guide.acceptReceipt({runId:"run-real",messageId:"message-real",revision:1,status:"queued"});
const before=get("#runGuidanceReceipt").textContent;
const summary=guide.summary({type:"bot.progress",payload:{kind:"result",data:{summary:"public summary",type:"bot.context.applied",scopeId:"spoofed",revision:99,status:"received"}}});
assert.equal(summary,"结果 · public summary");assert.equal(get("#runGuidanceReceipt").textContent,before);checks+=2;
context.events=[{id:4,run_id:"run-real",created:0,type:"bot.context.applied",payload:{scopeId:"scope-real",messageId:"message-real",revision:1}},{id:5,run_id:"run-real",created:0,type:"bot.action.admitted",payload:{scopeId:"scope-real",decisionId:"decision-real",revision:1,actionId:"action-real",kind:"tool"}}];
context.renderEvents();assert(get("#eventsPanel").innerHTML.includes("Bot 上下文已应用"));assert(get("#eventsPanel").innerHTML.includes("动作 action-real"));assert.equal(get("#runGuidanceReceipt").textContent,before);checks+=3;
assert.equal(guide.summary({type:"toString",payload:{}}),null);checks++;
console.log(JSON.stringify({status:"passed",checks,hostIdentitiesPreserved:true,publicDataCannotApplyContext:true,existingRendererUsed:true}));
