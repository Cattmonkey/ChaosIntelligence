"""Actual iterator and stream protocol phases on the existing execution cursor."""
from dataclasses import dataclass

@dataclass(frozen=True)
class IterationFrameRequest:
    operation: str
    values: tuple

def _caps(value):
    from .language import Function,Native
    from .iteration import Iterator,AsyncIterator
    from .suspension import Generator,AsyncGenerator
    from .agent_tools import AgentTool,BoundAgent
    from .team_bindings import BoundTeam
    from .session_tools import BoundSession
    from .bot_values import Bot
    from .objects import ChaosClass,ChaosInstance,BoundMethod,StaticMethod
    from .control_prompts import EscapeContinuation
    from .continuations import Continuation
    from .device_values import Device
    from .computer_values import Computer
    from .robot_values import Robot
    if type(value) in (Function,Native,Iterator,AsyncIterator,Generator,AsyncGenerator,AgentTool,BoundAgent,BoundTeam,BoundSession,Bot,ChaosClass,ChaosInstance,BoundMethod,Device,Computer,Robot,StaticMethod,EscapeContinuation,Continuation):return getattr(value,'capabilities',None)
    return None

class _IterationFrame:
    ROOTS=()
    capabilities=None
    def ceiling(self,name):
        from .conditions import intersection
        return self.capabilities
    def roots(self):
        from .capture_frames import Edge
        roots=[];saved=[]
        for name in self.ROOTS:
            value=getattr(self,name,None)
            values=tuple(value) if type(value) is tuple else (value,)
            if name in ('authority_runner','previous_authority','previous_team','saved_frames','saved_error','saved_context','saved_stream','saved_span'):
                saved.extend(values)
            else:roots.append(Edge(self.owner,self.ceiling(name),values))
        return tuple(roots)+(Edge(self.owner,getattr(self,'previous_authority',self.capabilities),tuple(saved)),)
    def enter_authority(self,e,handle):
        from .conditions import intersection
        self.authority_runner=e.suspension.current;self.previous_authority=self.previous_team=None
        if self.authority_runner is not None:
            self.previous_authority=self.authority_runner.capabilities;self.previous_team=self.authority_runner.team_scope
            self.authority_runner.capabilities=intersection(self.previous_authority,handle.capabilities)
            if handle.team_scope is not None:self.authority_runner.team_scope=handle.team_scope
        self.authority_entered=True
    def release_authority(self):
        if getattr(self,'authority_entered',False):
            self.authority_entered=False
            if self.authority_runner is not None:
                self.authority_runner.team_scope=self.previous_team;self.authority_runner.capabilities=self.previous_authority
    def leaf(self,iterator):
        from .execution_frames import Push,ProtocolBridge
        bridge=ProtocolBridge(iterator);bridge.supplementary=True
        return Push(bridge)
    def close(self,cursor):self.release_authority()

class IterationAcquireFrame(_IterationFrame):
    ROOTS=('value','values','handle','result','error')
    def __init__(self,value,asynchronous=False,temporary=False):
        self.value,self.asynchronous,self.temporary=value,asynchronous,temporary
        self.phase='start';self.values=self.handle=self.result=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .iteration import Iterator,HANDLES
        from .suspension import Generator
        from .channels import Channel
        from .hash_maps import HashMap,items as map_items
        from .set_values import SetValue
        from .set_runtime import items as set_items
        from .dynamic_contexts import Context,items as context_items
        from .tuple_values import TupleValue
        from .objects import ChaosInstance
        from .language import LanguageError,ResourceLimit,serializable
        e=cursor.owner;it=e.suspension.iteration
        if error is not None:self.error=error;raise error
        if self.phase=='factory':self.result=value;return Done(it.direct(self.result,self.asynchronous))
        e.tick();source=self.value
        if type(source) is HashMap and not self.asynchronous:self.values=map_items(e,source,'keys')
        elif type(source) is SetValue and not self.asynchronous:self.values=set_items(e,source)
        elif type(source) is Context and not self.asynchronous:return Done(it._register(values=context_items(e,source,'keys')))
        elif isinstance(source,HANDLES+(Generator,Channel)):return Done(it.direct(source,self.asynchronous))
        elif not self.asynchronous and (isinstance(source,(list,str,TupleValue)) or type(source) is SetValue):
            if (isinstance(source,(list,TupleValue)) or type(source) is SetValue) and len(source)>10000:raise ResourceLimit('iter 超过 10000 项')
            if isinstance(source,str):serializable(source)
            self.values=list(source) if isinstance(source,(list,TupleValue)) or type(source) is SetValue else source
        else:
            if not isinstance(source,ChaosInstance):raise LanguageError('aiter 需要异步可迭代值' if self.asynchronous else 'iter 需要 List、Tuple、String 或可迭代对象',code='argument_error')
            name='__aiter__' if self.asynchronous else '__iter__'
            if it._slot(source,name) is None:raise LanguageError('迭代对象缺少 '+name,code='argument_error')
            self.phase='factory';return Push(IterationInvokeFrame(source,name,[]))
        self.handle=Iterator(e.suspension,values=self.values) if self.temporary else it._register(values=self.values)
        return Done(self.handle)

