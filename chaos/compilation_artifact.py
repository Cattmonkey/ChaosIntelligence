"""Persistent source-authoritative, data-only portable flow templates.

Loading repeats parsing, linking, macro expansion and deterministic lowering for
validation. It is neither a native-code importer nor a saved continuation. Only
an existing explicit compile_function request links a decoded template; original
function creation, defaults, module effects and compilation charging are intact.
"""
from dataclasses import dataclass
from types import MappingProxyType, SimpleNamespace
import hashlib
import json
import math

SCHEMA = 'chaos.compiled-artifact/1'
ARTIFACT_ABI = 'chaos.portable-flow-template/1'
FLOW_ABI = 'pc-host-events/1'
EFFECT_ABI = 'chaos.structural-effects/1'
TARGET = 'portable-flow'
MAX_ARTIFACT_BYTES = 8000000
MAX_ARTIFACT_VALUES = 400000
MAX_ARTIFACT_DEPTH = 256
MAX_TEMPLATES = 512
MAX_TOTAL_INSTRUCTIONS = 32768
_FLAVORS = frozenset(('fn', 'gen_fn', 'async_fn', 'async_gen_fn'))
_NODE_EVENTS = frozenset(('enter', 'exit', 'eval', 'await', 'yield', 'delegate',
                          'acquire', 'bind', 'protect', 'resource', 'pattern', 'operand', 'access'))


def _error(message):
    from .language import LanguageError
    raise LanguageError(message, code='artifact_error')


def _limit(message):
    from .language import ResourceLimit
    raise ResourceLimit(message)


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        _error('Artifact requires bounded canonical JSON data')


