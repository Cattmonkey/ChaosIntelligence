"""Owned immutable source snapshots and cold, host-controlled program effects."""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field

from .language import LanguageError, ResourceLimit, Parser

DEFINITION_CHARS = 1200000
PAYLOAD_CHARS = 1500000
MAX_VERSION = 2 ** 63 - 1


def _error(message):
    raise LanguageError(message, code='program_error')


def _data(value, *, limit=250000, max_nodes=20000, checkpoint=None):
    from .extensions import json_copy
    if checkpoint: checkpoint()
    try:
        result = json_copy(value, max_chars=limit, max_nodes=max_nodes)
    except (ValueError, TypeError, OverflowError, UnicodeError) as error:
        _error('Program 需要有界 JSON: '+str(error))
    if checkpoint: checkpoint()
    return result


def configuration_schema():
    path = {'type':'string','minLength':1,'maxLength':240}
    return {'type':'object','additionalProperties':False,
            'required':['kind','entry','files','languageVersions'], 'properties':{
                'kind':{'const':'program'}, 'entry':path,
                'files':{'type':'object','minProperties':1,'maxProperties':64,
                         'propertyNames':dict(path), 'additionalProperties':{'type':'string','maxLength':100000}},
                'languageVersions':{'type':'object','minProperties':1,'maxProperties':64,
                                    'propertyNames':dict(path), 'additionalProperties':{'type':'integer','enum':[1,2]}}}}


def validate_definition(definition, *, checkpoint=None):
    from .modules import ProgramBundle
    definition = _data(definition, limit=DEFINITION_CHARS, checkpoint=checkpoint)
    if type(definition) is not dict or set(definition) != {'kind','entry','files','languageVersions'} or definition['kind'] != 'program':
        _error('Program 定义需要 kind/entry/files/languageVersions')
    files, versions, entry = definition['files'], definition['languageVersions'], definition['entry']
    if (type(files) is not dict or type(versions) is not dict or type(entry) is not str
            or entry not in files or set(versions) != set(files)):
        _error('Program 入口与语言版本必须精确对应 files')
    bundle = ProgramBundle(files[entry], files, entry, language_versions=versions, checkpoint=checkpoint)
    return definition_from_bundle(bundle)


def definition_from_bundle(bundle):
    if getattr(bundle, '_executable_source_available', True) is False:
        _error('Executable image has no source Program definition; use its image identity')
    return {'kind':'program', 'entry':bundle.entry, 'files':dict(bundle.files),
            'languageVersions':dict(bundle.language_versions)}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def canonical_hash(definition):
    """Hash already-validated JSON; validation is the caller's responsibility."""
    return hashlib.sha256(_canonical(definition).encode('utf-8')).hexdigest()


def definition_hash(definition, *, checkpoint=None):
    return canonical_hash(validate_definition(definition, checkpoint=checkpoint))


def source_definition(definition, name='saved_program', *, checkpoint=None):
    if (type(name) is not str or not 1 <= len(name) <= 128
            or re.fullmatch(r'[^\W\d]\w*', name, re.UNICODE) is None
            or name in Parser.reserved or name == 'program_from'):
        _error('Program 导出绑定名无效')
    definition = validate_definition(definition, checkpoint=checkpoint)
    source = 'let '+name+' = program_from('+_canonical(definition)+');\nexport {'+name+'};\n'
    Parser(source, checkpoint=checkpoint).program()
    return source


def validate_cases(cases, definition):
    cases = _data(cases)
    if type(cases) is not list or not 1 <= len(cases) <= 16:
        _error('Program cases 需要 1–16 项')
    for case in cases:
        if type(case) is not dict or not {'input','expect'} <= set(case) or set(case)-{'input','expect','entry','fixtures'}:
            _error('Program case 需要 input/expect 和可选 entry/fixtures')
        if 'fixtures' in case:
            from .evaluation import validate_fixtures
            case['fixtures']=validate_fixtures(case['fixtures'])
        if 'entry' in case and (type(case['entry']) is not str or case['entry'] not in definition['files']):
            _error('Program case entry 必须存在于候选包')
    return cases


