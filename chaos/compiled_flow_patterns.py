"""CFG child selection on the original native-close pattern algorithm.

Ordered matching and atomic binding/assignment remain the original helpers.
Every selected child traverses the original leaf/native transport and executes
on its original ambient outer cursor, with no second matcher or flow driver.
"""


def state_of(frame):
    from .execution_frames import EvaluationBoundaryFrame
    from .compiled_flow_state import FlowState
    from .language import LanguageError
    boundary=getattr(frame,'_flow_boundary',None)
    if type(boundary)is not EvaluationBoundaryFrame:raise LanguageError('Malformed compiled pattern boundary',code='ownership_error')
    state=getattr(boundary,'flow_state',None)
    if type(state)is not FlowState or state.owner is not frame.owner or boundary.node is not frame.node or getattr(boundary,'flow_operation',None)is not frame:raise LanguageError('Foreign compiled pattern boundary',code='ownership_error')
    if type(getattr(frame,'_flow_pattern_drain',None))is not bool or type(getattr(frame,'_flow_pattern_native_close',None))is not bool:state.malformed()
    return state


def key_of(frame):
    if frame.phase in ('subject','rhs'):return(frame.phase,0)
    if frame.phase in ('guard','body'):return(frame.phase,frame.index-1)
    state_of(frame).malformed()


def validate_operation(state,boundary):
    from .execution_patterns import PatternSyntaxFrame
    if boundary.node[0]not in PatternSyntaxFrame.KINDS:return
    frame=getattr(boundary,'flow_operation',None)
    if frame is None:return
    if type(frame)is not PatternSyntaxFrame or frame.owner is not state.owner or frame._flow_boundary is not boundary or frame.node is not boundary.node:state.malformed()
    region=getattr(frame,'_flow_pattern_region',None)
    if region is None:
        if state.phase in('host','checkpoint'):state.malformed()
        return
    expected=state.program.instructions[boundary.flow_index+1]
    if expected[0]!='pattern'or region is not expected:state.malformed()
    if frame._flow_pattern_segment is not None and not any(frame._flow_pattern_segment is row[0]for row in region[2]):state.malformed()
    if state._unwind_error is None and state.phase in('host','checkpoint')and frame.phase in('subject','rhs','guard','body')and frame._flow_pattern_segment!=key_of(frame):state.malformed()
    event=frame._flow_pattern_ready
    if event is not None and not any(event is state.program.instructions[row[2]]for row in region[2]):state.malformed()


def select(frame,key):
    from .compiled_flow_frames import take
    state=state_of(frame);region=frame._flow_pattern_region
    row=next((row for row in region[2]if row[0]==key),None)
    if row is None:state.malformed()
    frame._flow_pattern_segment=row[0]
    for actual,flag,entry,end in region[2]:state.flags[flag]=int(actual==key)
    state.pc=region[4]
    event=take(state)
    if event is None or state._event_index!=row[2]:state.malformed()
    return event


def evaluate(frame,evaluator,node,env,*,body=False,regions=False):
    from .compiled_flow_frames import FlowBodyRequest,make_boundary
    from .execution_frames import request_proxy
    state=state_of(frame);key=key_of(frame)
    event=frame._flow_pattern_ready;frame._flow_pattern_ready=None
    if event is None:event=select(frame,key)
    else:
        row=next((row for row in frame._flow_pattern_region[2]if row[0]==key),None)
        if row is None or state._event_index!=row[2]:state.malformed()
        frame._flow_pattern_segment=row[0]
    if event[0]not in('enter','eval','await','yield','delegate')or event[1]is not node:state.malformed()
    tail=frame.tail if body else False
    options={'all_results':frame.all_results if body else False}
    if regions:options['_compiled_regions']=frame.compiled_regions
    boundary=make_boundary(state,event,env,tail,options)
    # The original pattern protocol carries tail and all_results independently.
    boundary.tail=tail;boundary.options['all_results']=options['all_results']
    if not regions:boundary.options.pop('_compiled_regions',None)
    state.result=None
    return frame.leaf(request_proxy(FlowBodyRequest(evaluator,boundary)))


def decide(frame,truth):
    from .compiled_flow_frames import take
    state=state_of(frame)
    if state._unwind_error is not None or frame._flow_pattern_drain:return truth
    key=('guard',frame.index-1);slots=dict(frame._flow_pattern_region[3])
    if key not in slots:state.malformed()
    state.flags[slots[key]]=int(truth)
    event=take(state)
    if event==('pattern_next',frame._flow_boundary.flow_index):return False
    key=('body',frame.index-1)
    row=next((row for row in frame._flow_pattern_region[2]if row[0]==key),None)
    if event is None or row is None or state._event_index!=row[2]or event[1]is not frame.body:state.malformed()
    frame._flow_pattern_ready=event
    return True


def step(frame,cursor,value,error,advance):
    from .compiled_flow_frames import take,resumed
    from .execution_frames import Done
    state=state_of(frame)
    closing=getattr(cursor,'close_signal',None)is not None
    if closing:frame._flow_pattern_native_close=True
    if state.closed:
        # Only the original retryable native-close algorithm may remain alive
        # after its ambient invocation has retired. Drain that exact current
        # phase/index; never create another FlowState or replay a source prefix.
        from .execution_frames import PatternCloseCursor
        state.validate(expected_owner=cursor.owner)
        if not frame._flow_pattern_native_close or type(cursor)is not PatternCloseCursor or not cursor.frames or cursor.frames[0]is not frame or frame.owner is not cursor.owner or frame.epoch!=state.epoch:state.malformed()
        frame._flow_pattern_drain=True
        return advance(cursor,value,error)
    if closing and state.phase in('host','checkpoint'):
        # A native delegate may legally consume close and return a value. The
        # algorithm's actual error, not a synthesized GE, owns this transition.
        error=resumed(state._cursor,error)
    if not hasattr(frame,'_flow_pattern_region'):
        event=take(state)
        if event is None or event[0]!='pattern'or event[1]is not frame.node:state.malformed()
        frame._flow_pattern_region=event
        frame._flow_pattern_segment=frame._flow_pattern_ready=None
    elif frame._flow_pattern_segment is not None:
        key=frame._flow_pattern_segment
        if error is None and state._unwind_error is None:
            if closing and cursor.close_completed is True:
                # A lawful native delegate completion can supply the result
                # before its selected FlowBodyRequest has entered the ambient
                # cursor. An enter event now owns a structured operand subtree;
                # consume only that exact, still-unentered segment, not its AST.
                from .execution_frames import PatternCloseCursor
                if (type(cursor) is not PatternCloseCursor or not cursor.frames
                        or cursor.frames[0] is not frame or frame.owner is not cursor.owner
                        or frame.epoch != state.epoch):state.malformed()
                row=next((row for row in frame._flow_pattern_region[2] if row[0] is key),None)
                if row is None:state.malformed()
                if state._event_index==row[2] and state.pc==row[2]+1:
                    selected=state.program.instructions[row[2]]
                    if selected[0]=='enter':
                        if any(boundary.flow_index==row[2] for boundary in state._owned_activations):state.malformed()
                        state.pc=row[3]
            event=take(state)
            if event!=('pattern_return',frame._flow_boundary.flow_index,key):state.malformed()
        frame._flow_pattern_segment=None
    action=advance(cursor,value,error)
    if type(action)is Done:
        if state._unwind_error is not None:raise state._unwind_error
        state.pc=frame._flow_pattern_region[5]
        event=take(state)
        if event is None or event[0]!='exit'or event[1]is not frame.node:state.malformed()
        state.result=action.value
    return action
