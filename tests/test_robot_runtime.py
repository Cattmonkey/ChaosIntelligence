"""Engine admission and teardown use real registered host resources."""
import threading,time,unittest
from chaos import Framework,Registry,Settings
from chaos.language import LanguageError,ResourceLimit
from chaos.devices import DeviceSpec
from chaos.robots import RobotSpec
from test_robot_values import Adapter,ACTUATORS,host,run


class RobotRuntimeTests(unittest.TestCase):
    def test_failed_factory_retirement_keeps_physical_alias_quarantined(self):
        calls=[];registry=Registry()
        def factory(ctx):
            calls.append('open');failure=LanguageError('original failed open',code='robot_open_failed')
            failure._chaos_robot_retirement_failed=True
            raise failure
        registry.register_robot(RobotSpec('rig',factory,ACTUATORS,resource_id='serial:failed'))
        registry.register_robot(RobotSpec('alias',factory,ACTUATORS,resource_id='serial:failed'))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            self.assertEqual(run(app,'[try{await robot_open("rig")}catch e{e.code},try{await robot_open("alias")}catch e{e.code}]'),['robot_open_failed','robot_busy'])
        self.assertEqual(calls,['open'])

    def test_robot_and_hid_share_one_physical_lease(self):
        log=[];registry=Registry();registry.register_robot(RobotSpec('rig',lambda ctx:Adapter(log),ACTUATORS,resource_id='serial:fixture'))
        class HID:
            def handle(self,*args):return {'protocol':'chaos-hid/1','id':args[0],'status':'acknowledged'}
            def close(self):log.append(('hid-close',{}))
        registry.register_device(DeviceSpec('desk',lambda ctx:HID(),resource_id='serial:fixture'))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            self.assertEqual(run(app,'let r=await robot_open("rig");let denied=try{await device_open("desk")}catch e{e.code};await robot_close(r);let d=await device_open("desk");[denied,type_of(d)]',['robot.control','device.control']),['device_busy','Device'])

    def test_unknown_act_outcome_is_retired_and_never_replayed(self):
        log=[]
        class Broken(Adapter):
            def handle(self,operation,payload,ctx):
                if operation=='act':self.log.append(('act',payload));raise LanguageError('unknown write')
                return super().handle(operation,payload,ctx)
        with host(log,Broken) as app:
            self.assertEqual(run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let c=robot_command(o,{pan:0,tilt:0});try{await r(c)}catch e{null};[r.status,c.used,try{await r(c)}catch e{e.code}]'),['uncertain',True,'robot_state_error'])
        self.assertEqual(sum(x[0]=='act' for x in log),1);self.assertEqual(sum(x[0]=='close' for x in log),1)

    def test_cached_packet_cannot_refresh_after_admission(self):
        log=[]
        class Cached(Adapter):
            def handle(self,operation,payload,ctx):
                if operation=='observe':
                    if not hasattr(self,'packet'):self.packet=super().handle(operation,payload,ctx)
                    return self.packet
                return super().handle(operation,payload,ctx)
        with host(log,Cached) as app:self.assertEqual(run(app,'let r=await robot_open("rig");let o=await robot_observe(r);await r(robot_command(o,{pan:0,tilt:0}));try{await robot_observe(r)}catch e{e.code}'),'robot_stale_observation')

    def test_packet_timestamp_is_not_replaced_by_observe_time(self):
        log=[]
        class Old(Adapter):
            def handle(self,operation,payload,ctx):
                result=super().handle(operation,payload,ctx)
                if operation=='observe':result['receivedAtNs']-=2000000000
                return result
        with host(log,Old) as app:self.assertEqual(run(app,'let r=await robot_open("rig");try{await robot_observe(r)}catch e{e.code}'),'robot_expired')
        self.assertEqual(sum(x[0]=='close' for x in log),1)

    def test_host_receipt_status_never_claims_verified_motion(self):
        log=[]
        class Wrong(Adapter):
            def handle(self,operation,payload,ctx):
                result=super().handle(operation,payload,ctx)
                if operation=='act':return {'status':'verified'}
                return result
        with host(log,Wrong) as app:self.assertEqual(run(app,'let r=await robot_open("rig");let o=await robot_observe(r);try{await r(robot_command(o,{pan:0,tilt:0}))}catch e{e.code}'),'robot_error')

    def test_ordinary_context_error_and_control_abort_close_once(self):
        log=[]
        with host(log) as app:self.assertEqual(run(app,'let p=prompt_tag();call_prompt(p,async fn(){async with await robot_open("rig") as r{abort_prompt(p,42)}})'),42)
        self.assertEqual(sum(x[0]=='close' for x in log),1)
        log=[]
        with host(log) as app:self.assertEqual(run(app,'try{async with await robot_open("rig") as r{throw "body"}}catch e{e.value}'),'body')
        self.assertEqual(sum(x[0]=='close' for x in log),1)

    def test_async_exit_stack_and_explicit_close_are_idempotent(self):
        log=[]
        with host(log) as app:self.assertEqual(run(app,'async with exit_stack(true) as s{let r=await s.enter_async_context(await robot_open("rig"));await robot_close(r);42}'),42)
        self.assertEqual(sum(x[0]=='close' for x in log),1)

    def test_hard_budget_still_retires_host_resource(self):
        log=[]
        with host(log) as app:
            with self.assertRaises(Exception):run(app,'async with await robot_open("rig") as r{while true{1}}')
        self.assertEqual(sum(x[0]=='close' for x in log),1)

    def test_project_authority_rechecked_at_adapter_byte_edge(self):
        log=[];registry=Registry();shared={}
        class Revokes(Adapter):
            def handle(self,operation,payload,ctx):
                if operation=='act':
                    current=shared['app'].get_project(ctx.project_id)
                    shared['app'].edit_program(ctx.project_id,current['version'],source=current['source'],capabilities=[])
                    ctx.checkpoint()
                return super().handle(operation,payload,ctx)
        registry.register_robot(RobotSpec('rig',lambda ctx:Revokes(log),ACTUATORS))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            shared['app']=app
            self.assertEqual(run(app,'let r=await robot_open("rig");let o=await robot_observe(r);try{await r(robot_command(o,{pan:0,tilt:0}))}catch e{e.code}'),'capability_error')
        self.assertFalse(any(x[0]=='act' for x in log));self.assertEqual(sum(x[0]=='close' for x in log),1)

    def test_cancelled_controller_retires_before_parent_reopens_alias(self):
        log=[]
        with host(log) as app:
            value=run(app,'var ready=false;async fn controller(){async with await robot_open("rig") as r{ready=true;await sleep(10)}}let t=spawn(controller());while !ready{await sleep(0.001)};cancel(t);while task_info(t).state!="cancelled"{await sleep(0.001)};let again=await robot_open("alias");[task_info(t).state,type_of(again)]')
        self.assertEqual(value,['cancelled','Robot']);self.assertEqual([x[0] for x in log],['open','close','open','close'])

    def test_cancelled_stack_retires_native_entry_before_parent_continues(self):
        log=[]
        with host(log) as app:
            value=run(app,'var ready=false;async fn controller(){async with exit_stack(true) as s{await s.enter_async_context(await robot_open("rig"));ready=true;await sleep(10)}}let t=spawn(controller());while !ready{await sleep(0.001)};cancel(t);while task_info(t).state!="cancelled"{await sleep(0.001)};let again=await robot_open("alias");type_of(again)')
        self.assertEqual(value,'Robot');self.assertEqual([x[0] for x in log],['open','close','open','close'])

    def test_native_robot_push_rejected_before_registration(self):
        log=[]
        with host(log) as app:self.assertEqual(run(app,'let r=await robot_open("rig");async with exit_stack(true) as s{[try{s.push(r)}catch e{e.code},try{s.push_async_exit(r)}catch e{e.code}]}'),['context_error','context_error'])
        self.assertEqual([x[0] for x in log],['open','close'])

    def test_observe_timeout_retires_adapter_before_caller_recovers(self):
        log=[]
        class Timeout(Adapter):
            def handle(self,operation,payload,ctx):
                if operation=='observe':raise LanguageError('timeout',code='robot_timeout')
                return super().handle(operation,payload,ctx)
        with host(log,Timeout) as app:self.assertEqual(run(app,'let r=await robot_open("rig");try{await robot_observe(r)}catch e{null};let again=await robot_open("alias");type_of(again)'),'Robot')
        self.assertEqual([x[0] for x in log],['open','close','open','close'])

    def test_caught_physical_recording_attempt_cannot_seal_skill(self):
        log=[]
        with host(log) as app:
            p=app.create_project('record',source='async fn action(value){try{await robot_open("rig")}catch e{null}};await record_skill(action)',capabilities=['robot.control'])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='source').wait()['status'],'failed')
            rows=app.modes('record')['items'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'incomplete')
        self.assertEqual(log,[])
