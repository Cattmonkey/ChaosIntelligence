"""Strict, bounded codec for portable expanded syntax, never live runtime values.

The decoded syntax is executable program input, not a claim that an accompanying
source digest proves equivalence. All authority still belongs to the evaluator.
"""
import math
import re
from types import SimpleNamespace

MAX_AST_VALUES = 400000
MAX_AST_DEPTH = 128
MAX_AST_STRING = 1000000
_PHASE = frozenset(('macro', 'macrocall', 'quote', 'unquote'))
_STATEMENTS = frozenset(('decorated', 'class', 'let', 'var', 'let_pattern',
    'var_pattern', 'assign', 'assign_pattern', 'aug_assign', 'set_member',
    'set_item', 'def', 'agent', 'rec', 'data', 'import', 'export'))
_METADATA = frozenset(('span', 'macro_bindings', 'macro_module', 'numeric_capture', 'condition_capture'))


def error(message):
    from .language import LanguageError
    raise LanguageError(message, code='executable_error')


def limit(message='Executable syntax exceeds its bounded resource limit'):
    from .language import ResourceLimit
    raise ResourceLimit(message)


def _string(value):
    if type(value) is not str: error('Executable syntax requires an exact String')
    if len(value) > MAX_AST_STRING: limit()
    try: value.encode('utf-8')
    except UnicodeError: error('Executable syntax requires valid Unicode')
    return value


def _span(value, modules, depth=0):
    if depth > 32: limit('Executable macro provenance is too deep')
    if type(value) is not dict or set(value) - {'file', 'line', 'column', 'endLine', 'endColumn', 'macroOrigin'}:
        error('Invalid executable source position')
    result = {}
    for key, item in value.items():
        if key == 'file':
            if _string(item) not in modules: error('Source position refers outside the object closure')
            result[key] = item
        elif key == 'macroOrigin':
            if type(item) is not dict or set(item) != {'macro', 'definition', 'origin', 'expansion', 'definitionModule'}:
                error('Invalid executable macro provenance')
            if type(item['expansion']) is not list or len(item['expansion']) > 1000 or any(type(x) is not str for x in item['expansion']):
                error('Invalid executable macro expansion chain')
            if _string(item['definitionModule']) not in modules: error('Macro definition is outside the object closure')
            result[key] = {'macro': _string(item['macro']),
                'definition': _span(item['definition'], modules, depth + 1),
                'origin': _span(item['origin'], modules, depth + 1),
                'expansion': [_string(x) for x in item['expansion']],
                'definitionModule': item['definitionModule']}
        else:
            if type(item) is not int or not 1 <= item <= 100000000: error('Invalid executable source coordinate')
            result[key] = item
    if all(key in result for key in ('line', 'column', 'endLine', 'endColumn')):
        if (result['endLine'], result['endColumn']) < (result['line'], result['column']): error('Reversed executable source range')
    return result


def _metadata(value, modules, node=None, decoding=False):
    if type(value) is not dict or set(value) - _METADATA: error('Unknown executable Node metadata')
    result = {}
    for key, item in value.items():
        if key == 'span': result[key] = _span(item, modules)
        elif key == 'macro_module':
            if _string(item) not in modules: error('Macro module is outside the object closure')
            result[key] = item
        elif key == 'macro_bindings':
            if type(item) is not dict or len(item) > 10000 or any(type(k) is not str or type(v) is not str or not k.isidentifier() or not v.isidentifier() for k, v in item.items()):
                error('Invalid executable hygiene aliases')
            if node is not None:
                from .language import Parser
                if not node or node[0] not in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn') or len(node) != 5 or type(node[1]) is not list:
                    error('Hygiene parameter aliases require a function')
                rows = node[1]
                if any(type(row) not in (tuple, list) or len(row) not in (2, 4, 5) or type(row[0]) is not str for row in rows):
                    error('Invalid hygiene parameter rows')
                from .patterns import parameter_names
                names = set(parameter_names(rows)); slots = {row[0] for row in rows}
                if set(item) - slots or any(value in Parser.reserved or value in names for value in item.values()) or len(set(item.values())) != len(item):
                    error('Hygiene aliases collide with parameter bindings')
            result[key] = dict(item)
        elif key == 'numeric_capture':
            from .numeric_values import captured_constructor, capture_constructor
            name = item if decoding else captured_constructor(item)
            if name not in ('fraction', 'complex', 'decimal') or node is not None and tuple(node) != ('name', name): error('Invalid intrinsic numeric capture')
            result[key] = capture_constructor(name) if decoding else name
        else:
            from .condition_values import captured_constructor, capture_constructor
            if (item != 'condition' if decoding else not captured_constructor(item)) or node is not None and tuple(node) != ('name', 'condition'):
                error('Invalid intrinsic condition capture')
            result[key] = capture_constructor() if decoding else 'condition'
    return result


