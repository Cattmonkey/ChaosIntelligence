"""Nominal exceptions use the ordinary language class and pattern machinery.

Error remains the occurrence/cause carrier. No host exception class, name-based
matching, user getter or metaclass predicate participates in classification.
"""
DEFINITIONS = (
    ('Exception', None, 'exception', ()),
    ('RuntimeError', 'Exception', 'runtime_error', ('runtime_error',)),
    ('UserError', 'Exception', 'user_throw', ('user_throw',)),
    ('TypeError', 'Exception', 'type_mismatch', ('type_mismatch', 'TypeError')),
    ('ArgumentError', 'TypeError', 'argument_error', ('argument_error',)),
    ('ValueError', 'Exception', 'value_error', ('value_error', 'ValueError')),
    ('LookupError', 'Exception', 'lookup_error', ('lookup_error',)),
    ('IndexError', 'LookupError', 'index_error', ('index_error', 'IndexError')),
    ('KeyError', 'LookupError', 'key_error', ('key_error', 'KeyError')),
    ('ArithmeticError', 'Exception', 'arithmetic_error', ('arithmetic_error',)),
    ('ZeroDivisionError', 'ArithmeticError', 'zero_division', ('zero_division', 'ZeroDivisionError')),
    ('OverflowError', 'ArithmeticError', 'overflow_error', ('overflow_error', 'OverflowError')),
    ('AttributeError', 'Exception', 'attribute_error', ('attribute_error',)),
    ('NameError', 'Exception', 'undefined_name', ('undefined_name',)),
    ('MatchError', 'Exception', 'match_error', ('match_error', 'pattern_mismatch')),
    ('OwnershipError', 'Exception', 'ownership_error', ('ownership_error',)),
    ('CapabilityError', 'Exception', 'capability_error', ('capability_error',)),
    ('ToolError', 'Exception', 'tool_error', ('tool_error',)),
    ('AgentError', 'Exception', 'agent_error', ('agent_error',)),
    ('ProviderError', 'AgentError', 'provider_error', ('provider_error',)),
    ('AgentToolError', 'AgentError', 'agent_tool_error', ('agent_tool_error',)),
    ('ImportError', 'Exception', 'import_error', ('import_error',)),
)
FIELDS = (('code', 'String'), ('message', 'String'), ('args', 'Tuple'), ('value', 'Any'))


def intrinsic(evaluator, value):
    return value is not None and (value is getattr(evaluator, 'native_type', None) or any(
        value is klass for klass in getattr(evaluator, 'exception_classes', {}).values()))


def is_class(evaluator, value):
    from .objects import ChaosClass, owned
    if type(value) is not ChaosClass: return False
    owned(value, evaluator)
    root = evaluator.exception_classes['Exception']
    return not value.building and any(base is root for base in value.mro)


def is_instance(evaluator, value):
    from .objects import ChaosInstance, owned
    if type(value) is not ChaosInstance: return False
    owned(value, evaluator)
    return is_class(evaluator, value.klass)


def bootstrap(evaluator):
    from .objects import ChaosClass, Member, declaration
    from .language import Native, LanguageError, TupleValue, Cell
    from .class_creation import allocate, project_value
    classes = {}; evaluator.exception_classes = classes
    def initialize(self, *args):
        from .language import check_type
        if not is_instance(evaluator, self):
            raise LanguageError('Exception.__init__ 需要当前解释器的异常实例', code='argument_error')
        args = TupleValue(project_value(evaluator, list(args), self, self.klass))
        values = {'args': args, 'message': args[0] if len(args) == 1 and type(args[0]) is str else self.klass.name,
                  'value': args[0] if len(args) == 1 else args}
        prepared = {}
        for name, value in values.items():
            member = declaration(self.klass, name)
            contract = member.contract if member is not None else dict(FIELDS)[name]
            prepared[name] = Cell(check_type(value, contract), contract)
        self.fields.update(prepared)
        return None
    def new(cls, *args, **options):
        if not is_class(evaluator, cls):
            raise LanguageError('Exception.__new__ 需要当前解释器的 Exception 子类', code='argument_error')
        result = allocate(evaluator, cls)
        initialize(result, *args)
        # Original positional/keyword arguments are bound by the real __init__.
        return result
    for name, parent, code, codes in DEFINITIONS:
        bases = (classes[parent],) if parent else ()
        klass = ChaosClass(name, bases, evaluator, building=False, metaclass=evaluator.native_type)
        klass.mro = (klass,) + (bases[0].mro if bases else ())
        klass.members['code'] = Member(code, 'String', defining_class=klass)
        if parent is None:
            for label, value, typ in [('message', '', 'String'), ('args', TupleValue([]), 'Tuple'), ('value', None, 'Any')]:
                klass.members[label] = Member(value, typ, defining_class=klass)
            klass.members['__new__'] = Member(Native('Exception.__new__', new, evaluator, spec_id='Exception.__new__'),
                                             method=True, defining_class=klass, static_bound=True)
            klass.members['__init__'] = Member(Native('Exception.__init__', initialize, evaluator, spec_id='Exception.__init__'),
                                              method=True, defining_class=klass)
        classes[name] = klass
    evaluator.exception_codes = {code: classes[name] for name, _, _, codes in DEFINITIONS for code in codes}
    return classes


