"""Content-bound, independently compiled runtime libraries.

Only new source units pass through the original frontend. Supplied expanded
objects are validated data and are never converted to source or re-expanded.
The v2 contract has no external macro phase. Explicit v3 publication carries
only raw macro declarations for the original bounded pure Expander.
"""
import hashlib

from .executable_ast import encode_tree, error, limit
from .executable_image import (_data, _hash, _digest, _module, _object,
    _validate_objects, _OBJECT_KEYS, _IMAGE_KEYS, MAX_OBJECTS,
    OBJECT_SCHEMA, EXECUTABLE_ABI)

OBJECT_SCHEMA_V2 = 'chaos.module-object/2'
IMAGE_SCHEMA_V2 = 'chaos.executable/2'
EXECUTABLE_ABI_V2 = 'chaos.expanded-ast/2'
_OBJECT_KEYS_V2 = _OBJECT_KEYS | {'requires'}
OBJECT_SCHEMA_V3 = 'chaos.module-object/3'
IMAGE_SCHEMA_V3 = 'chaos.executable/3'
EXECUTABLE_ABI_V3 = 'chaos.expanded-ast/3'
PHASE_SCHEMA = 'chaos.macro-phase/1'
_OBJECT_KEYS_V3 = _OBJECT_KEYS_V2 | {'phaseCapsule'}


def _index(objects, entry=None, *, allow_empty=False):
    from .modules import STDLIB, MAX_FILES
    if type(objects) is not list or len(objects) > MAX_OBJECTS or not objects and not allow_empty:
        limit('Executable requires1–96 bounded module objects')
    table = {}
    for row in objects:
        if type(row) is not dict: error('Invalid executable object header')
        name = _module(row.get('module'))
        if name in table: error('Duplicate executable module')
        table[name] = row
    if entry is not None:
        entry = _module(entry)
        if entry in STDLIB or entry not in table: error('Executable entry must be a project module in the closure')
    local = {name for name in table if name not in STDLIB}
    if len(local) > MAX_FILES: limit('Executable has more than64 project modules')
    if any(any(name[:i] in local for i, char in enumerate(name) if char == '/') for name in local):
        error('Executable path is both a file and a directory')
    return table


def _topological(table):
    from .modules import MAX_DEPTH
    active = []; seen = set(); result = []
    def visit(name):
        if name in active: error('Executable contains an import cycle')
        if name in seen: return
        if len(active) >= MAX_DEPTH: limit('Executable module dependency depth exceeds32')
        active.append(name)
        for target in table[name]['imports'].values():
            if target not in table: error('Unresolved executable module import')
            visit(target)
        active.pop(); seen.add(name); result.append(name)
    for name in table: visit(name)
    return result


def _standard(name):
    from .modules import STDLIB
    return {name: hashlib.sha256(STDLIB[name].encode('utf-8')).hexdigest()} if name in STDLIB else {}


def validate_objects(objects, entry=None, *, allow_empty=False):
    """Validate every unit, including orphan units, before any frontend/effect."""
    if type(objects) is list and any(type(row) is dict and row.get('schema') == OBJECT_SCHEMA_V3 for row in objects):
        return _validate_macro_objects(objects, entry, allow_empty=allow_empty)[0]
    table = _index(objects, entry, allow_empty=allow_empty)
    modules = frozenset(table); trees = {}
    for name, row in table.items():
        if set(row) != _OBJECT_KEYS_V2 or row['schema'] != OBJECT_SCHEMA_V2 or row['abi'] != EXECUTABLE_ABI_V2:
            error('Incompatible independent runtime object')
        if not _digest(row['objectHash']) or _hash({k: v for k, v in row.items() if k != 'objectHash'}) != row['objectHash']:
            error('Executable object content hash mismatch')
        required = row['requires']
        if type(required) is not dict or any(type(key) is not str or not _digest(value) for key, value in required.items()):
            error('Invalid executable content requirements')
        # Reuse the exact expanded grammar, export and relocation validator.
        # This temporary header is never published or used as executable code.
        legacy = {k: v for k, v in row.items() if k != 'requires'}
        legacy['schema'] = OBJECT_SCHEMA; legacy['abi'] = EXECUTABLE_ABI
        legacy['objectHash'] = _hash({k: v for k, v in legacy.items() if k != 'objectHash'})
        trees[name] = _object(legacy, modules)
    for name in _topological(table):
        row = table[name]
        targets = set(row['imports'].values())
        expected = {target: table[target]['objectHash'] for target in targets}
        if row['requires'] != expected: error('Executable content requirements do not match actual imports')
        standard = _standard(name)
        for target in targets: standard.update(table[target]['stdlibABI'])
        if row['stdlibABI'] != standard: error('Executable per-module standard closure mismatch')
        if name in standard and row['sourceHash'] != standard[name]: error('Executable standard-module provenance mismatch')
    return trees


