"""Native dynamic control state on the original authoritative execution cursor.

Callbacks retain their real invocation frame and logical scheduler context.
These frames unwind dynamic extents; they do not save or replay continuations.
"""
from dataclasses import dataclass


@dataclass(frozen=True, eq=False)
class CallbackRequest:
    function: object
    args: object = ()
    authority: object = None


@dataclass(frozen=True, eq=False)
class PromptHandlerRequest:
    scope: object
    handler: object
    signal: object


class _AuthorityFrame:
    def enter_authority(self, evaluator, capabilities):
        self.authority_runner = evaluator.suspension.current
        if self.authority_runner is not None:
            self.previous_authority = self.authority_runner.capabilities
            previous = self.previous_authority
            self.authority_runner.capabilities = (capabilities if previous is None else
                                                  previous if capabilities is None else
                                                  previous & capabilities)
        self.authority_entered = True

    def release_authority(self):
        if self.authority_entered:
            self.authority_entered = False
            if self.authority_runner is not None:
                self.authority_runner.capabilities = self.previous_authority


class ControlCallbackFrame(_AuthorityFrame):
    def __init__(self, function, args=(), authority=None):
        self.function, self.args, self.authority = function, args, authority
        self.phase = 'start'
        self.authority_runner = self.previous_authority = None
        self.authority_entered = False
        self.result = self.error = None

    def roots(self):
        from .capture_frames import Edge
        args = tuple(self.args) if type(self.args) in (tuple, list) else (self.args,)
        return (Edge(self.owner, self.authority,
                     (self.function, *args, self.result, self.error)),
                Edge(self.owner, self.previous_authority, (self.authority_runner,)),
                self.authority, self.previous_authority)

    def step(self, cursor, value, error):
        from .execution_frames import Done, Push, InvocationFrame, InvocationRequest
        try:
            if error is not None:
                self.error = error
                raise error
            if self.phase == 'start':
                self.enter_authority(cursor.owner, self.authority)
                self.phase = 'invoke'
                return Push(InvocationFrame(InvocationRequest(
                    self.function, list(self.args), options={
                        'force': True, '_context_callback': True, 'all_results': True})))
            if self.phase == 'invoke':
                self.result = value
                self.release_authority()
                return Done(self.result)
            raise RuntimeError('Invalid control-callback-frame phase')
        except BaseException:
            self.release_authority()
            raise

    def close(self, cursor):
        self.release_authority()
        self.function = self.args = self.result = self.error = None


class PromptHandlerFrame(_AuthorityFrame):
    def __init__(self, scope, handler, signal):
        self.scope, self.handler, self.signal = scope, handler, signal
        self.phase = 'start'
        self.ceiling = self.completion = self.result = self.error = None
        self.authority_runner = self.previous_authority = None
        self.authority_entered = False

    def roots(self):
        from .capture_frames import Edge
        from .conditions import intersection
        ceiling = intersection(self.ceiling, getattr(self.scope, 'capabilities', None),
                               getattr(self.signal, 'authority', None))
        return (Edge(self.owner, ceiling,
                     (self.scope, self.handler, self.signal, self.completion,
                      self.result, self.error)),
                Edge(self.owner, self.previous_authority, (self.authority_runner,)),
                self.ceiling, self.previous_authority)

    def step(self, cursor, value, error):
        from .execution_frames import Done, Push
        from .conditions import intersection
        from .agent_tools import current_caps
        from .multiple_values import components, prepare
        e = cursor.owner
        try:
            if error is not None:
                self.error = error
                raise error
            if self.phase == 'start':
                self.ceiling = intersection(self.scope.capabilities,
                                            self.signal.authority, current_caps(e))
                self.enter_authority(e, self.ceiling)
                self.completion = prepare(e, self.signal.completion)
                if self.handler is None:
                    self.result = self.completion
                    self.release_authority()
                    return Done(self.result)
                self.phase = 'handler'
                return Push(ControlCallbackFrame(self.handler,
                                                 components(self.completion, e),
                                                 self.ceiling))
            if self.phase == 'handler':
                self.result = value
                self.release_authority()
                return Done(self.result)
            raise RuntimeError('Invalid prompt-handler-frame phase')
        except BaseException:
            self.release_authority()
            raise

    def close(self, cursor):
        self.release_authority()
        self.scope = self.handler = self.signal = self.completion = None
        self.result = self.error = None