def encode_tree(tree, modules):
    from .language import Node
    memo = {}; active = set(); count = [0]
    def visit(item, depth):
        count[0] += 1
        if count[0] > MAX_AST_VALUES or depth > MAX_AST_DEPTH: limit()
        kind = type(item)
        if item is None: return ['null']
        if kind is bool: return ['bool', item]
        if kind is str: return ['str', _string(item)]
        if kind is int:
            if item.bit_length() > 14000: limit('Executable integer exceeds 14000 bits')
            return ['int', hex(item)]
        if kind is float:
            if not math.isfinite(item): error('Executable numbers must be finite')
            return ['float', item.hex()]
        if kind not in (Node, tuple, list): error('Executable syntax cannot contain host or live language values')
        identity = id(item)
        if identity in active: error('Executable syntax cannot contain cycles')
        if identity in memo: return ['ref', memo[identity]]
        memo[identity] = len(memo); active.add(identity)
        children = [visit(child, depth + 1) for child in item]
        active.remove(identity)
        if kind is Node:
            return ['node', children, _metadata(vars(item), modules, item)]
        return ['tuple' if kind is tuple else 'list', children]
    return visit(tree, 0)


def decode_tree(data, modules):
    return _decode_tree(data, modules, validate_tree)


def _decode_tree(data, modules, validator):
    from .language import Node
    memo = []; count = [0]
    def visit(row, depth):
        count[0] += 1
        if count[0] > MAX_AST_VALUES or depth > MAX_AST_DEPTH: limit()
        if type(row) is not list or not row or type(row[0]) is not str: error('Invalid tagged executable syntax')
        tag = row[0]
        if tag == 'null' and len(row) == 1: return None
        if tag == 'bool' and len(row) == 2 and type(row[1]) is bool: return row[1]
        if tag == 'str' and len(row) == 2: return _string(row[1])
        if tag == 'int' and len(row) == 2:
            text = _string(row[1])
            if len(text) > 3504: limit('Executable integer exceeds 14000 bits')
            if re.fullmatch(r'-?0x(?:0|[1-9a-f][0-9a-f]*)', text) is None: error('Invalid canonical executable integer')
            value = int(text, 16)
            if hex(value) != text: error('Noncanonical executable integer')
            if value.bit_length() > 14000: limit()
            return value
        if tag == 'float' and len(row) == 2:
            text = _string(row[1])
            if len(text) > 40: error('Invalid canonical executable Number')
            try: value = float.fromhex(text)
            except (ValueError, OverflowError): error('Invalid executable Number')
            if not math.isfinite(value) or value.hex() != text: error('Invalid canonical executable Number')
            return value
        if tag == 'ref' and len(row) == 2:
            index = row[1]
            if type(index) is not int or not 0 <= index < len(memo) or memo[index] is None: error('Executable reference is forward, cyclic or outside the graph')
            return memo[index]
        if tag not in ('node', 'tuple', 'list') or len(row) != (3 if tag == 'node' else 2) or type(row[1]) is not list:
            error('Invalid executable container')
        index = len(memo); memo.append(None)
        children = [visit(child, depth + 1) for child in row[1]]
        value = Node(children) if tag == 'node' else tuple(children) if tag == 'tuple' else children
        if tag == 'node': value.__dict__.update(_metadata(row[2], modules, value, True))
        memo[index] = value
        return value
    try: result = visit(data, 0)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as failure:
        error('Malformed executable syntax: ' + type(failure).__name__)
    except Exception as failure:
        from .language import LanguageError, ResourceLimit
        if not isinstance(failure, LanguageError) or isinstance(failure, ResourceLimit) or failure.code == 'executable_error': raise
        error('Malformed executable metadata: ' + failure.message)
    validator(result)
    return result