def _semantic(row):
    return {key: value for key, value in row.items()
            if key not in ('schema', 'abi', 'stdlibABI', 'objectHash', 'requires')}


def _upgrade(table):
    result = {}
    for name in _topological(table):
        row = dict(table[name]); targets = set(row['imports'].values())
        row['schema'] = OBJECT_SCHEMA_V2; row['abi'] = EXECUTABLE_ABI_V2
        row['requires'] = {target: result[target]['objectHash'] for target in sorted(targets)}
        standard = _standard(name)
        for target in targets: standard.update(result[target]['stdlibABI'])
        row['stdlibABI'] = standard
        row['objectHash'] = _hash({k: v for k, v in row.items() if k != 'objectHash'})
        result[name] = row
    return result


def admit_libraries(libraries):
    """Accept flat v1 closed builds and/or v2 closures without mutating callers."""
    objects = _data(libraries)
    if type(objects) is list and any(type(row) is dict and row.get('schema') == OBJECT_SCHEMA_V3 for row in objects):
        return _admit_macro_libraries(objects)
    if type(objects) is not list: error('libraries must be a list of module objects')
    if len(objects) > MAX_OBJECTS: limit('Executable has more than96 module objects')
    groups = {}; modern = []
    for row in objects:
        if type(row) is not dict: error('Invalid library object header')
        if row.get('schema') == OBJECT_SCHEMA:
            # Old objects used the entire build standard set as ABI. Each such
            # original closed set must pass its old meaning before upgrading.
            key = _hash(row.get('stdlibABI'))
            group = groups.setdefault(key, {})
            name = _module(row.get('module'))
            if name in group and group[name] != row: error('Conflicting library module objects')
            group[name] = row
        elif row.get('schema') == OBJECT_SCHEMA_V2: modern.append(row)
        else: error('Incompatible library object schema')
    legacy = {}
    from .modules import STDLIB
    for group in groups.values():
        entries = [name for name in group if name not in STDLIB]
        if not entries: error('A v1 library must include its original closed project build')
        _validate_objects(list(group.values()), entries[0])
        for name, row in group.items():
            if name in legacy and _semantic(legacy[name]) != _semantic(row): error('Conflicting library module implementations')
            legacy[name] = row
    normalized = _upgrade(legacy)
    for row in modern:
        name = _module(row.get('module'))
        if name in normalized and normalized[name] != row: error('Conflicting library module implementations')
        normalized[name] = row
    result = list(normalized.values())
    validate_objects(result, allow_empty=True)
    return result


def library_modules(libraries):
    """Validated module names for explicit filesystem collectors; no frontend."""
    return frozenset(row['module'] for row in admit_libraries(libraries))


