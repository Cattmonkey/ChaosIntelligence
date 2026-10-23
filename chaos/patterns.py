"""Pure structural patterns shared by runtime bindings and compile-time code.

Matching never mutates an environment, consumes an iterator, or invokes user
protocols. Constructor resolution, strict equality and budgets are explicit.
"""


def pattern_names(pattern, *, tick=None):
    """Validate a pattern and return its distinct leaf names in source order."""
    from .language import LanguageError, Parser
    pending, names, seen = [pattern], [], set()
    def fail(message, node):
        raise LanguageError(message, code='syntax_error').attach(getattr(node, 'span', {}))
    while pending:
        node = pending.pop()
        if tick: tick()
        if not isinstance(node, tuple) or not node: fail('需要结构模式', node)
        kind = node[0]
        if kind == 'p_any':
            if len(node) != 1: fail('无效通配模式', node)
        elif kind == 'p_bind':
            if len(node) != 2 or not isinstance(node[1], str) or not node[1].isidentifier() or node[1] in Parser.reserved or node[1] == '_':
                fail('无效模式绑定名', node)
            if node[1] in seen: fail('模式变量重复 '+node[1], node)
            seen.add(node[1]); names.append(node[1])
        elif kind == 'p_literal':
            if len(node) != 2 or node[1] is not None and type(node[1]) not in (bool, int, float, str): fail('模式字面量需要标量', node)
        elif kind in ('p_list', 'p_tuple', 'p_record', 'p_class'):
            if kind == 'p_class':
                if len(node) != 4 or not isinstance(node[1], str) or not all(part.isidentifier() and part not in Parser.reserved for part in node[1].split('.')):
                    fail('无效类模式名称', node)
                children_index = 2
            else: children_index = 1
            if len(node) != children_index + 2 or not isinstance(node[children_index], list): fail('无效容器模式', node)
            rest = node[children_index + 1]
            if rest is not None:
                if not isinstance(rest, tuple) or not rest or rest[0] not in ('p_bind', 'p_any'): fail('剩余模式需要名字或 _', node)
                pending.append(rest)
            if kind in ('p_list', 'p_tuple'): children = node[1]
            else:
                keys, children = set(), []
                for pair in node[children_index]:
                    if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], str): fail('无效记录模式字段', node)
                    if pair[0] in keys: fail('重复模式字段 '+pair[0], node)
                    keys.add(pair[0]); children.append(pair[1])
            pending.extend(reversed(children))
        elif kind == 'p_tag':
            if len(node) != 3 or not isinstance(node[1], str) or not isinstance(node[2], list): fail('无效构造器模式', node)
            if not all(part.isidentifier() and part not in Parser.reserved for part in node[1].split('.')): fail('无效构造器名称', node)
            pending.extend(reversed(node[2]))
        else: fail('未知模式 '+str(kind), node)
    return tuple(names)


def match_pattern(pattern, value, *, resolve_tag, equal, tick=None, project_class=None):
    """Return all bindings on success, None on mismatch, with no partial commit."""
    from .language import LanguageError
    from .tuple_values import TupleValue
    pattern_names(pattern, tick=tick)
    pending, bound = [(pattern, value)], {}
    while pending:
        node, item = pending.pop()
        if tick: tick()
        kind = node[0]
        if kind == 'p_any': continue
        if kind == 'p_literal':
            if not equal(node[1], item): return None
        elif kind == 'p_bind': bound[node[1]] = item
        elif kind == 'p_tag':
            datatype, tag, arity = resolve_tag(node[1])
            if len(node[2]) != arity: raise LanguageError('构造器模式参数数量不符 '+tag).attach(getattr(node, 'span', {}))
            if not datatype.accepts(item) or item['$tag'] != tag: return None
            pending.extend(reversed(list(zip(node[2], item['$values']))))
        elif kind in ('p_list', 'p_tuple'):
            children, rest = node[1:]
            if type(item) is not (TupleValue if kind == 'p_tuple' else list) or (len(item) != len(children) if rest is None else len(item) < len(children)): return None
            if rest is not None: pending.append((rest, item[len(children):]))
            pending.extend(reversed(list(zip(children, item))))
        elif kind == 'p_class':
            if project_class is None:
                raise LanguageError('当前模式环境不支持运行期类字段匹配').attach(getattr(node, 'span', {}))
            pairs, rest = node[2:]
            keys = tuple(key for key, _ in pairs)
            fields = project_class(node[1], item, keys, rest is not None and rest[0] == 'p_bind')
            if fields is None: return None
            if rest is not None and rest[0] == 'p_bind':
                requested = set(keys)
                pending.append((rest, {key:value for key,value in fields.items() if key not in requested}))
            pending.extend(reversed([(child, fields[key]) for key,child in pairs]))
        elif kind == 'p_record':
            pairs, rest = node[1:]; keys = {key for key, _ in pairs}
            if not isinstance(item, dict) or not keys.issubset(item) or rest is None and keys != item.keys(): return None
            if rest is not None: pending.append((rest, {key:value for key,value in item.items() if key not in keys}))
            pending.extend(reversed([(child, item[key]) for key,child in pairs]))
    return bound


def parameter_names(params, *, tick=None):
    """Validate the single lexical namespace shared by parameter slots/leaves."""
    from .language import LanguageError
    names, seen = [], set()
    for param in params:
        if tick: tick()
        pattern = param[4] if len(param) == 5 else None
        leaves = pattern_names(pattern, tick=tick) if pattern is not None else ()
        for name in (param[0], *leaves):
            if name in seen:
                raise LanguageError('参数或模式变量不能重名 '+name, code='syntax_error').attach(getattr(pattern, 'span', {}))
            seen.add(name); names.append(name)
    return tuple(names)


def bind_parameter_patterns(params, values, *, resolve_tag, equal, tick=None, project_class=None):
    """Match all parameter patterns without publishing a partial call frame.

The caller supplies the captured constructor environment, before any slot or
leaf has been installed. Whole-value argument contracts have already passed.
"""
    from .language import LanguageError
    result = dict(values)
    for param in params:
        if len(param) != 5: continue
        pattern = param[4]
        bound = match_pattern(pattern, values[param[0]], resolve_tag=resolve_tag, equal=equal, tick=tick, project_class=project_class)
        if bound is None:
            raise LanguageError('参数 '+param[0]+' 解构模式不匹配', code='match_error').attach(getattr(pattern, 'span', {}))
        for name in bound:
            if name in result:
                raise LanguageError('参数或模式变量不能重名 '+name, code='syntax_error').attach(getattr(pattern, 'span', {}))
        result.update(bound)
    return result
