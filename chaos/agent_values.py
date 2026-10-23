"""Pure, explicit data roundtrips for the existing owned Agent callable."""
import copy
import hashlib
import json
import re

from .language import Agent, LanguageError, Parser, ResourceLimit, serializable


_CONFIG_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': [],
    'description': 'Public Agent configuration only. Construction is pure; permissions and routing resolve when called. Omitted fields retain runtime defaults.',
    'properties': {
        'provider': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'title': '提供方'},
        'prompt': {'type': 'string', 'maxLength': 100000, 'title': '提示词'},
        'tools': {'type': 'array', 'maxItems': 128, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 128}, 'title': '工具能力'},
        'maxSteps': {'type': 'integer', 'minimum': 1, 'maximum': 12, 'title': '最大步数'},
        'mode': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'title': '本地模式'},
        'policy': {'type': 'string', 'maxLength': 100000, 'title': '策略描述'},
        'level': {'type': 'string', 'enum': ['worker', 'coordinator', 'platform'], 'title': '角色层级'},
        'role': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'title': '路由角色'},
        'route': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'title': '路由标识'},
    },
}


def configuration_schema():
    """Fresh standard JSON Schema; no implicit defaults or host settings."""
    return copy.deepcopy(_CONFIG_SCHEMA)


def _error(message):
    raise LanguageError(message, code='agent_error')


def _identifier(value, *, binding=False):
    # This is the lexer's Unicode identifier rule, not Python's wider isidentifier.
    if (type(value) is not str or not 1 <= len(value) <= 128
            or re.fullmatch(r'[^\W\d]\w*', value, re.UNICODE) is None
            or value in Parser.reserved or (binding and value == 'agent_from')):
        _error('Agent '+('导出绑定名' if binding else 'name')+' 需要 1–128 字符的有效语言标识符')
    return value


def _field(value, schema, name):
    kind = schema['type']
    if kind == 'string':
        if type(value) is not str:
            _error('Agent config.'+name+' 需要 String')
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 128):
            _error('Agent config.'+name+' 文本长度无效')
        if 'enum' in schema and value not in schema['enum']:
            _error('Agent config.'+name+' 不在允许的选项中')
    elif kind == 'integer':
        if type(value) is not int or not schema['minimum'] <= value <= schema['maximum']:
            _error('Agent config.'+name+' 需要 1–12 的 Int')
    elif kind == 'array':
        if type(value) is not list or len(value) > schema['maxItems']:
            _error('Agent config.'+name+' 需要最多 128 项的 List')
        for item in value:
            _field(item, schema['items'], name+'[]')


def validate_definition(definition):
    """Validate and copy public data; never inspect Settings or execute a value."""
    from .extensions import json_copy
    try:
        serializable(definition)
        definition = json_copy(definition)
    except ResourceLimit:
        raise
    except (LanguageError, ValueError, TypeError, OverflowError, UnicodeError) as error:
        _error('Agent 定义必须为有界 JSON: '+str(error))
    if type(definition) is not dict or set(definition) != {'kind', 'name', 'config'}:
        _error('Agent 定义需要且仅允许 kind/name/config')
    if definition['kind'] != 'agent':
        _error('Agent definition.kind 必须是 agent')
    _identifier(definition['name'])
    config = definition['config']
    if type(config) is not dict or set(config) - set(_CONFIG_SCHEMA['properties']):
        _error('Agent config 包含非公开或未知字段')
    for name, value in config.items():
        _field(value, _CONFIG_SCHEMA['properties'][name], name)
    return definition


def _canonical(definition):
    return json.dumps(validate_definition(definition), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def definition_hash(definition):
    """Hash public definition data, not resolved Settings/model dependencies."""
    return hashlib.sha256(_canonical(definition).encode('utf-8')).hexdigest()


def source_definition(definition, name='saved_agent'):
    """An ordinary module that constructs a value, without invoking its provider."""
    _identifier(name, binding=True)
    source = 'let '+name+' = agent_from('+_canonical(definition)+');\nexport {'+name+'};\n'
    Parser(source).program()
    return source


def builtins(evaluator):
    def agent_from(definition):
        definition = validate_definition(definition)
        return Agent(definition['name'], definition['config'], evaluator)

    def agent_info(value):
        if not isinstance(value, Agent):
            _error('agent_info 需要 Agent 可调用值，包装后的普通 Function 不能反向还原定义')
        if value.owner is not None and value.owner is not evaluator:
            raise LanguageError('Agent 不能跨执行上下文使用；请显式传递定义', code='ownership_error')
        return validate_definition({'kind': 'agent', 'name': value.name, 'config': value.config})

    def agent_source(value, name='saved_agent'):
        return source_definition(agent_info(value), name)

    return {'agent_from': agent_from, 'agent_info': agent_info, 'agent_source': agent_source}
