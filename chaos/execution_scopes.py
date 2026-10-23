"""Data-backed lexical prompt, cancellation and structured-task scopes.

The existing ExecutionCursor and scheduler retain ownership of every child.
Lexical bodies and native prompt handlers use their shared authoritative
control frames on the real cursor.
"""


class PromptScopeFrame:
    def __init__(self, boundary, manager):
        self.boundary, self.manager = boundary, manager
        self.node, self.env = boundary.node, boundary.env
        self.phase = 'start'
        self.scope = self.handler = self.signal = self.result = self.error = None
        self.entered = False

    def roots(self):
        return (self.env, self.manager, self.scope, self.handler,
                self.signal, self.result, self.error)

    def release(self):
        if self.entered:
            from .control_prompts import release
            self.entered = False
            release(self.manager)

    def step(self, cursor, value, error):
        if getattr(self.boundary,'flow_structural',False):
            from .compiled_flow_resources import special_step
            return special_step(self,cursor,value,error,self.advance)
        return self.advance(cursor,value,error)

    def advance(self, cursor, value, error):
        from .execution_frames import Done, Push, ProtocolBridge
        from .execution_protection import ProtectedValueFrame
        from .control_prompts import enter, PromptTransfer, handled
        from .language import Env
        e = cursor.owner
        try:
            if self.phase == 'start':
                if error is not None:
                    raise error
                self.handler = self.manager.handler
                enter(e, self.manager)
                self.entered = True
                self.scope = Env(self.env)
                e.bind_scope_target(self.node, self.manager, self.scope, self.env)
                self.phase = 'body'
                return Push(ProtectedValueFrame(self.node[3], self.scope))
            if self.phase == 'body':
                self.result = value
                if error is not None:
                    self.error = error
                    if not isinstance(error, PromptTransfer) or error.target is not self.manager:
                        raise error
                    self.signal = error
                # The handler executes after the target has left its holder.
                self.release()
                self.scope = None
                if self.signal is not None:
                    self.phase = 'handler'
                    # Keep the original request bridge's capture/tick shape.
                    # handled itself only forwards to the shared typed frame.
                    return Push(ProtocolBridge(handled(e, self.manager, self.handler, self.signal)))
                return Done(self.result)
            if self.phase == 'handler':
                if error is not None:
                    raise error
                self.result = value
                return Done(value)
            raise RuntimeError('Invalid prompt-scope-frame phase')
        except BaseException:
            self.release()
            raise

    def close(self, cursor):
        self.release()
        self.scope = self.handler = self.signal = self.result = self.error = None


class CancelShieldFrame:
    def __init__(self, boundary, manager):
        self.boundary, self.manager = boundary, manager
        self.node, self.env = boundary.node, boundary.env
        self.phase = 'start'
        self.task = self.scope = self.result = self.error = None
        self.pending = self.cancelled = self.notification = None
        self.entered = False

    def roots(self):
        return (self.env, self.manager, self.task, self.scope, self.result,
                self.error, self.pending, self.cancelled, self.notification)

    def retire(self, evaluator):
        """Original stable-event cancellation delivery, after lexical removal."""
        if not self.entered:
            return
        from .cancellation import requested
        from .suspension import error_priority
        scheduler = evaluator.suspension
        value, task = self.manager, self.task
        value.requested = requested(task)
        task.cancel_shields.remove(value)
        value.state, value.depth = 'closed', 0
        self.entered = False
        if not task.cancel_shields:
            self.pending, task.deferred_interrupt = task.deferred_interrupt, None
            if task.cancel_sources and not task.cancel_event.is_set():
                scheduler._cancel(task, next(iter(task.cancel_sources)))
            if task.cancel_event.is_set():
                self.cancelled = scheduler.cancelled_error(task)
                if error_priority(self.cancelled) >= error_priority(self.pending):
                    self.pending = self.cancelled
            if self.pending is not None:
                priority, previous = error_priority(self.pending), error_priority(self.error)
                if priority > previous or priority == previous and priority < 2:
                    self.error = self.pending
        try:
            scheduler.event('shield.closed', value)
        except BaseException as notification:
            self.notification = notification
            if error_priority(notification) > error_priority(self.error):
                self.error = notification

    def finish(self, evaluator):
        from .execution_frames import Done
        self.phase = 'retiring'
        self.retire(evaluator)
        self.scope = None
        if self.error is not None:
            raise self.error
        return Done(self.result)

    def step(self, cursor, value, error):
        if getattr(self.boundary,'flow_structural',False):
            from .compiled_flow_resources import special_step
            return special_step(self,cursor,value,error,self.advance)
        return self.advance(cursor,value,error)

    def advance(self, cursor, value, error):
        from .execution_frames import Push
        from .execution_protection import ProtectedValueFrame
        from .language import Env, LanguageError
        from .cancellation import owned
        e = cursor.owner
        if self.phase == 'start':
            if error is not None:
                raise error
            owned(self.manager, e)
            scheduler = e.suspension
            self.task = scheduler.current.task if scheduler.current else None
            if self.task is None:
                raise LanguageError('CancelShield 必须位于活动 Task 内', code='cancel_shield_error')
            if self.manager.state != 'new':
                raise LanguageError('CancelShield 只能进入一次', code='cancel_shield_error')
            e.tick()
            self.manager.task, self.manager.stream = self.task, scheduler.stream
            self.manager.depth, self.manager.state = len(self.task.cancel_shields)+1, 'active'
            self.task.cancel_shields.append(self.manager)
            self.entered = True
            try:
                scheduler.event('shield.entered', self.manager)
                self.scope = Env(self.env)
                e.bind_scope_target(self.node, self.manager, self.scope, self.env)
            except BaseException as failure:
                self.error = failure
                return self.finish(e)
            self.phase = 'body'
            return Push(ProtectedValueFrame(self.node[3], self.scope))
        if self.phase == 'body':
            self.error, self.result = error, value
            return self.finish(e)
        if error is not None:
            raise error
        raise RuntimeError('Invalid cancel-shield-frame phase')

    def close(self, cursor):
        # Closing the old lexical generator injected GeneratorExit, which is a
        # hard control. It cannot deliver a deferred soft cancellation instead.
        # Retain that priority while doing bookkeeping, without running a body.
        if self.entered:
            closing = GeneratorExit()
            self.error = closing
            self.retire(cursor.owner)
            if self.error is not closing:
                raise self.error
        self.scope = self.result = self.error = self.pending = self.cancelled = self.notification = None


