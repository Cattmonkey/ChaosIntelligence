'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=process.argv[2]||path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/ide.js'),'utf8');
const context=vm.createContext({});
vm.runInContext(source.slice(0,source.indexOf('(()=>{'))+';globalThis.contracts=ChaosIDEContracts;',context);
const c=context.contracts,files={'main.chaos':'let result=1; result','lib.chaos':'// 🙂\nlet answer=42; answer'};
assert(c.definitionTarget({file:'lib.chaos',from:10,to:16},files));
for(const target of [null,{file:'missing.chaos',from:0,to:1},{file:'toString',from:0,to:1},{file:'main.chaos',from:-1,to:1},{file:'main.chaos',from:0,to:1000},{file:'main.chaos',from:2,to:2},{file:'main.chaos',from:0.5,to:2}])assert.equal(c.definitionTarget(target,files),null);
const implementation=source.slice(source.indexOf(' async function goToDefinition('),source.indexOf(' function showDiagnostics('));
async function scenario(change){
 let resolve;const pending=new Promise(done=>{resolve=done}),actions=[];
 const ticket={projectId:'p',epoch:1,file:'main.chaos',revision:2,editRevision:2,source:files['main.chaos'],context:'all drafts'};
 const state={snapshot:{...ticket},editorFile:'main.chaos',editor:{state:{selection:{main:{head:17}}},dispatch:arg=>actions.push(['selection',arg.selection]),focus:()=>actions.push(['focus'])}};
 const scope=vm.createContext({state,project:{files},ChaosIDEContracts:c,analysisSnapshot:()=>({...state.snapshot}),snapshotCurrent:before=>c.snapshotMatches(before,state.snapshot),analyze:()=>pending,analysisBody:()=>({files}),switchFile:file=>{state.editorFile=file;actions.push(['file',file]);},toast:text=>actions.push(['toast',text])});
 vm.runInContext(implementation+';globalThis.navigate=goToDefinition;',scope);
 const operation=scope.navigate();change?.(state);resolve({definition:{file:'lib.chaos',from:10,to:16}});await operation;return actions;
}
(async()=>{
 const accepted=await scenario();assert.equal(accepted[0][1],'lib.chaos');assert.deepEqual(JSON.parse(JSON.stringify(accepted[1][1])),{anchor:10,head:16});assert.equal(accepted[2][0],'focus');
 for(const key of ['projectId','epoch','file','revision','editRevision','source','context'])assert.deepEqual(await scenario(state=>{state.snapshot[key]='changed';}),[],key);
 assert.deepEqual(await scenario(state=>{state.editor.state.selection.main.head=0;}),[],'new cursor');
 assert(source.includes("key:'F12'"));assert(source.includes("definitionButton.id='ideDefinitionButton'"));
 console.log(JSON.stringify({status:'passed',checks:12,details:['bounded source-backed target','actual navigation selects exact span and focuses','all source/project/context revisions reject late navigation','new cursor rejects late navigation','F12 and labeled button wired']}));
})().catch(error=>{console.error(error);process.exit(1);});