def cases_hash(cases): return canonical_hash(cases)


def _origin(value):
    if value is None: return None
    value = _data(value)
    if (type(value) is not dict or set(value) != {'projectId','version','definitionHash'}
            or type(value['projectId']) is not str or not 1 <= len(value['projectId']) <= 128
            or type(value['version']) is not int or value['version'] < 1
            or type(value['definitionHash']) is not str or re.fullmatch('[0-9a-f]{64}', value['definitionHash']) is None):
        _error('可信 Program origin 无效')
    return value


@dataclass(frozen=True, eq=False)
class Program:
    definition_json: str
    owner: object
    origin_json: object = None
    entry: str = field(init=False)
    definition_hash: str = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, 'entry', json.loads(self.definition_json)['entry'])
        object.__setattr__(self, 'definition_hash', hashlib.sha256(self.definition_json.encode('utf-8')).hexdigest())

    @property
    def definition(self): return json.loads(self.definition_json)

    @property
    def origin(self): return json.loads(self.origin_json) if self.origin_json is not None else None


def owned(value, evaluator):
    if type(value) is not Program: _error('需要受控 Program 值')
    if value.owner is not evaluator:
        raise LanguageError('Program 不能跨执行上下文使用；请传递定义', code='ownership_error')
    return value


def describe(value): return {'$program':value.entry, 'definitionHash':value.definition_hash}


@dataclass(frozen=True)
class ProgramResult:
    """An internally validated reflection value; no live value crosses the host."""
    value: object


@dataclass(frozen=True)
class ProgramRequest:
    operation: str
    payload: dict


@dataclass(frozen=True)
class _RevisionPayload:
    """Private, immutable data crossing only the revision worker boundary.

    No Program/Env is created in a worker. The resumed AST validates and owns
    the resulting Program; ordinary effect results keep their original limit.
    """
    json: str

    @classmethod
    def capture(cls, value):
        return cls(_canonical(_data(value, limit=PAYLOAD_CHARS, max_nodes=50000)))


