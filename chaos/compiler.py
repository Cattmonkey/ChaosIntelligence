"""Content-addressed front-end and portable specialization reports.

The report never executes a program or grants permission to reorder effects.
Raw parsing may be reused. Linking and hygienic macro expansion always run.
"""
import hashlib
import json

from .compilation_cache import COMPILER_ABI, DEFAULT_COMPILER_CACHE
from .language import Node, ResourceLimit
from .modules import ProgramBundle
from .version import __version__

SCHEMA = 'chaos.compilation/1'
TARGET = 'portable-ast'

# These are grammar kinds, not names of shadowable functions or prompt words.
# Missing/new grammar is explicitly unknown, never silently treated as pure.
_EFFECTS = {
    'literal': (), 'name': ('read', 'raise'),
    'block': ('control',), 'do': ('control',),
    'let': ('binding', 'raise'), 'var': ('binding', 'state', 'raise'),
    'let_pattern': ('binding', 'raise'), 'var_pattern': ('binding', 'state', 'raise'),
    'def': ('binding', 'raise'), 'rec': ('binding', 'raise'),
    'assign': ('state', 'raise'), 'assign_pattern': ('state', 'raise'),
    'set_member': ('state', 'protocol', 'raise'), 'set_item': ('state', 'protocol', 'raise'),
    'aug_assign': ('state', 'protocol', 'raise'),
    'call': ('call', 'unknown', 'raise'),
    'binary': ('protocol', 'raise'), 'unary': ('protocol', 'raise'),
    'index': ('protocol', 'raise'), 'subscript': ('protocol', 'raise'),
    'list': ('allocation',), 'tuple': ('allocation',), 'record': ('allocation',),
    'set': ('allocation', 'protocol', 'raise'), 'slice': ('allocation', 'raise'),
    'spread': ('protocol', 'raise'), 'kwspread': ('raise',), 'keyword': (),
    'if': ('control', 'raise'), 'match': ('control', 'raise'),
    'while': ('control', 'raise'), 'for': ('control', 'protocol', 'raise'),
    'for_pattern': ('control', 'protocol', 'raise'),
    'async_for': ('control', 'protocol', 'suspend', 'raise'),
    'async_for_pattern': ('control', 'protocol', 'suspend', 'raise'),
    'loop_else': ('control',), 'try_else': ('control',),
    'try': ('control',), 'try_cases': ('control',), 'finally': ('control',),
    'try_group': ('control', 'allocation', 'raise'), 'group_case': ('control', 'binding', 'raise'),
    'throw': ('control', 'raise'), 'rethrow': ('control', 'raise'),
    'return': ('control',), 'break': ('control',), 'continue': ('control',),
    'fn': ('allocation', 'raise'), 'gen_fn': ('allocation', 'raise'),
    'async_fn': ('allocation', 'raise'), 'async_gen_fn': ('allocation', 'raise'),
    'await': ('suspend', 'raise'), 'yield': ('suspend',),
    'yield_from': ('suspend', 'protocol', 'raise'),
    'with': ('resource', 'protocol', 'raise'), 'with_pattern': ('resource', 'protocol', 'raise'),
    'async_with': ('resource', 'protocol', 'suspend', 'raise'),
    'async_with_pattern': ('resource', 'protocol', 'suspend', 'raise'),
    'class': ('binding', 'allocation', 'protocol', 'raise'),
    'decorated': ('call', 'unknown', 'raise'), 'agent': ('binding', 'allocation', 'raise'),
    'data': ('binding', 'allocation', 'raise'),
    'import': ('module', 'raise'), 'export': (),
    'comp_for': ('control', 'protocol', 'raise'),
    'comp_for_pattern': ('control', 'protocol', 'raise'),
    'comp_async_for': ('control', 'protocol', 'suspend', 'raise'),
    'comp_async_for_pattern': ('control', 'protocol', 'suspend', 'raise'),
    'comp_if': ('control', 'raise'),
    'list_comp': ('allocation', 'control', 'protocol', 'raise'),
    'record_comp': ('allocation', 'control', 'protocol', 'raise'),
    'set_comp': ('allocation', 'control', 'protocol', 'raise'),
    'gen_comp': ('allocation', 'control', 'protocol', 'suspend', 'raise'),
    'async_gen_comp': ('allocation', 'control', 'protocol', 'suspend', 'raise'),
}
_FUNCTIONS = frozenset(('fn', 'gen_fn', 'async_fn', 'async_gen_fn'))