class PromptCallFrame:
    def __init__(self, request):
        self.operation = request.operation
        if self.operation == 'prompt':
            self.tag, self.body, self.handler = request.values
        else:
            self.tag = self.handler = None
            self.body = request.values[0]
        self.phase = 'start'
        self.manager = self.escape = self.signal = self.result = self.error = None
        self.args = ()
        self.entered = False

    def roots(self):
        args = tuple(self.args) if type(self.args) in (tuple, list) else (self.args,)
        return (self.tag, self.body, self.handler, self.manager, self.escape,
                *args, self.signal, self.result, self.error)

    def release(self):
        if self.entered:
            from .control_prompts import release
            self.entered = False
            release(self.manager)

    def step(self, cursor, value, error):
        from .execution_frames import Done, Push
        from .control_prompts import PromptScope, EscapeContinuation, PromptTransfer, enter, tag
        e = cursor.owner
        try:
            if self.phase == 'start':
                if error is not None:
                    raise error
                if self.operation == 'escape':
                    self.tag = tag(e)
                self.manager = PromptScope(e, self.tag, self.handler)
                if self.operation == 'escape':
                    scheduler = e.suspension
                    self.escape = EscapeContinuation(scheduler, scheduler.epoch, self.manager,
                                                     'escape-'+str(scheduler.next_id()),
                                                     self.manager.capabilities)
                    self.args = (self.escape,)
                enter(e, self.manager)
                self.entered = True
                self.phase = 'body'
                return Push(ControlCallbackFrame(self.body, self.args))
            if self.phase == 'body':
                self.result, self.error = value, error
                if error is not None:
                    if not isinstance(error, PromptTransfer) or error.target is not self.manager:
                        raise error
                    self.signal = error
                self.release()
                if self.signal is not None:
                    self.phase = 'handler'
                    return Push(PromptHandlerFrame(self.manager, self.handler, self.signal))
                return Done(self.result)
            if self.phase == 'handler':
                self.result, self.error = value, error
                if error is not None:
                    raise error
                return Done(self.result)
            raise RuntimeError('Invalid prompt-call-frame phase')
        except BaseException:
            self.release()
            raise

    def close(self, cursor):
        self.release()
        self.body = self.handler = self.escape = self.signal = self.result = self.error = None
        self.args = ()


class ControlBarrierFrame:
    def __init__(self, body):
        self.body = body
        self.phase = 'start'
        self.manager = self.result = self.error = None
        self.entered = False

    def roots(self):
        return (self.body, self.manager, self.result, self.error)

    def release(self):
        if self.entered:
            from .control_prompts import release
            self.entered = False
            release(self.manager)

    def step(self, cursor, value, error):
        from .execution_frames import Done, Push
        from .control_prompts import Barrier, enter
        try:
            if error is not None:
                self.error = error
                raise error
            if self.phase == 'start':
                self.manager = Barrier(cursor.owner)
                enter(cursor.owner, self.manager)
                self.entered = True
                self.phase = 'body'
                return Push(ControlCallbackFrame(self.body))
            if self.phase == 'body':
                self.result = value
                self.release()
                return Done(self.result)
            raise RuntimeError('Invalid control-barrier-frame phase')
        except BaseException:
            self.release()
            raise

    def close(self, cursor):
        self.release()
        self.body = self.result = self.error = None


class DynamicWindFrame:
    def __init__(self, before, body, after):
        self.before, self.body, self.after = before, body, after
        self.phase = 'start'
        self.result = self.pending_error = self.error = None
        self.previous_error = self.previous_context = None
        self.error_context_entered = False

    def roots(self):
        return (self.before, self.body, self.after, self.result, self.pending_error,
                self.error, self.previous_error, self.previous_context)

    def restore_error_context(self, evaluator):
        if self.error_context_entered:
            self.error_context_entered = False
            evaluator.handled_error, evaluator.error_context = self.previous_error, self.previous_context

    def step(self, cursor, value, error):
        from .execution_frames import Done, Push
        from .language import LanguageError
        from .suspension import hard_error
        e = cursor.owner
        if self.phase == 'after_error':
            self.error = error
            self.restore_error_context(e)
            if error is not None:
                raise error
            raise self.pending_error
        if self.phase == 'body':
            self.result, self.pending_error = value, error
            if error is not None:
                if hard_error(error):
                    raise error
                self.previous_error, self.previous_context = e.handled_error, e.error_context
                self.error_context_entered = True
                if isinstance(error, LanguageError):
                    e.handled_error = e.error_context = error
                self.phase = 'after_error'
                return Push(ControlCallbackFrame(self.after))
            self.phase = 'after_success'
            return Push(ControlCallbackFrame(self.after))
        if error is not None:
            self.error = error
            raise error
        if self.phase == 'start':
            self.phase = 'before'
            return Push(ControlCallbackFrame(self.before))
        if self.phase == 'before':
            self.phase = 'body'
            return Push(ControlCallbackFrame(self.body))
        if self.phase == 'after_success':
            return Done(self.result)
        raise RuntimeError('Invalid dynamic-wind-frame phase')

    def close(self, cursor):
        self.restore_error_context(cursor.owner)
        self.before = self.body = self.after = self.result = self.pending_error = self.error = None
        self.previous_error = self.previous_context = None


def control_frame(request):
    """One router used by the thin gateways and active protocol bridges."""
    from .control_prompts import Request, fail
    from .execution_continuations import continuation_frame
    continuation=continuation_frame(request)
    if continuation is not None:return continuation
    if type(request) is CallbackRequest:
        return ControlCallbackFrame(request.function, request.args, request.authority)
    if type(request) is PromptHandlerRequest:
        return PromptHandlerFrame(request.scope, request.handler, request.signal)
    if type(request) is Request:
        if request.operation in ('prompt', 'escape'):
            return PromptCallFrame(request)
        if request.operation == 'barrier':
            return ControlBarrierFrame(request.values[0])
        if request.operation == 'wind':
            return DynamicWindFrame(*request.values)
        fail('Unknown control request', 'argument_error')
    return None
