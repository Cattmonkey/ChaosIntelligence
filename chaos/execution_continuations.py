"""Typed frame images and composition on the existing authoritative cursor."""
from dataclasses import dataclass
from .continuations import fail,MAX_FRAMES,MAX_INVOCATIONS,MAX_IMAGES

# These are semantic fields of exact runtime frame classes, not an arbitrary
# object copier. Activation anchors are reconstructed against the current stack.
SCHEMAS={
 'EvaluationBoundaryFrame':('node env tail options phase result entered','capture previous_span runner previous_module debug_holder previous_debug owner'),
 'OperandFrame':('boundary node env tail options phase index args kwargs callee result left selected_argument record_key operator contract','owner'),
 'InvocationFrame':('fn args kwargs span contracts continuations force synchronous bound_parameters handled_error error_context _recovery_callback _context_callback all_results primary_only phase entered value scope bindings capture_result call_span required pending counted_depth','owner capture previous_span previous_error previous_context authority_runner previous_authority previous_module debug_frame restore_authority restore_module'),
 'ReturnFrame':('invocation body phase','owner'),
 'LoopFrame':('boundary node env options kind phase items handle scope item operation asynchronous','owner'),
 'LoopCompletionFrame':('boundary node env tail options phase outcome value','owner'),
 'ProtectedValueFrame':('node env phase value signal capabilities','owner'),
 'TryFrame':('boundary node env tail options phase error scope cases index case previous_error previous_context active','owner'),
 'FinallyFrame':('boundary node env options phase value pending_error previous_error previous_context active','owner'),
 'TryCompletionFrame':('boundary node env tail options phase outcome value','owner'),
 'ControlCallbackFrame':('function args authority phase result error authority_entered','owner authority_runner previous_authority'),
 'DynamicWindFrame':('before body after phase result pending_error error previous_error previous_context error_context_entered','owner'),
 'PromptCallFrame':('operation tag body handler phase args entered','owner manager escape signal result error'),
 'ContinuationDispatchRelayFrame':('dispatch','owner'),
 'InvocationDispatchFrame':('state phase native_stage program_validated item_validated effect epoch capabilities native_completed native_result','owner')}
ANCHORS=set('owner capture previous_span runner previous_module debug_holder previous_debug authority_runner previous_authority debug_frame'.split())


def frame_schemas():
    from .execution_frames import EvaluationBoundaryFrame,OperandFrame,InvocationFrame,ReturnFrame
    from .execution_loops import LoopFrame,LoopCompletionFrame
    from .execution_protection import ProtectedValueFrame,TryFrame,FinallyFrame,TryCompletionFrame
    from .execution_control import ControlCallbackFrame,DynamicWindFrame,PromptCallFrame
    from .execution_invocation import InvocationDispatchFrame
    types=(EvaluationBoundaryFrame,OperandFrame,InvocationFrame,ReturnFrame,LoopFrame,LoopCompletionFrame,ProtectedValueFrame,TryFrame,FinallyFrame,TryCompletionFrame,ControlCallbackFrame,DynamicWindFrame,PromptCallFrame,ContinuationDispatchRelayFrame,InvocationDispatchFrame)
    return {cls:SCHEMAS[cls.__name__]for cls in types}


