"""Test-only exact pre-migration object-access algorithms; no product fallback."""
from contextlib import contextmanager,ExitStack
from unittest.mock import patch
from chaos.language import *
from chaos.objects import ChaosClass,ChaosInstance,owned,declaration,member_value,bind_member
from chaos.attribute_protocols import NO_HOOK,name,kind,project,directory_hook
from chaos.indexing import Slice,IndexRequest,ItemRequest,validate_owned,native_get,slice_indices
def attribute_hook(evaluator, target, actual, label):
    member = declaration(actual, label) if actual is not None else None
    if member is None:
        return NO_HOOK
    return (yield from evaluator._s_access_result(bind_member(member_value(member), member, target, actual, evaluator, instance_access=type(target) is ChaosClass)))

def attribute_read(evaluator, target, label, *, default=False):
    from chaos.agent_tools import scope
    from chaos.language import LanguageError
    label = name(evaluator, label)
    actual = kind(evaluator, target)
    with scope(evaluator, getattr(target, 'capabilities', None)), scope(evaluator, getattr(actual, 'capabilities', None)):
        if actual is None:
            from chaos.objects import _project_callable
            return _project_callable((yield from evaluator._s_scoped_get_member(target, label)), evaluator, target)
        if default:
            return project(evaluator, (yield from evaluator._s_scoped_get_member(target, label)), target, actual)
        try:
            function = (yield from hook(evaluator, target, actual, '__getattribute__'))
            value = (yield from evaluator._s_scoped_get_member(target, label)) if function is NO_HOOK else (yield from evaluator._s_invoke(function, [label]))
            return project(evaluator, value, target, actual)
        except LanguageError as failure:
            if failure.code != 'attribute_error':
                raise
            original = failure
        previous = evaluator.error_context
        evaluator.error_context = original
        try:
            function = (yield from hook(evaluator, target, actual, '__getattr__'))
            if function is NO_HOOK:
                raise original
            return project(evaluator, (yield from evaluator._s_invoke(function, [label])), target, actual)
        finally:
            evaluator.error_context = previous

def attribute_write(evaluator, target, label, item=None, *, delete=False, default=False):
    from chaos.agent_tools import scope
    from chaos.class_creation import validate_value
    from chaos.exception_classes import intrinsic
    from chaos.language import LanguageError, check_type
    from chaos.objects import require_callable
    label = name(evaluator, label)
    actual = kind(evaluator, target)
    if not delete:
        validate_value(evaluator, item)
    if intrinsic(evaluator, target):
        raise LanguageError('内置类的成员不可修改', code='attribute_error')
    with scope(evaluator, getattr(target, 'capabilities', None)), scope(evaluator, getattr(actual, 'capabilities', None)):
        function = NO_HOOK if default or actual is None else (yield from hook(evaluator, target, actual, '__delattr__' if delete else '__setattr__'))
        if function is NO_HOOK:
            return (yield from evaluator._s_scoped_delete_member(target, label)) if delete else (yield from evaluator._s_scoped_set_member(target, label, item))
        require_callable(function, evaluator, setter=True)
        result = (yield from evaluator._s_invoke(function, [label] if delete else [label, item], synchronous=True))
        check_type(result, 'Null')
        return None if delete else item

def attribute_directory(evaluator, target, raw_directory, *, default=False):
    from chaos.agent_tools import scope
    from chaos.language import LanguageError, ResourceLimit, serializable
    from chaos.suspension import Task, AsyncGenerator
    from chaos.iteration import AsyncIterator
    from chaos.capture_frames import enter, leave, retain
    from chaos.language import MISSING
    actual = kind(evaluator, target)
    with scope(evaluator, getattr(target, 'capabilities', None)), scope(evaluator, getattr(actual, 'capabilities', None)):
        function = NO_HOOK if default or actual is None else (yield from hook(evaluator, target, actual, '__dir__'))
        if function is NO_HOOK:
            return raw_directory(target)
        directory_hook(evaluator, function)
        source = (yield from evaluator._s_invoke(function, []))
        if type(source) in (Task, AsyncGenerator, AsyncIterator):
            raise LanguageError('__dir__ requires a synchronous iterable, without implicit await', code='argument_error')
        from chaos.class_creation import validate_value
        validate_value(evaluator, source)
        capture = enter(evaluator, 'attribute_directory', target, function, source)
        cursor = None
        locked = False
        try:
            cursor = (yield from evaluator.suspension.iteration.acquire(source, temporary=True))
            retain(capture, cursor)
            if cursor.busy:
                raise LanguageError('Directory iterator cannot be re-entered', code='argument_error')
            cursor.busy = True
            locked = True
            result = []
            chars = 0
            while True:
                step = (yield from evaluator.suspension.iteration.step(cursor, locked=True))
                if step['done']:
                    return sorted(result)
                if len(result) >= 10000:
                    raise ResourceLimit('Directory exceeds 10000 names')
                value = step['value']
                if type(value) is not str:
                    raise LanguageError('__dir__ members require exact String names', code='argument_error')
                evaluator.tick()
                chars += len(value)
                if chars > 250000:
                    raise ResourceLimit('Directory text exceeds 250000 characters')
                serializable(value)
                result.append(value)
        finally:
            if locked:
                cursor.busy = False
            leave(capture, MISSING)

