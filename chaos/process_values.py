"""Language-native persistent Processes: owned definitions, cold host operations."""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field

from .language import LanguageError, Parser
from .modes import identifier


def _error(message):
    raise LanguageError(message, code='process_error')


def data(value, *, max_chars=40000):
    from .extensions import json_copy
    try:
        copied=json_copy(value, max_chars=max_chars)
        if len(json.dumps(copied,ensure_ascii=False,separators=(',',':'),allow_nan=False))>max_chars:
            _error('Process JSON 文本超过 '+str(max_chars)+' 字符')
        return copied
    except (ValueError, TypeError, OverflowError, UnicodeError) as error:
        _error('Process 需要有界普通 JSON: '+str(error))


def binding_name(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or re.fullmatch(r'[^\W\d]\w*',value,re.UNICODE) is None or value in Parser.reserved):
        _error('Process export 需要有效语言标识符')
    return value


def configuration_schema():
    return {'type':'object','additionalProperties':False,
            'required':['kind','module','export','intervalSeconds','maxTurns','name'], 'properties':{
                'kind':{'const':'process'}, 'module':{'type':'string','minLength':1,'maxLength':240},
                'export':{'type':'string','minLength':1,'maxLength':128},
                'name':{'type':'string','minLength':1,'maxLength':200},
                'roles':{'type':'array','maxItems':16,'uniqueItems':True,'items':{'type':'string','pattern':'^[A-Za-z][A-Za-z0-9_.-]{0,63}$'}},
                'intervalSeconds':{'anyOf':[{'type':'null'},{'type':'number','minimum':1,'maximum':86400}]},
                'maxTurns':{'anyOf':[{'type':'null'},{'type':'integer','minimum':1,'maximum':1000000}]}}}


