"""Authoritative protected evaluation, handler and cleanup cursor states.

These are active single-use frames. They do not copy or save continuations and
never replay a completed effect. Unmigrated native protocols retain their own
explicit bridges; a whole protected language body no longer needs one.
"""


class ProtectedValueFrame:
    """Resolve pending calls before leaving a protected dynamic boundary."""
    def __init__(self,node,env):
        self.node,self.env=node,env;self.phase='start'
        self.value=None;self.signal=None;self.capabilities=None

    def roots(self):return (self.env,self.value,self.signal)

    def resolve(self,value,phase):
        from .language import TailCall
        from .execution_frames import Push,InvocationFrame,InvocationRequest
        self.value=value;self.phase=phase
        if isinstance(value,TailCall):
            options={'span':value.span,'contracts':value.contracts,
                     'continuations':value.continuations,
                     'handled_error':value.handled_error,
                     'error_context':value.error_context,
                     'all_results':True,'primary_only':value.primary_only}
            return Push(InvocationFrame(InvocationRequest(value.function,value.args,value.kwargs,options)))
        from .multiple_values import prepare
        return self.finished(prepare(self.owner,value))

    def finished(self,value):
        from .language import ReturnSignal
        from .execution_frames import Done
        returning=self.phase=='return_resolved'
        self.value=self.signal=None
        if returning:raise ReturnSignal(value)
        return Done(value)

    def step(self,cursor,value,error):
        from .language import ReturnSignal
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        if self.phase=='start':
            if error is not None:raise error
            from .agent_tools import current_caps
            self.capabilities=current_caps(cursor.owner);self.phase='body'
            if getattr(self,'_flow_state',None) is not None:
                from .compiled_flow_protection import protected_value_child
                return protected_value_child(self)
            return Push(EvaluationBoundaryFrame(EvaluationRequest(self.node,self.env,True,{'all_results':True})))
        if error is not None:
            if isinstance(error,ReturnSignal) and self.phase!='return_resolved':
                self.signal=error
                return self.resolve(error.value,'return_resolved')
            self.value=self.signal=None;raise error
        if self.phase=='body':return self.resolve(value,'resolved')
        return self.finished(value)

    def close(self,cursor):self.value=self.signal=None


class TryFrame:
    KINDS={'try','try_cases'}

    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env
        self.tail=boundary.tail;self.options=boundary.options;self.phase='start'
        self.error=None;self.scope=None;self.cases=None;self.index=0;self.case=None
        self.previous_error=None;self.previous_context=None;self.active=False

    def roots(self):
        return (self.env,self.scope,self.error,self.previous_error,self.previous_context)

    def restore(self,evaluator):
        if self.active:
            self.active=False
            evaluator.handled_error,evaluator.error_context=self.previous_error,self.previous_context
        self.previous_error=self.previous_context=None;self.scope=None;self.error=None

    def child(self,node,scope,*,handler=False):
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,scope,self.tail if handler else False,
                    {'all_results':self.options.get('all_results',False)} if handler else {})))

    def step(self,cursor,value,error):
        try:
            if getattr(self.boundary,'flow_structural',False):
                from .compiled_flow_protection import step
                return step(self,cursor,value,error,self.advance)
            return self.advance(cursor,value,error)
        except BaseException as failure:
            from .language import LanguageError
            try:
                if self.active and isinstance(failure,LanguageError):
                    from .errors import observe
                    failure=observe(cursor.owner,failure)
            finally:self.restore(cursor.owner)
            raise failure

    def advance(self,cursor,value,error):
        from .language import Env,LanguageError,ResourceLimit,validate_try_cases,check_type
        from .execution_frames import Push,Done
        e=cursor.owner
        if self.phase=='start':
            if error is not None:raise error
            e.tick()
            if self.node[0]=='try_cases':validate_try_cases(self.node,tick=e.tick)
            self.phase='body';return Push(ProtectedValueFrame(self.node[1],self.env))
        if self.phase=='body':
            if error is None:
                completion=self.options.get('_completion')
                if completion is not None:completion.state='normal'
                return Done(value)
            if not isinstance(error,LanguageError) or isinstance(error,ResourceLimit):raise error
            self.error=error;self.previous_error=e.handled_error;self.previous_context=e.error_context
            e.handled_error=e.error_context=error;self.active=True
            self.cases=[(('p_bind',self.node[2]),None,self.node[3])] if self.node[0]=='try' else self.node[2]
            self.phase='select'
        elif error is not None:raise error
        elif self.phase=='handler':
            completion=self.options.get('_completion')
            if completion is not None:completion.state='handled'
            self.restore(e);return Done(value)
        elif self.phase=='guard':
            truth=check_type(value,'Bool')
            if getattr(self.boundary,'flow_structural',False):
                from .compiled_flow_protection import decide
                truth=decide(self,truth)
            if truth:
                self.phase='handler';return self.child(self.case[2],self.scope,handler=True)
            self.scope=None;self.phase='select'
        if self.phase=='select':
            from .exception_classes import match
            while self.index<len(self.cases):
                self.case=self.cases[self.index];self.index+=1
                pattern,guard,handler=self.case;bindings={}
                if not match(e,pattern,self.error,self.env,bindings):continue
                self.scope=Env(self.env)
                for name,item in bindings.items():self.scope.bind(name,item)
                if guard is not None:
                    self.phase='guard';return self.child(guard,self.scope)
                self.phase='handler';return self.child(handler,self.scope,handler=True)
            raise self.error
        raise RuntimeError('Invalid try-frame phase')

    def close(self,cursor):self.restore(cursor.owner)


