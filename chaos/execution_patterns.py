"""Authoritative match and structural declaration/reassignment evaluation.

The structural matcher and its preflight/commit helpers remain synchronous and
pure. This frame owns every actual RHS, ordered case, guard and body suspension.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PatternFrameRequest:
    operation: str
    values: tuple


def pattern_protocol(evaluator, operation, *values):
    from .execution_frames import request_proxy, ensure_cursor
    proxy = request_proxy(PatternFrameRequest(operation, values))
    if getattr(evaluator, '_execution_cursor', None) is not None:
        return proxy
    return ensure_cursor(evaluator, proxy, observer=True)


def _child_transport(frame, iterator):
    # Let the native yield-from protocol decide whether delegate.close is an
    # exception or a lawful completion. In particular this is not inferred from
    # Python versions, exception messages, or merely observing a StopIteration.
    value = yield from iterator
    frame.native_result = value
    frame.native_completed = True
    return value


class PatternSyntaxFrame:
    KINDS = {'match', 'let_pattern', 'var_pattern', 'assign_pattern'}

    def __init__(self, node, env, tail=False, all_results=False, compiled_regions=None, flow_boundary=None):
        self.node, self.env = node, env
        self.tail, self.all_results, self.compiled_regions = tail, all_results, compiled_regions
        self.phase = 'start'; self.kind = None; self.index = 0
        self.value = self.cases = self.case = self.pattern = self.guard = self.body = None
        self.bindings = self.scope = self.result = None
        self.capabilities = None; self.epoch = None; self.dependencies = None
        self.native_completed = False; self.native_result = None
        self._flow_boundary=flow_boundary;self._flow_pattern_native_close=False;self._flow_pattern_drain=False

    def roots(self):
        from .capture_frames import Edge
        from .language import LanguageError, Node, Env
        def malformed():
            raise LanguageError('Malformed pattern execution state', code='ownership_error')
        if type(self.phase) is not str or self.phase not in ('start','subject','rhs','select','guard','body','done'):
            malformed()
        if self.kind is not None and (type(self.kind) is not str or self.kind not in self.KINDS):
            malformed()
        if type(self.tail) is not bool or type(self.all_results) is not bool or type(self.native_completed) is not bool or type(self.index) is not int or self.index < 0:
            malformed()
        if self.epoch is not None or self.phase != 'start':
            if type(self.epoch) is not int or self.epoch != self.owner.suspension.epoch:
                malformed()
        if self.capabilities is not None and (type(self.capabilities) is not frozenset or any(type(x) is not str for x in self.capabilities)):
            malformed()
        if type(self.node) not in (tuple, Node) or not self.node or type(self.node[0]) is not str or self.node[0] not in self.KINDS:
            malformed()
        if type(self.env) is not Env or self.scope is not None and type(self.scope) is not Env:
            malformed()
        if self.phase in ('subject','select','guard','body') and self.kind != 'match' or self.phase == 'rhs' and self.kind not in ('let_pattern','var_pattern','assign_pattern'):
            malformed()
        if self.cases is not None and type(self.cases) not in (list, tuple):
            malformed()
        def ast_node(value, optional=False):
            return optional and value is None or type(value) in (tuple, Node) and bool(value)
        def case_row(row):
            return type(row) in (tuple,list,Node) and len(row) == 3 and ast_node(row[0]) and ast_node(row[1],True) and ast_node(row[2])
        if self.cases is not None and any(not case_row(row) for row in self.cases):
            malformed()
        if self.case is not None and not case_row(self.case):
            malformed()
        if not ast_node(self.pattern,True) or not ast_node(self.guard,True) or not ast_node(self.body,True):
            malformed()
        if self.bindings is not None and (type(self.bindings) is not dict or any(type(x) is not str for x in self.bindings)):
            malformed()
        # The original caller and scrutinee are not arguments to a narrower
        # guard callback. Keep their entry ceiling; actual callback admission
        # continues through the pre-existing InvocationFrame authority checks.
        # Case tables/rows are validated syntax metadata, not language Lists:
        # walking their representation would add a new10000-item capture limit.
        values = (self.node, self.env, self.value, self.pattern, self.guard,
                  self.body, self.bindings, self.scope, self.result, self.native_result)
        return (Edge(self.owner, self.capabilities, values),)

    def bind(self):
        # These original local import bindings are acquired on first advance,
        # never on construction/cold throw. Language globals remain late bound.
        from .suspension import Await, Yield, Generator, AsyncGenerator, GeneratorClose
        from .capture_frames import active, reset, retain
        from . import language
        return language, active

    def leaf(self, iterator):
        from .execution_frames import (Push, ProtocolBridge, ExecutionCursor,
                                       GenericCloseCursor, PatternCloseCursor)
        self.native_completed = False; self.native_result = None
        bridge = ProtocolBridge(_child_transport(self, iterator))
        bridge.owner = self.owner; bridge.supplementary = True
        # With no ambient caller cursor, the original _s_evaluate returns a real
        # standalone cursor. Own that exact delegate explicitly instead of
        # inspecting the native generator or converting it to a raw request.
        if type(iterator) in (ExecutionCursor,GenericCloseCursor,PatternCloseCursor):
            if iterator.owner is not self.owner or iterator.parent_bridge is not None:
                from .language import LanguageError
                raise LanguageError('Pattern child cursor is already owned or foreign',code='ownership_error')
            bridge.pending_cursor = iterator; iterator.parent_bridge = bridge
        return Push(bridge)

    def evaluate(self, evaluator, node, env, *, body=False, regions=False):
        if self._flow_boundary is not None and not self._flow_pattern_drain:
            from .compiled_flow_patterns import evaluate
            return evaluate(self,evaluator,node,env,body=body,regions=regions)
        if body:
            return self.leaf(evaluator._s_evaluate(node, env, self.tail, all_results=self.all_results))
        if regions:
            return self.leaf(evaluator._s_evaluate(node, env, _compiled_regions=self.compiled_regions))
        return self.leaf(evaluator._s_evaluate(node, env))

    def done(self, value):
        from .execution_frames import Done
        self.result = value; self.phase = 'done'
        return Done(value)

    def step(self, cursor, value, error):
        # Preserve the ambient gateway choice at every original callsite. A
        # direct native host call may have no outer cursor; an engine call has
        # its actual parent. Do not pin the first parent across later resumes.
        from .execution_frames import PatternCloseCursor
        previous = getattr(cursor.owner,'_execution_cursor',None)
        if type(cursor) is PatternCloseCursor:
            cursor.owner._execution_cursor = cursor.ambient_cursor
        try:
            try:
                if self.dependencies is None:
                    if error is not None:
                        raise error
                    self.dependencies = self.bind()
                # A result already delivered to the state machine no longer belongs
                # to the preceding native delegate. Every new leaf resets this pair.
                self.native_completed = False; self.native_result = None
                if self._flow_boundary is not None:
                    from .compiled_flow_patterns import step
                    return step(self,cursor,value,error,self.advance)
                return self.advance(cursor, value, error)
            except StopIteration as failure:
                # Only an exception escaping the former native algorithm boundary
                # is converted. Done(value) remains the driver's normal completion.
                raise RuntimeError('generator raised StopIteration') from failure
        finally:
            cursor.owner._execution_cursor = previous

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner; language, capture_active = self.dependencies
        if self.phase == 'start':
            self.epoch = e.suspension.epoch
            capture_active(e)  # Original lookup/call before tick; return is dead.
            e.tick(); self.kind = self.node[0]
            if self.kind == 'match':
                self.phase = 'subject'
                return self.evaluate(e, self.node[1], self.env)
            self.phase = 'rhs'
            rhs = self.node[2] if self.kind == 'assign_pattern' else self.node[3]
            return self.evaluate(e, rhs, self.env, regions=True)
        if self.phase == 'rhs':
            self.value = value
            if self.kind == 'assign_pattern':
                return self.done(e.assign_pattern(self.node[1], self.value, self.env))
            self.value = language.check_type(self.value, e.contract(self.node[2], self.env))
            return self.done(e.bind_pattern(self.node[1], self.value, self.env, mutable=self.kind == 'var_pattern'))
        if self.phase == 'subject':
            self.value = value; self.cases = self.node[2]; self.index = 0; self.phase = 'select'
        elif self.phase == 'body':
            return self.done(value)
        elif self.phase == 'guard':
            truth=language.check_type(value,'Bool')
            if self._flow_boundary is not None:
                from .compiled_flow_patterns import decide
                truth=decide(self,truth)
            if truth:
                self.phase = 'body'
                return self.evaluate(e, self.body, self.scope, body=True)
            self.case = self.pattern = self.guard = self.body = self.bindings = self.scope = None
            self.phase = 'select'
        if self.phase == 'select':
            while self.index < len(self.cases):
                self.case = self.cases[self.index]; self.index += 1
                self.pattern, self.guard, self.body = self.case
                self.bindings = {}
                if not e.pattern_match(self.pattern, self.value, self.env, self.bindings):
                    continue
                self.scope = language.Env(self.env)
                for name, item in self.bindings.items():
                    self.scope.bind(name, item)
                if self.guard is not None:
                    self.phase = 'guard'
                    return self.evaluate(e, self.guard, self.scope)
                self.phase = 'body'
                return self.evaluate(e, self.body, self.scope, body=True)
            raise language.LanguageError('match 没有匹配分支', code='match_error')
        raise RuntimeError('Invalid pattern execution phase')

    def close(self, cursor):
        # The original four branches own no cleanup scope or borrowed resource.
        # Existing child frames close first; RHS/guard effects are not undone.
        pass


PATTERN_FRAMES = (PatternSyntaxFrame,)


def pattern_frame(request):
    if type(request) is not PatternFrameRequest:
        return None
    if type(request.operation) is not str or request.operation != 'syntax':
        raise RuntimeError('Unknown pattern execution operation')
    if type(request.values) is not tuple or len(request.values) != 5:
        from .language import LanguageError
        raise LanguageError('Malformed pattern execution request', code='ownership_error')
    return PatternSyntaxFrame(*request.values)


def pattern_carrier_roots(evaluator, request):
    from .language import LanguageError, Node
    from .capture_frames import Edge
    if type(request) is not PatternFrameRequest or type(request.operation) is not str or request.operation != 'syntax' or type(request.values) is not tuple or len(request.values) != 5:
        raise LanguageError('Malformed pattern execution request', code='ownership_error')
    node, env, tail, all_results, compiled_regions = request.values
    if type(node) not in (tuple, Node) or not node or type(node[0]) is not str or node[0] not in PatternSyntaxFrame.KINDS or type(tail) is not bool or type(all_results) is not bool:
        raise LanguageError('Malformed pattern execution request', code='ownership_error')
    return (Edge(evaluator, None, (node, env)),)


def pattern_cursor(evaluator, node, env, tail=False, all_results=False, compiled_regions=None, flow_boundary=None):
    from .execution_frames import PatternCloseCursor, link_observer
    frame=PatternSyntaxFrame(node,env,tail,all_results,compiled_regions,flow_boundary)
    cursor=PatternCloseCursor(evaluator,frame)
    if flow_boundary is not None:flow_boundary.flow_operation=frame
    link_observer(evaluator, cursor)
    return cursor