def validate_tree(tree):
    return _validate_grammar(tree)


def _validate_grammar(tree, *, phase=False):
    """Grammar shapes plus original expanded-scope rules; no render or parser.

    SHAPES describes field structure only. The context pass below and original
    macro scope validator close the grammatical-context checks that a parser
    previously supplied. They do not expand or evaluate a macro.
    """
    from .language import Node, Parser, TYPES, LanguageError
    from .program import SHAPES
    from .patterns import pattern_names
    from .parameters import validate_parameters
    from .operators import PRECEDENCE, AUGMENTED
    count = [0]
    def charge():
        count[0] += 1
        if count[0] > MAX_AST_VALUES: limit()
    def ident(value, qualified=False):
        if type(value) is not str or not value or not all(part.isidentifier() and part not in Parser.reserved for part in (value.split('.') if qualified else [value])):
            error('Invalid executable identifier')
    def seq(value, length=None):
        if type(value) not in (tuple, list) or length is not None and len(value) != length: error('Invalid executable syntax row')
    def visit(node, depth=0, role='expression', module=False, quote_depth=0):
        charge()
        if depth > MAX_AST_DEPTH: limit()
        if type(node) not in (Node, tuple) or not node or type(node[0]) is not str or node[0] not in SHAPES or (node[0] in _PHASE and not phase):
            error('Unknown or unexpanded executable syntax')
        kind = node[0]; fields = SHAPES[kind]
        if phase:
            if type(node) is Node and (set(vars(node)) - {'span'} or 'macroOrigin' in getattr(node, 'span', {})):
                error('Raw macro interfaces cannot contain generated metadata')
            if kind == 'macro' and not (module and role == 'statement' and depth == 1):
                error('Macro declarations require the phase module top level')
            if kind == 'unquote' and not quote_depth:
                error('Unquote requires a quote context')
        if kind in _STATEMENTS and role != 'statement': error('Declaration or assignment outside an executable statement position')
        if len(node) != len(fields) + 1 and not (kind == 'class' and len(node) == 4): error('Invalid executable node arity')
        if kind.startswith('p_') and role != 'pattern': error('Pattern outside a pattern position')
        if role == 'pattern' and not kind.startswith('p_'): error('Expression outside an executable expression position')
        if kind in ('import', 'export', 'data') and not module: error('Module declaration outside module top level')
        if kind == 'group_case' and role != 'group_case': error('Group case outside a group handler')
        if kind in ('keyword', 'kwspread', 'spread') and role != 'argument' and not (kind == 'spread' and role == 'setitem'): error('Argument marker outside argument position')
        if kind == 'slice' and role != 'slice': error('Slice outside direct item-key position')
        if kind.startswith('comp_') and role != 'clause': error('Comprehension clause outside its comprehension')
        for index, (field, typ) in enumerate(fields, 1):
            if index == len(node): break
            item = node[index]
            child_role = 'expression'
            if kind in ('block', 'rec') and typ == 'nodes' or kind == 'decorated' and field == 'declaration': child_role = 'statement'
            if field == 'pattern' or kind.startswith('p_') and typ in ('node', 'optional', 'nodes', 'pairs'): child_role = 'pattern'
            if kind == 'call' and field == 'arguments' or kind == 'class' and field == 'options': child_role = 'argument'
            if kind == 'set' and field == 'items': child_role = 'setitem'
            if field == 'clauses': child_role = 'clause'
            if kind == 'try_group' and field == 'cases': child_role = 'group_case'
            if kind in ('subscript', 'set_item') and field == 'index': child_role = 'key'
            if kind == 'tuple' and role == 'key' and field == 'items': child_role = 'slice'
            def child(value, child_role=child_role):
                if type(value) not in (Node, tuple) or not value or type(value[0]) is not str:
                    error('Executable child must be an actual syntax node')
                actual = 'slice' if child_role == 'key' and value[0] == 'slice' else 'key' if child_role == 'key' and value[0] == 'tuple' else 'expression' if child_role in ('key', 'slice') and value[0] != 'slice' else child_role
                visit(value, depth + 1, actual, kind == 'block' and module and depth == 0, quote_depth + (kind == 'quote'))
            if typ == 'str': _string(item)
            elif typ == 'literal':
                if item is not None and type(item) not in (str, bool, int, float): error('Invalid executable literal')
            elif typ == 'node': child(item)
            elif typ == 'optional':
                if item is not None: child(item)
            else:
                if type(item) is not list or len(item) > 10000: error('Invalid executable syntax sequence')
                if typ == 'nodes':
                    for value in item: child(value)
                elif typ == 'strings':
                    for value in item: ident(value)
                elif typ == 'params':
                    validate_parameters(item)
                    for row in item:
                        if len(row) >= 4 and row[3] is not None: visit(row[3], depth + 1, quote_depth=quote_depth)
                        if len(row) == 5 and row[4] is not None: visit(row[4], depth + 1, 'pattern', quote_depth=quote_depth)
                elif typ == 'pairs':
                    keys = []
                    for row in item:
                        seq(row, 2); _string(row[0]); keys.append(row[0]); child(row[1])
                    if kind.startswith('p_') and len(keys) != len(set(keys)): error('Duplicate executable pattern fields')
                elif typ == 'variants':
                    labels = set()
                    for row in item:
                        seq(row, 2); ident(row[0]); seq(row[1])
                        if row[0] in labels or row[0] == node[1]: error('Duplicate executable data constructor')
                        labels.add(row[0])
                        for value in row[1]: ident(value)
                        if len(row[1]) != len(set(row[1])): error('Duplicate executable constructor parameters')
                    if not item: error('Empty executable data declaration')
                elif typ == 'arms':
                    if not item: error('Empty executable match/catch arms')
                    for row in item:
                        seq(row, 3); visit(row[0], depth + 1, 'pattern', quote_depth=quote_depth)
                        if row[1] is not None: visit(row[1], depth + 1, quote_depth=quote_depth)
                        visit(row[2], depth + 1, quote_depth=quote_depth)
        if phase:
            if kind == 'macro':
                ident(node[1])
                if len(set(node[2])) != len(node[2]) or node[3][0] != 'block': error('Invalid macro declaration')
            if kind == 'macrocall':
                ident(node[1], True)
                if len(node[1].split('.')) > 2: error('Macro call requires name or module.name')
            if kind == 'quote' and node[1][0] != 'block': error('Quote requires a block')
            if kind in ('loop_else', 'try_else'):
                allowed = ('while','for','async_for','for_pattern','async_for_pattern') if kind == 'loop_else' else ('try','try_cases','try_group')
                if node[1][0] not in allowed or node[2][0] != 'block': error('Invalid macro control branch')
            if kind in ('try_cases', 'try_group'):
                from .language import validate_try_cases, validate_try_group
                (validate_try_cases if kind == 'try_cases' else validate_try_group)(node, tick=charge)
            if kind == 'aug_assign' and node[1][0] not in (('name','index','subscript','unquote') if quote_depth else ('name','index','subscript')):
                error('Invalid macro assignment target')
            if kind in ('assign_pattern','with_pattern','async_with_pattern') and node[1][0] not in ('p_list','p_tuple','p_record','p_tag','p_class'):
                error('Invalid macro structural target')
            if kind in ('list_comp','set_comp','record_comp','gen_comp','async_gen_comp'):
                from .language import comprehension_scopes
                if not quote_depth: comprehension_scopes(node, True)
                else:
                    clauses = node[-1]
                    for_kinds = ('comp_for','comp_async_for','comp_for_pattern','comp_async_for_pattern')
                    if not clauses or len(clauses) > 64 or clauses[0][0] not in for_kinds or any(part[0] not in for_kinds + ('comp_if',) for part in clauses):
                        error('Macro comprehension requires1–64 clauses starting with for')
            if kind == 'set' and not node[1]: error('Empty set syntax requires a spread')
            if kind == 'decorated' and node[2][0] not in ('def','class','agent'): error('Invalid decorated declaration')
            if kind == 'class':
                from .class_creation import class_options
                labels = set()
                for option in class_options(node):
                    if option[0] not in ('keyword','kwspread'): error('Invalid class option')
                    if option[0] == 'keyword':
                        if option[1] in labels: error('Duplicate class option')
                        labels.add(option[1])
        if kind == 'if' and (node[2][0] != 'block' or node[3][0] not in ('block', 'if')): error('Executable if requires block branches')
        if kind == 'try' and (node[1][0] != 'block' or node[3][0] != 'block'): error('Executable try requires block bodies')
        if kind == 'do' and node[1][0] != 'block': error('Executable do requires a block')
        if kind == 'agent' and node[2][0] != 'record': error('Executable Agent config requires a record')
        if kind == 'finally' and (node[1][0] not in ('block', 'try', 'try_cases', 'try_group', 'try_else') or node[2][0] != 'block'): error('Invalid executable finally body')
        if kind == 'decorated' and not 1 <= len(node[1]) <= 64: error('Executable decorators require1–64 entries')
        if kind == 'call':
            labels = set(); named = False
            for argument in node[2]:
                if argument[0] in ('keyword', 'kwspread'):
                    named = True
                    if argument[0] == 'keyword':
                        if argument[1] in labels: error('Duplicate executable keyword argument')
                        labels.add(argument[1])
                elif named: error('Executable positional arguments must precede keyword arguments')
        if kind in ('let_pattern', 'var_pattern', 'for_pattern', 'async_for_pattern', 'comp_for_pattern', 'comp_async_for_pattern') and node[1][0] in ('p_bind', 'p_any'): error('Simple executable targets require original name syntax')
        if kind in ('comp_for', 'comp_async_for'): ident(node[1])
        if kind == 'import': ident(node[2])
        if kind == 'try': ident(node[2])
        if kind in ('name', 'assign', 'let', 'var', 'def', 'agent', 'class', 'data', 'p_bind', 'for', 'async_for', 'with', 'async_with'): ident(node[1])
        if kind in ('let', 'var', 'let_pattern', 'var_pattern'): ident(node[2], True)
        if kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
            ident(node[2], True)
            if type(node[4]) is not str or node[3][0] != 'block': error('Invalid executable function')
        if kind == 'binary' and node[1] not in PRECEDENCE or kind == 'unary' and node[1] not in ('+', '-', '!', '~'): error('Unknown executable operator')
        if kind == 'aug_assign' and node[2] not in AUGMENTED.values(): error('Unknown executable assignment operator')
        if kind == 'def' and node[2][0] not in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'): error('Executable declaration requires a function')
        if kind == 'rec' and (not node[1] or any(value[0] != 'def' for value in node[1]) or len({value[1] for value in node[1]}) != len(node[1])): error('Invalid executable recursive declarations')
        if kind == 'class':
            if node[1] in TYPES or node[3][0] != 'block' or any(value[0] not in ('let', 'var', 'def') and not (value[0] == 'decorated' and value[2][0] == 'def') for value in node[3][1]): error('Invalid executable class body')
        if kind == 'data' and node[1] in TYPES: error('Executable declaration shadows a builtin type')
        if kind in ('with', 'async_with', 'with_pattern', 'async_with_pattern') and node[3][0] != 'block': error('Executable resource body must be a block')
        if kind in ('while',) and node[2][0] != 'block' or kind in ('for', 'async_for', 'for_pattern', 'async_for_pattern') and node[3][0] != 'block': error('Executable loop body must be a block')
        if kind.startswith('p_'): pattern_names(node)
    if type(tree) not in (Node, tuple) or not tree or tree[0] != 'block': error('Executable module requires a block root')
    try:
        visit(tree, module=True)
        from .macros import Expander
        checker = SimpleNamespace(tick=charge, fail=lambda message, node=None: error(message))
        if not phase:
            Expander.validate_scope(checker, tree)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as failure:
        error('Malformed executable grammar: ' + type(failure).__name__)
    except LanguageError as failure:
        from .language import ResourceLimit
        if isinstance(failure, ResourceLimit) or failure.code == 'executable_error': raise
        error('Invalid executable grammar: ' + failure.message)