def _hash(value):
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _analysis(tree, checkpoint=None):
    """Potential structural effects, including both branches; no call inference.

    Function defaults are immediate; only their body is latent. A latent body's
    defaults stay latent. Lazy comprehensions are deliberately over-approximated.
    Metadata/hygiene aliases are not executable children; calls stay unknown.
    """
    todo = [(tree, False)]; effects = set(); latent = set(); kinds = {}; count = 0
    visits = 0
    while todo:
        item, deferred = todo.pop(); visits += 1
        if visits > 400000: raise ResourceLimit('编译分析结构超过 400000 项')
        if checkpoint: checkpoint()
        if isinstance(item, Node):
            count += 1; kind = item[0]; kinds[kind] = kinds.get(kind, 0) + 1
            (latent if deferred else effects).update(_EFFECTS.get(kind, ('unknown', 'raise')))
            if kind == 'binary' and item[1] == '|>':
                (latent if deferred else effects).update(('call', 'unknown'))
            if kind == 'binary' and item[1] in ('&&', '||'):
                (latent if deferred else effects).add('control')
            for index in range(len(item) - 1, 0, -1):
                todo.append((item[index], deferred or (kind in _FUNCTIONS and index == 3)))
        elif type(item) in (tuple, list):
            todo.extend((child, deferred) for child in reversed(item))
        elif type(item) is dict:
            todo.extend((child, deferred) for child in reversed(list(item.values())))
    return {'nodeCount': count, 'effects': sorted(effects), 'latentEffects': sorted(latent),
            'kinds': dict(sorted(kinds.items())), 'conservative': True}


def compile_bundle(bundle, *, checkpoint=None):
    """Inspect a validated bundle; does not link or expand it a second time."""
    if type(bundle) is not ProgramBundle: raise TypeError('compile_bundle requires ProgramBundle')
    rows = []; identities = {}; total = 0; effects = set(); latent = set()
    # ProgramBundle is a snapshot, but its public AST remains mutable for legacy
    # SDK use. Analysis is recomputed and never cached by these source identities.
    def dependency_hash(name):
        if name not in identities:
            dependencies = bundle.dependencies[name]
            identities[name] = _hash({'unitId': bundle.compilation_units[name]['unitId'],
                'dependencies': {key: dependency_hash(value) for key, value in sorted(dependencies.items())}})
        return identities[name]
    for name in sorted(bundle.trees):
        if checkpoint: checkpoint()
        unit = bundle.compilation_units[name]
        analysis = _analysis(bundle.expanded_trees[name], checkpoint)
        total += analysis['nodeCount']; effects.update(analysis['effects']); latent.update(analysis['latentEffects'])
        rows.append({'file': name, **unit, 'dependencyHash': dependency_hash(name),
                     'dependencies': sorted(set(bundle.dependencies[name].values())),
                     'exports': sorted(bundle.exports[name]), 'macroExports': sorted(bundle.macro_exports[name]),
                     'analysis': analysis})
    compiler = {'version': __version__, 'abi': COMPILER_ABI}
    from .portable_optimizer import build_plan
    specialization = build_plan(bundle.expanded_trees, checkpoint=checkpoint).info()
    # Whole-bundle identity includes every orphan and the transitive stdlib text;
    # it is distinct from the original Program definitionHash and no authority.
    identity = {'compiler': compiler, 'target': TARGET, 'entry': bundle.entry,
                'literalOptimizer': specialization['algorithm'],
                'units': {row['file']: row['unitId'] for row in rows},
                'languageVersions': dict(bundle.language_versions)}
    statuses = [row['cacheStatus'] for row in rows]
    return {'schema': SCHEMA, 'compiler': compiler, 'target': TARGET,
            'artifactHash': _hash(identity), 'entry': bundle.entry,
            'languageVersions': dict(bundle.language_versions), 'modules': rows,
            'summary': {'moduleCount': len(rows), 'nodeCount': total,
                        'cacheHits': statuses.count('hit'), 'cacheMisses': statuses.count('miss'),
                        'cacheBypasses': sum(status not in ('hit', 'miss') for status in statuses),
                        'effects': sorted(effects), 'latentEffects': sorted(latent)},
            'passes': [{'name': 'incremental-parse', 'status': 'enabled'},
                       {'name': 'module-link', 'status': 'validated'},
                       {'name': 'hygienic-macro-expansion', 'status': 'complete'},
                       {'name': 'structural-effect-analysis', 'status': 'conservative'},
                       {'name': 'literal-operation-specialization', 'status': 'enabled', **specialization},
                       {'name': 'instruction-reordering', 'status': 'not-implemented'}],
            'limitations': ['Effect analysis is structural and over-approximates; calls remain unknown.',
                            'All modules are linked and macros expanded on every compilation.',
                            'Checkpoint compilation bypasses parsing cache to preserve budgets.',
                            'Guarded literal and immutable lexical integer specialization retains every original name read, node/frame and operator checkpoint; candidateSites are static opportunities, not executed operations or performance measurements.',
                            'This portable-ast report does not emit native code; compile_function separately offers optional guarded scalar regions and resumable PC control flow. Selected control, protection, resource and pattern bodies keep their original runtime protocols, atomic binding and ownership. GPU numerical Tasks use optional device kernels; opt-in gpu_map Metal compilation generates a bounded pure arithmetic kernel in an isolated worker, not from this portable report. No AOT, custom GC, or program-level reordering is implemented.']}


def compile_program(source, files=None, entry='main.chaos', *, language_versions=None,
                    checkpoint=None, compiler_cache=DEFAULT_COMPILER_CACHE):
    """Compile a closed source snapshot and return a bounded, non-executable report."""
    bundle = ProgramBundle(source, files, entry, language_versions=language_versions,
                           checkpoint=checkpoint, compiler_cache=compiler_cache)
    return compile_bundle(bundle, checkpoint=checkpoint)
