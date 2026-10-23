"""Actual public async loop bodies; transparent observations, no semantic oracle patch."""
import copy
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,LanguageError,ExecutionControl,serializable
from chaos.jit_flow import info
from chaos.jit import compile_info
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
import _generator_flow208 as streams

TARGETS=streams.targets

def retired(test,target,states,functions):
    if target in ('raw','interpreter'):
        test.assertEqual(states,[]);return
    test.assertTrue(states,'new structural async consumer must actually execute')
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and s.pending_cursor is None and s.pending_request is None for s in states))
    for f in functions:
        test.assertIsNotNone(f.compiled_plan.flow)
        nodes=[row[1][0] for row in f.compiled_plan.flow.instructions if row[0]=='enter']
        test.assertTrue({'async_for','async_for_pattern'} & set(nodes),'whole-loop eval is not a compiled async loop')
        test.assertGreater(info(f.owner,f)['portableResumes'],0)
        test.assertEqual(info(f.owner,f)['nativeResumes'],0,'trace/debug must retain documented portable fallback')
        test.assertEqual(len(info(f.owner,f)),11);test.assertEqual(len(compile_info(f.owner,f)),12)

def record(body,args=(),target='portable',setup='',limit=30000,inject=None):
    effects=[];trace=[];debug=[];functions=[]
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:effects.append((n,copy.deepcopy(v)))or v)
    # One real module block: its returned Function closes over setup bindings.
    # A second run would create a distinct lexical block, losing these values.
    f=streams.compile_choice(e,e.run((setup+';'if setup else'')+body),target);functions.append(f)
    base=e.steps;e.step_limit=base+limit
    def traced(row):
        row=copy.deepcopy(row)
        if type(row.get('step'))is int:row['step']-=base
        trace.append(row)
    e.trace_handler=traced;e.debug_hook=lambda n,env,frames:debug.append((n[0],dict(e.current_span),e.steps-base))
    failure=None
    with streams.observe_states()as states,effect_schedule('already_done'):
        try:result=('value',serializable(e.invoke(f,list(args),force=True)))
        except LanguageError as error:failure=error;result=('error',error.record())
        except ExecutionControl as error:failure=error;result=('control',type(error).__name__,str(error))
        finally:e.suspension.finish(failure)
    return (result,e.steps-base,effects,trace,debug,e.depth,len(e.frames)),states,functions

# Exactly one item plus EOF: six real worker Futures, including two anexts.
# All five regions remain non-tail while the named effect is held.
PENDING='''class Cursor{var n=0;fn __aiter__(self){remember_effect("acquire_hold");tool("acquire_hold",0);self}
async fn __anext__(self){remember_effect("anext_hold");tool("anext_hold",self.n);self.n=self.n+1;{done:self.n>1,value:40}}
async fn __aclose__(self){record_close();null}};
let padding=range(PADDING);let raw=Cursor();let work=choose(async fn(pad,source){var total=0;
async for value in do{remember_effect("source_hold");tool("source_hold",0);source}{
remember_effect("body_hold");let used=tool("body_hold",value);total=total+used
}else{remember_effect("else_hold");total=total+tool("else_hold",2)};total+length(pad)-length(pad)});
let task=work(padding,raw);remember(task,padding);spawn(task);let result=await task;before_epoch_close();result'''

