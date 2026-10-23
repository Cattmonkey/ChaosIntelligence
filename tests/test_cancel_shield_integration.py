"""Cancellation protection crosses the real Team and durable Session tool paths."""
import threading
import time
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, ToolSpec
from chaos.settings import Settings
from tests.test_session_integration import NativeFixture
from tests.test_task_join_integration import team_definition

class CancelShieldIntegrationTests(unittest.TestCase):
    def test_bound_session_private_handler_drains_without_committing_success(self):
        def choose(body):
            return {'role':'assistant','content':None,'tool_calls':[{'id':'protected-call','type':'function','function':{'name':body['tools'][-1]['function']['name'],'arguments':'{"input":null}'}}]}
        fixture=NativeFixture(choose);self.addCleanup(fixture.close)
        registry,calls=Registry(),[]
        def audit(value,context):
            context.checkpoint();calls.append(value);return value
        registry.register_tool(ToolSpec('fixture.audit',audit))
        source='''var release=false;var protection=null;
async fn handler(value){with cancel_shield() as p{
  protection=p;while !release {await sleep(0.001)};
  tool("fixture.audit","finished");42
}}
agent worker{provider:"model",tools:["fixture.audit"]};
let session=await session_open("protected",worker);
let bound=session_bind(session,[agent_tool("protected_work",handler,capabilities=["fixture.audit"])]);
let job=spawn(bound("work",message_id="one"));
while protection==null {if task_info(job).state=="failed" {await job} else {null};await sleep(0.001)};cancel(job);
while !cancel_shield_info(protection).cancellationDeferred {await sleep(0.001)};
let deferred=cancel_shield_info(protection).cancellationDeferred;
release=true;await task_wait([job]);
[deferred,task_info(job).state,cancel_shield_info(protection).state]'''
        with Framework(settings=fixture.settings,registry=registry) as app:
            app.create_project('session-shield',source=source,capabilities=['session.manage','fixture.audit'])
            run=app.start('session-shield',1,None,mode='source')
            self.assertEqual(run.result(8),[True,'cancelled','closed'])
            self.assertEqual(calls,['finished'])
            row=app.agent_session('session-shield','protected')
            history=app.agent_session_history('session-shield','protected')['items']
            self.assertEqual(row['turn'],0)
            self.assertTrue(history)
            self.assertTrue(all(item['status']!='completed' for item in history))
            self.assertFalse(any(item['message']['role']=='tool' for item in history))
            self.assertEqual(len(fixture.requests),1)

    def test_team_role_host_work_keeps_provenance_during_deferred_cancel(self):
        registry=Registry();entered=threading.Event();release=threading.Event();finished=threading.Event();observed=[]
        def provider(request,context):
            entered.set();deadline=time.monotonic()+5
            try:
                while not release.wait(.002):
                    context.checkpoint()
                    if time.monotonic()>deadline:raise AssertionError('controller did not release protected work')
                context.checkpoint();observed.append(bool(context.cancelled));return Final(42)
            finally:finished.set()
        def wait_entered(value,context):
            return entered.is_set()
        registry.register_provider(ProviderSpec('fixture.worker',provider))
        registry.register_tool(ToolSpec('fixture.entered',wait_entered))
        registry.register_tool(ToolSpec('fixture.release',lambda value,context:release.set()))
        source='''let team=team_from(input);var protection=null;
async fn work(){with cancel_shield() as p{protection=p;await team_role(team,"worker")(null)}}
let job=spawn(work());while !tool("fixture.entered",null) {await sleep(0)};cancel(job);
let pending=task_info(job);tool("fixture.release",null);await task_wait([job]);
[pending.cancellationDeferred,pending.shieldDepth,task_info(job).state,cancel_shield_info(protection).state]'''
        with Framework(settings=Settings(':memory:'),registry=registry) as app:
            app.create_project('team-shield',source=source,capabilities=['fixture.entered','fixture.release'])
            run=app.start('team-shield',1,team_definition(),mode='source')
            try:result=run.result(8)
            finally:release.set()
            self.assertEqual(result,[True,1,'cancelled','closed'])
            self.assertTrue(finished.is_set());self.assertEqual(observed,[False])
            events=app.events('team-shield',run_id=run.id,limit=300)['items']
            roles=[row['payload'] for row in events if row['type']=='team.role.started']
            self.assertEqual(len(roles),1)
            self.assertEqual(roles[0]['rolePath'],['worker'])
            self.assertEqual(roles[0]['roleId'],'worker')
