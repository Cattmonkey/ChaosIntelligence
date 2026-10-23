"""Real original drivers, ordered pattern state, and transparent pending evidence."""
import copy
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator,Native,LanguageError,ExecutionControl,serializable
from chaos.execution_patterns import PatternSyntaxFrame
from chaos.jit_flow import info
from chaos.jit import compile_info
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
from test_callable_execution_frames import owned_frame
import _generator_flow208 as streams
import _protected_flow214 as protected

TARGETS=streams.targets
KINDS={'match','let_pattern','var_pattern','assign_pattern'}
SOURCE='''var events=[];async fn raw(value){let [left,right]=do{await sleep(0);[value,2]};var [a,b]=[left,right];[a,b]=[a+1,b-1];match {a:a,b:b}{{a:x,b:y} if do{events=append(events,["false",x]);false}=>-1;{a:x,b:y} if do{await sleep(0);events=append(events,["true",x]);true}=>do{let result=x+y;events=append(events,["body",result]);result};_=>-2}};let f=compile_function(raw,target="auto-flow");let cold=compile_flow_info(f).resumeCalls;[await f(40),await f(0),cold,compile_flow_info(f).resumeCalls>0,events]'''
EXPECTED=[42,2,0,True,[["false",41],["true",41],["body",42],["false",1],["true",1],["body",2]]]

def structural(program):
    return [row for row in program.instructions if row[0]=='pattern' and row[1][0]in KINDS]

def compiled(test,target,states,functions,required=None):
    if target in ('raw','interpreter'):test.assertEqual(states,[]);return
    test.assertTrue(states,'the actual pattern body must execute structural CFG')
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers and s.pending_cursor is None and s.pending_request is None for s in states))
    for f in functions:
        flow=f.compiled_plan.flow;test.assertIsNotNone(flow);rows=structural(flow);test.assertTrue(rows,'whole-pattern opaque eval is not the new structure')
        if required:test.assertTrue(set(required)<={r[1][0]for r in rows})
        test.assertGreater(info(f.owner,f)['portableResumes'],0);test.assertEqual(info(f.owner,f)['nativeResumes'],0,'these differential rows intentionally enable trace/debug fallback')
        test.assertEqual(len(info(f.owner,f)),11);test.assertEqual(len(compile_info(f.owner,f)),12)

record=protected.record

MATCH_PENDING='''let padding=range(PADDING);let work=choose(async fn(pad){match do{remember_effect("subject_hold");tool("subject_hold",42)}{value if do{remember_effect("false_guard_hold");tool("false_guard_hold",false)}=>-1;value if do{remember_effect("true_guard_hold");tool("true_guard_hold",true)}=>do{remember_effect("body_hold");let answer=tool("body_hold",value);answer+length(pad)-length(pad)};_=>0}});let task=work(padding);remember(task,padding);spawn(task);await task'''
BIND_PENDING='''let padding=range(PADDING);let work=choose(async fn(pad){DECL [a,b]=do{remember_effect("rhs_hold");tool("rhs_hold",[20,22])};a+b+length(pad)-length(pad)});let task=work(padding);remember(task,padding);spawn(task);await task'''
ASSIGN_PENDING='''let padding=range(PADDING);let work=choose(async fn(pad){var a:Int=1;var b:Int=2;[a,b]=do{remember_effect("rhs_hold");tool("rhs_hold",[20,22])};a+b+length(pad)-length(pad)});let task=work(padding);remember(task,padding);spawn(task);await task'''
AWAIT_PENDING='''let padding=range(PADDING);let borrowed=spawn((async fn(){remember_effect("borrowed_hold");tool("borrowed_hold",40)})());let work=choose(async fn(pad,child){match 0{_=>do{let value=await child;value+2+length(pad)-length(pad)}}});let task=work(padding,borrowed);remember(task,padding,borrowed);spawn(task);FINAL_AWAIT'''
GROUP_PENDING='''let padding=range(PADDING);let work=choose(async fn(pad){for n in [1,2]{async with task_group() as group{match n{1=>do{remember_effect("group_body_hold");tool("group_body_hold",40);continue};_=>do{remember_effect("group_body_hold");let value=tool("group_body_hold",40);return value+2+length(pad)-length(pad)}}}};0});let task=work(padding);remember(task,padding);spawn(task);await task'''

