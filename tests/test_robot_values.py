"""Native Robot contracts with registered deterministic adapters only."""
import time,unittest,uuid
from chaos import Framework,Registry,Settings
from chaos.language import Evaluator,LanguageError
from chaos.robots import RobotSpec

ACTUATORS={'pan':{'minimum':-1,'maximum':1,'unit':'normalized'},'tilt':{'minimum':-1,'maximum':1,'unit':'normalized'}}


class Adapter:
    def __init__(self,log,fail=None):self.log=log;self.fail=fail;self.sequence=0;self.closed=False
    def handle(self,operation,payload,context):
        context.checkpoint();self.log.append((operation,dict(payload)))
        if self.fail and operation=='act':raise self.fail
        if operation=='observe':
            self.sequence+=1
            return {'sequence':self.sequence,'receivedAtNs':time.monotonic_ns(),'readings':{'angle':{'value':self.sequence,'unit':'degree'}}}
        return {'status':'sent' if operation=='act' else 'stopSent'}
    def close(self):
        if not self.closed:self.closed=True;self.log.append(('close',{}))


def host(log,adapter_type=Adapter,resource_id='fixture:robot'):
    registry=Registry()
    def factory(ctx):ctx.checkpoint();log.append(('open',{}));return adapter_type(log)
    registry.register_robot(RobotSpec('rig',factory,ACTUATORS,profile='fixture/1',resource_id=resource_id))
    registry.register_robot(RobotSpec('alias',factory,ACTUATORS,profile='fixture/1',resource_id=resource_id))
    return Framework(registry=registry,settings=Settings(':memory:'))


def run(app,source,caps=None):
    p=app.create_project('r'+uuid.uuid4().hex,source=source,capabilities=['robot.control'] if caps is None else caps)
    return app.start(p['id'],p['version'],None,mode='source').result()


class RobotValueTests(unittest.TestCase):
    def test_five_operations_are_cold_and_constructors_pure(self):
        log=[]
        with host(log) as app:
            self.assertEqual(run(app,'type_of(robot_open("rig"))'),'Task');self.assertEqual(log,[])
            value=run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let c=robot_command(o,{pan:0,tilt:1});[type_of(robot_observe(r)),type_of(r(c)),type_of(robot_stop(r)),type_of(robot_close(r)),robot_command_info(c).used]')
        self.assertEqual(value,['Task','Task','Task','Task',False]);self.assertEqual([x[0] for x in log],['open','observe','close'])

    def test_native_types_profile_and_truthful_receipt(self):
        log=[]
        with host(log) as app:
            value=run(app,'async with await robot_open("rig") as r{let o=await robot_observe(r);let c=robot_command(o,{pan:0.5,tilt:-0.5});let receipt=await r(c);[type_of(r),type_of(o),type_of(c),type_of(receipt),r.profile,o.readings.angle.unit,c.used,receipt.status,receipt.targetVerified,receipt.commandId==c.id]}')
        self.assertEqual(value,['Robot','RobotObservation','RobotCommand','RobotReceipt','fixture/1','degree',True,'sent',False,True]);self.assertEqual([x[0] for x in log],['open','observe','act','close'])

    def test_channels_are_generic_exact_and_finite(self):
        log=[]
        with host(log) as app:
            value=run(app,'let r=await robot_open("rig");let o=await robot_observe(r);[try{robot_command(o,{left:0,right:0})}catch e{e.code},try{robot_command(o,{pan:true,tilt:0})}catch e{e.code},try{robot_command(o,{pan:2,tilt:0})}catch e{e.code},robot_command(o,{pan:0,tilt:0}).channels]')
        self.assertEqual(value,['argument_error']*3+[{'pan':0,'tilt':0}]);self.assertFalse(any(x[0]=='act' for x in log))

    def test_argument_limits_reject_bool_and_invalid_ranges(self):
        log=[]
        with host(log) as app:
            source='let r=await robot_open("rig");let o=await robot_observe(r);['+','.join('try{'+s+'}catch e{e.code}' for s in ['robot_observe(r,true)','robot_observe(r,0)','robot_observe(r,10001)','robot_command(o,{pan:0,tilt:0},true)','robot_command(o,{pan:0,tilt:0},0)','robot_command(o,{pan:0,tilt:0},1001)'])+']'
            self.assertEqual(run(app,source),['argument_error']*6)

    def test_command_aliases_and_one_observation_admission(self):
        log=[]
        with host(log) as app:
            value=run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let a=robot_command(o,{pan:0,tilt:0});let b=robot_command(o,{pan:1,tilt:0});let saved=r(a);await saved;[a.used,try{await r(a)}catch e{e.code},try{await r(b)}catch e{e.code},r.latestObservationId]')
        self.assertEqual(value,[True,'robot_command_used','robot_stale_observation',None]);self.assertEqual(sum(x[0]=='act' for x in log),1)

    def test_next_observation_invalidates_old_commands(self):
        log=[]
        with host(log) as app:self.assertEqual(run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let c=robot_command(o,{pan:0,tilt:0});await robot_observe(r);[try{await r(c)}catch e{e.code},c.used]'),['robot_stale_observation',False])

    def test_held_cold_command_does_not_refresh_ttl(self):
        log=[]
        with host(log) as app:self.assertEqual(run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let c=robot_command(o,{pan:0,tilt:0},1);let pending=r(c);await sleep(0.01);[try{await pending}catch e{e.code},c.used]'),['robot_expired',False])
        self.assertFalse(any(x[0]=='act' for x in log))

    def test_info_and_native_members_are_readonly_snapshots(self):
        log=[]
        with host(log) as app:
            value=run(app,'let r=await robot_open("rig");var copy=robot_info(r);copy=set(copy,"status","fake");[r.status,try{r.status="fake"}catch e{e.code},try{r.lease}catch e{e.code},try{delattr(r,"name")}catch e{e.code},length(dir(r))]')
        self.assertEqual(value,['open','attribute_error','attribute_error','attribute_error',8])

    def test_stop_close_and_new_session(self):
        log=[]
        with host(log) as app:self.assertEqual(run(app,'let r=await robot_open("rig");let stop=await robot_stop(r);await robot_close(r);await robot_close(r);let again=await robot_open("alias");[stop.status,stop.targetVerified,r.status,r.id!=again.id]'),['stopSent',False,'closed',True])
        self.assertEqual(sum(x[0]=='close' for x in log),2)

    def test_no_handler_and_reserved_permission_are_not_tools(self):
        with self.assertRaises(LanguageError):Evaluator().run('await robot_open("rig")')
        log=[]
        with host(log) as app:
            self.assertEqual(run(app,'try{await robot_open("rig")}catch e{e.code}',[]),'capability_error')
            self.assertEqual(run(app,'try{tool("robot.control",{})}catch e{e.code}'),'program_error')
        self.assertEqual(log,[])

    def test_huge_finite_integer_channels_reject_before_effect(self):
        log=[]
        with host(log) as app:self.assertEqual(run(app,'let r=await robot_open("rig");let o=await robot_observe(r);try{robot_command(o,{pan:2**2000,tilt:0})}catch e{e.code}'),'argument_error')
        self.assertFalse(any(x[0]=='act' for x in log))
