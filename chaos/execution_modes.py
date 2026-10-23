"""Authoritative Mode algorithms on the shared cursor and scheduler.

Native generators below are transport only. The former Mode locals, loops and
unwind continuations are explicit frame fields; protocol leaves remain original.
"""


def _completed(frame, iterator):
    value = yield from iterator
    frame.native_result = value
    frame.native_completed = True
    return value


class _ModeFrame:
    ROOTS = ()
    PHASES = ()

    def __init__(self):
        self.phase = 'start'
        self.epoch = self.capabilities = None
        self.native_completed = False
        self.native_result = None

    def malformed(self):
        from .language import LanguageError
        raise LanguageError('Malformed mode execution state', code='ownership_error')

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

    def roots(self):
        from .capture_frames import Edge
        self.validate_state()
        return (Edge(self.owner, self.capabilities,
            tuple(getattr(self, name, None) for name in self.ROOTS) + (self.native_result,)),)

    def leaf(self, iterator):
        from .execution_frames import Push, ProtocolBridge, ModeCloseCursor
        self.native_completed = False; self.native_result = None
        bridge = ProtocolBridge(_completed(self, iterator))
        bridge.owner = self.owner; bridge.supplementary = True
        if type(iterator) is ModeCloseCursor:
            if iterator.owner is not self.owner or iterator.parent_bridge is not None:
                self.malformed()
            # This is one actual nested native delegation, not a second driver.
            bridge.mode_cursor = iterator; iterator.parent_bridge = bridge
            bridge.iterator = _completed(self, mode_protocol(self.owner, None, _cursor=iterator))
        return Push(bridge)

    def child(self, operation, *args):
        return self.leaf(mode_cursor(self.owner, operation, *args))

    def external(self, request):
        from .execution_frames import External
        self.native_completed = False; self.native_result = None
        return External(request)

    def done(self, value=None):
        from .execution_frames import Done
        self.phase = 'done'
        return Done(value)

    def step(self, cursor, value, error):
        e = cursor.owner
        previous = getattr(e, '_execution_cursor', None)
        e._execution_cursor = cursor.ambient_cursor
        try:
            try:
                # Incoming errors belong at the original suspended statement;
                # do not replace hard errors with a new metadata diagnosis.
                if error is None:
                    if self.owner is not e:
                        self.malformed()
                    self.validate_state()
                if self.phase == 'start' and self.epoch is None:
                    self.epoch = e.suspension.epoch
                self.native_completed = False; self.native_result = None
                return self.advance(cursor, value, error)
            except StopIteration as failure:
                raise RuntimeError('generator raised StopIteration') from failure
        finally:
            e._execution_cursor = previous

    def close(self, cursor):
        # Actual unwind is delivered by the shared close engine into advance.
        # No duplicate cleanup is run after that continuation has completed.
        pass


class ModeInvokeFrame(_ModeFrame):
    PHASES = ('start', 'invoke', 'await', 'done')
    ROOTS = ('action', 'value', 'group', 'result')

    def __init__(self, action, value, group=None):
        super().__init__(); self.action = action; self.value = value
        self.group = group; self.result = None; self.before = 0

    def advance(self, cursor, value, error):
        if error is not None: raise error
        from .suspension import Task, Await, Generator
        from .iteration import HANDLES
        from .language import LanguageError
        e = cursor.owner
        if self.phase == 'start':
            self.before = len(e.suspension.tasks); self.phase = 'invoke'
            return self.leaf(e._s_invoke(self.action, [self.value]))
        if self.phase == 'invoke':
            self.result = value
            if isinstance(self.result, Task):
                e.suspension.validate(self.result, Task)
                current = e.suspension.current.task
                if current.recording_scope is not None and self.result.recording_scope != current.recording_scope:
                    raise LanguageError('录制不能等待来自其他 scope 的 Task', code='mode_error')
                if self.group is not None:
                    from .contexts import _operation
                    if _operation(e, self.result, e.suspension.tasks[self.before:]):
                        self.group.spawn(self.result)
                self.phase = 'await'
                return self.external(Await(self.result))
        elif self.phase == 'await':
            self.result = value
        else: raise RuntimeError('Invalid mode invocation phase')
        if isinstance(self.result, (Task, Generator) + HANDLES):
            raise LanguageError('模式 action 不能返回未消费的 Task/stream', code='mode_error')
        return self.done(self.result)