def allowed(frame):
    name=type(frame).__name__
    schemas=frame_schemas()
    if type(frame)not in schemas:fail('Unreifiable frame: '+name)
    semantic,anchors=schemas[type(frame)];keys=set(semantic.split())|set(anchors.split())
    if set(vars(frame))-keys:fail('Unknown active frame metadata: '+name)
    if getattr(frame,'owner',None)is None:fail('Unowned continuation frame','ownership_error')
    if name=='EvaluationBoundaryFrame':
        if not frame.entered or frame.phase!='result' or any(k.startswith('_compiled')for k in frame.options):fail('Only entered interpreter boundaries are reifiable')
    if name=='InvocationFrame':
        if getattr(frame,'_continuation_compiled',False):fail('Compiled tail ancestry is a continuation barrier')
        if not frame.entered or frame.phase not in('result','dispatch_result'):fail('Invocation is not at a resumable result frontier')
        from .language import Function
        if type(frame.fn)is Function and (frame.fn.flavor!='fn' or frame.fn.compiled_plan is not None and frame.fn.compiled_plan.requested!='interpreter'):fail('Async, generator and compiled invocation are continuation barriers')
    if name=='LoopFrame'and(frame.kind!='while'or frame.handle is not None or frame.operation is not None):fail('Live iterator operation is a continuation barrier')
    if name in('FinallyFrame','DynamicWindFrame')and getattr(frame,'pending_error',None)is not None:fail('Pending unwind is a continuation barrier')
    if name=='DynamicWindFrame'and frame.phase!='body':fail('Wind transition is a continuation barrier')
    if name=='ProtectedValueFrame'and frame.signal is not None:fail('Pending protected return is a continuation barrier')
    if name=='PromptCallFrame'and(frame.operation!='prompt'or frame.phase!='body'):fail('Escape or prompt transition is a continuation barrier')


@dataclass(frozen=True)
class Special:
    kind:str
    values:tuple


@dataclass(frozen=True)
class Ref:
    kind:str
    index:int


