'use strict';
// Embedded Chaos editing uses the same non-executing language service as the IDE.
(() => {
  const cm = window.ChaosCM, instances = new Set(), mounted = new WeakMap();
  let formatPreference=true;try{formatPreference=localStorage.getItem('chaos.editor.formatOnSave')!=='false';}catch{}
  const node = (tag, cls, text) => {const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;};
  function createLexicalLanguage(cm) {
    if (typeof cm?.StreamLanguage?.define !== 'function') return [];
    function stringToken(stream, state) {
      let escaped = false;
      while (!stream.eol()) {
        const character = stream.next();
        if (character === state.quote && !escaped) { state.quote = null; break; }
        escaped = character === '\\' && !escaped;
      }
      return 'string';
    }
    // A temporary lexical view while real analysis is pending. Identifiers
    // and contextual keywords deliberately have no guessed semantic role.
    return cm.StreamLanguage.define({
      startState: () => ({depth: 0, quote: null}),
      copyState: state => ({...state}),
      token(stream, state) {
        if (state.quote) return stringToken(stream, state);
        if (stream.eatSpace()) return null;
        if (stream.match('//') || stream.match('#')) { stream.skipToEnd(); return 'comment'; }
        const character = stream.peek();
        if (character === '"' || character === "'") { state.quote = stream.next(); return stringToken(stream, state); }
        if (stream.match(/^(?:\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/)) return 'number';
        if (stream.match(/^[\p{L}_][\p{L}\p{N}_]*/u)) return null;
        if (stream.match('..')) return 'operator';
        stream.next();
        if ('{[('.includes(character)) state.depth++;
        if ('}])'.includes(character)) state.depth = Math.max(0, state.depth - 1);
        return /[+\-*\/%=<>!&|@^~?]/.test(character) ? 'operator' : 'punctuation';
      },
      indent: (state, text, context) => Math.max(0, state.depth - (/^\s*[}\])]/.test(text) ? 1 : 0)) * context.unit,
      languageData: {commentTokens: {line: '//'}, closeBrackets: {brackets: ['(', '[', '{', '"', "'"]}, indentOnInput: /^\s*[}\])]$/}
    });
  }

  function lexicalHighlights(cm) {
    if (!cm?.tags || typeof cm.HighlightStyle?.define !== 'function' || typeof cm.syntaxHighlighting !== 'function') return [];
    return [cm.syntaxHighlighting(cm.HighlightStyle.define(
      ['string', 'number', 'comment', 'operator', 'punctuation'].map(role => ({
        tag: cm.tags[role], color: 'var(--syntax-' + role + ')'
      }))
    ))];
  }

  function formatChanges(before, after) {
    if(before===after)return [];
    const edits=[];let a=0,b=0;
    while(a<before.length||b<after.length){const from=a,start=b;while(a<before.length&&/\s/u.test(before[a]))a++;while(b<after.length&&/\s/u.test(after[b]))b++;if(before.slice(from,a)!==after.slice(start,b))edits.push({from,to:a,insert:after.slice(start,b)});if(a===before.length&&b===after.length)return edits;if(before[a]!==after[b])break;a++;b++;}
    let from=0,to=before.length,end=after.length;while(from<to&&from<end&&before[from]===after[from])from++;while(to>from&&end>from&&before[to-1]===after[end-1]){to--;end--;}return [{from,to,insert:after.slice(from,end)}];
  }
  function mount(textarea, options={}) {
    if(!cm||!textarea||mounted.has(textarea))return mounted.get(textarea)||null;
    const box=node('section','embedded-editor chaos-code'),toolbar=node('div','embedded-editor-toolbar'),host=node('div','embedded-editor-host'),status=node('p','embedded-editor-status'),signature=node('section','embedded-editor-signature');
    box.setAttribute('aria-label',options.label||'函数体编辑器');status.setAttribute('role','status');signature.setAttribute('aria-label','调用签名与当前参数');signature.hidden=true;
    const suggest=node('button','','智能提示'),format=node('button','','格式化'),help=node('button','','参数提示'),automatic=node('label','','保存时格式化'),check=node('input');suggest.type=format.type=help.type='button';suggest.title='显示当前上下文的代码补全 · Ctrl Space';format.title='格式化函数体 · Shift Alt F';check.type='checkbox';check.checked=formatPreference;check.onchange=()=>{formatPreference=check.checked;try{localStorage.setItem('chaos.editor.formatOnSave',String(formatPreference));}catch{}};automatic.prepend(check);toolbar.append(suggest,format,help,automatic);box.append(toolbar,host,signature,status);textarea.after(box);textarea.hidden=true;
    const appearance=window.ChaosCodeAppearance?.createButton();if(appearance)format.after(appearance);
    let destroyed=false,revision=0,timer=null,requestId=0,writing=false,formatTask=null,formattedText=null,changed=false,compositionEpoch=0;
    const requests=new Set();
    const identity=()=>String(options.identity?.()??'');
    const context=()=>options.context?.()||{file:'main.chaos',entry:'main.chaos'};
    const readOnly=()=>textarea.disabled||textarea.readOnly||Boolean(options.readOnly?.());
    const snapshot=()=>({revision,identity:identity(),context:JSON.stringify(context()),source:editor.state.doc.toString()});
    const current=t=>!destroyed&&textarea.isConnected&&mounted.get(textarea)===api&&t.revision===revision&&t.identity===identity()&&t.context===JSON.stringify(context())&&t.source===editor.state.doc.toString();
    async function request(path,body,ticket){const abort=new AbortController();requests.add(abort);try{const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:abort.signal});const data=await response.json();if(!current(ticket))return null;if(!response.ok)throw Error(data.error?.message||data.error||'语言服务请求失败');return data;}finally{requests.delete(abort);}}
    async function analyze(position){const ticket=snapshot();return request('/api/language/analyze',{...JSON.parse(ticket.context),source:ticket.source,position},ticket);}
    function showSignature(value,explicit=false){signature.replaceChildren();signature.hidden=!value&&!explicit;if(!value){if(explicit)signature.append(node('span','','当前位置没有可推断的调用签名。'));return;}signature.append(node('code','',value.label));const params=node('div','embedded-editor-parameters');for(const [i,p]of(value.parameters||[]).entries()){const chip=node('code',i===value.activeParameter?'active':'',p.label);if(i===value.activeParameter)chip.setAttribute('aria-current','true');params.append(chip);}signature.append(params);}
    const semantic=cm.StateEffect.define(),editable=cm.StateEffect.define();
    const semanticField=cm.StateField.define({create:()=>cm.Decoration.none,update(value,tr){if(tr.docChanged)value=cm.Decoration.none;for(const effect of tr.effects)if(effect.is(semantic)){const max=tr.state.doc.length;value=cm.Decoration.set(effect.value.filter(t=>Number.isInteger(t.from)&&Number.isInteger(t.to)&&t.from>=0&&t.to>t.from&&t.to<=max&&['keyword','string','number','comment','operator','punctuation','variable','function','parameter','type','property'].includes(t.type)).map(t=>cm.Decoration.mark({class:'chaos-token-'+t.type}).range(t.from,t.to)),true);}return value;},provide:f=>cm.EditorView.decorations.from(f)});
    const disabledField=cm.StateField.define({create:()=>readOnly(),update(value,tr){for(const effect of tr.effects)if(effect.is(editable))value=effect.value;return value;},provide:f=>[cm.EditorState.readOnly.from(f),cm.EditorView.editable.from(f,value=>!value)]});
    const boundary=cm.StateField.define({create:()=>false,update(value,tr){return tr.docChanged?tr.isUserEvent('input.format'):value;}});
    async function refresh(explicit=false){if(destroyed)return;clearTimeout(timer);const seq=++requestId,pos=editor.state.selection.main.head;try{const data=await analyze(pos);if(!data||seq!==requestId||pos!==editor.state.selection.main.head)return;editor.dispatch({effects:semantic.of(data.tokens||[])});const max=editor.state.doc.length;editor.dispatch(cm.setDiagnostics(editor.state,(data.diagnostics||[]).filter(d=>!d.file||d.file===context().file).map(d=>({from:Math.min(max,Math.max(0,d.from)),to:Math.min(max,Math.max(d.from,d.to)),severity:d.severity==='warning'?'warning':'error',message:d.message}))));showSignature(data.signatureHelp,explicit);status.textContent=(data.complete?'分析已更新':'编辑中')+(data.diagnostics?.length?' · '+data.diagnostics.map(d=>d.message).slice(0,3).join('；'):' · 未发现语法问题');}catch(error){if(!destroyed&&seq===requestId&&error.name!=='AbortError'){status.textContent='语言服务暂不可用 · '+error.message;showSignature(null);}}}
    function schedule(){clearTimeout(timer);requestId++;showSignature(null);status.textContent='正在分析函数体…';timer=setTimeout(()=>refresh(),240);}
    const complete=window.ChaosEditorAssist?.createSource({analyze,allowed:()=>!destroyed&&!readOnly()&&!editor.composing&&!editor.compositionStarted,currentContext:()=>identity()+'\n'+JSON.stringify(context())})||(()=>null);
    const language=createLexicalLanguage(cm);
    const editor=new cm.EditorView({parent:host,state:cm.EditorState.create({doc:textarea.value,extensions:[...(globalThis.ChaosCodeAppearance?.extension?.(cm)||[]),language,...lexicalHighlights(cm),semanticField,disabledField,boundary,cm.EditorView.lineWrapping,cm.lineNumbers(),cm.drawSelection(),cm.highlightSpecialChars(),cm.highlightActiveLine(),cm.bracketMatching(),cm.closeBrackets(),cm.indentOnInput(),cm.indentUnit.of('  '),cm.lintGutter(),cm.history({joinToEvent:(tr,adjacent)=>adjacent&&!tr.isUserEvent('input.format')&&!tr.startState.field(boundary)}),cm.autocompletion({override:[complete]}),cm.EditorView.domEventHandlers({compositionstart(event,view){compositionEpoch++;cm.closeCompletion(view);return false;}}),cm.hoverTooltip(async(view,pos)=>{try{const data=await analyze(pos);if(!data?.hover)return null;return {pos:data.hover.from,end:data.hover.to,above:true,create:()=>({dom:node('div','embedded-editor-hover',data.hover.contents)})};}catch{return null;}}),cm.EditorView.contentAttributes.of({'aria-label':options.label||'Chaos 函数体','spellcheck':'false'}),cm.keymap.of([{key:'Shift-Alt-f',run(){formatDraft().catch(report);return true;}},{key:'Ctrl-Shift-Space',run(){refresh(true);return true;}},{key:'Mod-Shift-Space',run(){refresh(true);return true;}},...cm.closeBracketsKeymap,...cm.defaultKeymap,...cm.historyKeymap,...cm.searchKeymap,...cm.completionKeymap,cm.indentWithTab]),cm.EditorView.updateListener.of(update=>{if(update.docChanged){revision++;changed=true;if(!writing){textarea.value=update.state.doc.toString();textarea.dispatchEvent(new Event('input',{bubbles:true}));}schedule();}else if(update.selectionSet)schedule();})]})});
    const releaseAppearance=globalThis.ChaosCodeAppearance?.bind?.(editor,cm)||(()=>{});
    function report(error){if(!destroyed&&error.name!=='AbortError')status.textContent=error.message;}
    async function formatDraft(){if(readOnly())return;if(editor.composing||editor.compositionStarted)throw Error("请先完成输入法组字，再格式化或保存。");if(formatTask)return formatTask;const formatEpoch=compositionEpoch,ticket=snapshot();const work=async()=>{format.disabled=true;status.textContent='正在格式化…';const ctx=JSON.parse(ticket.context);const result=await request('/api/language/format-source',{source:ticket.source,file:ctx.file,languageVersion:ctx.languageVersions?.[ctx.file]??2,lineWidth:72},ticket);if(!result||!current(ticket)||readOnly())throw Error('格式化期间函数或草稿已变化；保留当前内容，请重试。');if(formatEpoch!==compositionEpoch||editor.composing||editor.compositionStarted)throw Error('格式化期间发生输入法组字；已保留当前草稿，请重试。');const edits=formatChanges(ticket.source,result.source);if(edits.length){const changes=editor.state.changes(edits);editor.dispatch({changes,selection:editor.state.selection.map(changes),userEvent:'input.format'});}formattedText=result.source;return result.source;};formatTask=work();try{return await formatTask;}finally{formatTask=null;if(!destroyed)format.disabled=readOnly();}}
    function fromTextarea(){if(writing||destroyed)return;const value=textarea.value;if(value!==editor.state.doc.toString()){cm.closeCompletion(editor);writing=true;try{editor.dispatch({changes:{from:0,to:editor.state.doc.length,insert:value},userEvent:'input.external'});}finally{writing=false;}}}
    function syncDisabled(){if(destroyed)return;const disabled=readOnly();if(disabled)cm.closeCompletion(editor);if(editor.state.field(disabledField)!==disabled)editor.dispatch({effects:editable.of(disabled)});format.disabled=disabled||!!formatTask;suggest.disabled=disabled||!window.ChaosEditorAssist;help.disabled=check.disabled=disabled;}
    const observer=new MutationObserver(()=>{if(!textarea.isConnected)destroy();else syncDisabled();});observer.observe(textarea,{attributes:true,attributeFilter:['disabled','readonly']});observer.observe(textarea.parentElement,{childList:true});observer.observe(textarea.parentElement.parentElement,{childList:true});
    function destroy(){if(destroyed)return;destroyed=true;clearTimeout(timer);for(const abort of requests)abort.abort();observer.disconnect();textarea.removeEventListener('input',fromTextarea);releaseAppearance();editor.destroy();box.remove();textarea.hidden=false;instances.delete(api);mounted.delete(textarea);}
    const api={editor,format:formatDraft,destroy,syncDisabled,async formatBeforeSave(){if(!destroyed&&!readOnly()&&check.checked&&(changed||options.changed?.())&&textarea.value!==formattedText&&options.visible?.()!==false)await formatDraft();}};
    mounted.set(textarea,api);instances.add(api);textarea.addEventListener('input',fromTextarea);suggest.onclick=()=>{if(!readOnly()&&!destroyed&&!editor.composing&&!editor.compositionStarted){editor.focus();cm.startCompletion(editor);}};format.onclick=()=>formatDraft().catch(report);help.onclick=()=>refresh(true);syncDisabled();schedule();return api;
  }
  window.ChaosEditor=Object.freeze({mount,formatChanges,createLexicalLanguage,lexicalHighlights,get:textarea=>mounted.get(textarea),destroyWithin:parent=>{for(const textarea of parent.querySelectorAll('textarea'))mounted.get(textarea)?.destroy();},async formatBeforeSave(){for(const instance of [...instances])await instance.formatBeforeSave();}});
})();
