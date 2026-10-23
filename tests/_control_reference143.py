"""Exact pre-frame native control algorithms, available only to tests."""
from contextlib import contextmanager, ExitStack
from unittest.mock import patch
from chaos.control_prompts import (PromptScope, EscapeContinuation, PromptTransfer, Barrier,
                                   enter, release, tag, fail)

def callback(evaluator,function,args=(),authority=None):
    from chaos.agent_tools import scope
    with scope(evaluator,authority):
        return (yield from evaluator._s_invoke(function,list(args),force=True,_context_callback=True,all_results=True))


def handled(evaluator,scope,handler,signal):
    from chaos.conditions import intersection
    from chaos.agent_tools import current_caps,scope as authority_scope
    from chaos.multiple_values import components,prepare
    ceiling=intersection(scope.capabilities,signal.authority,current_caps(evaluator))
    with authority_scope(evaluator,ceiling):
        completion=prepare(evaluator,signal.completion)
        if handler is None:return completion
        return (yield from callback(evaluator,handler,components(completion,evaluator),ceiling))


def run(evaluator,scope,body,args=()):
    handler=scope.handler
    enter(evaluator,scope)
    signal=None
    try:
        try:result=yield from callback(evaluator,body,args)
        except PromptTransfer as caught:
            if caught.target is not scope:raise
            signal=caught
    finally:release(scope)
    if signal is not None:return (yield from handled(evaluator,scope,handler,signal))
    return result


def resolve(evaluator,request):
    operation,values=request.operation,request.values
    if operation=='prompt':
        value,body,handler=values
        return (yield from run(evaluator,PromptScope(evaluator,value,handler),body))
    if operation=='escape':
        body=values[0];scope=PromptScope(evaluator,tag(evaluator))
        escape=EscapeContinuation(evaluator.suspension,evaluator.suspension.epoch,scope,
                                  'escape-'+str(evaluator.suspension.next_id()),scope.capabilities)
        return (yield from run(evaluator,scope,body,(escape,)))
    if operation=='barrier':
        value=Barrier(evaluator);enter(evaluator,value)
        try:return (yield from callback(evaluator,values[0]))
        finally:release(value)
    if operation=='wind':
        before,body,after=values
        yield from callback(evaluator,before)
        from chaos.language import LanguageError
        from chaos.suspension import hard_error
        try:result=yield from callback(evaluator,body)
        except BaseException as error:
            if hard_error(error):raise
            previous_error,previous_context=evaluator.handled_error,evaluator.error_context
            if isinstance(error,LanguageError):evaluator.handled_error=evaluator.error_context=error
            try:yield from callback(evaluator,after)
            finally:evaluator.handled_error,evaluator.error_context=previous_error,previous_context
            raise
        yield from callback(evaluator,after)
        return result
    fail('Unknown control request','argument_error')



@contextmanager
def reference(evaluator=None, *, lexical=False):
    with ExitStack() as patches:
        patches.enter_context(patch('chaos.control_prompts.resolve', resolve))
        if lexical:
            # The lexical frame retains its original forwarding bridge. Swap
            # only its called handler algorithm back to the exact original.
            patches.enter_context(patch('chaos.control_prompts.handled', handled))
        yield
