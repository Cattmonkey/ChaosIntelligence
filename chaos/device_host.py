"""Run-owned adapters and engine-wide physical-resource admission."""
import threading,uuid
from .language import LanguageError,ResourceLimit
from .extensions import json_copy
from .device_values import validate_action

class DeviceHost:
    def __init__(self,context):self.context=context;self.resources={};self.lock=threading.RLock();self.closed=False
    def handle(self,ctx,operation,payload,allowed):
        shapes={'open':{'name'},'act':{'id','spec'},'close':{'id'}}
        if operation not in shapes or type(payload)is not dict or set(payload)!=shapes[operation]:raise LanguageError('Invalid Device envelope',code='device_error')
        ctx.budget.tick()
        granted=ctx.scoped_allowed(allowed)
        if 'device.control' not in granted or 'device.control' not in ctx.store.project(ctx.project['id'])['capabilities']:raise LanguageError('Device effect requires device.control',code='capability_error')
        if operation=='open':return self.open(ctx,payload['name'])
        with self.lock:
            resource=self.resources.get(payload['id'])
            if self.closed or resource is None:raise LanguageError('Device is not owned by this run',code='ownership_error')
        if not resource['lock'].acquire(False):raise LanguageError('Device already has an operation in flight',code='device_busy')
        try:
            if operation=='close':return self.close_one(ctx,resource)
            if resource['status']!='open':raise LanguageError('Device is not open',code='device_error')
            spec=validate_action(payload['spec']);request_id=uuid.uuid4().hex
            receipt={'schema':'chaos.device-receipt/1','id':request_id,'deviceId':resource['id'],'name':resource['name'],'generation':resource['generation'],'action':spec['action'],'status':'admitted','targetVerified':False}
            ctx.event('device.action.admitted',dict(receipt,spec=spec))
            try:
                ack=resource['adapter'].handle(request_id,spec,ctx.extension_context('device'))
                if type(ack)is not dict or set(ack)!={'protocol','id','status'} or ack!={'protocol':'chaos-hid/1','id':request_id,'status':'acknowledged'}:raise LanguageError('Invalid or uncorrelated device acknowledgment',code='device_error')
                ctx.budget.tick();receipt['status']='acknowledged';ctx.event('device.action.completed',receipt)
                return json_copy(receipt)
            except BaseException as error:
                resource['status']='uncertain';receipt['status']='uncertain'
                try:ctx.event('device.action.uncertain',dict(receipt,error=ctx.engine._run_error(error)))
                except BaseException:pass
                # No new action can use this adapter after an unknown outcome.
                try:self.retire(resource)
                except BaseException:pass
                if isinstance(error,LanguageError) and not isinstance(error,ResourceLimit):
                    raise LanguageError(error.message,code=error.code,value=receipt) from error
                raise
        finally:resource['lock'].release()
    def open(self,ctx,name):
        if type(name)is not str or name not in ctx.engine.device_specs:raise LanguageError('Unknown registered Device binding',code='device_error')
        spec=ctx.engine.device_specs[name];key=spec.resource_id or spec.name
        token=uuid.uuid4().hex
        with ctx.engine.device_lock:
            if key in ctx.engine.device_leases:raise LanguageError('Physical Device is already leased',code='device_busy')
            ctx.engine.device_leases[key]=token
        adapter=None
        try:
            adapter=spec.factory(ctx.extension_context('device'))
            if not callable(getattr(adapter,'handle',None)) or not callable(getattr(adapter,'close',None)):raise LanguageError('Device adapter requires handle and close',code='device_error')
            ctx.budget.tick()
            resource={'id':token,'name':name,'generation':1,'profile':'chaos-hid/1','status':'open','key':key,'adapter':adapter,'lock':threading.Lock(),'retired':False}
            with self.lock:
                if self.closed:raise LanguageError('Device resources are closing',code='device_error')
                self.resources[token]=resource
            ctx.event('device.opened',{k:resource[k] for k in ('id','name','generation','profile','status')})
            return {k:resource[k] for k in ('id','name','generation','profile','status')}
        except BaseException:
            released=adapter is None
            if adapter is not None:
                try:adapter.close();released=True
                except BaseException:pass
            if released:
                with ctx.engine.device_lock:
                    if ctx.engine.device_leases.get(key)==token:ctx.engine.device_leases.pop(key,None)
            raise
    def retire(self,resource):
        if resource['retired']:return
        # Keep exclusivity through actual adapter cleanup; even a failed close
        # must not silently make the endpoint available to another run.
        resource['adapter'].close();resource['retired']=True
        with self.context.engine.device_lock:
            if self.context.engine.device_leases.get(resource['key'])==resource['id']:self.context.engine.device_leases.pop(resource['key'],None)
    def close_one(self,ctx,resource):
        try:self.retire(resource)
        except BaseException:
            resource['status']='uncertain'
            raise
        resource['status']='closed'
        receipt={'schema':'chaos.device-receipt/1','id':uuid.uuid4().hex,'deviceId':resource['id'],'name':resource['name'],'generation':resource['generation'],'action':'close','status':'closed','targetVerified':False}
        ctx.event('device.closed',receipt);return receipt
    def close(self,outcome='completed'):
        with self.lock:self.closed=True;resources=list(self.resources.values())
        error=None
        for resource in resources:
            try:
                with resource['lock']:self.retire(resource);resource['status']='closed'
            except BaseException as caught:
                if error is None:error=caught
        if error is not None:raise error
