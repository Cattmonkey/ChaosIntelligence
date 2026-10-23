"""The default execution cursor with authoritative data-backed control frames.

Opaque protocol bridges retain the original unmigrated native operations. They
are deliberately not capturable. This module provides no saved continuation API.
"""
from dataclasses import dataclass,field
from .execution_loops import LoopFrame,LoopCompletionFrame
from .execution_protection import ProtectedValueFrame,TryFrame,FinallyFrame,TryCompletionFrame
from .execution_group_handlers import GroupTryFrame
from .execution_resources import ContextFrame
from .execution_scopes import PromptScopeFrame,CancelShieldFrame,TaskGroupFrame,GroupBodyFrame
from .execution_bot import BOT_FRAMES,bot_frame
from .execution_iteration import ITERATION_FRAMES,iteration_frame
from .execution_comprehensions import COMPREHENSION_FRAMES,comprehension_frame
from .execution_context_protocols import CONTEXT_PROTOCOL_FRAMES,context_frame
from .execution_conditions import RECOVERY_FRAMES,recovery_frame
from .execution_operators import OPERATOR_FRAMES,operator_frame
from .execution_access import ACCESS_FRAMES,AccessFrameRequest,access_frame,ObjectAccessEvaluationFrame
from .execution_hash_collections import (HASH_COLLECTION_FRAMES,HashCollectionFrameRequest,
                                         hash_collection_frame,SetLiteralEvaluationFrame)
from .execution_class_creation import (CLASS_FRAMES, ClassFrameRequest, class_frame,
                                       ClassSyntaxEvaluationFrame)
from .execution_callables import (CALLABLE_FRAMES, CallableFrameRequest, callable_frame,
                                  CallableSyntaxFrame)
from .execution_patterns import (PATTERN_FRAMES, PatternFrameRequest, pattern_frame,
                                  PatternSyntaxFrame)
from .execution_modules import MODULE_FRAMES,ModuleFrameRequest,module_frame,ModuleSyntaxFrame
from .execution_suspension import SUSPENSION_FRAMES,SuspensionFrameRequest,suspension_frame
from .execution_multiple_values import MULTIPLE_VALUE_FRAMES
from .execution_invocation import INVOCATION_DISPATCH_FRAMES
from .execution_modes import MODE_FRAMES
from .execution_control import (ControlCallbackFrame,PromptHandlerFrame,PromptCallFrame,
                                ControlBarrierFrame,DynamicWindFrame,control_frame)

@dataclass
class EvaluationRequest:
    node:object
    env:object
    tail:bool=False
    options:dict=field(default_factory=dict)

@dataclass
class InvocationRequest:
    fn:object
    args:object
    kwargs:object=None
    options:dict=field(default_factory=dict)

@dataclass
class ProtectedValueRequest:
    node:object
    env:object

@dataclass
class Done:
    value:object=None

@dataclass
class Push:
    frame:object

@dataclass
class External:
    request:object
    native_leaf:bool=False

@dataclass(frozen=True)
class CloseYield:
    """One actual protocol yield reached after a context suppressed close."""
    request:object

class DispatchAgain:pass

@dataclass
class DispatchResult:
    value:object
    cold:bool=False

@dataclass
class BodyEvaluation:
    function:object
    scope:object


class ProtocolBridge:
    """One explicitly opaque operation; never copied or treated as a snapshot."""
    def __init__(self,iterator):self.iterator=iterator;self.started=False
    def step(self,cursor,value,error):
        try:
            self.started=True
            if type(cursor) in (PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor):
                request=cursor.original_call(self.iterator.throw,error,_bridge=self) if error is not None else cursor.original_call(self.iterator.send,value,_bridge=self)
            else:
                request=self.iterator.throw(error) if error is not None else self.iterator.send(value)
        except StopIteration as stop:return Done(stop.value)
        # This exact new driver owns only generic algorithm/control state.
        # Keep all pre-existing Invocation/Evaluation frames on the original
        # outer cursor, with their original charged capture path and ordering.
        if type(cursor) in (GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor):return External(request)
        if type(request) is EvaluationRequest:return Push(EvaluationBoundaryFrame(request))
        if type(request) is InvocationRequest:return Push(InvocationFrame(request))
        if type(request) is ProtectedValueRequest:return Push(ProtectedValueFrame(request.node,request.env))
        from .compiled_flow_frames import flow_frame
        frame=flow_frame(request,cursor.owner)
        if frame is not None:return Push(frame)
        frame=control_frame(request)
        if frame is None:frame=bot_frame(request)
        if frame is None:frame=recovery_frame(request)
        if frame is None:frame=iteration_frame(request)
        if frame is None:frame=comprehension_frame(request)
        if frame is None:frame=context_frame(request)
        if frame is None:frame=operator_frame(request)
        if frame is None:frame=access_frame(request)
        if frame is None:frame=hash_collection_frame(request)
        if frame is None:frame=class_frame(request)
        if frame is None:frame=callable_frame(request)
        if frame is None:frame=pattern_frame(request)
        if frame is None:frame=module_frame(request)
        if frame is None:frame=suspension_frame(request)
        if frame is not None:return Push(frame)
        return External(request)
    def close(self,cursor):
        try:
            if type(cursor) in (PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor):cursor.original_call(self.iterator.close,_bridge=self)
            else:self.iterator.close()
        except BaseException as error:
            child=getattr(self,'pending_cursor',None)
            if type(child) in (GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor) and child.close_failure is not None:
                from .suspension import error_priority
                if error_priority(child.close_failure)>=error_priority(error):raise child.close_failure
            raise


def request_proxy(request):
    return (yield request)


def gateway(evaluator,request,*,normalized=False):
    active=getattr(evaluator,'_execution_cursor',None)
    if active is not None and not normalized:return request_proxy(request)
    if type(request) is EvaluationRequest:frame=EvaluationBoundaryFrame(request)
    elif type(request) is ProtectedValueRequest:frame=ProtectedValueFrame(request.node,request.env)
    elif type(request) is InvocationRequest:frame=InvocationFrame(request)
    else:
        frame=control_frame(request)
        if frame is None:frame=bot_frame(request)
        if frame is None:frame=recovery_frame(request)
        if frame is None:frame=iteration_frame(request)
        if frame is None:frame=comprehension_frame(request)
        if frame is None:frame=context_frame(request)
        if frame is None:frame=operator_frame(request)
        if frame is None:frame=access_frame(request)
        if frame is None:frame=hash_collection_frame(request)
        if frame is None:frame=class_frame(request)
        if frame is None:frame=callable_frame(request)
        if frame is None:frame=pattern_frame(request)
        if frame is None:frame=module_frame(request)
        if frame is None:frame=suspension_frame(request)
        if frame is None:raise RuntimeError('Unknown execution gateway request')
    cursor=ExecutionCursor(evaluator,frame)
    if normalized:link_observer(evaluator,cursor)
    return cursor


