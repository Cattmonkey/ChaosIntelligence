"""CFG child selection on the original shared operand algorithm.

The original OperandFrame owns every value, binding and protocol transition.
This adapter selects its actual child boundaries and consumes their exact return
sites; it never evaluates an operand, invokes a callable or rebuilds an Env.
"""
KINDS = frozenset(('call', 'let', 'var', 'list', 'tuple', 'record',
                   'binary', 'unary', 'do'))


def state_of(frame):
    from .execution_frames import OperandFrame, EvaluationBoundaryFrame
    from .compiled_flow_state import FlowState
    from .language import LanguageError
    boundary = frame.boundary
    state = getattr(boundary, 'flow_state', None)
    if (type(frame) is not OperandFrame or type(boundary) is not EvaluationBoundaryFrame
            or type(state) is not FlowState or state.owner is not frame.owner
            or frame.node is not boundary.node
            or getattr(boundary, 'flow_operation', None) is not frame):
        raise LanguageError('Foreign compiled operand activation', code='ownership_error')
    return state


def key_of(frame):
    if frame.phase in ('argument_value', 'collection_value'):
        return frame.phase, frame.index
    if frame.phase in ('callee', 'first', 'right', 'boolean'):
        return frame.phase, 0
    state_of(frame).malformed()


def validate_operation(state, boundary):
    if boundary.node[0] not in KINDS:
        return
    from .execution_frames import OperandFrame
    frame = getattr(boundary, 'flow_operation', None)
    if frame is None:
        return  # Original scalar-region fast path has no OperandFrame.
    if (type(frame) is not OperandFrame or frame.owner is not state.owner
            or frame.boundary is not boundary or frame.node is not boundary.node):
        state.malformed()
    phases = ({'start','callee','argument_next','argument_value','spread_value','done'} if frame.node[0]=='call'
              else {'start','collection_next','collection_value'} if frame.node[0] in ('list','tuple','record')
              else {'start','first','right','boolean','done'} if frame.node[0]=='binary'
              else {'start','first','done'})
    size = len(frame.node[2]) if frame.node[0]=='call' else len(frame.node[1]) if frame.node[0] in ('list','tuple','record') else 0
    if type(frame.phase) is not str or frame.phase not in phases or type(frame.index) is not int or not 0<=frame.index<=size:
        state.malformed()
    region = getattr(frame, '_flow_operand_region', None)
    if region is None:
        if state.phase in ('host', 'checkpoint'): state.malformed()
        return
    if region is not state.program.instructions[boundary.flow_index + 1] or region[0] != 'operand':
        state.malformed()
    segment = frame._flow_operand_segment
    if state.phase in ('host','checkpoint') and frame.phase in ('callee','first','right','boolean','argument_value','collection_value') and segment is None:
        state.malformed()
    if segment is not None:
        row = next((row for row in region[2] if row[0] is segment), None)
        if (row is None or frame.phase not in ('callee', 'first', 'right', 'boolean', 'argument_value', 'collection_value')
                or segment != key_of(frame)
                or state.phase in ('host','checkpoint') and not row[2] <= state._event_index <= row[3]):
            state.malformed()


def child(frame, node, *, tail=False, all_results=False, regions=True):
    from .compiled_flow_frames import take, make_boundary
    from .execution_frames import Push
    state = state_of(frame); region = frame._flow_operand_region
    if frame._flow_operand_segment is not None: state.malformed()
    key = key_of(frame)
    row = next((row for row in region[2] if row[0] == key), None)
    if row is None: state.malformed()
    frame._flow_operand_segment = row[0]
    for actual, flag, entry, end in region[2]: state.flags[flag] = int(actual == key)
    state.pc = region[3]
    event = take(state)
    if event is None or state._event_index != row[2] or event[1] is not node:
        state.malformed()
    options = {'all_results': all_results}
    if regions and frame.options.get('_compiled_regions') is not None:
        options['_compiled_regions'] = frame.options['_compiled_regions']
    boundary = make_boundary(state, event, frame.env, tail, options)
    # Operand semantics carry these independently; a non-tail boolean RHS still
    # requests all values and call/collection children disable scalar regions.
    boundary.tail = tail; boundary.options['all_results'] = all_results
    if not regions or frame.options.get('_compiled_regions') is None:
        boundary.options.pop('_compiled_regions', None)
    state.result = None
    return Push(boundary)


def fast_finish(boundary):
    """The original guarded scalar kernel already consumed every checkpoint."""
    from .compiled_flow_frames import take
    state = boundary.flow_state
    region = take(state)
    if region is None or region[0] != 'operand' or region[1] is not boundary.node:
        state.malformed()
    state.pc = region[4]
    event = take(state)
    if event is None or event[0] != 'exit' or event[1] is not boundary.node:
        state.malformed()


def step(frame, cursor, value, error):
    from .compiled_flow_frames import take
    from .execution_frames import Done
    state = state_of(frame)
    if not hasattr(frame, '_flow_operand_region'):
        frame._flow_operand_region = state.program.instructions[frame.boundary.flow_index + 1]
        frame._flow_operand_segment = None
        event = take(state)
        if event is not frame._flow_operand_region or event[0] != 'operand': state.malformed()
    if frame._flow_operand_segment is not None and error is None:
        key = frame._flow_operand_segment
        event = take(state)
        if event != ('operand_return', frame.boundary.flow_index, key): state.malformed()
        frame._flow_operand_segment = None
    # All semantic phases, protocol suspension and errors remain original.
    action = frame.advance(cursor, value, error)
    if type(action) is Done:
        if frame._flow_operand_segment is not None: state.malformed()
        state.pc = frame._flow_operand_region[4]
        event = take(state)
        if event is None or event[0] != 'exit' or event[1] is not frame.node: state.malformed()
    return action
