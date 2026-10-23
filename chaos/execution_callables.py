"""Authoritative callable formation and complete generic method combination.

The public gateways remain late bound. These frames own the algorithm operands,
default/definition loops and method scopes, never a legacy whole generator.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CallableFrameRequest:
    operation: str
    values: tuple


def callable_protocol(evaluator, operation, *values):
    from .execution_frames import request_proxy, ensure_cursor
    proxy = request_proxy(CallableFrameRequest(operation, values))
    if getattr(evaluator, '_execution_cursor', None) is not None:
        return proxy
    return ensure_cursor(evaluator, proxy, observer=True)


class _CallableFrame:
    capabilities = None
    ROOTS = ()
    ENTRY_ROOTS = ('request', 'node', 'env', 'environment', 'shared')
    def __init__(self):
        self.phase = 'start'; self.result = None; self.dependencies = None
        self.authority_entered = False; self.active_caps = None
    def bind(self):
        return {}
    def roots(self):
        from .capture_frames import Edge
        from .generic_functions import intersection
        from .language import LanguageError
        def malformed():
            raise LanguageError('Malformed callable execution state', code='ownership_error')
        if type(self.phase) is not str or type(self.authority_entered) is not bool or hasattr(self, 'close_suppressed') and type(self.close_suppressed) is not bool:
            malformed()
        if hasattr(self, 'index') and type(self.index) is not int:
            malformed()
        for ceiling in (self.capabilities, self.active_caps, getattr(self, 'previous_authority', None)):
            if ceiling is not None and (type(ceiling) is not frozenset or any(type(item) is not str for item in ceiling)):
                malformed()
        runner = getattr(self, 'authority_runner', None)
        if runner is not None:
            from .suspension import Runner
            from .execution_frames import ExecutionCursor
            if type(runner) is not Runner or type(runner.iterator) is not ExecutionCursor or runner.iterator.owner is not self.owner or type(self.authority_epoch) is not int or self.authority_epoch != self.owner.suspension.epoch:
                malformed()
        if hasattr(self, 'names') and (type(self.names) is not list or any(type(name) is not str for name in self.names)):
            malformed()
        for name in ('parameter_name', 'parameter_mode'):
            if hasattr(self, name) and getattr(self, name) is not None and type(getattr(self, name)) is not str:
                malformed()
        request = getattr(self, 'request', None)
        from .generic_functions import GenericRequest, Descriptor
        from .code_values import FunctionBuildRequest, Code
        from types import MappingProxyType
        if type(request) is GenericRequest:
            if type(request.descriptor) is not Descriptor or request.descriptor.owner is not self.owner or type(request.args) is not tuple or type(request.kwargs) not in (dict, MappingProxyType) or any(type(name) is not str for name in request.kwargs) or type(request.index) is not int or type(request.phase) is not str:
                malformed()
        if type(request) is FunctionBuildRequest:
            if type(request.code) is not Code or request.code.owner is not self.owner or any(type(getattr(request.code, name)) is not str for name in ('source', 'ir_json', 'info_json')) or type(request.bindings) is not tuple or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str for row in request.bindings):
                malformed()
        result = []
        for name in self.ROOTS + ('result', 'previous_authority'):
            value = getattr(self, name, None)
            if name == 'params':
                if type(value) is not list or any(type(row) is not tuple or len(row) not in (4, 5) or type(row[0]) is not str or type(row[2]) is not str for row in value):
                    malformed()
                values = tuple(child for row in value for child in (row[1], row[3]))
            elif name == 'parameter':
                if value is not None and (type(value) not in (tuple, list) or len(value) not in (2, 4, 5)):
                    malformed()
                values = tuple(value) if value is not None else ()
            elif name == 'functions':
                if type(value) is not list:
                    malformed()
                values = tuple(value)
            else:
                values = (value,)
            ceiling = self.capabilities if name in self.ENTRY_ROOTS or name == 'previous_authority' else intersection(self.capabilities, self.active_caps)
            result.append(Edge(self.owner, ceiling, values))
        return tuple(result)
    def enter_scope(self, evaluator, ceiling, factory):
        self.authority_runner = evaluator.suspension.current
        self.authority_epoch = evaluator.suspension.epoch
        self.previous_authority = getattr(self.authority_runner, 'capabilities', None)
        self.scope_manager = factory(evaluator, ceiling)
        self.scope_manager.__enter__()
        self.authority_entered = True
        self.active_caps = getattr(self.authority_runner, 'capabilities', None)
    def retire(self, error=None):
        if not self.authority_entered:
            return False
        self.authority_entered = False
        try:
            result = self.scope_manager.__exit__(type(error) if error is not None else None, error, getattr(error, '__traceback__', None))
            return bool(result) if error is not None else False
        finally:
            self.active_caps = None
    def done(self, value):
        from .execution_frames import Done
        self.result = value; self.phase = 'done'
        return Done(value)
    def step(self, cursor, value, error):
        try:
            if self.dependencies is None:
                if error is not None:
                    raise error
                self.dependencies = self.bind()
            return self.advance(cursor, value, error)
        except BaseException as failure:
            if self.retire(failure):
                return self.suppressed(cursor)
            raise
    def suppressed(self, cursor):
        return self.done(None)
    def close(self, cursor):
        if self.retire(GeneratorExit()):
            self.suppressed(cursor)
    def leaf(self, iterator):
        from .execution_frames import Push, ProtocolBridge
        bridge = ProtocolBridge(iterator); bridge.supplementary = True
        return Push(bridge)
    def evaluate(self, evaluator, node, env):
        return self.leaf(evaluator._s_evaluate(node, env))


class GenericResolveFrame(_CallableFrame):
    ENTRY_ROOTS = _CallableFrame.ENTRY_ROOTS + ('descriptor', 'combination', 'call', 'primary_result')
    ROOTS = ('request', 'descriptor', 'combination', 'method', 'call', 'primary_result')
    def __init__(self, request):
        super().__init__(); self.request = request
        self.descriptor = self.combination = self.method = self.call = self.primary_result = None
        self.index = 0; self.close_suppressed = False
    def bind(self):
        from .agent_tools import scope
        from . import generic_functions as generic
        return {'scope': scope, 'generic': generic}
    def step(self, cursor, value, error):
        # This frame replaces one native generator algorithm. Its real scopes
        # get the first opportunity to suppress the original exception; only a
        # StopIteration escaping the complete algorithm gets PEP479 conversion.
        try:
            return super().step(cursor, value, error)
        except StopIteration as failure:
            raise RuntimeError('generator raised StopIteration') from failure
    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner; g = self.dependencies['generic']; request = self.request
        if self.phase == 'start':
            self.descriptor = request.descriptor
            if type(self.descriptor) is not g.Descriptor or self.descriptor.owner is not e:
                g.fail('Generic invocation cannot cross evaluators', 'ownership_error')
            self.combination = request.combination or g.select(e, self.descriptor, request.args)
            if request.phase == 'primary':
                return self.done(g.primary_call(e, request, self.combination, request.index))
            if request.index < len(self.combination.around):
                self.method = self.combination.around[request.index]
                # A synchronous TailCall carrier is returned after this scope
                # exits. Do not extend this scope into actual method execution.
                with self.dependencies['scope'](e, self.method.capabilities):
                    next_call = g.continuation(e, request, self.combination, request.index + 1, 'around')
                    call = g.delegate(e, self.method, [next_call] + list(request.args), request.kwargs)
                    return self.done(call)
            self.index = 0; self.phase = 'before'
        if self.phase == 'before_return':
            if value is not None:
                g.fail('Generic before/after methods must return Null; no result is implicitly awaited', 'generic_protocol_error', value)
            self.retire(); self.index += 1; self.phase = 'before'
        if self.phase == 'before':
            if self.index < len(self.combination.before):
                self.method = self.combination.before[self.index]
                self.enter_scope(e, self.method.capabilities, self.dependencies['scope'])
                self.phase = 'before_return'
                return self.leaf(e._s_invoke(self.method.implementation, list(request.args), request.kwargs, synchronous=True))
            self.call = g.primary_call(e, request, self.combination, 0)
            if not self.combination.after:
                return self.done(self.call)
            self.phase = 'primary_return'
            return self.leaf(e._s_invoke(self.call.function, self.call.args, self.call.kwargs, all_results=True))
        if self.phase == 'primary_return':
            self.primary_result = value; self.index = 0; self.phase = 'after'
        if self.phase == 'after_return':
            if value is not None:
                g.fail('Generic before/after methods must return Null; no result is implicitly awaited', 'generic_protocol_error', value)
            self.retire(); self.index += 1; self.phase = 'after'
        if self.phase == 'after':
            if self.index < len(self.combination.after):
                self.method = self.combination.after[self.index]
                self.enter_scope(e, self.method.capabilities, self.dependencies['scope'])
                self.phase = 'after_return'
                return self.leaf(e._s_invoke(self.method.implementation, list(request.args), request.kwargs, synchronous=True))
            return self.done(self.primary_result)
        raise RuntimeError('Invalid generic combination phase')
    def suppressed(self, cursor):
        if self.phase in ('before_return', 'after_return'):
            self.index += 1
            self.phase = 'before' if self.phase == 'before_return' else 'after'
            return self.step(cursor, None, None)
        return self.done(None)
    def close_transition(self, cursor, error=None):
        from .execution_frames import Done
        try:
            error = GeneratorExit() if error is None else error
            self.close_suppressed = self.retire(error)
            if self.close_suppressed:
                return self.suppressed(cursor)
            if isinstance(error, GeneratorExit):
                return Done()
            raise error
        except StopIteration as failure:
            raise RuntimeError('generator raised StopIteration') from failure
    def close(self, cursor):
        action = self.close_transition(cursor)
        from .execution_frames import Done, Push
        if type(action) is Done:
            return
        # The original generator would reach another protocol yield after its
        # context suppresses GeneratorExit, which close() must reject. No new
        # language callback body is executed by this rejected transport.
        try:
            if type(action) is Push:
                action.frame.close(cursor)
        finally:
            self.retire(GeneratorExit())
        raise RuntimeError('generator ignored GeneratorExit')


class FunctionBuildFrame(_CallableFrame):
    ROOTS = ('request', 'environment', 'node')
    def __init__(self, request):
        super().__init__(); self.request = request
        self.environment = self.node = None
    def bind(self):
        from . import code_values as code
        return {'code': code}
    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner; c = self.dependencies['code']; request = self.request
        if self.phase == 'start':
            c.owned(request.code, e)
            from .class_creation import validate_value
            self.environment = c.Env(e.env)
            for name, item in request.bindings:
                validate_value(e, item); self.environment.bind(name, item)
            ir = c.json.loads(request.code.ir_json)
            tree = c.parsed(request.code.source, ir['file'], e.tick, ir['languageVersion'] == 1)
            self.node, _ = c.function_node(tree)
            from .generated_sources import Source, register, tag
            info = c.json.loads(request.code.info_json)
            descriptor = Source(ir['file'], request.code.source, ir['languageVersion'], info['sourceHash'], info['semanticHash'])
            register(e, descriptor); tag(tree, descriptor)
            self.phase = 'evaluate'
            return self.evaluate(e, self.node, self.environment)
        if type(value) is not c.Function:
            c.error('Code 构造未得到普通语言 Function')
        return self.done(value)


class CallableSyntaxFrame(_CallableFrame):
    KINDS = {'fn', 'gen_fn', 'async_fn', 'async_gen_fn', 'def', 'rec', 'agent'}
    ROOTS = ('node', 'env', 'params', 'contract', 'functions', 'shared', 'function', 'value', 'parameter')
    def __init__(self, node, env):
        super().__init__(); self.node = node; self.env = env
        self.params = []; self.functions = []; self.contract = self.shared = self.function = self.value = None
        self.index = 0; self.parameter = None; self.parameter_name = self.parameter_mode = None; self.names = []
    def bind(self):
        # These original _s_execute entry imports occur on first advance only.
        from .suspension import Await, Yield, Generator, AsyncGenerator, GeneratorClose
        from .capture_frames import active, reset, retain
        from . import language
        return {'active': active, 'language': language}
    def append_parameter(self, value):
        parameter = self.parameter; name = self.parameter_name; mode = self.parameter_mode
        self.params.append((name, self.contract, mode, value, parameter[4]) if len(parameter) == 5 else (name, self.contract, mode, value))
        self.index += 1; self.phase = 'parameters'
    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner; l = self.dependencies['language']; node = self.node; kind = node[0]
        if self.phase == 'start':
            self.dependencies['active'](e)
            e.tick()
            if kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
                from .parameters import validate_parameters
                validate_parameters(node[1], tick=e.tick)
                self.phase = 'parameters'
            elif kind == 'def':
                self.phase = 'definition'; return self.evaluate(e, node[2], self.env)
            elif kind == 'rec':
                self.names = [definition[1] for definition in node[1]]
                if any(name in self.env.values for name in self.names):
                    raise l.LanguageError('rec 名称与当前作用域冲突')
                self.shared = self.env.snapshot(); self.phase = 'rec_next'
            elif kind == 'agent':
                self.phase = 'agent'; return self.evaluate(e, node[2], self.env)
        if self.phase == 'default':
            self.value = l.check_type(value, self.contract)
            self.append_parameter(self.value)
        while self.phase == 'parameters':
            if self.index >= len(node[1]):
                returns = e.contract(node[2], self.env)
                return self.done(l.Function(self.params, returns, node[3], self.env.snapshot(), node[4], e, kind, getattr(node, 'macro_bindings', None)))
            self.parameter = node[1][self.index]
            self.parameter_name, typ = self.parameter[:2]
            self.parameter_mode, default = self.parameter[2:4] if len(self.parameter) >= 4 else ('positional', None)
            self.contract = e.contract(typ, self.env)
            if default is None:
                self.append_parameter(l.MISSING)
            else:
                self.phase = 'default'; return self.evaluate(e, default, self.env)
        if self.phase == 'definition':
            self.function = value; value.env.values[node[1]] = value
            return self.done(self.env.bind(node[1], value))
        if self.phase == 'rec_definition':
            self.function = value; value.env = self.shared; self.functions.append(value)
            self.index += 1; self.phase = 'rec_next'
        if self.phase == 'rec_next':
            if self.index < len(node[1]):
                self.phase = 'rec_definition'; return self.evaluate(e, node[1][self.index][2], self.env)
            # Preserve both original loops, including partial publication if a
            # trusted direct AST supplies duplicate names. No added rollback.
            for name, function in zip(self.names, self.functions): self.shared.values[name] = function
            for name, function in zip(self.names, self.functions): self.env.bind(name, function)
            return self.done(None)
        if self.phase == 'agent':
            self.value = value
            return self.done(self.env.bind(node[1], l.Agent(node[1], value, e)))
        raise RuntimeError('Invalid callable syntax phase')


CALLABLE_FRAMES = (GenericResolveFrame, FunctionBuildFrame, CallableSyntaxFrame)
_OPERATIONS = {'generic': ('GenericResolveFrame', 1), 'build': ('FunctionBuildFrame', 1), 'syntax': ('CallableSyntaxFrame', 2)}


def request_values(request):
    from .language import LanguageError
    if type(request.operation) is not str or request.operation not in _OPERATIONS or type(request.values) is not tuple or len(request.values) != _OPERATIONS[request.operation][1]:
        raise LanguageError('Malformed callable frame request', code='ownership_error')
    return request.values


def callable_frame(request):
    if type(request) is not CallableFrameRequest:
        return None
    values = request_values(request)
    return globals()[_OPERATIONS[request.operation][0]](*values)


def callable_carrier_roots(evaluator, request):
    from .capture_frames import Edge
    return (Edge(evaluator, None, request_values(request)),)


def generic_cursor(evaluator, request):
    from .execution_frames import GenericCloseCursor, link_observer
    cursor = GenericCloseCursor(evaluator, GenericResolveFrame(request))
    link_observer(evaluator, cursor)
    return cursor