def _module_state_link(evaluator,cursor):
    state=getattr(evaluator,'_module_sync_state',None)
    if state is not None:
        from .execution_modules import LegacyModuleLoadFrame
        if type(state) is not LegacyModuleLoadFrame or state.owner is not evaluator:raise RuntimeError('Foreign legacy module state')
        state.attach(cursor)


def ensure_cursor(evaluator,iterator,*,observer=False):
    if type(iterator) is ExecutionCursor:
        if iterator.owner is not evaluator:raise RuntimeError('Execution cursor owner mismatch')
        _module_state_link(evaluator,iterator)
        return iterator
    cursor=ExecutionCursor(evaluator,ProtocolBridge(iterator))
    _module_state_link(evaluator,cursor)
    if observer:link_observer(evaluator,cursor)
    return cursor


def link_observer(evaluator,cursor):
    native_parent=getattr(evaluator,'_module_native_parent',None)
    if native_parent is not None:
        if type(native_parent) is not ProtocolBridge or native_parent.owner is not evaluator or getattr(native_parent,'pending_cursor',None) is not None:
            raise RuntimeError('Module observer leaf is foreign or already occupied')
        native_parent.pending_cursor=cursor;cursor.parent_bridge=native_parent
        # This target belongs to one native delegation edge, not every nested
        # cursor created while that delegate is being pulled.
        evaluator._module_native_parent=None
        return
    parent=getattr(evaluator,'_execution_cursor',None)
    if parent is not None and parent is not cursor and parent.frames and type(parent.frames[-1]) is ProtocolBridge:
        bridge=parent.frames[-1];bridge.pending_cursor=cursor;cursor.parent_bridge=bridge


def frame_roots(frame):
    """Declared current semantic roots, never Python generator reflection."""
    from .execution_continuations import CONTINUATION_FRAMES
    if type(frame)in CONTINUATION_FRAMES:return frame.roots()
    if type(frame) in BOT_FRAMES+RECOVERY_FRAMES+ITERATION_FRAMES+COMPREHENSION_FRAMES+CONTEXT_PROTOCOL_FRAMES+OPERATOR_FRAMES+ACCESS_FRAMES+HASH_COLLECTION_FRAMES+CLASS_FRAMES+CALLABLE_FRAMES+PATTERN_FRAMES+MODULE_FRAMES+SUSPENSION_FRAMES+MULTIPLE_VALUE_FRAMES+INVOCATION_DISPATCH_FRAMES+MODE_FRAMES:return frame.roots()
    if type(frame) is ProtocolBridge:
        child=getattr(frame,'pending_cursor',None)
        # A generic transport link replaces a formerly empty observer slot.
        # Retain that original charged None prefix; the exact new child is
        # audited separately as supplemental control state.
        return (None,child) if type(child) in (GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor) else (child,)
    if type(frame) is EvaluationBoundaryFrame:return (frame.env,frame.result,getattr(frame,'previous_module',None))
    if type(frame) is LoopFrame:return (frame.env,frame.items,frame.handle,frame.scope,frame.item,frame.operation)
    if type(frame) is LoopCompletionFrame:return (frame.env,frame.value)
    if type(frame) in (ProtectedValueFrame,TryFrame,FinallyFrame,TryCompletionFrame,GroupTryFrame,ContextFrame,PromptScopeFrame,CancelShieldFrame,TaskGroupFrame,GroupBodyFrame,ControlCallbackFrame,PromptHandlerFrame,PromptCallFrame,ControlBarrierFrame,DynamicWindFrame):return frame.roots()
    if type(frame) is OperandFrame:return (frame.env,frame.callee,frame.args,frame.kwargs,frame.result,frame.left,getattr(frame,'contract',None))
    if type(frame) is ReturnFrame:return (frame.invocation,frame.body.function,frame.body.scope)
    if type(frame) is InvocationFrame:
        from .capture_frames import Edge
        roots=tuple(getattr(frame,name,None) for name in ('fn','args','kwargs','bound_parameters','bindings','scope','value','handled_error','error_context','previous_error','previous_context','previous_module'))
        required=getattr(frame,'required',{})
        pending=getattr(frame,'pending',())
        roots+=tuple(required)+tuple(Edge(frame.owner,item[3],(item[0],*item[2],item[4],item[5],item[6])) for item in pending)
        return roots
    raise RuntimeError('Unknown execution frame root schema')


