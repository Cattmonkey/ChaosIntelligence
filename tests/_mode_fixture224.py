"""Original-driver Mode observations; real workers and explicit typed states."""
import copy
from contextlib import contextmanager, ExitStack, nullcontext
from unittest.mock import patch
from chaos.language import Evaluator, LanguageError, serializable
from chaos.execution_frames import External
from chaos.suspension import Effect
from chaos.task_groups import GroupRun
from chaos.execution_modes import MODE_FRAMES, ModeHostFrame, ModeParallelPlanFrame, ModeRecordFrame
from _effect_schedule158 import effect_schedule
from _mode_reference224 import original_modes, fixed_execution_ids

@contextmanager
def observe_frames(test):
    seen=[];actions=[]
    with ExitStack() as stack:
        for cls in MODE_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                test.assertIs(frame.owner,cursor.owner)
                test.assertIn(frame,cursor.frames)
                if not any(frame is old[0] for old in seen):seen.append((frame,cursor))
                action=_original(frame,cursor,value,error)
                if type(action) is External:
                    actions.append((frame,cursor,action.request))
                    if type(frame) is ModeHostFrame:
                        test.assertEqual(frame.phase,'effect')
                        test.assertIs(type(action.request),Effect)
                        test.assertEqual(action.request.kind,'mode')
                        test.assertEqual(action.request.payload,{'operation':frame.operation,'payload':frame.payload})
                    if type(frame) is ModeParallelPlanFrame:
                        test.assertEqual(frame.phase,'group')
                        test.assertIs(type(action.request),GroupRun)
                        test.assertIs(action.request.group,frame.group)
                    if type(frame) is ModeRecordFrame and frame.phase!='start':
                        test.assertIs(frame.task,cursor.owner.suspension.current.task)
                return action
            stack.enter_context(patch.object(cls,'step',step))
        yield seen,actions

def snapshot(test,source,legacy=False,host=None,tool=None,fault=None,schedule='already_done'):
    effects=[];trace=[];debug=[];e=None
    def effect(kind,payload,cancel):
        effects.append((kind,copy.deepcopy(payload)))
        if kind=='mode':return host(payload['operation'],copy.deepcopy(payload['payload']))
        if kind=='tool':
            if fault is not None and payload['name']=='fault':raise fault
            return tool(payload['name'],payload['value']) if tool else payload['value']
        raise AssertionError('Unexpected Mode fixture effect '+kind)
    e=Evaluator(step_limit=30000,timeout=20,tool_handler=lambda n,v:v,
        mode_handler=(lambda op,payload:None)if host else None,effect_handler=effect,
        trace_handler=lambda row:trace.append(copy.deepcopy(row)))
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    failure=None
    with original_modes()if legacy else nullcontext(),fixed_execution_ids(),observe_frames(test)as observed,effect_schedule(schedule)as timing:
        try:outcome=('value',serializable(e.run(source)))
        except BaseException as error:
            failure=error
            outcome=('error',type(error).__name__,error.record()if isinstance(error,LanguageError)else str(error),error is fault)
    row=(outcome,e.steps,effects,trace,debug,e.depth,len(e.frames),
         [(task.id,task.state,task.observed,task.recording_scope)for task in e.suspension.tasks],
         [group.state for group in e.suspension.groups])
    return row,observed,e,failure,timing

SERIAL='''var seen=[];let p=plan([{id:"end",call:"end",needs:["a"]},{id:"b",call:"b"},{id:"a",call:"a"}]);
let out=await run_plan(p,{a:fn(c){seen=append(seen,"a");40},b:fn(c){seen=append(seen,"b");0},end:fn(c){seen=append(seen,"end");[keys(c.results),c.results.a+2]}},null);[out,seen]'''
GOAL='''let p=plan([{id:"x",call:"step"}]);let g=goal("reach42","planner","verify",3);
await pursue(g,{planner:fn(c){p},step:fn(c){tool("step",c.input+1)},verify:fn(c){{done:c.results.x==42,state:c.results.x,evidence:c.results}}},40,key="goal224")'''
SOURCE='''let p=plan([{id:"later",call:"end",needs:["seed"]},{id:"seed",call:"seed"},{id:"other",call:"other"}]);
let out=await run_plan(p,{end:async fn(c){await sleep(0);c.results.seed+2},seed:fn(c){multiple_value_list(fn(){return_values(c.input,9)})[0]},other:fn(c){0}},40);
[out,mode_info(p).kind,type_of(save_mode(p,"cold"))]'''
EXPECTED=[{'seed':40,'other':0,'later':42},'plan','Task']