class IterationInvokeFrame(_IterationFrame):
    ROOTS=('target','args','function','result','error')
    def __init__(self,target,name,args):
        self.target,self.name,self.args=target,name,args;self.phase='start';self.function=self.result=self.error=None
    def ceiling(self,name):
        from .conditions import intersection
        ceiling=super().ceiling(name)
        return intersection(ceiling,_caps(self.function)) if name in ('args','result') else ceiling
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,ProtocolBridge,InvocationFrame,InvocationRequest
        from .objects import special_method
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            e.tick();self.phase='access';return self.leaf(e._s_access_result(special_method(self.target,self.name,e)))
        if self.phase=='access':
            self.function=value;self.phase='invoke';return Push(InvocationFrame(InvocationRequest(self.function,self.args)))
        self.result=value;return Done(self.result)

class IterationAwaitFrame(_IterationFrame):
    ROOTS=('task','result','error')
    def __init__(self,task):self.task=task;self.phase='start';self.result=self.error=None;self.entered=False
    def cancel(self,e):
        from .suspension import TERMINAL
        if self.entered and self.task.state not in TERMINAL:e.suspension.cancel(self.task)
    def step(self,cursor,value,error):
        from .execution_frames import External,Done
        from .suspension import Task,Await
        from .multiple_values import primary,prepare
        e=cursor.owner
        if self.phase=='start':
            if error is not None:raise error
            e.suspension.validate(self.task,Task);self.entered=True;self.phase='await';return External(Await(self.task))
        try:
            if error is not None:self.error=error;raise error
            self.result=primary(prepare(e,value),e);return Done(self.result)
        except BaseException:self.cancel(e);raise
    def close(self,cursor):self.cancel(cursor.owner)