class ExecutionCursor:
    def __init__(self,owner,frame):
        self.owner=owner;frame.owner=owner
        if type(frame) in BOT_FRAMES+RECOVERY_FRAMES+ITERATION_FRAMES+COMPREHENSION_FRAMES+CONTEXT_PROTOCOL_FRAMES+OPERATOR_FRAMES+ACCESS_FRAMES+HASH_COLLECTION_FRAMES+CLASS_FRAMES+CALLABLE_FRAMES+PATTERN_FRAMES+MODULE_FRAMES+SUSPENSION_FRAMES+MULTIPLE_VALUE_FRAMES+INVOCATION_DISPATCH_FRAMES+MODE_FRAMES:
            from .agent_tools import current_caps
            frame.capabilities=current_caps(owner)
        self.frames=[frame];self.started=False;self.closed=False;self.parent_bridge=None
        self.root_bridge=frame if type(frame) is ProtocolBridge else None
    def __iter__(self):return self
    def __next__(self):return self.send(None)
    @property
    def gi_frame(self):
        # Trusted legacy GPU diagnostic only; never used by execution/capture.
        return getattr(self.root_bridge.iterator,'gi_frame',None) if self.root_bridge is not None else None
    def send(self,value):return self._advance(value,None)
    def throw(self,error,*args):
        if isinstance(error,type):
            value=args[0] if args else None
            error=value if isinstance(value,error) else error() if value is None else error(*value) if type(value) is tuple else error(value)
            if len(args)>1 and args[1] is not None:error=error.with_traceback(args[1])
        elif args:raise TypeError('An exception instance takes no separate throw value')
        return self._advance(None,error)
    def _advance(self,value,error):
        # Existing logical-cursor observation boundary. Transparent new native
        # transport shares the engine without reporting another logical resume.
        return self._advance_frames(value,error)

    def _advance_frames(self,value,error):
        if self.closed:
            if error is not None:raise error
            raise StopIteration
        if not self.started and value is not None:raise TypeError('Cannot send a value to an unstarted execution cursor')
        if not self.started and error is not None:
            self.frames.clear();self.closed=True;raise error
        self.started=True;e=self.owner;previous=getattr(e,'_execution_cursor',None);e._execution_cursor=self
        try:
            if getattr(self,'_compiled_flows',None):
                from .compiled_flow_frames import resumed
                error=resumed(self,error)
            while self.frames:
                frame=self.frames[-1]
                try:action=frame.step(self,value,error)
                except BaseException as failure:
                    self.frames.pop();value=None;error=failure;continue
                value=error=None
                if type(action) is Push:
                    action.frame.owner=e
                    if type(action.frame) in BOT_FRAMES+RECOVERY_FRAMES+ITERATION_FRAMES+COMPREHENSION_FRAMES+CONTEXT_PROTOCOL_FRAMES+OPERATOR_FRAMES+ACCESS_FRAMES+HASH_COLLECTION_FRAMES+CLASS_FRAMES+CALLABLE_FRAMES+PATTERN_FRAMES+MODULE_FRAMES+SUSPENSION_FRAMES+MULTIPLE_VALUE_FRAMES+INVOCATION_DISPATCH_FRAMES+MODE_FRAMES:
                        from .agent_tools import current_caps
                        action.frame.capabilities=current_caps(e)
                    boundary=getattr(action.frame,'boundary',None)
                    if type(boundary) is EvaluationBoundaryFrame and getattr(boundary,'flow_state',None) is not None:
                        boundary.flow_operation=action.frame
                    self.frames.append(action.frame)
                elif type(action) is Done:self.frames.pop();value=action.value
                elif type(action) is External:
                    if not action.native_leaf:
                        if getattr(self,'_compiled_flows',None):
                            from .compiled_flow_frames import paused
                            paused(self,action.request)
                        return action.request
                    # A single scheduler request is an opaque native leaf. Its
                    # parent frame owns every algorithm phase and semantic root.
                    bridge=ProtocolBridge(request_proxy(action.request))
                    bridge.owner=e;bridge.supplementary=True
                    self.frames.append(bridge)
                else:raise RuntimeError('Invalid execution-frame transition')
            self.closed=True
            if error is not None:raise error
            raise StopIteration(value)
        finally:
            e._execution_cursor=previous
            if self.closed and self.parent_bridge is not None and getattr(self.parent_bridge,'pending_cursor',None) is self:self.parent_bridge.pending_cursor=None
    def close(self):
        if self.closed:return
        previous=getattr(self.owner,'_execution_cursor',None);self.owner._execution_cursor=self
        failure=None
        try:
            while self.frames:
                frame=self.frames.pop()
                try:frame.close(self)
                except BaseException as error:
                    from .suspension import error_priority
                    if failure is None or error_priority(error)>=error_priority(failure):failure=error
            self.closed=True
            if failure is not None:raise failure
        finally:
            self.owner._execution_cursor=previous
            if self.parent_bridge is not None and getattr(self.parent_bridge,'pending_cursor',None) is self:self.parent_bridge.pending_cursor=None


class GenericCloseCursor(ExecutionCursor):
    """Opt-in typed generic driver with a reachable, retryable close frontier.

    Existing ExecutionCursor.close and its111 prior frame families are unchanged.
    A close yield is exposed before dispatching its InvocationRequest, exactly
    as the original generic generator's yield-from native protocol did.
    """
    def __init__(self, owner, frame):
        super().__init__(owner, frame)
        self.epoch=owner.suspension.epoch
        self.close_pending=False;self.close_suppressed=False
        self.pending_request=self.pending_authority=self.close_failure=None
    def _owned_epoch(self):
        from .language import LanguageError
        if type(self.epoch) is not int or self.epoch!=self.owner.suspension.epoch:
            raise LanguageError('Generic close cursor cannot cross owner or epoch',code='ownership_error')
    def _advance(self,value,error):
        self._owned_epoch()
        self.close_pending=False
        self.pending_request=self.pending_authority=self.close_failure=None
        return super()._advance(value,error)
    def roots(self):
        from .capture_frames import Edge
        from .language import LanguageError
        self._owned_epoch()
        if type(self.closed) is not bool or type(self.started) is not bool or type(self.close_pending) is not bool or type(self.close_suppressed) is not bool or type(self.frames) is not list:
            raise LanguageError('Malformed generic close cursor',code='ownership_error')
        authority=self.pending_authority
        if authority is not None and(type(authority) is not frozenset or any(type(x) is not str for x in authority)):
            raise LanguageError('Malformed generic close authority',code='ownership_error')
        request=self.pending_request
        values=()
        if type(request) is InvocationRequest:
            if type(request.args) not in (tuple,list) or request.kwargs is not None and(type(request.kwargs) is not dict or any(type(x) is not str for x in request.kwargs)) or type(request.options) is not dict or any(type(x) is not str for x in request.options):
                raise LanguageError('Malformed pending generic invocation',code='ownership_error')
            contracts=request.options.get('contracts',())
            continuations=request.options.get('continuations',())
            if type(contracts) not in(tuple,list) or type(continuations) not in(tuple,list) or any(type(row) not in(tuple,list) or len(row)!=2 for row in continuations):
                raise LanguageError('Malformed pending generic invocation options',code='ownership_error')
            values=(request.fn,*request.args,request.kwargs,request.options,*contracts,*tuple(row[0] for row in continuations))
        elif request is not None:
            values=(request,)
        return tuple(self.frames)+(Edge(self.owner,authority,values),self.close_failure)
    def _close_action(self,frame,action):
        # Only this selected algorithm opts into retryable close. The cold
        # protocol iterator is advanced once to its actual raw yield; its
        # InvocationRequest is not submitted and no method body is run here.
        from .agent_tools import current_caps
        while True:
            if type(action) is Done:
                self.frames.pop()
                return action
            if type(action) is not Push or type(action.frame) is not ProtocolBridge:
                raise RuntimeError('Invalid generic close transition')
            child=action.frame;child.owner=self.owner;self.frames.append(child)
            try:
                child.started=True
                request=child.iterator.send(None)
            except StopIteration as stop:
                self.frames.pop();action=frame.step(self,stop.value,None)
            except BaseException as failure:
                self.frames.pop();action=frame.step(self,None,failure)
            else:
                self.pending_request=request;self.pending_authority=current_caps(self.owner)
                self.close_pending=True
                return CloseYield(request)
    def close_request(self,error=None):
        from .execution_callables import GenericResolveFrame
        from .suspension import error_priority
        self._owned_epoch()
        if self.closed:return Done()
        signal=GeneratorExit() if error is None else error
        previous=getattr(self.owner,'_execution_cursor',None);self.owner._execution_cursor=self
        failure=None;delegated_error=None;completion=None
        self.close_pending=False;self.close_suppressed=False
        self.pending_request=self.pending_authority=self.close_failure=None
        try:
            while self.frames:
                frame=self.frames[-1]
                try:
                    if type(frame) is GenericResolveFrame:
                        # A delegate.close failure is the actual exception seen
                        # by the surrounding with, rather than a fresh GE.
                        action=frame.close_transition(self,delegated_error if delegated_error is not None else signal)
                        delegated_error=None
                        self.close_suppressed=frame.close_suppressed
                        result=self._close_action(frame,action)
                        if type(result) is CloseYield:
                            self.close_failure=failure
                            return result
                        completion=result.value
                    else:
                        self.frames.pop()
                        try:frame.close(self)
                        except BaseException as cause:
                            if type(frame) is ProtocolBridge and self.frames and type(self.frames[-1]) is GenericResolveFrame:
                                delegated_error=cause
                            else:raise
                except BaseException as cause:
                    if self.frames and self.frames[-1] is frame:
                        self.frames.pop()
                        if type(frame) is GenericResolveFrame:
                            try:frame.retire(cause)
                            except BaseException as cleanup:
                                if error_priority(cleanup)>=error_priority(cause):cause=cleanup
                    if failure is None or error_priority(cause)>=error_priority(failure):failure=cause
            self.closed=True
            self.close_failure=failure
            if failure is not None:raise failure
            return Done(completion)
        finally:
            self.owner._execution_cursor=previous
            if self.closed and self.parent_bridge is not None and getattr(self.parent_bridge,'pending_cursor',None) is self:
                self.parent_bridge.pending_cursor=None
    def close(self):
        action=self.close_request()
        if type(action) is CloseYield:
            if self.close_failure is not None:raise self.close_failure
            raise RuntimeError('generator ignored GeneratorExit')


