"""Application-owned orchestration; harnesses and tools are replaceable workers."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import threading
import time
from .settings import Settings

from .tool_schemas import CODE_SCHEMA, BROWSER_SCHEMA, IMAGE_SCHEMA, DELEGATE_SCHEMA
from .tool_declarations import native_tool


def plugin_host(settings, event=None):
    from .plugins import PluginHost, PluginSpec
    from .execution import CodeExecutor, BrowserSession
    from .harness import NativeHarness, CodexHarness
    host=PluginHost({'settings':settings},emit=event)
    for name,label,service in [('code','代码解释器',CodeExecutor),('browser','无头浏览器',BrowserSession),('image','图像生成',True),('native','原生工具调用',NativeHarness),('codex','Codex harness',CodexHarness)]:
        def apply(ctx,name=name,service=service):ctx.provide(name,service)
        host.add(PluginSpec(name,apply,requires=('settings',),label=label))
    host.refresh(settings.public()['plugins'])
    return host


class ToolEnvironment:
    def __init__(self, settings, config, checkpoint, event):
        self.settings,self.config,self.checkpoint,self.event=settings,config,checkpoint,event
        self.browser=None;self.plugins=plugin_host(settings,event)
        self.workspace=Path(config['workspace']).expanduser().resolve();self.workspace.mkdir(parents=True,exist_ok=True)

    def declarations(self):
        result=[]
        if self.config.get('codeEnabled') and self.plugins.get('code'):
            result.append(native_tool('code.execute',{'description':'Execute Python or shell in a private task working directory; returns stdout, stderr and exit status. This is trusted host execution, not a security sandbox.'},mode='flat',parameters=CODE_SCHEMA,category='code'))
        if self.config.get('browserEnabled') and self.plugins.get('browser'):
            result.append(native_tool('browser.control',{'description':'Operate an isolated headless browser; virtual cursor never moves the desktop pointer. Navigate first, inspect, then use observed selectors.'},mode='flat',parameters=BROWSER_SCHEMA,category='browser',image_output=True))
        if self.plugins.get('image'):
            result.append(native_tool('image.generate',{'description':'Generate an image through a configured image provider; saves an artifact to the task directory.'},mode='flat',parameters=IMAGE_SCHEMA))
        if self.config.get('depth',0)<2:
            result.append(native_tool('agent.delegate',{'description':'Delegate an explicit subtask to an agent using its own inherited or overridden role/provider. Use planner for difficult planning, executor for execution and reviewer for review.'},mode='flat',parameters=DELEGATE_SCHEMA))
        return result

    def call(self, name, value):
        self.checkpoint()
        if name=='code.execute' and self.config.get('codeEnabled'):
            executor=self.plugins.require('code')
            result=executor(self.workspace,timeout=min(60,self.config['timeout'])).run(value,checkpoint=self.checkpoint)
        elif name=='browser.control' and self.config.get('browserEnabled'):
            browser=self.plugins.require('browser')
            if self.browser is None:self.browser=browser(self.workspace)
            result=self.browser.call(value,checkpoint=self.checkpoint)
        elif name=='image.generate':result=self.image(value)
        elif name=='agent.delegate' and self.config.get('depth',0)<2:result=self.delegate(value)
        else:raise ValueError('Tool is unavailable: '+str(name))
        self.checkpoint();return self.settings.redact(result)

    def delegate(self,value):
        if not isinstance(value,dict)or not isinstance(value.get('prompt'),str)or not 1<=len(value['prompt'])<=20000:raise ValueError('Delegated prompt required')
        agent=value.get('agentId','delegate');role=value.get('role','executor')
        config=self.settings.resolve(agent,role);config.update(depth=self.config.get('depth',0)+1,workspace=str(self.workspace/'delegates'))
        config['codeEnabled']=config['codeEnabled']and self.config['codeEnabled'];config['browserEnabled']=config['browserEnabled']and self.config['browserEnabled']
        self.event('agent.delegated',{'agentId':agent,'role':role,'model':config['model'],'summary':'子智能体开始处理独立任务'})
        child=ToolEnvironment(self.settings,config,self.checkpoint,self.event)
        try:return execute_harness(self.settings,child,config,value['prompt'],None,self.checkpoint,self.event)
        finally:child.close()

    def image(self,value):
        self.plugins.require('image')
        import base64,uuid
        if not isinstance(value,dict)or not isinstance(value.get('prompt'),str)or not 1<=len(value['prompt'])<=10000:raise ValueError('Image prompt required')
        pid=value.get('provider','image1k');public=self.settings.public()
        if pid not in public['providers']:raise ValueError('Unknown image provider')
        client=self.settings.client(pid)
        try:
            from .harness import _request
            reply=_request(lambda:client.images.generate(model=public['providers'][pid]['model'],prompt=value['prompt'],size=value.get('size','1024x1024'),n=1,timeout=min(20,self.config['timeout'])),self.checkpoint)
            row=reply.data[0]
            if getattr(row,'b64_json',None):
                raw=base64.b64decode(row.b64_json,validate=True)
                if len(raw)>30_000_000:raise ValueError('Image exceeds artifact limit')
                p=self.workspace/('image-'+uuid.uuid4().hex+'.png');p.write_bytes(raw)
                return {'artifact':str(p),'bytes':len(raw)}
            return {'url':getattr(row,'url',None)}
        except Exception as e:
            self.checkpoint()
            raise RuntimeError('Image request failed'+(' (HTTP '+str(e.status_code)+')'if getattr(e,'status_code',None)else ''))from None
        finally:client.close()

    def close(self):
        if self.browser:self.browser.close()
        self.plugins.close()


def execute_harness(settings,environment,config,prompt,value,checkpoint,event):
    factory=environment.plugins.require(config['backend'])
    if config['backend']=='native':
        harness=factory(config,client_factory=lambda c:settings.client(resolved=c),report_failure=settings.report_failure,report_success=settings.report_success)
    else:harness=factory(config)
    declared=environment.declarations()if config['backend']=='native'else []
    return harness.run(prompt,value,tools=declared,call_tool=environment.call,checkpoint=checkpoint,event=event)


class ControlPlane:
    def __init__(self,settings,state_dir):
        from .background import BackgroundManager
        self.settings=settings;self.state_dir=Path(state_dir).resolve();self.state_dir.mkdir(parents=True,exist_ok=True)
        self.plugins=plugin_host(settings)
        self.stopping=threading.Event();self.discovery_status={};self.discovery_lock=threading.Lock()
        self.manager=BackgroundManager(self.state_dir/'tasks.sqlite3',self._work,max_workers=settings.runtime()['maxConcurrent'])
        self.discovery_thread=threading.Thread(target=self._discover_loop,daemon=True,name='chaos-model-catalog');self.discovery_thread.start()

    def _work(self,task,agent_id,previous,checkpoint,event):
        from .harness import run_harness
        # Each iteration resolves the newest committed deployment configuration.
        metadata=task.get('metadata',{})if isinstance(task,dict)else{}
        prompt=task.get('prompt',task.get('task',''))if isinstance(task,dict)else task
        config=self.settings.resolve(agent_id,metadata.get('role','executor'))
        identity=metadata.get('workspaceId',agent_id)
        import hashlib
        config['workspace']=str(Path(config['workspace'])/hashlib.sha256(str(identity).encode()).hexdigest()[:16])
        emit=lambda kind,payload:event(kind,self.settings.redact(payload))
        emit('route.selected',{k:config[k]for k in ('provider','model','backend','configVersion')})
        deadline=time.monotonic()+config['timeout']
        def check():
            checkpoint()
            if time.monotonic()>deadline:raise TimeoutError('Iteration time budget exhausted')
        environment=ToolEnvironment(self.settings,config,check,emit)
        try:
            environment.plugins.require(config['backend'])
            if config['role']=='image':return environment.image({'prompt':prompt,'provider':config['provider']})
            return self.settings.redact(execute_harness(self.settings,environment,config,prompt,{'previous':previous},check,emit))
        finally:environment.close()

    def start(self,body):
        import uuid
        prompt=body.get('prompt');agent=body.get('agentId','background');role=body.get('role','executor')
        if not isinstance(prompt,str)or not 1<=len(prompt)<=20000:raise ValueError('prompt must contain 1–20000 characters')
        if not isinstance(agent,str)or not 1<=len(agent)<=128:raise ValueError('Invalid agent id')
        config=self.settings.resolve(agent,role)
        iterations=body.get('iterations',1)
        if type(iterations)is not int or not 1<=iterations<=100:raise ValueError('iterations must be 1–100')
        if iterations>1 and not config['continuousEnabled']:raise ValueError('Continuous iteration is disabled')
        interval=body.get('intervalSeconds',config['intervalSeconds'])
        if type(interval)not in (int,float)or not 1<=interval<=3600:raise ValueError('Invalid interval')
        # The worker receives explicit role metadata, not a keyword-based task classifier.
        task={'prompt':prompt,'metadata':{'role':role,'workspaceId':uuid.uuid4().hex}}
        self.manager.configure(config['maxConcurrent'])
        return self.manager.start(task,agent_id=agent,iterations=iterations,interval=interval,metadata={'role':role})

    def _discover_loop(self):
        # Discovery runs independently; no request handler waits for scheduled work.
        next_at=0
        while not self.stopping.wait(1):
            now=time.monotonic()
            if now<next_at:continue
            for name,p in self.settings.public()['providers'].items():
                if self.stopping.is_set():return
                if p.get('hasKey'):
                    try:self.settings.discover(name);self.discovery_status[name]={'ok':True,'at':time.time()}
                    except Exception as e:self.discovery_status[name]={'ok':False,'at':time.time(),'error':self.settings.redact(str(e))}
            next_at=time.monotonic()+self.settings.runtime()['discoveryInterval']

    def status(self):
        rt=self.settings.runtime();binary=shutil.which(rt['codexPath'])
        return {'backends':[{'id':'native','available':bool(importlib.util.find_spec('openai')),'description':'OpenAI SDK native structured tool calling'},
                            {'id':'codex','available':bool(binary),'description':'Standalone Codex CLI process; Responses-compatible provider required'}],
                'tools':[{'id':'code.execute','enabled':rt['codeEnabled'],'available':True,'isolation':'trusted-host-process'},
                         {'id':'browser.control','enabled':rt['browserEnabled'],'available':bool(importlib.util.find_spec('playwright')),'isolation':'headless-browser-context'},
                         {'id':'image.generate','enabled':True,'available':bool(importlib.util.find_spec('openai'))}],
                'plugins':self.plugins.refresh(self.settings.public()['plugins']),'discovery':copy.deepcopy(self.discovery_status),'configVersion':self.settings.public()['version']}

    def close(self):
        self.stopping.set();self.manager.close();self.plugins.close()