class ModeHostFrame(_ModeFrame):
    PHASES = ('start', 'effect', 'done')
    ROOTS = ('payload', 'result')

    def __init__(self, operation, payload):
        super().__init__(); self.operation = operation; self.payload = payload; self.result = None

    def advance(self, cursor, value, error):
        if error is not None: raise error
        from .modes import data
        from .suspension import Effect
        from .language import LanguageError
        e = cursor.owner
        if self.phase == 'start':
            if e.mode_handler is None:
                raise LanguageError('当前上下文没有模式持久化/录制能力', code='capability_error')
            e.tick(); self.phase = 'effect'
            return self.external(Effect('mode', {'operation': self.operation, 'payload': data(self.payload)}))
        if self.phase == 'effect':
            e.tick(); self.result = data(value)
            return self.done(self.result)
        raise RuntimeError('Invalid mode host phase')


class ModePlanStepFrame(_ModeFrame):
    PHASES = ('start', 'invoke', 'done')
    ROOTS = ('step_data', 'action', 'context', 'metadata', 'group', 'payload', 'result')

    def __init__(self, step, action, context, metadata, group=None):
        super().__init__(); self.step_data = step
        self.action = action; self.context = context; self.metadata = metadata; self.group = group
        self.payload = self.result = None

    def advance(self, cursor, value, error):
        from .modes import data, emit, _plan_error_event
        from .language import ExecutionControl
        e = cursor.owner
        # Payload construction is outside the original try.
        if self.phase == 'start':
            if error is not None: raise error
            self.payload = {'stepId': self.step_data['id'], 'call': self.step_data['call']}
            if self.group is not None: self.payload['stepTaskId'] = e.suspension.current.task.id
        try:
            if error is not None: raise error
            if self.phase == 'start':
                emit(e, 'plan.step.started', **self.metadata, **self.payload)
                self.phase = 'invoke'
                return self.child('invoke', self.action, self.context, self.group)
            self.result = data(value)
            emit(e, 'plan.step.completed', **self.metadata, **self.payload)
            return self.done(self.result)
        except BaseException as failure:
            event = 'plan.step.cancelled' if isinstance(failure, (ExecutionControl, GeneratorExit)) else 'plan.step.failed'
            _plan_error_event(e, event, failure, self.metadata, **self.payload)
            raise


class ModeSerialPlanFrame(_ModeFrame):
    PHASES = ('start', 'scan', 'step', 'done')
    ROOTS = ('definition', 'actions', 'input', 'metadata', 'results', 'selected')

    def __init__(self, definition, actions, input, metadata):
        super().__init__(); self.definition = definition; self.actions = actions; self.input = input; self.metadata = metadata
        self.results = {}; self.index = 0; self.selected = None

    def advance(self, cursor, value, error):
        if error is not None: raise error
        from .modes import data
        e = cursor.owner
        if self.phase == 'step':
            self.results[self.selected['id']] = value; self.results = data(self.results)
        self.phase = 'scan'
        steps = self.definition['steps']
        while True:
            if self.index == len(steps):
                if len(self.results) == len(steps): return self.done(self.results)
                self.index = 0
            while self.index < len(steps):
                self.selected = steps[self.index]; self.index += 1; e.tick()
                step = self.selected
                if step['id'] in self.results or not all(name in self.results for name in step['needs']): continue
                context = {'input': data(self.input), 'results': {name: data(self.results[name]) for name in step['needs']}}
                self.phase = 'step'
                return self.child('plan_step', step, self.actions[step['call']], context, self.metadata)