class NativeAlgorithmCursor(ExecutionCursor):
    """Shared opt-in native-close continuation for explicit algorithms.

    It owns only selected algorithm state and cold native child bridges.
    Raw requests are interpreted by the original outer cursor, preserving the
    charged Evaluation/Invocation frame stack. Other close protocols are intact.
    """
    ALGORITHMS = ()
    LABEL = 'Pattern'

    def __init__(self, owner, frame):
        super().__init__(owner, frame)
        self.epoch = owner.suspension.epoch
        self.close_pending = False; self.close_completed = False
        self.pending_request = self.pending_authority = self.close_failure = None
        self.close_signal = None
        self.ambient_cursor = None
    def _owned_epoch(self):
        from .language import LanguageError
        if type(self.epoch) is not int or self.epoch != self.owner.suspension.epoch:
            raise LanguageError(self.LABEL + ' close cursor cannot cross owner or epoch',code='ownership_error')
    def _advance(self, value, error):
        self._owned_epoch()
        self.close_pending = self.close_completed = False
        self.pending_request = self.pending_authority = self.close_failure = self.close_signal = None
        ambient = self.ambient_cursor
        self.ambient_cursor = getattr(self.owner,'_execution_cursor',None)
        try:
            return self._advance_transport(value,error)
        finally:
            self.ambient_cursor = ambient

    def _advance_transport(self, value, error):
        # All pre-existing Pattern/Module transport keeps its historical public
        # logical-cursor observation path.
        return super()._advance(value,error)
    def original_call(self, function, *args, _bridge=None):
        previous = getattr(self.owner,'_execution_cursor',None)
        self.owner._execution_cursor = self.ambient_cursor
        prior_leaf=getattr(self.owner,'_module_native_parent',None)
        if type(self) is ModuleCloseCursor and _bridge is not None:
            self.owner._module_native_parent=_bridge if getattr(_bridge,'pending_cursor',None) is None else None
        try:
            return function(*args)
        finally:
            self.owner._execution_cursor = previous
            if type(self) is ModuleCloseCursor and _bridge is not None:self.owner._module_native_parent=prior_leaf
    def roots(self):
        from .capture_frames import Edge
        from .language import LanguageError, Node, Env
        self._owned_epoch()
        def malformed():
            raise LanguageError('Malformed ' + self.LABEL.lower() + ' close continuation',code='ownership_error')
        if any(type(getattr(self,name)) is not bool for name in ('started','closed','close_pending','close_completed')) or type(self.frames) is not list:
            malformed()
        ambient = self.ambient_cursor
        if ambient is not None and (type(ambient) not in (ExecutionCursor,GenericCloseCursor,PatternCloseCursor,ModuleCloseCursor,SuspensionCloseCursor,MultipleValuesCloseCursor,InvocationCloseCursor,ModeCloseCursor) or ambient is self or ambient.owner is not self.owner):
            malformed()
        # The temporary ambient pointer is validated metadata, never a root that
        # recursively captures the whole unrelated caller execution graph.
        authority = self.pending_authority
        if authority is not None and (type(authority) is not frozenset or any(type(x) is not str for x in authority)):
            malformed()

        if self.closed:
            if self.frames:
                malformed()
        elif not self.frames or len(self.frames) > 2 or type(self.frames[0]) not in self.ALGORITHMS or self.frames[0].owner is not self.owner:
            malformed()
        if len(self.frames) == 2:
            bridge = self.frames[1]
            if type(bridge) is not ProtocolBridge or bridge.owner is not self.owner or type(bridge.started) is not bool or getattr(bridge,'supplementary',None) is not True:
                malformed()
        request = self.pending_request
        values = ()
        from .compiled_flow_frames import FlowBodyRequest,flow_frame
        if type(request) is EvaluationRequest:
            if type(request.node) not in (tuple,Node) or not request.node or type(request.env) is not Env or type(request.tail) is not bool or type(request.options) is not dict or any(type(x) is not str for x in request.options):
                malformed()
            if 'all_results' in request.options and type(request.options['all_results']) is not bool:
                malformed()
            # Compiled-region handles remain opaque execution metadata under the
            # original EvaluationBoundary; the live language roots are explicit.
            values = (request.node,request.env)
        elif type(request)is FlowBodyRequest:
            frame=flow_frame(request,self.owner)
            if frame is None or type(frame.env)is not Env or type(frame.node)not in(tuple,Node)or not frame.node or type(frame.node[0])is not str or type(frame.tail)is not bool or type(frame.options)is not dict or any(type(x)is not str for x in frame.options):malformed()
            if 'all_results'in frame.options and type(frame.options['all_results'])is not bool:malformed()
            values=(frame.node,frame.env)
        elif type(request) is InvocationRequest:
            if type(request.args) not in (tuple,list) or request.kwargs is not None and (type(request.kwargs) is not dict or any(type(x) is not str for x in request.kwargs)) or type(request.options) is not dict or any(type(x) is not str for x in request.options):
                malformed()
            contracts = request.options.get('contracts',())
            continuations = request.options.get('continuations',())
            if type(contracts) not in (tuple,list) or type(continuations) not in (tuple,list) or any(type(row) not in (tuple,list) or len(row) != 2 for row in continuations):
                malformed()
            values = (request.fn,*request.args,request.kwargs,*contracts,*tuple(row[0] for row in continuations))
        elif type(request) is ProtectedValueRequest:
            if type(request.node) not in (tuple,Node) or not request.node or type(request.env) is not Env:
                malformed()
            values = (request.node,request.env)
        elif request is not None:
            values = (request,)
        if self.close_signal is not None and not isinstance(self.close_signal,BaseException):
            malformed()
        if self.close_failure is not None and not isinstance(self.close_failure,BaseException):
            malformed()
        return tuple(self.frames)+(Edge(self.owner,authority,values),self.close_failure,self.close_signal)
    def _close_action(self, frame, action):
        # Advance only native protocol transport. A returned raw request is
        # yielded by the public generator before any Evaluation/Invocation body.
        while True:
            if type(action) is Done:
                self.frames.pop()
                return action
            if type(action) is External:
                return self._close_external(frame, action)
            if type(action) is not Push or type(action.frame) is not ProtocolBridge:
                raise RuntimeError('Invalid ' + self.LABEL.lower() + ' close transition')
            child = action.frame; child.owner = self.owner
            self.frames.append(child)
            try:
                child.started = True
                request = self.original_call(child.iterator.send,None,_bridge=child)
            except StopIteration as stop:
                self.frames.pop(); action = frame.step(self,stop.value,None)
            except BaseException as failure:
                self.frames.pop(); action = frame.step(self,None,failure)
            else:
                self.pending_request = request
                # The evaluation scope is the original entry environment, not
                # an argument newly handed to a narrowed callback.
                self.pending_authority = frame.capabilities
                self.close_pending = True
                return CloseYield(request)
    def _close_external(self, frame, action):
        # Existing native algorithm families never yield direct requests during
        # close. An explicit family may opt in without copying this loop.
        raise RuntimeError('Invalid ' + self.LABEL.lower() + ' close transition')

    def close_request(self, error=None):

        self._owned_epoch()
        if self.closed:
            return Done()
        signal = GeneratorExit() if error is None else error
        previous = getattr(self.owner,'_execution_cursor',None)
        ambient = self.ambient_cursor
        self.ambient_cursor = previous
        self.owner._execution_cursor = self
        self.close_pending = self.close_completed = False
        self.pending_request = self.pending_authority = self.close_failure = None
        self.close_signal = signal
        delegated_error = None
        try:
            if self.frames and type(self.frames[-1]) is ProtocolBridge:
                child = self.frames.pop()
                try:
                    child.close(self)
                except BaseException as failure:
                    delegated_error = failure
            if not self.frames:
                self.closed = True
                if delegated_error is not None and not isinstance(delegated_error,GeneratorExit):
                    self.close_failure = delegated_error
                    raise delegated_error
                if delegated_error is not None:
                    self.close_signal = delegated_error
                return Done()
            frame = self.frames[-1]
            if type(frame) not in self.ALGORITHMS or len(self.frames) != 1 or type(frame.native_completed) is not bool:
                raise RuntimeError('Malformed ' + self.LABEL.lower() + ' close stack')
            try:
                if delegated_error is None and frame.native_completed:
                    # This marker was written only after the current native
                    # yield-from really completed. Consume once, then run the
                    # actual algorithm continuation (including atomic commit).
                    value = frame.native_result
                    frame.native_completed = False; frame.native_result = None
                    self.close_completed = True
                    action = frame.step(self,value,None)
                else:
                    self.close_signal = delegated_error if delegated_error is not None else signal
                    action = frame.step(self,None,self.close_signal)
                action = self._close_action(frame,action)
            except GeneratorExit as failure:
                self.close_signal = failure
                self.close_completed = False
                self.frames.clear(); self.closed = True
                return Done()
            except BaseException as failure:
                self.close_failure = failure
                self.frames.clear(); self.closed = True
                raise
            if type(action) is CloseYield:
                return action
            self.closed = True
            return action
        finally:
            self.owner._execution_cursor = previous
            self.ambient_cursor = ambient
            if self.closed and self.parent_bridge is not None and getattr(self.parent_bridge,'pending_cursor',None) is self:
                self.parent_bridge.pending_cursor = None
    def close(self):
        action = self.close_request()
        if type(action) is CloseYield:
            raise RuntimeError('generator ignored GeneratorExit')


