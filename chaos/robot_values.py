"""Owned profile-specific robots on the original cold-effect scheduler."""
from dataclasses import dataclass,field,replace
import json,math,time,uuid


def fail(message,code='robot_error',value=None):
    from .language import LanguageError
    raise LanguageError(message,code=code,value=value)


def intersection(*values):
    result=None
    for value in values:
        if value is not None:result=frozenset(value) if result is None else result & frozenset(value)
    return result


def data(value):
    from .extensions import json_copy
    try:return json_copy(value,max_chars=16000)
    except (ValueError,TypeError,OverflowError,UnicodeError) as error:fail(str(error),'argument_error')


def encoded(value):return json.dumps(data(value),ensure_ascii=False,separators=(',',':'),allow_nan=False)
def identity(value):return type(value) is str and 1<=len(value)<=128
def finite(value):
    if type(value) not in (int,float):return False
    try:return math.isfinite(value)
    except OverflowError:return False
def integer(value,low,high):return type(value) is int and low<=value<=high


def validate_actuators(value):
    value=data(value)
    if type(value) is not dict or not 1<=len(value)<=16:fail('Robot profile requires 1..16 actuator channels','argument_error')
    for name,spec in value.items():
        if not identity(name) or type(spec) is not dict or set(spec)!={'minimum','maximum','unit'}:
            fail('Invalid Robot actuator descriptor','argument_error')
        if not finite(spec['minimum']) or not finite(spec['maximum']) or spec['minimum']>spec['maximum'] or type(spec['unit']) is not str or len(spec['unit'])>64:
            fail('Invalid Robot actuator bounds or unit','argument_error')
    return value


def validate_readings(value):
    value=data(value)
    if type(value) is not dict or not 1<=len(value)<=32:fail('Robot observation requires 1..32 packet readings','argument_error')
    for name,sample in value.items():
        if not identity(name) or type(sample) is not dict or set(sample)!={'value','unit'} or type(sample['unit']) is not str or len(sample['unit'])>64:
            fail('Invalid Robot packet reading','argument_error')
    return value


def validate_channels(value,actuators):
    value=data(value)
    if type(value) is not dict or set(value)!=set(actuators):fail('Robot command channels must exactly match its profile','argument_error')
    for name,number in value.items():
        descriptor=actuators[name]
        if not finite(number) or not descriptor['minimum']<=number<=descriptor['maximum']:
            fail('Robot channel is outside its finite profile bounds','argument_error')
    return value


@dataclass(eq=False)
class Lease:
    owner:object
    epoch:int
    metadata:dict
    busy:bool=False
    context_active:bool=False
    scheduler:object=None

    def __post_init__(self):
        if self.scheduler is None:self.scheduler=self.owner.suspension


@dataclass(frozen=True,eq=False)
class Robot:
    owner:object
    lease:Lease
    capabilities:object=None


@dataclass(frozen=True,eq=False)
class RobotObservation:
    owner:object
    lease:Lease
    document_json:str
    capabilities:object=None


@dataclass(eq=False)
class Admission:
    used:bool=False


@dataclass(frozen=True,eq=False)
class RobotCommand:
    owner:object
    observation:RobotObservation
    id:str
    channels_json:str
    ttl_ms:int
    capabilities:object=None
    admission:Admission=field(default_factory=Admission)


@dataclass(frozen=True,eq=False)
class RobotReceipt:
    owner:object
    epoch:int
    document_json:str
    scheduler:object=None

    def __post_init__(self):
        if self.scheduler is None:object.__setattr__(self,'scheduler',self.owner.suspension)


VALUES=(Robot,RobotObservation,RobotCommand,RobotReceipt)


def owned(value,evaluator,expected=None):
    if type(value) not in VALUES or expected is not None and type(value) is not expected:fail('Expected '+(expected.__name__ if expected else 'Robot value'),'argument_error')
    if value.owner is not evaluator:fail('Robot value belongs to another evaluator','ownership_error')
    lease=value.observation.lease if type(value) is RobotCommand else getattr(value,'lease',None)
    epoch=value.epoch if type(value) is RobotReceipt else lease.epoch
    scheduler=value.scheduler if type(value) is RobotReceipt else lease.scheduler
    if scheduler is not evaluator.suspension or epoch!=evaluator.suspension.epoch or not evaluator.suspension.active:fail('Robot value belongs to another execution epoch','ownership_error')
    if lease is not None and lease.owner is not evaluator:fail('Robot lease belongs to another evaluator','ownership_error')
    if type(value) is RobotCommand:owned(value.observation,evaluator,RobotObservation)
    return value


