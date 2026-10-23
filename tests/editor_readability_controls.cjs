'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=process.argv[2]||path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/ide.js'),'utf8');
const context=vm.createContext({console,setTimeout,clearTimeout});
vm.runInContext(fs.readFileSync(path.join(root,'web/vendor/codemirror.js'),'utf8'),context);
vm.runInContext(source.slice(0,source.indexOf('(()=>{'))+';globalThis.contracts=ChaosIDEContracts;',context);
const c=context.contracts,plain=x=>JSON.parse(JSON.stringify(x)),checks=[];
const ticket={projectId:'a',epoch:2,file:'main.chaos',revision:3,editRevision:4,source:'same',context:'files:v1'};
assert(c.snapshotMatches(ticket,{...ticket}));
for(const [key,value] of Object.entries({projectId:'b',epoch:3,file:'lib.chaos',revision:4,editRevision:5,source:'other',context:'files:v2'}))assert(!c.snapshotMatches(ticket,{...ticket,[key]:value}),key);
checks.push('Identical text in another project, view epoch, file, edit or imported language context rejects late responses');
const before='// 🙂 preserved\nfn add(a:Int,b:Int){a+b};let note="keep   spacing";add(2,3)',after='// 🙂 preserved\nfn add(a: Int, b: Int) {\n  a + b\n}\n;\nlet note = "keep   spacing";\nadd(2, 3)\n';
const edits=plain(c.formatChanges(before,after));
function apply(text,changes){let result='',position=0;for(const change of changes){assert(change.from>=position);result+=text.slice(position,change.from)+change.insert;position=change.to;}return result+text.slice(position);}
assert.equal(apply(before,edits),after);assert(edits.length>5);assert(edits.every(e=>/^\s*$/u.test(before.slice(e.from,e.to))&&/^\s*$/u.test(e.insert)));checks.push('Formatting touches only whitespace gaps and preserves literal/comment content including Unicode');
assert.deepEqual(plain(c.formatChanges(after,after)),[]);assert.equal(apply('items[0]',c.formatChanges('items[0]','items.[0]\n')),'items.[0]\n');assert.equal(apply('',c.formatChanges('','\n')),'\n');checks.push('No-op, empty source and legacy syntax normalization use exact bounded edits');
// Instantiate the actual editorState factory and its history extension from the
// adapter. No browser or external service is needed for selection/undo behavior.
vm.runInContext('const cm=ChaosCM,theme=[],chaosLanguage=[],semanticField=[];function completionSource(){};'+source.slice(source.indexOf(' const formatBoundaryField='),source.indexOf(' const semanticEffect='))+source.slice(source.indexOf(' function editorState('),source.indexOf(' function syncFromTextarea('))+';globalThis.createEditorState=editorState;',context);
const cm=context.ChaosCM;let state=context.createEditorState(before);const word=before.indexOf('note'),other=before.indexOf('add(2');const range=cm.EditorState.create({doc:before,selection:{anchor:other,head:other+3}}).selection.main;
state=state.update({selection:state.selection.constructor.single(word+4,word).addRange(range)}).state;
const changes=state.changes(edits),selection=state.selection.map(changes),original=state.selection.toJSON();state=state.update({changes,selection,userEvent:'input.format'}).state;
assert.equal(state.doc.toString(),after);assert.equal(state.sliceDoc(state.selection.ranges[0].from,state.selection.ranges[0].to),'note');assert(state.selection.ranges[0].anchor>state.selection.ranges[0].head);assert.equal(state.sliceDoc(state.selection.ranges[1].from,state.selection.ranges[1].to),'add');checks.push('Real CodeMirror changes preserve reverse selection and multiple ranges inside unchanged names');
const undo=cm.historyKeymap.find(item=>item.key==='Mod-z').run,redo=cm.historyKeymap.find(item=>item.key==='Mod-y').run;
const run=command=>command({state,dispatch:transaction=>{state=transaction.state;}});
assert(run(undo));assert.equal(state.doc.toString(),before);assert.deepEqual(plain(state.selection.toJSON()),plain(original));assert(run(redo));assert.equal(state.doc.toString(),after);checks.push('Formatting is one reversible history event restoring exact text and selections');
state=state.update({changes:{from:state.doc.length,insert:' '},userEvent:'input.type'}).state;assert(run(undo));assert.equal(state.doc.toString(),after);assert(run(undo));assert.equal(state.doc.toString(),before);checks.push('Typing immediately after format remains a separate undo event');
state=context.createEditorState('let x=1');state=state.update({changes:{from:state.doc.length,insert:';'},userEvent:'input.type'}).state;const formatted=state.changes(c.formatChanges(state.doc.toString(),'let x = 1;\n'));state=state.update({changes:formatted,selection:state.selection.map(formatted),userEvent:'input.format'}).state;assert(run(undo));assert.equal(state.doc.toString(),'let x=1;');assert(run(undo));assert.equal(state.doc.toString(),'let x=1');checks.push('Typing immediately before formatting remains independently undoable');
const large='let long_name=1;\n'.repeat(18000),largeFormatted='let long_name = 1;\n'.repeat(18000);assert.equal(apply(large,c.formatChanges(large,largeFormatted)),largeFormatted);checks.push('Large source formatting scans whitespace without quadratic character diff');
new vm.Script(source);console.log(JSON.stringify({status:'passed',checks:checks.length,details:checks},null,2));
