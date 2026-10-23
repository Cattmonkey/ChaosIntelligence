"""Explicit invocation dispatch, shared by functions, agents and robot values.

Only protocol delegation remains native. The callable-selection phase, ordered
native request cascade, validation latches and pending Agent effect live here.
This is not a reusable continuation or an effect-replay mechanism.
"""
import inspect


def _child_transport(frame, iterator):
    value = yield from iterator
    frame.native_result = value
    frame.native_completed = True
    return value


class InvocationDispatchFrame:
    PHASES = ('start', 'lookup', 'class', 'native', 'native_has', 'agent', 'done')
    STAGES = (
        'type(state.value) is ControlRequest',
        'type(state.value) is ValuesRequest',
        'type(state.value) is FunctionBuildRequest',
        'type(state.value) is ModuleRequest',
        'type(state.value) is SetRequest',
        'type(state.value) is HashMapRequest',
        'type(state.value) is GenericRequest',
        'isinstance(state.value, ContextRunRequest)',
        'isinstance(state.value, RecoveryRequest)',
        'isinstance(state.value, ClassRequest)',
        'type(state.value) is TypeNewRequest',
        'type(state.value) is TypeCallRequest',
        'type(state.value) is TypeCheckRequest',
        'type(state.value) is RobotRequest',
        'type(state.value) is DeviceRequest',
        'isinstance(state.value, ComputerRequest)',
        'isinstance(state.value, SessionRequest)',
        'isinstance(state.value, ProcessRequest)',
        'isinstance(state.value, ProgramRequest)',
        'self.program_validated = isinstance(state.value, ProgramResult)',
        'self.program_validated',
        'isinstance(state.value, TeamRequest)',
        'isinstance(state.value, RoleRequest)',
        'isinstance(state.value, ModeRequest)',
        'isinstance(state.value, AccessRequest)',
        'isinstance(state.value, OperationRequest)',
        'isinstance(state.value, StackRequest)',
        'isinstance(state.value, IterationRequest)',
        'isinstance(state.value, IndexRequest)',
        'self.item_validated = isinstance(state.value, ItemRequest)',
        'self.item_validated',
        'isinstance(state.value, Effect)',
    )

    def __init__(self, state):
        self.state = state
        self.phase = 'start'; self.native_stage = 0
        self.program_validated = self.item_validated = False
        self.effect = None
        self.epoch = self.capabilities = None
        self.native_completed = False; self.native_result = None

    def malformed(self):
        from .language import LanguageError
        raise LanguageError('Malformed invocation dispatch state', code='ownership_error')

    def validate_state(self):
        from .execution_frames import InvocationFrame
        if type(self.state) is not InvocationFrame or self.state.owner is not self.owner:
            self.malformed()
        if type(self.phase) is not str or self.phase not in self.PHASES:
            self.malformed()
        if type(self.native_stage) is not int or not 0 <= self.native_stage <= len(self.STAGES):
            self.malformed()
        if any(type(x) is not bool for x in (self.program_validated, self.item_validated, self.native_completed)):
            self.malformed()
        if self.epoch is not None or self.phase != 'start':
            if type(self.epoch) is not int or self.epoch != self.owner.suspension.epoch:
                self.malformed()
        if self.capabilities is not None and (type(self.capabilities) is not frozenset or any(type(x) is not str for x in self.capabilities)):
            self.malformed()

    def roots(self):
        from .capture_frames import Edge
        self.validate_state()
        return (Edge(self.owner, self.capabilities, (self.state, self.effect, self.native_result)),)

    def leaf(self, iterator):
        from .execution_frames import Push, ProtocolBridge
        self.native_completed = False; self.native_result = None
        bridge = ProtocolBridge(_child_transport(self, iterator))
        bridge.owner = self.owner; bridge.supplementary = True
        return Push(bridge)

    def done(self, value):
        from .execution_frames import Done
        self.phase = 'done'
        return Done(value)

    def step(self, cursor, value, error):
        from .language import LanguageError, ExecutionControl
        from .execution_frames import DispatchAgain, DispatchResult
        e = cursor.owner
        previous = getattr(e, '_execution_cursor', None)
        e._execution_cursor = cursor.ambient_cursor
        try:
            try:
                if error is not None:
                    if self.phase == 'native_has' and isinstance(error, LanguageError) and error.code == 'attribute_error':
                        value = False
                    elif self.phase == 'agent' and isinstance(error, Exception) and not isinstance(error, (LanguageError, ExecutionControl)):
                        raise LanguageError(str(error), code='provider_error') from None
                    else:
                        raise error
                if self.owner is not e:
                    self.malformed()
                self.validate_state()
                self.native_completed = False; self.native_result = None
                if self.phase == 'start':
                    self.epoch = e.suspension.epoch
                    return self.dispatch(cursor)
                if self.phase == 'lookup':
                    self.state.fn = value
                    return self.done(DispatchAgain())
                if self.phase == 'class':
                    self.state.value = value
                    return self.done(DispatchResult(value))
                if self.phase == 'agent':
                    self.state.value = e.copy_value(value)
                    e.tick()
                    return self.done(DispatchResult(self.state.value))
                if self.phase in ('native', 'native_has'):
                    self.state.value = (error is None) if self.phase == 'native_has' else value
                    self.phase = 'native'
                    return self.native(cursor)
                raise RuntimeError('Completed invocation dispatch cannot advance')
            except StopIteration as failure:
                raise RuntimeError('generator raised StopIteration') from failure
        finally:
            e._execution_cursor = previous

    def close(self, cursor):
        # InvocationFrame alone owns call scope, error observation and reduction.
        pass

    def dispatch(self, cursor):
        e = cursor.owner; state = self.state
        from .language import (Native, Function, Agent, Bot, Robot, EscapeContinuation, Continuation,
            LanguageError, ExecutionControl, MISSING, ResourceLimit, check_type,
            serializable, Env, TailCall, SetRequest, set_resolve)
        from .execution_frames import DispatchAgain, DispatchResult, BodyEvaluation, External
        from .suspension import Effect
        from .team_bindings import RoleRequest, call as bound_team_call
        from .objects import AccessRequest
        from .modes import ModeRequest, resolve as mode_resolve
        from .team_values import TeamRequest, resolve as team_resolve
        from .program_values import ProgramRequest, ProgramResult, resolve as program_resolve
        from .process_values import ProcessRequest, resolve as process_resolve
        from .session_values import SessionRequest, resolve as session_resolve
        from .device_values import DeviceRequest, resolve as device_resolve
        from .computer_values import ComputerRequest, resolve as computer_resolve
        from .agent_tools import BoundAgent, call as bound_agent_call
        from .team_bindings import BoundTeam, RoleRequest, call as bound_team_call
        from .objects import ChaosClass, ChaosInstance, BoundMethod, StaticMethod, AccessRequest, special_method, declaration, owned
        from .capture_frames import enter as capture_enter, leave as capture_leave, reset as capture_reset, retain as capture_retain, Edge as CaptureEdge
        from .execution_frames import DispatchAgain, DispatchResult, BodyEvaluation
        from .capture_frames import reset as capture_reset, retain as capture_retain, Edge as CaptureEdge
        capture_reset(state.capture, state.fn, state.args, state.kwargs, state.bound_parameters, *state.required, *(CaptureEdge(e, item[3], (item[0], *item[2], item[4], item[5], item[6])) for item in state.pending))
        e.current_span = state.call_span
        e.tick()
        e.argument_budget(state.args, state.kwargs)
        owned(state.fn, e)
        if getattr(state.fn, 'capabilities', None) is not None and state.authority_runner is not None:
            current = state.authority_runner.capabilities
            state.authority_runner.capabilities = state.fn.capabilities if current is None else current & state.fn.capabilities
        from .agent_tools import current_caps
        self.capabilities = current_caps(e)
        from .generic_functions import enter as generic_enter
        generic_enter(e, state.fn, state.args, state.kwargs)
        if isinstance(state.fn, StaticMethod):
            state.fn = state.fn.value
            return self.done(DispatchAgain())
        if isinstance(state.fn, BoundMethod):
            owned(state.fn.receiver, e)
            owned(state.fn.defining_class, e)
            state.args = [state.fn.receiver] + list(state.args)
            state.fn = state.fn.function
            e.argument_budget(state.args, state.kwargs)
            return self.done(DispatchAgain())
        elif isinstance(state.fn, ChaosInstance):
            self.phase = 'lookup'
            return self.leaf(e._s_access_result(special_method(state.fn, '__call__', e)))
        if isinstance(state.fn, (Native, Function, Agent)) and state.fn.owner is not None and (state.fn.owner is not e):
            raise LanguageError('可调用值不能跨执行上下文传递；请传递源代码或数据')
        from .modes import ModeValue, Plan, Goal, Skill, ModeRequest, owned as mode_owned, resolve as mode_resolve
        from .team_values import Team, TeamRequest, owned as team_owned, resolve as team_resolve
        from .program_values import Program, ProgramRequest, ProgramResult, run_request as program_run, resolve as program_resolve
        from .process_values import Process, ProcessRequest, start_request as process_start, resolve as process_resolve
        from .session_tools import BoundSession, call as bound_session_call
        from .session_values import AgentSession, SessionRequest, turn_request as session_turn, resolve as session_resolve
        from .device_values import Device, DeviceRequest, request as device_request, resolve as device_resolve
        from .computer_values import Computer, ComputerRequest, ObservedInput, act_request as computer_act, resolve as computer_resolve, observed_payload, location as computer_location
        if type(state.fn) is Bot:
            from .bot_values import call as bot_call
            bound = e.bind_arguments('Bot', [('input', 'Any', 'positional', None)], state.args, state.kwargs)
            state.value = bot_call(e, state.fn, bound['input'])
        elif type(state.fn) is BoundTeam:
            state.value = bound_team_call(e, state.fn, state.args, state.kwargs)
        elif type(state.fn) is Robot:
            from .robot_values import request as robot_request, resolve as robot_resolve
            bound = e.bind_arguments('Robot', [('command', 'RobotCommand', 'positional', MISSING)], state.args, state.kwargs)
            state.value = robot_resolve(e, robot_request(e, 'act', state.fn, bound['command']))
        elif type(state.fn) is Device:
            bound = e.bind_arguments('Device', [('action', 'DeviceAction', 'positional', MISSING)], state.args, state.kwargs)
            state.value = device_resolve(e, device_request(e, 'act', state.fn, bound['action']))
        elif type(state.fn) is Computer:
            bound = e.bind_arguments('Computer', [('action', 'ComputerAction', 'positional', MISSING)], state.args, state.kwargs)
            state.value = computer_resolve(e, computer_act(e, state.fn, bound['action']))
        elif type(state.fn) is BoundSession:
            bound = e.bind_arguments('BoundSession', [('input', 'Any', 'positional', MISSING), ('message_id', 'Any', 'positional', None)], state.args, state.kwargs)
            state.value = bound_session_call(e, state.fn, bound['input'], bound['message_id'])
        elif type(state.fn) is AgentSession:
            bound = e.bind_arguments('AgentSession', [('input', 'Any', 'positional', MISSING), ('message_id', 'Any', 'positional', None)], state.args, state.kwargs)
            state.value = session_resolve(e, session_turn(e, state.fn, bound['input'], bound['message_id']))
        elif type(state.fn) is Process:
            bound = e.bind_arguments('Process', [('key', 'String', 'positional', MISSING), ('state', 'Any', 'positional', None)], state.args, state.kwargs)
            state.value = process_resolve(e, process_start(e, bound['key'], state.fn, bound['state']))
        elif type(state.fn) is Program:
            bound = e.bind_arguments('Program', [('input', 'Any', 'positional', MISSING)], state.args, state.kwargs)
            state.value = program_resolve(e, program_run(e, state.fn, bound['input']))
        elif type(state.fn) is Team:
            team_owned(state.fn, e)
            bound = e.bind_arguments('Team', [('input', 'Any', 'positional', MISSING)], state.args, state.kwargs)
            state.value = team_resolve(e, TeamRequest(state.fn, state.fn.definition['entry'], bound['input']))
        elif isinstance(state.fn, ModeValue):
            mode_owned(state.fn, e)
            if isinstance(state.fn, Skill):
                bound = e.bind_arguments('Skill', [('values', 'Record', 'positional', {})], state.args, state.kwargs)
                state.value = mode_resolve(e, ModeRequest('replay_skill', (state.fn, bound['values'])))
            else:
                bound = e.bind_arguments(type(state.fn).__name__, [('input', 'Any', 'positional', None), ('bindings', 'Record', 'positional', {})], state.args, state.kwargs)
                call = (state.fn, bound['bindings'], bound['input'])
                state.value = mode_resolve(e, ModeRequest('run_plan' if isinstance(state.fn, Plan) else 'pursue', call if isinstance(state.fn, Plan) else call + (None,)))
        elif isinstance(state.fn, ChaosClass):
            from .metaclasses import class_call
            self.phase = 'class'
            return self.leaf(class_call(e, state.fn, state.args, state.kwargs, state.call_span, synchronous=state.synchronous))
        elif type(state.fn) is Continuation:
            from .continuations import invoke_request
            state.value = invoke_request(e, state.fn, state.args, state.kwargs)
            capture_retain(state.capture, state.value)
            self.phase = 'native'
            return self.native(cursor)
        elif type(state.fn) is EscapeContinuation:
            from .control_prompts import invoke as invoke_escape
            invoke_escape(e, state.fn, state.args, state.kwargs)
        elif isinstance(state.fn, Native):
            try:
                inspect.signature(state.fn.call).bind(*state.args, **state.kwargs)
            except TypeError as error:
                raise LanguageError(str(error), code='argument_error') from None
            state.value = state.fn.call(*state.args, **state.kwargs)
            capture_retain(state.capture, state.value)
            self.phase = 'native'
            return self.native(cursor)
        elif type(state.fn) is BoundAgent:
            bound = e.bind_arguments(state.fn.name, [('input', 'Any', 'positional', MISSING)], state.args, state.kwargs)
            state.value = bound_agent_call(e, state.fn, bound['input'])
        elif isinstance(state.fn, Agent):
            from .agent_tools import current_caps
            capabilities = current_caps(e)
            if capabilities is not None and (not set(state.fn.config.get('tools', [])).issubset(capabilities)):
                raise LanguageError('Agent capabilities exceed enclosing tool authority', code='capability_error')
            bound = e.bind_arguments(state.fn.name, [('input', 'Any', 'positional', MISSING)], state.args, state.kwargs)
            if not e.agent_handler:
                raise LanguageError('当前纯函数环境不允许智能体效果')
            try:
                observed = observed_payload(bound['input'], e) if type(bound['input']) is ObservedInput else None
                if observed is not None and e.computer_handler is None:
                    raise LanguageError('Current environment does not allow visual Agent input', code='capability_error')
                if e.suspension.current and e.suspension.current.task:
                    payload = {'name': state.fn.name, 'config': serializable(e.host_value(state.fn.config)), 'value': None if observed is not None else serializable(e.host_value(bound['input']))}
                    if observed is not None:
                        payload['_observed'] = observed
                    self.phase = 'agent'
                    self.effect = Effect('agent', payload)
                    return External(self.effect)
                elif observed is not None:
                    raw = e.computer_handler('agent', {'name': state.fn.name, 'config': serializable(e.host_value(state.fn.config)), 'value': None, '_observed': observed, 'location': computer_location(e)})
                else:
                    raw = e.agent_handler(state.fn.name, e.host_value(state.fn.config), e.host_value(bound['input']))
            except (LanguageError, ExecutionControl):
                raise
            except Exception as error:
                raise LanguageError(str(error), code='provider_error') from None
            state.value = e.copy_value(raw)
            e.tick()
        elif isinstance(state.fn, Function):
            if state.authority_runner is not None:
                state.authority_runner.module_scope = getattr(state.fn.body, '_module_scope', None)
            source = getattr(state.fn.body, '_generated_source', None)
            if source is not None:
                from .generated_sources import register
                register(e, source)
            if state._context_callback and state.fn.flavor in ('gen_fn', 'async_gen_fn'):
                state.force = False
            state._context_callback = False
            if state._recovery_callback and state.force and (state.fn.flavor in ('gen_fn', 'async_gen_fn')):
                raise LanguageError('恢复回调不能强制执行生成器；请显式消费生成器', code='condition_protocol_error')
            state._recovery_callback = False
            if state.synchronous and state.fn.flavor != 'fn':
                raise LanguageError('描述符写入、删除或命名 hook 必须是同步可调用值', code='argument_error')
            state.synchronous = False
            if state.bound_parameters is None:
                from .patterns import bind_parameter_patterns
                state.bindings = e.bind_arguments(state.fn.name, state.fn.params, state.args, state.kwargs)
                state.bindings = bind_parameter_patterns(state.fn.params, state.bindings, resolve_tag=lambda name: e.pattern_constructor(name, state.fn.env), equal=e.equal, tick=e.tick, project_class=lambda name, item, keys, rest: e.pattern_class_fields(name, item, keys, rest, state.fn.env))
            else:
                state.bindings, state.bound_parameters = (state.bound_parameters, None)
            if state.fn.flavor != 'fn' and (not state.force):
                iterator = e._s_invoke(state.fn, state.args, state.kwargs, span=state.call_span, force=True, bound_parameters=state.bindings, all_results=True)
                state.value = e.suspension.task(iterator) if state.fn.flavor == 'async_fn' else e.suspension.generator(iterator, asynchronous=state.fn.flavor == 'async_gen_fn')
                state.value.capture_roots = (state.fn, state.bindings)
                for typ, location in state.required.items():
                    try:
                        check_type(state.value, typ)
                    except LanguageError as error:
                        raise error.attach(location)
                if state.pending:
                    state.fn, state.call_span, state.required, state.restore_authority, e.handled_error, e.error_context, state.restore_module, state.primary_only = state.pending.pop()
                    if state.authority_runner is not None:
                        state.authority_runner.capabilities = state.restore_authority
                        state.authority_runner.module_scope = state.restore_module
                    state.args, state.kwargs = ([state.value], {})
                    return self.done(DispatchAgain())
                state.capture_result = state.value
                return self.done(DispatchResult(state.value, cold=True))
            state.force = False
            if not state.counted_depth:
                e.depth += 1
                state.counted_depth = True
                if e.depth > 120:
                    raise ResourceLimit('非尾递归深度超过 120')
            state.scope = Env(state.fn.env)
            for name, state.value in state.bindings.items():
                state.scope.bind((state.fn.macro_bindings or {}).get(name, name), state.value)
            if state.debug_frame is None:
                e.frame_sequence += 1
                state.debug_frame = {'id': 'frame-' + str(e.frame_sequence)}
                e.frames.append(state.debug_frame)
            task = e.suspension.current.task if e.suspension.current else None
            state.debug_frame.update(name=state.fn.name, span=dict(state.call_span), env=state.scope, taskId=task.id if task else None)
            state.required.setdefault(state.fn.returns, state.call_span)
            return self.done(BodyEvaluation(state.fn, state.scope))
        else:
            raise LanguageError('尝试调用非函数值')
        return self.done(DispatchResult(state.value))

    def native(self, cursor):
        e = cursor.owner; state = self.state
        from .language import (Native, Function, Agent, Bot, Robot, EscapeContinuation, Continuation,
            LanguageError, ExecutionControl, MISSING, ResourceLimit, check_type,
            serializable, Env, TailCall, SetRequest, set_resolve)
        from .execution_frames import DispatchAgain, DispatchResult, BodyEvaluation, External
        from .suspension import Effect
        from .team_bindings import RoleRequest, call as bound_team_call
        from .objects import AccessRequest
        from .modes import ModeRequest, resolve as mode_resolve
        from .team_values import TeamRequest, resolve as team_resolve
        from .program_values import ProgramRequest, ProgramResult, resolve as program_resolve
        from .process_values import ProcessRequest, resolve as process_resolve
        from .session_values import SessionRequest, resolve as session_resolve
        from .device_values import DeviceRequest, resolve as device_resolve
        from .computer_values import ComputerRequest, resolve as computer_resolve
        from .control_prompts import Request as ControlRequest, resolve as control_resolve
        from .multiple_values import Request as ValuesRequest, resolve as values_resolve
        from .code_values import FunctionBuildRequest, construct as construct_function
        from .module_values import Request as ModuleRequest, construct as construct_module
        from .hash_maps import Request as HashMapRequest, resolve as hash_map_resolve
        from .generic_functions import GenericRequest, resolve as generic_resolve
        from .conditions import Request as RecoveryRequest, resolve as recovery_resolve
        from .dynamic_contexts import RunRequest as ContextRunRequest, resolve as context_resolve
        from .class_creation import ClassRequest, create as create_class
        from .metaclasses import TypeNewRequest, TypeCallRequest, TypeCheckRequest, lowlevel_new, default_call, check as type_check
        from .robot_values import Request as RobotRequest, resolve as robot_resolve
        from .operators import OperationRequest, operation
        from .resource_stacks import StackRequest, resolve as stack_resolve
        from .iteration import IterationRequest
        from .indexing import ItemRequest, operation as item_operation
        from .indexing import IndexRequest, resolve_index
        if self.native_stage == 0:
            self.native_stage += 1
            if type(state.value) is ControlRequest:
                return self.leaf(control_resolve(e, state.value))
        if self.native_stage == 1:
            self.native_stage += 1
            if type(state.value) is ValuesRequest:
                return self.leaf(values_resolve(e, state.value))
        if self.native_stage == 2:
            self.native_stage += 1
            if type(state.value) is FunctionBuildRequest:
                return self.leaf(construct_function(e, state.value))
        if self.native_stage == 3:
            self.native_stage += 1
            if type(state.value) is ModuleRequest:
                return self.leaf(construct_module(e, state.value))
        if self.native_stage == 4:
            self.native_stage += 1
            if type(state.value) is SetRequest:
                return self.leaf(set_resolve(e, state.value))
        if self.native_stage == 5:
            self.native_stage += 1
            if type(state.value) is HashMapRequest:
                return self.leaf(hash_map_resolve(e, state.value))
        if self.native_stage == 6:
            self.native_stage += 1
            if type(state.value) is GenericRequest:
                return self.leaf(generic_resolve(e, state.value))
        if self.native_stage == 7:
            self.native_stage += 1
            if isinstance(state.value, ContextRunRequest):
                return self.leaf(context_resolve(e, state.value))
        if self.native_stage == 8:
            self.native_stage += 1
            if isinstance(state.value, RecoveryRequest):
                return self.leaf(recovery_resolve(e, state.value))
        if self.native_stage == 9:
            self.native_stage += 1
            if isinstance(state.value, ClassRequest):
                return self.leaf(create_class(e, state.value))
        if self.native_stage == 10:
            self.native_stage += 1
            if type(state.value) is TypeNewRequest:
                return self.leaf(lowlevel_new(e, state.value))
        if self.native_stage == 11:
            self.native_stage += 1
            if type(state.value) is TypeCallRequest:
                return self.leaf(default_call(e, state.value))
        if self.native_stage == 12:
            self.native_stage += 1
            if type(state.value) is TypeCheckRequest:
                return self.leaf(type_check(e, state.value))
        if self.native_stage == 13:
            self.native_stage += 1
            if type(state.value) is RobotRequest:
                state.value = robot_resolve(e, state.value)
        if self.native_stage == 14:
            self.native_stage += 1
            if type(state.value) is DeviceRequest:
                state.value = device_resolve(e, state.value)
        if self.native_stage == 15:
            self.native_stage += 1
            if isinstance(state.value, ComputerRequest):
                state.value = computer_resolve(e, state.value)
        if self.native_stage == 16:
            self.native_stage += 1
            if isinstance(state.value, SessionRequest):
                state.value = session_resolve(e, state.value)
        if self.native_stage == 17:
            self.native_stage += 1
            if isinstance(state.value, ProcessRequest):
                state.value = process_resolve(e, state.value)
        if self.native_stage == 18:
            self.native_stage += 1
            if isinstance(state.value, ProgramRequest):
                state.value = program_resolve(e, state.value)
        if self.native_stage == 19:
            self.native_stage += 1
            self.program_validated = isinstance(state.value, ProgramResult)
        if self.native_stage == 20:
            self.native_stage += 1
            if self.program_validated:
                state.value = state.value.value
        if self.native_stage == 21:
            self.native_stage += 1
            if isinstance(state.value, TeamRequest):
                state.value = team_resolve(e, state.value)
        if self.native_stage == 22:
            self.native_stage += 1
            if isinstance(state.value, RoleRequest):
                state.value = bound_team_call(e, state.value.binding, state.value.args, state.value.kwargs, state.value.role)
        if self.native_stage == 23:
            self.native_stage += 1
            if isinstance(state.value, ModeRequest):
                state.value = mode_resolve(e, state.value)
        if self.native_stage == 24:
            self.native_stage += 1
            if isinstance(state.value, AccessRequest):
                if state.value.operation in ('dir', 'default_dir'):
                    from .attribute_protocols import directory
                    return self.leaf(directory(e, state.value.target, e._attribute_default_directory, default=state.value.operation == 'default_dir'))
                elif state.value.operation == 'default_get':
                    return self.leaf(e._s_default_get_member(state.value.target, state.value.name))
                elif state.value.operation == 'default_set':
                    return self.leaf(e._s_default_set_member(state.value.target, state.value.name, state.value.item))
                elif state.value.operation == 'default_delete':
                    return self.leaf(e._s_default_delete_member(state.value.target, state.value.name))
                elif state.value.operation == 'set':
                    return self.leaf(e._s_set_member(state.value.target, state.value.name, state.value.item))
                elif state.value.operation == 'delete':
                    return self.leaf(e._s_delete_member(state.value.target, state.value.name))
                elif state.value.operation == 'has':
                    self.phase = 'native_has'
                    return self.leaf(e._s_get_member(state.value.target, state.value.name))
                else:
                    return self.leaf(e._s_get_member(state.value.target, state.value.name))
        if self.native_stage == 25:
            self.native_stage += 1
            if isinstance(state.value, OperationRequest):
                return self.leaf(operation(e, state.value.operation, state.value.operands))
        if self.native_stage == 26:
            self.native_stage += 1
            if isinstance(state.value, StackRequest):
                return self.leaf(stack_resolve(e, state.value))
        if self.native_stage == 27:
            self.native_stage += 1
            if isinstance(state.value, IterationRequest):
                return self.leaf(e.suspension.iteration.resolve(state.value))
        if self.native_stage == 28:
            self.native_stage += 1
            if isinstance(state.value, IndexRequest):
                return self.leaf(resolve_index(e, state.value))
        if self.native_stage == 29:
            self.native_stage += 1
            self.item_validated = isinstance(state.value, ItemRequest)
        if self.native_stage == 30:
            self.native_stage += 1
            if self.item_validated:
                return self.leaf(item_operation(e, state.value))
        if self.native_stage == 31:
            self.native_stage += 1
            if isinstance(state.value, Effect):
                return External(state.value)
        if isinstance(state.value, Native):
            state.value.owner = e
        if not isinstance(state.value, TailCall) and (not self.item_validated) and (not self.program_validated):
            from .multiple_values import Completion, validate as validate_completion
            if type(state.value) is Completion:
                validate_completion(e, state.value)
            else:
                serializable(state.value)
        e.tick()
        return self.done(DispatchResult(state.value))


