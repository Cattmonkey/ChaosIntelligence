"""CFG-selected children of the original lexical resource/scope frames.

The original frames own acquisition, exit, suppression and structured cleanup.
TaskGroup retains its original child cursor; only its selected body segment has
an invocation-local controller on that cursor, using the same sealed program.
"""


def context(frame):
    return getattr(frame,'_flow_resource_context',frame)


def validate_operation(state,boundary):
    from .execution_resources import ContextFrame
    from .execution_scopes import PromptScopeFrame,CancelShieldFrame,TaskGroupFrame
    if boundary.node[0] not in ContextFrame.KINDS:return
    frame=getattr(boundary,'flow_operation',None)
    if frame is None or not hasattr(frame,'_flow_resource_region'):return
    original=context(frame)
    if type(original)is not ContextFrame or original.boundary is not boundary:state.malformed()
    region=state.program.instructions[boundary.flow_index+1]
    if region[0]!='resource' or original._flow_resource_region is not region or frame._flow_resource_region is not region:state.malformed()
    if original._flow_resource_segment is not None and not any(original._flow_resource_segment is row[0] for row in region[2]):state.malformed()
    if type(frame) not in (ContextFrame,PromptScopeFrame,CancelShieldFrame,TaskGroupFrame):state.malformed()
    expected=('manager' if type(frame)is ContextFrame and frame.phase=='manager' else
              'body' if type(frame)is ContextFrame and frame.phase=='body' or type(frame)in(PromptScopeFrame,CancelShieldFrame)and frame.phase=='body' or type(frame)is TaskGroupFrame and frame.phase=='group' else None)
    if state._unwind_error is None and original._flow_resource_segment!=expected:state.malformed()
    child=getattr(frame,'_flow_group_state',None)
    if child is not None:
        from .compiled_flow_state import FlowState
        if type(frame)is not TaskGroupFrame or type(child)is not FlowState or child.owner is not state.owner or child.program is not state.program or child.flags is not state.flags:state.malformed()
        if not child.closed and (child._segment_parent is not state or child._segment_frame is not frame):state.malformed()


def select(frame,key):
    from .compiled_flow_frames import take
    original=context(frame);state=original.boundary.flow_state
    region=original._flow_resource_region
    row=next((row for row in region[2] if row[0]==key),None)
    if row is None:state.malformed()
    original._flow_resource_segment=row[0]
    for actual,flag,entry,end in region[2]:state.flags[flag]=int(actual==key)
    state.pc=region[3]
    event=take(state)
    if event is None or state._event_index!=row[2] or event[0]not in ('enter','eval','await','yield','delegate'):state.malformed()
    return event


def complete(frame,error,value):
    from .execution_scopes import TaskGroupFrame
    from .compiled_flow_frames import take
    original=context(frame);state=original.boundary.flow_state
    key=original._flow_resource_segment
    if key is None:return
    failure=error
    if type(frame)is TaskGroupFrame and frame.phase=='group':
        child=getattr(frame,'_flow_group_state',None)
        if child is not None:
            if not child.closed:state.malformed()
            # A ScopeOutcome signal is delivered by the original Group frame
            # before a parent loop/return successor may be dispatched.
            failure=failure or getattr(value,'signal',None) or getattr(value,'error',None)
            if failure is None and state._unwind_error is None:
                if frame._flow_group_pc is None:state.malformed()
                state.pc=frame._flow_group_pc
            frame._flow_group_state=None;frame._flow_group_pc=None
    if failure is None and state._unwind_error is None:
        event=take(state)
        if event!=('resource_return',original.boundary.flow_index,key):state.malformed()
    original._flow_resource_segment=None


def step(frame,cursor,value,error,advance):
    from .execution_frames import Push,Done,EvaluationBoundaryFrame,ProtocolBridge
    from .execution_protection import ProtectedValueFrame
    from .execution_resources import ContextFrame
    from .execution_scopes import PromptScopeFrame,CancelShieldFrame,TaskGroupFrame,GroupBodyFrame
    from .compiled_flow_frames import take,make_boundary,retire_transfer
    original=context(frame);state=frame.boundary.flow_state
    if not hasattr(original,'_flow_resource_region'):
        region=take(state)
        if region is None or region[0]!='resource' or region[1] is not frame.node:state.malformed()
        original._flow_resource_region=region;original._flow_resource_segment=None
    frame._flow_resource_region=original._flow_resource_region
    complete(frame,error,value)
    pending=original.pending_error if type(frame)is ContextFrame else None
    try:action=advance(cursor,value,error)
    except BaseException as failure:
        if pending is not None and failure is not pending:retire_transfer(state,pending)
        if error is not None and failure is not error:retire_transfer(state,error)
        raise
    if type(action)is Push:
        child=action.frame
        if type(child)in(PromptScopeFrame,CancelShieldFrame,TaskGroupFrame):
            child._flow_resource_context=original
            child._flow_resource_region=original._flow_resource_region
            return action
        if type(child)is ProtocolBridge:return action
        if state._unwind_error is not None:return action
        if type(child)is EvaluationBoundaryFrame:
            event=select(frame,'manager')
            if event[1] is not child.node:state.malformed()
            state.result=None
            return Push(make_boundary(state,event,child.env,child.tail,child.options))
        if type(child)is ProtectedValueFrame:
            event=select(frame,'body')
            if event[1] is not child.node:state.malformed()
            state.result=None
            child._flow_state,child._flow_event=state,event
            return action
        state.malformed()
    from .execution_frames import External
    if type(action)is External:
        if type(frame)is not TaskGroupFrame:return action
        if state._unwind_error is not None:return action
        from .task_groups import GroupRun
        if type(action.request)is not GroupRun or action.request.iterator is not frame.body_cursor:state.malformed()
        body=frame.body_cursor.frames[0]
        if type(body)is not GroupBodyFrame:state.malformed()
        event=select(frame,'body')
        if event[1] is not body.node[3]:state.malformed()
        body._flow_parent_state=state;body._flow_parent_frame=frame;body._flow_body_event=event
        body._flow_body_index=state._event_index
        row=next(row for row in original._flow_resource_region[2]if row[0]=='body')
        body._flow_body_end=row[3]
        frame._flow_group_state=None;frame._flow_group_pc=None
        return action
    if type(action)is Done:
        if type(frame)is not ContextFrame:
            if getattr(frame.boundary,'flow_operation',None)is frame:frame.boundary.flow_operation=original
            return action
        if state._unwind_error is not None:raise state._unwind_error
        state.pc=original._flow_resource_region[4]
        event=take(state)
        if event is None or event[0]!='exit' or event[1] is not frame.node:state.malformed()
        state.result=action.value
        return action
    state.malformed()


