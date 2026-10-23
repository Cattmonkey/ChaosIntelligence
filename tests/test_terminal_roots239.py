"""Consumed execution roots retire; observable handle payloads and authority remain."""
from contextlib import nullcontext
import gc
import unittest
from unittest.mock import patch
import weakref

from chaos import Evaluator, Framework, Registry, Settings, ToolCall, Final, LanguageError, ResourceLimit
from chaos.language import Native, serializable
from chaos import suspension
from _effect_schedule158 import effect_schedule
from _terminal_roots_reference239 import original_step
from test_executable231 import no_frontend
from test_native_jit import NATIVE

NEW_CONSUMER_SOURCE='''var calls=[];
async fn action(){calls=append(calls,"task");return_values(40,2)};
let t=action();let cold=calls;let a=await t;let b=await t;
fn* stream(){calls=append(calls,"gen");yield 11;return_values(41,3)};
let s=stream();let s1=next(s);let s2=next(s);let s3=next(s);
async fn* both(){calls=append(calls,"asyncgen");await sleep(0);yield 22;return_values(42,4)};
let g=both();let g1=await anext(g);let g2=await anext(g);let g3=await anext(g);
[cold,a,b,multiple_value_list(t),s1,s2,s3,multiple_value_list(s),g1,g2,g3,multiple_value_list(g),calls,task_info(t).state]'''
NEW_CONSUMER_EXPECTED=[[],40,40,[40,2],{'done':False,'value':11},{'done':True,'value':41},{'done':True,'value':41},[41,3],{'done':False,'value':22},{'done':True,'value':42},{'done':True,'value':42},[42,4],['task','gen','asyncgen'],'completed']

TASK='''fn make(){let large=range(4096);let callback=fn(){length(large)};remember239("value",callback);(async fn(c){c();42})(callback)};
let h=make();probe239("value",h);let first=await h;probe239("value",h);[first,await h]'''


def execution(source,baseline=False,extra=None,**options):
    refs={};observations=[];handles=[];effects=[];initial_cursor_ids={}
    e=Evaluator(step_limit=100000,timeout=20,tool_handler=lambda name,value:effects.append((name,value)) or value,**options)
    def remember(label,value):refs[label]=weakref.ref(value);return None
    def probe(label,handle):
        # Test-only GC handles optional cycles; no runtime GC setting is changed.
        gc.collect();handles.append(handle);runner=handle.runner;cursor=runner.iterator
        initial_cursor_id=initial_cursor_ids.setdefault(id(handle),id(cursor))
        observations.append({'alive':refs[label]() is not None,'epoch':e.suspension.epoch,'active':e.suspension.active,
            'state':handle.state,'retired':getattr(runner,'execution_retired',False),'captures':len(handle.capture_roots),
            'initialCursorId':initial_cursor_id,'cursorId':id(cursor),'cursorType':type(cursor).__name__,
            'cursorClosed':getattr(cursor,'closed',False),'cursorFrames':len(getattr(cursor,'frames',())),
            'bridgeRetired':getattr(cursor,'root_bridge',None) is None,'frames':len(runner.frames),'captureFrames':len(runner.capture_frames)})
        return None
    def same(left,right):return left is right
    e.env.bind('remember239',Native('remember239',remember,e,captures=()))
    e.env.bind('probe239',Native('probe239',probe,e,captures=()))
    e.env.bind('same239',Native('same239',same,e,captures=()))
    if extra:extra(e,refs,observations,handles)
    with original_step() if baseline else nullcontext():value=serializable(e.run(source))
    return value,observations,handles,e,effects