class Image:
    def __init__(self,e,frames,tag,hole):
        from .execution_frames import ProtocolBridge,InvocationFrame,InvocationCloseCursor
        from .execution_invocation import InvocationDispatchFrame
        from .control_prompts import Request
        from .language import Function
        from .compilation_artifact import _syntax_hash
        if not 0<len(frames)<=MAX_FRAMES:fail('Continuation image frame quota','resource_limit')
        normalized=[]
        for frame in frames:
            if type(frame)is ProtocolBridge:
                dispatch=getattr(frame,'dispatch_cursor',None)
                if type(dispatch)is not InvocationCloseCursor or len(dispatch.frames)!=2 or type(dispatch.frames[0])is not InvocationDispatchFrame:
                    fail('Opaque native protocol is a continuation barrier')
                request=dispatch.frames[0].state.value
                if type(request)is not Request or request.operation not in('wind','prompt')or dispatch.frames[0].native_stage!=1:
                    fail('Opaque native protocol is a continuation barrier')
                actual=dispatch.frames[0]
                if actual.phase!='native' or actual.effect is not None or dispatch.close_signal is not None:fail('Native protocol is not at a control result frontier')
                relay=ContinuationDispatchRelayFrame(actual);relay.owner=e;normalized.append(relay)
            else:normalized.append(frame)
        self.tag=tag;self.invocations=0;self.rows=();self.containers=();self.roots=();self.definitions=();self.environments=();self.completions=()
        allframes=list(normalized)
        for frame in normalized:
            if type(frame)is ContinuationDispatchRelayFrame:allframes.append(frame.dispatch)
        positions={id(f):i for i,f in enumerate(allframes)}
        for f in allframes:
            if f.owner is not e:fail('Foreign active continuation frame','ownership_error')
            allowed(f)
        from .language import Env
        envs=[];env_ids={}
        def env(value):
            if type(value)is Env and id(value)not in env_ids:
                env_ids[id(value)]=len(envs);envs.append(value)
        for f in allframes:
            for key in('env','scope'):env(getattr(f,key,None))
            body=getattr(f,'body',None)
            if type(body).__name__=='BodyEvaluation':env(body.scope)
        own={};containers=[]
        def container(value):
            if type(value)in(list,dict)and id(value)not in own:own[id(value)]=len(containers);containers.append(value)
        for f in allframes:
            for key in('args','kwargs','options','required','pending','bindings'):
                if hasattr(f,key):container(getattr(f,key))
            if type(f).__name__=='OperandFrame'and type(f.result)in(list,dict):container(f.result)
            if hasattr(f,'capture')and f.capture is not None:container(f.capture.roots)
            for row in getattr(f,'pending',()):
                if type(row)is tuple:
                    for item in row:
                        if type(item)is dict:container(item)
        from .language import _Completion
        from .execution_frames import BodyEvaluation
        leaves=[];completion_ids={};completion_states=[]
        def encode(value):
            if id(value)in env_ids:return Ref('env',env_ids[id(value)])
            if id(value)in positions:return Ref('frame',positions[id(value)])
            if id(value)in own:return Ref('container',own[id(value)])
            if type(value)is _Completion:
                if id(value)not in completion_ids:completion_ids[id(value)]=len(completion_states);completion_states.append(value.state)
                return Ref('completion',completion_ids[id(value)])
            if type(value)is BodyEvaluation:return Special('body',(value.function,encode(value.scope)))
            if type(value)is tuple and any(id(x)in positions or id(x)in own for x in value):return Special('tuple',tuple(encode(x)for x in value))
            leaves.append(value);return value
        rows=[];definitions=[]
        for f in allframes:
            data={k:encode(v)for k,v in vars(f).items()if k in SCHEMAS[type(f).__name__][0].split()}
            if f is hole:data['phase']='result'
            cap=getattr(f,'capture',None)
            if cap is not None:data['_capture_roots']=encode(cap.roots);data['_capture_caps']=cap.capabilities
            rows.append((type(f),tuple(data.items())))
            fn=getattr(f,'fn',None)
            if type(fn)is Function:definitions.append((fn,_syntax_hash(fn.body)))
        frozen=[]
        for c in containers:
            frozen.append(('list',tuple(encode(v)for v in c))if type(c)is list else('dict',tuple((k,encode(v))for k,v in c.items())))
        self.rows=tuple(rows);self.stack_count=len(normalized);self.containers=tuple(frozen);self.definitions=tuple(definitions)
        self.environments=tuple((encode(v.parent),tuple((k,encode(x))for k,x in v.values.items()))for v in envs)
        self.roots=tuple(leaves);self.completions=tuple(completion_states)
        self._seal=(id(self.rows),id(self.containers),id(self.roots),id(self.definitions),id(self.environments),id(self.completions),self.stack_count)
        if len(self.roots)+len(self.rows)+len(self.containers)>20000:fail('Continuation image graph quota','resource_limit')
        for _ in self.rows:e.tick()
        self.validate(e)

    def validate(self,e):
        from .compilation_artifact import _syntax_hash
        if self._seal!=(id(self.rows),id(self.containers),id(self.roots),id(self.definitions),id(self.environments),id(self.completions),self.stack_count)or type(self.invocations)is not int or not 0<=self.invocations<=MAX_INVOCATIONS:fail('Malformed continuation image','ownership_error')
        if not 0<self.stack_count<=len(self.rows)<=MAX_FRAMES+MAX_FRAMES:fail('Malformed continuation image','ownership_error')
        for fn,seal in self.definitions:
            if _syntax_hash(fn.body)!=seal:fail('Captured function definition changed','definition_changed')

    def instantiate(self,e):
        self.validate(e)
        if self.invocations>=MAX_INVOCATIONS:fail('Continuation invocation quota','resource_limit')
        frames=[object.__new__(cls)for cls,data in self.rows]
        containers=[[]if kind=='list'else{}for kind,rows in self.containers]
        from .language import Env
        envs=[Env()for _ in self.environments]
        from .language import _Completion
        from .execution_frames import BodyEvaluation
        completions=[]
        for state in self.completions:
            outcome=_Completion();outcome.state=state;completions.append(outcome)
        def decode(value):
            if type(value)is Ref:return {'frame':frames,'container':containers,'env':envs,'completion':completions}[value.kind][value.index]
            if type(value)is Special:
                if value.kind=='completion':x=_Completion();x.state=value.values[0];return x
                if value.kind=='body':return BodyEvaluation(value.values[0],decode(value.values[1]))
                if value.kind=='tuple':return tuple(decode(x)for x in value.values)
            return value
        for target,(parent,items)in zip(envs,self.environments):
            target.parent=decode(parent);target.values.update((k,decode(v))for k,v in items)
        for target,(kind,rows)in zip(containers,self.containers):
            if kind=='list':target.extend(decode(x)for x in rows)
            else:target.update((k,decode(v))for k,v in rows)
        for f,(cls,rows)in zip(frames,self.rows):
            for k,v in rows:setattr(f,k,decode(v))
            f.owner=e
            if type(f).__name__=='ControlCallbackFrame':f.authority_runner=f.previous_authority=None
            if type(f).__name__=='PromptCallFrame':f.manager=f.escape=f.signal=f.result=f.error=None
        for _ in self.rows:e.tick()
        return frames[:self.stack_count]


