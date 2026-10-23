"""Owned physical-device leases: cold effects, explicit authority, no JSON revival."""
from dataclasses import dataclass,field,replace
import json


def fail(message,code='device_error',value=None):
    from .language import LanguageError
    raise LanguageError(message,code=code,value=value)

def intersect(*caps):
    result=None
    for value in caps:
        if value is not None:result=frozenset(value) if result is None else result & frozenset(value)
    return result

def current(e):
    from .agent_tools import current_caps
    return current_caps(e)

def encoded(value):
    from .extensions import json_copy
    return json.dumps(json_copy(value,max_chars=16000),ensure_ascii=False,separators=(',',':'),allow_nan=False)

@dataclass(eq=False)
class Lease:
    owner: object
    epoch: int
    metadata: dict
    busy: bool=False
    context_active: bool=False

@dataclass(frozen=True,eq=False)
class Device:
    owner: object
    lease: Lease
    capabilities: object=None

@dataclass(eq=False)
class Admission:
    used: bool=False

@dataclass(frozen=True,eq=False)
class DeviceAction:
    owner: object
    lease: Lease
    spec_json: str
    capabilities: object=None
    admission: Admission=field(default_factory=Admission)

@dataclass(frozen=True,eq=False)
class DeviceReceipt:
    owner: object
    epoch: int
    document_json: str

VALUES=(Device,DeviceAction,DeviceReceipt)

def owned(value,evaluator,expected=None):
    if type(value) not in VALUES or expected is not None and type(value) is not expected:fail('Expected '+(expected.__name__ if expected else 'Device value'),'argument_error')
    if value.owner is not evaluator:fail('Device value belongs to another evaluator','ownership_error')
    epoch=value.epoch if type(value) is DeviceReceipt else value.lease.epoch
    if epoch!=evaluator.suspension.epoch:fail('Device value belongs to an earlier execution epoch','ownership_error')
    if type(value) is not DeviceReceipt and value.lease.owner is not evaluator:fail('Device lease belongs to another evaluator','ownership_error')
    return value

def project(e,value,*sources):
    owned(value,e)
    if type(value) is DeviceReceipt:return value
    caps=intersect(current(e),value.capabilities,*(getattr(s,'capabilities',None) for s in sources))
    return value if caps==value.capabilities else replace(value,capabilities=caps)

def describe(value):
    if type(value) is Device:return {'$device':value.lease.metadata['id'],'name':value.lease.metadata['name'],'status':value.lease.metadata['status']}
    if type(value) is DeviceAction:return {'$deviceAction':json.loads(value.spec_json)['action'],'deviceId':value.lease.metadata['id'],'used':value.admission.used}
    return {'$deviceReceipt':json.loads(value.document_json)}

def validate_action(spec):
    from .extensions import json_copy
    from .hid_protocol import validate_action as raw_validate
    try:return raw_validate(json_copy(spec,max_chars=4000))
    except (ValueError,TypeError,OverflowError) as error:fail(str(error),'argument_error')

@dataclass(frozen=True)
class DeviceRequest:
    operation: str
    device: object=None
    action: object=None
    name: object=None
    capabilities: object=None

def request(e,operation,device=None,action=None,name=None):
    if device is not None:owned(device,e,Device)
    if action is not None:
        owned(action,e,DeviceAction)
        if action.lease is not device.lease:fail('Action belongs to a different device lease')
    return DeviceRequest(operation,device,action,name,intersect(current(e),getattr(device,'capabilities',None),getattr(action,'capabilities',None)))

