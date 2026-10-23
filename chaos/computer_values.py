"""Owned computer resources and immutable observation-bound actions.

Only JSON identities cross effects. Browser objects and pixels belong to the host.
"""
import copy
import json
from dataclasses import dataclass, field, replace
from .language import LanguageError
from .session_protocol import data
from .computer_schema import validate_action,validate_url,error
from .modes import identifier


def _json(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


@dataclass(eq=False)
class ComputerLease:
    owner: object
    snapshot: dict
    epoch: int
    busy: bool=False
    observation_id: object=None
    context_active: bool=False
    required_capability: str='browser.control'


@dataclass(eq=False,init=False)
class Computer:
    owner: object
    lease: ComputerLease
    capabilities: object
    def __init__(self,owner,snapshot,epoch,busy=False,observation_id=None,context_active=False,*,capabilities=None,lease=None,required_capability='browser.control'):
        self.owner=owner
        self.lease=lease if lease is not None else ComputerLease(owner,snapshot,epoch,busy,observation_id,context_active,required_capability)
        self.capabilities=capabilities
    snapshot=property(lambda self:self.lease.snapshot,lambda self,value:setattr(self.lease,'snapshot',value))
    epoch=property(lambda self:self.lease.epoch)
    busy=property(lambda self:self.lease.busy,lambda self,value:setattr(self.lease,'busy',value))
    observation_id=property(lambda self:self.lease.observation_id,lambda self,value:setattr(self.lease,'observation_id',value))
    context_active=property(lambda self:self.lease.context_active,lambda self,value:setattr(self.lease,'context_active',value))


@dataclass(frozen=True,eq=False)
class Observation:
    owner: object
    document_json: str
    capabilities: object=None
    id: str=field(init=False)
    computer_id: str=field(init=False)
    has_image: bool=field(init=False)
    def __post_init__(self):
        document=json.loads(self.document_json)
        object.__setattr__(self,'id',document['id'])
        object.__setattr__(self,'computer_id',document['computerId'])
        object.__setattr__(self,'has_image',document['image'] is not None)
    @property
    def document(self):return json.loads(self.document_json)


@dataclass(eq=False)
class Admission:
    used: bool=False


@dataclass(eq=False,init=False)
class ComputerAction:
    owner: object
    observation: Observation
    spec_json: str
    admission: Admission
    capabilities: object
    action_name: str
    def __init__(self,owner,observation,spec_json,used=False,*,capabilities=None,admission=None):
        self.owner,self.observation,self.spec_json=owner,observation,spec_json
        self.admission=admission if admission is not None else Admission(used)
        self.capabilities=capabilities
        self.action_name=json.loads(spec_json)['action']
    used=property(lambda self:self.admission.used,lambda self,value:setattr(self.admission,'used',value))
    @property
    def spec(self):return json.loads(self.spec_json)


@dataclass(frozen=True,eq=False)
class ObservedInput:
    owner: object
    observation: Observation
    input_json: str
    capabilities: object=None
    @property
    def input(self):return json.loads(self.input_json)


VALUES=(Computer,Observation,ComputerAction,ObservedInput)


def owned(value,evaluator,expected=None):
    if type(value) not in VALUES or expected is not None and type(value) is not expected:error('Expected '+(expected.__name__ if expected else 'owned computer value'))
    if value.owner is not evaluator:raise LanguageError('Computer values cannot cross evaluator ownership',code='ownership_error')
    if type(value) is Computer and value.lease.owner is not evaluator:raise LanguageError('Computer lease belongs to another evaluator',code='ownership_error')
    return value


def intersect(*values):
    result=None
    for value in values:
        if value is not None:result=frozenset(value) if result is None else result & frozenset(value)
    return result


def ceiling(evaluator,*values):
    from .agent_tools import current_caps
    return intersect(current_caps(evaluator),*(getattr(value,'capabilities',None) for value in values))


def project(evaluator,value,*sources):
    """Narrow an alias without cloning its mutable lease/admission state."""
    owned(value,evaluator)
    inherited=(value.observation,) if type(value) in (ComputerAction,ObservedInput) else ()
    caps=ceiling(evaluator,value,*sources,*inherited)
    if caps==value.capabilities:return value
    if type(value) is Computer:
        return Computer(evaluator,value.snapshot,value.epoch,capabilities=caps,lease=value.lease)
    if type(value) is ComputerAction:
        return ComputerAction(evaluator,project(evaluator,value.observation,*sources,value),value.spec_json,
                              capabilities=caps,admission=value.admission)
    if type(value) is ObservedInput:
        return replace(value,observation=project(evaluator,value.observation,*sources,value),capabilities=caps)
    return replace(value,capabilities=caps)


def describe(value):
    if type(value) is Computer:return {'$computer':value.snapshot['id'],'backend':value.snapshot.get('backend','browser'),'status':value.snapshot['status']}
    if type(value) is Observation:return {'$observation':value.id,'computerId':value.computer_id}
    if type(value) is ComputerAction:return {'$computerAction':value.action_name,'observationId':value.observation.id,'used':value.used}
    if type(value) is ObservedInput:return {'$observedInput':value.observation.id}
    error('Unknown computer value')


def location(evaluator):
    result=copy.deepcopy(evaluator.current_span)
    current=evaluator.suspension.current
    result['taskId']=current.task.id if current and current.task else None
    return data(result,4000)


def observed_payload(value,evaluator):
    owned(value,evaluator,ObservedInput);owned(value.observation,evaluator,Observation)
    return {'recordId':value.observation.id,'input':value.input}


SESSION_FIELDS=('sessionKey','stableSessionId','generation','requiresReconcile','continued')


def _session_metadata(value,previous=None):
    if (any(key not in value for key in SESSION_FIELDS)
        or type(value['sessionKey']) is not str or type(value['stableSessionId']) is not str
        or not 1<=len(value['stableSessionId'])<=200
        or type(value['generation']) is not int or value['generation']<1
        or type(value['requiresReconcile']) is not bool or type(value['continued']) is not bool):
        error('Invalid persistent Computer host metadata')
    identifier(value['sessionKey'],'Computer session key')
    if previous is not None and any(value[key]!=previous[key] for key in SESSION_FIELDS[:3]):
        error('Computer host changed the leased session identity')
    return {key:value[key] for key in SESSION_FIELDS}


def _metadata(value,expected=None,previous=None,persistent=False):
    value=data(value,20000)
    if (type(value) is not dict or type(value.get('id')) is not str or not 1<=len(value['id'])<=200
        or value.get('backend') not in ('browser','registered') or value.get('status') not in ('open','closed') or (expected is not None and value['id']!=expected)):
        error('Invalid Computer host metadata')
    if previous is not None and value['backend']!=previous['backend']:error('Computer backend changed')
    if value['backend']=='registered':
        if any(type(value.get(k))is not str or not 1<=len(value[k])<=128 for k in ('binding','profile')):error('Invalid registered Computer metadata')
        if previous is not None and any(value[k]!=previous[k]for k in ('binding','profile')):error('Computer binding changed')
    if persistent or any(key in value for key in SESSION_FIELDS):_session_metadata(value,previous)
    return value


def _observation(value,evaluator,computer):
    value=data(value,200000)
    if type(value) is not dict:error('Invalid Observation host result')
    for key in ('id','runId','computerId','sessionId'):
        if type(value.get(key)) is not str or not 1<=len(value[key])<=200:error('Invalid Observation '+key)
    if value['computerId']!=computer.snapshot['id']:error('Observation belongs to another computer')
    if computer.lease.required_capability=='computer.control' and any(value.get(k)!=computer.snapshot[k]for k in ('backend','binding','profile')):error('Observation changed its registered binding')
    for key in ('sourceVersion','controlEpoch','snapshotId'):
        if type(value.get(key)) is not int or value[key]<1:error('Invalid Observation '+key)
    if type(value.get('targets')) is not list or len(value['targets'])>40:error('Invalid Observation targets')
    refs=set()
    for target in value['targets']:
        if type(target) is not dict or type(target.get('ref')) is not str or not 1<=len(target['ref'])<=128 or target['ref'] in refs:error('Invalid or duplicate target reference')
        refs.add(target['ref'])
    if 'image' not in value or value['image'] is not None and type(value['image']) is not dict:error('Invalid Observation image metadata')
    if 'artifactPath' in value or 'model_image' in value or 'modelImage' in value:error('Observation must not contain host paths or raw images')
    if value['image'] is not None:
        if set(value['image'])!={'preview','model'}:error('Invalid Observation image metadata')
        for image in value['image'].values():
            if type(image) is not dict or set(image)!={'mimeType','sha256','width','height','scale'}:error('Observation image must only contain metadata')
            if image['mimeType'] not in ('image/png','image/jpeg','image/webp') or type(image['sha256']) is not str or len(image['sha256'])!=64:error('Invalid Observation image metadata')
            if any(type(image[k]) is not int or image[k]<1 for k in ('width','height')):error('Invalid Observation image dimensions')
            if type(image['scale']) not in (int,float) or not 0<image['scale']<=1:error('Invalid Observation image scale')
    return Observation(evaluator,_json(value),ceiling(evaluator,computer))


@dataclass(frozen=True)
class ComputerRequest:
    operation: str
    payload: dict
    computer: object=None
    action: object=None
    capabilities: object=None
    observation: object=None


def request(evaluator,operation,payload,computer=None,action=None,observation=None):
    if computer is not None:owned(computer,evaluator,Computer)
    if action is not None:owned(action,evaluator,ComputerAction)
    if observation is not None:owned(observation,evaluator,Observation)
    return ComputerRequest(operation,payload,computer,action,
                           ceiling(evaluator,computer,action,observation,getattr(action,'observation',None)),observation)


def act_request(evaluator,computer,action):
    owned(computer,evaluator,Computer);owned(action,evaluator,ComputerAction)
    owned(action.observation,evaluator,Observation)
    document=action.observation.document
    if document['computerId']!=computer.snapshot['id']:error('ComputerAction belongs to another computer')
    return request(evaluator,'act',{'id':computer.snapshot['id'],'observationId':document['id'],'spec':action.spec,'location':location(evaluator)},computer,action,action.observation)


def resolve(evaluator,request):
    from .agent_tools import scope,current_caps
    payload=data(request.payload,120000)
    captured=ceiling(evaluator,request,request.computer,request.action,request.observation,getattr(request.action,'observation',None))
    def perform():
        from .suspension import Effect
        if evaluator.computer_handler is None:raise LanguageError('Current environment does not allow Computer effects',code='capability_error')
        computer=owned(request.computer,evaluator,Computer) if request.computer is not None else None
        if computer is not None:
            if computer.epoch!=evaluator.suspension.epoch:error('Computer resource belongs to an earlier execution epoch')
            if computer.busy:raise LanguageError('Computer is already executing an operation',code='computer_busy')
            if computer.snapshot['status']=='closed':
                if request.operation in ('close','release'):return copy.deepcopy(computer.snapshot)
                error('Computer is closed')
            if request.operation=='reconcile' and computer.observation_id!=payload['observationId']:
                error('Computer reconciliation observation is stale')
            if request.action is not None:
                action=owned(request.action,evaluator,ComputerAction)
                if action.used:error('ComputerAction has already been consumed')
                if computer.observation_id!=action.observation.id:error('ComputerAction observation is stale')
                action.used=True
            computer.busy=True
        try:
            evaluator.tick()
            result=yield Effect('computer',{'operation':request.operation,'payload':payload})
            if request.operation in ('open','connect','attach'):
                result=_metadata(result,persistent=request.operation=='connect')
                if request.operation=='connect' and (result['status']!='open' or result['sessionKey']!=payload['key']):
                    error('Computer connect did not return the requested open session')
                if request.operation=='attach' and (result['backend']!='registered' or result['binding']!=payload['name']):error('Computer attach returned a different binding')
                if request.operation!='attach' and result['backend']!='browser':error('Browser open returned a different backend')
                return Computer(evaluator,result,evaluator.suspension.epoch,capabilities=ceiling(evaluator,request),required_capability='computer.control' if request.operation=='attach' else 'browser.control')
            persistent='sessionKey' in computer.snapshot
            if request.operation in ('close','release','reconcile'):
                result=_metadata(result,computer.snapshot['id'],computer.snapshot,persistent)
                expected_status='open' if request.operation=='reconcile' else 'closed'
                if result['status']!=expected_status:error('Computer operation did not return '+expected_status+' metadata')
                if request.operation=='reconcile':
                    if result['requiresReconcile']:error('Computer reconciliation did not clear recovery requirement')
                    computer.snapshot=result
                    return copy.deepcopy(result)
                computer.snapshot=result;computer.observation_id=None
                return copy.deepcopy(result)
            observation=_observation(result,evaluator,computer)
            if persistent:
                computer.snapshot.update(_session_metadata(observation.document,computer.snapshot))
            computer.observation_id=observation.id
            return observation
        finally:
            if computer is not None:computer.busy=False
    def execute():
        caps=intersect(current_caps(evaluator),captured)
        with scope(evaluator,caps):
            required=request.computer.lease.required_capability if request.computer is not None else ('computer.control' if request.operation=='attach' else 'browser.control')
            if caps is not None and required not in caps:
                raise LanguageError('Computer effect requires '+required,code='capability_error')
            return (yield from perform())
    task=evaluator.suspension.task(execute(),capture_roots=(request,))
    task.capabilities=intersect(task.capabilities,captured);task.runner.capabilities=task.capabilities
    return task



def context_enter(evaluator, computer):
    """Acquire one lexical scope for an already opened, run-owned Computer."""
    owned(computer, evaluator, Computer)
    evaluator.tick()
    if not evaluator.suspension.active or computer.epoch != evaluator.suspension.epoch:
        error('Computer resource belongs to an earlier execution epoch')
    if computer.context_active:
        raise LanguageError('Computer already has an active context scope', code='computer_busy')
    if computer.busy:
        raise LanguageError('Computer is already executing an operation', code='computer_busy')
    if computer.snapshot['status'] != 'open':
        error('Computer is closed')
    computer.context_active = True
    return computer


def context_exit(evaluator, computer):
    """Release a persistent lease, or close an ephemeral browser normally."""
    owned(computer, evaluator, Computer)
    operation='release' if 'sessionKey' in computer.snapshot else 'close'
    return resolve(evaluator, request(evaluator,operation, {
        'id': computer.snapshot['id'], 'location': location(evaluator)}, computer))


def builtins(evaluator):
    def computer_attach(name):
        identifier(name,'Computer binding name')
        return request(evaluator,'attach',{'name':name,'location':location(evaluator)})
    def computer_open(url=None):
        if url is not None:validate_url(url)
        return request(evaluator,'open',{'url':url,'location':location(evaluator)})
    def computer_connect(key):
        identifier(key,'Computer session key')
        return request(evaluator,'connect',{'key':key,'location':location(evaluator)})
    def computer_info(value):return copy.deepcopy(owned(value,evaluator,Computer).snapshot)
    def computer_observe(value,image=True):
        owned(value,evaluator,Computer)
        if type(image) is not bool:error('computer_observe image requires Bool')
        return request(evaluator,'observe',{'id':value.snapshot['id'],'image':image,'location':location(evaluator)},value)
    def observation_info(value):return owned(value,evaluator,Observation).document
    def computer_action(observation,spec):
        owned(observation,evaluator,Observation);spec=validate_action(spec,registered=observation.document.get('backend')=='registered')
        supported=observation.document.get('actions')
        if supported is not None and spec['action']not in supported:error('Action is not supported by this observed binding')
        if 'ref' in spec and not any(t['ref']==spec['ref']for t in observation.document['targets']):error('Action ref is absent from this observation')
        return ComputerAction(evaluator,observation,_json(spec),capabilities=ceiling(evaluator,observation))
    def action_info(value):
        owned(value,evaluator,ComputerAction)
        return {'spec':value.spec,'observationId':value.observation.id,'computerId':value.observation.computer_id,'used':value.used}
    def computer_act(value,action):return act_request(evaluator,value,action)
    def computer_close(value):
        owned(value,evaluator,Computer)
        return request(evaluator,'close',{'id':value.snapshot['id'],'location':location(evaluator)},value)
    def computer_release(value):
        owned(value,evaluator,Computer)
        operation='release' if 'sessionKey' in value.snapshot else 'close'
        return request(evaluator,operation,{'id':value.snapshot['id'],'location':location(evaluator)},value)
    def computer_reconcile(value,observation,evidence):
        owned(value,evaluator,Computer);owned(observation,evaluator,Observation)
        if 'sessionKey' not in value.snapshot:error('Computer reconciliation requires a persistent connection')
        if observation.computer_id!=value.snapshot['id']:error('Reconciliation Observation belongs to another computer')
        if type(evidence) is not str or not evidence.strip() or len(evidence)>1000:
            error('Computer reconciliation evidence requires 1–1000 nonblank characters')
        return request(evaluator,'reconcile',{'id':value.snapshot['id'],'observationId':observation.id,'evidence':evidence,'location':location(evaluator)},value,observation=observation)
    def observation_input(observation,input=None):
        owned(observation,evaluator,Observation)
        if not observation.has_image:error('ObservedInput requires a captured image')
        return ObservedInput(evaluator,observation,_json(data(input,40000)),ceiling(evaluator,observation))
    return {fn.__name__:fn for fn in (computer_attach,computer_open,computer_connect,computer_info,computer_observe,observation_info,computer_action,action_info,computer_act,computer_close,computer_release,computer_reconcile,observation_input)}
