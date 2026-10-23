"""Explicit, bounded agent-mode definitions and cooperative cold operations."""
import hashlib
import json
import re
import uuid
from dataclasses import dataclass

from .language import LanguageError, ResourceLimit, ExecutionControl, check_type, serializable
from .mode_schema import PLAN_MAX_CONCURRENCY


def data(value):
    from .extensions import json_copy
    serializable(value)
    try: return json_copy(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise LanguageError('模式数据必须为有界 JSON: '+str(error), code='mode_error') from None


def identifier(value, label='name'):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}', value):
        raise LanguageError(label+' 需要 1–128 字符标识符', code='mode_error')
    return value


def parameter_paths(parameters, actions):
    if type(parameters) is not list or len(parameters)>128: raise LanguageError('技能参数最多 128 项', code='mode_error')
    previous=[]
    for row in parameters:
        if type(row) is not dict or set(row)!={'name','step','path'}: raise LanguageError('技能参数需要 name/step/path', code='mode_error')
        identifier(row['name'])
        step,path=row['step'],row['path']
        if type(step) is not int or not 0<=step<len(actions) or type(path) is not list or len(path)>64:
            raise LanguageError('技能参数路径无效', code='mode_error')
        current=actions[step]['input']
        for key in path:
            if type(current) is dict and type(key) is str and key in current: current=current[key]
            elif type(current) is list and type(key) is int and 0<=key<len(current): current=current[key]
            else: raise LanguageError('技能参数路径不存在', code='mode_error')
        for old_step,old_path in previous:
            if old_step==step and (path[:len(old_path)]==old_path or old_path[:len(path)]==path):
                raise LanguageError('技能参数路径不能重叠', code='mode_error')
        previous.append((step,path))
    return parameters


def validate_definition(definition):
    definition=data(definition)
    if type(definition) is not dict: raise LanguageError('模式定义需要 Record', code='mode_error')
    kind=definition.get('kind')
    if kind=='plan':
        if not {'kind','name','steps'}<=set(definition) or set(definition)-{'kind','name','steps','max_concurrency'}: raise LanguageError('Plan 字段无效', code='mode_error')
        concurrency=definition.get('max_concurrency',1)
        if type(concurrency) is not int or not 1<=concurrency<=PLAN_MAX_CONCURRENCY:
            raise LanguageError('Plan max_concurrency 需要 1–'+str(PLAN_MAX_CONCURRENCY), code='mode_error')
        # An explicit default has the same canonical definition and hash as
        # every Plan persisted before bounded parallel execution was added.
        if concurrency==1:definition.pop('max_concurrency',None)
        name=definition['name']
        if type(name) is not str or not 1<=len(name)<=200: raise LanguageError('Plan name 需要 1–200 字符', code='mode_error')
        steps=definition['steps']
        if type(steps) is not list or not 1<=len(steps)<=128: raise LanguageError('Plan 需要 1–128 步', code='mode_error')
        ids=set()
        for step in steps:
            if type(step) is not dict or not {'id','call'}<=set(step) or set(step)-{'id','call','needs'}: raise LanguageError('Plan 步骤字段无效', code='mode_error')
            identifier(step['id'],'id'); identifier(step['call'],'call')
            if step['id'] in ids: raise LanguageError('Plan 步骤 id 重复', code='mode_error')
            ids.add(step['id']); needs=step.setdefault('needs',[])
            if type(needs) is not list or len(needs)>128 or any(type(x) is not str for x in needs) or len(set(needs))!=len(needs): raise LanguageError('Plan needs 无效', code='mode_error')
        if any(set(step['needs'])-ids for step in steps): raise LanguageError('Plan 依赖不存在', code='mode_error')
        done=set()
        while len(done)<len(steps):
            ready=[step['id'] for step in steps if step['id'] not in done and set(step['needs'])<=done]
            if not ready: raise LanguageError('Plan 依赖形成循环', code='mode_error')
            done.update(ready)
    elif kind=='goal':
        if set(definition)!={'kind','objective','planner','verify','max_iterations'}: raise LanguageError('Goal 字段无效', code='mode_error')
        if type(definition['objective']) is not str or not 1<=len(definition['objective'])<=20000: raise LanguageError('Goal objective 需要 1–20000 字符', code='mode_error')
        identifier(definition['planner'],'planner'); identifier(definition['verify'],'verify')
        if type(definition['max_iterations']) is not int or not 1<=definition['max_iterations']<=100: raise LanguageError('Goal max_iterations 需要 1–100', code='mode_error')
    elif kind=='skill':
        if set(definition)!={'kind','actions','parameters'}: raise LanguageError('Skill 字段无效', code='mode_error')
        actions=definition['actions']
        if type(actions) is not list or not 1<=len(actions)<=128: raise LanguageError('Skill 需要 1–128 个真实工具动作', code='mode_error')
        for action in actions:
            if type(action) is not dict or set(action)!={'tool','input'}: raise LanguageError('Skill 动作需要 tool/input', code='mode_error')
            identifier(action['tool'],'tool')
        parameter_paths(definition['parameters'],actions)
    else: raise LanguageError('未知模式 kind', code='mode_error')
    return definition


