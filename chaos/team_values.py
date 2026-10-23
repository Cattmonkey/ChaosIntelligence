"""Immutable role definitions and cold, capability-scoped Agent calls."""
import hashlib
import json
import re
from dataclasses import dataclass, field

from .language import LanguageError, ResourceLimit, Native, Parser, serializable
from .agent_values import validate_definition as agent_definition
from .agent_values import configuration_schema as agent_schema
from .modes import identifier


def _error(message):
    raise LanguageError(message, code='team_error')


def _data(value):
    from .extensions import json_copy
    try:
        serializable(value)
        return json_copy(value)
    except ResourceLimit:
        raise
    except (LanguageError, ValueError, TypeError, OverflowError, UnicodeError) as error:
        _error('Team 数据必须为有界 JSON: '+str(error))


def configuration_schema():
    role_id = {'type':'string', 'pattern':'^[A-Za-z_][A-Za-z0-9_.-]{0,127}$', 'minLength':1, 'maxLength':128}
    return {'type':'object', 'additionalProperties':False,
            'required':['kind','name','entry','roles'], 'properties':{
                'kind':{'const':'team'}, 'name':{'type':'string','minLength':1,'maxLength':200},
                'entry':dict(role_id), 'roles':{'type':'array','minItems':1,'maxItems':64,'items':{
                    'type':'object','additionalProperties':False,
                    'required':['id','parent','capabilities','agent'],'properties':{
                        'id':dict(role_id),'parent':{'anyOf':[dict(role_id),{'type':'null'}]},
                        'capabilities':{'type':'array','maxItems':128,'uniqueItems':True,
                                        'items':{'type':'string','minLength':1,'maxLength':128}},
                        'agent':{'type':'object','additionalProperties':False,
                                 'required':['kind','name','config'],'properties':{
                                     'kind':{'const':'agent'},
                                     'name':{'type':'string','minLength':1,'maxLength':128},
                                     'config':agent_schema()}}}}}}}


def validate_definition(definition):
    definition = _data(definition)
    if type(definition) is not dict or set(definition) != {'kind','name','entry','roles'} or definition['kind'] != 'team':
        _error('Team 定义需要 kind/name/entry/roles')
    if type(definition['name']) is not str or not 1 <= len(definition['name']) <= 200:
        _error('Team name 需要 1–200 字符')
    identifier(definition['entry'], 'entry')
    roles = definition['roles']
    if type(roles) is not list or not 1 <= len(roles) <= 64:
        _error('Team 需要 1–64 个角色')
    indexed = {}
    for role in roles:
        if type(role) is not dict or set(role) != {'id','parent','capabilities','agent'}:
            _error('Team 角色需要 id/parent/capabilities/agent')
        name = identifier(role['id'], 'role id')
        if name in indexed: _error('Team 角色 id 重复')
        if role['parent'] is not None: identifier(role['parent'], 'parent')
        capabilities = role['capabilities']
        if (type(capabilities) is not list or len(capabilities) > 128
                or any(type(item) is not str or not 1 <= len(item) <= 128 for item in capabilities)
                or len(set(capabilities)) != len(capabilities)):
            _error('角色 capabilities 需要最多 128 个唯一工具名称')
        role['agent'] = agent_definition(role['agent'])
        if not set(role['agent']['config'].get('tools', [])) <= set(capabilities):
            _error('Agent tools 超过角色 capabilities')
        indexed[name] = role
    if definition['entry'] not in indexed: _error('Team entry 角色不存在')
    for role in roles:
        parent = role['parent']
        if parent is not None:
            if parent not in indexed or parent == role['id']: _error('Team parent 无效')
            if not set(role['capabilities']) <= set(indexed[parent]['capabilities']):
                _error('子角色 capabilities 必须是父角色的子集')
        visited = set()
        current = role
        while current is not None:
            if current['id'] in visited: _error('Team parent 形成循环')
            visited.add(current['id'])
            parent = current['parent']
            if parent is not None and parent not in indexed: _error('Team parent 不存在')
            current = indexed[parent] if parent is not None else None
    return definition


