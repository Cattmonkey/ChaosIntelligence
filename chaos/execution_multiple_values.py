"""Authoritative producer and receiver state for invocation-local completions.

The original pure Completion contract, callback invocation and handle admission
remain in multiple_values. Only the two formerly native generator algorithms
live here; the shared native-close cursor transports their original leaves.
"""


def _child_transport(frame, iterator):
    value = yield from iterator
    frame.native_result = value
    frame.native_completed = True
    return value


def _public_transport(cursor):
    # Preserve the host's own PEP380 close/throw/closed-generator behavior.
    # Algorithm phases/results remain exclusively on the typed cursor frames.
    return (yield from cursor)


class _ValuesFrame:
    def malformed(self):
        from .language import LanguageError
        raise LanguageError('Malformed multiple-value execution state', code='ownership_error')

    def validate_state(self):
        if type(self.phase) is not str or self.phase not in self.PHASES:
            self.malformed()
        if type(self.native_completed) is not bool:
            self.malformed()
        if self.epoch is not None or self.phase != 'start':
            if type(self.epoch) is not int or self.epoch != self.owner.suspension.epoch:
                self.malformed()
        if self.capabilities is not None and (type(self.capabilities) is not frozenset or any(type(x) is not str for x in self.capabilities)):
            self.malformed()

    def leaf(self, iterator):
        from .execution_frames import (Push, ProtocolBridge, ExecutionCursor,
            GenericCloseCursor, PatternCloseCursor, ModuleCloseCursor,
            SuspensionCloseCursor, MultipleValuesCloseCursor)
        self.native_completed = False; self.native_result = None
        owned = iterator if type(iterator) in (ExecutionCursor, GenericCloseCursor,
            PatternCloseCursor, ModuleCloseCursor, SuspensionCloseCursor,
            MultipleValuesCloseCursor) else None
        if type(iterator) is MultipleValuesCloseCursor:
            # Nested receive had its own native generator boundary originally.
            # Retain that boundary while exposing its known actual cursor edge.
            iterator = _public_transport(iterator)
        bridge = ProtocolBridge(_child_transport(self, iterator))
        bridge.owner = self.owner; bridge.supplementary = True
        if owned is not None:
            if owned.owner is not self.owner or owned.parent_bridge is not None:
                self.malformed()
            bridge.pending_cursor = owned; owned.parent_bridge = bridge
        return Push(bridge)

    def done(self, value):
        from .execution_frames import Done
        self.phase = 'done'
        return Done(value)

    def step(self, cursor, value, error):
        from .execution_frames import MultipleValuesCloseCursor
        previous = getattr(cursor.owner, '_execution_cursor', None)
        if type(cursor) is MultipleValuesCloseCursor:
            cursor.owner._execution_cursor = cursor.ambient_cursor
        try:
            try:
                # The former generator receives a thrown error at its actual
                # suspended yield-from before running any receiver statement.
                # A new metadata check must not replace that original error.
                if error is not None:
                    raise error
                if self.owner is not cursor.owner:
                    self.malformed()
                if self.dependencies is None:
                    if error is not None:
                        raise error
                    self.dependencies = self.bind()
                self.validate_state()
                self.native_completed = False; self.native_result = None
                return self.advance(cursor, value, error)
            except StopIteration as failure:
                raise RuntimeError('generator raised StopIteration') from failure
        finally:
            cursor.owner._execution_cursor = previous

    def close(self, cursor):
        # These algorithms own no borrowed handle, resource, worker or scope.
        # The actual current delegate closes through NativeAlgorithmCursor.
        pass


class MultipleValueReceiveFrame(_ValuesFrame):
    PHASES = {'start', 'callback', 'done'}

    def __init__(self, producer):
        self.producer = producer; self.phase = 'start'; self.result = None
        self.epoch = self.capabilities = self.dependencies = None
        self.native_completed = False; self.native_result = None

    def bind(self):
        from .suspension import Task, Generator, AsyncGenerator
        return Task, Generator, AsyncGenerator

    def roots(self):
        from .capture_frames import Edge
        self.validate_state()
        return (Edge(self.owner, self.capabilities,
                     (self.producer, self.result, self.native_result)),)

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner
        if self.phase == 'start':
            self.epoch = e.suspension.epoch
            Task, Generator, AsyncGenerator = self.dependencies
            if type(self.producer) in (Task, Generator, AsyncGenerator):
                from .multiple_values import producer, Completion, prepare
                producer(e, self.producer)
                from .agent_tools import scope, current_caps
                from .capture_graph import seal
                from .class_creation import validate_value, _project_return
                with scope(e, self.producer.capabilities):
                    self.result = self.producer.runner.result if type(self.producer) is Task else self.producer.result
                    if type(self.result) is Completion:
                        self.result = prepare(e, self.result)
                    else:
                        validate_value(e, self.result)
                        ceiling = current_caps(e)
                        seal(e, (self.result,), ceiling)
                        if ceiling is not None:
                            self.result = _project_return(e, self.result, ceiling)
                return self.done(self.result)
            self.phase = 'callback'
            return self.leaf(e._s_invoke(self.producer, [], force=True,
                _context_callback=True, all_results=True))
        if self.phase == 'callback':
            self.result = value
            return self.done(self.result)
        raise RuntimeError('Invalid multiple-value producer phase')


