"""Cooperative ordinary attributes, separate from raw implicit protocol lookup."""
from .objects import ChaosClass,ChaosInstance,owned,declaration,member_value,bind_member

NO_HOOK=object()


def name(evaluator,value):
    from .language import LanguageError,serializable
    evaluator.tick()
    if type(value) is not str:raise LanguageError('Attribute name requires an exact String',code='argument_error')
    serializable(value)
    return value


def kind(evaluator,target):
    owned(target,evaluator)
    actual=target.metaclass if type(target) is ChaosClass else target.klass if type(target) is ChaosInstance else None
    if actual is not None:owned(actual,evaluator)
    return actual


def project(evaluator,value,target,actual):
    """Full unrestricted reads retain aliases; narrowed results retain ceilings."""
    from .agent_tools import current_caps
    from .class_creation import validate_value,project_value
    validate_value(evaluator,value)
    if current_caps(evaluator) is None:return value
    return project_value(evaluator,value,target,actual)


def hook(evaluator,target,actual,label):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'hook',target,actual,label)



def read(evaluator,target,label,*,default=False):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'read',target,label,default)



def write(evaluator,target,label,item=None,*,delete=False,default=False):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'write',target,label,item,delete,default)



def directory_hook(evaluator,function):
    """Directory may be a sync generator factory; it never starts async code."""
    from .language import Function,LanguageError
    from .objects import StaticMethod,BoundMethod,require_callable
    require_callable(function,evaluator)
    target=function;seen=set()
    while id(target) not in seen:
        seen.add(id(target))
        if type(target) is Function and target.flavor in ('async_fn','async_gen_fn'):
            raise LanguageError('__dir__ must be synchronous',code='argument_error')
        if type(target) is BoundMethod:target=target.function
        elif type(target) is StaticMethod:target=target.value
        else:break


def directory(evaluator,target,raw_directory,*,default=False):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'directory',target,raw_directory,default)

