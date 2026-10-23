"""Lexical resource lifetime held by the shared execution cursor.

Acquisition and exit are explicit native protocol leaves. The language body,
its pending completion and acquired lease remain real cursor state.
"""


class ContextFrame:
    KINDS={'with','async_with','with_pattern','async_with_pattern'}

    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env
        self.phase='start';self.manager=None;self.acquired_value=None
        self.entry=None;self.scope=None;self.result=None;self.pending_error=None
        self.suppressible=False

    def roots(self):
        return (self.env,self.manager,self.acquired_value,self.entry,self.scope,
                self.result,self.pending_error)

    def release(self,evaluator):
        if self.entry is not None:
            from .contexts import release
            release(evaluator,self.entry)

    def retire(self):
        self.manager=self.acquired_value=self.entry=self.scope=None
        self.result=self.pending_error=None;self.suppressible=False

    def step(self,cursor,value,error):
        try:
            if getattr(self.boundary,'flow_structural',False):
                from .compiled_flow_resources import step
                return step(self,cursor,value,error,self.advance)
            return self.advance(cursor,value,error)
        except BaseException:
            try:self.release(cursor.owner)
            finally:self.retire()
            raise

    def exit_body(self,evaluator,value,error):
        from .language import (LanguageError,ResourceLimit,ExecutionControl,
                               ReturnSignal,BreakSignal,ContinueSignal)
        from .suspension import GeneratorClose
        from .task_groups import GroupAbort
        from .control_prompts import NonlocalTransfer
        from .execution_frames import Push,ProtocolBridge
        from .contexts import exit_entry,hard_exit_entry
        if isinstance(error,(ResourceLimit,ExecutionControl)):
            try:hard_exit_entry(evaluator,self.entry)
            except BaseException:pass
            raise error
        exit_error=None
        if isinstance(error,GroupAbort):exit_error=error
        elif isinstance(error,LanguageError):
            from .errors import observe
            observe(evaluator,error);exit_error=error;self.suppressible=True
        elif error is not None and not isinstance(error,(ReturnSignal,BreakSignal,ContinueSignal,
                                                        GeneratorClose,NonlocalTransfer)):
            raise error
        self.result=value;self.pending_error=error;self.phase='exit'
        return Push(ProtocolBridge(exit_entry(evaluator,self.entry,exit_error)))

    def advance(self,cursor,value,error):
        from .language import Env
        from .execution_frames import (Push,Done,ProtocolBridge,
                                       EvaluationBoundaryFrame,EvaluationRequest)
        from .execution_protection import ProtectedValueFrame
        e=cursor.owner
        if self.phase=='start':
            if error is not None:raise error
            e.tick();self.phase='manager'
            return Push(EvaluationBoundaryFrame(EvaluationRequest(self.node[2],self.env)))
        if self.phase=='manager':
            if error is not None:raise error
            self.manager=value
            from .execution_scopes import PromptScopeFrame,CancelShieldFrame,TaskGroupFrame
            from .control_prompts import PromptScope
            from .cancellation import CancelShield
            from .task_groups import TaskGroup
            special={PromptScope:PromptScopeFrame,CancelShield:CancelShieldFrame,TaskGroup:TaskGroupFrame}
            if type(value) in special:
                self.phase='special';return Push(special[type(value)](self.boundary,value))
            from .contexts import acquire
            self.phase='acquire'
            return Push(ProtocolBridge(acquire(e,value,self.node[0] in ('async_with','async_with_pattern'))))
        if self.phase=='acquire':
            if error is not None:raise error
            self.acquired_value,self.entry=value
            try:
                self.scope=Env(self.env)
                e.bind_scope_target(self.node,self.acquired_value,self.scope,self.env)
            except BaseException as failure:return self.exit_body(e,None,failure)
            self.phase='body';return Push(ProtectedValueFrame(self.node[3],self.scope))
        if self.phase=='body':return self.exit_body(e,value,error)
        if self.phase=='exit':
            if error is not None:raise error
            pending=self.pending_error;result=self.result
            suppressed=self.suppressible and value
            self.release(e);self.retire()
            if pending is not None and not suppressed:raise pending
            return Done(None if suppressed else result)
        if self.phase=='special':
            if error is not None:raise error
            self.retire();return Done(value)
        raise RuntimeError('Invalid context-frame phase')

    def close(self,cursor):
        # Forced cursor close retires bookkeeping without executing user exits.
        try:self.release(cursor.owner)
        finally:self.retire()