def _version(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= MAX_VERSION:
        _error('Program version/after 需要有界'+('非负' if zero else '正')+'整数')
    return value


def _history(value, after, limit):
    value = _data(value)
    fields = {'items','nextCursor','currentVersion','firstAvailableVersion'}
    if type(value) is not dict or set(value) != fields or type(value['items']) is not list:
        _error('Program history 宿主响应无效')
    head, first = _version(value['currentVersion']), _version(value['firstAvailableVersion'])
    if first > head or len(value['items']) > limit: _error('Program history 范围无效')
    previous = after
    for item in value['items']:
        if type(item) is not dict or set(item) != {'version','definitionHash','entry','created','kind','runId','reason'}:
            _error('Program history 元数据无效')
        version = _version(item['version'])
        if not first <= version <= head or version <= previous: _error('Program history 版本顺序无效')
        previous = version
        if type(item['definitionHash']) is not str or re.fullmatch('[0-9a-f]{64}', item['definitionHash']) is None:
            _error('Program history 哈希无效')
        from .modules import path_name
        if type(item['entry']) is not str or path_name(item['entry']) != item['entry']:
            _error('Program history 入口无效')
        if type(item['created']) not in (int,float) or not 0 <= item['created'] <= 1e20 or not math.isfinite(item['created']):
            _error('Program history 时间无效')
        if item['kind'] not in ('created','saved','patched','adopted','migration'):
            _error('Program history kind 无效')
        if item['runId'] is not None and (type(item['runId']) is not str or not 1 <= len(item['runId']) <= 128):
            _error('Program history runId 无效')
        if type(item['reason']) is not str or len(item['reason']) > 1000:
            _error('Program history reason 无效')
    cursor = value['nextCursor']
    if cursor is not None:
        _version(cursor)
        if not value['items'] or cursor != previous or cursor >= head: _error('Program history cursor 无效')
    return value


def _revision(value, version, evaluator):
    if type(value) is not _RevisionPayload: _error('Program revision 需要专用宿主响应')
    value = json.loads(value.json)
    if type(value) is not dict or set(value) != {'definition','origin'}:
        _error('Program revision 宿主响应无效')
    origin = _origin(value['origin'])
    if origin is None or origin['version'] != version: _error('Program revision 来源版本不匹配')
    expected = _origin(evaluator.program_origin)
    if expected is not None and origin['projectId'] != expected['projectId']:
        _error('Program revision 来源项目不匹配')
    definition = validate_definition(value['definition'], checkpoint=evaluator.tick)
    if origin['definitionHash'] != canonical_hash(definition): _error('Program revision 哈希不匹配')
    return Program(_canonical(definition), evaluator, _canonical(origin))


def run_request(evaluator, value, input=None):
    owned(value, evaluator)
    return ProgramRequest('run', {'definition':value.definition, 'input':_data(input)})


def resolve(evaluator, request):
    payload = _data(request.payload, limit=PAYLOAD_CHARS, max_nodes=50000, checkpoint=evaluator.tick)
    def call():
        from .suspension import Effect
        task = evaluator.suspension.current.task
        if task.recording_scope is not None:
            if evaluator.mode_handler is not None:
                yield Effect('mode', {'operation':'recording.abort', 'payload':{
                    'scopeId':task.recording_scope, 'reason':'program_effect_forbidden'}})
            raise LanguageError('录制 scope 不支持 Program 效果', code='mode_error')
        if evaluator.program_handler is None:
            raise LanguageError('当前环境不允许 Program 效果', code='capability_error')
        evaluator.tick()
        result = yield Effect('program', {'operation':request.operation, 'payload':payload})
        if request.operation == 'revision': result = _revision(result, payload['version'], evaluator)
        elif request.operation == 'history': result = _history(result, payload['after'], payload['limit'])
        else: result = _data(result)
        evaluator.tick()
        return result
    return evaluator.suspension.task(call())


def builtins(evaluator):
    def make(definition, origin=None):
        return Program(_canonical(validate_definition(definition, checkpoint=evaluator.tick)), evaluator,
                       _canonical(_origin(origin)) if origin is not None else None)

    def program_from(definition): return make(definition)
    def program_link(definition,libraries):
        from .program_linking import _assemble, _mounts
        if type(definition) is not dict:_error('program_link definition requires a Record')
        mounts=_mounts(libraries,evaluator.tick)
        # Check every live owner before inspecting any library definition.
        for mount in mounts:
            evaluator.tick();owned(libraries[mount],evaluator)
        linked=_assemble(definition,libraries,checkpoint=evaluator.tick,decode=lambda value:value.definition)
        return Program(_canonical(linked),evaluator)
    def program_info(value): return ProgramResult(owned(value,evaluator).definition)
    def program_metadata(value):
        owned(value,evaluator)
        return {'definitionHash':value.definition_hash, 'origin':value.origin}
    def current_program():
        scope=getattr(evaluator.suspension.current,'module_scope',None)
        bundle=scope.bundle if scope is not None else getattr(evaluator,'bundle',None)
        if bundle is None: _error('当前执行没有 ProgramBundle')
        definition = definition_from_bundle(bundle)
        origin = None if scope is not None else _origin(evaluator.program_origin)
        if origin is not None and origin['definitionHash'] != canonical_hash(definition): origin = None
        # The active bundle was already compiled; reflection does not expand its macros again.
        return Program(_canonical(definition), evaluator, _canonical(origin) if origin is not None else None)
    def program_ir(value, file=None):
        from .program import parse_program
        definition = owned(value,evaluator).definition
        file = definition['entry'] if file is None else file
        if type(file) is not str or file not in definition['files']: _error('Program 文件不存在')
        evaluator.tick()
        # Charge parsing work through the optional compiler checkpoint as well.
        Parser(definition['files'][file],file,legacy_items=definition['languageVersions'][file]==1,checkpoint=evaluator.tick).program()
        ir = parse_program(definition['files'][file],file,language_version=definition['languageVersions'][file])
        return ProgramResult(_data(ir,limit=DEFINITION_CHARS,max_nodes=50000,checkpoint=evaluator.tick))
    def program_edit(value, file, operations):
        from .program import patch_program, format_program
        owned(value,evaluator)
        ir = program_ir(value,file).value
        operations = _data(operations,limit=DEFINITION_CHARS,checkpoint=evaluator.tick)
        edited = patch_program(ir,operations)
        # The canonical renderer writes legacy access as .[], so upgrading this
        # edited module preserves old access and permits newly inserted [] nodes.
        edited['languageVersion'] = 2
        definition = value.definition
        definition['files'][file] = format_program(edited)
        definition['languageVersions'][file] = edited.get('languageVersion',2)
        return make(definition,value.origin)
    def program_compile(value):
        owned(value, evaluator)
        from .compiler import compile_program
        definition = value.definition
        report = compile_program(definition['files'][definition['entry']], definition['files'],
                                 definition['entry'], language_versions=definition['languageVersions'],
                                 checkpoint=evaluator.tick)
        return _data(report, checkpoint=evaluator.tick)

    def program_references(value, file, position, include_declaration=True):
        from .symbols import references
        definition = owned(value,evaluator).definition
        if type(file) is not str or file not in definition['files']: _error('Program 文件不存在')
        result = references(definition['files'][file], file=file, files=definition['files'],
                            entry=definition['entry'], position=position,
                            language_versions=definition['languageVersions'],
                            include_declaration=include_declaration, checkpoint=evaluator.tick)
        return ProgramResult(_data(result,limit=DEFINITION_CHARS,max_nodes=50000,checkpoint=evaluator.tick))
    def program_rename(value, file, position, new_name):
        from .symbols import rename
        definition = owned(value,evaluator).definition
        if type(file) is not str or file not in definition['files']: _error('Program 文件不存在')
        result = rename(definition['files'][file], file=file, files=definition['files'],
                        entry=definition['entry'], position=position, new_name=new_name,
                        language_versions=definition['languageVersions'], checkpoint=evaluator.tick)
        candidate = {'kind':'program','entry':result['entry'],'files':result['files'],
                     'languageVersions':result['languageVersions']}
        # The symbol engine has parsed and checked the whole candidate without
        # expanding macros. Preserve that inert boundary; probe/adopt compile it.
        candidate = _data(candidate,limit=DEFINITION_CHARS,checkpoint=evaluator.tick)
        return Program(_canonical(candidate),evaluator,value.origin_json)
    def program_source(value, name='saved_program'):
        return source_definition(owned(value,evaluator).definition,name,checkpoint=evaluator.tick)
    def program_history(after=0, limit=50):
        _version(after, zero=True)
        if type(limit) is not int or not 1 <= limit <= 100: _error('Program history limit 需要 1–100 整数')
        return ProgramRequest('history', {'after':after,'limit':limit})
    def program_revision(version):
        return ProgramRequest('revision', {'version':_version(version)})
    def run_program(value, input=None): return run_request(evaluator,value,input)
    def program_probe(value, cases):
        definition = owned(value,evaluator).definition
        return ProgramRequest('probe', {'definition':definition,'cases':validate_cases(cases,definition)})
    def program_adopt(base, candidate, cases, reason=''):
        owned(base,evaluator); owned(candidate,evaluator)
        origin = base.origin
        if origin is None or base.definition_hash != origin['definitionHash']:
            _error('采用需要未修改且有可信 origin 的基线 Program')
        if type(reason) is not str or len(reason) > 1000: _error('采用 reason 最多 1000 字符')
        definition = candidate.definition
        return ProgramRequest('adopt', {'definition':definition, 'cases':validate_cases(cases,definition),
                                        'base':origin, 'reason':reason})
    return {'program_from':program_from,'program_link':program_link,'program_info':program_info,'program_metadata':program_metadata,
            'current_program':current_program,'program_ir':program_ir,'program_edit':program_edit,
            'program_references':program_references,'program_rename':program_rename,'program_compile':program_compile,
            'program_source':program_source,'run_program':run_program,'program_probe':program_probe,
            'program_adopt':program_adopt,'program_history':program_history,'program_revision':program_revision}