class IterationStepFrame(_IterationFrame):
    def ceiling(self,name):
        from .conditions import intersection
        return intersection(self.capabilities,_caps(self.handle))
    ROOTS=('value','sent','injected','handle','args','result','operation','error','authority_runner','previous_authority','previous_team')
    def __init__(self,value,sent=None,error=None,asynchronous=False,locked=False,scoped=True):
        self.value,self.sent,self.injected=value,sent,error;self.asynchronous,self.locked,self.scoped=asynchronous,locked,scoped
        self.phase='start';self.handle=self.args=self.result=self.operation=self.error=None;self.running=False;self.authority_entered=False
    def retire(self):
        if self.running:
            self.running=False
            if not self.locked:self.handle.busy=False
        self.release_authority()
    def failed(self,error):
        from .language import ResourceLimit,ExecutionControl
        if self.running:
            if isinstance(error,(ResourceLimit,ExecutionControl,GeneratorExit)):self.handle.state,self.handle.result,self.handle.close_attempted='cancelled',None,True
            else:self.handle.state,self.handle.result='failed',None
    def step(self,cursor,value,error):
        from .execution_frames import Push,External,Done
        from .suspension import Generator,TERMINAL,Await
        from .channels import Channel,receive
        from .language import LanguageError,Raised
        e=cursor.owner;it=e.suspension.iteration
        try:
            if error is not None:
                self.error=error
                if self.phase=='channel' and self.operation.state not in TERMINAL:e.suspension.cancel(self.operation)
                raise error
            if self.phase in ('generator','channel'):self.result=value;self.retire();return Done(self.result)
            if self.phase=='invoke':
                self.result=value
                if self.asynchronous:self.phase='await';return Push(IterationAwaitFrame(self.result))
                self.phase='record'
            elif self.phase=='await':self.result=value;self.phase='record'
            if self.phase=='record':
                self.result=it._record(self.result);self.phase='finish'
            if self.phase=='finish':
                self.handle.state='completed' if self.result['done'] else 'suspended'
                if self.result['done']:self.handle.result=self.result['value']
                self.retire();return Done(self.result)
            self.handle=it.direct(self.value,self.asynchronous)
            if self.scoped:
                self.enter_authority(e,self.handle);self.handle=it.direct(self.handle,self.asynchronous)
            if isinstance(self.handle,Generator):
                self.phase='generator';return Push(StreamResumeFrame(self.handle,self.sent,self.injected,self.locked))
            e.tick()
            if self.handle.busy and not self.locked:raise LanguageError('迭代器不能重入',code='argument_error')
            if self.handle.state in TERMINAL:
                if self.injected is not None:raise self.injected
                self.result={'done':True,'value':self.handle.result};self.retire();return Done(self.result)
            if type(self.handle.target) is Channel:
                if self.injected is not None:raise self.injected
                if self.sent is not None:raise LanguageError('Channel 迭代器只接受 null',code='argument_error')
                self.operation=receive(e,self.handle.target,cursor=self.handle,locked=self.locked);self.phase='channel'
                return External(Await(self.operation))
            prefix='__a' if self.asynchronous else '__'
            if self.injected is not None:
                if not isinstance(self.injected,Raised):raise self.injected
                name,self.args=prefix+'throw__',[self.injected.value]
            elif self.sent is not None:name,self.args=prefix+'send__',[self.sent]
            else:name,self.args=prefix+'next__',[]
            if (self.injected is not None or self.sent is not None) and (self.handle.target is None or it._slot(self.handle.target,name) is None):
                if self.injected is not None:raise self.injected
                raise LanguageError('迭代器不支持非 null send',code='argument_error')
            if not self.locked:self.handle.busy=True
            self.handle.state='running';self.running=True
            if self.handle.target is None:
                if self.handle.index>=len(self.handle.values):self.result={'done':True,'value':None}
                else:self.result={'done':False,'value':self.handle.values[self.handle.index]};self.handle.index+=1
                self.phase='finish';return self.step(cursor,None,None)
            self.phase='invoke';return Push(IterationInvokeFrame(self.handle.target,name,self.args))
        except BaseException as failure:self.failed(failure);self.retire();raise
    def close(self,cursor):
        from .suspension import TERMINAL
        if self.phase=='channel' and self.operation is not None and self.operation.state not in TERMINAL:cursor.owner.suspension.cancel(self.operation)
        self.failed(GeneratorExit());self.retire()

class IterationCloseFrame(_IterationFrame):
    def ceiling(self,name):
        from .conditions import intersection
        return intersection(self.capabilities,_caps(self.handle))
    ROOTS=('value','handle','result','error','authority_runner','previous_authority','previous_team')
    def __init__(self,value,asynchronous=False,scoped=True):
        self.value,self.asynchronous,self.scoped=value,asynchronous,scoped;self.phase='start'
        self.handle=self.result=self.error=None;self.closing=False;self.authority_entered=False
    def retire(self):
        if self.closing:self.closing=False;self.handle.state,self.handle.busy='closed',False
        self.release_authority()
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .suspension import Generator
        from .channels import Channel,owned as channel_owned
        from .language import LanguageError,check_type
        e=cursor.owner;it=e.suspension.iteration
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='generator':self.retire();return Done(value)
            if self.phase=='invoke':
                self.result=value
                if self.asynchronous:self.phase='await';return Push(IterationAwaitFrame(self.result))
                self.phase='checked'
            elif self.phase=='await':self.result=value;self.phase='checked'
            if self.phase=='checked':check_type(self.result,'Null');self.retire();return Done(None)
            self.handle=it.direct(self.value,self.asynchronous)
            if self.scoped:self.enter_authority(e,self.handle);self.handle=it.direct(self.handle,self.asynchronous)
            if isinstance(self.handle,Generator):self.phase='generator';return Push(StreamCloseFrame(self.handle))
            if self.handle.busy:raise LanguageError('迭代器不能重入',code='argument_error')
            if self.handle.close_attempted:self.retire();return Done(None)
            self.handle.close_attempted,self.handle.busy=True,True;self.closing=True;e.tick()
            if type(self.handle.target) is Channel:channel_owned(self.handle.target,e);self.retire();return Done(None)
            name='__aclose__' if self.asynchronous else '__close__'
            if self.handle.target is not None and it._slot(self.handle.target,name) is not None:
                self.phase='invoke';return Push(IterationInvokeFrame(self.handle.target,name,[]))
            self.retire();return Done(None)
        except BaseException:self.retire();raise
    def close(self,cursor):self.retire()