class ModeParallelBodyFrame(_ModeFrame):
    PHASES = ('start', 'wave', 'wait', 'done')
    ROOTS = ('definition', 'actions', 'input', 'metadata', 'group', 'results', 'pending', 'selected')

    def __init__(self, definition, actions, input, metadata, group):
        super().__init__(); self.definition = definition; self.actions = actions; self.input = input; self.metadata = metadata; self.group = group
        self.results = {}; self.pending = {}; self.admitted = set(); self.selected = None

    def advance(self, cursor, value, error):
        from .modes import data, _plan_step
        from .suspension import Await, TERMINAL
        from .task_groups import ScopeOutcome
        from .task_waits import wait
        e = cursor.owner
        try:
            if error is not None: raise error
            self.phase = 'wave'
            while len(self.results) < len(self.definition['steps']):
                e.tick()
                for step_id, task in list(self.pending.items()):
                    if task.state not in TERMINAL: continue
                    if task.runner.error is not None: raise task.runner.error
                    self.results[step_id] = task.runner.result; del self.pending[step_id]
                self.results = data(self.results)
                for step in self.definition['steps']:
                    e.tick()
                    if len(self.pending) >= self.metadata['maxConcurrency']: break
                    if step['id'] in self.admitted or not all(name in self.results for name in step['needs']): continue
                    self.selected = step
                    context = {'input': data(self.input), 'results': {name: data(self.results[name]) for name in step['needs']}}
                    task = e.suspension.task(_plan_step(e, step, self.actions[step['call']], context, self.metadata, self.group), capture_roots=(self.actions[step['call']], context, self.group))
                    self.group.spawn(task); self.pending[step['id']] = task; self.admitted.add(step['id'])
                if self.pending:
                    self.phase = 'wait'
                    return self.external(Await(wait(e, list(self.pending.values()))))
            return self.done(ScopeOutcome(value={step['id']: self.results[step['id']] for step in self.definition['steps']}))
        except BaseException as failure:
            return self.done(ScopeOutcome(error=failure))


class ModeParallelPlanFrame(_ModeFrame):
    PHASES = ('start', 'group', 'done')
    ROOTS = ('definition', 'actions', 'input', 'metadata', 'group', 'outcome')

    def __init__(self, definition, actions, input, metadata):
        super().__init__(); self.definition = definition; self.actions = actions; self.input = input; self.metadata = metadata
        self.group = self.outcome = None

    def advance(self, cursor, value, error):
        if error is not None: raise error
        from .task_groups import create, GroupRun
        e = cursor.owner
        if self.phase == 'start':
            self.group = create(e); self.phase = 'group'
            return self.external(GroupRun(self.group, mode_protocol(e, 'parallel_body', self.definition, self.actions, self.input, self.metadata, self.group)))
        self.outcome = value
        return self.done(self.outcome.value)


class ModeRunPlanFrame(_ModeFrame):
    PHASES = ('start', 'execute', 'done')
    ROOTS = ('mode', 'bindings', 'input', 'definition', 'actions', 'metadata', 'result')

    def __init__(self, mode, bindings, input):
        super().__init__(); self.mode = mode; self.bindings = bindings; self.input = input
        self.definition = self.actions = self.metadata = self.result = None

    def advance(self, cursor, value, error):
        from .modes import owned, Plan, bindings_for, data, definition_hash, emit, _plan_error_event
        from .language import ExecutionControl
        from . import modes
        e = cursor.owner
        if self.phase == 'start':
            if error is not None: raise error
            self.definition = owned(self.mode, e, Plan).definition
            self.actions = bindings_for(e, self.bindings, dict.fromkeys(step['call'] for step in self.definition['steps']))
            self.input = data(self.input)
            self.metadata = {'executionId': 'plan-' + modes.uuid.uuid4().hex, 'name': self.definition['name'], 'definitionHash': definition_hash(self.definition), 'maxConcurrency': self.definition.get('max_concurrency', 1)}
        try:
            if error is not None: raise error
            if self.phase == 'start':
                emit(e, 'plan.started', steps=len(self.definition['steps']), **self.metadata)
                operation = 'serial_plan' if self.metadata['maxConcurrency'] == 1 else 'parallel_plan'
                self.phase = 'execute'
                return self.child(operation, self.definition, self.actions, self.input, self.metadata)
            self.result = value; emit(e, 'plan.completed', **self.metadata)
            return self.done(self.result)
        except BaseException as failure:
            event = 'plan.cancelled' if isinstance(failure, (ExecutionControl, GeneratorExit)) else 'plan.failed'
            _plan_error_event(e, event, failure, self.metadata)
            raise


