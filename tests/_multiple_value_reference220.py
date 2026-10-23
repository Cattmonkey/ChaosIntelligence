"""Frozen .134 receive/resolve, with only absolute test imports substituted.

Original algorithms are executed only by the root-authorized test process.
No replacement scheduler, evaluator driver, capture walker or tick policy.
"""
import copy
from contextlib import contextmanager, ExitStack, nullcontext
from unittest.mock import patch
import chaos.multiple_values as mv
from chaos.multiple_values import Completion,producer,prepare,validate,components
from chaos.language import Evaluator,LanguageError,ExecutionControl,Native,serializable
from _effect_schedule158 import effect_schedule

def receive(evaluator, value):
    from chaos.suspension import Task,Generator,AsyncGenerator
    if type(value) in (Task,Generator,AsyncGenerator):
        producer(evaluator,value)
        from chaos.agent_tools import scope,current_caps
        from chaos.capture_graph import seal
        from chaos.class_creation import validate_value,_project_return
        with scope(evaluator,value.capabilities):
            result=value.runner.result if type(value) is Task else value.result
            if type(result) is Completion:result=prepare(evaluator,result)
            else:
                validate_value(evaluator,result)
                ceiling=current_caps(evaluator)
                seal(evaluator,(result,),ceiling)
                if ceiling is not None:result=_project_return(evaluator,result,ceiling)
            return result
    # A handle returned by a callback remains one ordinary result.
    return (yield from evaluator._s_invoke(value,[],force=True,_context_callback=True,all_results=True))

def resolve(evaluator, value):
    from chaos.language import LanguageError, TailCall
    from chaos.capture_frames import active, retain
    if value.owner is not evaluator:
        raise LanguageError('Multiple-value request cannot cross evaluators', code='ownership_error')
    def callback(function):
        return receive(evaluator,function)
    operation = value.operation
    if operation in ('list', 'nth', 'bind', 'prog1'):
        result = yield from callback(value.producers[0])
        validate(evaluator, result)
        retain(active(evaluator), result)
        items = components(result, evaluator)
        if operation == 'list': return list(items)
        if operation == 'nth': return items[value.count] if value.count < len(items) else None
        if operation == 'bind':
            args = list(items[:value.count])
            args.extend([None] * (value.count - len(args)))
            return (yield from evaluator._s_invoke(value.function, args, force=True, _context_callback=True, all_results=True))
        for function in value.producers[1:]:
            yield from callback(function)
        return result
    if operation == 'call':
        args = []
        for function in value.producers:
            result = yield from callback(function)
            validate(evaluator, result)
            args.extend(components(result, evaluator))
            evaluator.argument_budget(args, {})
            retain(active(evaluator), result)
        return TailCall(value.function, args, span=dict(evaluator.current_span),ordinary_call=True)
    raise LanguageError('Unknown multiple-value receiver', code='argument_error')

@contextmanager
def reference():
    reached={'receive':0,'resolve':0}
    def old_receive(e,value):
        reached['receive']+=1
        return receive(e,value)
    def old_resolve(e,value):
        reached['resolve']+=1
        return resolve(e,value)
    with patch.object(mv,'receive',old_receive),patch.object(mv,'resolve',old_resolve):
        yield reached

@contextmanager
def observe():
    from chaos.execution_multiple_values import MULTIPLE_VALUE_FRAMES
    counts={};held=[]
    with ExitStack() as stack:
        for cls in MULTIPLE_VALUE_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                name=type(frame).__name__;counts[name]=counts.get(name,0)+1
                held.append((frame,cursor))
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield counts,held

def snapshot(source,legacy=False,limit=200000,cancel=None,setup=None):
    effects=[];trace=[];debug=[];kept=[]
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)))
        return payload['value'] if kind=='tool' else payload.get('input')
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:v,effect_handler=effect,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup:setup(e,kept)
    with reference() if legacy else nullcontext() as reached,observe() as (counts,frames),effect_schedule('already_done') as schedule:
        try:result=('value',serializable(e.run(source)))
        except LanguageError as error:kept.append((error,error.__traceback__));result=('error',error.record())
        except ExecutionControl as error:kept.append((error,error.__traceback__));result=('control',type(error).__name__,str(error))
    assert e.handled_error is None and e.error_context is None
    assert not e.suspension.active and e.suspension.pool is None
    vector=(result,e.steps,effects,trace,debug,e.depth,len(e.frames),dict(schedule))
    return vector,counts,frames,reached