def _canonical(definition):
    return json.dumps(validate_definition(definition), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def definition_hash(definition):
    return hashlib.sha256(_canonical(definition).encode('utf-8')).hexdigest()


def source_definition(definition, name='saved_team'):
    if (type(name) is not str or not 1 <= len(name) <= 128
            or re.fullmatch(r'[^\W\d]\w*', name, re.UNICODE) is None
            or name in Parser.reserved or name == 'team_from'):
        _error('Team 导出绑定名无效')
    source = 'let '+name+' = team_from('+_canonical(definition)+');\nexport {'+name+'};\n'
    Parser(source).program()
    return source


@dataclass(frozen=True, eq=False)
class Team:
    definition_json: str
    owner: object
    name: str = field(init=False)
    definition_hash: str = field(init=False)

    def __post_init__(self):
        # Describing repeated aliases must not repeatedly decode the full roles.
        object.__setattr__(self, 'name', json.loads(self.definition_json)['name'])
        object.__setattr__(self, 'definition_hash', hashlib.sha256(self.definition_json.encode('utf-8')).hexdigest())

    @property
    def definition(self): return json.loads(self.definition_json)


def owned(value, evaluator):
    if type(value) is not Team: _error('需要受控 Team 值')
    if value.owner is not evaluator:
        raise LanguageError('Team 不能跨执行上下文使用；请显式传递定义', code='ownership_error')
    return value


def describe(value):
    return {'$team':value.name, 'definitionHash':value.definition_hash}


def _role(value, role_id):
    identifier(role_id, 'role')
    definition = value.definition
    roles = {role['id']:role for role in definition['roles']}
    if role_id not in roles: _error('Team 角色不存在: '+role_id)
    path = []; current = roles[role_id]
    while current is not None:
        path.append(current['id'])
        current = roles[current['parent']] if current['parent'] is not None else None
    scope = {'name':definition['name'], 'definitionHash':describe(value)['definitionHash'],
             'roleId':role_id, 'rolePath':list(reversed(path)),
             'capabilities':roles[role_id]['capabilities']}
    return roles[role_id], scope


@dataclass(frozen=True)
class TeamRequest:
    team: Team
    role: str
    input: object


def resolve(evaluator, request):
    # Resolve on the interpreter thread; only copied data enters the effect pool.
    owned(request.team, evaluator)
    _role(request.team, request.role)
    def call():
        from .suspension import Effect
        owned(request.team, evaluator)
        role, scope = _role(request.team, request.role)
        if evaluator.agent_handler is None:
            raise LanguageError('当前纯函数环境不允许智能体效果', code='capability_error')
        from .computer_values import ObservedInput, observed_payload
        observed=observed_payload(request.input,evaluator) if type(request.input) is ObservedInput else None
        if observed is not None and evaluator.computer_handler is None:
            raise LanguageError('Current environment does not allow visual Team input',code='capability_error')
        value = None if observed is not None else _data(request.input)
        evaluator.tick()
        task = evaluator.suspension.current.task
        provenance = {key:item for key,item in scope.items() if key != 'capabilities'}
        def emit(event):
            if evaluator.trace_handler:
                evaluator.trace_handler({'event':'team.role.'+event, **provenance,
                                         'taskId':task.id, 'location':dict(task.location)})
        emit('started')
        try:
            payload={'name':role['agent']['name'],'config':role['agent']['config'],'value':value,'_team':scope}
            if observed is not None:payload['_observed']=observed
            result = yield Effect('agent',payload)
            result = _data(result)
            evaluator.tick()
        except BaseException:
            emit('failed')
            raise
        emit('completed')
        return result
    return evaluator.suspension.task(call())


def builtins(evaluator):
    def team_from(definition): return Team(_canonical(definition), evaluator)
    def team_info(value): return owned(value, evaluator).definition
    def team_source(value, name='saved_team'): return source_definition(team_info(value), name)
    def team_role(value, role):
        from .team_bindings import BoundTeam,role_handle
        if type(value)is BoundTeam:return role_handle(evaluator,value,role)
        owned(value, evaluator); _role(value, role)
        def call(input): return TeamRequest(value, role, input)
        return Native('Team.'+role, call, evaluator, captures=(value,))
    def team_bindings(value):
        from .team_bindings import BoundTeam,owned as bound_owned
        definition = bound_owned(value,evaluator).team.definition if type(value)is BoundTeam else team_info(value)
        return {role['id']:team_role(value, role['id']) for role in definition['roles']}
    return {'team_from':team_from,'team_info':team_info,'team_source':team_source,
            'team_role':team_role,'team_bindings':team_bindings}