def validate_embedded(evaluator,value):
    """Check live robot leaves before a scalar/container result is exposed.

    Preserve the original value and its aliases, and do not charge ordinary
    scalar returns or invoke object protocols. Completion packets have their
    existing full graph validator.
    """
    from .tuple_values import TupleValue
    from .set_values import SetValue
    from .hash_maps import HashMap,children
    from .eql_specializers import EqlSpecializer
    pending=[(value,0)];seen=set()
    while pending:
        item,depth=pending.pop();kind=type(item)
        if kind in VALUES:owned(item,evaluator);continue
        if kind not in (list,dict,TupleValue,SetValue,HashMap,EqlSpecializer):continue
        if id(item) in seen:continue
        seen.add(id(item))
        if len(seen)>20000 or depth>100:
            from .language import ResourceLimit
            raise ResourceLimit('Robot result graph exceeds its bounded container limit')
        if kind is HashMap:items=children(item)
        elif kind is EqlSpecializer:items=(item.payload,)
        elif kind is dict:items=item.values()
        else:items=item
        pending.extend((child,depth+1) for child in items)
    return value


def project(evaluator,value,*sources):
    owned(value,evaluator)
    if type(value) is RobotReceipt:return value
    from .agent_tools import current_caps
    ceiling=intersection(current_caps(evaluator),value.capabilities,*(getattr(s,'capabilities',None) for s in sources))
    return value if ceiling==value.capabilities else replace(value,capabilities=ceiling)


def same_identity(left,right):
    if type(left) is not type(right) or type(left) not in VALUES or left.owner is not right.owner:return False
    if type(left) is Robot:return left.lease is right.lease
    if type(left) is RobotCommand:return left.admission is right.admission and left.id==right.id
    if type(left) is RobotObservation:return left.lease is right.lease and json.loads(left.document_json)['id']==json.loads(right.document_json)['id']
    return left.epoch==right.epoch and json.loads(left.document_json)['id']==json.loads(right.document_json)['id']


def info(value):
    if type(value) is Robot:return data(value.lease.metadata)
    if type(value) is RobotCommand:
        observation=json.loads(value.observation.document_json)
        return {'id':value.id,'robotId':observation['robotId'],'generation':observation['generation'],
                'observationId':observation['id'],'channels':json.loads(value.channels_json),'ttlMs':value.ttl_ms,'used':value.admission.used}
    return json.loads(value.document_json)


def describe(value):return {'$robotValue':type(value).__name__,'info':info(value)}


def member(evaluator,value,name):
    owned(value,evaluator);fields=info(value)
    if name not in fields:fail('Robot member does not exist: '+str(name),'attribute_error')
    return data(fields[name])


@dataclass(frozen=True)
class Request:
    owner:object
    operation:str
    robot:object=None
    command:object=None
    name:object=None
    timeout_ms:object=None
    capabilities:object=None


def request(evaluator,operation,robot=None,command=None,name=None,timeout_ms=None):
    from .agent_tools import current_caps
    if robot is not None:owned(robot,evaluator,Robot)
    if command is not None:
        owned(command,evaluator,RobotCommand)
        if command.observation.lease is not robot.lease:fail('Robot command belongs to another lease','ownership_error')
    return Request(evaluator,operation,robot,command,name,timeout_ms,intersection(current_caps(evaluator),getattr(robot,'capabilities',None),getattr(command,'capabilities',None)))


def validate_open(result,name):
    fields={'id','name','generation','profile','status','actuators','maxObservationAgeMs','latestObservationId'}
    if type(result) is not dict or set(result)!=fields or not identity(result['id']) or result['name']!=name or not integer(result['generation'],1,1000000000) or not identity(result['profile']) or result['status']!='open' or result['latestObservationId'] is not None or not integer(result['maxObservationAgeMs'],1,10000):fail('Invalid Robot host metadata')
    validate_actuators(result['actuators']);return data(result)


def validate_observation(result,lease):
    fields={'id','robotId','generation','sequence','receivedAtNs','readings'}
    if type(result) is not dict or set(result)!=fields or not identity(result['id']) or result['robotId']!=lease.metadata['id'] or result['generation']!=lease.metadata['generation'] or not integer(result['sequence'],1,2**63-1) or not integer(result['receivedAtNs'],0,2**63-1):fail('Invalid Robot observation')
    validate_readings(result['readings']);return data(result)


def validate_receipt(result,request):
    robot=request.robot;command=request.command
    fields={'schema','id','robotId','name','generation','operation','status','targetVerified','commandId','observationId'}
    if type(result) is not dict or set(result)!=fields or result['schema']!='chaos.robot-receipt/1' or not identity(result['id']) or result['robotId']!=robot.lease.metadata['id'] or result['name']!=robot.lease.metadata['name'] or result['generation']!=robot.lease.metadata['generation'] or result['operation']!=request.operation or result['status']!={'act':'sent','stop':'stopSent','close':'closed'}[request.operation] or result['targetVerified'] is not False or result['commandId']!=(command.id if command else None) or result['observationId']!=(info(command.observation)['id'] if command else None):fail('Invalid Robot receipt')
    return data(result)


