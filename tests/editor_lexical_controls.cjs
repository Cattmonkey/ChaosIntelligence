"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(process.argv[2]),window={};
// The standalone editor keeps working without completion, a DOM or a backend.
vm.runInNewContext(fs.readFileSync(path.join(root,'web/editor.js'),'utf8'),{window});
const api=window.ChaosEditor;
assert.equal(typeof api.formatChanges,'function');
assert.equal(typeof api.createLexicalLanguage,'function');
assert.equal(typeof api.lexicalHighlights,'function');
assert.deepEqual(Array.from(api.createLexicalLanguage(null)),[]);
assert.deepEqual(Array.from(api.lexicalHighlights({})),[]);
const tags=Object.fromEntries(['string','number','comment','operator','punctuation'].map(role=>[role,role]));
const cm={StreamLanguage:{define:parser=>parser},tags,HighlightStyle:{define:styles=>styles},syntaxHighlighting:styles=>styles};
const parser=api.createLexicalLanguage(cm),state=parser.startState();
function scan(text){
 const stream={pos:0,eol(){return this.pos>=text.length;},peek(){return text[this.pos];},next(){return text[this.pos++];},
  eatSpace(){const m=/^\s+/.exec(text.slice(this.pos));if(!m)return false;this.pos+=m[0].length;return true;},
  match(pattern){const m=typeof pattern==='string'?(text.startsWith(pattern,this.pos)?[pattern]:null):pattern.exec(text.slice(this.pos));if(!m)return false;this.pos+=m[0].length;return m;},
  skipToEnd(){this.pos=text.length;}};
 const spans=[];while(!stream.eol()){const start=stream.pos,role=parser.token(stream,state);assert.ok(stream.pos>start,'tokenizer must advance');spans.push({text:text.slice(start,stream.pos),role});}return spans;
}
let checks=0;
{
 const spans=scan('fn agent function42 数据123 Int await');
 assert.ok(spans.every(token=>token.role===null),'names and keywords require backend semantics');checks++;
}
{
 const spans=scan('42 + 1e2; -3.5 # comment');
 for(const [text,role]of [['42','number'],['+','operator'],['1e2','number'],[';','punctuation'],['-','operator'],['3.5','number'],['# comment','comment']])assert.ok(spans.some(token=>token.text===text&&token.role===role),text);
 checks++;
}
{
 const first=scan('let text="first');assert.equal(first.at(-1).role,'string');
 const copied=parser.copyState(state);assert.equal(copied.quote,'"');assert.notEqual(copied,state);
 const next=scan('    // quoted # value" + 2');
 assert.equal(next[0].text,'    // quoted # value"');assert.equal(next[0].role,'string');
 assert.equal(state.quote,null);assert.ok(next.some(token=>token.role==='number'));checks++;
}
{
 const spans=scan(String.raw`"a\"b" + 2 // done`);
 assert.equal(spans[0].text,String.raw`"a\"b"`);assert.equal(spans[0].role,'string');
 assert.equal(spans.at(-1).role,'comment');checks++;
}
{
 scan('{ [ (');assert.equal(state.depth,3);assert.equal(parser.indent(state,'}',{unit:2}),4);
 scan(') ] }');assert.equal(state.depth,0);scan('}');assert.equal(state.depth,0);checks++;
}
{
 const styles=api.lexicalHighlights(cm);assert.equal(styles.length,1);
 assert.deepEqual(Array.from(styles[0],entry=>entry.tag),Object.keys(tags));
 for(const entry of styles[0]){assert.equal(entry.color,'var(--syntax-'+entry.tag+')');assert.equal(entry.fontStyle,undefined);}
 checks++;
}
process.stdout.write(JSON.stringify({passed:true,checks,sharedLexer:true,standaloneEmbedded:true,dom:false,network:false})+'\n');