def raised(evaluator, value):
    """An explicit throw creates an occurrence; only Error/bare throw reraises."""
    from .language import Raised, LanguageError, check_type
    from .objects import pattern_fields
    from .class_creation import project_value
    if not is_instance(evaluator, value): return Raised(value)
    value = project_value(evaluator, value)
    fields = pattern_fields(value, evaluator.exception_classes['Exception'], evaluator, ('code', 'message'), False)
    if fields is None:
        raise LanguageError('异常 code/message 需要真实存储字段；不执行属性协议', code='argument_error')
    code, message = check_type(fields['code'], 'String'), check_type(fields['message'], 'String')
    if not code or len(code) > 160 or len(message) > 10000:
        raise LanguageError('异常 code 需1..160字符、message至多10000字符', code='argument_error')
    error = Raised(value, message=message, code=code)
    error._exception_instance = value
    return error


def nominal_class(evaluator, exception):
    from .errors import catchable, GroupedError
    from .language import LanguageError
    if not catchable(exception):
        raise LanguageError('控制信号不能作为异常实例', code='argument_error')
    original = getattr(exception, '_exception_instance', None)
    if original is not None:
        if not is_instance(evaluator, original):
            raise LanguageError('异常实例不能跨解释器使用', code='ownership_error')
        return original.klass
    if type(exception) is GroupedError: return evaluator.exception_classes['Exception']
    return evaluator.exception_codes.get(exception.code, evaluator.exception_classes['RuntimeError'])


def instance(evaluator, exception):
    """Fresh bounded runtime snapshots; user instances keep nominal identity.

    Never cache a view obtained with a wider authority on a shared host error.
    Native classes are sealed and authority-neutral; each allocated view captures
    the actual current ceiling and receives only projected language data.
    """
    from .class_creation import allocate, project_value
    from .language import Cell, TupleValue
    from .agent_tools import scope
    klass = nominal_class(evaluator, exception)
    original = getattr(exception, '_exception_instance', None)
    with scope(evaluator, getattr(exception, '_error_capabilities', None)):
        if original is not None: return project_value(evaluator, original)
        record = project_value(evaluator, exception.record())
        value = allocate(evaluator, klass)
        fields = dict(code=record['code'], message=record['message'], value=record['value'], args=TupleValue([record['message']]))
        for name, item in fields.items(): value.fields[name] = Cell(item, dict(FIELDS)[name])
        return value


def match(evaluator, pattern, exception, env, bound):
    """Only a root nominal exception pattern selects the exception instance.

    All other patterns retain the exact legacy four-field record. A rejected
    nominal class never allocates a snapshot or executes user protocols.
    """
    from .error_values import record_for, propagate, capture
    from .agent_tools import scope
    if pattern[0] == 'p_class':
        klass = evaluator.lookup_qualified(pattern[1], env)
        if is_class(evaluator, klass):
            actual = nominal_class(evaluator, exception)
            if not any(base is klass for base in actual.mro): return False
            exception = propagate(evaluator, capture(evaluator, exception))
            with scope(evaluator, getattr(exception, '_error_capabilities', None)):
                value = instance(evaluator, exception)
                return evaluator.pattern_match(pattern, value, env, bound)
    record = evaluator.copy_value(record_for(evaluator, exception))
    if pattern[0] == 'p_bind':
        bound[pattern[1]] = record
        return True
    return evaluator.pattern_match(pattern, record, env, bound)