class IterationCollectFrame(_IterationFrame):
    ROOTS=('value','handle','values','item','error')
    def __init__(self,value,asynchronous=False):
        self.value,self.asynchronous=value,asynchronous;self.phase='start';self.handle=self.item=self.error=None;self.values=[];self.locked=False
    def retire(self):
        if self.locked:
            self.locked=False;self.handle.busy=False
            self.handle.owner._retire_execution(self.handle)
    def failed(self,error):
        from .language import ResourceLimit,ExecutionControl
        from .iteration import HANDLES
        if self.locked and isinstance(error,(ResourceLimit,ExecutionControl,GeneratorExit)) and isinstance(self.handle,HANDLES):self.handle.state,self.handle.result,self.handle.close_attempted='cancelled',None,True
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .language import LanguageError,ResourceLimit
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='start':self.phase='acquire';return Push(IterationAcquireFrame(self.value,self.asynchronous))
            if self.phase=='acquire':
                self.handle=value
                if self.handle.busy:raise LanguageError('迭代器不能重入',code='argument_error')
                self.handle.busy=True;self.locked=True
            else:
                self.item=value
                if self.item['done']:self.retire();return Done(self.values)
                if len(self.values)>=10000:raise ResourceLimit('acollect/collect 超过 10000 项')
                self.values.append(self.item['value'])
            self.phase='step';return Push(IterationStepFrame(self.handle,asynchronous=self.asynchronous,locked=True))
        except BaseException as failure:self.failed(failure);self.retire();raise
    def close(self,cursor):self.failed(GeneratorExit());self.retire()

class IterationConsumeFrame(_IterationFrame):
    ROOTS=('value','callback','initial','handle','values','result','item','arguments','error')
    def __init__(self,value,operation,callback=None,initial=None,limit=10000):
        self.value,self.operation,self.callback,self.initial,self.limit=value,operation,callback,initial,limit
        self.phase='start';self.handle=self.item=self.arguments=self.error=None;self.values=[];self.result=initial;self.consumed=0;self.locked=False;self.callback_called=False
        self.message='调用参数超过 10000 项限制' if operation=='expand' else operation+' 超过 10000 项'
    def ceiling(self,name):
        from .conditions import intersection
        ceiling=super().ceiling(name)
        return intersection(ceiling,_caps(self.callback)) if (name=='arguments' or name=='result' and self.callback_called) and self.operation!='set' else ceiling
    def retire(self):
        if self.locked:
            self.locked=False;self.handle.busy=False
            self.handle.owner._retire_execution(self.handle)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,ProtocolBridge,InvocationFrame,InvocationRequest
        from .language import ResourceLimit,LanguageError,check_type
        from .tuple_values import TupleValue
        from .set_values import SetValue
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='start':
                if (isinstance(self.value,(list,TupleValue)) or type(self.value) is SetValue) and len(self.value)>self.limit:raise ResourceLimit(self.message)
                self.phase='acquire';return Push(IterationAcquireFrame(self.value,temporary=True))
            if self.phase=='acquire':
                self.handle=value
                if self.handle.busy:raise LanguageError('迭代器不能重入',code='argument_error')
                self.handle.busy=True;self.locked=True;self.phase='next'
            elif self.phase=='callback':
                self.result=value
                if self.operation=='map':self.values.append(self.result)
                elif self.operation=='filter':
                    if check_type(self.result,'Bool'):self.values.append(self.item['value'])
                elif self.operation in ('any','all'):
                    truth=check_type(self.result,'Bool')
                    if truth==(self.operation=='any'):self.retire();return Done(truth)
                self.phase='next'
            elif self.phase=='add':self.phase='next'
            elif self.phase=='step':
                self.item=value
                if self.item['done']:
                    result=self.result if self.operation in ('set','fold') else self.operation=='all' if self.operation in ('any','all') else self.values
                    self.retire();return Done(result)
                if self.consumed>=self.limit:raise ResourceLimit(self.message)
                self.consumed+=1
                if self.operation=='set':self.phase='add';return self.leaf(self.result.add(self.item['value']))
                if self.operation=='expand':self.values.append(self.item['value']);self.phase='next'
                else:
                    self.arguments=[self.result,self.item['value']] if self.operation=='fold' else [self.item['value']]
                    self.callback_called=True;self.phase='callback';return Push(InvocationFrame(InvocationRequest(self.callback,self.arguments)))
            self.phase='step';return Push(IterationStepFrame(self.handle,locked=True))
        except BaseException:self.retire();raise
    def close(self,cursor):self.retire()

