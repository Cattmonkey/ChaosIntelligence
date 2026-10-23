'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
function node(tag){return {tagName:tag,children:[],dataset:{},style:{},append(...items){this.children.push(...items)},setAttribute(){},querySelectorAll(){return this.children.filter(n=>n.tagName==='button')},replaceChildren(...items){this.children=items}}}
const mount=node('section'),ctx=vm.createContext({document:{querySelector:s=>s==='#artifactPanel'?mount:node('div'),createElement:node,head:node('head')},window:{addEventListener(){}},MutationObserver:class{observe(){}},guarded:f=>f,currentRun:'r',project:{id:'p',version:2},dirty:false,controlDraft:false,snapshotView:false,busy:false,launching:false,clone:x=>JSON.parse(JSON.stringify(x)),setTimeout,clearTimeout,console});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/artifacts.js'),'utf8'),ctx);
const run=s=>vm.runInContext(s,ctx);run('window.ChaosArtifacts.state.run={id:"r",projectId:"p",version:2,status:"completed"};window.ChaosArtifacts.state.definition={};window.ChaosArtifacts.state.original=window.ChaosArtifacts.state.definition');assert(run('window.ChaosArtifacts.actionAllowed()'));
for(const flag of ['dirty','controlDraft','snapshotView','busy','launching']){run(flag+'=true');assert(!run('window.ChaosArtifacts.actionAllowed()'));run(flag+'=false')}
run('project.version=3');assert(!run('window.ChaosArtifacts.actionAllowed()'));run('project.version=2;window.ChaosArtifacts.state.partial=true');assert(!run('window.ChaosArtifacts.actionAllowed()'));run('window.ChaosArtifacts.state.partial=false;window.ChaosArtifacts.state.definition={}');assert(!run('window.ChaosArtifacts.actionAllowed()'));
console.log(JSON.stringify({status:'passed',checks:9}));