def pending(test,padding,target,poison=None,cancel=False):
    effects=[];trace=[];debug=[];held=[];requests={};names={};seen=[];observations=[];functions=[];effects_tasks=[];base=[None];closed=[];completed=[]
    def effect(kind,payload,event):effects.append((kind,copy.deepcopy(payload)));return payload['value']
    e=Evaluator(tool_handler=lambda n,v:v,effect_handler=effect,step_limit=30000,timeout=30)
    def choose(f):answer=streams.compile_choice(e,f,target);functions.append(answer);return answer
    def remember(task,pad):held.append((task,pad));base[0]=e.steps;e.step_limit=e.steps+30000;trace.clear();debug.clear()
    def remember_effect(name):effects_tasks.append((e.suspension.current.task,name))
    def record_close():
        test.assertTrue(e.suspension.finishing,'this close belongs to real epoch retirement, not loop borrowing')
        test.assertIn(held[0][0].state,suspension.TERMINAL);closed.append(True)
    def before_epoch_close():
        test.assertEqual(closed,[],'loop exhaustion must not itself close its borrowed iterator')
        test.assertEqual(held[0][0].state,'completed');completed.append(True)
    for name,fn in [('choose',choose),('remember',remember),('remember_effect',remember_effect),('record_close',record_close),('before_epoch_close',before_epoch_close)]:e.env.bind(name,Native(name,fn,e,captures=()))
    def traced(row):
        if base[0]is not None:
            row=copy.deepcopy(row)
            if type(row.get('step'))is int:row['step']-=base[0]
            trace.append(row)
    e.trace_handler=traced;e.debug_hook=lambda n,env,frames:debug.append((n[0],dict(e.current_span),e.steps-base[0]))if base[0]is not None else None
    seal=observed_seal()
    with streams.observe_states()as states,effect_schedule('first_wait')as schedule:
        wait=suspension.wait;submit=suspension.ThreadPoolExecutor.submit
        def submitting(pool,fn,*args,**kw):
            future=submit(pool,fn,*args,**kw)
            if args and type(args[0])is suspension.Effect:
                names[future]=args[0].payload['name'];requests[future]=args[0]
            return future
        def waiting(futures,*args,**kw):
            futures=tuple(futures)
            try:
                for task,name in reversed(effects_tasks):
                    future=task.runner.future
                    if future is None or future not in futures or future in seen:continue
                    seen.append(future);test.assertFalse(future.done());test.assertEqual(names[future],name)
                    test.assertEqual(task.runner.state,'effect');test.assertNotIn(task.state,suspension.TERMINAL)
                    consumer,pad=held[0];test.assertNotIn(consumer.state,suspension.TERMINAL)
                    test.assertIs(consumer.capture_roots[1]['pad'],pad);test.assertEqual(pad,list(range(padding)))
                    if name=='anext_hold':
                        operation=consumer.runner.waiting
                        test.assertIs(type(operation),suspension.Task);test.assertIsNot(operation,task)
                        test.assertIs(operation.runner.waiting,task)
                    if target not in ('raw','interpreter'):
                        live=[s for s in states if not s.closed and s.function is functions[0]];test.assertEqual(len(live),1)
                        state=live[0];state.validate(full=True);test.assertIs(state.owner,e)
                        test.assertIs(state.pending_cursor,consumer.runner.iterator)
                        test.assertTrue(any(getattr(f,'compiled_flow_state',None)is state for f in consumer.runner.capture_frames))
                        if name=='anext_hold':
                            from chaos.execution_loops import LoopFrame
                            loops=[f for f in consumer.runner.iterator.frames if type(f)is LoopFrame and f.kind=='async_for']
                            test.assertEqual(len(loops),1);loop=loops[0]
                            test.assertTrue(loop.boundary.flow_structural);test.assertIs(loop.operation,operation)
                            test.assertEqual(loop.phase,'for_item_async');test.assertEqual(loop._flow_wait,'step_async')
                            test.assertIs(type(state.pending_request),suspension.Await);test.assertIs(state.pending_request.task,operation)
                            test.assertIs(consumer.runner.waiting,operation);test.assertEqual(state.pending_kind,'await')
                        else:
                            test.assertIs(task,consumer);test.assertIs(state.pending_request,requests[future]);test.assertEqual(state.pending_kind,'host')
                    else:test.assertEqual(states,[])
                    _,delta,paths=seal(e,(consumer,task),None);observations.append((name,delta,paths))
                    if poison is not None and name=='anext_hold':poison(e,state,consumer,task)
                    if cancel and name=='anext_hold':e.suspension.cancel(consumer)
            finally:answer=wait(futures,*args,**kw)
            return answer
        with patch.object(suspension.ThreadPoolExecutor,'submit',submitting),patch.object(suspension,'wait',waiting):
            try:result=('value',serializable(e.run(PENDING.replace('PADDING',str(padding)))))
            except LanguageError as error:result=('error',error.code)
        if not poison and not cancel:
            test.assertEqual([x[0]for x in observations],['source_hold','acquire_hold','anext_hold','body_hold','anext_hold','else_hold'])
            test.assertEqual(schedule['waitBoundaryReleases'],6);test.assertEqual(schedule['shutdownReleases'],0)
            test.assertEqual(completed,[True])
        test.assertEqual(closed,[True],'original Scheduler.finish retires the registered custom iterator exactly once')
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and s.pending_cursor is None and s.pending_request is None for s in states))
    test.assertEqual(e.depth,0);test.assertEqual(e.frames,[]);test.assertFalse(e.suspension.active);test.assertIsNone(e.suspension.pool)
    return (result,e.steps-base[0],effects,trace,debug,observations),states,functions
