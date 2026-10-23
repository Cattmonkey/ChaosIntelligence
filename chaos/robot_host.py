"""Run-owned robot admission; adapters never become language values."""
import threading,time,uuid
from dataclasses import replace
from .language import LanguageError,ResourceLimit
from .robot_values import data,finite,integer,identity,validate_actuators,validate_channels,validate_readings


def error(message,code='robot_error'):raise LanguageError(message,code=code)


class RobotHost:
    def __init__(self,context):self.context=context;self.resources={};self.lock=threading.RLock();self.closed=False

    def handle(self,ctx,operation,payload,allowed):
        shapes={'open':{'name'},'observe':{'id','timeoutMs'},'act':{'id','commandId','observationId','channels','ttlMs'},'stop':{'id'},'close':{'id'}}
        if operation not in shapes or type(payload) is not dict or set(payload)!=shapes[operation]:error('Invalid Robot envelope')
        ctx.budget.tick();granted=ctx.scoped_allowed(allowed)
        if 'robot.control' not in granted or 'robot.control' not in ctx.store.project(ctx.project['id'])['capabilities']:error('Robot effect requires robot.control','capability_error')
        if operation=='open':return self.open(ctx,payload['name'],allowed)
        with self.lock:
            resource=self.resources.get(payload['id'])
            if self.closed or resource is None:error('Robot is not owned by this run','ownership_error')
        if not resource['lock'].acquire(False):error('Robot operation is already in flight','robot_busy')
        try:
            if operation=='close':return self.close_one(ctx,resource)
            if resource['status']!='open':error('Robot resource is not open','robot_state_error')
            if operation=='observe':return self.observe(ctx,resource,payload['timeoutMs'],allowed)
            if operation=='act':return self.act(ctx,resource,payload,allowed)
            request_id=uuid.uuid4().hex
            result=resource['adapter'].handle('stop',{'requestId':request_id},self.extension_context(ctx,allowed))
            if type(result) is not dict or result!={'status':'stopSent'}:error('Invalid Robot stop outcome')
            ctx.budget.tick();receipt=self.receipt(resource,request_id,'stop','stopSent');ctx.event('robot.stopped',receipt);return receipt
        except BaseException as caught:
            resource['status']='uncertain'
            try:self.retire(resource)
            except BaseException:pass
            try:ctx.event('robot.uncertain',{'robotId':resource['id'],'operation':operation,'targetVerified':False,'error':ctx.engine._run_error(caught)})
            except BaseException:pass
            raise
        finally:resource['lock'].release()

    @staticmethod
    def extension_context(ctx,allowed):
        original=ctx.extension_context('robot')
        def checkpoint():
            original.checkpoint()
            if 'robot.control' not in ctx.scoped_allowed(allowed) or 'robot.control' not in ctx.store.project(ctx.project['id'])['capabilities']:
                error('Robot authority was revoked','capability_error')
        return replace(original,_checkpoint=checkpoint)

    def open(self,ctx,name,allowed):
        if type(name) is not str or name not in ctx.engine.robot_specs:error('Unknown registered Robot binding')
        spec=ctx.engine.robot_specs[name];key=spec.resource_id or spec.name;token=uuid.uuid4().hex
        with ctx.engine.device_lock:
            if key in ctx.engine.device_leases:error('Physical resource is already leased','robot_busy')
            ctx.engine.device_leases[key]=token
        adapter=None
        try:
            adapter=spec.factory(self.extension_context(ctx,allowed))
            if not callable(getattr(adapter,'handle',None)) or not callable(getattr(adapter,'close',None)):error('Robot adapter requires handle and close')
            ctx.budget.tick();actuators=validate_actuators(spec.describe()['actuators'])
            resource={'id':token,'name':name,'generation':1,'profile':spec.profile,'status':'open','actuators':actuators,'maxObservationAgeMs':spec.max_observation_age_ms,'latestObservationId':None,'key':key,'adapter':adapter,'lock':threading.Lock(),'retired':False,'latest':None,'admitted':set()}
            with self.lock:
                if self.closed:error('Robot resources are closing')
                self.resources[token]=resource
            metadata={k:resource[k] for k in ('id','name','generation','profile','status','actuators','maxObservationAgeMs','latestObservationId')};ctx.event('robot.opened',metadata);return data(metadata)
        except BaseException as caught:
            released=adapter is None and not getattr(caught,'_chaos_robot_retirement_failed',False)
            if adapter is not None:
                try:adapter.close();released=True
                except BaseException:pass
            if released:
                with ctx.engine.device_lock:
                    if ctx.engine.device_leases.get(key)==token:ctx.engine.device_leases.pop(key,None)
            raise

    def observe(self,ctx,resource,timeout_ms,allowed):
        if not integer(timeout_ms,1,10000):error('Invalid Robot observe timeout','argument_error')
        result=resource['adapter'].handle('observe',{'timeoutMs':timeout_ms},self.extension_context(ctx,allowed))
        if type(result) is not dict or set(result)!={'sequence','receivedAtNs','readings'} or not integer(result['sequence'],1,2**63-1) or not integer(result['receivedAtNs'],0,2**63-1):error('Invalid Robot packet')
        readings=validate_readings(result['readings']);ctx.budget.tick();now=time.monotonic_ns();latest=resource['latest']
        if result['receivedAtNs']>now or now-result['receivedAtNs']>=resource['maxObservationAgeMs']*1000000:error('Robot packet is expired','robot_expired')
        if latest is not None and (result['sequence']<=latest['sequence'] or result['receivedAtNs']<latest['receivedAtNs']):error('Robot packet sequence is stale','robot_stale_observation')
        observation={'id':uuid.uuid4().hex,'robotId':resource['id'],'generation':resource['generation'],'sequence':result['sequence'],'receivedAtNs':result['receivedAtNs'],'readings':readings}
        resource['latest']=data(observation);resource['latestObservationId']=observation['id'];ctx.event('robot.observed',observation);return data(observation)

    def act(self,ctx,resource,payload,allowed):
        if not identity(payload['commandId']) or not identity(payload['observationId']) or not integer(payload['ttlMs'],1,1000):error('Invalid Robot command','argument_error')
        channels=validate_channels(payload['channels'],resource['actuators']);latest=resource['latest']
        if latest is None or payload['observationId']!=resource['latestObservationId']:error('Robot observation is not current','robot_stale_observation')
        now=time.monotonic_ns();expires=min(latest['receivedAtNs']+payload['ttlMs']*1000000,int(ctx.budget.deadline*1000000000))
        if now-latest['receivedAtNs']>=resource['maxObservationAgeMs']*1000000 or now>=expires:error('Robot command expired','robot_expired')
        if payload['commandId'] in resource['admitted']:error('Robot command was already admitted','robot_command_used')
        if len(resource['admitted'])>=10000:raise ResourceLimit('Robot command admission budget exceeds 10000')
        ctx.budget.tick();resource['admitted'].add(payload['commandId']);request_id=payload['commandId']
        resource['latestObservationId']=None
        receipt=self.receipt(resource,request_id,'act','admitted',payload['commandId'],latest['id']);ctx.event('robot.command.admitted',dict(receipt,channels=channels))
        result=resource['adapter'].handle('act',{'requestId':request_id,'observationId':latest['id'],'sequence':latest['sequence'],'receivedAtNs':latest['receivedAtNs'],'expiresAtNs':expires,'channels':channels},self.extension_context(ctx,allowed))
        if type(result) is not dict or result!={'status':'sent'}:error('Invalid Robot act outcome')
        ctx.budget.tick();receipt['status']='sent';ctx.event('robot.command.sent',receipt);return data(receipt)

    @staticmethod
    def receipt(resource,request_id,operation,status,command_id=None,observation_id=None):
        return {'schema':'chaos.robot-receipt/1','id':request_id,'robotId':resource['id'],'name':resource['name'],'generation':resource['generation'],'operation':operation,'status':status,'targetVerified':False,'commandId':command_id,'observationId':observation_id}

    def retire(self,resource):
        if resource['retired']:return
        resource['adapter'].close();resource['retired']=True
        with self.context.engine.device_lock:
            if self.context.engine.device_leases.get(resource['key'])==resource['id']:self.context.engine.device_leases.pop(resource['key'],None)

    def force_retire(self,resource_id):
        if not identity(resource_id):error('Invalid Robot cleanup identity','ownership_error')
        with self.lock:resource=self.resources.get(resource_id)
        if resource is None:error('Robot cleanup resource is not owned by this run','ownership_error')
        with resource['lock']:
            resource['status']='uncertain'
            self.retire(resource)
            resource['status']='closed'

    def close_one(self,ctx,resource):
        self.retire(resource);resource['status']='closed';receipt=self.receipt(resource,uuid.uuid4().hex,'close','closed');ctx.event('robot.closed',receipt);return receipt

    def close(self,outcome='completed'):
        with self.lock:self.closed=True;resources=list(self.resources.values())
        failure=None
        for resource in resources:
            try:
                with resource['lock']:self.retire(resource);resource['status']='closed'
            except BaseException as error:
                if failure is None:failure=error
        if failure is not None:raise failure
