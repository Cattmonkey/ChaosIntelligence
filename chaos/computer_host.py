"""Run-owned Computers share Context's tool path, never revive from evidence JSON."""
import copy
import hashlib
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from .execution import BrowserBroker
from .language import LanguageError
from .extensions import json_copy


class ComputerHost:
    def __init__(self,context):
        self.context=context;self.lock=threading.RLock();self.resources={};self.observations={};self.capturing=0;self.opening=0;self.closed=False

    @staticmethod
    def error(message):return LanguageError(message,code='computer_error')

    def _resource(self,key):
        with self.lock:
            value=self.resources.get(key)
            if self.closed or value is None:raise self.error('Computer is not owned by this run')
            return value

    def _authority(self,ctx,allowed,persistent=False):
        ctx.budget.tick()
        if 'browser.control' not in ctx.scoped_allowed(allowed):raise self.error('Current context does not grant browser.control')
        if persistent and 'browser.control' not in ctx.store.project(ctx.project['id'])['capabilities']:
            raise self.error('Project browser.control grant was revoked')
        from .control import plugin_host
        resolved=ctx.engine.settings.resolve('','executor')
        if not resolved['browserEnabled']:raise self.error('Browser capability is disabled')
        host=plugin_host(ctx.engine.settings)
        try:host.require('browser')
        finally:host.close()
        return resolved

    def handle(self,ctx,operation,payload,allowed):
        from .computer_schema import validate_action,validate_url
        fields={'attach':{'name','location'},'open':{'url','location'},'connect':{'key','location'},'observe':{'id','image','location'},'act':{'id','observationId','spec','location'},'close':{'id','location'},'release':{'id','location'},'reconcile':{'id','observationId','evidence','location'}}
        if type(payload)is not dict or operation not in fields or set(payload)!=fields[operation]:raise self.error('Invalid Computer operation envelope')
        location=json_copy(payload['location'],max_chars=4000)
        if type(location)is not dict:raise self.error('Computer location must be an object')
        if ctx.program_call_id is not None:location.update(programCallId=ctx.program_call_id,sourceKind='program')
        persistent=operation=='connect' or bool(self.resources.get(payload.get('id'),{}).get('persistent'))
        registered=operation=='attach' or bool(self.resources.get(payload.get('id'),{}).get('registered'))
        resolved=None if registered else self._authority(ctx,allowed,persistent)
        if registered:self._registered_checkpoint(ctx,allowed)()
        if operation=='attach':return self._attach(ctx,payload['name'],allowed,location)
        if operation=='connect':
            with self.lock:
                if self.closed:raise self.error('Computer resources are closed')
                if len(self.resources)+self.opening>=4:raise self.error('At most four Computers per run')
                resource=ctx.engine.computer_pool.acquire(ctx,payload['key'],resolved)
                self.resources[resource['id']]=resource
            try:
                ctx.store.computers.record(ctx.project,ctx.run_id,resource['id'],'lifecycle','completed',
                    {'action':'connect','sessionKey':resource['sessionKey'],'stableSessionId':resource['stableSessionId'],
                     'generation':resource['generation']},location)
                return self.info(resource)
            except BaseException:
                try:ctx.engine.computer_pool.release(resource,close=True)
                except BaseException:pass
                raise
        if operation=='open':
            validate_url(payload['url']) if payload['url'] is not None else None
            with self.lock:
                if self.closed:raise self.error('Computer resources are closed')
                if len(self.resources)+self.opening>=4:raise self.error('At most four Computers per run')
                key=uuid.uuid4().hex;resource={'id':key,'backend':'browser','status':'open','broker':BrowserBroker(Path(resolved['workspace'])/ctx.project['id'],timeout=min(15,resolved['timeout'])),'lock':threading.Lock(),'latest':None}
                self.resources[key]=resource
            try:
                ctx.store.computers.record(ctx.project,ctx.run_id,key,'lifecycle','completed',{'action':'open','backend':'browser'},location)
                if payload['url'] is not None:self._tool(ctx,resource,{'action':'navigate','url':payload['url']},allowed)
                return self.info(resource)
            except BaseException:
                resource['status']='closed'
                try:resource['broker'].close()
                except BaseException:pass
                raise
        key=payload['id'];resource=self._resource(key)
        if not resource['lock'].acquire(False):raise self.error('Computer has an in-flight operation')
        reserved=False
        try:
            if operation in ('close','release'):
                if resource['status']!='closed':
                    if resource.get('registered'):self._retire_registered(resource);resource['status']='closed';resource['latest']=None
                    elif resource.get('persistent'):ctx.engine.computer_pool.release(resource,close=operation=='close')
                    else:
                        resource['broker'].close();resource['status']='closed';resource['latest']=None
                    ctx.store.computers.record(ctx.project,ctx.run_id,key,'lifecycle','completed',{'action':operation},location)
                return self.info(resource)
            if resource['status']!='open':raise self.error('Computer is closed')
            if resource.get('persistent'):ctx.engine.computer_pool.validate(resource)
            if operation=='reconcile':
                if not resource.get('persistent'):raise self.error('Only persistent Computers can be reconciled')
                observation=self.observations.get(payload['observationId'])
                if observation is None or observation['computerId']!=key or resource['latest']!=observation['id']:
                    raise self.error('Reconciliation requires the latest observation of this Computer lease')
                evidence=payload['evidence']
                if type(evidence)is not str or not evidence.strip() or len(evidence)>1000:
                    raise self.error('Reconciliation evidence must be a nonempty String of at most 1000 characters')
                ctx.engine.computer_pool.reconcile(resource,observation['id'],ctx.engine.settings.redact(evidence))
                return self.info(resource)
            if operation=='act' and resource.get('requiresReconcile'):
                raise self.error('Computer action outcome is uncertain; observe and reconcile before acting')
            with self.lock:
                if len(self.observations)+self.capturing>=64:raise self.error('At most 64 retained observations per run')
                self.capturing+=1;reserved=True
            action_id=None
            if operation=='observe':
                if type(payload['image'])is not bool:raise self.error('Observation image must be Bool')
                command={'action':'observe','image':payload['image']}
            elif operation=='act':
                observation=self.observations.get(payload['observationId'])
                if observation is None or observation['computerId']!=key or resource['latest']!=observation['id']:raise self.error('Stale or foreign observation; observe again')
                spec=validate_action(payload['spec'],registered=resource.get('registered',False));command=copy.deepcopy(spec)
                if resource.get('registered') and spec['action']not in resource['actions']:raise self.error('Action is not supported by this registered binding')
                if 'ref' in spec:
                    if not any(row['ref']==spec['ref']for row in observation['targets']):raise self.error('Target ref is not in this observation')
                    command.update({k:observation[k]for k in ('sessionId','controlEpoch','snapshotId')})
                command['_observation']=[observation[k]for k in ('sessionId','controlEpoch','snapshotId')]
                command['_capture']=True;command['image']=True
                # Persist admission before any actual driver operation. An admitted
                # Action consumes mutation authority even if later capture fails.
                admission={'spec':spec,'observationId':observation['id']}
                if resource.get('persistent'):
                    admission.update({k:resource[k] for k in ('sessionKey','stableSessionId','generation')})
                row=ctx.store.computers.record(ctx.project,ctx.run_id,key,'action','started',ctx.engine.settings.redact(admission),location)
                action_id=row['id'];resource['latest']=None
            else:raise self.error('Unknown Computer operation')
            try:
                result=self._tool(ctx,resource,command,allowed)
                return self._capture(ctx,resource,result,location,action_id,payload.get('spec'),payload.get('observationId'))
            except BaseException as error:
                resource['latest']=None
                if action_id:
                    try:
                        if resource.get('persistent'):ctx.engine.computer_pool.uncertain(resource,action_id,ctx.engine._run_error(error))
                        else:ctx.store.computers.terminal(ctx.project['id'],action_id,'uncertain',{'error':ctx.engine._run_error(error)})
                    except BaseException:pass
                if not action_id:
                    try:ctx.store.computers.record(ctx.project,ctx.run_id,key,'observation','failed',{'error':ctx.engine._run_error(error)},location)
                    except BaseException:pass
                raise
        finally:
            if reserved:
                with self.lock:self.capturing-=1
            resource['lock'].release()

    def _registered_checkpoint(self,ctx,allowed):
        if ctx.recording_scope:
            ctx.mode_host().handle('recording.abort',{'scopeId':ctx.recording_scope,'reason':'registered_computer_effect'})
            raise self.error('Registered Computer input cannot be recorded as a replayable Skill')
        def checkpoint():
            ctx.budget.tick()
            if 'computer.control' not in ctx.scoped_allowed(allowed) or 'computer.control' not in ctx.store.project(ctx.project['id'])['capabilities']:
                raise LanguageError('Registered Computer requires computer.control',code='capability_error')
        return checkpoint

    def _attach(self,ctx,name,allowed,location):
        if type(name)is not str or name not in ctx.engine.computer_specs:raise self.error('Unknown registered Computer binding')
        spec=ctx.engine.computer_specs[name];key=spec.resource_id or 'computer:'+name;token=uuid.uuid4().hex
        with self.lock:
            if self.closed:raise self.error('Computer resources are closed')
            if len(self.resources)+self.opening>=4:raise self.error('At most four Computers per run')
            self.opening+=1
        try:
            with ctx.engine.device_lock:
                if key in ctx.engine.device_leases:raise LanguageError('Physical resource is already leased',code='computer_busy')
                ctx.engine.device_leases[key]=token
            adapter=None;resource=None
            try:
                context=replace(ctx.extension_context('computer'),_checkpoint=self._registered_checkpoint(ctx,allowed))
                context.checkpoint();adapter=spec.factory(context);context.checkpoint()
                if not callable(getattr(adapter,'call',None)) or not callable(getattr(adapter,'close',None)):raise self.error('Computer adapter requires call and close')
                resource={'id':token,'backend':'registered','status':'open','broker':adapter,'lock':threading.Lock(),'latest':None,
                          'registered':True,'binding':name,'profile':spec.profile,'resourceKey':key,'retired':False,'packet':None,'actions':list(spec.actions)}
                with self.lock:
                    if self.closed:raise self.error('Computer resources are closed')
                    self.resources[token]=resource
                ctx.store.computers.record(ctx.project,ctx.run_id,token,'lifecycle','completed',{'action':'attach','backend':'registered','binding':name,'profile':spec.profile},location)
                return self.info(resource)
            except BaseException as error:
                released=adapter is None and not getattr(error,'_chaos_computer_retirement_failed',False)
                if adapter is not None:
                    try:
                        adapter.close();released=True
                        if resource is not None:resource.update(status='closed',latest=None,retired=True)
                    except BaseException:pass
                if released:
                    with ctx.engine.device_lock:
                        if ctx.engine.device_leases.get(key)==token:ctx.engine.device_leases.pop(key,None)
                raise

        finally:
            with self.lock:self.opening-=1

    def _retire_registered(self,resource):
        if resource['retired']:return
        # Transport.close must fail if remote input may still be in flight.
        # Only confirmed retirement releases the shared physical lease.
        resource['broker'].close();resource.update(retired=True,status='closed',latest=None)
        with self.context.engine.device_lock:
            if self.context.engine.device_leases.get(resource['resourceKey'])==resource['id']:
                self.context.engine.device_leases.pop(resource['resourceKey'],None)

    def _tool(self,ctx,resource,value,allowed):
        if not resource.get('registered'):
            return ctx.tool('browser.control',value,allowed,'computer',_computer=resource)
        from .computer_adapters import validate_computer_packet
        checkpoint=self._registered_checkpoint(ctx,allowed);checkpoint()
        event={'computerId':resource['id'],'binding':resource['binding'],'operation':value['action']}
        ctx.event('computer.driver.started',event)
        try:
            result=resource['broker'].call(copy.deepcopy(value),checkpoint=checkpoint)
            checkpoint();result=validate_computer_packet(result,previous=resource['packet'])
            if value.get('image',True) and not result.get('_images'):raise self.error('Registered Computer did not provide requested image capture')
            resource['packet']=result
            ctx.event('computer.driver.completed',event)
            return result
        except BaseException as error:
            resource['latest']=None
            try:ctx.event('computer.driver.failed',dict(event,error=ctx.engine._run_error(error)))
            except BaseException:pass
            raise

    def _capture(self,ctx,resource,result,location,action_id,spec,observation_id):
        images=result.pop('_images',None);image_info=None
        if images:
            image_info={}
            for variant,item in images.items():
                import base64
                item['sha256']=hashlib.sha256(base64.b64decode(item['data'],validate=True)).hexdigest()
                image_info[variant]={k:v for k,v in item.items()if k!='data'}
        record_id=uuid.uuid4().hex
        data={k:result[k]for k in ('sessionId','controlEpoch','snapshotId','url','title','viewport','cursor','truncated')}
        data.update(id=record_id,runId=ctx.run_id,sourceVersion=ctx.project['version'],computerId=resource['id'],text=result['snapshot'],
                    targets=[{k:v for k,v in row.items()if k!='selector'}for row in result['targets']],image=image_info,
                    receipt={'id':action_id,'status':'completed','action':spec['action'],'observationId':observation_id}if action_id else None)
        if resource.get('registered'):data.update({k:resource[k]for k in ('backend','binding','profile','actions')})
        if resource.get('persistent'):
            data.update({k:resource[k] for k in ('sessionKey','stableSessionId','generation','requiresReconcile','continued')})
        data=ctx.engine.settings.redact(data)
        ctx.store.computers.complete(ctx.project,ctx.run_id,resource['id'],data,location,images,action_id)
        with self.lock:self.observations[record_id]=data;resource['latest']=record_id
        return copy.deepcopy(data)

    def observed_message(self,ctx,envelope):
        from .session_protocol import validate_message
        if type(envelope)is not dict or set(envelope)!={'recordId','input'}:raise self.error('Invalid owned observation input')
        with self.lock:
            observation=self.observations.get(envelope['recordId'])
            if observation is None or observation['runId']!=ctx.run_id:raise self.error('Observation is not owned by this run')
            if observation['image']is None:raise self.error('Observation has no captured image')
            image=ctx.store.computers.image(ctx.project['id'],observation['id'],'model')
        import json
        value={'input':json_copy(envelope['input'],max_chars=40000),'observation':copy.deepcopy(observation)}
        return validate_message({'role':'user','content':[{'type':'text','text':json.dumps(value,ensure_ascii=False,separators=(',',':'))},
          {'type':'image_url','image_url':{'url':'data:'+image['mimeType']+';base64,'+image['data']}}]})

    @staticmethod
    def info(resource):
        fields=('id','backend','status')
        if resource.get('registered'):fields+=('binding','profile','actions')
        if resource.get('persistent'):fields+=('sessionKey','stableSessionId','generation','requiresReconcile','continued')
        return {k:resource[k]for k in fields}

    def close(self,outcome='completed'):
        with self.lock:self.closed=True;resources=list(self.resources.values())
        error=None
        for resource in resources:
            try:
                with resource['lock']:
                    was_open=resource['status']!='closed'
                    persistent=resource.get('persistent')
                    if not was_open:
                        if persistent and outcome!='completed':self.context.engine.computer_pool.close_idle(resource)
                        continue
                    if resource.get('registered'):self._retire_registered(resource)
                    elif persistent:self.context.engine.computer_pool.release(resource,close=outcome!='completed')
                    else:
                        resource['broker'].close();resource['status']='closed';resource['latest']=None
                    action='release' if persistent and outcome=='completed' else 'close'
                    self.context.store.computers.record(self.context.project,self.context.run_id,resource['id'],'lifecycle','completed',{'action':action,'automatic':True},{})
            except BaseException as caught:
                if error is None:error=caught
        if error is not None:raise error