class MultipleValueResolveFrame(_ValuesFrame):
    PHASES = {'start', 'producer', 'after', 'consumer', 'call_next', 'done'}
    OPERATIONS = {'list', 'nth', 'bind', 'prog1', 'call'}

    def __init__(self, request):
        self.request = request; self.phase = 'start'; self.operation = None
        self.function = self.producers = self.count = self.selected_producer = None
        self.index = 0; self.result = self.items = None; self.args = []
        self.epoch = self.capabilities = self.dependencies = None
        self.native_completed = False; self.native_result = None

    def bind(self):
        from .language import LanguageError, TailCall
        from .capture_frames import active, retain
        return LanguageError, TailCall, active, retain

    def roots(self):
        from .capture_frames import Edge
        from .multiple_values import Request
        self.validate_state()
        if type(self.request) is not Request or self.request.owner is not self.owner:
            self.malformed()
        if self.operation is not None and (type(self.operation) is not str or self.operation not in self.OPERATIONS):
            self.malformed()
        if type(self.index) is not int or self.index < 0 or type(self.args) is not list:
            self.malformed()
        if self.producers is not None and type(self.producers) is not tuple:
            self.malformed()
        if self.items is not None and type(self.items) is not tuple:
            self.malformed()
        if self.operation in ('nth', 'bind') and (type(self.count) is not int or self.count < 0):
            self.malformed()
        # Request/producers remain at their entry ceiling; a narrower current
        # callback must not shrink unrelated later producer closures.
        return (Edge(self.owner, self.capabilities,
                     (self.request, self.function, self.selected_producer,
                      self.result, self.items, self.args, self.native_result,
                      *(self.producers or ()))),)

    def receive(self, evaluator, producer, phase):
        self.selected_producer = producer; self.phase = phase
        # This actual nested delegate is owned by our next leaf. Attaching it
        # to the ambient observer first would overwrite the existing resolver
        # edge and falsely give one cursor two protocol parents.
        return self.leaf(values_cursor(evaluator, 'receive', producer, observer=False))

    def advance(self, cursor, value, error):
        if error is not None:
            raise error
        e = cursor.owner
        LanguageError, TailCall, active, retain = self.dependencies
        from .multiple_values import validate, components
        if self.phase == 'start':
            self.epoch = e.suspension.epoch
            if self.request.owner is not e:
                raise LanguageError('Multiple-value request cannot cross evaluators', code='ownership_error')
            self.operation = self.request.operation
            self.function, self.producers, self.count = self.request.function, self.request.producers, self.request.count
            if self.operation in ('list', 'nth', 'bind', 'prog1'):
                return self.receive(e, self.producers[0], 'producer')
            if self.operation == 'call':
                self.args = []; self.phase = 'call_next'
            else:
                raise LanguageError('Unknown multiple-value receiver', code='argument_error')
        if self.phase == 'consumer':
            return self.done(value)
        if self.phase == 'producer':
            self.result = value
            validate(e, self.result)
            if self.operation == 'call':
                self.args.extend(components(self.result, e))
                e.argument_budget(self.args, {})
                retain(active(e), self.result)
                self.index += 1; self.phase = 'call_next'
            else:
                retain(active(e), self.result)
                self.items = components(self.result, e)
                if self.operation == 'list':
                    return self.done(list(self.items))
                if self.operation == 'nth':
                    return self.done(self.items[self.count] if self.count < len(self.items) else None)
                if self.operation == 'bind':
                    self.args = list(self.items[:self.count])
                    self.args.extend([None] * (self.count - len(self.args)))
                    self.phase = 'consumer'
                    return self.leaf(e._s_invoke(self.function, self.args, force=True,
                        _context_callback=True, all_results=True))
                self.index = 1
                if self.index < len(self.producers):
                    return self.receive(e, self.producers[self.index], 'after')
                return self.done(self.result)
        if self.phase == 'after':
            self.index += 1
            if self.index < len(self.producers):
                return self.receive(e, self.producers[self.index], 'after')
            return self.done(self.result)
        if self.phase == 'call_next':
            if self.index < len(self.producers):
                return self.receive(e, self.producers[self.index], 'producer')
            return self.done(TailCall(self.function, self.args,
                span=dict(e.current_span), ordinary_call=True))
        raise RuntimeError('Invalid multiple-value receiver phase')


MULTIPLE_VALUE_FRAMES = (MultipleValueReceiveFrame, MultipleValueResolveFrame)


def values_cursor(evaluator, operation, value, *, observer=True):
    from .execution_frames import MultipleValuesCloseCursor, link_observer
    frame = MultipleValueReceiveFrame(value) if operation == 'receive' else MultipleValueResolveFrame(value)
    cursor = MultipleValuesCloseCursor(evaluator, frame)
    if observer:
        link_observer(evaluator, cursor)
    return cursor


def values_protocol(evaluator, operation, value):
    # Construct on first native advance, not cold creation/throw/close. This
    # wrapper carries only delegation, never a receiver algorithm or replay.
    return (yield from values_cursor(evaluator, operation, value))