def index_integer_index(evaluator, value):
    """Lossless conversion through the type's special slot, never host coercion."""
    from chaos.objects import ChaosInstance, owned, declaration, special_method, require_callable
    evaluator.tick()
    owned(value, evaluator)
    if type(value) is not int:
        if not isinstance(value, ChaosInstance):
            raise LanguageError('index 需要 Int 或定义 __index__ 的 Instance', code='argument_error')
        owned(value.klass, evaluator)
        if declaration(value.klass, '__index__') is None:
            raise LanguageError('对象未定义 __index__', code='argument_error')
        function = (yield from evaluator._s_access_result(special_method(value, '__index__', evaluator)))
        require_callable(function, evaluator, setter=True)
        value = (yield from evaluator._s_invoke(function, [], synchronous=True))
        evaluator.tick()
        if type(value) is not int:
            raise LanguageError('__index__ 必须返回精确 Int，不能返回 Bool、Number 或挂起值', code='argument_error')
    if value.bit_length() > 14000:
        raise ResourceLimit('index 整数超过 14000 bit')
    return value

def index_resolve_index(evaluator, request):
    if request.operation == 'index':
        return (yield from integer_index(evaluator, request.values[0]))
    if request.operation == 'slice':
        parts = []
        for value in request.values:
            parts.append(None if value is None else (yield from integer_index(evaluator, value)))
        return Slice(*parts)
    if request.operation == 'indices':
        spec, length = request.values
        if type(spec) is not Slice:
            raise LanguageError('slice_indices 需要 Slice', code='argument_error')
        return slice_indices(spec, (yield from integer_index(evaluator, length)))
    raise LanguageError('未知整数索引操作', code='argument_error')

def index_invoke_item(evaluator, function, request):
    """One protocol call contract for instance and metaclass item operations."""
    from chaos.objects import require_callable
    function = (yield from evaluator._s_access_result(function))
    write = request.operation != 'get'
    if write:
        require_callable(function, evaluator, setter=True)
    args = [request.key, request.item] if request.operation == 'set' else [request.key]
    result = (yield from evaluator._s_invoke(function, args, synchronous=write))
    evaluator.tick()
    if write:
        return check_type(result, 'Null')
    serializable(result)
    validate_owned(evaluator, result)
    return result

def index_operation(evaluator, request):
    from chaos.objects import ChaosClass, ChaosInstance, SuperProxy, DESCRIPTORS, owned, declaration, special_method, bind_member, member_value
    evaluator.tick()
    target, key, item = (request.target, request.key, request.item)
    owned(target, evaluator)
    validate_owned(evaluator, key)
    if request.operation != 'get':
        validate_owned(evaluator, item)
    from chaos.hash_maps import HashMap, Request as HashMapRequest, resolve as hash_map_resolve
    if type(target) is HashMap:
        if request.operation != 'get':
            raise LanguageError('HashMap 为持久值；请使用 hash_set/hash_remove 返回新映射', code='argument_error')
        return (yield from hash_map_resolve(evaluator, HashMapRequest('index', target, key)))
    if isinstance(target, ChaosInstance):
        owned(target.klass, evaluator)
        name = {'get': '__getitem__', 'set': '__setitem__', 'delete': '__delitem__'}[request.operation]
        member = declaration(target.klass, name)
        if member is not None:
            return (yield from invoke_item(evaluator, special_method(target, name, evaluator), request))
        if request.operation == 'get':
            result = (yield from evaluator._s_get_member(target, key))
            validate_owned(evaluator, result)
            return result
        if request.operation == 'set' and request.fallback:
            return (yield from evaluator._s_set_member(target, key, item))
        raise LanguageError('对象未定义 ' + name, code='argument_error')
    if isinstance(target, ChaosClass):
        from chaos.agent_tools import scope
        meta = owned(target.metaclass, evaluator)
        name = {'get': '__getitem__', 'set': '__setitem__', 'delete': '__delitem__'}[request.operation]
        member = declaration(meta, name)
        if member is not None:
            with scope(evaluator, target.capabilities):
                function = bind_member(member_value(member), member, target, meta, evaluator, instance_access=True)
                return (yield from invoke_item(evaluator, function, request))
        if request.operation == 'get' and (declaration(target, '__class_getitem__') is not None or declaration(meta, '__class_getitem__') is not None):
            function = (yield from evaluator._s_default_get_member(target, '__class_getitem__'))
            return (yield from invoke_item(evaluator, function, request))
    if isinstance(target, (ChaosClass, SuperProxy) + DESCRIPTORS):
        if request.operation == 'get':
            result = (yield from evaluator._s_get_member(target, key))
            validate_owned(evaluator, result)
            return result
        if request.operation == 'set' and request.fallback:
            return (yield from evaluator._s_set_member(target, key, item))
        raise LanguageError('显式项目修改需要 Instance 协议', code='argument_error')
    from chaos.dynamic_contexts import Context, ContextVar, get_in
    if type(target) is Context and type(key) is ContextVar and (request.operation == 'get'):
        return get_in(evaluator, target, key)
    if request.operation != 'get':
        raise LanguageError('原生 List/Tuple/String/Record 是值式容器，不支持项目修改', code='argument_error')
    if type(target) in (list, str, TupleValue) and type(key) is not Slice:
        key = (yield from integer_index(evaluator, key))
    result = native_get(target, key)
    validate_owned(evaluator, result)
    return result