def _hash(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _data(value):
    """Validate exact JSON types before any parsing, callbacks or template use."""
    pending = [(value, 0, False)]
    active = set(); count = 0
    while pending:
        item, depth, leaving = pending.pop()
        if leaving:
            active.remove(id(item)); continue
        count += 1
        if count > MAX_ARTIFACT_VALUES or depth > MAX_ARTIFACT_DEPTH:
            _limit('Artifact JSON structure exceeds its bounded resource limit')
        kind = type(item)
        if kind in (dict, list):
            if id(item) in active: _error('Artifact JSON cannot contain cycles')
            active.add(id(item)); pending.append((item, depth, True))
            if kind is dict:
                if any(type(key) is not str for key in item): _error('Artifact JSON keys must be strings')
                pending.extend((child, depth + 1, False) for child in item.values())
            else: pending.extend((child, depth + 1, False) for child in item)
        elif kind is float:
            if not math.isfinite(item): _error('Artifact JSON requires finite numbers')
        elif kind is int:
            if item.bit_length() > 4096: _limit('Artifact metadata integer exceeds its bounded limit')
        elif kind not in (str, bool, type(None)):
            _error('Artifact JSON cannot contain live runtime or host values')
    encoded = _canonical(value)
    if len(encoded.encode('utf-8')) > MAX_ARTIFACT_BYTES:
        _limit('Artifact JSON exceeds 8000000 bytes')
    return json.loads(encoded)


def _syntax_hash(value):
    """Static tree identity; never serialize runtime module/capture handles.

    Fresh original macro expansion reconstitutes its own numeric/condition
    captures. Static source contents, spans, hygiene aliases and macro-module
    provenance are represented; runtime _module_scope is deliberately excluded.
    """
    from .language import Node
    count = [0]; active = set()
    def visit(item, depth=0):
        count[0] += 1
        if count[0] > MAX_ARTIFACT_VALUES or depth > MAX_ARTIFACT_DEPTH:
            _limit('Artifact syntax identity exceeds its bounded limit')
        kind = type(item)
        if kind in (Node, tuple, list, dict):
            if id(item) in active: _error('Cyclic syntax is not an artifact input')
            active.add(id(item))
            try:
                if kind is dict:
                    if any(type(key) is not str for key in item): _error('Syntax metadata keys must be strings')
                    return ['dict', [[key, visit(item[key], depth + 1)] for key in sorted(item)]]
                children = [visit(child, depth + 1) for child in item]
                if kind is Node:
                    metadata = {'span': getattr(item, 'span', {})}
                    for key in ('macro_bindings', 'macro_module'):
                        if hasattr(item, key): metadata[key] = getattr(item, key)
                    return ['node', children, visit(metadata, depth + 1)]
                return ['tuple' if kind is tuple else 'list', children]
            finally: active.remove(id(item))
        if kind is int: return ['int', hex(item)]
        if kind is float: return ['float', item.hex()]
        if kind is bool: return ['bool', item]
        if kind is str: return ['str', item]
        if item is None: return ['null']
        _error('Syntax contains a live runtime or host value')
    return _hash(visit(value))


def _walk(value):
    """Deterministic structural paths; only original syntax containers."""
    from .language import Node
    pending = [(value, ())]; count = 0
    while pending:
        item, path = pending.pop(); count += 1
        if count > MAX_ARTIFACT_VALUES or len(path) > MAX_ARTIFACT_DEPTH:
            _limit('Artifact syntax relocation exceeds its bounded limit')
        yield path, item
        if type(item) in (Node, tuple, list):
            pending.extend((item[index], path + (index,)) for index in range(len(item) - 1, -1, -1))
        elif type(item) is dict:
            if any(type(key) is not str for key in item): _error('Syntax relocation keys must be strings')
            pending.extend((item[key], path + (key,)) for key in sorted(item, reverse=True))


def _resolve(body, path):
    from .language import Node
    if type(path) is not list or len(path) > MAX_ARTIFACT_DEPTH:
        _error('Invalid artifact syntax relocation')
    item = body
    for part in path:
        if type(item) in (Node, tuple, list) and type(part) is int and 0 <= part < len(item): item = item[part]
        elif type(item) is dict and type(part) is str and part in item: item = item[part]
        else: _error('Artifact syntax relocation is outside its function body')
    if type(item) not in (Node, tuple) or not item:
        _error('Artifact semantic relocation must select an actual syntax node')
    return item


def _pack(value):
    if type(value) is tuple: return {'tuple': [_pack(item) for item in value]}
    if value is None or type(value) in (str, int, bool): return value
    _error('Unexpected non-data flow instruction metadata')


def _unpack(value):
    if type(value) is dict and set(value) == {'tuple'} and type(value['tuple']) is list:
        return tuple(_unpack(item) for item in value['tuple'])
    if value is None or type(value) in (str, int, bool): return value
    _error('Malformed artifact flow instruction metadata')


def _encode_program(program, body):
    from .language import Node
    paths = {}
    for path, item in _walk(body):
        if type(item) in (Node, tuple) and item: paths.setdefault(id(item), path)
    rows = []
    for instruction in program.instructions:
        row = []
        for index, value in enumerate(instruction):
            if index == 1 and instruction[0] in _NODE_EVENTS:
                if id(value) not in paths: _error('Flow instruction references syntax outside its body')
                row.append({'node': list(paths[id(value)])})
            else: row.append(_pack(value))
        rows.append(row)
    return rows


def _decode_template(template, body):
    """Construct the saved, remapped program, not a regenerated validator plan."""
    from .jit_flow import FlowProgram, node_ids, validate_program
    rows = template.get('instructions'); flag_count = template.get('flagCount'); effects = template.get('effects')
    if type(rows) is not list or not 0 < len(rows) <= 1024 or type(flag_count) is not int or not 0 <= flag_count <= 1024 or type(effects) is not list or len(effects) != len(rows):
        _error('Malformed artifact flow template')
    instructions = []
    for row in rows:
        if type(row) is not list or not row or type(row[0]) is not str: _error('Malformed artifact instruction row')
        values = []
        for index, item in enumerate(row):
            if index == 1 and row[0] in _NODE_EVENTS:
                if type(item) is not dict or set(item) != {'node'}: _error('Malformed semantic syntax relocation')
                values.append(_resolve(body, item['node']))
            else: values.append(_unpack(item))
        instructions.append(tuple(values))
    if any(type(tags) is not list or any(type(tag) is not str for tag in tags) or tags != sorted(set(tags)) for tags in effects):
        _error('Malformed artifact effect vector')
    program = FlowProgram(tuple(instructions), flag_count, node_ids=node_ids(body),
                          effects=tuple(frozenset(tags) for tags in effects))
    try: program.layout_seal = validate_program(program)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        _error('Invalid artifact flow layout')
    return program


def _template(module, path, node):
    from .jit_flow import lower
    program = lower(SimpleNamespace(body=node[3], flavor=node[0]))
    row = {'module': module, 'path': list(path), 'flavor': node[0],
           'definitionHash': _syntax_hash(node), 'bodyHash': _syntax_hash(node[3]),
           'instructions': _encode_program(program, node[3]), 'flagCount': program.flag_count,
           'effects': [sorted(tags) for tags in program.effects]}
    row['templateHash'] = _hash(row)
    return row


def _manifest(bundle):
    from .language import Node
    from .jit import Unsupported
    from .compilation_cache import COMPILER_ABI
    from .version import __version__
    templates = []; unsupported = []; total = 0; functions = 0
    for module in sorted(bundle.expanded_trees):
        for path, node in _walk(bundle.expanded_trees[module]):
            if type(node) not in (Node, tuple) or not node or type(node[0]) is not str or node[0] not in _FLAVORS: continue
            functions += 1
            if functions > MAX_TEMPLATES: _limit('Artifact has more than512 function templates')
            if len(node) != 5: _error('Malformed expanded function syntax')
            try: row = _template(module, path, node)
            except (Unsupported, RecursionError) as error:
                unsupported.append({'module': module, 'path': list(path), 'flavor': node[0],
                                    'reason': 'depth_limit' if isinstance(error, RecursionError) else str(error)})
                continue
            total += len(row['instructions'])
            if total > MAX_TOTAL_INSTRUCTIONS: _limit('Artifact has more than32768 flow instructions')
            templates.append(row)
    artifact = {'schema': SCHEMA, 'abi': ARTIFACT_ABI, 'target': TARGET,
        'compiler': {'version': __version__, 'frontendABI': COMPILER_ABI, 'flowABI': FLOW_ABI, 'effectABI': EFFECT_ABI},
        'entry': bundle.entry, 'files': dict(bundle.files), 'fileOrder': list(bundle.files),
        'languageVersions': dict(bundle.language_versions),
        'units': {name: {key: row[key] for key in ('sourceHash', 'unitId')} for name, row in sorted(bundle.compilation_units.items())},
        'dependencies': {name: dict(value) for name, value in sorted(bundle.dependencies.items())},
        'expandedHashes': {name: _syntax_hash(tree) for name, tree in sorted(bundle.expanded_trees.items())},
        'templates': templates, 'unsupported': unsupported}
    artifact['artifactHash'] = _hash(artifact)
    return _data(artifact)


def compile_artifact(source, files=None, entry='main.chaos', *, language_versions=None):
    """Build inert closed-source JSON; do not form or invoke any Function."""
    from .modules import ProgramBundle
    return _manifest(ProgramBundle(source, files, entry, language_versions=language_versions))


@dataclass(frozen=True)
class _StoredTemplate:
    body: object
    flavor: str
    body_hash: str
    encoded: str


@dataclass(frozen=True)
class _ArtifactTemplates:
    artifact_hash: str
    entries: object


def load_artifact(data):
    """Validate closed source and every saved template before module execution."""
    from .modules import ProgramBundle
    from .language import Node
    artifact = _data(data)
    keys = {'schema', 'abi', 'target', 'compiler', 'entry', 'files', 'fileOrder', 'languageVersions',
            'units', 'dependencies', 'expandedHashes', 'templates', 'unsupported', 'artifactHash'}
    if type(artifact) is not dict or set(artifact) != keys or artifact['schema'] != SCHEMA or artifact['abi'] != ARTIFACT_ABI or artifact['target'] != TARGET:
        _error('Incompatible portable compilation artifact')
    saved_hash = artifact['artifactHash']
    if type(saved_hash) is not str or len(saved_hash) != 64 or _hash({key: value for key, value in artifact.items() if key != 'artifactHash'}) != saved_hash:
        _error('Artifact content hash mismatch')
    files = artifact['files']; order = artifact['fileOrder']
    if type(files) is not dict or type(order) is not list or any(type(name) is not str for name in order) or len(order) != len(set(order)) or set(order) != set(files):
        _error('Malformed artifact source ordering')
    if type(artifact['entry']) is not str or artifact['entry'] not in files or type(artifact['languageVersions']) is not dict or set(artifact['languageVersions']) != set(files):
        _error('Malformed artifact source closure')
    if any(type(name) is not str or type(text) is not str for name, text in files.items()): _error('Artifact sources must be text')
    bundle = ProgramBundle(files[artifact['entry']], {name: files[name] for name in order}, artifact['entry'],
                           language_versions=artifact['languageVersions'])
    # Complete regenerated manifest equality is a semantic validator. A caller
    # cannot authorize altered branches/effects/relocations by recomputing SHA.
    expected = _manifest(bundle)
    if _canonical(artifact) != _canonical(expected): _error('Artifact does not match its deterministic closed-source compilation')
    entries = {}
    for row in artifact['templates']:
        node = _resolve(bundle.expanded_trees[row['module']], row['path'])
        if type(node) not in (Node, tuple) or node[0] != row['flavor']: _error('Artifact function relocation mismatch')
        _decode_template(row, node[3])
        key = (id(node[3]), node[0])
        stored = _StoredTemplate(node[3], node[0], row['bodyHash'], _canonical(row))
        if key in entries:
            previous = json.loads(entries[key].encoded)
            if any(previous[field] != row[field] for field in ('bodyHash', 'instructions', 'flagCount', 'effects')):
                _error('Ambiguous artifact function body identity')
        else: entries[key] = stored
    bundle._flow_artifact_templates = _ArtifactTemplates(saved_hash, MappingProxyType(entries))
    return bundle


def link_flow_template(evaluator, function):
    """Demand-driven pure relocation; normal dynamic code returns None.

    Explicit compile_function still performs its original analysis, ticks,
    kernel quota and optional native/scalar preparation around this lookup.
    """
    registry = getattr(getattr(evaluator, 'bundle', None), '_flow_artifact_templates', None)
    if registry is None: return None
    if type(registry) is not _ArtifactTemplates or type(registry.entries) is not MappingProxyType:
        _error('Malformed private artifact template registry')
    stored = registry.entries.get((id(function.body), function.flavor))
    if stored is None: return None
    if type(stored) is not _StoredTemplate or stored.body is not function.body or stored.flavor != function.flavor:
        _error('Malformed private artifact template binding')
    if _syntax_hash(function.body) != stored.body_hash: return None
    template = json.loads(stored.encoded)
    program = _decode_template(template, function.body)
    program._artifact_hash = registry.artifact_hash
    program._artifact_template_id = template['templateHash']
    program._artifact_decoded = True
    return program
