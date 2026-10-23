"""Release consumed execution inputs after the original transport has drained.

No close/send/throw, callbacks, authority changes, graph projection or collector
is performed here. Terminal result/error and all observable handle metadata stay
on their original objects. Unresolved opaque transports remain conservative roots.
"""
from types import GeneratorType


def _drained(cursor, owner, consumed=None):
    from .execution_frames import (ExecutionCursor, GenericCloseCursor,
        PatternCloseCursor, ModuleCloseCursor, SuspensionCloseCursor,
        MultipleValuesCloseCursor, InvocationCloseCursor, ModeCloseCursor,
        ProtocolBridge)
    classes = (ExecutionCursor, GenericCloseCursor, PatternCloseCursor,
        ModuleCloseCursor, SuspensionCloseCursor, MultipleValuesCloseCursor,
        InvocationCloseCursor, ModeCloseCursor)
    if type(cursor) not in classes:
        return False
    todo, seen, verified = [cursor], set(), []
    while todo:
        current = todo.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if (type(current) not in classes or getattr(current, 'owner', None) is not owner
                or getattr(current, 'closed', None) is not True
                or type(getattr(current, 'frames', None)) is not list
                or current.frames or getattr(current, 'close_pending', False)
                or getattr(current, 'pending_request', None) is not None):
            return False
        if (type(current) is not ExecutionCursor
                and getattr(current, 'epoch', None) != owner.suspension.epoch):
            return False
        if getattr(current, '_compiled_flows', None):
            return False
        module = getattr(current, 'module_state', None)
        if module is not None:
            # Legacy module loading borrows every cursor as an observer until
            # the whole module completes. It is not this handle's live child.
            # Keep the original attachment/previous-scope restoration intact.
            from .execution_modules import LegacyModuleLoadFrame
            if (type(module) is not LegacyModuleLoadFrame
                    or module.owner is not owner
                    or module.epoch != owner.suspension.epoch
                    or module.phase not in module.PHASES
                    or type(module.attachments) is not list):
                return False
            attached = None
            for row in reversed(module.attachments):
                if type(row) is tuple and len(row) == 3 and row[0] is current:
                    attached = row
                    break
            if (attached is None or type(attached[1]) is not bool
                    or attached[2] is not None and (
                        type(attached[2]) is not LegacyModuleLoadFrame
                        or attached[2].owner is not owner
                        or attached[2].epoch != owner.suspension.epoch)):
                return False
        bridge = getattr(current, 'root_bridge', None)
        if bridge is not None:
            if type(bridge) is not ProtocolBridge:
                return False
            # Exact builtin generator state is structural exhaustion evidence;
            # no Python locals, closures or guest iterator methods are read. An
            # ignored GeneratorExit may leave it live after the outer cursor
            # closes, so a known carrier type alone is not sufficient.
            if (type(getattr(bridge, 'iterator', None)) is not GeneratorType
                    or bridge.iterator.gi_frame is not None):
                return False
            for name in ('pending_cursor', 'dispatch_cursor', 'mode_cursor'):
                child = getattr(bridge, name, None)
                if child is not None:
                    if (type(child) not in classes
                            or getattr(child, 'parent_bridge', None) is not bridge
                            or name == 'dispatch_cursor' and type(child) is not InvocationCloseCursor
                            or name == 'mode_cursor' and type(child) is not ModeCloseCursor):
                        return False
                    todo.append(child)
        verified.append(current)
    # Publish cleanup targets only after the complete owned transport graph has
    # passed. A later live/foreign child must not cause partial detachment.
    if consumed is not None:
        consumed.extend(verified)
    return True


def retire_execution(scheduler, handle):
    """Idempotently drop actual consumed roots; never manufacture completion."""
    from .suspension import Task, Generator, AsyncGenerator, Runner, TERMINAL
    if (type(handle) not in (Task, Generator, AsyncGenerator)
            or handle.owner is not scheduler or handle.epoch != scheduler.epoch
            or handle.state not in TERMINAL):
        return False
    runner = handle.runner
    if type(runner) is not Runner:
        return False
    if getattr(runner, 'execution_retired', False):
        return True
    if type(handle) is Task:
        if (runner.task is not handle or runner.state not in TERMINAL
                or scheduler.current is runner or scheduler.notifying_terminals
                or any(item is handle for item in scheduler.terminal_queue)):
            return False
    elif scheduler.stream is handle or handle.busy:
        return False
    consumed = []
    if (runner.future is not None or runner.waiting is not None
            or not _drained(runner.iterator, scheduler.evaluator, consumed)):
        return False
    # A module observer ledger can still own the exhausted cursor. A closed
    # never-started native carrier may physically retain its InvocationRequest
    # until that carrier object is released. Drop only our consumed bridge
    # reference; never mutate/close native iterators or the borrowed ledger.
    for cursor in consumed:
        cursor.root_bridge = None
    # Assign, do not mutate: lexical scope tasks/reentrant drivers can alias
    # capture lists and condition/control/context state with their live caller.
    handle.capture_roots = ()
    runner.frames = []
    runner.capture_frames = []
    runner.depth = 0
    for name in ('value', 'waiting', 'future', 'await_owned', 'effect_kind',
                 'effect_event', 'deferred_error', 'stream', 'handled_error',
                 'error_context', 'decimal_state', 'condition_state',
                 'control_state', 'context_state', 'module_scope',
                 'team_scope', 'bot_guard'):
        setattr(runner, name, None)
    if type(handle) is not Task:
        # A stream borrows its consumer Task; retiring the stream must not retain
        # that Task or change its actual lifecycle, result or cancellation state.
        runner.task = None
    runner.execution_retired = True
    return True