class ContinuationDispatchRelayFrame:
    """Only a known ControlRequest carrier is replaced, at its exact result PC."""
    def __init__(self,dispatch):self.dispatch=dispatch
    def roots(self):return (self.dispatch,)
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push,External
        # Adapter is not a second cursor/driver; the original dispatch performs
        # one semantic transition and the original parent cursor handles action.
        class Ambient:
            owner=cursor.owner
            ambient_cursor=cursor
        return self.dispatch.step(Ambient(),value,error)
    def close(self,cursor):pass


class ContinuationCaptureFrame:
    def __init__(self,tag,receiver):self.tag=tag;self.receiver=receiver;self.phase='start';self.value=None
    def roots(self):return (self.tag,self.receiver,self.value)
    def step(self,cursor,value,error):
        from .execution_frames import InvocationFrame,Push,Done
        from .execution_control import PromptCallFrame,ControlCallbackFrame
        from .control_prompts import find
        from .control_prompts import active_state
        from .agent_tools import current_caps
        from .continuations import Continuation
        if error is not None:raise error
        if self.phase=='start':
            target=find(cursor.owner,self.tag,required=True);stack=cursor.frames
            if any(type(f)is ContinuationComposeFrame and f.phase in('before','before_wait','unwind','abort_handler')for f in stack):fail('Capture from a wind transition is a continuation barrier')
            start=next((i for i,f in enumerate(stack)if type(f)in(PromptCallFrame,ContinuationComposeFrame)and f.manager is target),None)
            if start is None:fail('Prompt is not on the authoritative cursor')
            end=next((i for i in range(len(stack)-1,start,-1)if type(stack[i])is InvocationFrame),None)
            if end is None:fail('Capture has no invocation result hole')
            e=cursor.owner;s=e.suspension;task=s.current.task if s.current else None
            if task is not None or s.stream is not None:fail('Task and stream continuation capture is not supported')
            old_epoch,count=getattr(s,'continuation_images',(s.epoch,0))
            if old_epoch!=s.epoch:count=0
            if count>=MAX_IMAGES:fail('Continuation image quota','resource_limit')
            image=Image(e,stack[start+1:end+1],self.tag,stack[end]);self.value=Continuation(e,s.epoch,active_state(s),task.recording_scope if task else None,'continuation-'+str(s.next_id()),image,current_caps(e))
            from .capture_graph import seal as capture
            capture(e,(self.value,),current_caps(e))
            s.continuation_images=(s.epoch,count+1);self.phase='receiver';return Push(ControlCallbackFrame(self.receiver,(self.value,),self.value.capabilities))
        return Done(value)
    def close(self,cursor):self.receiver=self.value=None


class ContinuationHoleFrame:
    def __init__(self,value):self.value=value
    def roots(self):return (self.value,)
    def step(self,cursor,value,error):
        from .execution_frames import Done
        if error is not None:raise error
        return Done(self.value)
    def close(self,cursor):self.value=None