class ModePursueFrame(_ModeFrame):
    PHASES = ('start', 'claim', 'iteration', 'pending_commit', 'planner', 'plan', 'verify', 'verified_commit', 'release', 'done')
    ROOTS = ('mode', 'bindings', 'input', 'key', 'definition', 'actions', 'state', 'claim', 'context', 'verification', 'pending_error')

    def __init__(self, mode, bindings, input, key):
        super().__init__(); self.mode = mode; self.bindings = bindings; self.input = input; self.key = key
        self.definition = self.actions = self.state = self.claim = self.context = self.verification = self.pending_error = None
        self.in_try = False

    def result(self):
        from .modes import data
        return {'done': self.state['phase'] == 'done', 'state': data(self.state['state']), 'evidence': data(self.state['evidence']), 'iterations': self.state['iteration'], 'status': 'achieved' if self.state['phase'] == 'done' else 'exhausted'}

    def commit(self, phase):
        self.phase = phase
        return self.child('host', 'goal.commit', {'key': self.key, 'lease': self.claim['lease'], 'revision': self.claim['revision'], 'checkpoint': self.state})

    def advance(self, cursor, value, error):
        from .modes import owned, Goal, Plan, bindings_for, data, identifier, checkpoint, emit
        from .language import ResourceLimit, ExecutionControl, LanguageError
        e = cursor.owner
        if self.phase == 'release':
            # Original best-effort release swallows every failure, including a
            # hard one arriving at this particular release frontier.
            raise self.pending_error
        try:
            if error is not None: raise error
            if self.phase == 'start':
                self.definition = owned(self.mode, e, Goal).definition
                self.actions = bindings_for(e, self.bindings, [self.definition['planner'], self.definition['verify']])
                self.input = data(self.input)
                self.state = {'iteration': 0, 'state': self.input, 'results': {}, 'history': [], 'phase': 'ready', 'evidence': None}
                if self.key is not None:
                    identifier(self.key, 'key'); self.phase = 'claim'
                    return self.child('host', 'goal.claim', {'key': self.key, 'definition': self.definition, 'input': self.input})
                self.in_try = True
                if self.state['phase'] in ('done', 'exhausted'): return self.done(self.result())
                self.phase = 'iteration'
            if self.phase == 'claim':
                self.claim = value; self.state = checkpoint(self.claim['checkpoint'], self.definition)
                if self.state['phase'] == 'pending':
                    raise LanguageError('Goal 未完成动作需要先 reconciliation', code='mode_error')
                self.in_try = True
                if self.state['phase'] in ('done', 'exhausted'): return self.done(self.result())
                self.phase = 'iteration'
            if self.phase == 'iteration':
                if self.state['iteration'] >= self.definition['max_iterations']:
                    emit(e, 'goal.completed', status=self.result()['status'], iterations=self.state['iteration'])
                    return self.done(self.result())
                e.tick(); self.state['iteration'] += 1; self.state['phase'] = 'pending'
                if self.claim: return self.commit('pending_commit')
                self.phase = 'pending_commit'
            elif self.phase == 'pending_commit': self.claim = value
            if self.phase == 'pending_commit':
                emit(e, 'goal.iteration', iteration=self.state['iteration'])
                self.context = {'objective': self.definition['objective'], 'state': data(self.state['state']), 'iteration': self.state['iteration'], 'results': data(self.state['results']), 'history': data(self.state['history'])}
                self.phase = 'planner'
                return self.child('invoke', self.actions[self.definition['planner']], self.context)
            if self.phase == 'planner':
                owned(value, e, Plan); self.phase = 'plan'
                return self.child('run_plan', value, self.bindings, self.state['state'])
            if self.phase == 'plan':
                self.state['results'] = value
                observation = {**self.context, 'results': data(self.state['results'])}
                self.phase = 'verify'
                return self.child('invoke', self.actions[self.definition['verify']], observation)
            if self.phase == 'verify':
                self.verification = data(value); verification = self.verification
                if type(verification) is not dict or set(verification) != {'done', 'state', 'evidence'} or type(verification['done']) is not bool:
                    raise LanguageError('Goal verify 需要 {done:Bool,state:JSON,evidence:JSON}', code='mode_error')
                self.state['state'], self.state['evidence'] = verification['state'], verification['evidence']
                self.state['history'].append({'iteration': self.state['iteration'], 'done': verification['done'], 'evidence': data(verification['evidence'])})
                self.state['phase'] = 'done' if verification['done'] else ('exhausted' if self.state['iteration'] >= self.definition['max_iterations'] else 'ready')
                self.state = checkpoint(self.state, self.definition)
                if self.claim: return self.commit('verified_commit')
                self.phase = 'verified_commit'
            elif self.phase == 'verified_commit': self.claim = value
            if self.phase == 'verified_commit':
                emit(e, 'goal.verified', iteration=self.state['iteration'], done=self.verification['done'])
                if self.verification['done']:
                    emit(e, 'goal.completed', status=self.result()['status'], iterations=self.state['iteration'])
                    return self.done(self.result())
                self.phase = 'iteration'
                # Loop continuation has no new transport/callback/checkpoint.
                return self.advance(cursor, None, None)
            raise RuntimeError('Invalid mode goal phase')
        except (ResourceLimit, ExecutionControl, GeneratorExit): raise
        except BaseException as failure:
            if not self.in_try: raise
            if self.claim:
                self.pending_error = failure; self.phase = 'release'
                return self.child('host', 'goal.release', {'key': self.key, 'lease': self.claim['lease'], 'outcome': 'failed'})
            raise