def canonical_definition(definition):
    return json.dumps(validate_definition(definition), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def definition_hash(definition):
    return hashlib.sha256(canonical_definition(definition).encode('utf-8')).hexdigest()


def revision_number(value):
    if value is not None and (type(value) is not int or value <= 0):
        raise LanguageError('revision 需要正整数或 null', code='argument_error')
    return value


def origin_data(origin, definition):
    if origin is None: return None
    origin = data(origin)
    if (type(origin) is not dict or set(origin) != {'projectId','key','revision','definitionHash'}
            or type(origin['projectId']) is not str or not origin['projectId']):
        raise LanguageError('模式来源元数据无效', code='mode_error')
    identifier(origin['key'], 'key')
    if revision_number(origin['revision']) is None or origin['definitionHash'] != definition_hash(definition):
        raise LanguageError('模式来源修订或定义哈希不匹配', code='mode_error')
    return origin


@dataclass(frozen=True,eq=False)
class ModeValue:
    definition_json: str
    owner: object
    origin_json: object = None
    @property
    def definition(self): return json.loads(self.definition_json)
    @property
    def origin(self): return json.loads(self.origin_json) if self.origin_json is not None else None


class Plan(ModeValue): pass
class Goal(ModeValue): pass
class Skill(ModeValue): pass
VALUES=(Plan,Goal,Skill)


def make(definition, evaluator, origin=None):
    encoded=canonical_definition(definition)
    definition=json.loads(encoded)
    origin=origin_data(origin,definition)
    provenance=json.dumps(origin,ensure_ascii=False,sort_keys=True,separators=(',',':')) if origin is not None else None
    return {'plan':Plan,'goal':Goal,'skill':Skill}[definition['kind']](encoded,evaluator,provenance)


def owned(value, evaluator, expected=VALUES):
    if not isinstance(value,expected): raise LanguageError('需要受控模式值', code='mode_error')
    if value.owner is not evaluator: raise LanguageError('模式值不能跨执行上下文使用；请显式导出定义', code='ownership_error')
    return value


def describe(value):
    definition=value.definition
    return {'$mode':definition['kind'],'definitionHash':hashlib.sha256(value.definition_json.encode()).hexdigest()}


@dataclass(frozen=True)
class ModeRequest:
    operation: str
    arguments: tuple


def emit(evaluator, event, **payload):
    if evaluator.trace_handler: evaluator.trace_handler({'event':'mode.'+event,**data(payload)})


def bindings_for(evaluator, bindings, names):
    if type(bindings) is not dict: raise LanguageError('模式 bindings 需要 Record', code='mode_error')
    result={}
    from .objects import owned as object_owned
    for name in names:
        if name not in bindings: raise LanguageError('缺少模式 action binding: '+name, code='mode_error')
        value=bindings[name];check_type(value,'Function');object_owned(value,evaluator)
        if hasattr(value,'owner') and value.owner is not None and value.owner is not evaluator: raise LanguageError('模式 action 不能跨执行上下文使用', code='ownership_error')
        result[name]=value
    return result


def invoke(evaluator, action, value, group=None):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'invoke', action, value, group)


def host(evaluator, operation, payload):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'host', operation, payload)


def _plan_error_event(evaluator, event, error, metadata, **payload):
    # Trace observers must not downgrade the control failure being unwound.
    from .suspension import error_priority
    try:emit(evaluator,event,errorCode=getattr(error,'code',None),**metadata,**payload)
    except BaseException as trace_error:
        if error_priority(trace_error)>error_priority(error):raise


def _plan_step(evaluator, step, action, context, metadata, group=None):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'plan_step', step, action, context, metadata, group)


def _serial_plan(evaluator, definition, actions, input, metadata):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'serial_plan', definition, actions, input, metadata)


def _parallel_plan(evaluator, definition, actions, input, metadata):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'parallel_plan', definition, actions, input, metadata)


def run_plan(evaluator, value, bindings, input):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'run_plan', value, bindings, input)


def checkpoint(value, definition):
    value=data(value)
    fields={'iteration','state','results','history','phase','evidence'}
    if type(value) is not dict or set(value)!=fields: raise LanguageError('Goal checkpoint 无效',code='mode_error')
    if type(value['iteration']) is not int or not 0<=value['iteration']<=definition['max_iterations'] or value['phase'] not in ('ready','pending','done','exhausted') or type(value['results']) is not dict or type(value['history']) is not list or len(value['history'])>100:
        raise LanguageError('Goal checkpoint 状态无效',code='mode_error')
    return value


def pursue(evaluator, value, bindings, input, key):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'pursue', value, bindings, input, key)


def record(evaluator, action, input, parameters):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'record', action, input, parameters)


