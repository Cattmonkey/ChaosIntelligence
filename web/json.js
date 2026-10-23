'use strict';
// Exact JSON numbers are values, never magic user-record keys.
class ExactNumber{
  constructor(raw){this.raw=raw;Object.freeze(this);}
  toString(){return this.raw;}
  valueOf(){return Number(this.raw);}
}
function parseJSON(text){
  let pos=0,nodes=0;
  const white=()=>{while(/[\t\n\r ]/.test(text[pos]||'x'))pos++;};
  function value(depth=0){
    white();if(depth>400||++nodes>400000)throw new Error('JSON exceeds structural limit');
    const ch=text[pos];
    if(ch==='"'){
      const start=pos++;let escaped=false;
      while(pos<text.length){const c=text[pos++];if(c==='"'&&!escaped)return JSON.parse(text.slice(start,pos));if(c==='\\'&&!escaped)escaped=true;else escaped=false;}
      throw new Error('Unterminated JSON string');
    }
    if(ch==='['||ch==='{'){
      const array=ch==='[',result=array?[]:Object.create(null),end=array?']':'}';pos++;white();
      if(text[pos]===end){pos++;return result;}
      while(true){
        if(array)result.push(value(depth+1));
        else{
          white();if(text[pos]!=='"')throw new Error('JSON object key must be a string');
          const key=value(depth+1);white();if(text[pos++]!==':')throw new Error('Expected colon');
          Object.defineProperty(result,key,{value:value(depth+1),writable:true,enumerable:true,configurable:true});
        }
        white();const next=text[pos++];if(next===end)return result;if(next!==',')throw new Error('Expected comma');
      }
    }
    for(const [token,result]of [['true',true],['false',false],['null',null]])if(text.startsWith(token,pos)){pos+=token.length;return result;}
    const found=/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(text.slice(pos));
    if(!found)throw new Error('Invalid JSON at '+pos);
    const raw=found[0];pos+=raw.length;if(raw==='-0')return 0;
    return /[.eE]/.test(raw)||!Number.isSafeInteger(Number(raw))?new ExactNumber(raw):Number(raw);
  }
  const result=value();white();if(pos!==text.length)throw new Error('Trailing JSON data');return result;
}
function stringifyJSON(value,replacer=null,space=0){
  const gap=typeof space==='number'?' '.repeat(Math.min(10,space)):String(space||'').slice(0,10),seen=new Set();
  function encode(v,depth=0){
    if(v instanceof ExactNumber)return v.raw;
    if(v===null||typeof v==='string'||typeof v==='boolean')return JSON.stringify(v);
    if(typeof v==='number'){if(!Number.isFinite(v))throw new Error('Nonfinite JSON number');return Object.is(v,-0)?'-0.0':String(v);}
    if(typeof v==='undefined')return undefined;
    if(typeof v!=='object')throw new Error('Unsupported JSON value');
    if(seen.has(v)||depth>400)throw new Error('Cyclic/deep JSON');seen.add(v);
    const array=Array.isArray(v),parts=[];
    for(const [key,item]of Object.entries(v)){
      const out=encode(item,depth+1);
      if(out!==undefined||array)parts.push((array?'':JSON.stringify(key)+(gap?': ':':'))+(out??'null'));
    }
    seen.delete(v);const a=array?'[':'{',b=array?']':'}';
    return !parts.length?a+b:gap?a+'\n'+gap.repeat(depth+1)+parts.join(',\n'+gap.repeat(depth+1))+'\n'+gap.repeat(depth)+b:a+parts.join(',')+b;
  }
  return encode(value);
}
