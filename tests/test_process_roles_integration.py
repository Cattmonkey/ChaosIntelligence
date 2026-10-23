"""Actual Process host/agent barriers and fresh-turn durable role routing."""
import threading
import time
import unittest

from chaos import Final, Framework, Registry, Settings

VERIFY='fn(s){{done:true,context:null,output:s.evidence[0]}}'
DEFINITION={'kind':'process','module':'roles.chaos','export':'step','name':'role integration','intervalSeconds':None,'maxTurns':None,'roles':['left','right']}

class ProcessRoleIntegration(unittest.TestCase):
    def create(self,source,registry=None):
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        app._engine.pause_dispatch(True)
        app.create_project('p',source='null',files={'roles.chaos':source},capabilities=['process.manage','fixture.effect'])
        app.create_process('p',1,'worker',DEFINITION,state=None)
        return app

    def wait_row(self,app,predicate):
        until=time.monotonic()+8
        while time.monotonic()<until:
            row=app.get_process('p','worker')
            if predicate(row):return row
            time.sleep(.005)
        self.fail('Process did not reach expected actual state: '+repr(row))

    def barriers(self):
        registry=Registry();entered={key:threading.Event()for key in ('left','right')};release=threading.Event();calls=[];effects=[]
        @registry.provider('fixture.role_gate')
        def provider(request,context):
            role=request.input['role'];calls.append(role);entered[role].set();until=time.monotonic()+10
            while not release.wait(.005):
                context.checkpoint()
                if time.monotonic()>until:raise AssertionError('role gate was not released')
            context.checkpoint();return Final(11 if role=='right'else 3)
        @registry.tool('fixture.effect')
        def effect(value,context):context.checkpoint();effects.append(value);return value
        self.addCleanup(release.set)
        return registry,entered,release,calls,effects

    def source(self,root_guidance=False):
        first='if length(s.guidance)>0{s.guidance[-1].input}else{' if root_guidance else ''
        last='}'if root_guidance else ''
        return ('agent gate{provider:"fixture.role_gate",tools:[]};async fn step(c){var plans=0;'
          'fn make(role){bot(role,fn(s){if length(s.guidance)>0{s.guidance[-1].input}else{gate({role:role})}},'
          'fn(p,s){tool("fixture.effect",{role:role,value:p})},'+VERIFY+',role=role)};'
          'async fn planning(s){plans+=1;'+first+'let left=make("left");let right=make("right");'
          'let results=await gather([left(),right()]);[results[0].output,results[1].output]'+last+'};'
          'let root=bot("root",planning,fn(p,s){p},fn(s){{done:true,context:null,output:{answer:s.evidence[0],plans:plans}}});let answer=await root();'
          'process_step(answer.state,answer.output,answer.done)};export {step}')

    def test_actual_role_hot_guidance_replans_only_its_branch_before_effect(self):
        registry,entered,release,calls,effects=self.barriers();app=self.create(self.source(),registry);app._engine.pause_dispatch(False)
        try:
            self.assertTrue(entered['left'].wait(5));self.assertTrue(entered['right'].wait(5))
            before=app.get_process('p','worker');run_id=before['lastRunId'];target=before['botRoles']['left']['target']
            scopes=app.guidance_scopes(run_id)['scopes'];left=next(x for x in scopes if x.get('roleKey')=='left');right=next(x for x in scopes if x.get('roleKey')=='right')
            self.assertEqual(left['target'],target);self.assertTrue(left['addressable']);self.assertNotEqual(left['scopeId'],right['scopeId'])
            receipt=app.guide(run_id,42,'hot',target=target);self.assertEqual(set(receipt),{'key','runId','messageId','revision','status','target'})
            after=app.get_process('p','worker');self.assertEqual(after.get('guidanceRevision',0),0);self.assertEqual(after['botRoles']['left']['revision'],1);self.assertEqual(after['botRoles']['right']['revision'],0)
            release.set();row=self.wait_row(app,lambda r:r['status']in('completed','needs_reconciliation'))
            self.assertEqual(row['status'],'completed');self.assertEqual(row['output'],{'answer':[{'role':'left','value':42},{'role':'right','value':11}],'plans':1})
            self.assertCountEqual(calls,['left','right']);self.assertCountEqual(effects,[{'role':'left','value':42},{'role':'right','value':11}])
            self.assertEqual(app.guide_process('p','worker',42,message_id='hot',target=target)['status'],'applied')
            applied=[e['payload']for e in app.events('p',run_id=run_id,limit=300)['items']if e['type']=='bot.context.applied']
            self.assertEqual([x.get('roleId')for x in applied],[target['roleId']])
        finally:release.set()

    def test_actual_root_hot_guidance_invalidates_waiting_roles_and_preserves_reconciliation(self):
        registry,entered,release,calls,effects=self.barriers();app=self.create(self.source(True),registry);app._engine.pause_dispatch(False)
        try:
            self.assertTrue(entered['left'].wait(5));self.assertTrue(entered['right'].wait(5))
            run=app.get_process('p','worker')['lastRunId'];receipt=app.guide(run,99,'root-change')
            self.assertEqual(set(receipt),{'runId','messageId','revision','status'});release.set()
            row=self.wait_row(app,lambda r:r['status']in('completed','needs_reconciliation'))
            self.assertEqual(row['status'],'needs_reconciliation');self.assertEqual(row['turn'],0);self.assertIsNone(row['state']);self.assertIsNone(row['output']);self.assertIsNotNone(row['pending']);self.assertEqual(effects,[])
            self.assertCountEqual(calls,['left','right']);self.assertEqual([r['revision']for r in row['botRoles'].values()],[0,0])
        finally:release.set()

    def test_actual_next_turn_keeps_role_address_and_uses_new_scope_once(self):
        source=('async fn step(c){let child=bot("left",fn(s){if length(s.guidance)>0{s.guidance[-1].input}else{c.turn}},fn(p,s){p},'+VERIFY+',role="left");'
          'let root=bot("root",fn(s){null},async fn(p,s){(await child()).output},fn(s){{done:false,context:s.context,output:s.evidence[0]}},max_iterations=1);'
          'let answer=await root();process_step(answer.state,answer.output,answer.done)};export {step}')
        app=self.create(source);target=app.get_process('p','worker')['botRoles']['left']['target'];app._engine.pause_dispatch(False)
        first=self.wait_row(app,lambda r:r['turn']==1 and r['status']=='waiting');self.assertEqual(first['output'],1)
        receipt=app.guide_process('p','worker',42,message_id='second-turn',target=target)
        second=self.wait_row(app,lambda r:r['turn']==2 and r['status']=='waiting');self.assertEqual(second['output'],42);self.assertNotEqual(first['lastRunId'],second['lastRunId'])
        self.assertEqual(second['botRoles']['left']['target'],target)
        retry=app.guide_process('p','worker',42,message_id='second-turn',target=target);self.assertEqual(retry['runId'],receipt['runId']);self.assertEqual(retry['revision'],receipt['revision']);self.assertEqual(retry['status'],'applied')
        events=app.events('p',limit=300)['items'];opens=[e['payload']['scopeId']for e in events if e['type']=='bot.scope.opened'and e['payload'].get('roleId')==target['roleId']]
        self.assertEqual(len(opens),2);self.assertEqual(len(set(opens)),2)
        # No outstanding role input can repeatedly create empty turns.
        with app._engine.idle:self.assertEqual(app._store.processes.candidates(),[])
        self.assertEqual(app._store.db.execute("SELECT count(*) FROM process_role_messages WHERE status='guide_consumed'").fetchone()[0],1)
