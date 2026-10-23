"""Transparent original-cursor observations of structural resource flow."""
import copy
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,LanguageError,ExecutionControl,serializable
from chaos.execution_resources import ContextFrame
from chaos.jit_flow import info
from chaos.jit import compile_info
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
import _generator_flow208 as streams
import _protected_flow214 as protected

TARGETS=streams.targets
KINDS={'with','with_pattern','async_with','async_with_pattern'}
SETUP='''var events=[];class C{var value=null;var label="";var suppress=false;
fn __init__(s,value,label="r",suppress=false){s.value=value;s.label=label;s.suppress=suppress;null}
fn __enter__(s){events=append(events,["enter",s.label]);s.value}
fn __exit__(s,error){events=append(events,["exit",s.label,if error==null{null}else{error.code}]);s.suppress}}
class A{var value=null;var label="";var suppress=false;
fn __init__(s,value,label="r",suppress=false){s.value=value;s.label=label;s.suppress=suppress;null}
async fn __aenter__(s){await sleep(0);events=append(events,["enter",s.label]);s.value}
async fn __aexit__(s,error){await sleep(0);events=append(events,["exit",s.label,if error==null{null}else{error.code}]);s.suppress}}'''

def structural(program):
    return [row for row in program.instructions if row[0]=='resource' and row[1][0]in KINDS]

def compiled(test,target,states,functions):
    if target in ('raw','interpreter'):test.assertEqual(states,[]);return
    test.assertTrue(states,'resource function must execute actual CFG')
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers and s.pending_cursor is None and s.pending_request is None for s in states))
    for function in functions:
        flow=function.compiled_plan.flow;test.assertIsNotNone(flow);test.assertTrue(structural(flow),'whole-resource opaque evaluation is not structural CFG')
        test.assertGreater(info(function.owner,function)['portableResumes'],0)
        test.assertEqual(info(function.owner,function)['nativeResumes'],0,'trace/debug rows permanently select the documented portable fallback')
        test.assertEqual(len(info(function.owner,function)),11);test.assertEqual(len(compile_info(function.owner,function)),12)

def record(body,target='portable',**options):
    options['setup']=SETUP+options.pop('setup','')
    return protected.record(body,target,**options)

PENDING='''let padding=range(PADDING);
class Gate{HOOK fn ENTER(s){remember_effect("acquire_hold");tool("acquire_hold",[20,22])}
HOOK fn EXIT(s,error){remember_effect("exit1_hold");tool("exit1_hold",1);remember_effect("exit2_hold");tool("exit2_hold",2);null}}
fn manager(){remember_effect("source_hold");tool("source_hold",0);Gate()}
let work=choose(async fn(pad){KEYWORD manager() as [a,b]{remember_effect("body_hold");let value=tool("body_hold",a+b);value+length(pad)-length(pad)}});
let task=work(padding);remember(task,padding);spawn(task);await task'''
AWAIT='''let padding=range(PADDING);class Gate{fn __enter__(s){42}fn __exit__(s,error){remember_effect("exit1_hold");tool("exit1_hold",1);remember_effect("exit2_hold");tool("exit2_hold",2);null}}
let borrowed=spawn((async fn(){remember_effect("borrowed_hold");tool("borrowed_hold",40)})());
let work=choose(async fn(pad,child){with Gate() as value{let result=await child;result+2+length(pad)-length(pad)}});
let task=work(padding,borrowed);remember(task,padding,borrowed);spawn(task);FINAL_AWAIT'''

GROUP='''let padding=range(PADDING);let work=choose(async fn(pad){for n in [1,2]{async with task_group() as group{remember_effect("group_body_hold");let value=tool("group_body_hold",40);if n==1{continue}else{return value+2+length(pad)-length(pad)}}};0});let task=work(padding);remember(task,padding);spawn(task);await task'''

SHIELD='''let padding=range(PADDING);let work=choose(async fn(pad){with cancel_shield() as shield{remember_effect("body_hold");let value=tool("body_hold",42);value+length(pad)-length(pad)}});let task=work(padding);remember(task,padding);spawn(task);await task'''

