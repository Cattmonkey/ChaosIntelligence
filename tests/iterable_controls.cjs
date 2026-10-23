'use strict';
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const fixture=JSON.parse(fs.readFileSync(0,'utf8')),source=fixture.source,requests=[],copies=[];
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.className='';this.listeners={};this.raw='';this.parent=null;this.hidden=false;this.classList={add:(...names)=>{this.className=[...new Set([...this.className.split(/\s+/).filter(Boolean),...names])].join(' ')},remove:(...names)=>{this.className=this.className.split(/\s+/).filter(name=>name&&!names.includes(name)).join(' ')},toggle:(name,on)=>{const exists=this.className.split(/\s+/).includes(name),next=on===undefined?!exists:!!on;next?this.classList.add(name):this.classList.remove(name);return next},contains:name=>this.className.split(/\s+/).includes(name)}}
 set textContent(value){this.raw=String(value);this.children=[]}get textContent(){return this.raw+this.children.map(child=>child.textContent).join('')}
 get childNodes(){return this.children}
 append(...children){for(const child of children){if(child.tagName==='#fragment'){this.append(...child.children)}else{child.parent=this;this.children.push(child)}}}
 prepend(child){child.parent=this;this.children.unshift(child)}replaceChildren(...children){this.raw='';this.children=[];this.append(...children)}
 setAttribute(key,value){this.attributes[key]=String(value);if(key==='title')this.title=String(value);if(key==='class')this.className=String(value)}getAttribute(key){return this.attributes[key]??null}
 addEventListener(name,handler){(this.listeners[name]??=[]).push(handler)}remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this)}
 cloneNode(deep=false){const result=new Element(this.tagName);result.raw=this.raw;result.className=this.className;result.dataset={...this.dataset};result.attributes={...this.attributes};for(const name of ['title','hidden','value','type','tabIndex'])if(Object.hasOwn(this,name))result[name]=this[name];if(deep)result.append(...this.children.map(child=>child.cloneNode(true)));return result}
 matches(selector){if(selector==='*')return true;if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));const match=/^([\w-]+)?\[([\w-]+)(?:="([^"]*)")?\]$/.exec(selector);if(match){const [,tag,key,value]=match,actual=key==='title'&&Object.hasOwn(this,'title')?this.title:this.attributes[key];return (!tag||this.tagName===tag)&&actual!==undefined&&(value===undefined||actual===value)}return this.tagName===selector}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null}
 querySelectorAll(selector){const all=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);return all.filter(child=>child.matches(selector))}
 contains(child){for(let current=child;current;current=current.parent)if(current===this)return true;return false}
 closest(selector){const parts=selector.split(' ');for(let current=this;current;current=current.parent)if(current.matches(parts[parts.length-1])&&(parts.length===1||current.parent?.closest(parts.slice(0,-1).join(' '))))return current;return null}
 dispatchEvent(event){if(!event.target)event.target=this;event.currentTarget=this;for(const handler of this.listeners[event.type]||[])handler.call(this,event);this['on'+event.type]?.call(this,event);if(event.bubbles!==false&&this.parent)this.parent.dispatchEvent(event);return true}
 click(){return this.dispatchEvent({type:'click',target:this,bubbles:true})}
 focus(){document.activeElement=this}
}
const document={createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('#text');n.textContent=text;return n},createDocumentFragment:()=>new Element('#fragment'),activeElement:null};
const context=vm.createContext({document,window:{isSecureContext:true},navigator:{clipboard:{writeText:async value=>copies.push(value)}},AbortController,fetch:async(url,options)=>{requests.push(JSON.parse(options.body));const data=JSON.parse(JSON.stringify(fixture.analysis));data.referenceHints.push({from:source.indexOf('😃')+1,to:source.indexOf('😃')+2,detail:'INVALID_SURROGATE'},{from:-1,to:source.length,detail:'INVALID_NEGATIVE'});return {ok:true,json:async()=>data}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/code.js'),'utf8'),context);
(async()=>{
 const pre=context.window.ChaosCode.render(source,{language:'chaos',wrap:true});await pre.ready;
 assert.equal(pre.dataset.highlight,'ready');assert.equal(pre.querySelector('code').textContent,source);assert.equal(context.window.ChaosCode.source(pre),source);
 const spans=Array.from(pre.querySelector('code').querySelectorAll('span'));
 for(const name of ['map','gather','apply']){const item=spans.find(item=>item.textContent===name);assert(item);assert(item.className.includes('chaos-token-function'));}
 const mapped=spans.find(item=>item.textContent==='map');assert(mapped.title.includes('map(fn, xs) -> List'));assert(mapped.title.includes('同步可迭代'));
 const apply=spans.find(item=>item.textContent==='apply');assert(apply.title.includes('kwargs: Record | Null = null'));
 assert(spans.some(item=>item.textContent==='jobs'&&item.title.includes('List')));
 assert(!Array.from(pre.querySelectorAll('*')).some(item=>String(item.title).includes('INVALID_')));
 pre.querySelector('.copy-code').click();await new Promise(resolve=>setImmediate(resolve));assert.equal(pre.querySelector('.chaos-code-status').textContent,'已复制原始代码');assert.deepEqual(copies,[source]);assert.equal(requests.length,1);assert.equal(requests[0].source,source);
 // DOM cloning preserves presentation but never shares tree state or event handlers.
 const original=pre.querySelector('code'),first=original.querySelector('span');first.addEventListener('probe',()=>{});
 const cloned=original.cloneNode(true);assert.notStrictEqual(cloned,original);assert.equal(cloned.textContent,source);
 const child=cloned.querySelector('span');assert.notStrictEqual(child,first);assert.notStrictEqual(child.dataset,first.dataset);assert.deepEqual(child.listeners,{});
 child.dataset.probe='copy';child.textContent='changed';assert.equal(original.textContent,source);assert.equal(first.dataset.probe,undefined);
 pre.querySelector('.chaos-code-help').click();assert.equal(pre.querySelector('.chaos-code-hint').hidden,false);assert(pre.querySelector('.chaos-code-hint-text').textContent.length>0);
 const noAnalysis=context.window.ChaosCode.render('throw "do not execute"',{language:'chaos',analyze:false});await noAnalysis.ready;assert.equal(requests.length,1);assert.equal(noAnalysis.querySelector('code').textContent,'throw "do not execute"');
 assert(!fs.readFileSync(path.join(__dirname,'../web/code.js'),'utf8').includes('innerHTML'));
 console.log(JSON.stringify({status:'passed',checks:12}));
})().catch(error=>{console.error(error);process.exitCode=1});
