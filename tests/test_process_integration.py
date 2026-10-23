"""Durable language turns use the real Engine, Store, permissions and project snapshots."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Framework, Client, Registry, ProviderSpec, ToolSpec, Final
from chaos.extensions import DEFAULT_CAPABILITIES
from chaos.language import LanguageError
from chaos.runtime import Context
from chaos.settings import Settings
from chaos.store import Conflict


def definition(**changes):
    return {'kind':'process','name':'dot','module':'dot.chaos','export':'step','intervalSeconds':None,'maxTurns':10,**changes}


def wait_for(app, project='p', key='dot', predicate=lambda p:p['turn']==1, timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=app.get_process(project,key)
        if predicate(result):return result
        time.sleep(.01)
    raise AssertionError(result)


class ProcessIntegration(unittest.TestCase):
    def app(self, **kwargs):
        app=Framework(settings=Settings(':memory:'),**kwargs);self.addCleanup(app.close);return app

    def project(self, app, source='fn step(c){process_step(c.state+1,c.input)};export {step}', *, caps=None, main='throw "bootstrap must not execute"', id='p'):
        return app.create_project(id,source=main,files={'dot.chaos':source},capabilities=['process.manage'] if caps is None else caps)

    def test_sdk_client_schema_source_and_reserved_capability(self):
        app=self.app();d=definition();checked=app.validate_process(d)
        self.assertEqual(Framework.evaluate('import "d.chaos" as D;process_info(D.saved_process)',files={'d.chaos':checked['source']}),d)
        self.assertFalse(app.process_schema()['additionalProperties']);self.assertNotIn('process.manage',DEFAULT_CAPABILITIES)
        self.project(app,caps=[])
        with self.assertRaises(LanguageError):app.create_process('p',1,'dot',d)
        client=Client('http://localhost:1')
        with patch.object(client,'_request',return_value={}) as request:
            client.create_process('p',1,'dot',d,state=2)
            request.assert_called_with('POST','/projects/p/processes',{'version':1,'key':'dot','definition':d,'state':2})
            client.reconcile_process('p','dot',3,4,evidence='verified')
            request.assert_called_with('POST','/projects/p/processes/dot/reconcile',{'revision':3,'state':4,'evidence':'verified','resume':False})
            client.send_process('p','dot',42,message_id='one')
            request.assert_called_with('POST','/projects/p/processes/dot/messages',{'input':42,'messageId':'one'})

    def test_first_turn_fifo_idempotence_and_atomic_run_evidence(self):
        app=self.app();self.project(app);app._engine.pause_dispatch(True)
        app.create_process('p',1,'dot',definition(maxTurns=3),state=0)
        app.send_process('p','dot','first',message_id='a');app.send_process('p','dot','second',message_id='b')
        app._engine.pause_dispatch(False)
        result=wait_for(app,predicate=lambda p:p['status']=='completed')
        self.assertEqual((result['state'],result['turn'],result['output']),(3,3,'second'))
        self.assertEqual(app.send_process('p','dot','first',message_id='a')['status'],'consumed')
        with self.assertRaises(Conflict):app.send_process('p','dot','different',message_id='a')
        runs=list(reversed(app._store.runs('p')))
        self.assertEqual([app.get_run(r['id'])['input']['process']['kind'] for r in runs],['start','message','message'])
        self.assertTrue(all(r['status']=='completed' for r in runs))
        self.assertEqual(app.snapshot('p')['processes'][0]['state'],3)
        events=app.events('p',limit=300)['items']
        self.assertEqual(sum(e['type']=='process.turn.completed' for e in events),3)
        self.assertFalse(any('state' in e['payload'] or 'input' in e['payload'] for e in events if e['type'].startswith('process.')))

    def test_language_start_send_and_current_project_only(self):
        app=self.app()
        main='let p=process_from('+json.dumps(definition(maxTurns=2))+');let created=await p("dot",0);await process_send("dot",42,"one");created.key'
        self.project(app,main=main)
        self.assertEqual(app.start('p',1,None,mode='source').result(),'dot')
        result=wait_for(app,predicate=lambda p:p['status']=='completed')
        self.assertEqual(result['output'],42)
        self.project(app,id='other')
        with self.assertRaises(KeyError):app.get_process('other','dot')

    def test_headless_waiting_restart_preserves_state_and_queued_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'project.sqlite3'
            with Framework(path,settings=Settings(':memory:')) as app:
                self.project(app);app.create_process('p',1,'dot',definition(),state=0);first=wait_for(app)
                self.assertEqual(first['state'],1);self.assertEqual(app._engine.active,{})
                app._engine.pause_dispatch(True);app.send_process('p','dot',42,message_id='saved')
            with Framework(path,settings=Settings(':memory:')) as reopened:
                result=wait_for(reopened,predicate=lambda p:p['turn']==2)
                self.assertEqual(result['state'],2);self.assertEqual(result['output'],42)

    def test_timer_coalesces_and_mailbox_has_priority(self):
        app=self.app();self.project(app);app.create_process('p',1,'dot',definition(intervalSeconds=86400,maxTurns=3),state=0);wait_for(app)
        app._engine.pause_dispatch(True)
        with app._store.write():
            row=app._store.processes._read('p','dot');row['nextWakeAt']=time.time()-100000
            app._store.processes._write(row,'process.test.due')
        app.send_process('p','dot',42,message_id='one');app._engine.pause_dispatch(False)
        result=wait_for(app,predicate=lambda p:p['turn']==2)
        self.assertEqual(result['output'],42);self.assertGreater(result['nextWakeAt'],time.time()+80000)
        time.sleep(.15);self.assertEqual(app.get_process('p','dot')['turn'],2)

    def test_real_timer_wakes_without_mail(self):
        app=self.app();self.project(app);app.create_process('p',1,'dot',definition(intervalSeconds=1,maxTurns=2),state=0)
        result=wait_for(app,predicate=lambda p:p['status']=='completed',timeout=3)
        run=app.get_run(result['lastRunId']);self.assertEqual(run['input']['process']['kind'],'timer')

    def test_real_provider_agent_and_next_version_adoption(self):
        seen=[];registry=Registry()
        registry.register_provider(ProviderSpec('test.echo',lambda req,ctx:seen.append(req.input) or Final(req.input)))
        app=self.app(registry=registry)
        module='''agent worker {provider:"test.echo"};
async fn step(c){
 let seen=worker(c.state);
 if c.turn==1 {
  let base=current_program();let d=program_info(base);
  let files=set(d.files,"dot.chaos","fn step(c){process_step(c.state+41,c.programVersion,true)};export {step}");
  let candidate=program_from(merge(d,{files:files}));
  await program_adopt(base,candidate,[{input:null,expect:42,entry:"tests.chaos"}]);
 } else {null};
 process_step(seen+1,c.programVersion)
};export {step}'''
        app.create_project('p',source='throw "unrelated main"',files={'dot.chaos':module,'tests.chaos':'42'},capabilities=['process.manage','program.adopt'])
        app.create_process('p',1,'dot',definition(),state=0)
        first=wait_for(app);self.assertEqual(first['output'],1);self.assertEqual(app.get_project('p')['version'],2)
        self.assertEqual(app.get_run(first['lastRunId'])['snapshot']['version'],1)
        app.send_process('p','dot',None)
        result=wait_for(app,predicate=lambda p:p['status']=='completed')
        self.assertEqual((result['state'],result['output'],result['lastVersion']),(42,2,2));self.assertEqual(seen,[0])

    def test_invalid_step_and_throw_require_reconciliation(self):
        for source in ('fn step(c){42};export {step}','fn step(c){throw "fail"};export {step}',
                       'fn step(c){{state:c.state,output:fn(){1},done:false}};export {step}'):
            with self.subTest(source=source):
                app=self.app();self.project(app,source);app.create_process('p',1,'dot',definition(),state=0)
                failed=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation')
                self.assertEqual(failed['state'],0);self.assertEqual(failed['turn'],0);self.assertNotIn('lease',failed['pending'])
                with self.assertRaises(ValueError):app.control_process('p','dot',failed['revision'],'resume')
                reconciled=app.reconcile_process('p','dot',failed['revision'],10,evidence='checked effects')
                self.assertEqual((reconciled['status'],reconciled['state'],reconciled['turn']),('paused',10,1))
                with self.assertRaises(Conflict):app.reconcile_process('p','dot',failed['revision'],10)

    def blocking(self):
        entered=threading.Event();release=threading.Event();seen=[];registry=Registry()
        def hold(value,ctx):
            seen.append(value);entered.set()
            while not release.wait(.01):ctx.checkpoint()
            return value
        registry.register_tool(ToolSpec('test.hold',hold));return registry,entered,release,seen

    def test_pause_and_configure_during_turn_preserve_latest_intent(self):
        registry,entered,release,seen=self.blocking();app=self.app(registry=registry)
        self.project(app,'fn step(c){tool("test.hold",c.turn);process_step(c.state+1)};export {step}',caps=['process.manage','test.hold'])
        app.create_process('p',1,'dot',definition(intervalSeconds=1),state=0);self.assertTrue(entered.wait(2))
        active=app.get_process('p','dot');changed=app.configure_process('p','dot',active['revision'],definition(name='changed'))
        app.control_process('p','dot',changed['revision'],'pause');release.set()
        result=wait_for(app,predicate=lambda p:p['status']=='paused' and p['pending'] is None)
        self.assertEqual(result['definition']['name'],'changed');self.assertIsNone(result['nextWakeAt']);self.assertEqual(result['state'],1)
        app.send_process('p','dot',None)
        time.sleep(.15);self.assertEqual(len(seen),1)
        result=app.get_process('p','dot');app.control_process('p','dot',result['revision'],'resume')
        wait_for(app,predicate=lambda p:p['turn']==2);self.assertEqual(len(seen),2)

    def test_stop_active_is_uncertain_and_does_not_commit_state(self):
        registry,entered,release,seen=self.blocking();app=self.app(registry=registry)
        self.project(app,'fn step(c){tool("test.hold",c.turn);process_step(99)};export {step}',caps=['process.manage','test.hold'])
        app.create_process('p',1,'dot',definition(),state=0);self.assertTrue(entered.wait(2))
        active=app.get_process('p','dot');app.control_process('p','dot',active['revision'],'stop')
        result=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation')
        self.assertEqual(result['state'],0);self.assertEqual(app.get_run(result['lastRunId'])['status'],'cancelled')
        self.assertEqual(seen,[1])

    def test_permission_ceiling_narrow_control_and_next_turn_revocation(self):
        app=self.app();self.project(app,caps=['process.manage','text.normalize']);app._engine.pause_dispatch(True)
        app.create_process('p',1,'dot',definition(),state=0)
        with self.assertRaises(LanguageError):app._engine.process_operation('p','send',{'key':'dot','input':None,'messageId':None},allowed={'process.manage'})
        project=app.get_project('p');project['capabilities']=[];app.save_project(project)
        app._engine.pause_dispatch(False);paused=wait_for(app,predicate=lambda p:p['status']=='paused')
        self.assertEqual(paused['turn'],0);self.assertIn('revoked',paused['error'])

    def test_new_project_grants_do_not_widen_launch_capabilities(self):
        app=self.app();self.project(app,'fn step(c){process_step(c.state+1)};export {step}')
        app.create_process('p',1,'dot',definition(),state=0);wait_for(app)
        p=app.get_project('p');p['capabilities'].append('text.normalize')
        p['files']['dot.chaos']='fn step(c){process_step(tool("text.normalize"," x "))};export {step}';app.save_project(p)
        app.send_process('p','dot',None)
        row=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation');self.assertIn('text.normalize',row['error'])
        self.assertEqual(row['launchCapabilities'],['process.manage'])

    def test_recording_rejects_process_effect_and_poison_cannot_be_caught(self):
        app=self.app();source='''try {await record_skill(async fn(x){try {await process_status("dot")} catch e {null};42})} catch e {e.code}'''
        self.project(app,main=source)
        result=app.start('p',1,None,mode='source').result();self.assertEqual(result,'mode_error')
        self.assertEqual(app.processes('p')['items'],[])

    def test_claim_and_terminal_transactions_roll_back_together(self):
        app=self.app();self.project(app);app._engine.pause_dispatch(True);app.create_process('p',1,'dot',definition(),state=0)
        original=app._store._event
        def failure(project,kind,*args):
            if kind=='process.turn.queued':raise RuntimeError('injected claim failure')
            return original(project,kind,*args)
        with patch.object(app._store,'_event',side_effect=failure):
            with self.assertRaises(RuntimeError):app._store.processes.claim('p','dot','failedclaim')
        self.assertEqual(app.get_process('p','dot')['status'],'waiting');self.assertEqual(app._store.runs('p'),[])
        claim=app._store.processes.claim('p','dot','terminal')
        def final_failure(project,kind,*args):
            if kind=='run.completed':raise RuntimeError('injected terminal failure')
            return original(project,kind,*args)
        with patch.object(app._store,'_event',side_effect=final_failure):
            with self.assertRaises(RuntimeError):app._store.transition_run('terminal','completed',{}, {'state':1,'output':None,'done':False},_process=claim)
        self.assertEqual(app.get_process('p','dot')['state'],0);self.assertEqual(app.get_run('terminal')['status'],'queued')
        app._store.transition_run('terminal','completed',{}, {'state':1,'output':None,'done':False},_process=claim)
        self.assertEqual(app.get_process('p','dot')['state'],1)

    def test_crash_after_real_effect_requires_explicit_reconcile_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'db.sqlite3');effect=str(Path(tmp)/'effect.txt')
            code='''import os,time
from pathlib import Path
from chaos import Framework,Registry,ToolSpec
from chaos.settings import Settings
r=Registry()
def crash(value,ctx):
 Path(EFFECT).write_text('effect')
 os._exit(17)
r.register_tool(ToolSpec('test.crash',crash))
a=Framework(DB,registry=r,settings=Settings(':memory:'))
a.create_project('p',source='null',files={'dot.chaos':'fn step(c){tool("test.crash",null);process_step(99)};export {step}'},capabilities=['process.manage','test.crash'])
a.create_process('p',1,'dot',DEFINITION,state=0)
time.sleep(5)
os._exit(19)
'''.replace('EFFECT',repr(effect)).replace('DB',repr(path)).replace('DEFINITION',repr(definition()))
            result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,17,result.stderr);self.assertEqual(Path(effect).read_text(),'effect')
            with Framework(path,settings=Settings(':memory:')) as app:
                row=app.get_process('p','dot');self.assertEqual(row['status'],'needs_reconciliation');self.assertEqual(row['state'],0)
                self.assertEqual(app.get_run(row['lastRunId'])['status'],'interrupted')
                time.sleep(.15);self.assertEqual(app.get_process('p','dot')['revision'],row['revision'])
                accepted=app.reconcile_process('p','dot',row['revision'],1,evidence='effect independently checked')
                self.assertEqual(accepted['status'],'paused')

    def test_same_project_manual_run_blocks_admission_and_waiting_does_not_use_slot(self):
        registry,entered,release,seen=self.blocking();app=self.app(registry=registry)
        self.project(app,main='tool("test.hold",42)',caps=['process.manage','test.hold'])
        manual=app.start('p',1,None,mode='source');self.assertTrue(entered.wait(2))
        app.create_process('p',1,'dot',definition(),state=0);time.sleep(.1)
        self.assertEqual(app.get_process('p','dot')['turn'],0);release.set();self.assertEqual(manual.result(),42)
        wait_for(app);self.assertEqual(app._engine.active,{})

    def test_bounds_ids_cas_and_maintenance_admission(self):
        app=self.app();self.project(app);app._engine.pause_dispatch(True)
        row=app.create_process('p',1,'dot',definition(),state=0)
        with self.assertRaises(ValueError):app.send_process('p','dot','x'*40001)
        with self.assertRaises(Conflict):app.configure_process('p','dot',row['revision']+1,definition())
        for n in range(256):app.send_process('p','dot',n,message_id='m'+str(n))
        with self.assertRaises(ValueError):app.send_process('p','dot',257,message_id='overflow')
        self.assertEqual(app.get_process('p','dot')['turn'],0)
        self.assertEqual(app.send_process('p','dot',0,message_id='m0')['status'],'queued')

    def test_close_waits_for_effect_and_stops_dispatcher_before_storage(self):
        entered=threading.Event();release=threading.Event();registry=Registry()
        registry.register_tool(ToolSpec('test.hold',lambda value,ctx:entered.set() or release.wait(2) or value))
        app=self.app(registry=registry);self.project(app,'fn step(c){tool("test.hold",null);process_step(1)};export {step}',caps=['process.manage','test.hold'])
        app.create_process('p',1,'dot',definition(intervalSeconds=1),state=0);self.assertTrue(entered.wait(2))
        with self.assertRaises(TimeoutError):app.close(timeout=.01)
        self.assertEqual(app._store.project('p')['version'],1)
        release.set();app.close(timeout=3)
        self.assertFalse(app._engine.dispatcher.is_alive());self.assertEqual(app._engine.active,{})

    def test_thread_start_failure_terminalizes_pending_turn(self):
        app=self.app();self.project(app);app._engine.pause_dispatch(True);app.create_process('p',1,'dot',definition(),state=0)
        original=threading.Thread.start
        def start(thread):
            if thread.name=='chaos-process-dispatch':return original(thread)
            raise RuntimeError('cannot start worker')
        with patch.object(threading.Thread,'start',start):
            app._engine.pause_dispatch(False)
            result=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation')
        self.assertEqual(app.get_run(result['lastRunId'])['status'],'failed');self.assertEqual(app._engine.active,{})


    def test_concurrent_same_id_delivery_admits_one_and_rejects_different_input(self):
        app=self.app();self.project(app);app._engine.pause_dispatch(True);app.create_process('p',1,'dot',definition(maxTurns=2),state=0)
        barrier=threading.Barrier(9);errors=[];receipts=[]
        def send():
            try:barrier.wait();receipts.append(app.send_process('p','dot',42,message_id='same'))
            except BaseException as error:errors.append(error)
        threads=[threading.Thread(target=send) for _ in range(8)]
        for thread in threads:thread.start()
        barrier.wait()
        for thread in threads:thread.join(2)
        self.assertEqual(errors,[]);self.assertEqual(len(receipts),8)
        self.assertEqual(app._store.db.execute('SELECT count(*) FROM process_messages').fetchone()[0],1)
        app._engine.pause_dispatch(False);wait_for(app,predicate=lambda p:p['status']=='completed')
        self.assertEqual(len(app._store.runs('p')),2)

    def test_cleanup_failure_preserves_previous_checkpoint(self):
        app=self.app();self.project(app)
        original=Context.close_services
        def broken(context,outcome):
            original(context,outcome)
            raise RuntimeError('cleanup failed')
        with patch.object(Context,'close_services',broken):
            app.create_process('p',1,'dot',definition(),state=0)
            row=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation')
        self.assertEqual(row['state'],0);self.assertIn('cleanup failed',row['error'])

    def test_dispatcher_constructor_failure_releases_runtime_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite3'
            with Framework(path,settings=Settings(':memory:')) as app:
                self.project(app);app._engine.pause_dispatch(True);app.create_process('p',1,'dot',definition(),state=0)
            with patch.object(threading.Thread,'start',side_effect=RuntimeError('thread unavailable')):
                with self.assertRaisesRegex(RuntimeError,'thread unavailable'):Framework(path,settings=Settings(':memory:'))
            with Framework(path,settings=Settings(':memory:')) as reopened:
                self.assertEqual(wait_for(reopened)['state'],1)

    def test_reserved_permission_never_reaches_agent_provider_or_tool(self):
        seen=[];registry=Registry();registry.register_provider(ProviderSpec('test.echo',lambda req,ctx:seen.append(req) or Final(42)))
        app=self.app(registry=registry)
        app.create_project('p',source='agent a {provider:"test.echo",tools:["process.manage"]};a(null)',capabilities=['process.manage'])
        failed=app.start('p',1,None,mode='source').wait(3);self.assertEqual(failed['status'],'failed');self.assertEqual(seen,[])
        p=app.get_project('p');p['source']='tool("process.manage",null)';app.save_project(p)
        self.assertEqual(app.start('p',2,None,mode='source').wait(3)['status'],'failed')

    def test_known_secret_is_redacted_in_state_and_retained_message(self):
        app=self.app();self.project(app)
        original=app._engine.settings.redact
        def redact(value):
            if isinstance(value,str):return value.replace('AUDIT_KNOWN_SECRET','[redacted]')
            if isinstance(value,list):return [redact(x) for x in value]
            if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
            return original(value)
        with patch.object(app._engine.settings,'redact',redact):
            app._engine.pause_dispatch(True);row=app.create_process('p',1,'dot',definition(),state='AUDIT_KNOWN_SECRET')
            app.send_process('p','dot','AUDIT_KNOWN_SECRET',message_id='secret')
        self.assertEqual(row['state'],'[redacted]')
        stored=app._store.db.execute('SELECT input FROM process_messages').fetchone()[0]
        self.assertNotIn('AUDIT_KNOWN_SECRET',stored);self.assertIn('[redacted]',stored)

    def test_revoked_tool_does_not_prevent_pause_or_uncertain_reconciliation(self):
        app=self.app();self.project(app,'fn step(c){throw "uncertain"};export {step}',caps=['process.manage','memory.put'])
        app._engine.pause_dispatch(True);row=app.create_process('p',1,'dot',definition(),state=0)
        project=app.get_project('p');project['capabilities']=['process.manage'];app.save_project(project)
        paused=app.control_process('p','dot',row['revision'],'pause');self.assertEqual(paused['status'],'paused')
        app.control_process('p','dot',paused['revision'],'resume');app._engine.pause_dispatch(False)
        failed=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation')
        accepted=app.reconcile_process('p','dot',failed['revision'],1,evidence='confirmed',resume=False)
        self.assertEqual(accepted['status'],'paused');self.assertEqual(accepted['state'],1)

    def test_function_node_cannot_schedule_process_even_with_permission_marker(self):
        app=self.app();project=self.project(app);app._engine.pause_dispatch(True)
        ctx=Context(app._engine,None,project,threading.Event())
        source='await process_start("escape",process_from('+json.dumps(definition())+'),0)'
        node={'id':'function','kind':'function','config':{'source':source,'tools':['process.manage']}}
        with self.assertRaisesRegex(LanguageError,'Function-node'):
            app._engine.execute_node(ctx,node,None)
        self.assertEqual(app.processes('p')['items'],[])
        app.create_process('p',1,'dot',definition(),state=0)
        node['config']['source']='(await process_status("dot")).key'
        self.assertEqual(app._engine.execute_node(ctx,node,None),'dot')

    def test_engine_terminal_event_failure_becomes_reconcilable_without_reexecution(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('test.count',lambda value,ctx:calls.append(value) or 42))
        app=self.app(registry=registry);self.project(app,'fn step(c){tool("test.count",c.turn);process_step(99)};export {step}',caps=['process.manage','test.count'])
        original=app._store._event;injected=[]
        def event(project,kind,*args):
            if kind=='run.completed' and not injected:
                injected.append(True);raise RuntimeError('transient terminal write failure')
            return original(project,kind,*args)
        with patch.object(app._store,'_event',side_effect=event):
            app.create_process('p',1,'dot',definition(),state=0)
            row=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation')
        self.assertEqual(calls,[1]);self.assertEqual(row['state'],0);self.assertEqual(row['turn'],0)
        self.assertEqual(app.get_run(row['lastRunId'])['status'],'failed')
        app.reconcile_process('p','dot',row['revision'],99,evidence='single observed effect')

    def test_continuously_unwritable_terminal_preserves_pending_for_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite3'
            with Framework(path,settings=Settings(':memory:')) as app:
                self.project(app)
                original=app._store.transition_run
                def transition(run_id,state,*args,**kwargs):
                    if state in ('completed','failed'):raise RuntimeError('storage unavailable')
                    return original(run_id,state,*args,**kwargs)
                with patch.object(app._store,'transition_run',side_effect=transition):
                    app.create_process('p',1,'dot',definition(),state=0)
                    wait_for(app,predicate=lambda p:p['pending'] is not None)
                    end=time.monotonic()+2
                    while app._engine.active and time.monotonic()<end:time.sleep(.01)
                    self.assertEqual(app._engine.active,{})
                self.assertEqual(app.get_process('p','dot')['state'],0)
            with Framework(path,settings=Settings(':memory:')) as reopened:
                self.assertEqual(reopened.get_process('p','dot')['status'],'needs_reconciliation')

    def test_worker_start_and_terminal_write_failure_do_not_kill_dispatcher(self):
        app=self.app();self.project(app);app._engine.pause_dispatch(True);app.create_process('p',1,'dot',definition(),state=0)
        start=threading.Thread.start;transition=app._store.transition_run;failures=[]
        def broken_start(thread):
            if thread.name=='chaos-process-dispatch':return start(thread)
            raise RuntimeError('start rejected')
        def broken_transition(*args,**kwargs):
            if not failures:failures.append(True);raise RuntimeError('receipt rejected')
            return transition(*args,**kwargs)
        with patch.object(threading.Thread,'start',broken_start),patch.object(app._store,'transition_run',side_effect=broken_transition):
            app._engine.pause_dispatch(False)
            failed=wait_for(app,predicate=lambda p:p['status']=='needs_reconciliation')
        self.assertEqual(app._engine.active,{});self.assertTrue(app._engine.dispatcher.is_alive())
        app.create_process('p',1,'another',definition(maxTurns=1),state=0)
        other=wait_for(app,key='another',predicate=lambda p:p['status']=='completed')
        self.assertEqual(other['state'],1)

    def test_consumed_history_over_256_keeps_accepting_and_old_ids_never_replay(self):
        import hashlib
        from chaos.process_store import dump
        app=self.app();self.project(app);app.create_process('p',1,'dot',definition(maxTurns=None),state=0)
        wait_for(app)
        app.send_process('p','dot',42,message_id='real_old')
        second=wait_for(app,predicate=lambda p:p['turn']==2)
        consumed=app._store.db.execute("SELECT input,status FROM process_messages WHERE message_id='real_old'").fetchone()
        self.assertEqual((consumed['input'],consumed['status']),('null','consumed'))
        # Historical lightweight receipts simulate a long-lived session without hundreds of redundant turns.
        with app._store.write():
            app._store.db.executemany('INSERT INTO process_messages(project_id,key,message_id,input,input_hash,status) VALUES(?,?,?,?,?,?)',
                [('p','dot','history'+str(i),'null',hashlib.sha256(dump(i).encode()).hexdigest(),'consumed') for i in range(300)])
        app.send_process('p','dot',43,message_id='after_history')
        third=wait_for(app,predicate=lambda p:p['turn']==3)
        self.assertEqual(third['output'],43)
        self.assertEqual(app.send_process('p','dot',42,message_id='real_old')['status'],'consumed')
        self.assertEqual(app.send_process('p','dot',299,message_id='history299')['status'],'consumed')
        with self.assertRaises(Conflict):app.send_process('p','dot',False,message_id='history299')
        time.sleep(.12);self.assertEqual(app.get_process('p','dot')['turn'],3)
        self.assertEqual(app._store.db.execute("SELECT count(*) FROM process_messages WHERE input<>'null'").fetchone()[0],0)


if __name__=='__main__':unittest.main()