class ModeCloseResourcesFrame(_ModeFrame):
    PHASES = ('start', 'handles', 'closed', 'stacks', 'done')
    ROOTS = ('scope', 'streams', 'adapters', 'handle')

    def __init__(self, scope):
        super().__init__(); self.scope = scope; self.streams = self.adapters = None
        self.index = 0; self.handle = None

    def advance(self, cursor, value, error):
        if error is not None: raise error
        from .suspension import TERMINAL, AsyncGenerator, Await
        from .iteration import AsyncIterator
        from .resource_stacks import finalize_scope
        e = cursor.owner
        if self.phase == 'stacks': return self.done(bool(self.streams or self.adapters or value))
        if self.phase == 'start':
            self.streams = [h for h in e.suspension.generators if h.recording_scope == self.scope and h.state not in TERMINAL and h.context_manager is None]
            self.adapters = [h for h in e.suspension.iteration.handles if h.recording_scope == self.scope and not h.close_attempted]
        self.phase = 'handles'; handles = self.streams + self.adapters
        if self.index < len(handles):
            self.handle = handles[self.index]; self.index += 1; e.tick(); self.phase = 'closed'
            if isinstance(self.handle, (AsyncGenerator, AsyncIterator)):
                return self.external(Await(e.suspension.aclose(self.handle)))
            if self.handle in self.streams: return self.leaf(e.suspension._s_close(self.handle))
            return self.leaf(e.suspension.iteration.close(self.handle))
        self.phase = 'stacks'
        return self.leaf(finalize_scope(e, self.scope))


