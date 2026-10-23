"""Shared validation of source and Program function parameter structure.

This validates syntax rows, whose absent defaults are None. Runtime parameter
rows instead contain evaluated defaults and the MISSING sentinel; the call
binder consumes those without interpreting them as syntax again.
"""


MODES = ('positional_only', 'positional', 'keyword_only', 'varargs', 'varkw')


def validate_parameters(params, *, tick=None):
    """Validate ordering and return all slot and pattern-leaf names."""
    from .language import LanguageError, Parser, ResourceLimit
    from .patterns import parameter_names

    def fail(message):
        raise LanguageError(message, code='syntax_error')

    if not isinstance(params, (list, tuple)): fail('参数需要有序列表')
    if len(params) > 10000: raise ResourceLimit('函数参数超过 10000 项限制')
    positional_prefix, keyword_only, rest_seen, varkw_seen, default_seen = True, False, False, False, False
    for param in params:
        if tick: tick()
        if not isinstance(param, (tuple, list)) or len(param) not in (2, 4, 5): fail('参数结构需要名字、类型及可选模式、默认值、解构模式')
        name, typ = param[:2]
        if not isinstance(name, str) or not name.isidentifier() or name in Parser.reserved: fail('参数需要非保留标识符')
        if not isinstance(typ, str) or not all(part.isidentifier() and part not in Parser.reserved for part in typ.split('.')): fail('参数类型需要合法限定名称')
        mode, default = param[2:4] if len(param) >= 4 else ('positional', None)
        if mode not in MODES: fail('非法参数模式 '+str(mode))
        if varkw_seen: fail('** 参数必须位于最后')
        if mode in ('positional_only', 'positional'):
            if keyword_only: fail('位置参数必须位于 * 参数边界之前')
            if mode == 'positional_only' and not positional_prefix: fail('位置专用参数必须组成连续前缀')
            if mode == 'positional': positional_prefix = False
            if default is None and default_seen: fail('必需位置参数不能位于默认参数之后')
            default_seen = default_seen or default is not None
        elif mode == 'varargs':
            if keyword_only or rest_seen: fail('只能在仅关键字参数前声明一个 * 参数')
            rest_seen, keyword_only, positional_prefix = True, True, False
        elif mode == 'keyword_only':
            keyword_only, positional_prefix = True, False
        else:
            varkw_seen, positional_prefix = True, False
        if mode in ('varargs', 'varkw') and default is not None: fail('展开参数不能带默认值')
    return parameter_names(params, tick=tick)
