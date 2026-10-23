"""Observe actual invocation ownership; hold real Futures without changing language work."""
import copy
from contextlib import ExitStack,contextmanager,nullcontext
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ExecutionControl,Native,serializable
from chaos.execution_frames import ExecutionCursor
from chaos.execution_invocation import InvocationDispatchFrame
import chaos.suspension as suspension
from _effect_schedule158 import effect_schedule
from _invocation_reference222 import reference

@contextmanager
def observe_entries():
    seen=[];original=InvocationDispatchFrame.step
    def step(frame,cursor,value,error):
        if frame.phase=='start':seen.append((frame,cursor,type(frame.state.fn).__name__))
        return original(frame,cursor,value,error)
    with patch.object(InvocationDispatchFrame,'step',step):yield seen

def owned_frames(testcase,root):
    todo=[root];visited=set();found=[]
    while todo:
        cursor=todo.pop()
        testcase.assertIsInstance(cursor,ExecutionCursor)
        testcase.assertIs(cursor.owner,root.owner)
        testcase.assertNotIn(id(cursor),visited,'owned cursor graph has a cycle or aliased child')
        visited.add(id(cursor))
        for frame in cursor.frames:
            if type(frame)is InvocationDispatchFrame:found.append((frame,cursor))
            for slot in ('pending_cursor','dispatch_cursor'):
                child=getattr(frame,slot,None)
                if child is not None:
                    testcase.assertIsInstance(child,ExecutionCursor)
                    testcase.assertIs(child.owner,root.owner)
                    testcase.assertIs(child.parent_bridge,frame)
                    testcase.assertIs(getattr(frame,slot),child)
                    if slot=='dispatch_cursor':
                        from chaos.execution_frames import InvocationCloseCursor
                        testcase.assertIs(type(child),InvocationCloseCursor)
                    todo.append(child)
    return found

def error_row(error,fault=None):
    return (type(error).__name__,error.record()if isinstance(error,LanguageError)else str(error),error is fault,
            type(error.__cause__).__name__ if error.__cause__ else None,
            str(error.__cause__)if error.__cause__ else None)

def run(testcase,source,*,legacy=False,phase=None,setup=None,fault=None,agent=False,pending_check=None):
    effects=[];trace=[];debug=[];remembered=[];pending=[];inspected=set()
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)))
        if fault is not None:raise fault
        return payload['value']if kind=='tool'else payload['value']+2
    e=Evaluator(timeout=30,step_limit=200000,tool_handler=lambda name,value:value,
                agent_handler=(lambda name,config,value:value)if agent else None,
                effect_handler=effect,trace_handler=trace.append)
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    e.env.bind('remember222',Native('remember222',lambda task,target:remembered.append((task,target)),e))
    if setup is not None:setup(e)
    with reference()if legacy else nullcontext(),observe_entries()as seen,effect_schedule('first_wait')as schedule:
        original_wait=suspension.wait
        def waiting(futures,*args,**kwargs):
            futures=tuple(futures)
            for task,target in remembered:
                future=task.runner.future
                if future is None or future not in futures or id(future)in inspected:continue
                inspected.add(id(future));testcase.assertFalse(future.done());testcase.assertEqual(task.runner.state,'effect')
                if pending_check is not None:pending_check(e,task,target,legacy)
                if phase is not None:
                    matches=[]if legacy else [(frame,cursor)for frame,cursor in owned_frames(testcase,task.runner.iterator)if frame.phase==phase and frame.state.fn is target]
                    testcase.assertEqual(len(matches),0 if legacy else 1)
                    if not legacy:
                        frame,cursor=matches[0];frame.validate_state()
                        testcase.assertIs(frame.owner,e);testcase.assertIs(frame.state.fn,target)
                        testcase.assertEqual(frame.epoch,e.suspension.epoch)
                        testcase.assertEqual(frame.state.args[0],40)if phase!='native'else None
                        testcase.assertIsNotNone(cursor.parent_bridge)
                        if phase=='agent':
                            testcase.assertIs(type(frame.effect),suspension.Effect)
                            testcase.assertEqual(frame.effect.kind,'agent');testcase.assertEqual(frame.effect.payload['value'],40)
                        if phase=='native':
                            from chaos.multiple_values import Request
                            testcase.assertIs(type(frame.state.value),Request)
                            testcase.assertEqual(frame.native_stage,2,'resume must continue after the ValuesRequest branch')
                        pending.append((phase,frame.native_stage))
            return original_wait(futures,*args,**kwargs)
        with patch.object(suspension,'wait',waiting):
            try:result=('value',serializable(e.run(source)))
            except BaseException as error:result=('error',error_row(error,fault))
    testcase.assertEqual(schedule['shutdownReleases'],0)
    if phase is not None:testcase.assertEqual(len(inspected),1,'one actual Future must stop before completion')
    if legacy:testcase.assertEqual(seen,[])
    else:testcase.assertTrue(seen)
    testcase.assertEqual(e.depth,0);testcase.assertEqual(e.frames,[])
    testcase.assertIsNone(e.handled_error);testcase.assertIsNone(e.error_context)
    testcase.assertFalse(e.suspension.active);testcase.assertIsNone(e.suspension.pool)
    row=(result,e.steps,effects,trace,debug,dict(schedule))
    return row,seen,pending

LOOKUP='class D{fn __get__(s,o,c){tool("lookup_hold",40);fn(x){x+2}}}class C{let __call__=D()}let callable=C();async fn work(){callable(40)};let task=work();remember222(task,callable);await task'
AGENT='agent worker{provider:"fixture",tools:[]};async fn work(){worker(40)};let task=work();remember222(task,worker);await task'
NATIVE='async fn work(){seed222()};let task=work();remember222(task,seed222);await task'