def pending(test,padding,target,asynchronous=True,awaited=False,cancel=False,group=False,shield=False):
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
                    if target not in ('raw','interpreter'):
                        live=[s for s in states if not s.closed and s.function is functions[0]]
                        if group:
                            from chaos.execution_scopes import TaskGroupFrame,GroupBodyFrame
                            from chaos.task_groups import GroupRun
                            test.assertEqual(len(live),2)
                            parents=[s for s in live if s.pending_cursor is root.runner.iterator];test.assertEqual(len(parents),1);state=parents[0]
                            child=[s for s in live if s is not state][0]
                            test.assertIs(type(state.pending_request),GroupRun);test.assertIs(state.pending_request.group.body,task)
                            test.assertIs(state.pending_request.iterator,task.runner.iterator);test.assertIsNot(state.pending_cursor,task.runner.iterator)
                            test.assertIs(child.pending_cursor,task.runner.iterator);test.assertIs(child.pending_request,requests[future]);test.assertEqual(child.pending_kind,'host')
                            taskframes=[f for f in root.runner.iterator.frames if type(f)is TaskGroupFrame];test.assertEqual(len(taskframes),1)
                            test.assertIs(taskframes[0].body_cursor,task.runner.iterator);test.assertIs(taskframes[0].manager.body,task)
                            test.assertTrue(any(type(f)is GroupBodyFrame for f in task.runner.iterator.frames))
                            for current,runner in [(state,root.runner),(child,task.runner)]:
                                current.validate(full=True);test.assertIs(current.owner,e);test.assertTrue(structural(current.program))
                                test.assertTrue(any(getattr(f,'compiled_flow_state',None)is current for f in runner.capture_frames))
                        else:
                            test.assertEqual(len(live),1);state=live[0];state.validate(full=True);test.assertIs(state.owner,e);test.assertIs(state.pending_cursor,root.runner.iterator)
                            test.assertTrue(structural(state.program));test.assertTrue(any(getattr(f,'compiled_flow_state',None)is state for f in root.runner.capture_frames))
                            resource=[f for f in root.runner.iterator.frames if type(f)is ContextFrame];test.assertEqual(len(resource),1)
                            frame=resource[0];test.assertIs(frame.boundary.flow_state,state);test.assertIsNotNone(frame._flow_resource_region)
                            phase='special'if shield else'manager'if name=='source_hold'else'acquire'if name=='acquire_hold'else'exit'if name.startswith('exit')else'body'
                            test.assertEqual(frame.phase,phase)
                            if task is root:test.assertIs(state.pending_request,requests[future]);test.assertEqual(state.pending_kind,'host')
                            else:
                                test.assertIs(type(state.pending_request),suspension.Await);test.assertIs(state.pending_request.task,root.runner.waiting)
                                chain=root.runner.waiting;visited=set()
                                while chain is not task:
                                    test.assertIsNotNone(chain);test.assertNotIn(id(chain),visited);visited.add(id(chain));chain=chain.runner.waiting
                                test.assertEqual(state.pending_kind,'await')
                            if name=='borrowed_hold':test.assertIs(task,borrowed);test.assertIs(root.runner.waiting,borrowed)
                    else:test.assertEqual(states,[])
                    _,delta,paths=seal(e,(root,borrowed)if borrowed is not None else(root,),None);observations.append((name,delta,paths))
                    if cancel and name=='borrowed_hold':e.suspension.cancel(root);test.assertFalse(borrowed.cancel_event.is_set())
            finally:answer=wait(futures,*args,**kw)
            return answer
        source=(SHIELD if shield else GROUP if group else AWAIT if awaited else PENDING).replace('PADDING',str(padding)).replace('FINAL_AWAIT','await borrowed'if cancel else'await task')
        if not awaited and not group:source=source.replace('HOOK','async'if asynchronous else'').replace('ENTER','__aenter__'if asynchronous else'__enter__').replace('EXIT','__aexit__'if asynchronous else'__exit__').replace('KEYWORD','async with'if asynchronous else'with')
        with patch.object(suspension.ThreadPoolExecutor,'submit',submitting),patch.object(suspension,'wait',waiting):result=('value',serializable(e.run(source)))
        expected=['body_hold']if shield else['group_body_hold','group_body_hold']if group else(['borrowed_hold']if cancel else['borrowed_hold','exit1_hold','exit2_hold'])if awaited else['source_hold','acquire_hold','body_hold','exit1_hold','exit2_hold']
        test.assertEqual([row[0]for row in observations],expected);test.assertEqual(schedule['waitBoundaryReleases'],len(expected));test.assertEqual(schedule['shutdownReleases'],0)
    test.assertEqual(e.depth,0);test.assertEqual(e.frames,[]);test.assertIsNone(e.handled_error);test.assertIsNone(e.error_context);test.assertFalse(e.suspension.active);test.assertIsNone(e.suspension.pool)
    if cancel:
        root,pad,borrowed=held[0];test.assertEqual(root.state,'cancelled');test.assertEqual(borrowed.state,'completed');test.assertFalse(borrowed.cancel_event.is_set())
    return (result,e.steps-base[0],effects,trace,debug,observations),states,functions