class PatternCloseCursor(NativeAlgorithmCursor):
    """Original structural-syntax opt-in, using the shared native transport."""
    ALGORITHMS = PATTERN_FRAMES
    LABEL = 'Pattern'


class ModuleCloseCursor(NativeAlgorithmCursor):
    """Module algorithms share native completion without a copied close loop."""
    ALGORITHMS = tuple(cls for cls in MODULE_FRAMES if cls.__name__ != 'LegacyModuleLoadFrame')
    LABEL = 'Module'


class SuspensionCloseCursor(NativeAlgorithmCursor):
    """Exact opt-in native suspension frontier; not a new algorithm count."""
    ALGORITHMS = SUSPENSION_FRAMES
    LABEL = 'Suspension'


    def _advance_transport(self, value, error):
        # This new cursor is transparent protocol bookkeeping, not a second
        # old logical resume. The identical engine still forwards every raw
        # CHECKPOINT/request unchanged to the original parent cursor.
        return self._advance_frames(value,error)

class MultipleValuesCloseCursor(NativeAlgorithmCursor):
    """Two multiple-value algorithms, using the one native-close engine."""
    ALGORITHMS = MULTIPLE_VALUE_FRAMES
    LABEL = 'Multiple-value'

    def _advance_transport(self, value, error):
        return self._advance_frames(value,error)

    def close(self):
        if self.closed:
            return
        action = self.close_request()
        if type(action) is CloseYield:
            raise RuntimeError('generator ignored GeneratorExit')
        if type(action) is Done and self.close_completed:
            # The actual native child lawfully completed during close. Pass
            # that completion to the public native delegation boundary; Python
            # itself decides its generator.close return convention. An actual
            # GeneratorExit/ordinary throw never takes this path.
            raise StopIteration(action.value)


class InvocationCloseCursor(NativeAlgorithmCursor):
    """Dispatch coordination uses the shared native completion frontier."""
    ALGORITHMS = INVOCATION_DISPATCH_FRAMES
    LABEL = 'Invocation'

    def _retire_observer(self):
        if self.closed and self.parent_bridge is not None and getattr(self.parent_bridge, 'dispatch_cursor', None) is self:
            self.parent_bridge.dispatch_cursor = None

    def _advance(self, value, error):
        try:
            return super()._advance(value, error)
        finally:
            self._retire_observer()

    def close_request(self, error=None):
        try:
            return super().close_request(error)
        finally:
            self._retire_observer()

    def _close_external(self, frame, action):
        # Invoked for every shared-loop successor, including a synchronous
        # leaf reached after lawful native completion during close.
        self.pending_request = action.request
        self.pending_authority = frame.capabilities
        self.close_pending = True
        return CloseYield(action.request)

    def _advance_transport(self, value, error):
        return self._advance_frames(value, error)

    def close(self):
        if self.closed:
            return
        try:
            action = self.close_request()
            if type(action) is CloseYield:
                raise RuntimeError('generator ignored GeneratorExit')
            if type(action) is Done and self.close_completed:
                raise StopIteration(action.value)
        finally:
            self._retire_observer()