class ModeRecordFrame(_ModeFrame):
    PHASES = ('start', 'begin', 'action', 'normal_wave', 'normal_wave_children', 'normal_child', 'cold_child', 'normal_resources', 'finish', 'abort_wave', 'drain', 'abort_resources', 'abort', 'finalize', 'done')
    ROOTS = ('action', 'input', 'parameters', 'task', 'scope', 'output', 'children', 'child_task', 'pending_error', 'original_error', 'result')

    def __init__(self, action, input, parameters):
        super().__init__(); self.action = action; self.input = input; self.parameters = parameters
        self.task = self.scope = self.output = self.child_task = self.pending_error = self.original_error = self.result = None
        self.children = []; self.index = 0; self.seen = set(); self.cancellation = None; self.opened = False

    def finalize(self, result=None, error=None):
        from .resource_stacks import finalize_scope
        self.result = result; self.pending_error = error; self.phase = 'finalize'
        return self.leaf(finalize_scope(self.owner, self.scope, hard=True))

    def start_abort(self, error):
        from .suspension import TERMINAL
        from .language import ResourceLimit, ExecutionControl
        e = self.owner
        if isinstance(error, (ResourceLimit, ExecutionControl, GeneratorExit)):
            try:
                for child in e.suspension.tasks:
                    if child is not self.task and child.recording_scope == self.scope and child.state not in TERMINAL:
                        e.suspension.cancel(child)
            except BaseException as failure:
                error = failure
            return self.finalize(error=error)
        self.original_error = self.pending_error = error
        self.seen = set(); self.cancellation = object(); self.phase = 'abort_wave'
        return self.abort_progress()

    def abort_progress(self):
        from .suspension import TERMINAL, Drain, hard_error
        e = self.owner
        while True:
            if self.phase == 'abort_wave':
                self.children = [c for c in e.suspension.tasks if c is not self.task and c.recording_scope == self.scope and id(c) not in self.seen]
                for child in self.children:
                    self.seen.add(id(child))
                    if child.state not in TERMINAL: e.suspension._cancel(child, self.cancellation)
                if hard_error(self.pending_error): return self.finalize(error=self.pending_error)
                self.index = 0; self.phase = 'drain'
            if self.phase == 'drain':
                if self.index < len(self.children):
                    self.child_task = self.children[self.index]; self.index += 1; self.child_task.observed = True
                    return self.external(Drain(self.child_task))
                if hard_error(self.pending_error): return self.finalize(error=self.pending_error)
                self.phase = 'abort_resources'
                return self.child('close_resources', self.scope)
            if self.phase == 'abort_resources': raise RuntimeError('Resource completion has not been supplied')

    def abandon(self):
        from .suspension import TERMINAL
        from .contexts import abandon
        e = self.owner
        for handle in e.suspension.generators:
            if handle.recording_scope == self.scope and handle.state not in TERMINAL:
                if handle.context_manager is not None: abandon(e, handle.context_manager)
                else:
                    e.suspension._abort_stream(handle); handle.state = 'closed'; handle.busy = False
                    e.suspension._retire_execution(handle)
        for handle in e.suspension.iteration.handles:
            if handle.recording_scope == self.scope:
                handle.close_attempted, handle.busy, handle.state = True, False, 'closed'
        self.task.recording_scope = None

    def advance(self, cursor, value, error):
        try:
            return self._advance_record(cursor, value, error)
        except BaseException as failure:
            if not self.opened or self.phase == 'finalize':
                raise
            # Exceptions in the ordinary-error cleanup suite still pass through
            # the original unconditional finally; no abort/drain replay.
            return self.finalize(error=failure)

    def _advance_record(self, cursor, value, error):
        from .modes import data, make, Skill
        from .language import check_type, LanguageError
        from .suspension import TERMINAL, TaskCancelled, Await, hard_error, error_priority
        from .contexts import abandon
        e = cursor.owner
        # finalize_scope itself may fail; original finally then stops immediately
        # without performing later statements (including recording_scope reset).
        if self.phase == 'finalize':
            if error is not None: raise error
            self.abandon()
            if self.pending_error is not None: raise self.pending_error
            return self.done(self.result)
        if self.phase == 'drain':
            if error is not None:
                local = e.suspension._local_cancel(error, self.child_task) and self.child_task.cancel_sources == {self.cancellation}
                if not local and error_priority(error) > error_priority(self.pending_error): self.pending_error = error
            return self.abort_progress()
        if self.phase == 'abort_resources':
            if error is not None: self.pending_error = error; value = True
            if not self.children and not value:
                if not hard_error(self.pending_error):
                    self.phase = 'abort'
                    return self.child('host', 'recording.abort', {'scopeId': self.scope, 'reason': 'action_failed'})
                return self.finalize(error=self.pending_error)
            self.phase = 'abort_wave'
            return self.abort_progress()
        if self.phase == 'abort':
            if error is not None and error_priority(error) > error_priority(self.pending_error): self.pending_error = error
            return self.finalize(error=self.pending_error)
        try:
            if error is not None:
                if self.phase == 'cold_child' and isinstance(error, TaskCancelled):
                    error = None
                else: raise error
            if self.phase == 'start':
                check_type(self.action, 'Function'); self.input = data(self.input); self.parameters = data(self.parameters)
                self.task = e.suspension.current.task
                if self.task.recording_scope is not None:
                    raise LanguageError('暂不支持嵌套录制 scope', code='mode_error')
                self.phase = 'begin'
                return self.child('host', 'recording.begin', {})
            if self.phase == 'begin':
                self.scope = value['scopeId']; self.task.recording_scope = self.scope; self.opened = True
                self.phase = 'action'; return self.child('invoke', self.action, self.input)
            if self.phase == 'action':
                self.output = value; self.phase = 'normal_wave'
            if self.phase in ('normal_child', 'cold_child'): self.phase = 'normal_wave_children'
            if self.phase == 'normal_resources':
                if not self.children and not value:
                    for handle in e.suspension.generators:
                        if handle.recording_scope == self.scope and handle.context_manager is not None and handle.state not in TERMINAL:
                            abandon(e, handle.context_manager)
                    self.output = data(self.output); self.phase = 'finish'
                    return self.child('host', 'recording.finish', {'scopeId': self.scope, 'parameters': self.parameters})
                self.phase = 'normal_wave'
            if self.phase == 'finish':
                skill = make(value['definition'], e)
                if not isinstance(skill, Skill): raise LanguageError('录制没有返回 Skill', code='mode_error')
                return self.finalize(result={'output': self.output, 'skill': skill})
            while True:
                if self.phase == 'normal_wave':
                    self.children = [c for c in e.suspension.tasks if c is not self.task and c.recording_scope == self.scope and id(c) not in self.seen]
                    self.index = 0; self.phase = 'normal_wave_children'
                while self.index < len(self.children):
                    self.child_task = self.children[self.index]; self.index += 1; self.seen.add(id(self.child_task))
                    if self.child_task.state == 'created':
                        e.suspension.cancel(self.child_task); self.child_task.observed = True; self.phase = 'cold_child'
                        return self.external(Await(self.child_task))
                    if self.child_task.state == 'cancelled': raise LanguageError('录制子 Task 已取消', code='mode_error')
                    self.child_task.observed = True
                    if self.child_task.state not in TERMINAL or self.child_task.state == 'failed':
                        self.phase = 'normal_child'; return self.external(Await(self.child_task))
                self.phase = 'normal_resources'; return self.child('close_resources', self.scope)
        except BaseException as failure:
            if not self.opened: raise
            return self.start_abort(failure)