class TerminalRoots239(unittest.TestCase):
    def test_same_epoch_consumed_task_capture_releases_against_frozen_original_algorithm(self):
        old=execution(TASK,baseline=True);new=execution(TASK)
        self.assertEqual(old[0],new[0]);self.assertEqual(new[0],[42,42]);self.assertEqual(old[4],new[4])
        self.assertEqual([x['alive'] for x in old[1]],[True,True])
        self.assertEqual([x['alive'] for x in new[1]],[True,False])
        self.assertTrue(all(x['active'] for x in new[1]));self.assertEqual(len({x['epoch'] for x in new[1]}),1)
        self.assertFalse(new[1][0]['retired']);self.assertTrue(new[1][1]['retired']);self.assertTrue(new[1][1]['bridgeRetired']);self.assertTrue(new[1][1]['cursorClosed']);self.assertEqual(new[1][1]['cursorFrames'],0);self.assertEqual(new[1][1]['cursorType'],'ExecutionCursor');self.assertEqual(new[1][0]['cursorId'],new[1][1]['cursorId']);self.assertEqual(new[1][1]['captures'],0)
        self.assertIn(new[2][-1],new[3].suspension.tasks,'Registry identity remains; cleanup did not simply delete the handle')

    def test_sync_generator_releases_only_after_terminal_completion_and_keeps_final_values(self):
        source='''fn make(){let large=range(4096);let c=fn(){length(large)};remember239("value",c);(fn*(f){yield f();return_values(42,43)})(c)};
let h=make();probe239("value",h);let a=next(h);probe239("value",h);let b=next(h);probe239("value",h);[a,b,next(h),multiple_value_list(h)]'''
        value,seen,handles,e,_=execution(source)
        self.assertEqual(value,[{'done':False,'value':4096},{'done':True,'value':42},{'done':True,'value':42},[42,43]])
        self.assertEqual([x['alive'] for x in seen],[True,True,False]);self.assertEqual([x['retired'] for x in seen],[False,False,True]);self.assertIn(handles[-1],e.suspension.generators)
        self.assertEqual(len({x['cursorId'] for x in seen}),1);self.assertTrue(seen[-1]['cursorClosed']);self.assertEqual(seen[-1]['cursorFrames'],0);self.assertTrue(seen[-1]['bridgeRetired']);self.assertEqual(seen[-1]['cursorType'],'ExecutionCursor')

    def test_async_generator_same_epoch_release_preserves_operation_tasks_and_final_values(self):
        source='''fn make(){let large=range(4096);let c=fn(){length(large)};remember239("value",c);(async fn*(f){await sleep(0);yield f();return_values(42,43)})(c)};
let h=make();probe239("value",h);let a=await anext(h);probe239("value",h);let b=await anext(h);probe239("value",h);[a,b,await anext(h),multiple_value_list(h)]'''
        value,seen,_,_,_=execution(source)
        self.assertEqual(value,[{'done':False,'value':4096},{'done':True,'value':42},{'done':True,'value':42},[42,43]])
        self.assertEqual([x['alive'] for x in seen],[True,True,False]);self.assertTrue(seen[-1]['retired'])
        self.assertEqual(len({x['cursorId'] for x in seen}),1);self.assertTrue(seen[-1]['cursorClosed']);self.assertEqual(seen[-1]['cursorFrames'],0);self.assertTrue(seen[-1]['bridgeRetired']);self.assertEqual(seen[-1]['cursorType'],'ExecutionCursor')

    def test_cold_task_cancel_and_cold_stream_close_do_not_start_user_code(self):
        for flavor,make,close in [('task','async fn(f){tool("forbidden",f());42}','cancel(h);await sleep(0)'),('gen','fn*(f){tool("forbidden",f());yield 1}','close(h)'),('asyncgen','async fn*(f){tool("forbidden",f());yield 1}','await aclose(h)')]:
            source='fn make(){let pad=range(4096);let c=fn(){length(pad)};remember239("value",c);('+make+')(c)};let h=make();'+close+';probe239("value",h);42'
            with self.subTest(flavor=flavor):
                value,seen,_,_,effects=execution(source);self.assertEqual(value,42);self.assertEqual(effects,[])
                if flavor=='task':
                    original=execution(source,baseline=True)
                    self.assertEqual(value,original[0]);self.assertEqual(effects,original[4]);self.assertTrue(original[1][0]['alive'])
                    self.assertTrue(seen[0]['alive']);self.assertFalse(seen[0]['retired']);self.assertFalse(seen[0]['bridgeRetired']);self.assertEqual(seen[0]['cursorType'],'ExecutionCursor')
                else:
                    self.assertEqual([x['alive'] for x in seen],[False]);self.assertTrue(seen[0]['retired'])

    def test_full_consumers_retire_after_unlock_but_short_circuit_borrows_live_stream(self):
        prefix='fn make(){let pad=range(4096);let c=fn(){length(pad)};remember239("value",c);(fn*(f){yield f();yield 42})(c)};let h=make();'
        for consume,expected in [('collect(h)',[4096,42]),('map(fn(x){x+1},h)',[4097,43]),('fold(fn(a,x){a+x},0,h)',4138)]:
            with self.subTest(consumer=consume):
                value,seen,_,_,_=execution(prefix+'let value='+consume+';probe239("value",h);value')
                self.assertEqual(value,expected);self.assertFalse(seen[-1]['alive']);self.assertTrue(seen[-1]['retired'])
        async_source='fn make(){let pad=range(4096);let c=fn(){length(pad)};remember239("value",c);(async fn*(f){yield f();yield 42})(c)};let h=make();let value=await acollect(h);probe239("value",h);value'
        value,seen,_,_,_=execution(async_source);self.assertEqual(value,[4096,42]);self.assertFalse(seen[-1]['alive']);self.assertTrue(seen[-1]['retired'])
        value,seen,_,_,_=execution(prefix+'let first=any(fn(x){true},h);probe239("value",h);let tail=collect(h);probe239("value",h);[first,tail]')
        self.assertEqual(value,[True,[42]]);self.assertEqual([x['alive'] for x in seen],[True,False]);self.assertEqual([x['retired'] for x in seen],[False,True])

    def test_ignored_GeneratorExit_keeps_actual_live_native_carrier_conservatively(self):
        from chaos.execution_retirement import retire_execution
        class Payload:pass
        e=Evaluator();scheduler=e.suspension;scheduler.begin();payload=Payload()
        def carrier():
            try:yield suspension.CHECKPOINT
            except GeneratorExit:yield suspension.CHECKPOINT
        native=carrier();task=scheduler.task(native,capture_roots=(payload,));cursor=task.runner.iterator
        try:
            self.assertIs(cursor.send(None),suspension.CHECKPOINT)
            with self.assertRaises(RuntimeError) as caught:cursor.close()
            self.assertTrue(cursor.closed);self.assertEqual(cursor.frames,[]);self.assertIsNotNone(native.gi_frame)
            scheduler._finish_runner(task.runner,'failed',error=caught.exception);task.observed=True
            self.assertFalse(retire_execution(scheduler,task));self.assertIs(task.runner.iterator,cursor);self.assertEqual(task.capture_roots,(payload,));self.assertIs(task.runner.error,caught.exception)
            self.assertIs(cursor.root_bridge.iterator,native);self.assertIsNotNone(native.gi_frame)
        finally:
            native.close();scheduler.finish()
        self.assertIsNone(native.gi_frame)

    def test_late_live_or_foreign_auxiliary_prevents_partial_detachment_and_preserves_native_alias(self):
        from chaos.execution_frames import ensure_cursor
        from chaos.execution_retirement import retire_execution
        class Payload:pass
        for mode in ('live-owned','foreign'):
            with self.subTest(mode=mode):
                e=Evaluator();scheduler=e.suspension;scheduler.begin();foreign=Evaluator();foreign.suspension.begin();payload=Payload()
                def parent():
                    yield suspension.CHECKPOINT
                    return 42
                def auxiliary():
                    yield suspension.CHECKPOINT
                    return 99
                native=parent();task=scheduler.task(native,capture_roots=(payload,));cursor=task.runner.iterator;bridge=cursor.root_bridge
                child=ensure_cursor(e if mode=='live-owned' else foreign,auxiliary());child_bridge=child.root_bridge
                try:
                    self.assertIs(cursor.send(None),suspension.CHECKPOINT)
                    with self.assertRaises(StopIteration) as completed:cursor.send(None)
                    self.assertEqual(completed.exception.value,42);self.assertTrue(cursor.closed);self.assertEqual(cursor.frames,[]);self.assertIsNone(native.gi_frame)
                    self.assertIs(child.send(None),suspension.CHECKPOINT);self.assertFalse(child.closed);self.assertIsNotNone(child_bridge.iterator.gi_frame)
                    # Adversarial retained-link boundary: all terminal/closed
                    # state above came from the original real transports.
                    bridge.pending_cursor=child;child.parent_bridge=bridge
                    scheduler._finish_runner(task.runner,'completed',42);task.observed=True
                    self.assertFalse(retire_execution(scheduler,task));self.assertFalse(task.runner.execution_retired)
                    self.assertIs(cursor.root_bridge,bridge);self.assertIs(bridge.pending_cursor,child);self.assertIs(child.root_bridge,child_bridge)
                    self.assertIs(task.runner.iterator,cursor);self.assertEqual(task.capture_roots,(payload,));self.assertIs(bridge.iterator,native)
                    # Original close clears precisely its own pending link.
                    child.close();self.assertIsNone(bridge.pending_cursor);self.assertIsNone(child_bridge.iterator.gi_frame)
                    self.assertTrue(retire_execution(scheduler,task));self.assertTrue(task.runner.execution_retired);self.assertEqual(task.runner.result,42)
                    self.assertIsNone(cursor.root_bridge);self.assertIs(task.runner.iterator,cursor);self.assertTrue(cursor.closed);self.assertEqual(cursor.frames,[]);self.assertEqual(task.capture_roots,())
                    # An externally retained original carrier/bridge is not
                    # rewritten or consumed again by the allowed retirement.
                    self.assertIs(bridge.iterator,native);self.assertIs(child.root_bridge,child_bridge);self.assertIsNone(native.gi_frame)
                    with self.assertRaises(StopIteration):native.send(None)
                finally:
                    child.close();native.close();scheduler.finish();foreign.suspension.finish()

    def test_task_result_alias_is_retained_after_capture_roots_retire(self):
        source='''fn make(){let pad=range(4096);let c=fn(){length(pad)};remember239("value",c);(async fn(f){return_values(f,99)})(c)};
let h=make();let first=await h;let second=await h;probe239("value",h);[same239(first,second),first(),second(),length(multiple_value_list(h))]'''
        value,seen,handles,_,_=execution(source)
        self.assertEqual(value,[True,4096,4096,2]);self.assertTrue(seen[0]['alive']);self.assertTrue(seen[0]['retired']);self.assertEqual(handles[0].capture_roots,())

    def test_original_failure_payload_remains_visible_on_repeated_await(self):
        source='''fn make(){let pad=range(4096);let c=fn(){length(pad)};remember239("value",c);(async fn(f){throw f})(c)};
let h=make();let a=try{await h}catch e{e.value};let b=try{await h}catch e{e.value};probe239("value",h);[same239(a,b),a(),b(),task_info(h).state]'''
        value,seen,handles,_,_=execution(source)
        self.assertEqual(value,[True,4096,4096,'failed']);self.assertTrue(seen[0]['alive']);self.assertEqual(handles[0].capture_roots,());self.assertIsNotNone(handles[0].runner.error)

    def test_pending_real_future_keeps_captures_until_worker_barrier_finishes(self):
        waits=[]
        def extra(e,refs,observations,handles):
            original_wait=suspension.wait
            def waiting(futures,*args,**kwargs):
                futures=tuple(futures)
                task=next((t for t in e.suspension.tasks if t.runner.future in futures and t.runner.future is not None),None)
                if task is not None:
                    self.assertFalse(task.runner.future.done());self.assertIsNotNone(refs['value']());self.assertTrue(task.capture_roots);self.assertFalse(getattr(task.runner,'execution_retired',False));waits.append(task.id)
                return original_wait(futures,*args,**kwargs)
            patches.append(patch.object(suspension,'wait',waiting));patches[-1].start()
        source='''fn make(){let pad=range(4096);let c=fn(){length(pad)};remember239("value",c);(async fn(f){tool("hold",f());42})(c)};let h=make();let value=await h;probe239("value",h);value'''
        patches=[]
        with effect_schedule('first_wait'):
            try:value,seen,_,_,effects=execution(source,extra=extra)
            finally:
                for item in reversed(patches):item.stop()
        self.assertEqual(value,42);self.assertTrue(waits);self.assertEqual(effects,[('hold',4096)]);self.assertFalse(seen[-1]['alive']);self.assertTrue(seen[-1]['retired'])

    def test_group_gather_channel_metadata_and_completion_order_survive_retirement(self):
        source='''let c=channel(1);async fn send(){await channel_send(c,42);7};
let result=async with task_group() as group{let sender=group.spawn(send());let value=await channel_receive(c);let repeated=await gather([sender,sender]);[value.value,repeated,task_info(sender).groupId==task_group_info(group).id]};channel_close(c);result'''
        old=execution(source,baseline=True);new=execution(source)
        self.assertEqual(old[0],new[0]);self.assertEqual(new[0],[42,[7,7],True]);self.assertFalse(new[3].suspension.active)
        self.assertTrue(all(t.state in suspension.TERMINAL and t.terminal_order>0 for t in new[3].suspension.tasks))

    def test_reentrant_driver_never_retires_the_still_running_primary_task(self):
        active=[]
        def extra(e,*unused):
            def nested(function):
                task=e.suspension.current.task;iterator=task.runner.iterator
                value=e.invoke(function,[])
                self.assertIs(task.runner.iterator,iterator);self.assertIsNotNone(iterator);self.assertFalse(getattr(task.runner,'execution_retired',False));active.append(task)
                return value
            e.env.bind('nested239',Native('nested239',nested,e,captures=()))
        source='async fn parent(){let value=nested239(fn(){40});await sleep(0);value+2};await parent()'
        value,_,_,_,_=execution(source,extra=extra);self.assertEqual(value,42);self.assertTrue(active);self.assertTrue(getattr(active[0].runner,'execution_retired',False))

    def test_hard_budget_and_error_priority_keep_original_observable_effects(self):
        source='async fn loop(){tool("prefix",1);try{while true{1}}finally{tool("forbidden",2)}};await loop()'
        records=[]
        for baseline in (True,False):
            effects=[];e=Evaluator(step_limit=200,timeout=20,tool_handler=lambda n,v:effects.append((n,v)) or v)
            with effect_schedule('already_done'),original_step() if baseline else nullcontext():
                with self.assertRaises(ResourceLimit) as caught:e.run(source)
            records.append((caught.exception.record(),e.steps,effects))
        self.assertEqual(records[0],records[1]);self.assertEqual(records[1][2],[('prefix',1)])

    def test_source_and_source_free_executable_share_the_same_persistent_handle_semantics(self):
        from chaos.executable_image import compile_executable,load_executable
        self.assertEqual(serializable(Framework.evaluate(NEW_CONSUMER_SOURCE)),NEW_CONSUMER_EXPECTED)
        image=compile_executable(NEW_CONSUMER_SOURCE)
        with no_frontend():self.assertEqual(serializable(Framework.evaluate(load_executable(image))),NEW_CONSUMER_EXPECTED)

    def test_Agent_tool_and_software_Robot_effects_remain_once_on_original_host(self):
        import test_robot_values as robots
        from chaos.robots import RobotSpec
        calls=[];steps=[];log=[];registry=Registry()
        @registry.tool('fixture.retire239.echo',effect='write')
        def echo(value,ctx):ctx.checkpoint();calls.append(value);return value+2
        @registry.provider('fixture.retire239')
        def provider(request,ctx):
            ctx.checkpoint();steps.append(request.step)
            return ToolCall('fixture.retire239.echo',request.input) if request.step==1 else Final(request.history[-1]['output'])
        def factory(ctx):ctx.checkpoint();log.append(('open',{}));return robots.Adapter(log)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:retire239'))
        source='''agent worker{provider:"fixture.retire239",tools:["fixture.retire239.echo"]};async fn action(){worker(input)};let t=action();let first=await t;let second=await t;let status=async with await robot_open("rig") as r{let o=await robot_observe(r);let receipt=await r(robot_command(o,{pan:0,tilt:1}));receipt.status};[first,second,status]'''
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('retirement-host',source=source,capabilities=['fixture.retire239.echo','robot.control']);run=app.start(p['id'],p['version'],40,mode='source');self.assertEqual(run.result(15),[42,42,'sent']);self.assertFalse(app._engine.active)
        self.assertEqual(calls,[40]);self.assertEqual(steps,[1,2]);self.assertEqual([name for name,_ in log],['open','observe','act','close'])


@unittest.skipUnless(NATIVE,'actual optional LLVM backend; portable contract is separately required')
class TerminalRootsNative239(unittest.TestCase):
    def test_compiled_four_flavors_retire_roots_and_keep_full_results(self):
        from test_native_objects236 import run_four,FOUR_EXPECTED
        from chaos.jit_flow import info
        value,e,functions,states=run_four(None,'native-flow');self.assertEqual(value,FOUR_EXPECTED)
        self.assertTrue(states);self.assertTrue(all(s.closed and s.domain.closed and s.domain.live_count==0 for s in states))
        self.assertTrue(all(info(e,f)['nativeResumes']>0 for f in functions))
        self.assertTrue(all(not t.capture_roots for t in e.suspension.tasks if t.state in suspension.TERMINAL and getattr(t.runner,'execution_retired',False)))
