"""Actual comprehension traversal and clause scopes on the original cursor."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ComprehensionRequest:
    operation: str
    node: object
    env: object
    outer: object = None

class ComprehensionState:
    def __init__(self,e,node,env,outer):
        from .set_runtime import Builder
        self.owner=e;self.node,self.env,self.outer=node,env,outer
        self.lazy=node[0] in ('gen_comp','async_gen_comp');self.record=node[0]=='record_comp'
        self.builder=Builder(e) if node[0]=='set_comp' else None
        self.result={} if self.record else [];self.entries=0
        from .agent_tools import current_caps
        self.capabilities=current_caps(e)
    def roots(self):return (self.env,self.outer,self.builder,self.result)

class ComprehensionFrame:
    capabilities=None
    def __init__(self,node,env,outer=None,operation='evaluate'):
        self.node,self.env,self.outer,self.operation=node,env,outer,operation
        self.phase='start';self.state=self.captured_env=self.result=self.error=None
    def roots(self):
        from .capture_frames import Edge
        return (Edge(self.owner,self.capabilities,(self.env,self.outer,self.state,self.captured_env,self.result,self.error)),)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,EvaluationBoundaryFrame,EvaluationRequest,request_proxy
        from .language import ReturnSignal,BreakSignal,ContinueSignal,LanguageError
        e=cursor.owner
        if self.phase=='start' and error is not None:raise error
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='start':
                if self.operation=='evaluate':self.phase='outer';return Push(EvaluationBoundaryFrame(EvaluationRequest(self.node[-1][0][2],self.env)))
                self.state=ComprehensionState(e,self.node,self.env,self.outer);self.phase='walk';return Push(ComprehensionWalkFrame(self.state,0,self.env))
            if self.phase=='outer':
                self.outer=value
                if self.node[0] in ('gen_comp','async_gen_comp'):
                    self.captured_env=self.env.snapshot()
                    iterator=request_proxy(ComprehensionRequest('traverse',self.node,self.captured_env,self.outer))
                    self.result=e.suspension.generator(iterator,asynchronous=self.node[0]=='async_gen_comp',capture_roots=(self.captured_env,self.outer))
                    return Done(self.result)
                self.phase='traverse';return Push(ComprehensionFrame(self.node,self.env,self.outer,'traverse'))
            if self.phase=='traverse':self.result=value;return Done(self.result)
            self.result=self.state.builder.finish() if self.state.builder is not None else self.state.result if not self.state.lazy else None
            return Done(self.result)
        except (ReturnSignal,BreakSignal,ContinueSignal):
            raise LanguageError('控制流不能越过推导式边界',code='syntax_error').attach(getattr(self.node,'span',{})) from None
    def close(self,cursor):pass

class ComprehensionWalkFrame:
    capabilities=None
    def __init__(self,state,index,scope):
        self.state,self.index,self.scope=state,index,scope;self.phase='start'
        self.source=self.handle=self.item=self.child=self.key=self.value=self.operation=self.error=None
        self.capture=None;self.entered=False;self.consumed=0;self.asynchronous=False
    def roots(self):
        from .capture_frames import Edge
        from .conditions import intersection
        from .execution_iteration import _caps
        return (Edge(self.owner,self.capabilities,(self.state,self.scope,self.source,self.child,self.key,self.value,self.capture,self.error)),
                Edge(self.owner,intersection(self.capabilities,_caps(self.handle)),(self.handle,self.item,self.operation)))
    def retire(self):
        if self.entered:
            from .capture_frames import leave
            from .language import MISSING
            self.entered=False;leave(self.capture,MISSING)
    def cancel(self,e):
        if self.phase=='await' and self.operation is not None and self.operation.state not in ('completed','failed','cancelled','closed'):e.suspension.cancel(self.operation)
    def child_eval(self,node):
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,self.scope)))
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,External,ProtocolBridge
        from .execution_iteration import IterationAcquireFrame,IterationStepFrame
        from .capture_frames import enter,retain,reset
        from .comprehensions import FOR_KINDS
        from .language import Env,ResourceLimit,check_type
        from .suspension import CHECKPOINT,Await,Yield
        e=cursor.owner;s=self.state;node=s.node
        try:
            if error is not None:self.error=error;self.cancel(e);raise error
            if self.phase=='start':
                self.capture=enter(e,'comprehension_clause',self.scope,s.result,s.outer,s.builder);self.entered=True;e.tick()
                if self.index==len(node[-1]):
                    if not s.lazy and s.entries>=10000:raise ResourceLimit('推导式结果超过 10000 项限制')
                    if s.record:self.phase='key';return self.child_eval(node[1])
                    self.phase='value';return self.child_eval(node[1])
                self.clause=node[-1][self.index]
                if self.clause[0]=='comp_if':self.phase='filter';return self.child_eval(self.clause[1])
                if self.index==0:self.source=s.outer;self.phase='acquiring'
                else:self.phase='source';return self.child_eval(self.clause[2])
            elif self.phase=='key':self.key=check_type(value,'String');self.phase='record_value';return self.child_eval(node[2])
            elif self.phase=='record_value':
                self.value=value;s.result[self.key]=self.value;s.entries+=1;self.retire();return Done(None)
            elif self.phase=='value':
                self.value=value
                if s.lazy:self.phase='yield';return External(Yield(self.value))
                if s.builder is not None:
                    self.phase='add';bridge=ProtocolBridge(s.builder.add(self.value));bridge.supplementary=True;return Push(bridge)
                s.result.append(self.value);s.entries+=1;self.retire();return Done(None)
            elif self.phase in ('yield','add'):s.entries+=1;self.retire();return Done(None)
            elif self.phase=='filter':
                if check_type(value,'Bool'):self.phase='filtered';return Push(ComprehensionWalkFrame(s,self.index+1,self.scope))
                self.retire();return Done(None)
            elif self.phase=='filtered':self.retire();return Done(None)
            elif self.phase=='source':self.source=value;self.phase='acquiring'
            elif self.phase=='acquired':
                self.handle=value;retain(self.capture,self.source,self.handle);self.consumed=0;self.phase='next'
            elif self.phase=='body':self.phase='next'
            elif self.phase in ('item','await'):
                self.item=value
                if self.item['done']:self.retire();return Done(None)
                if self.consumed>=10000:raise ResourceLimit('推导式输入超过 10000 项限制')
                self.consumed+=1;self.child=Env(self.scope)
                if self.clause[0].endswith('_pattern'):e.bind_pattern(self.clause[1],self.item['value'],self.child,lookup_env=self.scope)
                else:self.child.bind(self.clause[1],self.item['value'])
                self.phase='body';return Push(ComprehensionWalkFrame(s,self.index+1,self.child))
            elif self.phase=='checkpoint':
                e.tick()
                if self.asynchronous:
                    self.operation=e.suspension.anext(self.handle);self.phase='await';return External(Await(self.operation))
                self.phase='item';return Push(IterationStepFrame(self.handle))
            if self.phase=='acquiring':
                self.asynchronous=self.clause[0] in ('comp_async_for','comp_async_for_pattern');self.phase='acquired'
                return Push(IterationAcquireFrame(self.source,self.asynchronous,temporary=True))
            if self.phase=='next':
                reset(self.capture,self.scope,s.result,s.outer,self.source,self.handle,s.builder)
                self.phase='checkpoint';return External(CHECKPOINT)
            raise RuntimeError('Invalid comprehension clause phase')
        except BaseException:self.retire();raise
    def close(self,cursor):
        try:self.cancel(cursor.owner)
        finally:self.retire()

COMPREHENSION_FRAMES=(ComprehensionFrame,ComprehensionWalkFrame)

def comprehension_frame(request):
    if type(request) is ComprehensionRequest:return ComprehensionFrame(request.node,request.env,request.outer,request.operation)
    return None