class ModeReplayFrame(_ModeFrame):
    PHASES = ('start', 'next', 'invoke', 'done')
    ROOTS = ('mode', 'values', 'definition', 'actions', 'results', 'selected')

    def __init__(self, mode, values):
        super().__init__(); self.mode = mode; self.values = values
        self.definition = self.actions = self.selected = None; self.results = []; self.index = 0

    def advance(self, cursor, value, error):
        if error is not None: raise error
        from .modes import owned, Skill, substitute, data, emit
        e = cursor.owner
        if self.phase == 'start':
            self.definition = owned(self.mode, e, Skill).definition; self.actions = substitute(self.definition, self.values)
        elif self.phase == 'invoke':
            self.results.append(data(value)); data(self.results)
        self.phase = 'next'
        if self.index < len(self.actions):
            self.selected = self.actions[self.index]; e.tick(); emit(e, 'skill.step', step=self.index, tool=self.selected['tool']); self.index += 1
            self.phase = 'invoke'; return self.leaf(e._s_invoke(e.env.get('tool'), [self.selected['tool'], self.selected['input']]))
        return self.done(self.results)


class ModeArtifactFrame(_ModeFrame):
    PHASES = ('start', 'save', 'load', 'done')
    ROOTS = ('arguments', 'definition', 'payload')

    def __init__(self, operation, arguments):
        super().__init__(); self.operation = operation; self.arguments = arguments
        self.definition = self.payload = None

    def advance(self, cursor, value, error):
        if error is not None: raise error
        from .modes import owned, identifier, revision_number, validate_definition, origin_data, make
        from .language import LanguageError
        e = cursor.owner
        if self.phase == 'start':
            if self.operation == 'save_mode':
                mode, key, revision = self.arguments; self.definition = owned(mode, e).definition
                self.payload = {'key': identifier(key, 'key'), 'definition': self.definition}
                if revision_number(revision) is not None: self.payload['revision'] = revision
                self.phase = 'save'; return self.child('host', 'artifact.save', self.payload)
            key, revision = self.arguments
            self.payload = {'key': identifier(key, 'key')}
            if revision_number(revision) is not None: self.payload['revision'] = revision
            self.phase = 'load'; return self.child('host', 'artifact.load', self.payload)
        if self.phase == 'save': return self.done(value)
        key, revision = self.arguments; row = value; self.definition = validate_definition(row['definition'])
        if self.definition['kind'] == 'skill' and row.get('status') != 'ready':
            raise LanguageError('未完成技能不能加载为可执行值', code='mode_error')
        origin = origin_data(row.get('origin'), self.definition)
        if origin is None or origin['key'] != key or origin['revision'] != row.get('revision') or revision is not None and origin['revision'] != revision:
            raise LanguageError('加载来源不匹配请求的模式修订', code='mode_error')
        return self.done(make(self.definition, e, origin))


