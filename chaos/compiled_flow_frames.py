"""Compiled CFG events on the ordinary cursor's actual lexical activations.

There is no normalized child cursor and no reconstructed capture projection.
The existing boundary/operand/loop records execute and own their real fields.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FlowBodyRequest:
    owner: object
    frame: object


def flow_frame(request, owner):
    if type(request) is not FlowBodyRequest: return None
    if request.owner is not owner:
        from .language import LanguageError
        raise LanguageError('Foreign compiled body transport',code='ownership_error')
    from .execution_frames import EvaluationBoundaryFrame
    if type(request.frame) is not EvaluationBoundaryFrame:
        from .language import LanguageError
        raise LanguageError('Malformed compiled body transport',code='ownership_error')
    state = getattr(request.frame,'flow_state',None)
    if state is not None and state.owner is not request.owner: state.malformed()
    return request.frame


def body_frame(plan, owner, function, scope):
    from .jit import Unsupported
    from .jit_regions import analyze_structured, RegionInvocation
    from .jit_flow import node_ids, validate_program
    from .language import LanguageError
    from .compiled_flow_state import FlowState
    from .execution_frames import EvaluationBoundaryFrame, EvaluationRequest
    program = plan.flow
    if validate_program(program) != program.layout_seal:
        raise LanguageError('Compiled flow layout changed', code='ownership_error')
    try: current = analyze_structured(function, control_flow=True, resumable=True)
    except (Unsupported, RecursionError): current = None
    if current is None or current.fingerprint != plan.analysis.fingerprint or node_ids(function.body) != program.node_ids:
        plan.last_fallback = 'definition_changed'; plan.interpreter_calls += 1
        return EvaluationBoundaryFrame(EvaluationRequest(function.body, scope, True, {'all_results':True}))
    state = FlowState(plan, owner, function, scope, in_stack=True)
    try:
        state._plan = plan
        state._regions = RegionInvocation(plan, owner, function, scope) if plan.regions else None
        state._owned_activations = []
        state._cursor = state._root_boundary = None
        state._event_index = None; state._reserved = None
        entry = take(state)
        frame = make_boundary(state, entry, scope, True, {'all_results':True})
    except BaseException:
        state.finish()
        raise
    state._root_boundary = frame
    return frame


def structural(node):
    return node[0] in ('assign','index','subscript','set_member','set_item','aug_assign','call','let','var','list','tuple','record','binary','unary','do','match','let_pattern','var_pattern','assign_pattern','with','async_with','with_pattern','async_with_pattern','try','try_cases','try_group','finally','try_else','block','if','while','for','for_pattern','async_for','async_for_pattern','return','break','continue') or node[0] == 'loop_else' and node[1][0] in ('while','for','for_pattern','async_for','async_for_pattern')


def stable(state):
    from .jit import Unsupported
    from .jit_regions import analyze_structured
    from .jit_flow import node_ids
    from .language import LanguageError
    if state.invalid_definition is not None: raise state.invalid_definition
    try: current = analyze_structured(state.function, control_flow=True, resumable=True)
    except (Unsupported, RecursionError): current = None
    if current is None or current.fingerprint != state._plan.analysis.fingerprint or node_ids(state.function.body) != state.program.node_ids:
        state.invalid_definition = LanguageError('可恢复编译函数执行中定义发生变化；已提交状态保留', code='definition_changed')
        raise state.invalid_definition


def take(state):
    """The real native/portable successor chooses the next semantic event."""
    if state._unwind_error is not None:raise state._unwind_error
    state.validate(expected_owner=state.owner)
    if state.closed:state.malformed()
    if state.invalid_definition is not None: raise state.invalid_definition
    program = state.program; e = state.owner
    required = e.expression_trace_required
    instrumented = bool(e.debug_hook or (required() if required is not None else e.trace_handler))
    if instrumented: state.portable = True
    program.resume_calls += 1
    if state.portable:
        program.portable_resumes += 1
        packet = state.events.packet(*program.portable(state.pc, state.flags))
        if not state._regions and not state.portable_counted:
            state._plan.interpreter_calls += 1; state.portable_counted = True
        if state._regions: state._regions.fallback('debugger' if e.debug_hook else 'expression_trace' if instrumented else state._plan.reason)
    else:
        if not state.native_counted:
            state._plan.native_calls += 1; state.native_counted = True
            if state._regions: state._regions.native_counted = True
        program.native_resumes += 1
        packet = program.kernel.resume_event(state.pc, state.flags, state.events)
    event = state.events.resolve(packet, e, state.epoch)
    state.pc = packet.next_pc
    program.last_pc = state.pc
    if event is None: return None
    state._event_index = packet.site
    return event


def make_boundary(state, event, env, tail=False, options=None):
    from .execution_frames import EvaluationBoundaryFrame, EvaluationRequest
    if event is None or event[0] not in ('enter','eval','await','yield','delegate'): state.malformed()
    node = event[1]
    options = dict(options or {})
    # A disabled operand subtree cannot regain scalar-region eligibility from
    # the invocation's global profiling/context object. Use the real enclosing
    # boundary; a TaskGroup segment root inherits its original holder boundary.
    parent = state._owned_activations[-1] if state._owned_activations else None
    if parent is None and getattr(state,'_segment_parent',None) is not None:
        parent = state._segment_frame.boundary
    regions = parent.options.get('_compiled_regions') if parent is not None else state._regions
    if regions is not None and options.get('_compiled_regions',regions) is not None:
        options['_compiled_regions'] = regions
    else:
        options.pop('_compiled_regions',None)
    if event[0] in ('eval','await','yield','delegate'):
        tail = event[2]; options['all_results'] = event[2]
    frame = EvaluationBoundaryFrame(EvaluationRequest(node,env,tail,options))
    frame.flow_state = state; frame.flow_event = event; frame.flow_index = state._event_index
    frame.flow_structural = event[0] == 'enter'
    return frame


def entered(boundary, cursor):
    state = getattr(boundary, 'flow_state', None)
    if state is None: return
    if state._cursor is None:
        state._cursor = cursor
        active = getattr(cursor,'_compiled_flows',None)
        if active is None: cursor._compiled_flows = active = []
        active.append(state)
    if state._cursor is not cursor: state.malformed()
    # The real boundary owns cleanup independently of the writable inspection
    # vector. Entries are removed by exact identity, never by replacement rows.
    from .compiled_flow_resources import segment_boundary
    segment_boundary(state,boundary)
    state._owned_activations.append(boundary)
    state.frames.append(boundary)
    state.attach(boundary.capture)


def leaving(boundary, cursor):
    state = getattr(boundary, 'flow_state', None)
    if state is None: return
    if boundary not in state._owned_activations: state.malformed()
    state._owned_activations.remove(boundary)
    if type(state.frames) is list:
        state.frames[:] = [row for row in state.frames if row is not boundary]
    state.detach(boundary.capture)
    if boundary is state._root_boundary:
        if getattr(state,'_segment_parent',None)is not None:
            # The real body boundary is retired now. Its original outer PV
            # may still resolve a tail invocation on this same group cursor.
            # Keep only the supplementary controller until that PV completes.
            if state._owned_activations:state.malformed()
            return
        active = getattr(cursor,'_compiled_flows',None)
        if type(active) is list: active[:] = [row for row in active if row is not state]
        # All descendant boundaries have already drained on the ordinary stack.
        if state._owned_activations: state.malformed()
        state.frames = []
        from .compiled_flow_resources import segment_leaving
        segment_leaving(state)
        state.finish()


def failure(boundary, error):
    state = getattr(boundary, 'flow_state', None)
    if state is None: return
    from .language import BreakSignal, ContinueSignal
    if state._unwind_error is not None:return
    if isinstance(error,(BreakSignal,ContinueSignal)) and not any(row[0] is error for row in state._transfers):
        event = boundary.flow_event
        state._transfers.append((error,event[-1] if event[0] in ('enter','eval','await','yield','delegate') else None))


def retire_transfer(state,error):
    rows=getattr(state,'_transfers',None)
    if type(rows)is list:
        rows[:] = [row for row in rows if not(type(row)is tuple and len(row)==2 and row[0]is error)]


def resumed(cursor, error):
    """Validate before any child receives send/throw, then preserve hard control."""
    from .suspension import error_priority
    for state in tuple(getattr(cursor,'_compiled_flows',())):
        # A metadata/definition fault terminates this compiled invocation. Do
        # not reinject it into each resume of an already-owned opaque finally.
        # Its actual cleanup may call a different compiled invocation, whose
        # independent state continues to validate and dispatch normally.
        if state._unwind_error is None:
            try:
                state.validate(expected_owner=cursor.owner)
                if state.phase == 'checkpoint': stable(state)
                from .suspension import Await, Yield
                # Both stream feedback and async completion resume the same
                # selected event. Audit before forwarding send/throw/close;
                # never dispatch a replacement body or replay its prefix.
                if state.phase == 'host' and type(state.pending_request) in (Await,Yield): stable(state)
            except BaseException as corruption:
                if error is None or error_priority(corruption) >= error_priority(error): error = corruption
                state._unwind_error = error
        state.clear_pending()
        state.phase = 'dispatch'
    return error


def paused(cursor, request):
    from .suspension import CHECKPOINT, Await, Yield
    from .agent_tools import current_caps
    cursor._compiled_flow_request = request
    for state in tuple(getattr(cursor,'_compiled_flows',())):
        state.phase = 'checkpoint' if request is CHECKPOINT else 'host'
        # Exact request facts only. A dynamic opaque leaf may await or yield,
        # so its protocol kind must not be inferred from a syntax/call name.
        state.pending_kind = ('checkpoint' if request is CHECKPOINT else
                              'await' if type(request) is Await else
                              'yield' if type(request) is Yield else 'host')
        state.pending_cursor = cursor; state.pending_request = request
        state.pending_authority = current_caps(cursor.owner)
        state.pending_pc = state._event_index
        if type(request) is Await: state.program.await_suspensions += 1


def signal_leaf(state, node):
    from .language import BreakSignal, ContinueSignal
    from .capture_frames import active as capture_active
    capture_active(state.owner)
    state.owner.tick()
    event = take(state)
    if event is None or event[0] != 'unwind': state.malformed()
    if node[0] == 'break': raise BreakSignal()
    raise ContinueSignal()
    yield


def step(frame, cursor, value, error):
    """Execute the actual Operand/Loop record according to CFG-selected events."""
    from .execution_frames import Done, Push, External, ProtocolBridge, LoopFrame, LoopCompletionFrame
    from .language import Env, MISSING, ReturnSignal, BreakSignal, ContinueSignal, check_type, _Completion
    from .capture_frames import reset
    state = frame.boundary.flow_state; e = cursor.owner; node = frame.node; kind = node[0]
    wait = getattr(frame,'_flow_wait',None)
    # Exactly the original anext Await boundary owns cancellation of this
    # pending operation. The upstream stream remains borrowed.
    if type(frame) is LoopFrame and frame.phase == 'for_item_async':
        if error is not None:
            frame.cancel_pending(e)
            raise error
        frame.operation = None; frame.phase = 'for_item'
    if error is not None:
        if type(frame) is LoopFrame and frame.phase in ('while_body','for_body') and isinstance(error,(BreakSignal,ContinueSignal)):
            if isinstance(error,BreakSignal): frame.completed('broken')
            transfer = next((row for row in state._transfers if row[0] is error),None)
            if transfer is not None and transfer[0] is error and transfer[1] is not None:
                route = transfer[1]; state.pc = route[2] if isinstance(error,BreakSignal) else route[1]
            retire_transfer(state,error); value = None
        else: raise error
    if not getattr(frame,'_flow_started',False):
        frame._flow_started = True; e.tick()
        if kind == 'block':
            frame.env = Env(frame.env); frame.result = None; reset(frame.boundary.capture,frame.env)
        elif type(frame) is LoopCompletionFrame:
            from .language import LanguageError
            if len(node)!=3 or not isinstance(node[1],tuple) or not node[1] or node[1][0] not in ('while','for','async_for','for_pattern','async_for_pattern') or not isinstance(node[2],tuple) or not node[2] or node[2][0]!='block':
                raise LanguageError('正常完成分支需要对应控制结构和 else 块',code='syntax_error')
            frame.outcome = _Completion()
        elif type(frame) is LoopFrame:
            opening = state.program.instructions[frame.boundary.flow_index+1]
            if opening[0] != 'flag': state.malformed()
            frame._flow_completion_slot = opening[1]
    if wait == 'acquire_source':
        frame.items = value; frame.phase = 'for_acquired'; frame._flow_wait = 'acquire_handle'
        return Push(ProtocolBridge(e.suspension.iteration.acquire(frame.items,True) if frame.asynchronous else e.suspension.iteration.acquire(frame.items)))
    if wait == 'acquire_handle':
        frame.handle = value; frame.reset(frame.env,frame.items,frame.handle)
    elif wait in ('step','step_async'):
        frame._flow_record = value
        state.flags[frame._flow_flag] = int(not value['done'])
    elif wait == 'child':
        if kind == 'block': frame.result = value; frame.index += 1
        elif kind == 'if' and frame.phase == 'first': frame.left = value; frame.keep(value)
        elif kind == 'return': frame.left = value; frame.keep(value)
        elif type(frame) is LoopCompletionFrame:
            frame.value = value
            if frame.phase == 'loop': frame.phase = 'else'
        if type(frame) is LoopFrame:
            if frame.phase == 'for_body': frame.scope = frame.item = None
            elif frame.phase == 'while_body': frame.reset(frame.env)
    frame._flow_wait = None
    state.result = value
    while True:
        event = take(state)
        if event is None: state.malformed()
        op = event[0]
        if op in ('enter','eval','await','yield','delegate'):
            options = {}
            if type(frame) is LoopCompletionFrame and frame.phase != 'else':
                frame.phase = 'loop'; options['_completion'] = frame.outcome
            elif type(frame) is LoopCompletionFrame: frame.phase = 'else'
            elif kind == 'block': frame.phase = 'block_value'
            elif kind == 'if': frame.phase = 'first' if not getattr(frame,'_flow_condition_done',False) else 'done'
            elif kind == 'return': frame.phase = 'first'
            elif kind == 'while': frame.phase = 'while_body' if getattr(frame,'_flow_body_next',False) else 'while_condition'; frame._flow_body_next = False
            elif kind in ('for','for_pattern','async_for','async_for_pattern'): frame.phase = 'for_body'
            env = frame.scope if type(frame) is LoopFrame and kind != 'while' else frame.env
            tail = getattr(frame,'tail',False)
            if kind == 'block':
                last = frame.index == len(node[1])-1
                tail = tail and last; options['all_results'] = frame.options.get('all_results',False) and last
            elif kind == 'if':
                # Original OperandFrame evaluates the condition outside tail
                # position and projects its primary value before Bool checking.
                if frame.phase == 'first': tail = False; options['all_results'] = False
                else: options['all_results'] = frame.options.get('all_results',False)
            elif kind == 'return': tail = True; options['all_results'] = True
            elif type(frame) is LoopFrame: tail = False
            if type(frame) is LoopCompletionFrame:
                options['all_results'] = frame.options.get('all_results',False)
            frame._flow_wait = 'child'
            state.result = None
            return Push(make_boundary(state,event,env,tail,options))
        if op == 'exit':
            if event[1] is not node: state.malformed()
            result = frame.result if kind == 'block' else frame.value if type(frame) is LoopCompletionFrame else state.result
            if type(frame) is LoopFrame: result = None
            return Done(result)
        if op == 'reset':
            if kind != 'block': state.malformed()
            reset(frame.boundary.capture,frame.env)
        elif op == 'condition':
            actual = frame.left if kind == 'if' else state.result
            state.flags[event[1]] = int(check_type(actual,'Bool'))
            if kind == 'if': frame._flow_condition_done = True
        elif op == 'acquire':
            frame.phase = 'for_iterable'; frame._flow_wait = 'acquire_source'
            from .execution_frames import EvaluationBoundaryFrame, EvaluationRequest
            return Push(EvaluationBoundaryFrame(EvaluationRequest(node[2],frame.env,False,{})))
        elif op == 'step':
            frame._flow_flag = event[2]
            if frame.asynchronous:
                from .suspension import Await
                frame.operation = e.suspension.anext(frame.handle)
                frame.phase = 'for_item_async'; frame._flow_wait = 'step_async'
                return External(Await(frame.operation))
            frame.phase = 'for_item'; frame._flow_wait = 'step'
            return Push(ProtocolBridge(e.suspension.iteration.step(frame.handle)))
        elif op == 'loop_reset':
            if type(frame) is not LoopFrame: state.malformed()
            frame.scope = frame.item = None
            if kind == 'while': frame.reset(frame.env)
            else: frame.reset(frame.env,frame.items,frame.handle)
        elif op == 'tick':
            e.tick()
            if type(frame) is LoopFrame:
                frame._flow_body_next = True
                if kind != 'while': frame.item = frame._flow_record['value']
        elif op == 'bind':
            frame.scope = Env(frame.env)
            if kind in ('for_pattern','async_for_pattern'): e.bind_pattern(node[1],frame.item,frame.scope,lookup_env=frame.env)
            else: frame.scope.bind(node[1],frame.item)
            frame.reset(frame.env,frame.items,frame.handle)
        elif op == 'null':
            state.result = None
            if type(frame) is LoopFrame: frame.completed('normal') if state.flags[frame._flow_completion_slot] else None
        elif op == 'return': raise ReturnSignal(frame.left)
        elif op == 'unwind': state.malformed()
        else: state.malformed()


def validate_state(state, expected_owner=None, full=False):
    from .execution_frames import ExecutionCursor, EvaluationBoundaryFrame
    from .language import Env, Function
    from .jit_flow import FlowProgram, validate_program
    if expected_owner is not None and state.owner is not expected_owner: state.malformed()
    if type(state.epoch) is not int or state.epoch != state.owner.suspension.epoch: state.malformed()
    if type(state.program) is not FlowProgram or state.program.layout_seal is not state.compile_seal: state.malformed()
    if (id(state.program.instructions),id(state.program.effects),state.program.flag_count,id(state.program.node_ids)) != state.layout: state.malformed()
    if full: validate_program(state.program)
    from .compiled_flow_resources import validate_segment
    validate_segment(state)
    if type(state.pc) is not int or not 0 <= state.pc < len(state.program.instructions): state.malformed()
    if type(state.flags) is not list or len(state.flags) != state.program.flag_count or any(type(x) is not int or x not in (0,1) for x in state.flags): state.malformed()
    if type(state.phase) is not str or state.phase not in state.PHASES: state.malformed()
    if any(type(getattr(state,name)) is not bool for name in ('in_stack','asynchronous','generating','portable','native_counted','portable_counted','closed')): state.malformed()
    for caps in (state.capabilities,state.pending_authority):
        if caps is not None and (type(caps) is not frozenset or any(type(x) is not str for x in caps)): state.malformed()
    # The compiler's sealed instruction vector bounds actual activation records.
    # AST depth is zero-based, so a legal depth64 body can own65 boundaries;
    # the capture walk's64-node charge period is not an execution stack limit.
    if type(state.frames) is not list or len(state.frames) > len(state.program.instructions): state.malformed()
    if type(state.flavor) is not str or state.flavor not in ('fn','gen_fn','async_fn','async_gen_fn'): state.malformed()
    if state.pending_kind is not None and (type(state.pending_kind) is not str or state.pending_kind not in ('checkpoint','await','yield','host')): state.malformed()
    from .native_values import RootDomain
    from .flow_abi import EventTable
    if state.domain is not state._owned_domain or state.events is not state._owned_events or type(state.domain) is not RootDomain or state.domain.id != state._domain_id or type(state.events) is not EventTable or state.events.domain is not state.domain: state.malformed()
    if type(state._value_refs) is not dict or any(name not in state.ROOTED_FIELDS for name in state._value_refs): state.malformed()
    if state.closed:
        if not state.domain.closed or state.domain.live_count or state._value_refs: state.malformed()
        if state.phase != 'closed' or state.frames or state._transfers or any(getattr(state,x) is not None for x in ('function','scope','env','result','pending_cursor','pending_request','pending_pc','pending_authority','pending_kind','invalid_definition')): state.malformed()
        return
    state.domain.validate(state.owner, state.epoch)
    for ref in state._value_refs.values():
        if ref is not None: state._owned_domain.resolve(ref, state.owner, state.epoch)
    if state.events.instructions is not state.program.instructions: state.malformed()
    state.events.validate(full=full)
    if state.phase == 'closed' or type(state.function) is not Function or state.function.owner is not state.owner or type(state.scope) is not Env: state.malformed()
    if state.flavor != state.function.flavor or state.asynchronous != (state.flavor in ('async_fn','async_gen_fn')) or state.generating != (state.flavor in ('gen_fn','async_gen_fn')) or type(state.previous) is not dict: state.malformed()
    if state.invalid_definition is not None and not isinstance(state.invalid_definition,BaseException): state.malformed()
    if state._unwind_error is not None and not isinstance(state._unwind_error,BaseException): state.malformed()
    from .language import BreakSignal, ContinueSignal
    if type(state._transfers) is not list or len(state._transfers)>len(state.program.instructions): state.malformed()
    if any(type(row)is not tuple or len(row)!=2 or not isinstance(row[0],(BreakSignal,ContinueSignal)) or row[1]is not None and (type(row[1])is not tuple or len(row[1])!=3 or any(type(x)is not int for x in row[1])) for row in state._transfers): state.malformed()
    if len({id(row[0])for row in state._transfers})!=len(state._transfers): state.malformed()
    if state._transfers:
        routes={id(row[-1])for row in state.program.instructions if row[0]in('enter','eval','await','yield','delegate','acquire','step')and row[-1]is not None}
        if any(row[1]is not None and id(row[1])not in routes for row in state._transfers):state.malformed()
    if state.env is not None and type(state.env) is not Env: state.malformed()
    if any(type(row) is not EvaluationBoundaryFrame or row.owner is not state.owner or getattr(row,'flow_state',None) is not state for row in state.frames): state.malformed()
    if state.frames != state._owned_activations: state.malformed()
    active_indices = set()
    for row in state.frames:
        if type(row.flow_index) is not int or not 0 <= row.flow_index < len(state.program.instructions) or state.program.instructions[row.flow_index] is not row.flow_event: state.malformed()
        if row.flow_index in active_indices or row.flow_event[0] not in ('enter','eval','await','yield','delegate'): state.malformed()
        active_indices.add(row.flow_index)
        if type(row.flow_structural) is not bool or row.flow_structural != (row.flow_event[0]=='enter') or row.node is not row.flow_event[1] or type(row.env) is not Env: state.malformed()
        if row.flow_structural:
            from .compiled_flow_protection import validate_operation
            validate_operation(state,row)
            from .compiled_flow_resources import validate_operation as validate_resource
            validate_resource(state,row)
            from .compiled_flow_patterns import validate_operation as validate_pattern
            validate_pattern(state,row)
            from .compiled_flow_operands import validate_operation as validate_operand
            validate_operand(state,row)
            from .compiled_flow_access import validate_operation as validate_access
            validate_access(state,row)
    if state.phase in ('host','checkpoint'):
        from .suspension import CHECKPOINT, Await, Yield
        request = state.pending_request
        kind = ('checkpoint' if request is CHECKPOINT else 'await' if type(request) is Await else 'yield' if type(request) is Yield else 'host')
        if state.pending_kind != kind or (state.phase == 'checkpoint') != (request is CHECKPOINT): state.malformed()
        child = state.pending_cursor
        if type(child) is not ExecutionCursor or child is not state._cursor or child.owner is not state.owner or child.closed: state.malformed()
        if getattr(child,'_compiled_flow_request',None) is not state.pending_request or state not in getattr(child,'_compiled_flows',()): state.malformed()
        if type(state.pending_pc) is not int or state.pending_pc != state._event_index or state.pc != state.pending_pc + 1: state.malformed()
    elif any(getattr(state,x) is not None for x in ('pending_cursor','pending_request','pending_pc','pending_authority','pending_kind')): state.malformed()


def roots(state):
    from .capture_frames import Edge
    state.validate(full=True)
    if state.closed: return ()
    # All lexical activation fields already live on the original charged stack.
    # Only input authority and new pending/PC metadata are supplementary here.
    return (Edge(state.owner,state.capabilities,(state.function,state.scope,state.env,state.result,state.invalid_definition,state._unwind_error)+tuple(row[0]for row in state._transfers)),
            Edge(state.owner,state.pending_authority,(state.pending_cursor,state.pending_request)))
