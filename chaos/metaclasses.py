"""Evaluator-native metaclasses and prepared namespace protocols.

Type is a language value. All user hooks pass through the original AST runner;
none of these operations delegate class creation to Python's type machinery.
"""
from dataclasses import dataclass

DEFAULT = object()


@dataclass(frozen=True)
class TypeNewRequest:
    metaclass: object
    name: object
    bases: object
    namespace: object
    options: object


@dataclass(frozen=True)
class TypeCallRequest:
    klass: object
    args: object
    options: object


@dataclass(frozen=True)
class TypeCheckRequest:
    operation: str
    value: object
    klass: object


def subclass(child, parent):
    from .objects import ChaosClass
    return type(child) is ChaosClass and any(item is parent for item in child.mro)


def instance(value, klass):
    from .objects import ChaosClass, ChaosInstance
    actual = value.metaclass if type(value) is ChaosClass else value.klass if type(value) is ChaosInstance else None
    return actual is not None and subclass(actual, klass)


def check(evaluator, request):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'type_check',request))


def is_metaclass(evaluator, value):
    return subclass(value, evaluator.native_type)


def bootstrap(evaluator):
    from .objects import ChaosClass, Member
    from .language import Native
    root = ChaosClass('Type', (), evaluator, building=False)
    root.mro = (root,); root.metaclass = root
    evaluator.native_type = root
    def prepare(mcls, name, bases, **options): return {}
    def new(mcls, name, bases, namespace, **options): return TypeNewRequest(mcls, name, bases, namespace, options)
    def init(cls, name, bases, namespace, **options): return None
    def call(cls, *args, **options): return TypeCallRequest(cls, list(args), options)
    def instancecheck(cls, value):
        from .class_creation import validate_value
        from .language import LanguageError
        validate_value(evaluator, cls); validate_value(evaluator, value)
        if type(cls) is not ChaosClass or cls.building: raise LanguageError('类型判断需要已完成的 Class', code='argument_error')
        return instance(value, cls)
    def subclasscheck(cls, child):
        from .class_creation import validate_value
        from .language import LanguageError
        validate_value(evaluator, cls); validate_value(evaluator, child)
        if type(cls) is not ChaosClass or cls.building or type(child) is not ChaosClass or child.building:
            raise LanguageError('继承判断需要已完成的 Class', code='argument_error')
        return subclass(child, cls)
    for name, function in [('__prepare__', prepare), ('__new__', new), ('__init__', init), ('__call__', call),
                           ('__instancecheck__', instancecheck), ('__subclasscheck__', subclasscheck)]:
        root.members[name] = Member(Native('Type.'+name, function, evaluator, spec_id='Type.'+name), method=True,
                                    defining_class=root, class_bound=name=='__prepare__', static_bound=name=='__new__')
    return root


def validate_result(evaluator, value):
    from .class_creation import validate_value
    from .language import LanguageError
    from .suspension import Task, Generator
    if isinstance(value, (Task, Generator)):
        raise LanguageError('类创建协议不能返回 Task 或 Generator；不会隐式等待', code='argument_error')
    validate_value(evaluator, value)
    return value


def select(evaluator, bases, explicit=DEFAULT):
    from .objects import ChaosClass, ChaosInstance, BoundMethod, StaticMethod, owned, callable_object
    from .language import Function, Native, LanguageError
    from .class_creation import validate_value
    candidate = evaluator.native_type if explicit is DEFAULT else explicit
    validate_value(evaluator, candidate)
    if type(candidate) is ChaosClass and candidate.building:
        raise LanguageError('metaclass 需要已完成的 Class', code='argument_error')
    if not is_metaclass(evaluator, candidate):
        if type(candidate) in (Function, Native, BoundMethod, StaticMethod, ChaosInstance, ChaosClass) and callable_object(candidate):
            return candidate
        raise LanguageError('metaclass 需要原生 Type 子类或原生可调用工厂', code='argument_error')
    owned(candidate, evaluator)
    for base in bases:
        meta = owned(base.metaclass, evaluator)
        if subclass(candidate, meta): continue
        if subclass(meta, candidate): candidate = meta
        else: raise LanguageError('基类元类无法选择共同的最派生元类', code='metaclass_conflict')
    return candidate


def mapping_member(evaluator, namespace, name):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'mapping_member',namespace,name))


def namespace_snapshot(evaluator, namespace):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'namespace_snapshot',namespace))


def namespace_read(evaluator, namespace, key):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'namespace_read',namespace,key))


def lookup_name(evaluator, env, name):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'lookup_name',env,name))


def assign_name(evaluator, env, name, value):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'assign_name',env,name,value))


def namespace_write(evaluator, namespace, key, value):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'namespace_write',namespace,key,value))


def original_bases_write(evaluator, namespace, original, *sources):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'original_bases_write',namespace,original,sources))


def original_bases_result(evaluator, result):
    from .objects import ChaosClass, member_value
    from .class_creation import project_value
    from .language import Cell, check_type
    if type(result) is ChaosClass and '__orig_bases__' in result.members:
        # Preserve custom transformations, but publish the actual projected
        # callable values, not the wider originals. Never run another getter.
        member = result.members['__orig_bases__']
        projected = check_type(project_value(evaluator, member_value(member), result), member.contract)
        if isinstance(member.value, Cell): member.value.value = projected
        else: member.value = projected


def prepare(evaluator, meta, name, bases, options):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'prepare',meta,name,bases,options))


def build_stack(evaluator):
    target = evaluator.suspension.current or evaluator
    stack = getattr(target, '_class_build_stack', None)
    if stack is None: stack=[]; target._class_build_stack=stack
    return stack


def lowlevel_new(evaluator, request):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'lowlevel_new',request))


def new_value(evaluator, meta, args, options):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'new_value',meta,args,options))


def default_call(evaluator, request):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'default_call',request))


def class_call(evaluator, klass, args, options, span=None, synchronous=False):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'class_call',klass,args,options,span,synchronous))


def create(evaluator, request):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'metaclass_create',request))