class FinallyFrame:
    KINDS={'finally'}

    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env
        self.options=boundary.options;self.phase='start';self.value=None;self.pending_error=None
        self.previous_error=None;self.previous_context=None;self.active=False

    def roots(self):
        return (self.env,self.value,self.pending_error,self.previous_error,self.previous_context)

    def restore(self,evaluator):
        if self.active:
            self.active=False
            evaluator.handled_error,evaluator.error_context=self.previous_error,self.previous_context
        self.previous_error=self.previous_context=None

    def step(self,cursor,value,error):
        try:
            if getattr(self.boundary,'flow_structural',False):
                from .compiled_flow_protection import step
                return step(self,cursor,value,error,self.advance)
            return self.advance(cursor,value,error)
        except BaseException:
            self.restore(cursor.owner);self.value=self.pending_error=None;raise

    def advance(self,cursor,value,error):
        from .language import LanguageError,ResourceLimit,ExecutionControl,ReturnSignal,BreakSignal,ContinueSignal
        from .suspension import GeneratorClose
        from .task_groups import GroupAbort
        from .control_prompts import NonlocalTransfer
        from .execution_frames import Push,Done
        e=cursor.owner
        if self.phase=='start':
            if error is not None:raise error
            e.tick();self.phase='body';return Push(ProtectedValueFrame(self.node[1],self.env))
        if self.phase=='body':
            if isinstance(error,(ResourceLimit,ExecutionControl)):raise error
            if error is not None:
                if isinstance(error,LanguageError):
                    self.previous_error,self.previous_context=e.handled_error,e.error_context
                    e.handled_error=e.error_context=error;self.active=True
                elif not isinstance(error,(ReturnSignal,BreakSignal,ContinueSignal,GeneratorClose,GroupAbort,NonlocalTransfer)):
                    raise error
            self.value=value;self.pending_error=error;self.phase='cleanup'
            return Push(ProtectedValueFrame(self.node[2],self.env))
        if self.phase=='cleanup':
            self.restore(e)
            pending=self.pending_error;result=self.value;self.pending_error=self.value=None
            if error is not None:raise error
            if pending is not None:raise pending
            return Done(result)
        raise RuntimeError('Invalid finally-frame phase')

    def close(self,cursor):
        # Forced abort only restores bookkeeping. Normal GeneratorClose is a
        # signal through step(), where the original user-cleanup rule applies.
        self.restore(cursor.owner);self.pending_error=self.value=None


class TryCompletionFrame:
    KINDS={'try_else'}

    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env
        self.tail=boundary.tail;self.options=boundary.options;self.phase='start'
        self.outcome=None;self.value=None

    def roots(self):return (self.env,self.value)

    def child(self,node,*,completion=None):
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        options={'all_results':self.options.get('all_results',False)}
        if completion is not None:options['_completion']=completion
        if self.options.get('_compiled_regions') is not None:options['_compiled_regions']=self.options['_compiled_regions']
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,self.env,self.tail,options)))

    def step(self,cursor,value,error):
        if getattr(self.boundary,'flow_structural',False):
            from .compiled_flow_protection import step
            return step(self,cursor,value,error,self.advance)
        return self.advance(cursor,value,error)

    def advance(self,cursor,value,error):
        from .language import LanguageError,_Completion
        from .execution_frames import Done
        if error is not None:self.value=None;raise error
        if self.phase=='start':
            cursor.owner.tick();node=self.node
            if len(node)!=3 or not isinstance(node[1],tuple) or not node[1] or node[1][0] not in ('try','try_cases','try_group') or not isinstance(node[2],tuple) or not node[2] or node[2][0]!='block':
                raise LanguageError('正常完成分支需要对应控制结构和 else 块',code='syntax_error')
            self.outcome=_Completion();self.phase='protected';return self.child(node[1],completion=self.outcome)
        if self.phase=='protected':
            self.value=value
            if self.outcome.state not in ('normal','broken','handled'):raise LanguageError('无效内部完成状态')
            normal=self.outcome.state=='normal'
            if getattr(self.boundary,'flow_structural',False):
                from .compiled_flow_protection import decide
                normal=decide(self,normal)
            if normal:self.value=None;self.phase='else';return self.child(self.node[2])
            self.value=None;return Done(value)
        if self.phase=='else':return Done(value)
        raise RuntimeError('Invalid try-completion-frame phase')

    def close(self,cursor):self.value=None