def _s_access_result(self, value, protocol_depth=0):
    from chaos.objects import AccessorCall, ProtocolCall, special_method, require_callable
    if isinstance(value, (AccessorCall, ProtocolCall)):
        self.tick()
        if protocol_depth >= 60:
            raise ResourceLimit('描述符协议解析深度超过 60')
        if isinstance(value, ProtocolCall):
            function = (yield from self._s_access_result(special_method(value.descriptor, value.name, self), protocol_depth + 1))
        else:
            function = value.function
        if value.write:
            require_callable(function, self, setter=True)
        from chaos.agent_tools import scope as authority_scope
        with authority_scope(self, getattr(value, 'capabilities', None)):
            result = (yield from self._s_invoke(function, value.args, synchronous=value.write))
        if value.write:
            self.tick()
            check_type(result, 'Null')
            return value.item
        return result
    return value

def _s_item_key(self, node, env):
    if node[0] == 'tuple':
        if len(node[1]) > 10000:
            raise ResourceLimit('Tuple 超过 10000 项限制')
        parts = []
        for part in node[1]:
            parts.append((yield from self._s_item_key(part, env)) if part[0] == 'slice' else (yield from self._s_evaluate(part, env)))
        return TupleValue(parts)
    if node[0] != 'slice':
        return (yield from self._s_evaluate(node, env))
    from chaos.indexing import IndexRequest, resolve_index
    parts = []
    for part in node[1:]:
        parts.append(None if part is None else (yield from self._s_evaluate(part, env)))
    return (yield from resolve_index(self, IndexRequest('slice', tuple(parts))))

def _s_index_value(self, value, index):
    from chaos.objects import ChaosClass, ChaosInstance, ClassMember, UIArtifact, SuperProxy, DESCRIPTORS
    from chaos.indexing import Slice
    from chaos.task_groups import TaskGroup
    from chaos.cancellation import CancelShield
    from chaos.resource_stacks import STACKS
    from chaos.conditions import VALUES as RECOVERY_VALUES
    if type(value) is Context and type(index) is ContextVar:
        from chaos.dynamic_contexts import get_in
        return get_in(self, value, index)
    if type(value) in (Error, Condition, Fraction, Complex, Decimal, DecimalContext, Slice, TaskGroup, CancelShield) + STACKS + RECOVERY_VALUES + CONTEXT_VALUES + (PromptTag, PromptScope, EscapeContinuation) + ROBOT_VALUES or isinstance(value, (ChaosClass, ChaosInstance, ClassMember, UIArtifact, SuperProxy) + DESCRIPTORS):
        return (yield from self._s_get_member(value, index))
    if not isinstance(value, (str, list, dict, TupleValue)):
        raise LanguageError('仅可索引 List、Tuple、String、Record')
    if isinstance(value, dict) and (not isinstance(index, str)):
        raise LanguageError('Record 索引必须为 String')
    if isinstance(value, (str, list, TupleValue)) and (type(index) is not int or index < 0):
        raise LanguageError('序列索引必须为非负 Int')
    return value[index]