def validate_definition(definition):
    from .modules import path_name
    definition = data(definition)
    if type(definition) is not dict or set(definition) not in ({'kind','module','export','intervalSeconds','maxTurns','name'}, {'kind','module','export','intervalSeconds','maxTurns','name','roles'}) or definition['kind'] != 'process':
        _error('Process 定义需要 kind/module/export/intervalSeconds/maxTurns/name')
    module = definition['module']
    if (type(module) is not str or path_name(module) != module
            or not module.endswith('.chaos') or module.startswith('std/')):
        _error('Process module 必须是规范闭合包内 .chaos 路径')
    binding_name(definition['export'])
    if type(definition['name']) is not str or not 1 <= len(definition['name']) <= 200:
        _error('Process name 需要 1–200 字符')
    interval, maximum = definition['intervalSeconds'], definition['maxTurns']
    if interval is not None and (type(interval) not in (int,float) or not 1 <= interval <= 86400 or not math.isfinite(interval)):
        _error('intervalSeconds 需要 null 或 1–86400 秒')
    if maximum is not None and (type(maximum) is not int or not 1 <= maximum <= 1000000):
        _error('maxTurns 需要 null 或 1–1000000 整数')
    if 'roles' in definition:
        roles=definition['roles']
        if (type(roles)is not list or len(roles)>16 or any(type(role)is not str or
                re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',role)is None for role in roles)
                or len(set(roles))!=len(roles)):
            _error('Process roles 需要最多16个唯一的显式角色标识符')
    return definition


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def definition_hash(definition):
    return hashlib.sha256(_canonical(validate_definition(definition)).encode('utf-8')).hexdigest()


def source_definition(definition, name='saved_process'):
    binding_name(name)
    if name == 'process_from': _error('导出名称不能遮蔽 process_from')
    source='let '+name+' = process_from('+_canonical(validate_definition(definition))+');\nexport {'+name+'};\n'
    Parser(source).program()
    return source


def validate_binding(bundle, definition):
    """Validate public linkage without executing any module initializer."""
    from .modules import ProgramBundle
    definition=validate_definition(definition)
    if not isinstance(bundle,ProgramBundle): _error('Process 需要 ProgramBundle')
    module=definition['module']
    if module not in bundle.files: _error('Process module 不在程序包中')
    if definition['export'] not in bundle.exports.get(module,[]):
        _error('Process step 必须是模块明确导出的运行时绑定')
    return definition


def validate_step(value):
    # Validate the whole shape before copying; never serialize live values into
    # descriptions and subsequently mistake those descriptions for state.
    if type(value) is not dict or set(value) != {'state','output','done'} or type(value['done']) is not bool:
        _error('Process step 需要精确 {state,output,done:Bool}')
    state,output=data(value['state']),data(value['output'])
    return data({'state':state,'output':output,'done':value['done']},max_chars=100000)


@dataclass(frozen=True,eq=False)
class ProcessEvolution:
    """An immutable, evaluator-owned decision, never a serialized authority."""
    payload_json: str
    owner: object
    epoch: int
    claim: object
    capabilities: frozenset

    @property
    def payload(self): return json.loads(self.payload_json)


@dataclass(frozen=True,eq=False)
class _EvolutionCommit:
    """Host-prepared result of real tests, accepted only with its private claim."""
    decision: ProcessEvolution
    state_json: str
    receipt_json: str
    deadline: float

    @property
    def state(self): return json.loads(self.state_json)

    @property
    def receipt(self): return json.loads(self.receipt_json)


def evolution_owned(value,evaluator,*,finished=False):
    if type(value) is not ProcessEvolution: _error('需要受控 ProcessEvolution 值')
    if (value.owner is not evaluator or value.epoch != evaluator.suspension.epoch
            or (not finished and not evaluator.suspension.active)):
        raise LanguageError('ProcessEvolution 不能跨执行上下文或 epoch 使用',code='ownership_error')
    context=getattr(evaluator,'process_evolution_context',None)
    if context is None or value.claim is not context['claim']:
        raise LanguageError('ProcessEvolution 不属于当前 Process turn',code='ownership_error')
    return value


def evolution_info(value):
    from .program_values import canonical_hash, cases_hash
    payload=value.payload
    return {'base':payload['base'],'definitionHash':canonical_hash(payload['definition']),
            'casesHash':cases_hash(payload['cases']),'testCount':len(payload['cases']),
            **payload['step'],'reason':payload['reason']}


def describe_evolution(value):
    info=evolution_info(value)
    return {'$processEvolution':info}


def validate_result(value,evaluator,*,finished=False):
    return evolution_owned(value,evaluator,finished=finished) if type(value) is ProcessEvolution else validate_step(value)


@dataclass(frozen=True,eq=False)
class Process:
    definition_json: str
    owner: object
    name: str = field(init=False)
    definition_hash: str = field(init=False)

    def __post_init__(self):
        object.__setattr__(self,'name',json.loads(self.definition_json)['name'])
        object.__setattr__(self,'definition_hash',hashlib.sha256(self.definition_json.encode('utf-8')).hexdigest())

    @property
    def definition(self): return json.loads(self.definition_json)


def owned(value,evaluator):
    if type(value) is not Process: _error('需要受控 Process 值')
    if value.owner is not evaluator:
        raise LanguageError('Process 不能跨执行上下文使用；请传递定义',code='ownership_error')
    return value


def describe(value): return {'$process':value.name,'definitionHash':value.definition_hash}


@dataclass(frozen=True)
class ProcessRequest:
    operation: str
    payload: dict


def start_request(evaluator,key,value,state=None):
    return ProcessRequest('start',{'key':identifier(key,'key'),'definition':owned(value,evaluator).definition,'state':data(state)})


def resolve(evaluator,request):
    payload=data(request.payload,max_chars=160000)
    def call():
        from .suspension import Effect
        task=evaluator.suspension.current.task
        if task.recording_scope is not None:
            if evaluator.mode_handler is not None:
                yield Effect('mode',{'operation':'recording.abort','payload':{'scopeId':task.recording_scope,'reason':'process_effect_forbidden'}})
            raise LanguageError('录制 scope 不支持 Process 效果',code='mode_error')
        if evaluator.process_handler is None:
            raise LanguageError('当前环境不允许 Process 效果',code='capability_error')
        evaluator.tick()
        result=yield Effect('process',{'operation':request.operation,'payload':payload})
        result=data(result,max_chars=250000)
        evaluator.tick()
        return result
    return evaluator.suspension.task(call())


def _message_id(value):
    if value is None:return None
    from .process_store import message_identity
    try:return message_identity(value)
    except ValueError as error:_error(str(error))


def _revision(value):
    if type(value) is not int or value < 1: _error('Process revision 需要正整数')
    return value


def builtins(evaluator):
    def process_from(definition): return Process(_canonical(validate_definition(definition)),evaluator)
    def process_info(value): return owned(value,evaluator).definition
    def process_source(value,name='saved_process'): return source_definition(process_info(value),name)
    def process_start(key,value,state=None): return start_request(evaluator,key,value,state)
    def process_send(key,input,message_id=None):
        return ProcessRequest('send',{'key':identifier(key,'key'),'input':data(input),'messageId':_message_id(message_id)})
    def process_guide(key,input,message_id=None,*,target=None):
        payload={'key':identifier(key,'key'),'input':data(input),'messageId':_message_id(message_id)}
        if target is not None:
            from .guidance_targets import validate_target
            try:payload['target']=validate_target(target)
            except ValueError as error:_error(str(error))
        return ProcessRequest('guide',payload)
    def process_message(key,message_id):
        if type(message_id) is not str: _error('Process message_id 需要非空 String')
        from .process_store import message_identity
        try: message_id=message_identity(message_id)
        except ValueError as error: _error(str(error))
        return ProcessRequest('message',{'key':identifier(key,'key'),'messageId':message_id})
    def process_status(key): return ProcessRequest('status',{'key':identifier(key,'key')})
    def process_events(key,after=0,limit=100):
        from .store import page_bounds
        try:page_bounds(after,limit)
        except ValueError as error:_error(str(error))
        return ProcessRequest('events',{'key':identifier(key,'key'),'after':after,'limit':limit})
    def process_control(key,revision,action):
        if type(action) is not str or action not in ('pause','resume','stop'): _error('Process control 需要 pause/resume/stop')
        return ProcessRequest('control',{'key':identifier(key,'key'),'revision':_revision(revision),'action':action})
    def process_configure(key,revision,definition):
        return ProcessRequest('configure',{'key':identifier(key,'key'),'revision':_revision(revision),'definition':validate_definition(definition)})
    def process_reconcile(key,revision,state,evidence='',resume=False):
        if type(resume) is not bool: _error('Process resume 需要 Bool')
        if type(evidence) is not str or len(evidence)>1000: _error('Process evidence 最多 1000 字符')
        return ProcessRequest('reconcile',{'key':identifier(key,'key'),'revision':_revision(revision),
                                          'state':data(state),'evidence':evidence,'resume':resume})
    def process_step(state,output=None,done=False): return validate_step({'state':state,'output':output,'done':done})
    def process_evolve(base,candidate,cases,state,output=None,done=False,reason=''):
        from .program_values import owned as program_owned, validate_cases, canonical_hash
        context=getattr(evaluator,'process_evolution_context',None)
        if context is None or not evaluator.suspension.active:
            raise LanguageError('process_evolve 只能用于真实 Process turn',code='capability_error')
        runner=evaluator.suspension.current
        if runner is not None and getattr(runner.task,'recording_scope',None) is not None:
            raise LanguageError('录制 scope 不支持 Process evolution',code='mode_error')
        capabilities=frozenset(context['allowed']())
        if not {'process.manage','program.adopt'} <= capabilities:
            raise LanguageError('Process evolution 需要 process.manage 与 program.adopt',code='capability_error')
        base=program_owned(base,evaluator);candidate=program_owned(candidate,evaluator)
        if base.origin != context['origin'] or base.definition_hash != context['origin']['definitionHash']:
            _error('Process evolution base 必须是当前 turn 的可信程序快照')
        definition=candidate.definition
        if canonical_hash(definition)==base.definition_hash: _error('Process evolution 需要改变程序定义')
        cases=validate_cases(cases,definition)
        if type(reason) is not str or len(reason)>1000: _error('Process evolution reason 最多 1000 字符')
        step=validate_step({'state':state,'output':output,'done':done})
        evaluator.tick()
        return ProcessEvolution(_canonical({'base':base.origin,'definition':definition,'cases':cases,'step':step,'reason':reason}),
                                evaluator,evaluator.suspension.epoch,context['claim'],capabilities)
    def process_evolution_info(value): return evolution_info(evolution_owned(value,evaluator))
    return {'process_from':process_from,'process_info':process_info,'process_source':process_source,
            'process_start':process_start,'process_send':process_send,'process_guide':process_guide,'process_status':process_status,
            'process_events':process_events,'process_message':process_message,
            'process_control':process_control,'process_configure':process_configure,
            'process_reconcile':process_reconcile,'process_step':process_step,
            'process_evolve':process_evolve,'process_evolution_info':process_evolution_info}