def pending(test,padding,target,mode='match',awaited=False,cancel=False,group=False):
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
                    owner_task=task if group else root
                    frame=owned_frame(test,owner_task.runner.iterator,PatternSyntaxFrame)
                    phase='rhs'if mode!='match'and not awaited and not group else'subject'if name=='subject_hold'else'guard'if 'guard'in name else'body'
                    test.assertEqual(frame.phase,phase);test.assertIs(frame.env.get('pad'),pad)
                    if phase=='rhs':
                        test.assertIsNone(frame.bindings)
                        if mode=='assign_pattern':test.assertEqual([frame.env.get('a'),frame.env.get('b')],[1,2])
                        else:test.assertTrue({'a','b'}.isdisjoint(frame.env.values))
                    if phase=='guard':test.assertEqual(frame.index,1 if name=='false_guard_hold'else 2);test.assertEqual(frame.scope.get('value'),42)
                    if target not in ('raw','interpreter'):
                        live=[s for s in states if not s.closed and s.function is functions[0]]
                        if group:
                            from chaos.task_groups import GroupRun
                            test.assertEqual(len(live),2);parents=[s for s in live if s.pending_cursor is root.runner.iterator];test.assertEqual(len(parents),1);parent=parents[0]
                            test.assertIs(type(parent.pending_request),GroupRun);test.assertIs(parent.pending_request.group.body,task);test.assertIs(parent.pending_request.iterator,task.runner.iterator)
                            state=[s for s in live if s is not parent][0];test.assertIsNot(parent.pending_cursor,state.pending_cursor);parent.validate(full=True)
                        else:test.assertEqual(len(live),1);state=live[0]
                        state.validate(full=True);test.assertIs(state.owner,e);test.assertIs(state.pending_cursor,owner_task.runner.iterator);test.assertIs(frame._flow_boundary.flow_state,state);test.assertIsNotNone(frame._flow_pattern_region)
                        test.assertTrue(structural(state.program));test.assertTrue(any(getattr(f,'compiled_flow_state',None)is state for f in owner_task.runner.capture_frames))
                        if task is owner_task:test.assertIs(state.pending_request,requests[future]);test.assertEqual(state.pending_kind,'host')
                        else:test.assertIs(type(state.pending_request),suspension.Await);test.assertIs(state.pending_request.task,borrowed);test.assertIs(root.runner.waiting,borrowed);test.assertEqual(state.pending_kind,'await')
                    else:test.assertEqual(states,[])
                    _,delta,paths=seal(e,(root,borrowed)if borrowed is not None else(root,),None);observations.append((name,delta,paths))
                    if cancel and name=='borrowed_hold':e.suspension.cancel(root);test.assertFalse(borrowed.cancel_event.is_set())
            finally:answer=wait(futures,*args,**kw)
            return answer
        source=GROUP_PENDING if group else AWAIT_PENDING if awaited else ASSIGN_PENDING if mode=='assign_pattern'else BIND_PENDING.replace('DECL','let'if mode=='let_pattern'else'var')if mode!='match'else MATCH_PENDING
        source=source.replace('PADDING',str(padding)).replace('FINAL_AWAIT','await borrowed'if cancel else'await task')
        with patch.object(suspension.ThreadPoolExecutor,'submit',submitting),patch.object(suspension,'wait',waiting):result=('value',serializable(e.run(source)))
        expected=['group_body_hold','group_body_hold']if group else['borrowed_hold']if awaited else['rhs_hold']if mode!='match'else['subject_hold','false_guard_hold','true_guard_hold','body_hold']
        test.assertEqual([row[0]for row in observations],expected);test.assertEqual(schedule['waitBoundaryReleases'],len(expected));test.assertEqual(schedule['shutdownReleases'],0)
    test.assertEqual(e.depth,0);test.assertEqual(e.frames,[]);test.assertIsNone(e.handled_error);test.assertIsNone(e.error_context);test.assertFalse(e.suspension.active);test.assertIsNone(e.suspension.pool)
    if cancel:
        root,pad,borrowed=held[0];test.assertEqual(root.state,'cancelled');test.assertEqual(borrowed.state,'completed');test.assertFalse(borrowed.cancel_event.is_set())
    return (result,e.steps-base[0],effects,trace,debug,observations),states,functions