def substitute(definition, values):
    values=data(values)
    if type(values) is not dict:raise LanguageError('Skill values 需要 Record',code='mode_error')
    required={row['name'] for row in definition['parameters']}
    if set(values)!=required:raise LanguageError('Skill 参数名称不匹配',code='argument_error')
    actions=data(definition['actions'])
    for row in definition['parameters']:
        target=actions[row['step']]
        if not row['path']:target['input']=data(values[row['name']]);continue
        current=target['input']
        for key in row['path'][:-1]:current=current[key]
        current[row['path'][-1]]=data(values[row['name']])
    return data(actions)


def replay(evaluator, value, values):
    from .execution_modes import mode_protocol
    return mode_protocol(evaluator, 'replay', value, values)


def skill_source(value, name='recorded'):
    from .language import Parser
    identifier(name,'name')
    if name in Parser.reserved or name in ('tool','get','keys','sort') or '.' in name or '-' in name:raise LanguageError('导出函数名无效',code='mode_error')
    definition=value.definition
    encode=lambda item:json.dumps(item,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    bindings={(row['step'],tuple(row['path'])):row['name'] for row in definition['parameters']}
    def expression(item, step, path=()):
        if (step,path) in bindings:return 'get(values,'+encode(bindings[(step,path)])+')'
        if type(item) is list:return '['+','.join(expression(v,step,path+(i,)) for i,v in enumerate(item))+']'
        if type(item) is dict:return '{'+','.join(encode(k)+':'+expression(v,step,path+(k,)) for k,v in item.items())+'}'
        return encode(item)
    calls=['tool('+encode(action['tool'])+','+expression(action['input'],index)+')' for index,action in enumerate(definition['actions'])]
    required=sorted({row['name'] for row in definition['parameters']})
    source='fn '+name+'(values:Record){if sort(keys(values)) != '+encode(required)+' {throw \"Skill 参数名称不匹配\"}else{null};['+','.join(calls)+']}\nexport {'+name+'};\n'
    # Parsing validates safe literal/name generation, never executes a skill.
    Parser(source).program()
    from .source_format import format_source
    return format_source(source)


def resolve(evaluator, request):
    op, args = request.operation, request.arguments
    if op == 'run_plan': return evaluator.suspension.task(run_plan(evaluator, *args), capture_roots=tuple(args))
    if op == 'pursue': return evaluator.suspension.task(pursue(evaluator, *args), capture_roots=tuple(args))
    if op == 'record_skill': return evaluator.suspension.task(record(evaluator, *args), capture_roots=tuple(args))
    if op == 'replay_skill': return evaluator.suspension.task(replay(evaluator, *args), capture_roots=tuple(args))
    if op in ('save_mode', 'load_mode'):
        from .execution_modes import mode_protocol
        return evaluator.suspension.task(mode_protocol(evaluator, op, *args))
    raise LanguageError('未知模式操作', code='mode_error')


def builtins(evaluator):
    def plan(steps,name='plan',max_concurrency=1):return make({'kind':'plan','name':name,'steps':steps,'max_concurrency':max_concurrency},evaluator)
    def goal(objective,planner,verify,max_iterations=8):return make({'kind':'goal','objective':objective,'planner':planner,'verify':verify,'max_iterations':max_iterations},evaluator)
    def info(value):return owned(value,evaluator).definition
    def source(value,name='recorded'):return skill_source(owned(value,evaluator,Skill),name)
    def from_definition(definition):return make(definition,evaluator)
    def metadata(value):
        value=owned(value,evaluator)
        return {'kind':value.definition['kind'],'definitionHash':definition_hash(value.definition),'origin':value.origin}
    def value_source(value,name='saved_mode'):
        return mode_source_definition(owned(value,evaluator).definition,name)
    return {'plan':plan,'goal':goal,'mode_info':info,'skill_source':source,
            'mode_from':from_definition,'mode_metadata':metadata,'mode_source':value_source,
            'run_plan':lambda value,bindings,input=None:ModeRequest('run_plan',(value,bindings,input)),
            'pursue':lambda value,bindings,input=None,key=None:ModeRequest('pursue',(value,bindings,input,key)),
            'record_skill':lambda action,input=None,parameters=[]:ModeRequest('record_skill',(action,input,parameters)),
            'replay_skill':lambda skill,values={}:ModeRequest('replay_skill',(skill,values)),
            'save_mode':lambda value,key,revision=None:ModeRequest('save_mode',(value,key,revision)),
            'load_mode':lambda key,revision=None:ModeRequest('load_mode',(key,revision))}


def skill_source_definition(definition, name='recorded'):
    value=make(definition,None)
    if not isinstance(value,Skill):raise LanguageError('需要 Skill 定义',code='mode_error')
    return skill_source(value,name)


def mode_source_definition(definition, name='saved_mode'):
    """Export data as an ordinary value module; importing it runs no mode."""
    from .language import Parser
    identifier(name,'name')
    if name in Parser.reserved or name=='mode_from' or '.' in name or '-' in name:
        raise LanguageError('导出模式名称无效',code='mode_error')
    literal=canonical_definition(definition)
    source='let '+name+' = mode_from('+literal+');\nexport {'+name+'};\n'
    Parser(source).program()
    from .source_format import format_source
    return format_source(source)
