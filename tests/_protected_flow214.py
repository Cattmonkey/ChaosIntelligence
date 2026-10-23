"""Independent original public drivers and transparent pending observations."""
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
PROTECTED={'try','try_cases','try_else','finally'}

def compiled(test,target,states,functions):
    if target in ('raw','interpreter'):test.assertEqual(states,[]);return
    test.assertTrue(states,'the protected function must actually execute compiled flow')
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and s.pending_cursor is None and s.pending_request is None for s in states))
    for f in functions:
        test.assertIsNotNone(f.compiled_plan.flow)
        test.assertTrue(any(row[0]=='protect'for row in f.compiled_plan.flow.instructions),'opaque eval of the whole try is not protected CFG')
        test.assertGreater(info(f.owner,f)['portableResumes'],0)
        test.assertEqual(info(f.owner,f)['nativeResumes'],0,'these parity rows intentionally enable trace/debug fallback')
        test.assertEqual(len(info(f.owner,f)),11);test.assertEqual(len(compile_info(f.owner,f)),12)

def record(body,target='portable',args=(),setup='',limit=30000,bind=None,multiple=False):
    effects=[];trace=[];debug=[];functions=[];retained=[]
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:effects.append((n,copy.deepcopy(v)))or v)
    if bind:bind(e,retained)
    f=streams.compile_choice(e,e.run((setup+';'if setup else'')+body),target);functions.append(f)
    base=e.steps;e.step_limit=base+limit
    def traced(row):
        row=copy.deepcopy(row)
        if type(row.get('step'))is int:row['step']-=base
        trace.append(row)
    e.trace_handler=traced;e.debug_hook=lambda n,env,frames:debug.append((n[0],dict(e.current_span),e.steps-base))
    failure=None
    with streams.observe_states()as states,effect_schedule('already_done'):
        try:
            value=e.invoke(f,list(args),force=True,all_results=multiple)
            if multiple:
                from chaos.multiple_values import components
                value=list(components(value,e))
            result=('value',serializable(value))
        except LanguageError as error:failure=error;retained.extend((error,error.__traceback__));result=('error',error.record())
        except ExecutionControl as error:failure=error;retained.extend((error,error.__traceback__));result=('control',type(error).__name__,str(error))
        finally:e.suspension.finish(failure)
    assert e.handled_error is None and e.error_context is None
    return (result,e.steps-base,effects,trace,debug,e.depth,len(e.frames)),states,functions

PENDING='''let padding=range(PADDING);let work=choose(async fn(pad,raising){
try{remember_effect("body_hold");let value=tool("body_hold",40);if raising{throw value}else{value}}
catch e if do{remember_effect("false_guard_hold");let accepted=tool("false_guard_hold",false);accepted}{tool("forbidden_handler",0)}
catch e if do{remember_effect("true_guard_hold");let accepted=tool("true_guard_hold",true);accepted}{remember_effect("handler_hold");let handled=tool("handler_hold",e.value);handled+2}
else{remember_effect("else_hold");let normal=tool("else_hold",42);normal}
finally{remember_effect("cleanup1_hold");tool("cleanup1_hold",1);remember_effect("cleanup2_hold");tool("cleanup2_hold",2)}});let task=work(padding,RAISING);remember(task,padding);spawn(task);await task'''