def group_body_child(frame,cursor):
    """Attach only when the original GroupBodyFrame actually enters its body."""
    from .compiled_flow_state import FlowState
    from .compiled_flow_frames import make_boundary
    from .execution_frames import Push
    from .execution_protection import ProtectedValueFrame
    parent=frame._flow_parent_state;holder=frame._flow_parent_frame
    parent.validate(expected_owner=cursor.owner)
    if holder._flow_group_state is not None:parent.malformed()
    child=FlowState(parent._plan,cursor.owner,parent.function,frame.scope,in_stack=True)
    child._plan=parent._plan;child._regions=parent._regions
    child.flags=parent.flags;child.portable=parent.portable
    child.native_counted=parent.native_counted;child.portable_counted=parent.portable_counted
    child.capabilities=frame.capabilities
    child.pc=frame._flow_body_index+1
    child._event_index=frame._flow_body_index;child._reserved=None
    child._cursor=child._root_boundary=None
    child._segment_parent=parent;child._segment_frame=holder;child._segment_end=frame._flow_body_end
    holder._flow_group_state=frame._flow_state=child
    pv=ProtectedValueFrame(frame.node[3],frame.scope)
    pv._flow_state,pv._flow_event=child,frame._flow_body_event
    # The original ProtectedValueFrame creates the boundary and resolves any
    # pending tail invocation before GroupBodyFrame creates its ScopeOutcome.
    child._segment_entry=frame._flow_body_event
    return Push(pv)


def segment_boundary(state,boundary):
    if getattr(state,'_segment_parent',None)is not None and state._root_boundary is None:
        if boundary.flow_event is not state._segment_entry:state.malformed()
        state._root_boundary=boundary


def segment_leaving(state):
    parent=getattr(state,'_segment_parent',None)
    if parent is None:return
    frame=state._segment_frame
    if frame._flow_group_state is not state:state.malformed()
    frame._flow_group_pc=state.pc if state.pc==state._segment_end else None
    if state._unwind_error is not None and not parent.closed:parent._unwind_error=state._unwind_error
    state._segment_parent=state._segment_frame=state._segment_entry=None


def validate_segment(state):
    parent=getattr(state,'_segment_parent',None)
    if parent is None:return
    from .compiled_flow_state import FlowState
    from .execution_scopes import TaskGroupFrame
    if type(parent)is not FlowState or parent.owner is not state.owner or parent.epoch!=state.epoch or parent.program is not state.program or parent.flags is not state.flags or parent.closed:state.malformed()
    frame=state._segment_frame
    if type(frame)is not TaskGroupFrame or frame.boundary.flow_state is not parent or frame._flow_group_state is not state:state.malformed()
    row=next((row for row in context(frame)._flow_resource_region[2]if row[0]=='body'),None)
    if row is None or state._segment_end!=row[3] or state._segment_entry is not state.program.instructions[row[2]]:state.malformed()
    start=row[2]
    if not start<state.pc<=state._segment_end or type(state._event_index)is not int or not start<=state._event_index<state._segment_end:state.malformed()


def special_step(frame,cursor,value,error,advance):
    try:return step(frame,cursor,value,error,advance)
    except BaseException as failure:
        from .execution_scopes import PromptScopeFrame,CancelShieldFrame,TaskGroupFrame
        if type(frame)is PromptScopeFrame:frame.release()
        elif type(frame)is CancelShieldFrame and frame.entered:
            frame.error=failure;frame.retire(cursor.owner)
            failure=frame.error
        elif type(frame)is TaskGroupFrame:frame.close(cursor)
        if getattr(frame.boundary,'flow_operation',None)is frame:frame.boundary.flow_operation=context(frame)
        raise failure


def group_body_finished(frame,cursor):
    state=getattr(frame,'_flow_state',None)
    if state is None or state.closed:return
    if state._owned_activations:state.malformed()
    active=getattr(cursor,'_compiled_flows',None)
    if type(active)is list:active[:]=[row for row in active if row is not state]
    segment_leaving(state)
    state.frames=[];state.finish()
