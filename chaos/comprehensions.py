"""Native comprehension traversal on the existing cooperative AST scheduler.

The original expression remains the source/IR authority. Lazy forms own only
their wrapper; each input cursor uses the ordinary for-loop borrowing protocol.
"""


KINDS = ('list_comp', 'record_comp', 'set_comp', 'gen_comp', 'async_gen_comp')
FOR_KINDS = ('comp_for', 'comp_async_for', 'comp_for_pattern', 'comp_async_for_pattern')


def comprehension_scopes(node, outer_await_allowed):
    """Return component await permissions across a control-flow boundary.

Callers validate each expression with no enclosing return/yield/loop target.
Nested explicit functions and loops then establish their usual local targets.
"""
    from .language import LanguageError
    if node[0] not in KINDS: return None
    body_allowed = (node[0] == 'async_gen_comp' or
                    node[0] in ('list_comp', 'record_comp', 'set_comp') and outer_await_allowed)
    clauses = node[-1]
    if not clauses or len(clauses) > 64 or clauses[0][0] not in FOR_KINDS:
        raise LanguageError('推导式需要 1–64 个子句且首项必须为 for', code='syntax_error').attach(getattr(node, 'span', {}))
    expressions = [(value, body_allowed) for value in node[1:-1]]
    for index, clause in enumerate(clauses):
        kind = clause[0]
        if kind not in FOR_KINDS + ('comp_if',):
            raise LanguageError('无效推导子句', code='syntax_error').attach(getattr(clause, 'span', {}))
        if kind in ('comp_async_for', 'comp_async_for_pattern') and not body_allowed:
            raise LanguageError('async for 推导子句仅允许异步作用域', code='syntax_error').attach(getattr(clause, 'span', {}))
        expressions.append((clause[-1], outer_await_allowed if index == 0 else body_allowed))
    return body_allowed, expressions


def validate_comprehension(node, outer_await_allowed, checkpoint=None):
    """Check parsed components before any runtime effect, including cold bodies.

Quote trees are checked by macro expansion after their insertion context is
known. Reuse the language's explicit AST child mapping rather than interpreting
parameter records or patterns as executable nodes.
"""
    from .language import LanguageError
    from .macros import _children
    _, expressions = comprehension_scopes(node, outer_await_allowed)
    pending = [(expression, None, 0, allowed) for expression, allowed in expressions]
    while pending:
        expression, function, loops, allowed = pending.pop()
        if checkpoint: checkpoint()
        kind = expression[0]
        forbidden = (kind == 'return' and function is None or
                     kind in ('break', 'continue') and not loops or
                     kind in ('yield', 'yield_from') and function not in ('gen_fn', 'async_gen_fn') or
                     kind in ('await', 'async_for', 'async_for_pattern', 'async_with') and not allowed)
        if forbidden:
            raise LanguageError('推导式组件不能越过控制流或异步作用域边界', code='syntax_error').attach(getattr(expression, 'span', {}))
        if kind == 'quote': continue
        if kind in KINDS:
            _, nested = comprehension_scopes(expression, allowed)
            pending.extend((value, None, 0, permission) for value, permission in nested)
        elif kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
            pending.extend((param[3], None, 0, allowed) for param in expression[1] if len(param) >= 4 and param[3] is not None)
            pending.append((expression[3], kind, 0, kind in ('async_fn', 'async_gen_fn')))
        elif kind == 'class':
            pending.extend((base, function, loops, allowed) for base in expression[2])
            from .class_creation import class_options
            pending.extend((option[2] if option[0]=='keyword' else option[1],function,loops,allowed) for option in class_options(expression))
            for member in expression[3][1]:
                value = member if member[0] == 'decorated' else member[2] if member[0] == 'def' else member[3]
                pending.append((value, None, 0, allowed))
        elif kind == 'decorated':
            pending.extend((value, None, 0, allowed) for value in expression[1])
            pending.append((expression[2], function, loops, allowed))
        elif kind in ('for', 'async_for', 'for_pattern', 'async_for_pattern'):
            pending.extend(((expression[2], function, loops, allowed), (expression[3], function, loops + 1, allowed)))
        elif kind == 'while':
            pending.extend(((expression[1], function, loops, allowed), (expression[2], function, loops + 1, allowed)))
        else:
            _children(expression, lambda value: pending.append((value, function, loops, allowed)))


def evaluate(evaluator,node,env):
    from .execution_frames import request_proxy,ensure_cursor
    from .execution_comprehensions import ComprehensionRequest
    iterator=request_proxy(ComprehensionRequest('evaluate',node,env))
    return iterator if getattr(evaluator,'_execution_cursor',None) is not None else ensure_cursor(evaluator,iterator)


def traverse(evaluator,node,env,outer):
    from .execution_frames import request_proxy,ensure_cursor
    from .execution_comprehensions import ComprehensionRequest
    iterator=request_proxy(ComprehensionRequest('traverse',node,env,outer))
    return iterator if getattr(evaluator,'_execution_cursor',None) is not None else ensure_cursor(evaluator,iterator)