def compile_objects(source, files=None, entry='main.chaos', language_versions=None, *, libraries, macro_interfaces=False):
    from .modules import (bundle_files, normalize_language_versions, declarations,
        resolve_path, STDLIB, MAX_DEPTH, MAX_FILES)
    from .compilation_cache import parse_unit, DEFAULT_COMPILER_CACHE
    from .language import LanguageError
    from .macros import Expander
    if type(macro_interfaces) is not bool: error('macro_interfaces must be an exact Bool')
    supplied = admit_libraries(libraries)
    phase_enabled = macro_interfaces or any(row['schema'] == OBJECT_SCHEMA_V3 for row in supplied)
    if phase_enabled and not any(row['schema'] == OBJECT_SCHEMA_V3 for row in supplied):
        supplied = list(_promote({row['module']: row for row in supplied}).values())
    table = {row['module']: row for row in supplied}
    library_trees = validate_objects(supplied, allow_empty=True)
    phase_trees = _validate_macro_objects(supplied, allow_empty=True)[1] if phase_enabled else {}
    local_files, entry = bundle_files(source, files, entry)
    versions = normalize_language_versions(local_files, language_versions)
    if set(local_files) & set(table): error('Source module conflicts with a compiled library')
    if len(set(local_files) | {name for name in table if name not in STDLIB}) > MAX_FILES:
        limit('Executable has more than64 project modules')
    trees = {}; units = {}; exports = {}; macro_exports = {}; imports = {}; dependencies = {}
    active = []; visited = set()
    def visit(name):
        if name in table: return
        if name in active: raise LanguageError('循环导入: ' + ' -> '.join(active + [name]), code='module_error')
        if name in visited: return
        if len(active) >= MAX_DEPTH: limit('Executable module dependency depth exceeds32')
        text = STDLIB[name] if name in STDLIB else local_files.get(name)
        if text is None: raise LanguageError('模块不存在: ' + name, code='module_error')
        active.append(name)
        tree, unit = parse_unit(text, name, versions.get(name, 2), compiler_cache=DEFAULT_COMPILER_CACHE)
        trees[name] = tree; units[name] = unit
        exported = declarations(tree, include_macros=True)
        macros = {node[1] for node in tree[1] if node[0] == 'macro'}
        exports[name] = [key for key in exported if key not in macros]
        macro_exports[name] = [key for key in exported if key in macros]
        aliases = {}; relocation = {}
        for node in tree[1]:
            if node[0] == 'import':
                try: target = resolve_path(node[1], name); visit(target)
                except LanguageError as failure: raise failure.attach(getattr(node, 'span', {}))
                aliases[node[2]] = target; relocation[node[1]] = target
        imports[name] = aliases; dependencies[name] = relocation
        active.pop(); visited.add(name)
    visit(entry)
    for name in local_files: visit(name)
    if len(table) + len(trees) > MAX_OBJECTS: limit('Executable has more than96 module objects')
    all_imports = {name: {node[2]: table[name]['imports'][node[1]] for node in tree[1] if node[0] == 'import'}
                   for name, tree in library_trees.items()}
    all_imports.update(imports)
    all_macros = {row['module']: row['macroExports'] for row in supplied}; all_macros.update(macro_exports)
    class ClientExpander(Expander):
        def resolve_macro(self, name, module, node):
            parts = name.split('.')
            target = self.imports.get(module, {}).get(parts[0]) if len(parts) == 2 else None
            if target in table and parts[1] in self.macro_exports.get(target, ()) and target not in phase_trees:
                self.fail('独立编译运行时库不提供外部宏接口: ' + name, node)
            return super().resolve_macro(name, module, node)
    external = {name: {node[1]: node for node in tree[1]} for name, tree in phase_trees.items()}
    expander = ClientExpander(trees, imports=all_imports, macro_exports=all_macros, external_macros=external)
    # Avoid hygienic generated-name collisions with existing library code while
    # keeping those trees completely outside the expansion traversal.
    pending = list(library_trees.values()) + list(phase_trees.values())
    while pending:
        value = pending.pop()
        if isinstance(value, str): expander.used.add(value)
        elif isinstance(value, (tuple, list)): pending.extend(value)
    expanded = expander.expand()
    modules = frozenset(table) | frozenset(expanded)
    for name, tree in expanded.items():
        row = {'schema': OBJECT_SCHEMA_V2, 'abi': EXECUTABLE_ABI_V2, 'module': name,
            'languageVersion': versions.get(name, 2), 'sourceHash': units[name]['sourceHash'],
            'tree': encode_tree(tree, modules), 'imports': dict(dependencies[name]),
            'exports': list(exports[name]), 'macroExports': list(macro_exports[name]),
            'stdlibABI': {}, 'requires': {}}
        if phase_enabled:
            from .language import Node
            from .macro_interface import encode_phase
            definitions = Node(('block', [node for node in trees[name][1] if node[0] == 'macro']))
            row.update(schema=OBJECT_SCHEMA_V3, abi=EXECUTABLE_ABI_V3,
                phaseCapsule={'schema': PHASE_SCHEMA, 'definitions': encode_phase(definitions, modules)} if definitions[1] else None)
        table[name] = row
    # Existing supplied rows must remain exact; only new rows receive newly
    # calculated content requirements and transitive standard closures.
    for name in _topological(table):
        if name not in expanded: continue
        row = table[name]; targets = set(row['imports'].values())
        row['requires'] = {target: table[target]['objectHash'] for target in sorted(targets)}
        standard = _standard(name)
        for target in targets: standard.update(table[target]['stdlibABI'])
        row['stdlibABI'] = standard; row['objectHash'] = _hash(row)
    result = _data(list(table.values()))
    validate_objects(result, entry)
    return result


def link_objects(objects, entry='main.chaos'):
    objects = _data(objects)
    if type(objects) is not list: error('Executable objects must be a list')
    # Combining full v2 closures may repeat a shared dependency. Only exact
    # duplicate content coalesces here; image admission still rejects duplicates.
    unique = {}
    for row in objects:
        if type(row) is not dict: error('Invalid executable object header')
        name = _module(row.get('module'))
        if name in unique and unique[name] != row: error('Conflicting executable module implementations')
        unique[name] = row
    objects = list(unique.values())
    validate_objects(objects, entry)
    phase_enabled = any(row['schema'] == OBJECT_SCHEMA_V3 for row in objects)
    image = {'schema': IMAGE_SCHEMA_V3 if phase_enabled else IMAGE_SCHEMA_V2,
        'abi': EXECUTABLE_ABI_V3 if phase_enabled else EXECUTABLE_ABI_V2, 'entry': entry, 'objects': objects}
    image['imageHash'] = _hash(image)
    return image


