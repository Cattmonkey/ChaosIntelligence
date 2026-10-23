"""Authoritative loop control; user iterator protocols remain opaque bridges.

These are active, single-use execution frames, not saved continuations. Their
fields select the actual next condition, iterator step, binding and body.
"""


class LoopFrame:
    KINDS={'while','for','async_for','for_pattern','async_for_pattern'}

    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env
        self.options=boundary.options;self.kind=self.node[0];self.phase='start'
        self.items=None;self.handle=None;self.scope=None;self.item=None;self.operation=None
        self.asynchronous=self.kind in ('async_for','async_for_pattern')

    def child(self,node,env=None,*,regions=True):
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        options={}
        if regions and self.options.get('_compiled_regions') is not None:
            options['_compiled_regions']=self.options['_compiled_regions']
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,self.env if env is None else env,False,options)))

    def completed(self,state):
        outcome=self.options.get('_completion')
        if outcome is not None:outcome.state=state

    def reset(self,*values):
        from .capture_frames import reset
        # The loop owns these actual continuation operands until its next
        # iteration/retirement. Preserve saved module provenance even after
        # host authoring removes the incidental AST module marker.
        reset(self.boundary.capture,*values,self.scope,self.item,self.operation,
              getattr(self.boundary,'previous_module',None))

    def cancel_pending(self,evaluator):
        # Match the former try/except around precisely the anext Await. The
        # iterator itself remains borrowed and must not be closed here.
        operation=self.operation
        self.operation=None
        if operation is not None and operation.state not in ('completed','failed','cancelled','closed'):
            evaluator.suspension.cancel(operation)

    def step(self,cursor,value,error):
        if getattr(self.boundary,'flow_structural',False):
            from .compiled_flow_frames import step
            return step(self,cursor,value,error)
        from .execution_frames import Done,Push,External,ProtocolBridge
        from .language import Env,BreakSignal,ContinueSignal,check_type
        from .suspension import Await
        e=cursor.owner
        if self.phase=='while_body':
            # Only the body owns these handlers; condition/protocol/pattern
            # transfers must propagate to their original enclosing boundary.
            self.reset(self.env)
            if isinstance(error,BreakSignal):self.completed('broken');return Done(None)
            if error is not None and not isinstance(error,ContinueSignal):raise error
            self.phase='while_condition'
            return self.child(self.node[1])
        if self.phase=='for_body':
            self.scope=None;self.item=None
            if isinstance(error,BreakSignal):self.completed('broken');return Done(None)
            if error is not None and not isinstance(error,ContinueSignal):raise error
            self.phase='for_next'
        elif self.phase=='for_item_async':
            if error is not None:self.cancel_pending(e);raise error
            self.operation=None;self.phase='for_item'
        elif error is not None:raise error
        if self.phase=='start':
            e.tick()
            if self.kind=='while':
                self.phase='while_condition';return self.child(self.node[1])
            self.phase='for_iterable';return self.child(self.node[2],regions=False)
        if self.phase=='while_condition':
            if not check_type(value,'Bool'):self.completed('normal');return Done(None)
            e.tick();self.phase='while_body';return self.child(self.node[2])
        if self.phase=='for_iterable':
            self.items=value;self.phase='for_acquired'
            return Push(ProtocolBridge(e.suspension.iteration.acquire(self.items,self.asynchronous)))
        if self.phase=='for_acquired':
            self.handle=value;self.reset(self.env,self.items,self.handle);self.phase='for_next'
        if self.phase=='for_next':
            self.reset(self.env,self.items,self.handle)
            if self.asynchronous:
                self.operation=e.suspension.anext(self.handle);self.phase='for_item_async'
                return External(Await(self.operation))
            self.phase='for_item'
            return Push(ProtocolBridge(e.suspension.iteration.step(self.handle)))
        if self.phase=='for_item':
            if value['done']:self.completed('normal');return Done(None)
            e.tick();self.item=value['value'];self.scope=Env(self.env)
            if self.kind.endswith('_pattern'):e.bind_pattern(self.node[1],self.item,self.scope,lookup_env=self.env)
            else:self.scope.bind(self.node[1],self.item)
            self.reset(self.env,self.items,self.handle)
            self.phase='for_body';return self.child(self.node[3],self.scope)
        raise RuntimeError('Invalid loop-frame phase')

    def close(self,cursor):
        if self.phase=='while_body':self.reset(self.env)
        if self.phase=='for_item_async':self.cancel_pending(cursor.owner)
        self.scope=None;self.item=None


class LoopCompletionFrame:
    KINDS={'loop_else'}

    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env
        self.tail=boundary.tail;self.options=boundary.options;self.phase='start'
        self.outcome=None;self.value=None

    def child(self,node,*,completion=None):
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        options={'all_results':self.options.get('all_results',False)}
        if completion is not None:options['_completion']=completion
        if self.options.get('_compiled_regions') is not None:options['_compiled_regions']=self.options['_compiled_regions']
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,self.env,self.tail,options)))

    def step(self,cursor,value,error):
        if getattr(self.boundary,'flow_structural',False):
            from .compiled_flow_frames import step
            return step(self,cursor,value,error)
        from .execution_frames import Done
        from .language import LanguageError,_Completion
        if error is not None:raise error
        if self.phase=='start':
            cursor.owner.tick();node=self.node
            if len(node)!=3 or not isinstance(node[1],tuple) or not node[1] or node[1][0] not in ('while','for','async_for','for_pattern','async_for_pattern') or not isinstance(node[2],tuple) or not node[2] or node[2][0]!='block':
                raise LanguageError('正常完成分支需要对应控制结构和 else 块',code='syntax_error')
            self.outcome=_Completion();self.phase='loop';return self.child(node[1],completion=self.outcome)
        if self.phase=='loop':
            self.value=value
            if self.outcome.state=='normal':self.value=None;self.phase='else';return self.child(self.node[2])
            if self.outcome.state not in ('broken','handled'):raise LanguageError('无效内部完成状态')
            return Done(self.value)
        if self.phase=='else':return Done(value)
        raise RuntimeError('Invalid loop-completion-frame phase')

    def close(self,cursor):pass