def activate(e,frames):
    from .execution_frames import EvaluationBoundaryFrame,InvocationFrame
    from .execution_control import ControlCallbackFrame,PromptCallFrame
    from .execution_protection import TryFrame,FinallyFrame
    from .control_prompts import PromptScope,enter
    from .capture_frames import enter as capture_enter,holder
    from .conditions import intersection
    from .agent_tools import current_caps
    from .language import ResourceLimit
    current=holder(e)
    activated=[]
    try:
      for f in frames:
        activated.append(f)
        if type(f)is EvaluationBoundaryFrame:
            f.previous_span=e.current_span;f.runner=e.suspension.current;f.previous_module=getattr(f.runner,'module_scope',None)if f.runner else None
            f.debug_holder=current;f.previous_debug=getattr(current,'debug_context',None)
            f.capture=capture_enter(e,f.node[0],f.env)
            if f.capture is not None:
                f.capture.roots=list(f._capture_roots);f.capture.identities={id(x)for x in f.capture.roots};f.capture.capabilities=intersection(f._capture_caps,current_caps(e))
            if current is not None:current.debug_context=(f.node,f.env)
            if f.runner is not None:f.runner.module_scope=getattr(f.node,'_module_scope',None)
            e.current_span=getattr(f.node,'span',{})or f.previous_span
        elif type(f)is InvocationFrame:
            f.previous_span=e.current_span;f.previous_error=e.handled_error;f.previous_context=e.error_context
            from .language import MISSING
            if f.handled_error is not MISSING:e.handled_error=f.handled_error
            if f.error_context is not MISSING:e.error_context=f.error_context
            f.authority_runner=e.suspension.current;f.previous_authority=current_caps(e);f.previous_module=getattr(f.authority_runner,'module_scope',None)if f.authority_runner else None
            if f.authority_runner is not None:f.authority_runner.capabilities=intersection(current_caps(e),getattr(f.fn,'capabilities',None))
            f.debug_frame=None;f.capture=capture_enter(e,'invoke',f.fn,f.args,f.kwargs,f.bound_parameters)
            if f.capture is not None:
                f.capture.roots=list(f._capture_roots);f.capture.identities={id(x)for x in f.capture.roots};f.capture.capabilities=intersection(f._capture_caps,current_caps(e))
            if f.counted_depth:e.depth+=1
            e.current_span=f.call_span
        elif type(f)is ControlCallbackFrame:
            f.authority_runner=e.suspension.current;f.previous_authority=current_caps(e)
            if f.authority_runner is not None:f.authority_runner.capabilities=intersection(current_caps(e),f.authority)
        elif type(f)is TryFrame and f.active:
            f.previous_error,f.previous_context=e.handled_error,e.error_context
            e.handled_error=e.error_context=f.error
        elif type(f)is PromptCallFrame:
            f.manager=PromptScope(e,f.tag,f.handler);f.escape=f.signal=f.result=f.error=None;enter(e,f.manager)
        if hasattr(f,'_capture_roots'):del f._capture_roots
        if hasattr(f,'_capture_caps'):del f._capture_caps
    except BaseException:
      for f in reversed(activated):
        if hasattr(f,'close'):f.close(type('ActivationOwner',(),{'owner':e})())
      raise


def preflight(e,frames,root_pending=True):
    from .execution_frames import InvocationFrame
    from .execution_control import PromptCallFrame
    from .control_prompts import active_state,MAX_DEPTH,MAX_CREATED
    from .language import ResourceLimit
    if e.depth+sum(type(f)is InvocationFrame and f.counted_depth for f in frames)>120:raise ResourceLimit('Continuation invocation exceeds depth120')
    scopes=int(root_pending)+sum(type(f)is PromptCallFrame for f in frames)
    if len(active_state(e.suspension).stack)+scopes>MAX_DEPTH or len(e.suspension.control_scopes)+scopes>MAX_CREATED:raise ResourceLimit('Continuation prompt activation quota')