class GroupBodyFrame:
    def __init__(self, node, env, group, capabilities=None, scope=None):
        from .language import Env
        self.node, self.env, self.group = node, env, group
        self.capabilities = capabilities
        self.phase = 'start'
        self.scope = Env(env) if scope is None else scope
        self.outcome = None

    def roots(self):
        return (self.env, self.group, self.scope, self.outcome)

    def step(self, cursor, value, error):
        from .execution_frames import Done, Push
        from .execution_protection import ProtectedValueFrame
        from .language import Env, ReturnSignal, BreakSignal, ContinueSignal
        from .suspension import GeneratorClose
        from .task_groups import GroupAbort, ScopeOutcome
        from .control_prompts import NonlocalTransfer
        if self.phase == 'start' and error is None:
            try:
                cursor.owner.bind_scope_target(self.node, self.group, self.scope, self.env)
                self.phase = 'body'
                if getattr(self,'_flow_parent_state',None) is not None:
                    from .compiled_flow_resources import group_body_child
                    return group_body_child(self,cursor)
                return Push(ProtectedValueFrame(self.node[3], self.scope))
            except BaseException as failure:
                error = failure
        if getattr(self,'_flow_state',None)is not None:
            from .compiled_flow_resources import group_body_finished
            group_body_finished(self,cursor)
        if error is None:
            self.outcome = ScopeOutcome(value=value)
        elif isinstance(error, (ReturnSignal, BreakSignal, ContinueSignal,
                                GeneratorClose, GroupAbort, NonlocalTransfer)):
            self.outcome = ScopeOutcome(signal=error)
        else:
            self.outcome = ScopeOutcome(error=error)
        self.phase = 'done'
        return Done(self.outcome)

    def close(self, cursor):
        if getattr(self,'_flow_state',None)is not None:
            from .compiled_flow_resources import group_body_finished
            group_body_finished(self,cursor)
        self.scope = self.outcome = None


class TaskGroupFrame:
    def __init__(self, boundary, manager):
        self.boundary, self.manager = boundary, manager
        self.node, self.env = boundary.node, boundary.env
        self.phase = 'start'
        self.scope = self.body_cursor = self.outcome = self.result = self.error = None

    def roots(self):
        return (self.env, self.manager, self.scope, self.body_cursor,
                self.outcome, self.result, self.error)

    def step(self, cursor, value, error):
        if getattr(self.boundary,'flow_structural',False):
            from .compiled_flow_resources import special_step
            return special_step(self,cursor,value,error,self.advance)
        return self.advance(cursor,value,error)

    def advance(self, cursor, value, error):
        from .execution_frames import ExecutionCursor, Done, External
        from .language import Env, LanguageError
        from .task_groups import owned, GroupRun
        from .agent_tools import current_caps
        if error is not None:
            self.error = error
            raise error
        if self.phase == 'start':
            e = cursor.owner
            owned(self.manager, e)
            if self.node[0] not in ('async_with', 'async_with_pattern'):
                raise LanguageError('TaskGroup 需要 async with', code='argument_error')
            if self.manager.state != 'new':
                raise LanguageError('TaskGroup 只能进入一次', code='task_group_error')
            self.scope = Env(self.env)
            self.body_cursor = ExecutionCursor(e, GroupBodyFrame(self.node, self.env, self.manager,
                                                               current_caps(e), self.scope))
            self.phase = 'group'
            return External(GroupRun(self.manager, self.body_cursor))
        if self.phase == 'group':
            self.outcome = value
            self.body_cursor = self.scope = None
            if self.outcome.signal is not None:
                raise self.outcome.signal
            self.result = self.outcome.value
            return Done(self.result)
        raise RuntimeError('Invalid task-group-frame phase')

    def close(self, cursor):
        # Started group members remain owned/drained by the original scheduler.
        # An unsubmitted child has no language effects to run while closing.
        if self.manager.state == 'new' and self.body_cursor is not None:
            self.body_cursor.close()
        self.scope = self.body_cursor = self.outcome = self.result = self.error = None