MODE_FRAMES = (ModeInvokeFrame, ModeHostFrame, ModePlanStepFrame, ModeSerialPlanFrame,
    ModeParallelPlanFrame, ModeParallelBodyFrame, ModeRunPlanFrame, ModePursueFrame,
    ModeRecordFrame, ModeCloseResourcesFrame, ModeReplayFrame, ModeArtifactFrame)


def mode_cursor(evaluator, operation, *arguments):
    from .execution_frames import ModeCloseCursor
    types = dict(invoke=ModeInvokeFrame, host=ModeHostFrame, plan_step=ModePlanStepFrame,
        serial_plan=ModeSerialPlanFrame, parallel_plan=ModeParallelPlanFrame,
        parallel_body=ModeParallelBodyFrame, run_plan=ModeRunPlanFrame, pursue=ModePursueFrame,
        record=ModeRecordFrame, close_resources=ModeCloseResourcesFrame, replay=ModeReplayFrame)
    frame = ModeArtifactFrame(operation, arguments) if operation in ('save_mode', 'load_mode') else types[operation](*arguments)
    return ModeCloseCursor(evaluator, frame)


def mode_protocol(evaluator, operation, *arguments, _cursor=None):
    # Native transport only; construction/validation stays cold until next().
    # A nested child already has its one actual leaf parent and never steals
    # the ambient bridge's pending_cursor or the outer mode auxiliary slot.
    from .execution_frames import ProtocolBridge
    cursor = mode_cursor(evaluator, operation, *arguments) if _cursor is None else _cursor
    if _cursor is None:
        ambient = getattr(evaluator, '_execution_cursor', None)
        if ambient is not None and ambient.frames and type(ambient.frames[-1]) is ProtocolBridge:
            bridge = ambient.frames[-1]
            if bridge.owner is not evaluator or getattr(bridge, 'mode_cursor', None) is not None:
                raise RuntimeError('Mode observer is foreign or occupied')
            bridge.mode_cursor = cursor; cursor.parent_bridge = bridge
    from .execution_frames import Done
    value = error = closing_error = None
    while True:
        if closing_error is not None:
            action = cursor.close_request(closing_error)
            if type(action) is Done:
                if cursor.close_completed: return action.value
                raise cursor.close_signal
            pending = action.request
        else:
            try: pending = cursor.throw(error) if error is not None else cursor.send(value)
            except StopIteration as stop: return stop.value
        try:
            value = yield pending
            error = closing_error = None
        except GeneratorExit as failure:
            value = error = None; closing_error = failure
        except BaseException as failure:
            value = closing_error = None; error = failure


