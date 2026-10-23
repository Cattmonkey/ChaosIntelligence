"""Portable executable module objects and a closed, source-free image linker.

Build uses the original source frontend. Link/load never parse, expand, format,
replay source or run module initialization. Decoded expanded syntax is the program
being admitted, not proof of equivalence to a self-declared source digest.
"""
import hashlib
import json
import math
import re

from .executable_ast import encode_tree, decode_tree, error, limit

OBJECT_SCHEMA = 'chaos.module-object/1'
IMAGE_SCHEMA = 'chaos.executable/1'
EXECUTABLE_ABI = 'chaos.expanded-ast/1'
MAX_EXECUTABLE_BYTES = 16000000
MAX_EXECUTABLE_VALUES = 800000
MAX_EXECUTABLE_DEPTH = 256
MAX_OBJECTS = 96
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_OBJECT_KEYS = frozenset(('schema', 'abi', 'module', 'languageVersion', 'sourceHash',
    'tree', 'imports', 'exports', 'macroExports', 'stdlibABI', 'objectHash'))
_IMAGE_KEYS = frozenset(('schema', 'abi', 'entry', 'objects', 'imageHash'))


def _canonical(value):
    try: return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError): error('Executable requires bounded canonical JSON')


def _hash(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _data(value):
    """Exact JSON types, budgets and cycles before any semantic inspection."""
    pending = [(value, 0, False)]; active = set(); count = 0; strings = 0
    while pending:
        item, depth, leaving = pending.pop()
        if leaving: active.remove(id(item)); continue
        count += 1
        if count > MAX_EXECUTABLE_VALUES or depth > MAX_EXECUTABLE_DEPTH: limit('Executable JSON structure is too large or deep')
        kind = type(item)
        if kind in (dict, list):
            if id(item) in active: error('Executable JSON cannot contain cycles')
            active.add(id(item)); pending.append((item, depth, True))
            if kind is dict:
                if any(type(key) is not str for key in item): error('Executable JSON requires exact String keys')
                pending.extend((child, depth + 1, False) for child in item.values())
                pending.extend((key, depth + 1, False) for key in item)
            else: pending.extend((child, depth + 1, False) for child in item)
        elif kind is str:
            try: strings += len(item.encode('utf-8'))
            except UnicodeError: error('Executable JSON requires valid Unicode')
            if strings > MAX_EXECUTABLE_BYTES: limit('Executable JSON strings exceed their aggregate limit')
        elif kind is int:
            if item.bit_length() > 64: error('Executable JSON metadata integers must be bounded; syntax integers use exact text')
        elif kind is float:
            if not math.isfinite(item): error('Executable JSON requires finite numbers')
        elif kind not in (bool, type(None)): error('Executable JSON cannot contain host or live language values')
    encoded = _canonical(value)
    if len(encoded.encode('utf-8')) > MAX_EXECUTABLE_BYTES: limit('Executable image exceeds16000000 bytes')
    return json.loads(encoded)


def _digest(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _module(value):
    from .modules import path_name, STDLIB
    if type(value) is not str: error('Executable module name must be an exact String')
    try: valid = value in STDLIB or path_name(value) == value and not value.startswith('std/') and value.endswith('.chaos')
    except Exception: error('Invalid executable module path')
    if not valid: error('Invalid executable module path')
    return value


def _names(value):
    from .language import Parser
    if type(value) is not list or len(value) > 10000 or any(type(name) is not str or not name.isidentifier() or name in Parser.reserved for name in value) or len(value) != len(set(value)):
        error('Invalid executable export names')
    return value


def _abi(value):
    """Only compare the used standard-module source ABI, never obtain code from it."""
    from .modules import STDLIB
    if type(value) is not dict or any(type(key) is not str or key not in STDLIB or not _digest(digest) for key, digest in value.items()):
        error('Invalid executable standard-module ABI')
    for key, digest in value.items():
        if hashlib.sha256(STDLIB[key].encode('utf-8')).hexdigest() != digest: error('Executable standard-module ABI mismatch')


def _object(value, modules):
    if type(value) is not dict or set(value) != _OBJECT_KEYS or value['schema'] != OBJECT_SCHEMA or value['abi'] != EXECUTABLE_ABI:
        error('Incompatible executable module object')
    name = _module(value['module'])
    if type(value['languageVersion']) is not int or value['languageVersion'] not in (1, 2) or not _digest(value['sourceHash']): error('Invalid executable module provenance')
    if not _digest(value['objectHash']) or _hash({k: v for k, v in value.items() if k != 'objectHash'}) != value['objectHash']: error('Executable object content hash mismatch')
    _abi(value['stdlibABI']); _names(value['exports']); _names(value['macroExports'])
    if set(value['exports']) & set(value['macroExports']): error('Executable value/macro exports overlap')
    imports = value['imports']
    if type(imports) is not dict or len(imports) > 10000 or any(type(k) is not str or type(v) is not str for k, v in imports.items()): error('Invalid executable import relocation table')
    tree = decode_tree(value['tree'], modules)
    from .modules import resolve_path, declarations
    from .language import Node, LanguageError
    actual = {}; aliases = set()
    for node in tree[1]:
        if node[0] == 'import':
            specifier, alias = node[1:]
            if alias in aliases: error('Duplicate executable import alias')
            aliases.add(alias)
            try: target = resolve_path(specifier, name)
            except LanguageError: error('Invalid executable import specifier')
            if target not in modules: error('Unresolved executable module import')
            actual[specifier] = target
    if actual != imports: error('Executable import relocations do not match syntax')
    # Macro declarations disappeared during build; their export names remain in
    # source export syntax but are compile-time interfaces, never runtime names.
    statements = []
    for node in tree[1]:
        if node[0] == 'export':
            statements.append(Node(('export', [key for key in node[1] if key not in value['macroExports']]), getattr(node, 'span', {})))
        else: statements.append(node)
    try: exports = declarations(Node(('block', statements)))
    except LanguageError as failure: error('Invalid executable declarations: ' + failure.message)
    if exports != value['exports']: error('Executable export table does not match syntax')
    declared_macro_exports = [key for node in tree[1] if node[0] == 'export' for key in node[1] if key in value['macroExports']]
    if declared_macro_exports != value['macroExports']: error('Executable macro export table does not match syntax')
    return tree


def _validate_objects(objects, entry):
    from .modules import STDLIB, MAX_DEPTH, MAX_FILES
    if type(objects) is not list or not 1 <= len(objects) <= MAX_OBJECTS: limit('Executable requires1–96 bounded module objects')
    entry = _module(entry)
    if entry in STDLIB: error('Executable entry must be a project module')
    names = []
    for value in objects:
        if type(value) is not dict or type(value.get('module')) is not str: error('Invalid executable object header')
        names.append(_module(value['module']))
    if len(names) != len(set(names)) or entry not in names: error('Duplicate executable modules or missing entry')
    if sum(name not in STDLIB for name in names) > MAX_FILES: limit('Executable has more than64 project modules')
    local = {name for name in names if name not in STDLIB}
    if any(any(name[:i] in local for i, char in enumerate(name) if char == '/') for name in local): error('Executable path is both a file and a directory')
    modules = frozenset(names); trees = {}; table = {row['module']: row for row in objects}
    for value in objects: trees[value['module']] = _object(value, modules)
    standard = {name: hashlib.sha256(STDLIB[name].encode('utf-8')).hexdigest() for name in names if name in STDLIB}
    for value in objects:
        if value['stdlibABI'] != standard: error('Executable standard-module closure is incomplete or inconsistent')
        if value['module'] in STDLIB and value['sourceHash'] != standard[value['module']]: error('Executable standard-module provenance mismatch')
    active = []; seen = set()
    def visit(name):
        if name in active: error('Executable contains an import cycle')
        if name in seen: return
        if len(active) >= MAX_DEPTH: limit('Executable module dependency depth exceeds32')
        active.append(name)
        for target in table[name]['imports'].values(): visit(target)
        active.pop(); seen.add(name)
    visit(entry)
    for name in names: visit(name)
    return trees


def compile_objects(source, files=None, entry='main.chaos', language_versions=None, *, libraries=None, macro_interfaces=False):
    """Compile one closed source snapshot into separate expanded module objects.

    The default closed v1 build is preserved. Explicit libraries select the v2
    runtime ABI; macro_interfaces or v3 libraries select persistent v3 phases.
    """
    if type(macro_interfaces) is not bool: error('macro_interfaces must be an exact Bool')
    if libraries is not None or macro_interfaces:
        from .separate_compilation import compile_objects as compile_separate
        return compile_separate(source, files, entry, language_versions, libraries=[] if libraries is None else libraries, macro_interfaces=macro_interfaces)
    from .modules import ProgramBundle, STDLIB
    bundle = ProgramBundle(source, files, entry, language_versions=language_versions)
    modules = frozenset(bundle.expanded_trees)
    standard = {name: hashlib.sha256(STDLIB[name].encode('utf-8')).hexdigest() for name in modules if name in STDLIB}
    objects = []
    for name, tree in bundle.expanded_trees.items():
        value = {'schema': OBJECT_SCHEMA, 'abi': EXECUTABLE_ABI, 'module': name,
            'languageVersion': bundle.language_versions.get(name, 2),
            'sourceHash': bundle.compilation_units[name]['sourceHash'],
            'tree': encode_tree(tree, modules), 'imports': dict(bundle.dependencies[name]),
            'exports': list(bundle.exports[name]), 'macroExports': list(bundle.macro_exports[name]),
            'stdlibABI': dict(standard)}
        value['objectHash'] = _hash(value); objects.append(value)
    objects = _data(objects)
    _validate_objects(objects, bundle.entry)
    return objects


def link_objects(objects, entry='main.chaos'):
    """Resolve a closed set of module objects before any module effects."""
    objects = _data(objects)
    if type(objects) is list and any(type(row) is dict and row.get('schema') in ('chaos.module-object/2', 'chaos.module-object/3') for row in objects):
        from .separate_compilation import link_objects as link_separate
        return link_separate(objects, entry)
    _validate_objects(objects, entry)
    image = {'schema': IMAGE_SCHEMA, 'abi': EXECUTABLE_ABI, 'entry': entry, 'objects': objects}
    image['imageHash'] = _hash(image)
    return image


def mount_objects(objects, prefix):
    """Purely relocate a validated compiled library into a module namespace."""
    from .object_mounting import mount_objects as mount
    return mount(objects, prefix)


def compile_executable(source, files=None, entry='main.chaos', language_versions=None, *, libraries=None, macro_interfaces=False):
    return link_objects(compile_objects(source, files, entry, language_versions, libraries=libraries, macro_interfaces=macro_interfaces), entry)


def load_executable(data):
    """Admit fresh expanded syntax into the original evaluator bundle contract.

    The existing source-free literal planner is permitted. Explicit optional JIT
    later analyzes/lowers these loaded nodes normally; no saved CFG is imported.
    """
    image = _data(data)
    if type(image) is dict and image.get('schema') in ('chaos.executable/2', 'chaos.executable/3'):
        from .separate_compilation import load_executable as load_separate
        return load_separate(image)
    if type(image) is not dict or set(image) != _IMAGE_KEYS or image['schema'] != IMAGE_SCHEMA or image['abi'] != EXECUTABLE_ABI:
        error('Incompatible portable executable image')
    if not _digest(image['imageHash']) or _hash({k: v for k, v in image.items() if k != 'imageHash'}) != image['imageHash']: error('Executable image content hash mismatch')
    trees = _validate_objects(image['objects'], image['entry'])
    return _bundle_from_trees(image, trees)


def _bundle_from_trees(image, trees):
    from .modules import ProgramBundle, STDLIB
    from .portable_optimizer import build_plan
    bundle = ProgramBundle.__new__(ProgramBundle)
    bundle.entry = image['entry']
    bundle.files = {row['module']: '' for row in image['objects'] if row['module'] not in STDLIB}
    bundle.language_versions = {row['module']: row['languageVersion'] for row in image['objects'] if row['module'] not in STDLIB}
    bundle.trees = trees
    bundle.expanded_trees = trees
    bundle.exports = {row['module']: list(row['exports']) for row in image['objects']}
    bundle.macro_exports = {row['module']: list(row['macroExports']) for row in image['objects']}
    bundle.dependencies = {row['module']: dict(row['imports']) for row in image['objects']}
    bundle.imports = {name: {node[2]: bundle.dependencies[name][node[1]] for node in tree[1] if node[0] == 'import'} for name, tree in trees.items()}
    bundle.compilation_units = {row['module']: {'sourceHash': row['sourceHash'],
        'unitId': row['objectHash'], 'cacheStatus': 'executable'} for row in image['objects']}
    bundle._literal_plan = build_plan(trees)
    bundle._executable_image_hash = image['imageHash']
    bundle._executable_source_available = False
    return bundle