def resolve(e,r):
    from .suspension import Effect
    from .agent_tools import scope
    def execute():
        caps=intersect(current(e),r.capabilities)
        with scope(e,caps):
            if caps is not None and 'device.control' not in caps:fail('Device effect requires device.control','capability_error')
            if e.device_handler is None:fail('Device host is unavailable','capability_error')
            lease=None
            if r.device is not None:
                owned(r.device,e,Device);lease=r.device.lease
                if lease.busy:fail('Device already has an operation in flight','device_busy')
                if r.operation!='close' and lease.metadata['status']!='open':fail('Device lease is not open')
                if r.action is not None:
                    owned(r.action,e,DeviceAction)
                    if r.action.lease is not lease:fail('Device action lease changed')
                    if r.action.admission.used:fail('DeviceAction has already been consumed')
                    r.action.admission.used=True
                lease.busy=True
            try:
                e.tick()
                payload={'name':r.name} if r.operation=='open' else {'id':lease.metadata['id']}
                if r.action is not None:payload['spec']=json.loads(r.action.spec_json)
                result=yield Effect('device',{'operation':r.operation,'payload':payload})
                if r.operation=='open':
                    if type(result) is not dict or set(result)!={'id','name','generation','status','profile'} or result['name']!=r.name or type(result['id']) is not str or not 1<=len(result['id'])<=128 or type(result['generation']) is not int or result['generation']<1 or result['status']!='open' or result['profile']!='chaos-hid/1':fail('Invalid Device host metadata')
                    return Device(e,Lease(e,e.suspension.epoch,json.loads(encoded(result))),caps)
                expected='closed' if r.operation=='close' else 'acknowledged'
                fields={'schema','id','deviceId','name','generation','action','status','targetVerified'}
                if (type(result) is not dict or set(result)!=fields or result.get('schema')!='chaos.device-receipt/1'
                    or type(result.get('id')) is not str or not 1<=len(result['id'])<=128
                    or result.get('deviceId')!=lease.metadata['id'] or result.get('name')!=lease.metadata['name']
                    or type(result.get('generation')) is not int or result['generation']!=lease.metadata['generation']
                    or result.get('action')!=('close' if r.operation=='close' else json.loads(r.action.spec_json)['action'])
                    or result.get('status')!=expected or result.get('targetVerified') is not False):fail('Invalid Device receipt')
                if r.operation=='close':lease.metadata['status']='closed'
                return DeviceReceipt(e,e.suspension.epoch,encoded(result))
            except BaseException:
                if lease is not None and r.operation in ('act','close'):lease.metadata['status']='uncertain'
                raise
            finally:
                if lease is not None:lease.busy=False
    task=e.suspension.task(execute(),capture_roots=(r,))
    task.device_operation=True
    task.capabilities=intersect(task.capabilities,r.capabilities);task.runner.capabilities=task.capabilities
    return task

def context_enter(e,device):
    owned(device,e,Device)
    if device.lease.context_active or device.lease.busy:fail('Device already has an active scope or operation','device_busy')
    if device.lease.metadata['status']!='open':fail('Device is not open')
    device.lease.context_active=True
    return device

def context_exit(e,device):return resolve(e,request(e,'close',device))

def builtins(e):
    def device_open(name):
        from .modes import identifier
        identifier(name,'Device binding')
        return request(e,'open',name=name)
    def device_info(device):return json.loads(encoded(owned(device,e,Device).lease.metadata))
    def device_action(device,spec):
        owned(device,e,Device)
        if device.lease.metadata['status']!='open':fail('Device is not open')
        return DeviceAction(e,device.lease,encoded(validate_action(spec)),intersect(current(e),device.capabilities))
    def device_action_info(action):
        owned(action,e,DeviceAction)
        return {'deviceId':action.lease.metadata['id'],'generation':action.lease.metadata['generation'],'spec':json.loads(action.spec_json),'used':action.admission.used}
    def device_act(device,action):return request(e,'act',device,action)
    def device_receipt_info(receipt):return json.loads(owned(receipt,e,DeviceReceipt).document_json)
    def device_close(device):return request(e,'close',device)
    return {f.__name__:f for f in (device_open,device_info,device_action,device_action_info,device_act,device_receipt_info,device_close)}
