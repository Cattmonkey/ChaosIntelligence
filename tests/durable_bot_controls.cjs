"use strict";
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert'),root=process.argv[2];
const source=fs.readFileSync(path.join(root,'web/processes.js'),'utf8'),context=vm.createContext({window:{},document:{querySelector:()=>null}});vm.runInContext(source,context);const c=context.window.ChaosProcessContracts;
const first={key:'worker',runId:null,messageId:'1-guide',revision:1,status:'queued'};let current=c.guideReceipt(null,first,'worker');assert.strictEqual(current,first);
current=c.guideReceipt(current,{...first,status:'received'},'worker');assert.equal(current.status,'received');assert.equal(c.guideReceipt(current,first,'worker').status,'received');
current=c.guideReceipt(current,{...first,status:'applied'},'worker');assert.equal(current.status,'applied');assert.equal(c.guideReceipt(current,{...first,status:'received'},'worker').status,'applied');
assert.strictEqual(c.guideReceipt(current,{...first,key:'other',revision:99},'worker'),current);assert.strictEqual(c.guideReceipt(current,{...first,revision:0,status:'applied'},'worker'),current);assert.strictEqual(c.guideReceipt(current,{...first,status:'completed'},'worker'),current);
assert.equal(c.guideReceipt(current,{...first,messageId:'2-guide',revision:2},'worker').status,'queued');assert(c.guideLabel(current).includes('未证明动作完成'));assert(!source.includes('innerHTML'));assert(source.includes("action('/guidance'"));assert(source.includes('forms.messageId'));assert(source.includes('p.messageRevision??p.guidanceRevision??p.revision'));new vm.Script(source);
console.log(JSON.stringify({status:'passed',checks:13,receiptStagesMonotonic:true,noActionSuccessClaim:true}));