class IterationDelegateFrame(_IterationFrame):
    ROOTS=('value','handle','sent','injected','item','error','closing_error')
    def __init__(self,value,asynchronous=False):
        self.value,self.asynchronous=value,asynchronous;self.phase='start';self.handle=self.sent=self.injected=self.item=self.error=self.closing_error=None;self.use_async=False
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,External
        from .suspension import AsyncGenerator,Generator,Yield,GeneratorClose
        from .iteration import Iterator,AsyncIterator
        from .channels import Channel
        from .objects import ChaosInstance
        from .language import LanguageError,ResourceLimit,ExecutionControl,Raised
        from .tuple_values import TupleValue
        from .set_values import SetValue
        it=cursor.owner.suspension.iteration
        if self.phase=='closing':
            if error is not None:raise error
            raise self.closing_error
        if self.phase=='yield':
            self.sent,self.injected=None,None
            try:
                if error is not None:raise error
                self.sent=value
                if isinstance(self.handle,Iterator) and self.handle.target is None and self.sent is not None:
                    raise LanguageError(('Set' if type(self.value) is SetValue else 'Tuple' if type(self.value) is TupleValue else 'List/String')+' 委托只接受 null')
            except GeneratorClose as closing:
                self.closing_error=closing;self.phase='closing';return Push(IterationCloseFrame(self.handle,self.use_async))
            except (ResourceLimit,ExecutionControl):raise
            except LanguageError as injected:
                if isinstance(self.handle,Generator) or isinstance(injected,Raised):self.injected=injected
                else:raise
            self.phase='step';return Push(IterationStepFrame(self.handle,self.sent,self.injected,self.use_async))
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            self.use_async=self.asynchronous and (isinstance(self.value,(AsyncIterator,AsyncGenerator,Channel)) or isinstance(self.value,ChaosInstance) and it._slot(self.value,'__aiter__') is not None)
            self.phase='acquire';return Push(IterationAcquireFrame(self.value,self.use_async))
        if self.phase=='acquire':self.handle=value;self.phase='step';return Push(IterationStepFrame(self.handle,self.sent,self.injected,self.use_async))
        self.item=value
        if self.item['done']:return Done(self.handle.result if isinstance(self.handle,Generator) else self.item['value'])
        self.sent,self.injected=None,None;self.phase='yield';return External(Yield(self.item['value']))

class IterationResolveFrame(_IterationFrame):
    ROOTS=('request','handle','callback','kwargs','span','builder','result','error')
    def __init__(self,request):
        self.request=request;self.operation=request.operation;self.handle=request.handle;self.phase='start';self.callback=self.kwargs=self.span=self.builder=self.result=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .suspension import Generator,AsyncGenerator
        from .iteration import raised_value
        from .tuple_values import TupleValue
        from .language import TailCall
        e=cursor.owner;s=e.suspension;r=self.request;op=self.operation
        if error is not None:self.error=error;raise error
        if self.phase!='start':
            self.result=value
            if op=='set':return Done(self.builder.finish())
            if op=='tuple':return Done(TupleValue(self.result))
            if op=='apply':return Done(TailCall(self.callback,self.result,kwargs=self.kwargs,span=self.span))
            return Done(self.result)
        if isinstance(self.handle,Generator) and not isinstance(self.handle,AsyncGenerator):
            if op=='next':return Done(s.next(self.handle))
            if op=='send':return Done(s.send(self.handle,r.value))
            if op=='throw':return Done(s.throw_into(self.handle,r.value))
        self.phase='result'
        if op=='set':
            from .set_runtime import Builder
            from .capture_frames import active,retain
            self.builder=Builder(e);retain(active(e),self.builder)
            return Push(IterationConsumeFrame(self.handle,'set',initial=self.builder))
        if op=='tuple':return Push(IterationConsumeFrame(self.handle,'expand'))
        if op=='expand':return Push(IterationConsumeFrame(self.handle,'expand',limit=r.value))
        if op=='apply':
            self.callback,self.kwargs,self.span=r.value;return Push(IterationConsumeFrame(self.handle,'expand',limit=10000-len(self.kwargs)))
        if op=='fold':
            callback,initial=r.value;return Push(IterationConsumeFrame(self.handle,'fold',callback,initial))
        if op in ('map','filter','any','all'):return Push(IterationConsumeFrame(self.handle,op,r.value))
        if op=='iter':return Push(IterationAcquireFrame(self.handle))
        if op=='aiter':return Push(IterationAcquireFrame(self.handle,True))
        if op=='close':return Push(IterationCloseFrame(self.handle))
        if op=='collect':return Push(IterationCollectFrame(self.handle))
        injected=raised_value(e,e.copy_value(r.value)) if op=='throw' else None
        return Push(IterationStepFrame(self.handle,r.value if op=='send' else None,injected))

