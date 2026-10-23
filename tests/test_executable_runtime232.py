"""Persistent image runs use the original worker, Context, journal and owners."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Framework, Registry, Settings, Final
from chaos.executable_image import compile_executable
from chaos.language import LanguageError
from chaos.store import Conflict
from test_executable231 import no_frontend

ROOT=Path(__file__).resolve().parents[1]
GUIDED='''await progress_emit("plan",{summary:"image running"});
agent gate{provider:"fixture.image232",tools:[]};var plans=0;
fn plan(s){plans+=1;if length(s.guidance)>0{s.guidance[-1].input}else{gate("old")}};
async fn execute(p,s){let value=tool("fixture.image232.effect",p);async with await robot_open("rig") as r{let o=await robot_observe(r);let receipt=await r(robot_command(o,{pan:0,tilt:1}));[value,receipt.status]}};
fn verify(s){{done:true,context:null,output:s.evidence[-1]}};
let result=await bot("persistent executable",plan,execute,verify)();[result.output,plans]'''


class ExecutableRuntime232(unittest.TestCase):
    def app(self,registry=None,db=':memory:'):
        app=Framework(db,registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close);return app

    def project(self,app,capabilities=()):
        return app.create_project('authority',source='fn authority_only(){999};authority_only()',capabilities=list(capabilities))

    def events(self,app,project,run):
        return app.events(project['id'],run_id=run.id,limit=300)['items']

    def test_source_free_persistent_admission_and_execution_store_actual_image(self):
        image=compile_executable('fn actual_image(x){x+2};actual_image(input)');app=self.app();p=self.project(app);before=copy.deepcopy(p)
        with no_frontend():
            run=app.start_executable(p['id'],p['version'],image,40,trace=True)
            self.assertEqual(run.result(8),42)
        row=run.get();self.assertEqual(row['mode'],'executable');self.assertEqual(row['version'],p['version'])
        self.assertEqual(row['snapshot']['execution'],{'kind':'executable','imageHash':image['imageHash'],'entry':image['entry'],'image':image})
        self.assertEqual(row['snapshot']['source'],before['source']);self.assertEqual(app.get_project(p['id']),before)
        events=self.events(app,p,run);self.assertEqual(sum(e['type']=='run.queued' for e in events),1);self.assertEqual(sum(e['type']=='run.completed' for e in events),1)
        self.assertTrue(all(e['run_id']==run.id for e in events));self.assertFalse(app._engine.active)

    def test_caller_mutation_and_later_project_edit_cannot_change_frozen_running_code(self):
        entered=threading.Event();release=threading.Event();calls=[];registry=Registry()
        @registry.tool('fixture.freeze',effect='pure')
        def hold(value,ctx):
            calls.append(value);entered.set()
            if not release.wait(8):raise AssertionError('Frozen image fixture not released')
            ctx.checkpoint();return value
        image=compile_executable('tool("fixture.freeze",40)+2');original=copy.deepcopy(image);app=self.app(registry);p=self.project(app,['fixture.freeze'])
        with no_frontend():run=app.start_executable(p['id'],p['version'],image,None)
        try:
            self.assertTrue(entered.wait(3));image.clear()
            edited=app.edit_program(p['id'],p['version'],source='-1')
            release.set();self.assertEqual(run.result(8),42)
            row=run.get();self.assertEqual(row['snapshot']['execution']['image'],original)
            self.assertEqual(row['snapshot']['version'],p['version']);self.assertNotEqual(edited['version'],p['version'])
            self.assertEqual(calls,[40])
        finally:release.set()

    def test_malformed_image_stale_version_and_foreign_grants_never_queue_or_effect(self):
        app=self.app();p=self.project(app);image=compile_executable('tool("not-granted",1)')
        before=app.runs(p['id']);events=app.events(p['id'])['lastEventId']
        for bad in ({},dict(image,imageHash='0'*64),dict(image,capabilities=['not-granted'])):
            with self.subTest(bad=list(bad)),no_frontend(),self.assertRaises(LanguageError):app.start_executable(p['id'],p['version'],bad,None)
        with no_frontend(),self.assertRaises(Conflict):app.start_executable(p['id'],p['version']+1,image,None)
        self.assertEqual(app.runs(p['id']),before);self.assertEqual(app.events(p['id'])['lastEventId'],events);self.assertFalse(app._engine.active)
        # Valid executable syntax still cannot obtain a capability from its data.
        run=app.start_executable(p['id'],p['version'],image,None);row=run.wait(8)
        self.assertEqual(row['status'],'failed');self.assertIsNone(row['output'])

    def test_real_bot_guidance_agent_and_software_robot_are_once_in_same_run(self):
        from chaos.robots import RobotSpec
        import test_robot_values as robots
        entered=threading.Event();release=threading.Event();effects=[];providers=[];log=[];registry=Registry()
        @registry.provider('fixture.image232')
        def provider(request,ctx):
            providers.append(request.input);entered.set()
            deadline=time.monotonic()+10
            while not release.wait(.01):
                ctx.checkpoint()
                if time.monotonic()>deadline:raise AssertionError('Guidance fixture not released')
            ctx.checkpoint();return Final('stale proposal')
        @registry.tool('fixture.image232.effect',effect='write')
        def effect(value,ctx):ctx.checkpoint();effects.append(value);return value
        def factory(ctx):ctx.checkpoint();log.append(('open',{}));return robots.Adapter(log)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:image232'))
        image=compile_executable(GUIDED);app=self.app(registry);p=self.project(app,['fixture.image232.effect','robot.control'])
        with no_frontend():run=app.start_executable(p['id'],p['version'],image,None)
        try:
            self.assertTrue(entered.wait(3));payload={'answer':42};receipt=run.guide(payload,message_id='image-guidance')
            self.assertEqual(receipt,run.guide({'answer':42},message_id='image-guidance'));payload['answer']=0
            release.set();self.assertEqual(run.result(12),[[{'answer':42},'sent'],2])
        finally:release.set()
        self.assertEqual(providers,['old']);self.assertEqual(effects,[{'answer':42}]);self.assertEqual([name for name,_ in log],['open','observe','act','close'])
        events=self.events(app,p,run)
        self.assertEqual(sum(e['type']=='guidance.queued' for e in events),1);self.assertEqual(sum(e['type']=='bot.context.applied' for e in events),1)
        self.assertTrue(all(e['run_id']==run.id for e in events));self.assertFalse(app._engine.active)
        with self.assertRaises(Conflict):run.guide('too late')

    def test_cancel_same_run_drains_original_pending_host_and_owned_resources(self):
        entered=threading.Event();exited=threading.Event();effects=[];registry=Registry()
        @registry.tool('fixture.cancel232',effect='pure')
        def held(value,ctx):
            entered.set();deadline=time.monotonic()+10
            try:
                while time.monotonic()<deadline:ctx.checkpoint();time.sleep(.01)
                raise AssertionError('Cancellation was not delivered')
            finally:exited.set()
        @registry.tool('fixture.after232',effect='write')
        def after(value,ctx):effects.append(value);return value
        app=self.app(registry);p=self.project(app,['fixture.cancel232','fixture.after232'])
        image=compile_executable('tool("fixture.cancel232",null);tool("fixture.after232",1)')
        with no_frontend():run=app.start_executable(p['id'],p['version'],image,None)
        self.assertTrue(entered.wait(3));self.assertTrue(run.cancel());self.assertEqual(run.wait(10)['status'],'cancelled')
        self.assertTrue(exited.is_set());self.assertEqual(effects,[]);self.assertFalse(app._engine.active)
        self.assertEqual(sum(e['type']=='run.cancelled' for e in self.events(app,p,run)),1)

    def test_current_program_cannot_claim_authority_source_and_runtime_view_uses_image(self):
        app=self.app();p=self.project(app)
        image=compile_executable('fn image_only(){try{current_program()}catch error{error.code}};image_only()')
        with no_frontend():
            run=app.start_executable(p['id'],p['version'],image,None);self.assertEqual(run.result(8),'program_error')
            view=app.runtime_view(p['id'],run_id=run.id)
        self.assertIsNone(view['sourceVersion']);self.assertEqual(view['authorityVersion'],p['version'])
        self.assertEqual(view['execution'],{'kind':'executable','imageHash':image['imageHash'],'entry':image['entry'],'sourceAvailable':False})
        labels=[n.get('label','') for n in view['nodes']]
        self.assertTrue(any('image_only' in label for label in labels),labels)
        self.assertFalse(any('authority_only' in label for label in labels),labels)
        self.assertNotIn('image',view['execution'])

    def test_explicit_dynamic_source_program_retains_its_own_original_origin(self):
        definition={'kind':'program','entry':'child.chaos','files':{'child.chaos':'current_program().entry'},'languageVersions':{'child.chaos':2}}
        # Use the documented descriptor function instead of borrowing host source.
        definition['files']['child.chaos']='program_info(current_program()).entry'
        source='let child=program_from('+json.dumps(definition)+');await child(null)'
        app=self.app();p=self.project(app);run=app.start_executable(p['id'],p['version'],compile_executable(source),None)
        self.assertEqual(run.result(10),'child.chaos')

    def test_process_death_recovery_marks_interrupted_without_replaying_image(self):
        script='''import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from chaos import Framework,Registry,Settings
work=Path(sys.argv[2]);registry=Registry()
@registry.tool("fixture.crash232",effect="write")
def held(value,ctx):
 with (work/"effects.txt").open("a") as stream:stream.write("once\\n");stream.flush()
 (work/"entered").write_text("ready")
 while True:ctx.checkpoint();time.sleep(.01)
with Framework(str(work/"state.sqlite"),registry=registry,settings=Settings(":memory:")) as app:
 project=app.get_project("workspace")
 image=json.loads((work/"image.json").read_text())
 run=app.start_executable(project["id"],project["version"],image,None)
 (work/"run.json").write_text(json.dumps({"id":run.id}))
 run.result(20)
'''
        with tempfile.TemporaryDirectory(prefix='image-recovery232-') as directory:
            work=Path(directory);image=compile_executable('tool("fixture.crash232",1)');(work/'image.json').write_text(json.dumps(image))
            with Framework(str(work/'state.sqlite'),settings=Settings(':memory:')) as app:
                p=app.get_project('workspace');p['capabilities']=['fixture.crash232'];app.save_project(p,from_view='source')
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','TMPDIR','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
            env.update(PYTHONDONTWRITEBYTECODE='1',CHAOS_CONFIG=str(work/'config.json'))
            child=subprocess.Popen([sys.executable,'-I','-S','-B','-c',script,str(ROOT),str(work)],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+10
                while not ((work/'entered').exists() and (work/'run.json').exists()):
                    if child.poll() is not None:raise AssertionError(child.communicate())
                    if time.monotonic()>deadline:raise AssertionError('Missing real crash frontier')
                    time.sleep(.01)
                child.kill();child.communicate(timeout=3)
            finally:
                if child.poll() is None:child.kill();child.communicate(timeout=3)
            with Framework(str(work/'state.sqlite'),settings=Settings(':memory:')) as app:
                run_id=json.loads((work/'run.json').read_text())['id'];row=app.get_run(run_id)
                self.assertEqual(row['status'],'interrupted');self.assertEqual(row['snapshot']['execution']['image'],image)
                self.assertFalse(app._engine.active)
                self.assertEqual(sum(e['type']=='run.interrupted' for e in app.events('workspace',run_id=run_id)['items']),1)
            self.assertEqual((work/'effects.txt').read_text(),'once\n')

    def test_goal_resume_is_bound_to_actual_image_not_unchanged_authority_source(self):
        from tests.test_modes_integration import GOAL
        effects=[];registry=Registry()
        @registry.tool('fixture.goal232',effect='write')
        def advance(value,ctx):ctx.checkpoint();effects.append(value);return value+1
        source=GOAL.replace('ctx.input+1','tool("fixture.goal232",ctx.input)')
        image=compile_executable(source);different=compile_executable(source+';null')
        self.assertNotEqual(image['imageHash'],different['imageHash'])
        app=self.app(registry);p=self.project(app,['fixture.goal232'])
        first=app.start_executable(p['id'],p['version'],image,None);answer=first.result(10)
        self.assertEqual(answer['state'],42);self.assertEqual(effects,[40,41])
        retained=copy.deepcopy(app.mode(p['id'],'answer'))
        second=app.start_executable(p['id'],p['version'],copy.deepcopy(image),None)
        self.assertEqual(second.result(10),answer);self.assertEqual(effects,[40,41])
        self.assertEqual(app.mode(p['id'],'answer')['sourceHash'],retained['sourceHash'])
        self.assertFalse(any(e['type']=='mode.plan.step.started' for e in self.events(app,p,second)))
        bad=app.start_executable(p['id'],p['version'],different,None).wait(10)
        self.assertEqual(bad['status'],'failed');self.assertIn('source bundle',bad['error']);self.assertEqual(effects,[40,41])
        self.assertEqual(app.mode(p['id'],'answer')['sourceHash'],retained['sourceHash'])

    def test_dynamic_program_goal_identity_is_child_definition_not_parent_image(self):
        from tests.test_modes_integration import GOAL
        effects=[];registry=Registry()
        @registry.tool('fixture.childgoal232',effect='write')
        def advance(value,ctx):ctx.checkpoint();effects.append(value);return value+1
        child_source=GOAL.replace('ctx.input+1','tool("fixture.childgoal232",ctx.input)')
        definition={'kind':'program','entry':'child.chaos','files':{'child.chaos':child_source},'languageVersions':{'child.chaos':2}}
        image=compile_executable('let child=program_from(input);await child(null)')
        app=self.app(registry);p=self.project(app,['fixture.childgoal232'])
        first=app.start_executable(p['id'],p['version'],image,definition).result(10)
        self.assertEqual(first['state'],42);self.assertEqual(effects,[40,41])
        self.assertEqual(app.start_executable(p['id'],p['version'],image,copy.deepcopy(definition)).result(10),first)
        changed=copy.deepcopy(definition);changed['files']['child.chaos']+=';null'
        bad=app.start_executable(p['id'],p['version'],image,changed).wait(10)
        self.assertEqual(bad['status'],'failed');self.assertIn('source bundle',bad['error']);self.assertEqual(effects,[40,41])

    def test_another_SQLite_connection_revokes_authority_at_actual_create_run_frontier(self):
        with tempfile.TemporaryDirectory(prefix='image-cas232-') as directory:
            db=Path(directory)/'state.sqlite';app=self.app(db=str(db));p=self.project(app)
            image=compile_executable('42');original=app._store.create_run;calls=[]
            def at_commit(*args,**kwargs):
                calls.append((args[2],kwargs.get('_check_authority')))
                # The separate connection commits after Engine's earlier read,
                # immediately before the original create_run transaction begins.
                other=sqlite3.connect(db)
                try:
                    other.execute('UPDATE projects SET version=version+1,capabilities=? WHERE id=?',(json.dumps([]),p['id']));other.commit()
                finally:other.close()
                return original(*args,**kwargs)
            before=app.runs(p['id'])
            with patch.object(app._store,'create_run',at_commit),no_frontend(),self.assertRaises(Conflict):
                app.start_executable(p['id'],p['version'],image,None)
            self.assertEqual(calls,[('executable',True)]);self.assertEqual(app.runs(p['id']),before)
            self.assertFalse(app._engine.active);self.assertEqual(app._engine.guidance,{})
