"""Owned module loading algorithms; compilation and scheduling stay native.

All public native generators retain their own thin transport loop. The shared
NativeAlgorithmCursor owns protocol completion/close, never an old algorithm.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleFrameRequest:
    operation: str
    values: tuple


def module_cursor(evaluator, operation, *values):
    from .execution_frames import ModuleCloseCursor, link_observer
    frame = module_frame(ModuleFrameRequest(operation, values))
    cursor = ModuleCloseCursor(evaluator, frame)
    link_observer(evaluator, cursor)
    return cursor


def _child_transport(frame, iterator):
    value = yield from iterator
    frame.native_result = value
    frame.native_completed = True
    return value


class _ModuleFrame:
    PHASES = ('start', 'done')
    ROOTS = ()

    def __init__(self):
        self.phase = 'start'; self.capabilities = None; self.epoch = None
        self.dependencies = None; self.native_completed = False; self.native_result = None

    def bind(self):
        from . import module_values, language
        return module_values, language

    def roots(self):
        from .capture_frames import Edge
        from .language import LanguageError
        def malformed():
            raise LanguageError('Malformed module execution state', code='ownership_error')
        if type(self.phase) is not str or self.phase not in self.PHASES or type(self.native_completed) is not bool:
            malformed()
        if self.epoch is not None or self.phase != 'start':
            if type(self.epoch) is not int or self.epoch != self.owner.suspension.epoch:
                malformed()
        if self.capabilities is not None and (type(self.capabilities) is not frozenset or any(type(x) is not str for x in self.capabilities)):
            malformed()
        self.validate_metadata(malformed)
        values = tuple(getattr(self, name, None) for name in self.ROOTS) + (self.native_result,)
        return (Edge(self.owner, self.capabilities, values),)

    def validate_metadata(self, malformed):
        pass

    def leaf(self, iterator):
        from .execution_frames import Push, ProtocolBridge, ExecutionCursor, GenericCloseCursor, PatternCloseCursor, ModuleCloseCursor
        self.native_completed = False; self.native_result = None
        bridge = ProtocolBridge(_child_transport(self, iterator))
        bridge.owner = self.owner; bridge.supplementary = True
        if type(iterator) in (ExecutionCursor, GenericCloseCursor, PatternCloseCursor, ModuleCloseCursor):
            if iterator.owner is not self.owner or iterator.parent_bridge is not None:
                from .language import LanguageError
                raise LanguageError('Module child cursor is already owned or foreign', code='ownership_error')
            bridge.pending_cursor = iterator; iterator.parent_bridge = bridge
        return Push(bridge)

    def done(self, value):
        from .execution_frames import Done
        self.phase = 'done'
        return Done(value)

    def step(self, cursor, value, error):
        from .execution_frames import ModuleCloseCursor
        previous = getattr(cursor.owner, '_execution_cursor', None)
        if type(cursor) is ModuleCloseCursor:
            cursor.owner._execution_cursor = cursor.ambient_cursor
        try:
            try:
                try:
                    if self.dependencies is None:
                        if error is not None:
                            raise error
                        self.dependencies = self.bind()
                    if type(self.phase) is not str or self.phase not in self.PHASES or type(self.native_completed) is not bool:
                        from .language import LanguageError
                        raise LanguageError('Malformed module control state', code='ownership_error')
                    self.native_completed = False; self.native_result = None
                    return self.advance(cursor, value, error)
                except BaseException:
                    self.retire()
                    raise
            except StopIteration as failure:
                raise RuntimeError('generator raised StopIteration') from failure
        finally:
            cursor.owner._execution_cursor = previous

    def retire(self):
        pass

    def close(self, cursor):
        self.retire()


class ModuleLoadFrame(_ModuleFrame):
    PHASES = ('start', 'evaluate', 'resolve', 'exports', 'done')
    ROOTS = ('scope', 'bindings_env', 'environment', 'value', 'exports')

    def __init__(self, scope, name):
        super().__init__(); self.scope, self.name = scope, name
        self.bindings_env = self.environment = self.value = self.exports = None
        self.statements = self.current_node = None; self.index = 0
        self.debug_frame = None; self.entered = False

    def validate_metadata(self, malformed):
        from .module_values import Scope
        from .language import Env, Node
        if type(self.scope) is not Scope or self.scope.evaluator is not self.owner or type(self.name) is not str:
            malformed()
        if type(self.index) is not int or self.index < 0 or type(self.entered) is not bool:
            malformed()
        if any(x is not None and type(x) is not Env for x in (self.bindings_env, self.environment)):
            malformed()
        if self.statements is not None and type(self.statements) not in (list, tuple):
            malformed()
        if self.current_node is not None and (type(self.current_node) not in (Node, tuple) or not self.current_node):
            malformed()
        if self.exports is not None and (type(self.exports) is not dict or any(type(x) is not str for x in self.exports)):
            malformed()
        if self.debug_frame is not None:
            row = self.debug_frame
            if type(row) is not dict or set(row) != {'id','name','span','env','taskId'} or type(row['id']) is not str or type(row['name']) is not str or type(row['span']) is not dict or type(row['env']) is not Env or row['taskId'] is not None and type(row['taskId']) is not str:
                malformed()

    def next_statement(self, e):
        if self.index < len(self.statements):
            self.current_node = self.statements[self.index]; self.index += 1
            self.phase = 'evaluate'
            return self.leaf(e._s_evaluate(self.current_node, self.environment))
        self.phase = 'exports'; self.exports = {}
        for key in self.scope.bundle.exports[self.name]:
            self.exports[key] = self.environment.get(key)
        self.scope.cache[self.name] = self.exports
        result = self.exports
        self.retire()
        return self.done(result)

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        m, _ = self.dependencies; e = cursor.owner
        if self.phase == 'start':
            self.epoch = e.suspension.epoch
            if self.name in self.scope.cache:
                return self.done(self.scope.cache[self.name])
            if self.name in self.scope.loading:
                raise m.LanguageError('模块初始化循环', code='module_error')
            if self.name not in self.scope.bundle.trees:
                raise m.LanguageError('模块不在封闭包内', code='module_error')
            e.tick(); self.scope.loading.add(self.name)
            self.bindings_env = m.Env(e.env)
            for key, item in self.scope.bindings:
                self.bindings_env.bind(key, item)
            self.environment = m.Env(self.bindings_env)
            e.frame_sequence += 1
            task = e.suspension.current.task if e.suspension.current else None
            self.debug_frame = {'id':'module-frame-'+str(e.frame_sequence), 'name':'<module '+self.name+'>', 'span':dict(e.current_span), 'env':self.environment, 'taskId':task.id if task else None}
            e.frames.append(self.debug_frame)
            # This is precisely the original try boundary, after all prefixes.
            self.entered = True
            self.statements = self.scope.bundle.expanded_trees[self.name][1]
            return self.next_statement(e)
        if self.phase == 'evaluate':
            self.value = value; self.phase = 'resolve'
            return self.leaf(e._s_resolve(self.value))
        if self.phase == 'resolve':
            # The original yield-from result is discarded, not a new live root.
            self.value = None
            return self.next_statement(e)
        raise RuntimeError('Invalid module-load phase')

    def retire(self):
        if not self.entered:
            return
        self.entered = False
        self.scope.loading.discard(self.name)
        e = self.owner
        if self.debug_frame in e.frames:
            e.frames.remove(self.debug_frame)


class ModuleImportFrame(_ModuleFrame):
    PHASES = ('start', 'load', 'done')
    ROOTS = ('scope', 'node')

    def __init__(self, scope, node):
        super().__init__(); self.scope, self.node = scope, node
        self.file = self.target = None

    def validate_metadata(self, malformed):
        from .module_values import Scope
        from .language import Node
        if type(self.scope) is not Scope or self.scope.evaluator is not self.owner or type(self.node) not in (Node, tuple) or not self.node:
            malformed()
        if self.file is not None and type(self.file) is not str or self.target is not None and type(self.target) is not str:
            malformed()

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        if self.phase == 'start':
            self.epoch = cursor.owner.suspension.epoch
            self.file = self.node._module_file
            self.target = self.scope.bundle.dependencies.get(self.file, {}).get(self.node[1])
            if self.target is None:
                raise self.dependencies[0].LanguageError('导入不在封闭依赖图中', code='module_error')
            self.phase = 'load'
            return self.leaf(self.scope.load(self.target))
        return self.done(value)


class ModuleConstructFrame(_ModuleFrame):
    PHASES = ('start', 'load', 'done')
    ROOTS = ('request', 'scope')

    def __init__(self, request):
        super().__init__(); self.request = request; self.scope = None

    def validate_metadata(self, malformed):
        from .module_values import Scope, Request
        from .program_values import Program
        if type(self.request) is not Request or type(self.request.program) is not Program or self.request.program.owner is not self.owner:
            malformed()
        if type(self.request.bindings) is not tuple or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str for row in self.request.bindings):
            malformed()
        if self.scope is not None and (type(self.scope) is not Scope or self.scope.evaluator is not self.owner):
            malformed()

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        if self.phase == 'start':
            self.epoch = cursor.owner.suspension.epoch
            self.scope = self.dependencies[0].Scope(cursor.owner, self.request)
            self.phase = 'load'
            return self.leaf(self.scope.load(self.scope.bundle.entry))
        return self.done(value)


class ModuleSyntaxFrame(_ModuleFrame):
    KINDS = {'import', 'export'}
    PHASES = ('start', 'imported', 'done')
    ROOTS = ('node', 'env', 'scope')

    def __init__(self, node, env):
        super().__init__(); self.node, self.env = node, env
        self.scope = self.target = None

    def bind(self):
        from .suspension import Await, Yield, Generator, AsyncGenerator, GeneratorClose
        from .capture_frames import active, reset, retain
        from . import module_values, language
        return module_values, language, active

    def validate_metadata(self, malformed):
        from .language import Env, Node
        from .module_values import Scope
        if type(self.env) is not Env or type(self.node) not in (Node, tuple) or not self.node or self.node[0] not in self.KINDS:
            malformed()
        if self.scope is not None and (type(self.scope) is not Scope or self.scope.evaluator is not self.owner):
            malformed()
        if self.target is not None and type(self.target) is not str:
            malformed()

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner
        if self.phase == 'start':
            self.epoch = e.suspension.epoch
            self.dependencies[2](e); e.tick()
            if self.node[0] == 'export':
                return self.done(None)
            self.scope = getattr(self.node, '_module_scope', None)
            if self.scope is not None:
                self.phase = 'imported'
                return self.leaf(self.scope.imported(self.node))
            from .modules import resolve_path
            self.target = resolve_path(self.node[1], getattr(self.node, 'span', {}).get('file', e.bundle.entry))
            return self.done(self.env.bind(self.node[2], e.load_module(self.target)))
        return self.done(self.env.bind(self.node[2], value))


class ModuleExportFrame(_ModuleFrame):
    PHASES = ('start', 'invoke', 'await', 'done')
    ROOTS = ('target', 'value', 'result')

    def __init__(self, target, value):
        super().__init__(); self.target, self.value = target, value
        self.result = None

    def bind(self):
        from .suspension import Task, Await
        return Task, Await

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner; Task, Await = self.dependencies
        if self.phase == 'start':
            self.epoch = e.suspension.epoch; self.phase = 'invoke'
            return self.leaf(e._s_invoke(self.target, [self.value]))
        if self.phase == 'invoke':
            self.result = value
            if isinstance(self.result, Task):
                e.suspension.validate(self.result, Task)
                self.phase = 'await'
                from .execution_frames import request_proxy
                return self.leaf(request_proxy(Await(self.result)))
        else:
            self.result = value
        from .multiple_values import primary, prepare
        return self.done(primary(prepare(e, self.result), e))


class LegacyModuleLoadFrame:
    """Synchronous coordinator; no extra Runner and no PEP479 boundary."""
    PHASES = ('start', 'evaluate', 'resolve', 'exports', 'done')

    def __init__(self, name):
        self.name = name; self.phase = 'start'; self.index = 0
        self.bindings_env = self.environment = self.value = self.result = self.exports = None
        self.statements = self.current_node = None
        self.capabilities = None; self.epoch = None
        self.attachments = []

    def attach(self, cursor):
        from .execution_frames import ExecutionCursor
        if type(cursor) is not ExecutionCursor or cursor.owner is not self.owner:
            raise RuntimeError('Foreign legacy module cursor attachment')
        present = hasattr(cursor, 'module_state')
        previous = getattr(cursor, 'module_state', None)
        if previous is self:
            return
        if previous is not None and (type(previous) is not LegacyModuleLoadFrame or previous.owner is not self.owner):
            raise RuntimeError('Foreign existing legacy module attachment')
        self.attachments.append((cursor, present, previous))
        cursor.module_state = self

    def detach(self):
        for cursor, present, previous in reversed(self.attachments):
            if getattr(cursor, 'module_state', None) is self:
                if present:
                    cursor.module_state = previous
                else:
                    del cursor.module_state
        self.attachments.clear()

    def roots(self):
        from .capture_frames import Edge
        from .language import LanguageError, Env, Node
        if type(self.phase) is not str or self.phase not in self.PHASES or type(self.name) is not str or type(self.index) is not int or self.index < 0:
            raise LanguageError('Malformed legacy module state', code='ownership_error')
        if type(self.epoch) is not int or self.epoch != self.owner.suspension.epoch:
            raise LanguageError('Legacy module frame cannot cross epoch', code='ownership_error')
        if any(x is not None and type(x) is not Env for x in (self.bindings_env,self.environment)):
            raise LanguageError('Malformed legacy module environments', code='ownership_error')
        if self.statements is not None and type(self.statements) not in (list,tuple) or self.current_node is not None and type(self.current_node) not in (Node,tuple):
            raise LanguageError('Malformed legacy module syntax', code='ownership_error')
        if self.capabilities is not None and (type(self.capabilities) is not frozenset or any(type(x) is not str for x in self.capabilities)):
            raise LanguageError('Malformed legacy module authority', code='ownership_error')
        from .execution_frames import ExecutionCursor
        if type(self.attachments) is not list or any(type(row) is not tuple or len(row) != 3 or type(row[0]) is not ExecutionCursor or row[0].owner is not self.owner or type(row[1]) is not bool or row[2] is not None and (type(row[2]) is not LegacyModuleLoadFrame or row[2].owner is not self.owner) for row in self.attachments):
            raise LanguageError('Malformed legacy module attachment ledger', code='ownership_error')
        live_result = self.result if self.phase in ('exports','done') else None
        return (Edge(self.owner,self.capabilities,(self.bindings_env,self.environment,self.value,live_result,self.exports)+tuple(row[2] for row in self.attachments)),)

    def step(self, cursor, value, error):
        if error is not None:
            raise error
        e = self.owner
        from . import language
        from .execution_frames import Done
        if type(self.phase) is not str or self.phase not in self.PHASES or type(self.index) is not int or self.index < 0:
            raise language.LanguageError('Malformed legacy module control state', code='ownership_error')
        if self.epoch is None:
            self.epoch = e.suspension.epoch
        if self.phase == 'start':
            if self.name in e.module_cache:
                self.phase = 'done'; return Done(e.module_cache[self.name])
            from .agent_tools import current_caps
            self.capabilities = current_caps(e)
            e.tick(); self.bindings_env = language.Env(e.env)
            for key, item in e.module_bindings.items():
                self.bindings_env.bind(key, e.copy_value(item))
            self.environment = language.Env(self.bindings_env)
            self.statements = getattr(e.bundle, 'expanded_trees', e.bundle.trees)[self.name][1]
            self.phase = 'evaluate'
        if self.phase == 'resolve':
            self.result = e.resolve(self.value)
            self.value = None; self.current_node = None
            self.phase = 'evaluate'
        if self.index < len(self.statements):
            self.current_node = self.statements[self.index]; self.index += 1
            self.value = e.evaluate(self.current_node, self.environment)
            self.phase = 'resolve'
            return None
        self.phase = 'exports'; self.exports = {}
        for key in e.bundle.exports[self.name]:
            self.exports[key] = self.environment.get(key)
        e.module_cache[self.name], e.module_results[self.name] = self.exports, self.result
        self.phase = 'done'; return Done(self.exports)

    def close(self, cursor):
        pass


def legacy_load(evaluator, name):
    from .execution_frames import Done
    frame = LegacyModuleLoadFrame(name); frame.owner = evaluator
    frame.epoch = evaluator.suspension.epoch
    previous = getattr(evaluator, '_module_sync_state', None)
    evaluator._module_sync_state = frame
    try:
        while True:
            action = frame.step(None, None, None)
            if type(action) is Done:
                return action.value
    finally:
        try:
            frame.detach()
        finally:
            evaluator._module_sync_state = previous


MODULE_FRAMES = (ModuleLoadFrame, ModuleImportFrame, ModuleConstructFrame,
                 LegacyModuleLoadFrame, ModuleSyntaxFrame, ModuleExportFrame)


def module_frame(request):
    if type(request) is not ModuleFrameRequest:
        return None
    operations = {'load':(ModuleLoadFrame,2), 'imported':(ModuleImportFrame,2),
                  'construct':(ModuleConstructFrame,1),
                  'syntax':(ModuleSyntaxFrame,2), 'export-run':(ModuleExportFrame,2)}
    if type(request.operation) is not str or request.operation not in operations:
        raise RuntimeError('Unknown module execution operation')
    cls, count = operations[request.operation]
    if type(request.values) is not tuple or len(request.values) != count:
        from .language import LanguageError
        raise LanguageError('Malformed module execution request', code='ownership_error')
    return cls(*request.values)


def module_carrier_roots(evaluator, request):
    from .language import LanguageError
    frame = module_frame(request)
    frame.owner = evaluator
    # Cold request schema is validated before any stateful algorithm prefix.
    if type(frame) is LegacyModuleLoadFrame:
        from .agent_tools import current_caps
        frame.epoch = evaluator.suspension.epoch; frame.capabilities = current_caps(evaluator)
    return frame.roots()
