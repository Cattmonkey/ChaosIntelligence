/* Native OpenUI networks. Pure NetworkGraph data; RuntimeGraph reads only a host-bound run. */
'use strict';
(() => {
 const MAX_RESPONSE=2000000;
 const statuses=new Set(['idle','ready','queued','pending','running','waiting','paused','completed','failed','cancelled','stopped','interrupted','needs_reconciliation','unknown','not-observed','started','admitted','uncertain']);
 let origin=null,serial=0,connected=null,connectionProject=null;const subscribers=new Set(),listeners=new Set(),channels=new Map();
 const safeString=(value,max=200)=>typeof value==='string'?value.slice(0,max):'';
 const validOrigin=value=>value&&typeof value.projectId==='string'&&value.projectId.length>0&&value.projectId.length<=200&&typeof value.runId==='string'&&value.runId.length>0&&value.runId.length<=200&&Number.isSafeInteger(value.version)&&value.version>0&&value.partial!==true;
 const imageHash=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
 function imageExecution(value){return value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')==='entry,imageHash,kind,sourceAvailable'&&value.kind==='executable'&&imageHash(value.imageHash)&&typeof value.entry==='string'&&value.entry.length>0&&value.entry.length<=240&&!/[\u0000-\u001f]/.test(value.entry)&&value.sourceAvailable===false;}
 function snapshotIdentity(value,fixed,scope){
  if(scope==='run'&&value.runId!==fixed.runId||scope!=='run'&&value.runId!==null)return false;
  if(value.execution!==undefined)return scope==='run'&&imageExecution(value.execution)&&value.sourceVersion===null&&Number.isSafeInteger(value.authorityVersion)&&value.authorityVersion>0&&value.authorityVersion===fixed.version;
  return Number.isSafeInteger(value.sourceVersion)&&value.sourceVersion>0&&(scope!=='run'||value.sourceVersion===fixed.version);
 }
 const statusOf=value=>safeString(value,80)||'not-observed';
 const statusClass=value=>statuses.has(value)?value:'unknown';
 function configure(value){
  const next=validOrigin(value)?Object.freeze({projectId:value.projectId,runId:value.runId,version:value.version,partial:false}):null;
  if(JSON.stringify(next)===JSON.stringify(origin))return;
  origin=next;serial++;if(next&&connectionProject&&connectionProject!==next.projectId)connected=null;if(next&&connected!==null&&!connectionProject)connectionProject=next.projectId;for(const channel of channels.values())channel.close();channels.clear();for(const f of subscribers)f();
 }
 function notify(event){
  if(!event)return;
  const projectId=event.project_id??event.projectId;
  if(typeof event.connected==='boolean'){
   if(origin&&projectId!==undefined&&projectId!==origin.projectId)return;
   connected=event.connected;connectionProject=projectId??origin?.projectId??connectionProject;
   for(const f of listeners)f(event);return;
  }
  if(!origin||projectId!==undefined&&projectId!==origin.projectId)return;
  if(typeof event.type!=='string'||!(/^(node\.|run\.|task\.|group\.|process\.|session\.|computer\.|device\.|robot\.|guidance\.|program\.|bot\.|team\.|agent\.|tool\.|graph\.)/.test(event.type)))return;
  for(const f of listeners)f(event);
 }
 function normalize(nodes,edges,runtime=false){
  const MAX_NODES=runtime?400:200,MAX_EDGES=runtime?1200:400;
  const rawNodes=Array.isArray(nodes)?nodes:[],rawEdges=Array.isArray(edges)?edges:[],seen=new Set();
  const out=[];
  for(const value of rawNodes){
   if(out.length>=MAX_NODES)break;
   if(!value||typeof value!=='object'||typeof value.id!=='string'||!value.id||seen.has(value.id))continue;
   const id=safeString(value.id,200);if(id!==value.id)continue;seen.add(id);
   const number=v=>Number.isSafeInteger(v)&&v>=0?v:null;
   const proof=runtime&&value.provenance&&['structural','observed'].includes(value.provenance.kind)?{kind:value.provenance.kind,eventId:number(value.provenance.eventId),source:safeString(value.provenance.source,80)}:null;
   const loc=runtime&&value.location&&typeof value.location.file==='string'&&Number.isSafeInteger(value.location.line)&&value.location.line>0?{file:safeString(value.location.file,200),line:value.location.line,sourceVersion:number(value.location.sourceVersion),...(imageHash(value.location.imageHash)?{imageHash:value.location.imageHash}:{})}:null;
   out.push({provenance:proof,location:loc,...(runtime&&imageHash(value.imageHash)?{imageHash:value.imageHash}:{}),lastEventId:runtime?number(value.lastEventId):null,id,label:safeString(value.label,200)||id,kind:safeString(value.kind,60),group:safeString(value.group,80),status:statusOf(value.status),detail:safeString(value.detail,2000)});
  }
  const links=[],ids=new Set();
  for(const edge of rawEdges){
   if(links.length>=MAX_EDGES)break;
   if(!edge||typeof edge.id!=='string'||!edge.id||edge.id.length>200||ids.has(edge.id)||!seen.has(edge.source)||!seen.has(edge.target))continue;
   ids.add(edge.id);links.push({id:edge.id,source:edge.source,target:edge.target,status:statusOf(edge.status)});
  }
  return {nodes:out,edges:links,limited:out.length!==rawNodes.length||links.length!==rawEdges.length};
 }
 function positions(nodes,layout){
  const sorted=[...nodes].sort((a,b)=>a.id.localeCompare(b.id)),result=new Map(),count=sorted.length;
  sorted.forEach((node,index)=>{
   if(count===1){result.set(node.id,{x:500,y:290});return;}
   if(layout==='layered'){const columns=Math.max(1,Math.ceil(Math.sqrt(count*1.6))),rows=Math.ceil(count/columns);result.set(node.id,{x:75+(index%columns)*850/Math.max(1,columns-1),y:55+Math.floor(index/columns)*470/Math.max(1,rows-1)});return;}
   const ring=count<12?0:index%3,theta=2*Math.PI*(index/Math.max(1,count))+ring*.035;
   const rx=ring===0?400:ring===1?290:175,ry=ring===0?230:ring===1?166:101;
   result.set(node.id,{x:500+Math.cos(theta)*rx,y:290+Math.sin(theta)*ry});
  });return result;
 }
 function runtimeChannel(fixed,token,scope){
  const key=token+':'+scope;if(channels.has(key))return channels.get(key);
  let disposed=false,controller=null,inflight=false,again=false,revision=-1,last=null,timer=null;
  const channel={key,view:{token,scope,state:'loading',snapshot:null,message:'正在读取运行快照…'},watchers:new Set(),close(){disposed=true;listeners.delete(changed);clearTimeout(timer);controller?.abort();}};
  channels.set(key,channel);
  const current=()=>!disposed&&serial===token&&origin===fixed;
  const publish=value=>{if(!current())return;channel.view={...value,token,scope};for(const watcher of channel.watchers)watcher(channel.view);};
  async function refresh(){
   if(!current())return;if(inflight){again=true;return;}inflight=true;controller=new AbortController();timer=setTimeout(()=>controller.abort(),10000);
   const url='/api/v1/projects/'+encodeURIComponent(fixed.projectId)+'/runtime-view'+(scope==='run'?'?runId='+encodeURIComponent(fixed.runId):'');
   try{
    const response=await fetch(url,{method:'GET',credentials:'same-origin',mode:'same-origin',redirect:'error',cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
    if(!current())return;if(!response.ok)throw new Error('运行快照不可用');
    const text=await response.text();if(text.length>MAX_RESPONSE)throw new Error('运行快照超过显示上限');const value=JSON.parse(text);
    if(!current())return;
    if(!value||value.schema!=='chaos.runtime-view/1'||value.projectId!==fixed.projectId||!snapshotIdentity(value,fixed,scope)||!Number.isSafeInteger(value.eventCursor)||value.eventCursor<0||!Array.isArray(value.nodes)||!Array.isArray(value.edges))throw new Error('运行快照来源或格式不匹配');
    if(scope==='run'&&last&&last.execution?.imageHash!==value.execution?.imageHash)throw new Error('同一运行的执行身份已改变');
    if(value.eventCursor<revision)return;revision=value.eventCursor;last=value;
    publish({state:connected===true?'ready':'snapshot',snapshot:value,message:''});
   }catch(error){if(current())publish({state:'offline',snapshot:last,message:'连接中断；保留最后一次确认快照。'});}
   finally{clearTimeout(timer);inflight=false;if(again&&current()){again=false;refresh();}}
  }
  function changed(event){if(typeof event.connected==='boolean'){if(event.connected)refresh();else publish({state:'offline',snapshot:last,message:'实时通知已断开；保留最后一次确认快照。'});return;}if(scope==='run'&&event.run_id!==fixed.runId)return;refresh();}
  listeners.add(changed);refresh();return channel;
 }
 function install(R){
  const h=R.createElement;
  function NetworkGraph(p){
   const graph=R.useMemo(()=>normalize(p.nodes,p.edges,p._runtime===true),[p.nodes,p.edges,p._runtime]),ids=JSON.stringify(graph.nodes.map(n=>n.id).sort());
   const points=R.useMemo(()=>positions(graph.nodes,p.layout),[ids,p.layout]);
   const [selected,setSelected]=R.useState(null),[zoom,setZoom]=R.useState(1),[query,setQuery]=R.useState(''),[pan,setPan]=R.useState({x:0,y:0});const drag=R.useRef(null);
   const selectedNode=graph.nodes.find(n=>n.id===selected)||null;
   const neighbors=new Set(selected?[selected]:[]);
   if(selected)for(const edge of graph.edges){if(edge.source===selected)neighbors.add(edge.target);if(edge.target===selected)neighbors.add(edge.source);}
   R.useEffect(()=>{if(selected&&!graph.nodes.some(n=>n.id===selected))setSelected(null)},[ids,selected]);
   const activeCount=graph.nodes.filter(n=>['running','waiting','queued','pending','started','admitted'].includes(n.status)).length;
   const failureCount=graph.nodes.filter(n=>['failed','interrupted','needs_reconciliation','uncertain'].includes(n.status)).length;
   const completeCount=graph.nodes.filter(n=>n.status==='completed').length;
   const filtered=graph.nodes.filter(n=>(n.label+' '+n.id+' '+n.status).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
   const keySelect=(event,node,index)=>{
    if(event.key==='Enter'||event.key===' '){event.preventDefault();setSelected(node.id);}
    if(event.key==='Escape')setSelected(null);
    if(['ArrowRight','ArrowDown','ArrowLeft','ArrowUp'].includes(event.key)&&graph.nodes.length){event.preventDefault();const step=['ArrowRight','ArrowDown'].includes(event.key)?1:-1,next=graph.nodes[(index+step+graph.nodes.length)%graph.nodes.length];setSelected(next.id);const target=event.currentTarget?.ownerSVGElement?.querySelector('[data-network-node-index="'+((index+step+graph.nodes.length)%graph.nodes.length)+'"]');target?.focus();}
   };
   const edges=graph.edges.map(edge=>{
    const a=points.get(edge.source),b=points.get(edge.target);if(!a||!b)return null;
    const dx=b.x-a.x,dy=b.y-a.y,distance=Math.hypot(dx,dy)||1,bend=Math.min(45,distance*.13),cx=(a.x+b.x)/2-dy/distance*bend,cy=(a.y+b.y)/2+dx/distance*bend;
    return h('path',{key:edge.id,d:'M'+a.x+','+a.y+' Q'+cx+','+cy+' '+b.x+','+b.y,className:'ui-network-edge'+(selected&&(edge.source===selected||edge.target===selected)?' is-neighbor':'')+(selected&&!neighbors.has(edge.source)&&!neighbors.has(edge.target)?' is-muted':''),fill:'none','aria-hidden':true});
   });
   const nodes=graph.nodes.map((node,index)=>{
    const pos=points.get(node.id),chosen=node.id===selected,label=chosen||graph.nodes.length<=24;
    return h('g',{key:node.id,transform:'translate('+pos.x+' '+pos.y+')',className:'ui-network-node status-'+statusClass(node.status)+(chosen?' is-selected':'')+(selected&&!neighbors.has(node.id)?' is-muted':''),tabIndex:chosen||(!selected&&index===0)?0:-1,role:'button','aria-label':node.label+' · '+node.status,'aria-pressed':chosen,'data-network-node-index':index,onClick:()=>setSelected(node.id),onKeyDown:event=>keySelect(event,node,index)},h('title',{},node.label+'\n'+node.id+' · '+node.status),h('circle',{r:chosen?9:5+(node.kind==='agent'?1:0)}),label?h('text',{x:12,y:4},node.label):null);
   });
   const metric=(label,value,detail)=>h('section',{className:'ui-network-metric'},h('span',{},label),h('strong',{},String(value)),h('small',{},detail));
   return h('section',{className:'ui-network ui-network-theme-'+(['dark','light'].includes(p.theme)?p.theme:'auto'),'aria-label':safeString(p.title,200)||'网络状态'},
    h('header',{className:'ui-network-header'},h('h3',{},safeString(p.title,200)||'网络状态'),h('div',{className:'ui-network-controls'},typeof document!=='undefined'&&document.fullscreenEnabled?h('button',{type:'button','aria-label':'全屏网络视图',onClick:event=>{const target=event.currentTarget.closest('.ui-runtime-network')||event.currentTarget.closest('.ui-network');if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});else target?.requestFullscreen?.().catch(()=>{});}},'⛶'):null,h('button',{type:'button',onClick:()=>setZoom(z=>Math.max(.65,z-.15)),'aria-label':'缩小网络'},'−'),h('button',{type:'button',onClick:()=>{setZoom(1);setPan({x:0,y:0})},'aria-label':'重置网络视图'},Math.round(zoom*100)+'%'),h('button',{type:'button',onClick:()=>setZoom(z=>Math.min(2.5,z+.15)),'aria-label':'放大网络'},'+'))),
    h('div',{className:'ui-network-canvas'},h('svg',{viewBox:'0 0 1000 580',role:'group','aria-label':'交互网络图；拖动平移，方向键移动，回车选择，Escape取消',onPointerDown:event=>{if(event.button!==0||event.target.closest?.('[data-network-node-index]'))return;drag.current={x:event.clientX,y:event.clientY,pan};event.currentTarget.setPointerCapture?.(event.pointerId)},onPointerMove:event=>{if(!drag.current)return;const bounds=event.currentTarget.getBoundingClientRect();setPan({x:drag.current.pan.x+(event.clientX-drag.current.x)*1000/Math.max(1,bounds.width),y:drag.current.pan.y+(event.clientY-drag.current.y)*580/Math.max(1,bounds.height)})},onPointerUp:()=>{drag.current=null},onPointerCancel:()=>{drag.current=null}},h('g',{transform:'translate('+pan.x+' '+pan.y+') translate(500 290) scale('+zoom+') translate(-500 -290)'},...edges,...nodes))),
    graph.nodes.length?null:h('p',{className:'ui-network-empty'},'暂无网络节点'),
    h('div',{className:'ui-network-legend'},h('span',{className:'status-running'},'● 进行中'),h('span',{className:'status-completed'},'● 已完成'),h('span',{className:'status-failed'},'● 需关注'),h('span',{className:'status-unknown'},'○ 未知 / 空闲')),
    h('div',{className:'ui-network-details'},h('label',{},'查找节点',h('input',{value:query,onChange:event=>setQuery(event.target.value.slice(0,200)),placeholder:'名称、ID 或状态','aria-label':'查找网络节点'})),h('select',{value:selectedNode?.id||'','aria-label':'选择网络节点',onChange:event=>setSelected(event.target.value||null)},h('option',{value:''},'选择节点查看详情'),...filtered.map(node=>h('option',{key:node.id,value:node.id},node.label+' · '+node.status))),h('div',{className:'ui-network-selected','aria-live':'polite'},selectedNode?h(R.Fragment,{},h('strong',{},selectedNode.label),h('code',{},selectedNode.id),h('span',{},selectedNode.status+(selectedNode.kind?' · '+selectedNode.kind:'')),selectedNode.detail?h('p',{},selectedNode.detail):null,selectedNode.provenance?h('small',{},'来源 '+selectedNode.provenance.kind+(selectedNode.provenance.source?' · '+selectedNode.provenance.source:'')+(selectedNode.provenance.eventId!==null?' · 事件 '+selectedNode.provenance.eventId:'')+(selectedNode.lastEventId!==null?' · 最近事件 '+selectedNode.lastEventId:'')):null,selectedNode.location?h('small',{},selectedNode.location.file+':'+selectedNode.location.line+(selectedNode.location.imageHash?' · 镜像 '+selectedNode.location.imageHash:selectedNode.location.sourceVersion!==null?' · 源码 v'+selectedNode.location.sourceVersion:'')):null):h('span',{},'选择节点可查看状态与相邻连接'))),
    h('footer',{className:'ui-network-metrics'},metric('节点',graph.nodes.length,'已显示'),metric(p._runtime?'关系':'进行中',p._runtime?graph.edges.length:activeCount,p._runtime?'实际快照窗口':'排队 / 运行 / 等待'),metric('已完成',completeCount,'状态快照'),metric('需关注',failureCount,'失败 / 中断 / 不确定')),
    graph.limited||p._truncated?h('p',{className:'ui-network-notice',role:'status'},p._truncated?'当前显示有界运行窗口；不是全部历史。':'部分无效或超出显示上限的数据未展示。'):null);
  }
  function RuntimeGraph(p){
   const [binding,setBinding]=R.useState(()=>({origin,serial})),[view,setView]=R.useState({state:'loading',snapshot:null,message:''});
   R.useEffect(()=>{const changed=()=>setBinding({origin,serial});subscribers.add(changed);changed();return()=>subscribers.delete(changed)},[]);
   const scope=p.scope==='project'?'project':'run';
   R.useEffect(()=>{
    const fixed=binding.origin,token=binding.serial;
    if(!fixed){setView({state:'unbound',snapshot:null,message:'仅原始运行产物可绑定真实运行状态；本地候选和流式生成预览保持静态。'});return;}
    const channel=runtimeChannel(fixed,token,scope);channel.watchers.add(setView);setView(channel.view);
    return()=>{channel.watchers.delete(setView);if(!channel.watchers.size){channel.close();channels.delete(channel.key);}};
   },[binding.serial,scope]);
   const snapshot=binding.serial===serial&&view.token===binding.serial&&view.scope===scope?view.snapshot:null;
   return h('section',{className:'ui-runtime-network','data-runtime-state':view.state},h('p',{className:'ui-network-freshness',role:'status'},snapshot&&view.state!=='offline'?'运行状态快照 · '+(snapshot.execution?.kind==='executable'?'镜像 '+snapshot.execution.imageHash+' · 授权项目 v'+snapshot.authorityVersion:'v'+snapshot.sourceVersion)+' · 游标 '+snapshot.eventCursor+(typeof snapshot.snapshotAt==='number'&&Number.isFinite(snapshot.snapshotAt)?' · '+new Date(snapshot.snapshotAt*1000).toISOString():typeof snapshot.snapshotAt==='string'?' · '+snapshot.snapshotAt:'')+(Number.isSafeInteger(snapshot.metrics?.observedEvents)?' · 已观察事件 '+snapshot.metrics.observedEvents:'')+(Number.isSafeInteger(snapshot.metrics?.ignoredEvents)?' · 未投影事件 '+snapshot.metrics.ignoredEvents:'')+(view.state==='snapshot'?' · 实时通知未连接':''):view.message),snapshot?h(NetworkGraph,{title:p.title||'运行网络',layout:p.layout,theme:p.theme,nodes:snapshot.nodes,edges:snapshot.edges,_runtime:true,_truncated:snapshot.truncated===true}):h('div',{className:'ui-network-empty'},view.state==='loading'?'读取中…':'尚无可显示的真实运行快照'));
  }
  return {NetworkGraph,RuntimeGraph};
 }
 window.ChaosRuntimeNetwork={install,configure,notify};
})();
