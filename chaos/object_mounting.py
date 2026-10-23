"""Pure namespace relocation of already validated executable module closures.

Only declared module identities and Node provenance are relocated. The tagged
syntax graph, reference numbering, hygiene aliases, captured constructors and
ordinary language strings remain the original executable data.
"""
from .executable_ast import error


def _prefix(value):
    from .modules import path_name
    from .language import LanguageError
    if type(value) is not str:
        error('Object mount prefix must be an exact String')
    try:
        value.encode('utf-8')
        normalized = path_name(value)
    except (UnicodeError, LanguageError):
        error('Invalid object mount prefix')
    if normalized != value or value == 'std' or value.startswith('std/'):
        error('Object mount prefix must be a canonical nonstandard namespace')
    return value


def _span(span, names):
    # Admission has already validated every field and bounded this recursion.
    if 'file' in span:
        span['file'] = names[span['file']]
    origin = span.get('macroOrigin')
    if origin is not None:
        origin['definitionModule'] = names[origin['definitionModule']]
        _span(origin['definition'], names)
        _span(origin['origin'], names)


def _tree(tree, names):
    """Relocate metadata without decoding/re-encoding the syntax DAG."""
    pending = [tree]
    while pending:
        row = pending.pop()
        tag = row[0]
        if tag not in ('node', 'tuple', 'list'):
            continue
        pending.extend(row[1])
        if tag != 'node':
            continue
        metadata = row[2]
        if 'span' in metadata:
            _span(metadata['span'], names)
        if 'macro_module' in metadata:
            metadata['macro_module'] = names[metadata['macro_module']]


def _identity_provenance(tree):
    """Import resolution and data type identity must have relocatable sites."""
    from .language import Node
    from .macros import _children
    pending = [tree]; seen = set()
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if node[0] in ('data', 'import') and (type(node) is not Node
                or 'file' not in getattr(node, 'span', {})):
            error('Mounted data/import syntax requires explicit module provenance')
        if node[0] == 'macro':
            pending.append(node[3])
        else:
            _children(node, lambda child: pending.append(child))


def mount_objects(objects, prefix):
    """Mount a complete v1/v2/v3 library under a logical module namespace.

    Legacy v1 builds first retain their original admission and normalize to v2.
    Standard objects remain exact after that normalization. No source frontend,
    macro expansion, module initialization or language execution occurs here.
    """
    from .executable_image import _data, _hash, _module
    from .separate_compilation import (admit_libraries, validate_objects,
        _validate_macro_objects, OBJECT_SCHEMA_V3, _topological)
    from .modules import STDLIB

    # This copy/admission validates the entire closure, including unused units,
    # before inspecting executable metadata or constructing any relocated row.
    rows = admit_libraries(objects)
    prefix = _prefix(prefix)
    if any(row['schema'] == OBJECT_SCHEMA_V3 for row in rows):
        trees, phases = _validate_macro_objects(rows, allow_empty=True)
    else:
        trees = validate_objects(rows, allow_empty=True); phases = {}
    names = {row['module']: row['module'] if row['module'] in STDLIB
             else _module(prefix + '/' + row['module']) for row in rows}
    relocated = {}
    for row in rows:
        original = row['module']
        standard = original in STDLIB
        if standard and any(target not in STDLIB for target in row['imports'].values()):
            error('A standard object cannot depend on a mounted project module')
        if standard:
            # Provenance can reference modules beyond direct import edges. A
            # standard row requiring relocation cannot be both exact and valid.
            probe = _data(row)
            _tree(probe['tree'], names)
            if probe.get('phaseCapsule') is not None:
                _tree(probe['phaseCapsule']['definitions'], names)
            if probe != row:
                error('A standard object cannot contain mounted project provenance')
        else:
            _identity_provenance(trees[original])
            if original in phases:
                _identity_provenance(phases[original])
            row['module'] = names[original]
            row['imports'] = {specifier: names[target]
                              for specifier, target in row['imports'].items()}
            _tree(row['tree'], names)
            if row.get('phaseCapsule') is not None:
                _tree(row['phaseCapsule']['definitions'], names)
        relocated[names[original]] = row

    for name in _topological(relocated):
        row = relocated[name]
        if name in STDLIB:
            continue
        targets = set(row['imports'].values())
        row['requires'] = {target: relocated[target]['objectHash']
                           for target in sorted(targets)}
        row['objectHash'] = _hash({key: value for key, value in row.items()
                                   if key != 'objectHash'})
    result = _data(list(relocated.values()))
    validate_objects(result, allow_empty=True)
    return result