class StreamResumeFrame(_IterationFrame):
    def ceiling(self,name):
        from .conditions import intersection
        return intersection(self.capabilities,_caps(self.handle))
    ROOTS=('handle','sent','injected','context','runner','request','result','error','authority_runner','previous_authority','previous_team','saved_frames','saved_span','saved_error','saved_context','saved_stream')
    def __init__(self,handle,value=None,error=None,locked=False,context=None):
        self.handle,self.sent,self.injected,self.locked,self.context=handle,value,error,locked,context;self.phase='start'
        self.runner=self.request=self.result=self.error=None;self.entered=False;self.authority_entered=False
        self.saved_frames=self.saved_span=self.saved_error=self.saved_context=self.saved_stream=None;self.saved_depth=0
    def retire(self,e):
        if self.entered:
            self.entered=False;s=e.suspension;s._save(self.runner)
            e.frames,e.depth,e.current_span,e.handled_error,e.error_context,s.stream=self.saved_frames,self.saved_depth,self.saved_span,self.saved_error,self.saved_context,self.saved_stream
            self.release_authority()
            if not self.locked:self.handle.busy=False
            s._retire_execution(self.handle)
    def failed(self,e,error):
        from .language import ResourceLimit,ExecutionControl
        if isinstance(error,(ResourceLimit,ExecutionControl,GeneratorExit)):
            self.handle.state='cancelled';e.suspension._abort_stream(self.handle);e.suspension.event('generator.cancelled',self.handle);return error
        from .errors import observe
        try:observe(e,error)
        except BaseException as observed:error=observed
        self.handle.state='cancelled' if isinstance(error,(ResourceLimit,ExecutionControl)) else 'failed'
        e.suspension._abort_stream(self.handle);e.suspension.event('generator.'+self.handle.state,self.handle);return error
    def step(self,cursor,value,error):
        from .execution_frames import Done,External
        from .suspension import Generator,AsyncGenerator,GeneratorClose,TERMINAL,Yield
        from .language import LanguageError
        from .multiple_values import primary,prepare
        e=cursor.owner;s=e.suspension
        if self.phase=='start':
            if error is not None:raise error
            s.validate(self.handle,AsyncGenerator if isinstance(self.handle,AsyncGenerator) else Generator)
            if self.handle.context_manager is not None and self.handle.context_manager is not self.context:raise LanguageError('生成器已由上下文管理器独占',code='context_error')
            if self.handle.busy and not self.locked:raise LanguageError('生成器不能重入',code='argument_error')
            if self.handle.state in TERMINAL:
                if self.injected is not None and not isinstance(self.injected,GeneratorClose):raise self.injected
                return Done({'done':True,'value':primary(prepare(e,self.handle.result),e)})
            if self.handle.state=='created' and self.sent is not None and self.injected is None:raise LanguageError('未启动生成器只能发送 null',code='argument_error')
            if not self.locked:self.handle.busy=True
            self.runner=self.handle.runner
            self.saved_frames,self.saved_depth,self.saved_span,self.saved_error,self.saved_context,self.saved_stream=e.frames,e.depth,e.current_span,e.handled_error,e.error_context,s.stream
            self.enter_authority(e,self.handle);self.runner.task=s.current.task if s.current else None
            for frame in self.runner.frames:frame['taskId']=self.runner.task.id if self.runner.task else None
            s._restore(self.runner);s.stream=self.handle;self.handle.state='running';self.entered=True;self.phase='advance'
        try:
            if self.phase=='external':
                if isinstance(error,GeneratorExit):raise error
                self.injected,self.sent=error,value if error is None else None
            elif error is not None:raise error
            while True:
                self.runner.started=True
                try:self.request=self.runner.iterator.throw(self.injected) if self.injected is not None else self.runner.iterator.send(self.sent)
                except StopIteration as stop:
                    self.handle.state,self.handle.result='completed',stop.value;s.event('generator.completed',self.handle)
                    self.result={'done':True,'value':primary(prepare(e,stop.value),e)};self.retire(e);return Done(self.result)
                except GeneratorClose:
                    self.handle.state='closed';self.result={'done':True,'value':None};self.retire(e);return Done(self.result)
                self.injected,self.sent=None,None
                if isinstance(self.request,Yield):
                    from .cancellation import check_yield
                    check_yield(s,self.handle);self.handle.state='suspended';s.event('generator.yielded',self.handle)
                    self.result={'done':False,'value':self.request.value};self.retire(e);return Done(self.result)
                self.phase='external';return External(self.request)
        except BaseException as failure:
            try:failure=self.failed(e,failure)
            finally:self.retire(e)
            raise failure
    def close(self,cursor):
        if self.entered:
            try:self.failed(cursor.owner,GeneratorExit())
            finally:self.retire(cursor.owner)