class ContinuationComposeFrame:
    def __init__(self,value,completion):
        self.continuation=value;self.completion=completion;self.phase='start'
        self.manager=None;self.frames=None;self.winds=[];self.entered_winds=[]
        self.index=0;self.pending_error=None;self.previous_authority=None
        self.previous_error=self.previous_context=None;self.context_entered=False
    def roots(self):
        return (self.continuation,self.completion,self.manager,self.pending_error,*(self.frames or ()),*self.winds,*self.entered_winds)
    def release(self,e):
        from .control_prompts import release
        if self.manager is not None:release(self.manager);self.manager=None
        runner=e.suspension.current
        if runner is not None and self.phase!='start':runner.capabilities=self.previous_authority
        if self.context_entered:
            self.context_entered=False;e.handled_error=e.error_context=None
            e.handled_error,e.error_context=self.previous_error,self.previous_context
    def finish_error(self,e,error):
        from .control_prompts import PromptTransfer
        from .execution_frames import Push
        from .execution_control import PromptHandlerFrame
        target=self.manager
        self.release(e)
        if isinstance(error,PromptTransfer)and error.target is target and target is not None:
            self.phase='abort_handler'
            return Push(PromptHandlerFrame(target,None,error))
        raise error
    def unwind(self,e,error):
        from .suspension import hard_error
        from .language import LanguageError
        from .execution_frames import Push
        from .execution_control import ControlCallbackFrame
        self.pending_error=error
        if hard_error(error):return self.finish_error(e,error)
        if not self.context_entered:
            self.previous_error,self.previous_context=e.handled_error,e.error_context
            self.context_entered=True
        # Each outer cleanup observes the actual replacing error, while the
        # original caller context is still saved and restored exactly once.
        if isinstance(error,LanguageError):e.handled_error=e.error_context=error
        self.phase='unwind'
        if self.entered_winds:
            wind=self.entered_winds.pop()
            return Push(ControlCallbackFrame(wind.after,authority=self.continuation.capabilities))
        return self.finish_error(e,error)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .execution_control import ControlCallbackFrame,DynamicWindFrame
        from .control_prompts import PromptScope,enter
        from .continuations import owned
        from .agent_tools import current_caps
        from .conditions import intersection
        e=cursor.owner
        if self.phase=='running':
            if error is not None:return self.finish_error(e,error)
            self.release(e);self.phase='done';return Done(value)
        if self.phase=='abort_handler':
            if error is not None:raise error
            self.phase='done';return Done(value)
        if self.phase=='unwind':return self.unwind(e,error if error is not None else self.pending_error)
        if error is not None:return self.unwind(e,error)
        try:
            if self.phase=='start':
                owned(e,self.continuation)
                from .capture_graph import seal
                ceiling=intersection(current_caps(e),self.continuation.capabilities)
                seal(e,(self.continuation,self.completion),ceiling)
                self.frames=self.continuation.image.instantiate(e)
                preflight(e,self.frames)
                self.winds=[f for f in self.frames if type(f)is DynamicWindFrame]
                self.previous_authority=current_caps(e)
                if e.suspension.current is not None:e.suspension.current.capabilities=ceiling
                self.manager=PromptScope(e,self.continuation.image.tag);enter(e,self.manager);self.phase='before'
            elif self.phase=='before_wait':
                self.entered_winds.append(self.winds[self.index-1]);self.phase='before'
            if self.phase=='before':
                if self.index<len(self.winds):
                    f=self.winds[self.index];self.index+=1;self.phase='before_wait'
                    return Push(ControlCallbackFrame(f.before,authority=self.continuation.capabilities))
                self.continuation.image.validate(e);preflight(e,self.frames,False)
                activate(e,self.frames);self.continuation.image.invocations+=1
                self.entered_winds=[];self.phase='running'
                cursor.frames.extend(self.frames)
                # Ownership moved to the actual cursor. Retired clones must not
                # survive as stale supplemental roots of this controller.
                self.frames=None;self.winds=[]
                return Push(ContinuationHoleFrame(self.completion))
            fail('Invalid continuation composition phase','ownership_error')
        except BaseException as failure:return self.unwind(e,failure)
    def close(self,cursor):self.release(cursor.owner);self.frames=None


CONTINUATION_FRAMES=(ContinuationCaptureFrame,ContinuationComposeFrame,ContinuationHoleFrame,ContinuationDispatchRelayFrame)

def continuation_frame(request):
    from .control_prompts import Request
    if type(request)is not Request:return None
    if request.operation=='capture_continuation':return ContinuationCaptureFrame(*request.values)
    if request.operation=='compose_continuation':return ContinuationComposeFrame(*request.values)
    return None