class EvaluationBoundaryFrame:
    def __init__(self,request):
        self.node,self.env,self.tail,self.options=request.node,request.env,request.tail,request.options
        self.phase='enter';self.capture=None;self.result=None;self.entered=False
    def step(self,cursor,value,error):
        from .language import MISSING,TailCall,LanguageError,ResourceLimit
        from .capture_frames import enter,leave
        e=cursor.owner
        if self.phase=='enter':
            self.capture=enter(e,self.node[0],self.env);self.result=MISSING
            if getattr(self,'flow_state',None) is not None:
                from .compiled_flow_frames import entered
                entered(self,cursor)
            self.debug_holder=self.capture.holder if self.capture is not None else None
            self.previous_debug=getattr(self.debug_holder,'debug_context',None)
            if self.debug_holder is not None:self.debug_holder.debug_context=(self.node,self.env)
            self.previous_span=e.current_span;self.runner=e.suspension.current
            self.previous_module=getattr(self.runner,'module_scope',None) if self.runner is not None else None
            if self.runner is not None:self.runner.module_scope=getattr(self.node,'_module_scope',None)
            e.current_span=getattr(self.node,'span',{}) or self.previous_span;self.entered=True;self.phase='instrument'
            from .suspension import CHECKPOINT
            if not self.options.get('_entry_checkpoint_done',False) and self.node[0] in ('block','while','for','async_for','for_pattern','async_for_pattern'):
                return External(CHECKPOINT)
        try:
            if error is not None:raise error
            if self.phase=='instrument':
                regions=self.options.get('_compiled_regions')
                if regions is not None and (not getattr(self,'flow_structural',False) or self.node[0] in ('call','let','var','list','tuple','record','binary','unary','do')):
                    handled,result=regions.try_region(self.node,self.env,e.current_span)
                    if handled:
                        if getattr(self,'flow_structural',False):
                            from .compiled_flow_operands import fast_finish
                            fast_finish(self)
                        self.result=result;self.finish(cursor);return Done(result)
                e.debug_checkpoint(self.node,self.env)
                if e.trace_handler:e.trace_handler({'event':'expression','kind':self.node[0],'location':dict(e.current_span),'step':e.steps})
                self.phase='result'
                if getattr(self,'flow_structural',False) and self.node[0] in ('break','continue'):
                    from .compiled_flow_frames import signal_leaf
                    return Push(ProtocolBridge(signal_leaf(self.flow_state,self.node)))
                if self.node[0] in ModuleSyntaxFrame.KINDS:return Push(ProtocolBridge(e._s_execute(self.node,self.env,self.tail,**{k:v for k,v in self.options.items() if k in ('all_results','_completion','_compiled_regions')})))
                if self.node[0] in CallableSyntaxFrame.KINDS:return Push(ProtocolBridge(request_proxy(CallableFrameRequest('syntax',(self.node,self.env)))))
                if getattr(self,'flow_structural',False) and self.node[0]=='assign':return Push(ProtocolBridge(request_proxy(ClassFrameRequest('compiled_assign',(self,)))))
                if self.node[0] in ClassSyntaxEvaluationFrame.KINDS:return Push(ProtocolBridge(request_proxy(ClassFrameRequest('syntax',(self.node,self.env,self.options.get('_compiled_regions'))))))
                if self.node[0] in SetLiteralEvaluationFrame.KINDS:return Push(ProtocolBridge(request_proxy(HashCollectionFrameRequest('literal',(self.node,self.env)))))
                if self.node[0] in ObjectAccessEvaluationFrame.KINDS:return Push(ProtocolBridge(request_proxy(AccessFrameRequest('evaluate',(self,)))))
                if self.node[0] in OperandFrame.KINDS:return Push(OperandFrame(self))
                if self.node[0] in LoopFrame.KINDS:return Push(LoopFrame(self))
                if self.node[0] in LoopCompletionFrame.KINDS:return Push(LoopCompletionFrame(self))
                if self.node[0] in TryFrame.KINDS:return Push(TryFrame(self))
                if self.node[0] in FinallyFrame.KINDS:return Push(FinallyFrame(self))
                if self.node[0] in TryCompletionFrame.KINDS:return Push(TryCompletionFrame(self))
                if self.node[0] in GroupTryFrame.KINDS:return Push(GroupTryFrame(self))
                if self.node[0] in ContextFrame.KINDS:return Push(ContextFrame(self))
                if getattr(self,'flow_structural',False)and self.node[0]in PatternSyntaxFrame.KINDS:
                    return Push(ProtocolBridge(e._s_execute(self.node,self.env,self.tail,all_results=self.options.get('all_results',False),_compiled_regions=self.options.get('_compiled_regions'),_flow_boundary=self)))
                return Push(ProtocolBridge(e._s_execute(self.node,self.env,self.tail,**{k:v for k,v in self.options.items() if k in ('all_results','_completion','_compiled_regions')})))
            from .robot_values import validate_embedded
            from .multiple_values import prepare,primary
            validate_embedded(e,value)
            from .bot_values import validate_embedded as validate_bot_result
            validate_bot_result(e,value);self.result=prepare(e,value)
            if not self.options.get('all_results',False) and not isinstance(self.result,TailCall):self.result=primary(self.result,e)
            self.finish(cursor);return Done(self.result)
        except BaseException as failure:
            if getattr(self,'flow_state',None) is not None:
                from .compiled_flow_frames import failure as flow_failure
                flow_failure(self,failure)
            if isinstance(failure,LanguageError):
                from .errors import observe
                failure=observe(e,failure).attach(getattr(self.node,'span',{}))
            elif isinstance(failure,RecursionError):failure=ResourceLimit('语法求值深度超过限制').attach(getattr(self.node,'span',{}))
            elif isinstance(failure,(ValueError,TypeError,KeyError,IndexError,ZeroDivisionError,OverflowError)):
                from .errors import observe
                failure=observe(e,LanguageError(str(failure),code=type(failure).__name__)).attach(getattr(self.node,'span',{}))
            self.result=failure;self.finish(cursor);raise failure
    def finish(self,cursor):
        if not self.entered:return
        self.entered=False
        from .capture_frames import leave
        e=cursor.owner;e.current_span=self.previous_span
        if self.debug_holder is not None:self.debug_holder.debug_context=self.previous_debug
        if self.runner is not None:self.runner.module_scope=self.previous_module
        try:leave(self.capture,self.result)
        finally:
            if getattr(self,'flow_state',None) is not None:
                from .compiled_flow_frames import leaving
                leaving(self,cursor)
    def close(self,cursor):self.finish(cursor)