def _s_scoped_get_member(self, target, name):
    if type(target) is Bot:
        from chaos.bot_values import owned as bot_owned
        bot_owned(target, self)
        raise LanguageError('Bot has no public live members', code='attribute_error')
    if type(target) in ROBOT_VALUES:
        from chaos.robot_values import member as robot_member
        return robot_member(self, target, name)
    if type(target) in (PromptTag, PromptScope, EscapeContinuation):
        from chaos.control_prompts import member
        return member(self, target, name)
    if type(target) is Error:
        from chaos.error_values import member
        return member(target, name, self)
    if type(target) is DecimalContext:
        from chaos.decimal_values import member
        return member(target, name, self)
    if type(target) in (Fraction, Complex, Decimal):
        from chaos.numeric_values import member
        return member(target, name)
    from chaos.resource_stacks import STACKS, get_member as stack_member
    if type(target) in STACKS:
        return stack_member(target, name, self)
    from chaos.cancellation import CancelShield, owned as shield_owned
    if type(target) is CancelShield:
        shield_owned(target, self)
        raise LanguageError('CancelShield 没有公开成员', code='attribute_error')
    from chaos.task_groups import TaskGroup, get_member
    if type(target) is TaskGroup:
        return get_member(target, name, self)
    from chaos.indexing import Slice
    if type(target) is Slice:
        if name not in ('start', 'stop', 'step'):
            raise LanguageError('Slice 成员不存在', code='attribute_error')
        return {'start': target.start, 'stop': target.stop, 'step': target.step}[name]
    from chaos.objects import get_member
    return (yield from self._s_access_result(get_member(target, name, self)))

def _s_scoped_set_member(self, target, name, item):
    if type(target) in ROBOT_VALUES:
        from chaos.robot_values import owned as robot_owned
        robot_owned(target, self)
        raise LanguageError('Robot members are read-only', code='attribute_error')
    if type(target) in (PromptTag, PromptScope, EscapeContinuation):
        from chaos.control_prompts import owned as control_owned
        control_owned(target, self)
        raise LanguageError('Control members are read-only', code='attribute_error')
    if type(target) is Error:
        from chaos.error_values import owned as error_owned
        error_owned(target, self)
        raise LanguageError('Error 成员只读', code='attribute_error')
    from chaos.resource_stacks import STACKS, owned as stack_owned
    if type(target) in STACKS:
        stack_owned(target, self)
        raise LanguageError('ExitStack 成员只读', code='attribute_error')
    from chaos.cancellation import CancelShield, owned as shield_owned
    if type(target) is CancelShield:
        shield_owned(target, self)
        raise LanguageError('CancelShield 成员只读', code='attribute_error')
    from chaos.task_groups import TaskGroup, owned as group_owned
    if type(target) is TaskGroup:
        group_owned(target, self)
        raise LanguageError('TaskGroup 成员只读', code='attribute_error')
    from chaos.indexing import Slice
    if type(target) is Slice:
        raise LanguageError('Slice 字段只读', code='attribute_error')
    from chaos.objects import set_member
    return (yield from self._s_access_result(set_member(target, name, item, self)))

def _s_scoped_delete_member(self, target, name):
    if type(target) in ROBOT_VALUES:
        from chaos.robot_values import owned as robot_owned
        robot_owned(target, self)
        raise LanguageError('Robot members are read-only', code='attribute_error')
    if type(target) in (PromptTag, PromptScope, EscapeContinuation):
        from chaos.control_prompts import owned as control_owned
        control_owned(target, self)
        raise LanguageError('Control members are read-only', code='attribute_error')
    if type(target) is Error:
        from chaos.error_values import owned as error_owned
        error_owned(target, self)
        raise LanguageError('Error 成员只读', code='attribute_error')
    from chaos.resource_stacks import STACKS, owned as stack_owned
    if type(target) in STACKS:
        stack_owned(target, self)
        raise LanguageError('ExitStack 成员只读', code='attribute_error')
    from chaos.cancellation import CancelShield, owned as shield_owned
    if type(target) is CancelShield:
        shield_owned(target, self)
        raise LanguageError('CancelShield 成员只读', code='attribute_error')
    from chaos.task_groups import TaskGroup, owned as group_owned
    if type(target) is TaskGroup:
        group_owned(target, self)
        raise LanguageError('TaskGroup 成员只读', code='attribute_error')
    from chaos.indexing import Slice
    if type(target) is Slice:
        raise LanguageError('Slice 字段只读', code='attribute_error')
    from chaos.objects import delete_member
    return (yield from self._s_access_result(delete_member(target, name, self)))