INVOCATION_DISPATCH_FRAMES = (InvocationDispatchFrame,)


def dispatch_protocol(evaluator, state):
    # Construct on first advance. Cold close/throw never enters invocation work.
    from .execution_frames import InvocationCloseCursor, ProtocolBridge
    cursor = InvocationCloseCursor(evaluator, InvocationDispatchFrame(state))
    ambient = getattr(evaluator, '_execution_cursor', None)
    if ambient is not None and ambient.frames and type(ambient.frames[-1]) is ProtocolBridge:
        bridge = ambient.frames[-1]
        if bridge.owner is not evaluator or getattr(bridge, 'dispatch_cursor', None) is not None:
            raise RuntimeError('Invocation dispatch observer is foreign or occupied')
        # pending_cursor remains the original native algorithm observation
        # edge. Coordination is supplemental and must not steal that slot.
        bridge.dispatch_cursor = cursor
        cursor.parent_bridge = bridge
    # This native generator owns the transport loop. If close lawfully reaches
    # another yield, keep this same public iterator alive for its next action.
    # All callable selection and request progression remain on the one frame.
    from .execution_frames import Done
    value = error = None
    closing_error = None
    while True:
        if closing_error is not None:
            action = cursor.close_request(closing_error)
            if type(action) is Done:
                if cursor.close_completed:
                    return action.value
                raise cursor.close_signal
            pending = action.request
        else:
            try:
                pending = cursor.throw(error) if error is not None else cursor.send(value)
            except StopIteration as stop:
                return stop.value
        try:
            value = yield pending
            error = closing_error = None
        except GeneratorExit as failure:
            value = error = None
            closing_error = failure
        except BaseException as failure:
            value = closing_error = None
            error = failure