class OperandFrame:
    KINDS={'block','call','list','tuple','record','if','binary','unary','let','var','return','do','literal'}
    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env;self.tail=boundary.tail;self.options=boundary.options
        self.phase='start';self.index=0;self.args=[];self.kwargs={};self.callee=None;self.result=None;self.left=None;self.selected_argument=None;self.record_key=None;self.operator=None
    def child(self,node,*,tail=False,all_results=False,regions=True):
        if hasattr(self,'_flow_operand_region'):
            from .compiled_flow_operands import child
            return child(self,node,tail=tail,all_results=all_results,regions=regions)
        options={'all_results':all_results}
        if regions and self.options.get('_compiled_regions') is not None:options['_compiled_regions']=self.options['_compiled_regions']
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,self.env,tail,options)))
    def keep(self,*values):
        from .capture_frames import retain
        retain(self.boundary.capture,*values)
    def constant_site(self,evaluator):
        from .portable_optimizer import LiteralPlan
        plan=getattr(evaluator,'_literal_plan',None)
        if getattr(evaluator,'optimize_literals',False) is True and type(plan) is LiteralPlan:
            return plan.site(self.node)
        return None
    def step(self,cursor,value,error):
        if getattr(self.boundary,'flow_structural',False):
            if self.node[0] in ('call','let','var','list','tuple','record','binary','unary','do'):
                from .compiled_flow_operands import step
            else:
                from .compiled_flow_frames import step
            return step(self,cursor,value,error)
        return self.advance(cursor,value,error)
    def advance(self,cursor,value,error):
        from .language import Env,Cell,TupleValue,TailCall,ReturnSignal,LanguageError,ResourceLimit,check_type,serializable
        from .capture_frames import reset
        e=cursor.owner;k=self.node[0];node=self.node
        if error is not None:raise error
        if self.phase=='start':
            e.tick()
            if k=='literal':serializable(node[1]);return Done(node[1])
            if k=='block':self.env=Env(self.env);reset(self.boundary.capture,self.env);self.phase='block_next'
            elif k=='call':self.phase='callee';return self.child(node[1],regions=False)
            elif k in ('list','tuple','record'):
                if len(node[1])>10000:raise ResourceLimit('列表超过 10000 项限制' if k!='record' else '记录超过 10000 字段限制')
                self.result={} if k=='record' else [];self.keep(self.result);self.phase='collection_next'
            elif k in ('if','binary','unary','let','var','return','do'):
                if k=='binary':self.operator=node[1]
                if k=='var':self.contract=e.contract(node[2],self.env)
                self.phase='first'
                child=node[1] if k in ('if','return','do') else node[2] if k in ('binary','unary') else node[3]
                return self.child(child,tail=self.tail if k=='do' else k=='return',all_results=self.options.get('all_results',False) if k=='do' else k=='return')
        if self.phase=='block_value':
            self.result=value;self.index+=1;reset(self.boundary.capture,self.env);self.phase='block_next'
        if self.phase=='block_next':
            if self.index>=len(node[1]):return Done(self.result)
            self.phase='block_value';last=self.index==len(node[1])-1
            return self.child(node[1][self.index],tail=self.tail and last,all_results=self.options.get('all_results',False) and last)
        if self.phase=='collection_value':
            if k=='record':self.result[self.record_key]=value
            else:self.result.append(value)
            self.index+=1;self.phase='collection_next'
        if self.phase=='collection_next':
            if self.index>=len(node[1]):return Done(TupleValue(self.result) if k=='tuple' else self.result)
            self.phase='collection_value'
            if k=='record':self.record_key=node[1][self.index][0]
            return self.child(node[1][self.index][1] if k=='record' else node[1][self.index],regions=False)
        if self.phase=='callee':self.callee=value;self.keep(self.callee,self.args,self.kwargs);self.phase='argument_next'
        if self.phase in ('argument_value','spread_value'):
            arg=self.selected_argument;kind=arg[0]
            if self.phase=='spread_value':self.args.extend(value)
            elif kind=='keyword':
                if arg[1] in self.kwargs:raise LanguageError('重复关键字参数 '+arg[1],code='argument_error')
                self.kwargs[arg[1]]=value
            elif kind=='kwspread':
                if not isinstance(value,dict):raise LanguageError('** 展开需要 Record',code='argument_error')
                if not all(isinstance(key,str) for key in value):raise LanguageError('关键字名称必须为 String',code='argument_error')
                if self.kwargs.keys() & value.keys():raise LanguageError('重复展开关键字参数',code='argument_error')
                if len(self.args)+len(self.kwargs)+len(value)>10000:raise ResourceLimit('调用参数超过 10000 项限制')
                self.kwargs.update(value)
            elif kind=='spread':
                from .iteration import IterationRequest
                self.phase='spread_value';self.keep(value)
                return Push(ProtocolBridge(e.suspension.iteration.resolve(IterationRequest('expand',value,10000-len(self.args)-len(self.kwargs)))))
            else:self.args.append(value)
            e.argument_budget(self.args,self.kwargs);self.index+=1;self.phase='argument_next'
        if self.phase=='argument_next':
            if self.index<len(node[2]):
                arg=node[2][self.index];self.selected_argument=arg;self.phase='argument_value'
                return self.child(arg[2] if arg[0]=='keyword' else arg[1] if arg[0] in ('spread','kwspread') else arg,regions=False)
            if self.tail:return Done(TailCall(self.callee,self.args,kwargs=self.kwargs,span=dict(e.current_span),handled_error=e.handled_error,error_context=e.error_context,module_scope=getattr(node,'_module_scope',None),primary_only=not self.options.get('all_results',False)))
            self.phase='done';return Push(InvocationFrame(InvocationRequest(self.callee,self.args,self.kwargs,{'all_results':self.options.get('all_results',False)})))
        if self.phase=='first':
            self.left=value;self.keep(value)
            if k=='if':self.phase='done';return self.child(node[2] if check_type(value,'Bool') else node[3],tail=self.tail,all_results=self.options.get('all_results',False))
            if k=='let':return Done(self.env.bind(node[1],check_type(value,e.contract(node[2],self.env))))
            if k=='var':value=check_type(value,self.contract);self.env.bind(node[1],Cell(value,self.contract));return Done(value)
            if k=='return':raise ReturnSignal(value)
            if k=='do':return Done(value)
            if k=='unary':
                if node[1]=='!':return Done(not check_type(value,'Bool'))
                from .operators import operation
                self.phase='done';return Push(ProtocolBridge(operation(e,{'-':'neg','+':'pos','~':'invert'}[node[1]],(value,),_constant_site=self.constant_site(e))))
            op=self.operator
            if op in ('&&','||'):
                truth=check_type(value,'Bool')
                if op=='&&' and not truth or op=='||' and truth:return Done(truth)
                self.phase='boolean';return self.child(node[3],tail=self.tail,all_results=True)
            self.phase='right';return self.child(node[3])
        if self.phase=='boolean':
            if isinstance(value,TailCall):value.contracts+=('Bool',);value.primary_only=True;return Done(value)
            from .multiple_values import primary
            return Done(check_type(primary(value,e),'Bool'))
        if self.phase=='right':
            self.keep(value);op=self.operator
            if op=='|>':
                if self.tail:return Done(TailCall(value,[self.left],span=dict(e.current_span),handled_error=e.handled_error,error_context=e.error_context,module_scope=getattr(node,'_module_scope',None),primary_only=not self.options.get('all_results',False)))
                self.phase='done';return Push(InvocationFrame(InvocationRequest(value,[self.left],options={'all_results':self.options.get('all_results',False)})))
            from .operators import operation
            self.phase='done';return Push(ProtocolBridge(operation(e,op,(self.left,value),_constant_site=self.constant_site(e))))
        if self.phase=='done':return Done(value)
        raise RuntimeError('Invalid operand-frame phase')
    def close(self,cursor):pass


