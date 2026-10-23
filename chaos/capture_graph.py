"""Explicit native capture storage, independent of host closures and user hooks.

This walk conservatively includes every stored Function Env binding. It is not
free-variable analysis. Native adapters declare their language captures; no
Python closure, iterator frame, property or arbitrary __dict__ is inspected.
"""


def seal(evaluator, roots, ceiling):
    from .continuations import Continuation,roots as continuation_roots
    from .execution_continuations import CONTINUATION_FRAMES
    from .language import Native, Function, Env, Cell, Agent, LanguageError, Raised, ResourceLimit, ReturnSignal, TailCall, Node, MISSING
    from .multiple_values import Completion,Request as ValuesRequest,components
    from .tuple_values import TupleValue
    from .hash_maps import HashMap,_HashBudget as HashBudgetCarrier,_Builder as HashMapDraft
    from .objects import (ChaosClass, ChaosInstance, Member, ClassMember, BoundMethod,
                          SuperProxy, StaticMethod, ClassMethod, Property, AccessRequest, AccessorCall, ProtocolCall)
    from .error_values import Error
    from .errors import GroupedError
    from .dynamic_contexts import Context, ContextVar, ContextToken, ContextScope, State, Lease as ContextLease
    from .generic_functions import NextDescriptor, Method, Descriptor, Combination, GenericRequest, intersection, fail
    from .agent_tools import AgentTool, BoundAgent
    from .team_bindings import BoundTeam
    from .session_tools import BoundSession
    from .suspension import Task, Generator, AsyncGenerator, Runner, Await, Drain, Join, Yield, Effect
    from .channels import Channel, Message, Send, Receive
    from .channel_select import ChannelCase, Selection, Offer
    from .contexts import ContextManager, AsyncContextManager, ExitEntry
    from .resource_stacks import ExitStack, AsyncExitStack, CallbackEntry
    from .task_groups import TaskGroup, ScopeOutcome, GroupRun, GroupAbort
    from .task_waits import Wait
    from .guidance_waits import GuidanceWait
    from .bot_values import Bot,owned as bot_owned
    from .bot_runtime import Decision,BotRequest
    from .execution_bot import BOT_FRAMES
    from .execution_iteration import ITERATION_FRAMES,IterationFrameRequest
    from .execution_comprehensions import COMPREHENSION_FRAMES,ComprehensionState,ComprehensionRequest
    from .execution_conditions import RECOVERY_FRAMES,RecoveryCallbackRequest
    from .execution_operators import OPERATOR_FRAMES,OperatorFrameRequest
    from .execution_access import ACCESS_FRAMES,AccessFrameRequest
    from .execution_hash_collections import HASH_COLLECTION_FRAMES,HashCollectionFrameRequest,collection_carrier_roots
    from .execution_class_creation import CLASS_FRAMES,ClassFrameRequest,class_carrier_roots
    from .execution_callables import CALLABLE_FRAMES,CallableFrameRequest,callable_carrier_roots
    from .execution_patterns import PATTERN_FRAMES,PatternFrameRequest,pattern_carrier_roots
    from .execution_modules import MODULE_FRAMES,ModuleFrameRequest,module_carrier_roots,LegacyModuleLoadFrame
    from .execution_suspension import SUSPENSION_FRAMES,SuspensionFrameRequest,suspension_carrier_roots
    from .execution_multiple_values import MULTIPLE_VALUE_FRAMES
    from .execution_invocation import INVOCATION_DISPATCH_FRAMES
    from .execution_modes import MODE_FRAMES
    from .iteration import Iterator, AsyncIterator
    from .conditions import HandlerScope, RestartScope, Restart, DynamicState, RestartTransfer, Request as RecoveryRequest
    from .capture_frames import Frame as CaptureFrame, Edge as CaptureEdge
    from .control_prompts import PromptTag,PromptScope,EscapeContinuation,Barrier,ControlState,PromptTransfer,Request as ControlRequest
    from .resource_stacks import StackRequest
    from .iteration import IterationRequest
    from .code_values import FunctionBuildRequest
    from .module_values import Request as ModuleRequest, Scope as ModuleScope
    from .dynamic_contexts import RunRequest
    from .class_creation import ClassRequest
    from .metaclasses import TypeNewRequest, TypeCallRequest, TypeCheckRequest
    from .indexing import ItemRequest, IndexRequest
    from .operators import OperationRequest
    from .hash_maps import Request as HashRequest
    from .set_runtime import Request as SetRequest,Builder as SetDraft
    from .robot_values import VALUES as ROBOT_VALUES,RobotCommand,Request as RobotRequest,owned as robot_owned
    from .device_values import VALUES as DEVICE_VALUES, Device, DeviceAction, DeviceRequest, owned as device_owned
    from .computer_values import VALUES as COMPUTER_VALUES, Computer, ComputerAction, ObservedInput, ComputerRequest, owned as computer_owned
    from .set_values import SetValue
    from .execution_frames import ExecutionCursor,GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor,EvaluationBoundaryFrame,OperandFrame,InvocationFrame,ReturnFrame,ProtocolBridge,LoopFrame,LoopCompletionFrame,ProtectedValueFrame,TryFrame,FinallyFrame,TryCompletionFrame,GroupTryFrame,ContextFrame,PromptScopeFrame,CancelShieldFrame,TaskGroupFrame,GroupBodyFrame,frame_roots

    from .execution_context_protocols import ContextProtocolRequest,CONTEXT_PROTOCOL_FRAMES
    from .execution_control import (CallbackRequest,PromptHandlerRequest,ControlCallbackFrame,
                                    PromptHandlerFrame,PromptCallFrame,ControlBarrierFrame,DynamicWindFrame)

    from .compiled_flow_state import FlowState
    from .contracts import Contract
    from .eql_specializers import EqlSpecializer

    def evaluator_owned(value):
        if value.owner is not evaluator: fail('Captured native value cannot cross evaluators','ownership_error')

    def epoch_owned(value):
        scheduler=evaluator.suspension
        if value.owner is not scheduler or value.epoch!=scheduler.epoch:
            fail('Captured resource cannot cross owner or epoch','ownership_error')

    # New control frames expose state formerly held in an opaque protocol
    # generator. Audit that state fully without charging representation-only
    # traversal to the language step budget. Drain the original DFS first;
    # separate seen maps keep either route from suppressing the other's walk.
    control_nodes = CONTINUATION_FRAMES+(FlowState,CallbackRequest,PromptHandlerRequest,ControlCallbackFrame,
                     PromptHandlerFrame,PromptCallFrame,ControlBarrierFrame,DynamicWindFrame,BotRequest,RecoveryCallbackRequest,IterationFrameRequest,ComprehensionRequest,ComprehensionState,ContextProtocolRequest,OperatorFrameRequest,AccessFrameRequest,HashCollectionFrameRequest,ClassFrameRequest,CallableFrameRequest,PatternFrameRequest,ModuleFrameRequest,SuspensionFrameRequest,GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor,HashBudgetCarrier,HashMapDraft)+OPERATOR_FRAMES+ACCESS_FRAMES+HASH_COLLECTION_FRAMES+CLASS_FRAMES+CALLABLE_FRAMES+PATTERN_FRAMES+MODULE_FRAMES+SUSPENSION_FRAMES+MULTIPLE_VALUE_FRAMES+INVOCATION_DISPATCH_FRAMES+MODE_FRAMES+CONTEXT_PROTOCOL_FRAMES+BOT_FRAMES+RECOVERY_FRAMES+ITERATION_FRAMES+COMPREHENSION_FRAMES
    pending = [(root,ceiling,0) for root in roots]
    supplementary = []
    seen_by_mode = ({}, {})
    updates, count, charged_count = {}, 0, 0
    charging = True
    # frame_roots can create transient CaptureEdges. Keep object identities alive
    # through both walks so allocator ID reuse cannot masquerade as an alias.
    retained_identities = {}
    while pending or supplementary:
        if not pending:
            if charging:
                # The original charged DFS is now complete. A semantic object
                # already audited at an equally strict authority need not be
                # walked again merely because a new frame exposes its alias.
                # Copy once: narrower supplementary paths still re-audit, and
                # later batches must retain their accumulated intersections.
                seen_by_mode = (seen_by_mode[0], dict(seen_by_mode[0]))
            pending, supplementary = supplementary, []
            charging = False
        value, ceiling, depth = pending.pop()
        kind = type(value)
        retained_identities[id(value)] = value
        if charging and (kind in control_nodes or kind is ProtocolBridge and getattr(value,'supplementary',False)):
            supplementary.append((value,ceiling,depth))
            if len(pending)+len(supplementary)+count > 20000:
                raise ResourceLimit('Generic capture graph exceeds 20000 nodes')
            continue
        count += 1
        if count == 1: evaluator.tick()
        if charging:
            charged_count += 1
            if charged_count % 64 == 0: evaluator.tick()
        if count > 20000 or depth > 100:
            raise ResourceLimit('Generic capture graph exceeds 20000 nodes or depth 100')
        seen = seen_by_mode[0 if charging else 1]
        children = ()
        if kind is Native:
            if value.owner is not None and value.owner is not evaluator:
                fail('Captured native function cannot cross evaluators','ownership_error')
            ceiling = intersection(ceiling,value.capabilities)
            if type(value.generic) is NextDescriptor:
                token = value.generic
                if token.owner is not evaluator or value.owner is not evaluator:
                    fail('Generic next cannot cross evaluators','ownership_error')
                ceiling = intersection(ceiling,token.capabilities)
                if id(token) not in updates: updates[id(token)] = [token,ceiling,[]]
                update = updates[id(token)]
                update[1] = intersection(update[1],ceiling)
                update[2].append(value)
            children = value.captures
            if type(children) is not tuple: fail('Native captures require an immutable tuple')
        elif kind is Function:
            if value.owner is not None and value.owner is not evaluator:
                fail('Captured function cannot cross evaluators','ownership_error')
            ceiling = intersection(ceiling,value.capabilities)
            defaults = tuple(p[3] for p in value.params if len(p)>3 and p[3] is not MISSING)
            children = (value.env,value.returns)+tuple(p[1] for p in value.params)+defaults
            if type(value.body) is Node:children+=(getattr(value.body,"_module_scope",None),)
        elif kind is Completion:
            evaluator_owned(value);children=components(value,evaluator)
        elif kind is ValuesRequest:
            evaluator_owned(value);children=(value.function,)+value.producers
        elif kind is Continuation:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities);children=continuation_roots(evaluator,value)
        elif kind in CONTINUATION_FRAMES:
            evaluator_owned(value);children=value.roots()
        elif kind is PromptTag:evaluator_owned(value)
        elif kind in (PromptScope,EscapeContinuation,Barrier):
            epoch_owned(value);ceiling=intersection(ceiling,value.capabilities)
            if kind is PromptScope:children=(value.tag,value.handler,value.holder)
            elif kind is EscapeContinuation:children=(value.scope,)
            else:children=(value.holder,)
        elif kind is ControlState:
            epoch_owned(value);children=tuple(value.stack)
        elif kind is PromptTransfer:
            ceiling=intersection(ceiling,value.authority);children=(value.target,value.completion)
        elif kind is BotRequest:
            children=tuple(child for item in value.values for child in (item if type(item) is tuple else (item,)))
        elif kind is ModuleFrameRequest:
            children=module_carrier_roots(evaluator,value)
        elif kind is SuspensionFrameRequest:
            children=suspension_carrier_roots(evaluator,value)
        elif kind is PatternFrameRequest:
            children=pattern_carrier_roots(evaluator,value)
        elif kind is CallableFrameRequest:
            children=callable_carrier_roots(evaluator,value)
        elif kind is ClassFrameRequest:
            children=class_carrier_roots(evaluator,value)
        elif kind in (OperatorFrameRequest,AccessFrameRequest,HashCollectionFrameRequest):
            if type(value.values) in (tuple,list):
                children=tuple(child for item in value.values for child in (item if type(item) in (tuple,list) else (item,)))
            else:children=(value.values,)
        elif kind is ContextProtocolRequest:
            children=tuple(child for item in value.values for child in (item if type(item) is tuple else (item,))) if type(value.values) is tuple else (value.values,)
        elif kind in BOT_FRAMES+RECOVERY_FRAMES+ITERATION_FRAMES+COMPREHENSION_FRAMES+CONTEXT_PROTOCOL_FRAMES+OPERATOR_FRAMES+ACCESS_FRAMES+HASH_COLLECTION_FRAMES+CLASS_FRAMES+CALLABLE_FRAMES+PATTERN_FRAMES+MODULE_FRAMES+SUSPENSION_FRAMES+MULTIPLE_VALUE_FRAMES+INVOCATION_DISPATCH_FRAMES+MODE_FRAMES:
            evaluator_owned(value);children=frame_roots(value)
        elif kind is ControlRequest:children=value.values
        elif kind is CallbackRequest:
            ceiling=intersection(ceiling,value.authority)
            args=tuple(value.args) if type(value.args) in (tuple,list) else (value.args,)
            children=(value.function,)+args
        elif kind is PromptHandlerRequest:
            ceiling=intersection(ceiling,value.scope.capabilities,value.signal.authority)
            children=(value.scope,value.handler,value.signal)
        elif kind in (ControlCallbackFrame,PromptHandlerFrame,PromptCallFrame,ControlBarrierFrame,DynamicWindFrame):
            evaluator_owned(value);children=frame_roots(value)
        elif kind in (GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor):
            evaluator_owned(value);children=value.roots()
        elif kind is ExecutionCursor:
            evaluator_owned(value);children=tuple(value.frames)
            module_state=getattr(value,'module_state',None)
            if module_state is not None:
                if type(module_state) is not LegacyModuleLoadFrame or module_state.owner is not evaluator:fail('Foreign legacy module state','ownership_error')
                supplementary.append((module_state,ceiling,depth+1))
            # A rejected close may leave the public native generator reachable
            # through root_bridge even after this outer driver has drained.
            # Audit only the exact new opt-in cleanup cursor, supplementary.
            bridge=getattr(value,'root_bridge',None)
            child=getattr(bridge,'pending_cursor',None) if type(bridge) is ProtocolBridge else None
            if value.closed and type(child) in (GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor) and not child.closed:
                supplementary.append((child,ceiling,depth+1))
            mode=getattr(bridge,'mode_cursor',None) if type(bridge) is ProtocolBridge else None
            if value.closed and mode is not None:
                if type(mode) is not ModeCloseCursor or mode.owner is not evaluator or mode.parent_bridge is not bridge:
                    fail('Foreign retained mode observer', 'ownership_error')
                supplementary.append((mode,ceiling,depth+1))
            dispatch=getattr(bridge,'dispatch_cursor',None) if type(bridge) is ProtocolBridge else None
            if value.closed and dispatch is not None:
                if type(dispatch) is not InvocationCloseCursor or dispatch.owner is not evaluator or dispatch.parent_bridge is not bridge:
                    fail('Foreign retained invocation dispatch', 'ownership_error')
                # Retired dispatch normally clears this slot. If a closed
                # cursor remains, still validate its epoch/flags/empty frames;
                # a forged closed flag must not hide the new auxiliary state.
                supplementary.append((dispatch,ceiling,depth+1))
        elif kind in (EvaluationBoundaryFrame,OperandFrame,InvocationFrame,ReturnFrame,ProtocolBridge,LoopFrame,LoopCompletionFrame,ProtectedValueFrame,TryFrame,FinallyFrame,TryCompletionFrame,GroupTryFrame,ContextFrame,PromptScopeFrame,CancelShieldFrame,TaskGroupFrame,GroupBodyFrame):
            evaluator_owned(value)
            if kind is InvocationFrame:ceiling=intersection(ceiling,getattr(getattr(value,'authority_runner',None),'capabilities',None))
            elif kind is ProtectedValueFrame:ceiling=intersection(ceiling,value.capabilities)
            elif kind in (OperandFrame,LoopFrame,LoopCompletionFrame,TryFrame,FinallyFrame,TryCompletionFrame,GroupTryFrame,ContextFrame,PromptScopeFrame,CancelShieldFrame,TaskGroupFrame):
                ceiling=intersection(ceiling,getattr(value.boundary.capture,'capabilities',None))
                if kind in (PromptScopeFrame,CancelShieldFrame,TaskGroupFrame):ceiling=intersection(ceiling,getattr(value.manager,'capabilities',None))
            elif kind is GroupBodyFrame:
                ceiling=intersection(ceiling,value.capabilities)
                flow=getattr(value,'_flow_state',None)
                if flow is not None:
                    if type(flow)is not FlowState or flow.owner is not evaluator:fail('Foreign group compiled body','ownership_error')
                    supplementary.append((flow,ceiling,depth+1))
            if kind is ProtocolBridge:
                mode = getattr(value, 'mode_cursor', None)
                if mode is not None:
                    if type(mode) is not ModeCloseCursor or mode.owner is not evaluator or mode.parent_bridge is not value:
                        fail('Foreign mode observer', 'ownership_error')
                    supplementary.append((mode,ceiling,depth+1))
                dispatch = getattr(value, 'dispatch_cursor', None)
                if dispatch is not None:
                    if type(dispatch) is not InvocationCloseCursor or dispatch.owner is not evaluator or dispatch.parent_bridge is not value:
                        fail('Foreign invocation dispatch observer', 'ownership_error')
                    supplementary.append((dispatch,ceiling,depth+1))
            children=frame_roots(value)
        elif kind is FlowState:
            evaluator_owned(value);children=value.roots()
        elif kind is CaptureFrame:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities)
            children=tuple(value.roots)+(getattr(value.holder,"iterator",None),)
            compiled=getattr(value,'compiled_flow_state',None)
            if compiled is not None:
                if type(compiled)is not FlowState or compiled.owner is not evaluator:fail('Foreign compiled flow state','ownership_error')
                supplementary.append((compiled,ceiling,depth+1))
        elif kind is CaptureEdge:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities);children=value.values
        elif kind is TailCall:
            children=(value.function,)+tuple(value.args)+tuple((value.kwargs or {}).values())+tuple(value.contracts)+tuple(item[0] for item in value.continuations)+(value.handled_error,value.error_context,value.module_scope)
        elif kind is Bot:
            bot_owned(value,evaluator);ceiling=intersection(ceiling,value.capabilities)
            children=(value.planner,value.execute,value.verify,value.state.goal,value.state.context,value.state.input,value.state.guidance,value.state.evidence,value.state.history)
        elif kind is Decision:
            evaluator_owned(value)
            if value.epoch!=evaluator.suspension.epoch:fail('Bot decision cannot cross epochs','ownership_error')
            children=(value.group,)+value.parents
        elif kind in ROBOT_VALUES:
            robot_owned(value,evaluator);ceiling=intersection(ceiling,getattr(value,"capabilities",None))
            if kind is RobotCommand:children=(value.observation,)
        elif kind is RobotRequest:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities);children=(value.robot,value.command)
        elif kind in DEVICE_VALUES:
            device_owned(value,evaluator)
            ceiling=intersection(ceiling,getattr(value,'capabilities',None))
        elif kind is DeviceRequest:
            ceiling=intersection(ceiling,value.capabilities);children=(value.device,value.action)
        elif kind in COMPUTER_VALUES:
            computer_owned(value,evaluator)
            ceiling=intersection(ceiling,value.capabilities)
            if kind is Computer and value.epoch!=evaluator.suspension.epoch:
                fail('Captured Computer cannot cross epochs','ownership_error')
            if kind in (ComputerAction,ObservedInput):children=(value.observation,)
        elif kind is ComputerRequest:
            ceiling=intersection(ceiling,value.capabilities)
            children=(value.computer,value.action,value.observation)
        elif kind is AccessRequest:children=(value.target,value.item)
        elif kind is AccessorCall:
            ceiling=intersection(ceiling,value.capabilities);children=(value.function,value.item)+tuple(value.args)
        elif kind is ProtocolCall:children=(value.descriptor,value.item)+tuple(value.args)
        elif kind is StackRequest:
            arguments=tuple(value.args) if type(value.args) in (tuple,list) else (value.args,)
            keywords=tuple(value.kwargs.values()) if type(value.kwargs) is dict else (() if value.kwargs is None else (value.kwargs,))
            children=(value.stack,value.value)+arguments+keywords
        elif kind is IterationFrameRequest:
            children=tuple(child for item in value.values for child in (item if type(item) is tuple else (item,)))
        elif kind is ComprehensionRequest:children=(value.env,value.outer)
        elif kind is ComprehensionState:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities);children=value.roots()
        elif kind is IterationRequest:
            children=(value.handle,)+(tuple(value.value) if type(value.value) is tuple else (value.value,))
        elif kind in (FunctionBuildRequest,ModuleRequest):children=tuple(item[1] for item in value.bindings)
        elif kind is ModuleScope:
            if value.evaluator is not evaluator:fail('Captured Module cannot cross evaluators','ownership_error')
            children=tuple(item[1] for item in value.bindings)+tuple(value.cache.values())
        elif kind is RunRequest:
            arguments=tuple(value.args) if type(value.args) in (tuple,list) else (value.args,)
            keywords=tuple(value.kwargs.values()) if type(value.kwargs) is dict else (value.kwargs,)
            children=(value.context,value.function)+arguments+keywords
        elif kind is ClassRequest:children=(value.bases,value.members,value.options)
        elif kind is TypeNewRequest:children=(value.metaclass,value.bases,value.namespace,value.options)
        elif kind is TypeCallRequest:children=(value.klass,)+tuple(value.args)+tuple(value.options.values())
        elif kind is TypeCheckRequest:children=(value.value,value.klass)
        elif kind is ItemRequest:children=(value.target,value.key,value.item)
        elif kind is IndexRequest:children=tuple(value.values)
        elif kind is OperationRequest:children=tuple(value.operands)
        elif kind is HashRequest:children=(value.mapping,value.key,value.value)
        elif kind is SetRequest:children=(value.value,value.other)
        elif kind in (HashBudgetCarrier,HashMapDraft):
            children=collection_carrier_roots(evaluator,value)
        elif kind is SetDraft:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities);children=(value.items,)
            supplementary.extend((edge,ceiling,depth+1) for edge in collection_carrier_roots(evaluator,value))
        elif kind is SetValue:
            if value.owner is not None:evaluator_owned(value)
            ceiling=intersection(ceiling,value.capabilities);children=value.items
        elif kind is RecoveryCallbackRequest:
            ceiling=intersection(ceiling,value.authority)
            args=tuple(value.args) if type(value.args) in (tuple,list) else (value.args,)
            children=(value.function,)+args+(value.kwargs,)
        elif kind is RecoveryRequest:
            children=(tuple(value.value) if type(value.value) is tuple else (value.value,))+(tuple(value.extra) if type(value.extra) is tuple else (value.extra,))
        elif kind is RestartTransfer:
            ceiling=intersection(ceiling,value.authority)
            children=(value.restart,)+tuple(value.args)+tuple(value.kwargs.values())
        elif kind is Env:
            children = tuple(value.values.values())+(value.parent,)
            supplementary.extend((edge,ceiling,depth+1) for edge in (class_carrier_roots(evaluator,value) or ()))
        elif kind in (Cell,Member): children = (value.value,value.contract)
        elif kind is Method:
            ceiling = intersection(ceiling,value.capabilities)
            children = value.specializers+(value.implementation,)
        elif kind is Descriptor:
            if value.owner is not evaluator: fail('Captured generic cannot cross evaluators','ownership_error')
            children = value.methods
        elif kind is Combination: children = value.primary+value.before+value.after+value.around
        elif kind is GenericRequest:
            children = value.args+tuple(value.kwargs.values())+(value.descriptor,value.combination)
        elif kind in (list,dict,TupleValue):
            if len(value)>10000: raise ResourceLimit('Generic capture container exceeds 10000 items')
            if kind is dict and any(type(key) is not str for key in value): fail('Capture Record keys require String')
            children = tuple(value.values()) if kind is dict else value.items if kind is TupleValue else value
        elif kind is EqlSpecializer:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities)
            children=(value.payload,)
        elif kind is Contract:
            evaluator_owned(value)
            if type(value.kind) is not str or type(value.arguments) is not tuple:
                fail('Malformed native Contract captures')
            if value.kind == 'record':
                if len(value.arguments)!=3 or any(type(fields) is not tuple for fields in value.arguments[:2]):
                    fail('Malformed native Record Contract captures')
                fields=value.arguments[0]+value.arguments[1]
                if any(type(pair) is not tuple or len(pair)!=2 for pair in fields):
                    fail('Malformed native Record Contract fields')
                children=tuple(pair[1] for pair in fields)
            elif value.kind == 'recursive':
                args=value.arguments
                if len(args)!=2 or type(args[0]) is not tuple or not 1<=len(args[0])<=128 or type(args[1]) is not str:
                    fail('Malformed recursive Contract captures')
                if any(type(pair) is not tuple or len(pair)!=2 or type(pair[0]) is not str for pair in args[0]):
                    fail('Malformed recursive Contract definitions')
                children=tuple(pair[1] for pair in args[0])
            elif value.kind == 'ref':
                if len(value.arguments)!=1 or type(value.arguments[0]) is not str:fail('Malformed reference Contract captures')
                children=()
            else:children=value.arguments
        elif kind is HashMap:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities)
            if len(value.entries)>10000:raise ResourceLimit('Generic capture HashMap exceeds 10000 items')
            children = tuple(child for key,item,fingerprint in value.entries for child in (key,item))
        elif kind in (ChaosClass,ChaosInstance,ClassMember,BoundMethod,SuperProxy,StaticMethod,ClassMethod,Property):
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities)
            if kind is ChaosInstance: children = (value.klass,)+tuple(value.fields.values())
            elif kind is ChaosClass: children = tuple(value.members.values())+value.bases+(value.metaclass,)
            elif kind is ClassMember: children = (value.value,value.contract)
            elif kind is BoundMethod: children = (value.function,value.receiver,value.defining_class)
            elif kind is SuperProxy: children = (value.start_class,value.receiver)
            elif kind in (StaticMethod,ClassMethod): children = (value.value,)
            else: children = (value.fget,value.fset,value.fdel,value.previous)
        elif kind is Error:
            evaluator_owned(value);ceiling=intersection(ceiling,value.capabilities)
            children=(value.exception,)
        elif kind in (LanguageError,Raised,GroupedError):
            ceiling=intersection(ceiling,getattr(value,'_error_capabilities',None))
            children=(value.value,getattr(value,'_error_cause',None),getattr(value,'_error_context',None))
            if kind is GroupedError:children+=value.exceptions
        elif kind in (Context,ContextVar,ContextToken,ContextScope):
            epoch_owned(value)
            if kind is Context:children=tuple(value.bindings.values())+tuple(value.bindings)+tuple(value.leases)
            elif kind is ContextVar:children=(value.default,)
            elif kind is ContextToken:children=(value.variable,value.context,value.old_value)
            else:children=tuple(child for pair in value.bindings for child in pair)
        elif kind is State: children=(value.current,)
        elif kind is ContextLease:
            children=(value.manager,value.context)+tuple(child for pair in value.previous for child in pair)
        elif kind in (HandlerScope,RestartScope,Restart):
            epoch_owned(value);ceiling=intersection(ceiling,value.capabilities)
            if kind is Restart:children=(value.scope,value.function,value.test)
            else:
                children=tuple(child for binding in value.bindings for child in binding)
                if kind is RestartScope:children+=value.restarts
        elif kind is DynamicState:children=tuple(value.scopes)
        elif kind in (AgentTool,BoundAgent,BoundTeam,BoundSession):
            evaluator_owned(value)
            if kind in (BoundTeam,BoundSession) and value.epoch!=evaluator.suspension.epoch:
                fail('Captured binding cannot cross epochs','ownership_error')
            ceiling=intersection(ceiling,getattr(value,'capabilities',None))
            if kind is AgentTool:children=(value.handler,)
            elif kind is BoundAgent:children=(value.agent,)+value.tools
            elif kind is BoundSession:children=(value.session,)+value.tools
            else:children=(value.team,)+tuple(target for name,target in value.bindings)
        elif kind in (Task,Generator,AsyncGenerator):
            epoch_owned(value)
            ceiling=intersection(ceiling,value.capabilities)
            children=value.capture_roots+(value.runner,getattr(value,'bot_guard',None))
            if kind is Task:children+=tuple(value.members)+(value.channel,value.channel_message,value.selection,
                                                         value.native_wait,value.guidance_wait,value.interrupt,value.deferred_interrupt,value.group,value.scope_group,value.channel_cursor)
            else:children+=(value.result,)
        elif kind is Runner:
            ceiling=intersection(ceiling,value.capabilities)
            children=tuple(frame['env'] for frame in value.frames if type(frame) is dict and 'env' in frame)
            children+=(value.iterator,value.value,value.result,value.error,value.handled_error,value.error_context,value.context_state,value.condition_state,value.control_state,
                       getattr(value,'bot_guard',None),value.waiting,value.await_owned,value.deferred_error,value.stream,getattr(value,"module_scope",None))+tuple(value.capture_frames)
        elif kind in (Await,Drain):children=(value.task,)
        elif kind is Join:
            children=tuple(value.tasks)+tuple(value.members)+tuple(value.pending)+(value.error,value.runner)
        elif kind is Yield:children=(value.value,)
        elif kind is Effect:children=(value.payload,)
        elif kind is Send:children=(value.channel,value.message)
        elif kind is Receive:children=(value.channel,)
        elif kind is GroupRun:children=(value.group,)
        elif kind is GroupAbort:children=(value.group,value.error)
        elif kind is Wait:children=tuple(value.tasks)
        elif kind is GuidanceWait:
            epoch_owned(value);children=()
        elif kind is Channel:
            epoch_owned(value)
            children=tuple(value.buffer)+tuple(value.senders.values())
        elif kind is Message: children=(value.value,)
        elif kind is ChannelCase:
            epoch_owned(value)
            children=(value.channel,value.message)
        elif kind is Selection:children=tuple(value.cases)+tuple(value.messages.values())
        elif kind is Offer:children=(value.task,value.channel,value.message)
        elif kind in (ContextManager,AsyncContextManager):
            epoch_owned(value);children=(value.generator,)
        elif kind in (ExitStack,AsyncExitStack):
            epoch_owned(value);ceiling=intersection(ceiling,value.capabilities)
            children=tuple(value.entries)+(value.operation_task,)
        elif kind is CallbackEntry: children=(value.function,)+tuple(value.args)+tuple(value.kwargs.values())
        elif kind is ExitEntry:children=(value.target,value.function)
        elif kind is TaskGroup:
            epoch_owned(value);children=(value.body,value.runner,value.error,value.outcome)+tuple(value.members)+tuple(value.failures)
        elif kind is ScopeOutcome:children=(value.value,value.error,value.signal)
        elif kind is ReturnSignal:children=(value.value,)
        elif kind in (Iterator,AsyncIterator):
            epoch_owned(value)
            ceiling=intersection(ceiling,value.capabilities)
            children=(value.target,value.values,value.result,getattr(value,"bot_guard",None))
        else:
            # Pure descriptors/data and native definitions without language
            # captures are leaves. Host subclasses are never traversed.
            continue
        identity=id(value)
        if identity in seen:
            merged=intersection(seen[identity],ceiling)
            if merged==seen[identity]:continue
            ceiling=merged
        seen[identity]=ceiling
        if len(children)+len(pending)+len(supplementary)+count>20000:
            raise ResourceLimit('Generic capture graph exceeds 20000 nodes')
        pending.extend((child,ceiling,depth+1) for child in children)
    evaluator.tick()
    # Preflight succeeds before any shared capsule is changed. Every encountered
    # alias then shares the intersection of all paths, including narrower DAGs.
    for token,ceiling,aliases in updates.values():
        token.capabilities=intersection(token.capabilities,ceiling)
        for alias in aliases:alias.capabilities=intersection(alias.capabilities,token.capabilities)