def resolve(evaluator,value):
    from .agent_tools import current_caps,scope
    from .suspension import Effect
    def execute():
        if value.owner is not evaluator:fail('Robot request cannot cross evaluators','ownership_error')
        caps=intersection(current_caps(evaluator),value.capabilities)
        with scope(evaluator,caps):
            if caps is not None and 'robot.control' not in caps:fail('Robot effect requires robot.control','capability_error')
            if evaluator.robot_handler is None:fail('Robot host is unavailable','capability_error')
            task=evaluator.suspension.current.task if evaluator.suspension.current else None
            if task is not None and task.recording_scope is not None and evaluator.effect_handler is None:fail('Physical Robot effects cannot be Skill-recorded','mode_error')
            lease=None
            if value.robot is not None:
                owned(value.robot,evaluator,Robot);lease=value.robot.lease
                if lease.busy:fail('Robot already has an operation in flight','robot_busy')
                if value.operation!='close' and lease.metadata['status']!='open':fail('Robot lease is not open','robot_state_error')
                if value.command is not None:
                    command=owned(value.command,evaluator,RobotCommand);observation=info(command.observation)
                    if command.observation.lease is not lease:fail('Robot command belongs to another lease','ownership_error')
                    if command.admission.used:fail('Robot command has already been consumed','robot_command_used')
                    if observation['id']!=lease.metadata['latestObservationId']:fail('Robot observation is no longer current','robot_stale_observation')
                    age=time.monotonic_ns()-observation['receivedAtNs']
                    if age<0 or age>=min(command.ttl_ms,lease.metadata['maxObservationAgeMs'])*1000000:fail('Robot command observation expired','robot_expired')
                    command.admission.used=True
                    lease.metadata['latestObservationId']=None
                lease.busy=True
            try:
                evaluator.tick();payload={'name':value.name} if value.operation=='open' else {'id':lease.metadata['id']}
                if value.operation=='observe':payload['timeoutMs']=value.timeout_ms
                if value.command is not None:
                    payload.update({'commandId':value.command.id,'observationId':info(value.command.observation)['id'],'channels':json.loads(value.command.channels_json),'ttlMs':value.command.ttl_ms})
                result=yield Effect('robot',{'operation':value.operation,'payload':payload})
                if value.operation=='open':return Robot(evaluator,Lease(evaluator,evaluator.suspension.epoch,validate_open(result,value.name)),caps)
                if value.operation=='observe':
                    result=validate_observation(result,lease);lease.metadata['latestObservationId']=result['id']
                    return RobotObservation(evaluator,lease,encoded(result),caps)
                result=validate_receipt(result,value)
                if value.operation=='close':lease.metadata['status']='closed'
                return RobotReceipt(evaluator,evaluator.suspension.epoch,encoded(result))
            except BaseException:
                if lease is not None:lease.metadata['status']='uncertain'
                raise
            finally:
                if lease is not None:lease.busy=False
    task=evaluator.suspension.task(execute(),capture_roots=(value,));task.robot_operation=True
    task.capabilities=intersection(task.capabilities,value.capabilities);task.runner.capabilities=task.capabilities
    return task


def context_enter(evaluator,robot):
    owned(robot,evaluator,Robot)
    if robot.lease.context_active or robot.lease.busy or robot.lease.metadata['status']!='open':fail('Robot is unavailable for context acquisition','robot_busy')
    robot.lease.context_active=True;return robot


def context_exit(evaluator,robot):return resolve(evaluator,request(evaluator,'close',robot))


def hard_context_exit(evaluator,robot):
    owned(robot,evaluator,Robot)
    robot.lease.metadata['status']='uncertain'
    if evaluator.robot_cleanup_handler is None:return
    evaluator.robot_cleanup_handler(robot.lease.metadata['id'])
    robot.lease.metadata['status']='closed'



def builtins(evaluator):
    def robot_open(name):
        from .modes import identifier
        identifier(name,'Robot binding');return request(evaluator,'open',name=name)
    def robot_info(robot):return info(owned(robot,evaluator,Robot))
    def robot_observe(robot,timeout_ms=1000):
        if not integer(timeout_ms,1,10000):fail('Robot timeout_ms requires Int 1..10000','argument_error')
        return request(evaluator,'observe',robot,timeout_ms=timeout_ms)
    def robot_observation_info(observation):return info(owned(observation,evaluator,RobotObservation))
    def robot_command(observation,channels,ttl_ms=250):
        owned(observation,evaluator,RobotObservation)
        if not integer(ttl_ms,1,1000):fail('Robot ttl_ms requires Int 1..1000','argument_error')
        if info(observation)['id']!=observation.lease.metadata['latestObservationId'] or observation.lease.metadata['status']!='open':fail('Robot observation is no longer current','robot_stale_observation')
        channels=validate_channels(channels,observation.lease.metadata['actuators'])
        from .agent_tools import current_caps
        return RobotCommand(evaluator,observation,uuid.uuid4().hex,encoded(channels),ttl_ms,intersection(current_caps(evaluator),observation.capabilities))
    def robot_command_info(command):return info(owned(command,evaluator,RobotCommand))
    def robot_act(robot,command):return request(evaluator,'act',robot,command)
    def robot_stop(robot):return request(evaluator,'stop',robot)
    def robot_close(robot):return request(evaluator,'close',robot)
    def robot_receipt_info(receipt):return info(owned(receipt,evaluator,RobotReceipt))
    return {f.__name__:f for f in (robot_open,robot_info,robot_observe,robot_observation_info,robot_command,robot_command_info,robot_act,robot_stop,robot_close,robot_receipt_info)}
