"""CFG child routing for the actual existing protected semantic frames.

Pattern partitioning, context/error priority and tail resolution stay in the
original frames. The backend selects the real compiled child and guard/else
successor. No second execution cursor or protected interpreter is introduced.
"""


def descriptor(frame):
    return frame._flow_region


def validate_operation(state, boundary):
    if boundary.node[0] not in ('try','try_cases','try_group','finally','try_else'):
        return
    frame = getattr(boundary,'flow_operation',None)
    if frame is None or not hasattr(frame,'_flow_region'):return
    expected = state.program.instructions[boundary.flow_index+1]
    if expected[0] != 'protect' or frame._flow_region is not expected:state.malformed()
    keys = tuple(row[0] for row in expected[2])
    for name in ('_flow_segment','_flow_completed_segment'):
        value=getattr(frame,name)
        if value is not None and not any(value is key for key in keys):state.malformed()
    ready=frame._flow_ready_event
    if ready is not None and not any(ready is state.program.instructions[pc]for key,flag,pc in expected[2]):state.malformed()


def _route(frame, key):
    from .compiled_flow_frames import take
    state = frame.boundary.flow_state
    row = descriptor(frame)
    if key not in {entry[0] for entry in row[2]}: state.malformed()
    for actual, flag, entry in row[2]: state.flags[flag] = int(actual == key)
    state.pc = row[4]
    return take(state)


def child_key(frame):
    from .execution_protection import TryFrame, FinallyFrame, TryCompletionFrame
    from .execution_group_handlers import GroupTryFrame
    phase = frame.phase
    if type(frame) is TryFrame:
        return (phase, frame.index - 1) if phase in ('guard','handler') else ('body',0)
    if type(frame) is GroupTryFrame:
        return (phase, frame.case_index) if phase in ('guard','handler') else ('body',0)
    if type(frame) is FinallyFrame: return (phase,0)
    if type(frame) is TryCompletionFrame: return (phase,0)
    frame.boundary.flow_state.malformed()


def decide(frame, truth):
    """Backend guard/normal-completion branch, after the real Bool check."""
    from .compiled_flow_frames import take
    state = frame.boundary.flow_state
    if state._unwind_error is not None: return truth
    key = frame._flow_completed_segment
    slots = dict(descriptor(frame)[3])
    if key not in slots: state.malformed()
    state.flags[slots[key]] = int(truth)
    event = take(state)
    parent = frame.boundary.flow_index
    if event is None: state.malformed()
    if event[0] == 'protect_select':
        if event[1] != parent: state.malformed()
        return False
    if event[0] == 'protect_finish':
        if event[1] != parent: state.malformed()
        return False
    if event[0] == 'protect_guard':
        if event[1] != parent: state.malformed()
        return event[2]
    if event[0] not in ('enter','eval','await','yield','delegate'): state.malformed()
    # This is the actual backend-selected handler/else root, not a copy of the
    # child already chosen by the old semantic frame.
    frame._flow_ready_event = event
    return True


def protected_value_child(frame):
    from .compiled_flow_frames import make_boundary
    from .execution_frames import Push
    return Push(make_boundary(frame._flow_state, frame._flow_event, frame.env,
                              True, {'all_results':True}))


def step(frame, cursor, value, error, advance):
    from .compiled_flow_frames import take, make_boundary, retire_transfer
    from .execution_frames import Push, Done, EvaluationBoundaryFrame
    from .execution_protection import ProtectedValueFrame, FinallyFrame
    state = frame.boundary.flow_state
    if not hasattr(frame,'_flow_region'):
        region = take(state)
        if region is None or region[0] != 'protect' or region[1] is not frame.node: state.malformed()
        frame._flow_region = region
        frame._flow_segment = frame._flow_completed_segment = frame._flow_ready_event = None
    elif frame._flow_segment is not None and error is None and state._unwind_error is None:
        completed = take(state)
        if completed is None or completed != ('protect_return',frame.boundary.flow_index,frame._flow_segment): state.malformed()
        frame._flow_completed_segment = frame._flow_segment
        frame._flow_segment = None
    pending = frame.pending_error if type(frame) is FinallyFrame else None
    try:
        action = advance(cursor,value,error)
    except BaseException as failure:
        if pending is not None and failure is not pending: retire_transfer(state,pending)
        raise
    if type(action) is Push:
        if state._unwind_error is not None:
            # The already-owned original protection frame may drain its actual
            # handler/cleanup node after a single injected metadata fault. Its
            # invalid compiled PC is never reset, repaired or re-entered.
            return action
        event = frame._flow_ready_event
        frame._flow_ready_event = None
        if event is None: event = _route(frame,child_key(frame))
        if event is None or event[0] not in ('enter','eval','await','yield','delegate'): state.malformed()
        actual = next((key for key,flag,pc in descriptor(frame)[2] if pc == state._event_index),None)
        if actual is None: state.malformed()
        frame._flow_segment = actual
        state.result = None
        child = action.frame
        if type(child) is ProtectedValueFrame:
            child._flow_state,child._flow_event = state,event
            return action
        if type(child) is EvaluationBoundaryFrame:
            return Push(make_boundary(state,event,child.env,child.tail,child.options))
        state.malformed()
    if type(action) is Done:
        if state._unwind_error is not None: raise state._unwind_error
        state.pc = descriptor(frame)[5]
        event = take(state)
        if event is None or event[0] != 'exit' or event[1] is not frame.node: state.malformed()
        state.result = action.value
        return action
    state.malformed()
