"""Real registered deterministic Agent and software Robot compose before reusable pure suffix."""
import unittest
from chaos import Final,Framework,Registry,Settings
from chaos.robots import RobotSpec
import test_robot_values as robots

SOURCE='''agent worker{provider:"fixture.continuation227",tools:[]};async fn prepare(){let answer=worker(40);async with await robot_open("rig") as r{let observation=await robot_observe(r);let receipt=await r(robot_command(observation,{pan:0,tilt:1}));[answer,receipt.status]}};let prepared=await prepare();var saved=null;let p=prompt_tag();fn combine(prefix,n){[prefix[0]+n,prefix[1]]};let initial=call_prompt(p,fn(){let x=capture_continuation(p,fn(k){saved=k;1});combine(prepared,x)});async fn wrong_holder(){try{saved(99)}catch error{error.code}};let rejected=await wrong_holder();[initial,saved(2),saved(3),rejected]'''
EXPECTED=[[43,'sent'],[44,'sent'],[45,'sent'],'ownership_error']

class ContinuationAgentRobotTask227(unittest.TestCase):
    def test_completed_agent_robot_task_prefix_is_not_replayed_and_foreign_task_cannot_resume(self):
        registry=Registry();log=[];requests=[]
        def factory(context):context.checkpoint();log.append(('open',{}));return robots.Adapter(log)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:continuation227'))
        @registry.provider('fixture.continuation227')
        def provider(request,context):
            context.checkpoint();requests.append((request.step,request.input));return Final(request.input+2)
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            project=app.create_project('continuation-composition227',source=SOURCE,capabilities=['robot.control'])
            run=app.start(project['id'],project['version'],None,mode='source')
            self.assertEqual(run.result(15),EXPECTED)
        self.assertEqual(requests,[(1,40)])
        self.assertEqual([name for name,_ in log],['open','observe','act','close'])
        self.assertEqual(sum(name=='act' for name,_ in log),1)