class InvocationFrame:
    def __init__(self,request):
        from .language import MISSING
        self.fn,self.args,self.kwargs=request.fn,request.args,request.kwargs
        defaults={'span':None,'contracts':(),'continuations':(),'force':False,'synchronous':False,'bound_parameters':None,'handled_error':MISSING,'error_context':MISSING,'_recovery_callback':False,'_context_callback':False,'all_results':False,'primary_only':False}
        defaults.update(request.options)
        for key,value in defaults.items():setattr(self,key,value)
        self.phase='enter';self.entered=False;self.value=None;self.scope=None;self.bindings=None
    def step(self,cursor,value,error):
        from .language import LanguageError
        e=cursor.owner
        try:
            if self.phase=='enter':e._frame_invocation_enter(self);self.entered=True;self.phase='dispatch'
            if error is not None:raise error
            if self.phase=='dispatch':self.phase='dispatch_result';return Push(ProtocolBridge(e._frame_invocation_dispatch(self)))
            if self.phase=='dispatch_result':
                if type(value) is DispatchAgain:self.phase='dispatch';return self.step(cursor,None,None)
                if type(value) is BodyEvaluation:self.phase='result';return Push(ReturnFrame(self,value))
                if type(value) is not DispatchResult:raise RuntimeError('Invalid invocation dispatch')
                if value.cold:self.capture_result=value.value;self.finish(cursor);return Done(value.value)
                self.value=value.value;self.phase='result'
            elif self.phase=='result':self.value=value
            result=e._frame_invocation_reduce(self)
            if type(result) is DispatchAgain:self.phase='dispatch';return self.step(cursor,None,None)
            self.capture_result=result;self.finish(cursor);return Done(result)
        except BaseException as failure:
            if isinstance(failure,LanguageError):
                from .errors import observe
                failure=observe(e,failure).attach(self.call_span)
            elif isinstance(failure,(ValueError,TypeError,KeyError,IndexError,ZeroDivisionError,OverflowError)):
                from .errors import observe
                failure=observe(e,LanguageError(str(failure),code=type(failure).__name__)).attach(self.call_span)
            self.finish(cursor)
            e._frame_invocation_error(failure)
            raise failure
    def finish(self,cursor):
        if self.entered:
            self.entered=False
            cursor.owner._frame_invocation_leave(self)
    def close(self,cursor):self.finish(cursor)


class ReturnFrame:
    def __init__(self,invocation,body):self.invocation=invocation;self.body=body;self.phase='body'
    def step(self,cursor,value,error):
        from .language import ReturnSignal
        if self.phase=='body':
            self.phase='return';fn=self.body.function
            if fn.compiled_plan is not None:
                plan=fn.compiled_plan
                if plan.flow is not None:
                    self.invocation._continuation_compiled=True
                    from .compiled_flow_frames import body_frame
                    return Push(body_frame(plan,cursor.owner,fn,self.body.scope))
                if plan.requested=='interpreter':
                    plan.last_fallback=plan.reason;plan.interpreter_calls+=1
                    return Push(EvaluationBoundaryFrame(EvaluationRequest(fn.body,self.body.scope,True,{'all_results':True})))
                return Push(ProtocolBridge(plan.evaluate(cursor.owner,fn,self.body.scope)))
            return Push(EvaluationBoundaryFrame(EvaluationRequest(fn.body,self.body.scope,True,{'all_results':True})))
        if isinstance(error,ReturnSignal):value=error.value;error=None
        if error is not None:raise error
        return Done(value)
    def close(self,cursor):pass


class ModeCloseCursor(NativeAlgorithmCursor):
    """Mode state uses the one native-close engine and an auxiliary root edge."""
    ALGORITHMS = MODE_FRAMES
    LABEL = 'Mode'

    def roots(self):
        if self.parent_bridge is not None:
            from .language import LanguageError
            if type(self.parent_bridge) is not ProtocolBridge or self.parent_bridge.owner is not self.owner or getattr(self.parent_bridge, 'mode_cursor', None) is not self:
                raise LanguageError('Foreign mode observer', code='ownership_error')
        return super().roots()

    def _retire_observer(self):
        if self.closed and self.parent_bridge is not None and getattr(self.parent_bridge, 'mode_cursor', None) is self:
            self.parent_bridge.mode_cursor = None
            self.parent_bridge = None

    def _advance(self, value, error):
        try: return super()._advance(value, error)
        finally: self._retire_observer()

    def close_request(self, error=None):
        frame = self.frames[0] if self.frames else None
        try:
            action = super().close_request(error)
            if type(action) is Done and frame is not None and frame.phase == 'done':
                # Original parallel body catches BaseException and returns its
                # ScopeOutcome even when the incoming exception is GE. A real
                # normal completion is distinct from the shared engine's GE
                # unwind, which never advances that algorithm to 'done'.
                self.close_completed = True
            return action
        finally: self._retire_observer()

    def _advance_transport(self, value, error):
        return self._advance_frames(value, error)

    def _close_external(self, frame, action):
        self.pending_request = action.request
        self.pending_authority = frame.capabilities
        self.close_pending = True
        return CloseYield(action.request)

    def close(self):
        if self.closed: return
        try:
            action = self.close_request()
            if type(action) is CloseYield: raise RuntimeError('generator ignored GeneratorExit')
            if type(action) is Done and self.close_completed: raise StopIteration(action.value)
        finally: self._retire_observer()