class StreamCloseFrame(_IterationFrame):
    ROOTS=('handle','context','item','error')
    def __init__(self,handle,context=None):self.handle,self.context=handle,context;self.phase='start';self.item=self.error=None;self.closing=False
    def retire(self,e):
        if self.closing:
            self.closing=False;e.suspension._abort_stream(self.handle);self.handle.state,self.handle.busy='closed',False
            try:e.suspension.event('generator.closed',self.handle)
            finally:e.suspension._retire_execution(self.handle)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .suspension import Generator,AsyncGenerator,TERMINAL,GeneratorClose
        from .language import LanguageError
        e=cursor.owner;s=e.suspension
        if self.phase=='start':
            if error is not None:raise error
            s.validate(self.handle,AsyncGenerator if isinstance(self.handle,AsyncGenerator) else Generator)
            if self.handle.context_manager is not None and self.handle.context_manager is not self.context:raise LanguageError('生成器已由上下文管理器独占',code='context_error')
            if self.handle.busy:raise LanguageError('生成器不能重入',code='argument_error')
            if self.handle.state in TERMINAL:return Done(None)
            if self.handle.state=='created':
                self.handle.runner.iterator.close();self.handle.state='closed'
                try:s.event('generator.closed',self.handle)
                finally:s._retire_execution(self.handle)
                return Done(None)
            self.closing=True;self.phase='resume';return Push(StreamResumeFrame(self.handle,error=GeneratorClose(),context=self.context))
        try:
            if error is not None:self.error=error;raise error
            self.item=value
            if not self.item['done']:raise LanguageError('关闭生成器期间不能 yield')
        finally:self.retire(e)
        return Done(None)
    def close(self,cursor):self.retire(cursor.owner)

ITERATION_FRAMES=(IterationAcquireFrame,IterationInvokeFrame,IterationAwaitFrame,IterationStepFrame,IterationCloseFrame,IterationCollectFrame,IterationConsumeFrame,IterationDelegateFrame,IterationResolveFrame,StreamResumeFrame,StreamCloseFrame)

def iteration_frame(request):
    from .iteration import IterationRequest
    if type(request) is IterationRequest:return IterationResolveFrame(request)
    if type(request) is IterationFrameRequest:
        types={'acquire':IterationAcquireFrame,'invoke':IterationInvokeFrame,'await':IterationAwaitFrame,'step':IterationStepFrame,'close':IterationCloseFrame,'collect':IterationCollectFrame,'consume':IterationConsumeFrame,'delegate':IterationDelegateFrame,'resume':StreamResumeFrame,'stream_close':StreamCloseFrame}
        return types[request.operation](*request.values)
    return None