def source_access(self, node, env, tail=False, *, all_results=False, _completion=None, _compiled_regions=None):
    from chaos.capture_frames import active as capture_active, retain as capture_retain
    capture = capture_active(self)
    self.tick()
    k = node[0]
    if k in ('subscript', 'set_item'):
        from chaos.indexing import Slice, ItemRequest, operation
        target = (yield from self._s_evaluate(node[1], env))
        key = (yield from self._s_item_key(node[2], env))
        item = (yield from self._s_evaluate(node[3], env)) if k == 'set_item' else None
        return (yield from operation(self, ItemRequest('set' if k == 'set_item' else 'get', target, key, item, True)))
    if k == 'set_member':
        target = (yield from self._s_evaluate(node[1], env))
        key = (yield from self._s_evaluate(node[2], env))
        value = (yield from self._s_evaluate(node[3], env))
        return (yield from self._s_set_member(target, key, value))
    if k == 'index':
        target = (yield from self._s_evaluate(node[1], env))
        key = (yield from self._s_evaluate(node[2], env))
        return (yield from self._s_index_value(target, key))
    if k == 'aug_assign':
        from chaos.operators import inplace
        from chaos.indexing import ItemRequest, operation as item_operation
        target_node, operator, rhs = node[1:]
        target_kind = target_node[0]
        if target_kind == 'name':
            location = env.resolve_cell(target_node[1])
            from chaos.metaclasses import lookup_name
            previous = (yield from lookup_name(self, env, target_node[1]))
        else:
            target = (yield from self._s_evaluate(target_node[1], env))
            key = (yield from self._s_item_key(target_node[2], env))
            if target_kind == 'subscript':
                previous = (yield from item_operation(self, ItemRequest('get', target, key)))
            else:
                previous = (yield from self._s_index_value(target, key))
        capture_retain(capture, previous)
        right = (yield from self._s_evaluate(rhs, env, _compiled_regions=_compiled_regions))
        value = (yield from inplace(self, operator, previous, right))
        self.tick()
        if target_kind == 'name':
            from chaos.metaclasses import assign_name
            yield from assign_name(self, env, target_node[1], check_type(value, location.contract))
        elif target_kind == 'subscript':
            yield from item_operation(self, ItemRequest('set', target, key, value, True))
        else:
            yield from self._s_set_member(target, key, value)
        return value
_ORIGINALS=[('attribute_hook', 'chaos.attribute_protocols', 'hook'), ('attribute_read', 'chaos.attribute_protocols', 'read'), ('attribute_write', 'chaos.attribute_protocols', 'write'), ('attribute_directory', 'chaos.attribute_protocols', 'directory'), ('index_integer_index', 'chaos.indexing', 'integer_index'), ('index_resolve_index', 'chaos.indexing', 'resolve_index'), ('index_invoke_item', 'chaos.indexing', 'invoke_item'), ('index_operation', 'chaos.indexing', 'operation'), ('_s_access_result', 'chaos.language', '_s_access_result'), ('_s_item_key', 'chaos.language', '_s_item_key'), ('_s_index_value', 'chaos.language', '_s_index_value'), ('_s_scoped_get_member', 'chaos.language', '_s_scoped_get_member'), ('_s_scoped_set_member', 'chaos.language', '_s_scoped_set_member'), ('_s_scoped_delete_member', 'chaos.language', '_s_scoped_delete_member')]

@contextmanager
def reference():
    import importlib
    from chaos.language import Evaluator
    from chaos.execution_access import ObjectAccessEvaluationFrame
    reached={key:0 for key,module,name in _ORIGINALS};reached["source_access"]=0
    reached.update({"source_access:"+kind:0 for kind in ("subscript","set_item","set_member","index","aug_assign")})
    replacements={}
    for key,module,name in _ORIGINALS:
        function=globals()[key]
        def called(*args,_function=function,_key=key,**kwargs):
            reached[_key]+=1;return _function(*args,**kwargs)
        replacements[key]=called
    current_execute=Evaluator._s_execute
    def execute(self,node,env,*args,**kwargs):
        if node[0] in {"subscript","set_item","set_member","index","aug_assign"}:
            reached["source_access"]+=1;reached["source_access:"+node[0]]+=1;return source_access(self,node,env,*args,**kwargs)
        return current_execute(self,node,env,*args,**kwargs)
    with ExitStack() as stack:
        for key,module,name in _ORIGINALS:
            owner=Evaluator if name.startswith("_s_") else importlib.import_module(module)
            stack.enter_context(patch.object(owner,name,replacements[key]))
            stack.enter_context(patch.dict(globals(),{name:replacements[key]}))
        stack.enter_context(patch.object(ObjectAccessEvaluationFrame,"KINDS",set()))
        stack.enter_context(patch.object(Evaluator,"_s_execute",execute))
        yield reached