def load_executable(image):
    image = _data(image)
    if type(image) is not dict or set(image) != _IMAGE_KEYS:
        error('Incompatible independent executable image')
    phase_enabled = image['schema'] == IMAGE_SCHEMA_V3
    if (image['schema'], image['abi']) != ((IMAGE_SCHEMA_V3, EXECUTABLE_ABI_V3) if phase_enabled else (IMAGE_SCHEMA_V2, EXECUTABLE_ABI_V2)):
        error('Incompatible independent executable image')
    if not _digest(image['imageHash']) or _hash({k: v for k, v in image.items() if k != 'imageHash'}) != image['imageHash']:
        error('Executable image content hash mismatch')
    if phase_enabled:
        trees = _validate_macro_objects(image['objects'], image['entry'])[0]
    else:
        if type(image['objects']) is list and any(type(row) is dict and row.get('schema') != OBJECT_SCHEMA_V2 for row in image['objects']):
            error('V2 image requires V2 objects')
        trees = validate_objects(image['objects'], image['entry'])
    from .executable_image import _bundle_from_trees
    return _bundle_from_trees(image, trees)


def _promote(table):
    """Metadata-only legacy promotion; no phase definitions are invented."""
    result = {}
    for name in _topological(table):
        row = dict(table[name]); targets = set(row['imports'].values())
        row.update(schema=OBJECT_SCHEMA_V3, abi=EXECUTABLE_ABI_V3, phaseCapsule=None)
        row['requires'] = {target: result[target]['objectHash'] for target in sorted(targets)}
        row['objectHash'] = _hash({k: v for k, v in row.items() if k != 'objectHash'})
        result[name] = row
    return result


def _admit_macro_libraries(objects):
    legacy = [row for row in objects if type(row) is not dict or row.get('schema') != OBJECT_SCHEMA_V3]
    # Complete old closures retain their original validation before promotion.
    normalized = _promote({row['module']: row for row in admit_libraries(legacy)})
    for row in objects:
        if type(row) is not dict or row.get('schema') != OBJECT_SCHEMA_V3: continue
        name = _module(row.get('module'))
        if name in normalized and normalized[name] != row: error('Conflicting library module implementations')
        normalized[name] = row
    result = list(normalized.values())
    _validate_macro_objects(result, allow_empty=True)
    return result


def _validate_macro_objects(objects, entry=None, *, allow_empty=False):
    """Strict phase and runtime data validation; never invokes the Expander."""
    table = _index(objects, entry, allow_empty=allow_empty)
    modules = frozenset(table); trees = {}; phases = {}
    from .modules import declarations
    from .language import Node, LanguageError, ResourceLimit
    from .macro_interface import decode_phase
    for name, row in table.items():
        if set(row) != _OBJECT_KEYS_V3 or row['schema'] != OBJECT_SCHEMA_V3 or row['abi'] != EXECUTABLE_ABI_V3:
            error('Incompatible macro module object')
        if not _digest(row['objectHash']) or _hash({k: v for k, v in row.items() if k != 'objectHash'}) != row['objectHash']:
            error('Executable object content hash mismatch')
        required = row['requires']
        if type(required) is not dict or any(type(key) is not str or not _digest(value) for key, value in required.items()):
            error('Invalid executable content requirements')
        runtime = {k: v for k, v in row.items() if k not in ('requires', 'phaseCapsule')}
        runtime.update(schema=OBJECT_SCHEMA, abi=EXECUTABLE_ABI)
        runtime['objectHash'] = _hash({k: v for k, v in runtime.items() if k != 'objectHash'})
        trees[name] = _object(runtime, modules)
        capsule = row['phaseCapsule']
        if capsule is not None:
            if type(capsule) is not dict or set(capsule) != {'schema', 'definitions'} or capsule['schema'] != PHASE_SCHEMA:
                error('Invalid macro phase capsule')
            phase = decode_phase(capsule['definitions'], modules)
            names = {node[1] for node in phase[1]}
            if set(row['macroExports']) - names: error('Exported macro has no phase definition')
            # Preserve original module namespace/export collision checks while
            # keeping phase syntax completely outside the runtime tree.
            try: declarations(Node(('block', list(trees[name][1]) + list(phase[1]))), include_macros=True)
            except ResourceLimit: raise
            except LanguageError as failure: error('Invalid macro declarations: ' + failure.message)
            phases[name] = phase
    for name in _topological(table):
        row = table[name]; targets = set(row['imports'].values())
        if row['requires'] != {target: table[target]['objectHash'] for target in targets}:
            error('Executable content requirements do not match actual imports')
        standard = _standard(name)
        for target in targets: standard.update(table[target]['stdlibABI'])
        if row['stdlibABI'] != standard: error('Executable per-module standard closure mismatch')
        if name in standard and row['sourceHash'] != standard[name]: error('Executable standard-module provenance mismatch')
    return trees, phases