def pending(test,padding,target,raising=False,awaited=False,cancel=False):
    effects=[];trace=[];debug=[];held=[];tasks=[];requests={};names={};seen=[];observations=[];functions=[];base=[None]
    def effect(kind,payload,event):effects.append((kind,copy.deepcopy(payload)));return payload['value']
    e=Evaluator(tool_handler=lambda n,v:v,effect_handler=effect,step_limit=30000,timeout=30)
    def choose(f):answer=streams.compile_choice(e,f,target);functions.append(answer);return answer
    def remember(task,pad,borrowed=None):held.append((task,pad,borrowed));base[0]=e.steps;e.step_limit=e.steps+30000;trace.clear();debug.clear()
    def remember_effect(name):tasks.append((e.suspension.current.task,name))
    for name,fn in [('choose',choose),('remember',remember),('remember_effect',remember_effect)]:e.env.bind(name,Native(name,fn,e,captures=()))
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
            if args and type(args[0])is suspension.Effect:names[future]=args[0].payload['name'];requests[future]=args[0]
            return future
        def waiting(futures,*args,**kw):
            futures=tuple(futures)
            try:
                for task,name in reversed(tasks):
                    future=task.runner.future
                    if future is None or future not in futures or future in seen:continue
                    seen.append(future);test.assertFalse(future.done());test.assertEqual(names[future],name)
                    test.assertEqual(task.runner.state,'effect');test.assertNotIn(task.state,suspension.TERMINAL)
                    root,pad,borrowed=held[0];test.assertIs(root.capture_roots[1]['pad'],pad);test.assertEqual(pad,list(range(padding)))
                    if name=='borrowed_hold':
                        test.assertIs(task,borrowed);test.assertIs(root.runner.waiting,borrowed);test.assertEqual(root.runner.state,'waiting')
                    else:test.assertIs(task,root)
                    if target not in ('raw','interpreter'):
                        from chaos.execution_protection import TryFrame,FinallyFrame,TryCompletionFrame
                        live=[s for s in states if not s.closed and s.function is functions[0]];test.assertEqual(len(live),1)
                        state=live[0];state.validate(full=True);test.assertIs(state.owner,e);test.assertIs(state.pending_cursor,root.runner.iterator)
                        if name=='borrowed_hold':
                            test.assertIs(type(state.pending_request),suspension.Await);test.assertIs(state.pending_request.task,borrowed);test.assertEqual(state.pending_kind,'await')
                        else:test.assertIs(state.pending_request,requests[future]);test.assertEqual(state.pending_kind,'host')
                        test.assertTrue(any(getattr(f,'compiled_flow_state',None)is state for f in root.runner.capture_frames))
                        frames=root.runner.iterator.frames
                        expected=FinallyFrame if name.startswith('cleanup')or name=='borrowed_hold'else TryCompletionFrame if name=='else_hold'else TryFrame
                        phase='cleanup'if name.startswith('cleanup')else'else'if name=='else_hold'else'guard'if 'guard'in name else'handler'if name=='handler_hold'else'body'
                        actual=[f for f in frames if type(f)is expected and f.phase==phase];test.assertEqual(len(actual),1)
                        test.assertIsNotNone(actual[0]._flow_region,'the actual protected frame must be driven by CFG')
                        if 'guard'in name or name=='handler_hold':test.assertIs(task.runner.handled_error,actual[0].error);test.assertIs(task.runner.error_context,actual[0].error)
                    else:test.assertEqual(states,[])
                    _,delta,paths=seal(e,(root,borrowed)if borrowed is not None else(root,),None);observations.append((name,delta,paths))
                    if cancel and name=='borrowed_hold':
                        e.suspension.cancel(root);test.assertFalse(borrowed.cancel_event.is_set())
            finally:answer=wait(futures,*args,**kw)
            return answer
        source=(PENDING_AWAIT if awaited else PENDING).replace('PADDING',str(padding)).replace('RAISING','true'if raising else'false').replace('FINAL_AWAIT','await borrowed'if cancel else'await task')
        with patch.object(suspension.ThreadPoolExecutor,'submit',submitting),patch.object(suspension,'wait',waiting):result=('value',serializable(e.run(source)))
        expected=(['borrowed_hold']if cancel else['borrowed_hold','cleanup1_hold','cleanup2_hold'])if awaited else(['body_hold','false_guard_hold','true_guard_hold','handler_hold','cleanup1_hold','cleanup2_hold']if raising else['body_hold','else_hold','cleanup1_hold','cleanup2_hold'])
        test.assertEqual([row[0]for row in observations],expected);test.assertEqual(schedule['waitBoundaryReleases'],len(expected));test.assertEqual(schedule['shutdownReleases'],0)
    test.assertEqual(e.depth,0);test.assertEqual(e.frames,[]);test.assertIsNone(e.handled_error);test.assertIsNone(e.error_context);test.assertFalse(e.suspension.active);test.assertIsNone(e.suspension.pool)
    if cancel:
        root,pad,borrowed=held[0];test.assertEqual(root.state,'cancelled');test.assertEqual(borrowed.state,'completed');test.assertFalse(borrowed.cancel_event.is_set())
    return (result,e.steps-base[0],effects,trace,debug,observations),states,functions

PENDING_AWAIT='''let padding=range(PADDING);let borrowed=spawn((async fn(){remember_effect("borrowed_hold");tool("borrowed_hold",40)})());
let work=choose(async fn(pad,child){try{let got=await child;got+2+length(pad)-length(pad)}finally{remember_effect("cleanup1_hold");tool("cleanup1_hold",1);remember_effect("cleanup2_hold");tool("cleanup2_hold",2)}});
let task=work(padding,borrowed);remember(task,padding,borrowed);spawn(task);FINAL_AWAIT'''
