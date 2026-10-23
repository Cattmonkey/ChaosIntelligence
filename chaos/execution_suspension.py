"""Proposed authoritative suspension-expression and error-transfer algorithms.

Only this selected syntax owns new state. Existing scheduler, resource Await,
and stream delegation remain their original implementations. No worker runs AST.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SuspensionFrameRequest:
    operation: str
    values: tuple


def _child_transport(frame, iterator):
    # Native yield-from decides lawful completion versus delegate.close failure.
    value = yield from iterator
    frame.native_result = value
    frame.native_completed = True
    return value


class SuspensionSyntaxFrame:
    KINDS = {'await', 'yield', 'yield_from', 'throw', 'rethrow'}
    PHASES = {'start', 'operand', 'await_return', 'yield_return', 'delegate_return', 'done'}

    def __init__(self, node, env):
        self.node, self.env = node, env
        self.phase = 'start'; self.kind = None; self.operand = None
        self.asynchronous = None; self.capabilities = None; self.epoch = None
        self.dependencies = None; self.native_completed = False; self.native_result = None

    def malformed(self):
        from .language import LanguageError
        raise LanguageError('Malformed suspension expression state', code='ownership_error')

    def validate_state(self):
        if type(self.phase) is not str or self.phase not in self.PHASES:
            self.malformed()
        if self.kind is not None and (type(self.kind) is not str or self.kind not in self.KINDS):
            self.malformed()
        if type(self.native_completed) is not bool:
            self.malformed()
        if self.asynchronous is not None and type(self.asynchronous) is not bool:
            self.malformed()
        if self.phase == 'operand' and self.kind not in self.KINDS - {'rethrow'}:
            self.malformed()
        resumes = {'await_return':'await', 'yield_return':'yield', 'delegate_return':'yield_from'}
        if self.phase in resumes and self.kind != resumes[self.phase]:
            self.malformed()
        if self.epoch is not None or self.phase != 'start':
            if type(self.epoch) is not int or self.epoch != self.owner.suspension.epoch:
                self.malformed()
        if self.capabilities is not None and (type(self.capabilities) is not frozenset or any(type(x) is not str for x in self.capabilities)):
            self.malformed()

    def roots(self):
        from .capture_frames import Edge
        from .language import Env, Node
        from types import ModuleType, FunctionType, BuiltinFunctionType
        self.validate_state()
        if type(self.node) not in (tuple,Node) or not self.node or type(self.node[0]) is not str or self.node[0] not in self.KINDS or type(self.env) is not Env:
            self.malformed()
        if self.dependencies is not None:
            if type(self.dependencies) is not tuple or len(self.dependencies) != 5:
                self.malformed()
            language, active, awaited, yielded, async_generator = self.dependencies
            if type(language) is not ModuleType or language.__name__ != __package__+'.language':
                self.malformed()
            # Local imported host bindings are metadata, never reflected closures.
            if type(active) not in (FunctionType,BuiltinFunctionType) or any(type(alias) not in (type,FunctionType,BuiltinFunctionType) for alias in (awaited,yielded,async_generator)):
                self.malformed()
        return (Edge(self.owner,self.capabilities,(self.node,self.env,self.operand,self.native_result)),)

    def bind(self):
        # Preserve first-advance local import bindings and the original prefix.
        from .suspension import Await, Yield, Generator, AsyncGenerator, GeneratorClose
        from .capture_frames import active, reset, retain
        from . import language
        return language, active, Await, Yield, AsyncGenerator

    def leaf(self, iterator):
        from .execution_frames import (Push, ProtocolBridge, ExecutionCursor,
                                       GenericCloseCursor, PatternCloseCursor,
                                       ModuleCloseCursor, SuspensionCloseCursor)
        self.native_completed = False; self.native_result = None
        bridge = ProtocolBridge(_child_transport(self,iterator))
        bridge.owner = self.owner; bridge.supplementary = True
        if type(iterator) in (ExecutionCursor,GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor):
            if iterator.owner is not self.owner or iterator.parent_bridge is not None:
                self.malformed()
            bridge.pending_cursor = iterator; iterator.parent_bridge = bridge
        return Push(bridge)

    def done(self, value):
        from .execution_frames import Done
        self.phase = 'done'
        return Done(value)

    def step(self, cursor, value, error):
        from .execution_frames import SuspensionCloseCursor
        previous = getattr(cursor.owner,'_execution_cursor',None)
        if type(cursor) is SuspensionCloseCursor:
            cursor.owner._execution_cursor = cursor.ambient_cursor
        try:
            try:
                if self.owner is not cursor.owner:
                    self.malformed()
                if self.dependencies is None:
                    if error is not None:
                        raise error
                    self.dependencies = self.bind()
                self.validate_state()
                self.native_completed = False; self.native_result = None
                return self.advance(cursor,value,error)
            except StopIteration as failure:
                # Only an exception escaping the original native algorithm, not
                # the driver's Done(value), receives PEP479 conversion.
                raise RuntimeError('generator raised StopIteration') from failure
        finally:
            cursor.owner._execution_cursor = previous

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner
        language, capture_active, Await, Yield, AsyncGenerator = self.dependencies
        if self.phase == 'start':
            self.epoch = e.suspension.epoch
            capture_active(e)  # Original call before tick; its return is dead.
            e.tick(); self.kind = self.node[0]
            if self.kind == 'rethrow':
                if e.handled_error is None:
                    raise language.LanguageError('throw 需要活动的错误处理上下文',code='rethrow_error')
                raise e.handled_error
            self.phase = 'operand'
            return self.leaf(e._s_evaluate(self.node[1],self.env))
        if self.phase in ('await_return','yield_return','delegate_return'):
            return self.done(value)
        if self.phase == 'operand':
            self.operand = value
            if self.kind == 'await':
                current = e.suspension.current.task if e.suspension.current else None
                self.phase = 'await_return'
                if ((getattr(self.operand,'stack_operation',None) is not None or getattr(self.operand,'agent_operation',False) or getattr(self.operand,'compute_operation',False)) and self.operand.state == 'created' and self.operand.parent_task_id == (current.id if current else None)):
                    from .contexts import _await
                    return self.leaf(_await(e,self.operand,True,all_results=True))
                from .execution_frames import request_proxy
                return self.leaf(request_proxy(Await(self.operand)))
            if self.kind == 'yield':
                self.phase = 'yield_return'
                from .execution_frames import request_proxy
                return self.leaf(request_proxy(Yield(self.operand)))
            if self.kind == 'yield_from':
                self.asynchronous = isinstance(e.suspension.stream,AsyncGenerator)
                self.phase = 'delegate_return'
                return self.leaf(e.suspension.delegate(self.operand,self.asynchronous))
            if self.kind == 'throw':
                if type(self.operand) is language.Error:
                    from .error_values import propagate
                    raise propagate(e,self.operand)
                from .exception_classes import raised
                raise raised(e,e.copy_value(self.operand))
        raise RuntimeError('Invalid suspension expression phase')

    def close(self, cursor):
        # These original syntax branches own no new Task/stream resource scope.
        # Existing child protocols unwind first; never cancel a borrowed target,
        # replay a host effect or roll back earlier operand effects here.
        pass


SUSPENSION_FRAMES = (SuspensionSyntaxFrame,)


def suspension_frame(request):
    if type(request) is not SuspensionFrameRequest:
        return None
    if type(request.operation) is not str or request.operation != 'syntax' or type(request.values) is not tuple or len(request.values) != 2:
        from .language import LanguageError
        raise LanguageError('Malformed suspension expression request',code='ownership_error')
    return SuspensionSyntaxFrame(*request.values)


def suspension_carrier_roots(evaluator, request):
    from .language import LanguageError, Node, Env
    from .capture_frames import Edge
    if type(request) is not SuspensionFrameRequest or type(request.operation) is not str or request.operation != 'syntax' or type(request.values) is not tuple or len(request.values) != 2:
        raise LanguageError('Malformed suspension expression request',code='ownership_error')
    node, env = request.values
    if type(node) not in (tuple,Node) or not node or type(node[0]) is not str or node[0] not in SuspensionSyntaxFrame.KINDS or type(env) is not Env:
        raise LanguageError('Malformed suspension expression request',code='ownership_error')
    return (Edge(evaluator,None,(node,env)),)


def suspension_cursor(evaluator, node, env):
    from .execution_frames import SuspensionCloseCursor, link_observer
    cursor = SuspensionCloseCursor(evaluator,SuspensionSyntaxFrame(node,env))
    link_observer(evaluator,cursor)
    return cursor
