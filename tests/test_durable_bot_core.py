"""Actual Process claims protect native Bot guidance, checkpoints and prefixes."""
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

from chaos import Framework, Registry, Settings, ToolSpec
from chaos.language import LanguageError
from chaos.runtime import Context


DEFINITION={'kind':'process','module':'dot.chaos','export':'step',
            'name':'durable bot','intervalSeconds':None,'maxTurns':None}


def step(body):
    return 'async fn step(c){'+body+'};export {step}'


def worker(execute='p',verify_context='s.context',verify_output='42'):
    return ('let b=bot("durable goal",fn(s){42},fn(p,s){'+execute+'},'
            'fn(s){{done:true,context:'+verify_context+',output:'+verify_output+'}},context=c.state);')


class DurableBotCore(unittest.TestCase):
    def app(self,source,*,registry=None,caps=None,database=':memory:'):
        app=Framework(database,registry=registry,settings=Settings(':memory:'))
        self.addCleanup(app.close)
        app.create_project('p',source='42',files={'dot.chaos':source},
                           capabilities=['process.manage']+(caps or []))
        return app

    def start(self,app,state=None):
        app.create_process('p',1,'dot',DEFINITION,state={'kept':True} if state is None else state)

    def finished(self,app):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            row=app.get_process('p','dot')
            if row['status'] in ('completed','needs_reconciliation'):return row
            time.sleep(.005)
        self.fail('Actual Process did not reach a terminal or protected uncertain state')

    def blocked(self,app):
        row=self.finished(app)
        self.assertEqual(row['status'],'needs_reconciliation')
        self.assertEqual(row['state'],{'kept':True})
        self.assertIsNotNone(row['pending'])
        self.assertEqual(row['turn'],0)
        return row

    def test_prequeued_process_guidance_commits_actual_bot_snapshot_without_normal_input(self):
        source=step('fn plan(s){{input:s.input,guidance:s.guidance}};'
                    'let b=bot("goal",plan,fn(p,s){p},fn(s){{done:true,context:{verified:true},output:s.evidence}},context=c.state);'
                    'let answer=await b(c.input);process_step(answer.state,answer.output,answer.done)')
        app=self.app(source);app._engine.pause_dispatch(True)
        self.start(app)
        queued=app.guide_process('p','dot',{'text':'retained guidance'},message_id='before')
        app._engine.pause_dispatch(False)
        row=self.finished(app)
        self.assertEqual(row['status'],'completed')
        self.assertEqual(row['state']['context'],{'verified':True})
        self.assertIsNone(row['state']['input'])
        self.assertEqual(row['output'][0]['input'],None)
        self.assertEqual(row['state']['guidance'][0]['input'],{'text':'retained guidance'})
        self.assertEqual(row['state']['guidance'][0]['messageId'],'before')
        repeated=app.guide_process('p','dot',{'text':'retained guidance'},message_id='before')
        self.assertEqual(repeated['revision'],queued['revision'])
        self.assertEqual(repeated['messageId'],'before')
        self.assertIsNone(row['pending'])

    def test_admission_is_durable_before_the_actual_tool_and_checkpoint_has_verified_context(self):
        operations=[];effects=[];holder={};original=Context.process_bot_operation
        def bridge(context,operation,payload):
            result=original(context,operation,payload)
            operations.append((operation,json.loads(json.dumps(payload))))
            return result
        def action(value,context):
            effects.append(value)
            self.assertEqual(operations[-1][0],'admit')
            admission=operations[-1][1]
            self.assertEqual(admission['action']['status'],'admitted')
            with holder['app']._store.lock:
                pending=holder['app']._store.processes._read('p','dot')['pending']
            journal=json.dumps(pending)
            self.assertIn(admission['action']['id'],journal)
            self.assertIn('fixture.act',journal)
            return value
        registry=Registry();registry.register_tool(ToolSpec('fixture.act',action))
        app=self.app(step(worker('tool("fixture.act",p)','{verified:true}')+
                          'let answer=await b();process_step(answer.state,answer.output,answer.done)'),
                     registry=registry,caps=['fixture.act']);holder['app']=app
        with patch.object(Context,'process_bot_operation',bridge):
            self.start(app);row=self.finished(app)
        self.assertEqual(row['status'],'completed');self.assertEqual(effects,[42])
        commit=next(payload for operation,payload in operations if operation=='commit')
        self.assertTrue(commit['done']);self.assertTrue(commit['terminal'])
        self.assertEqual(commit['snapshot']['context'],{'verified':True})
        self.assertEqual(commit['snapshot'],row['state']);self.assertEqual(commit['output'],row['output'])
        self.assertTrue(next(payload for operation,payload in operations if operation=='close')['success'])

    def test_caught_executor_failure_cannot_forge_successful_process_step(self):
        effects=[];registry=Registry();registry.register_tool(ToolSpec('fixture.act',lambda value,c:effects.append(value) or value))
        source=step(worker('tool("fixture.act",p);throw "after prefix"')+
                    'let ignored=try{await b()}catch error{null};process_step({forged:true},42,true)')
        app=self.app(source,registry=registry,caps=['fixture.act']);self.start(app)
        self.blocked(app);self.assertEqual(effects,[42])

    def test_pure_bot_reconcile_cannot_reopen_host_blocked_work_or_replay_the_prefix(self):
        effects=[];registry=Registry();registry.register_tool(ToolSpec('fixture.act',lambda value,c:effects.append(value) or value))
        source=step(worker('tool("fixture.act",p);throw "after prefix"')+
                    'let first=await b();bot_reconcile(b,c.state,{checked:true});'
                    'let retry=try{await b()}catch error{null};process_step(bot_context(b),42,true)')
        app=self.app(source,registry=registry,caps=['fixture.act']);self.start(app)
        self.blocked(app);self.assertEqual(effects,[42])

    def test_successful_bot_checkpoint_cannot_be_replaced_by_wrapper_json(self):
        app=self.app(step(worker()+'let answer=await b();process_step({forged:true},42,true)'))
        self.start(app);self.blocked(app)

    def test_local_child_cancel_cannot_be_caught_to_clear_the_durable_latch(self):
        source=step('async fn p(s){await sleep(10);null};'
                    'let b=bot("cancel",p,fn(p,s){p},fn(s){{done:true,context:s.context,output:42}},context=c.state);'
                    'let work=spawn(b());while task_info(work).phase!="joining"{await sleep(0.001)};'
                    'cancel(work);await task_wait([work],until="all");process_step(bot_context(b),42,true)')
        app=self.app(source);self.start(app);self.blocked(app)

    def test_lost_commit_receipt_keeps_checkpoint_but_blocks_the_caught_wrapper(self):
        original=Context.process_bot_operation;committed=[]
        def bridge(context,operation,payload):
            result=original(context,operation,payload)
            if operation=='commit':
                committed.append(payload)
                raise LanguageError('Controlled committed receipt loss',code='checkpoint_receipt_lost')
            return result
        source=step(worker()+'let ignored=try{await b()}catch error{null};'
                    'process_step(bot_context(b),42,true)')
        app=self.app(source)
        with patch.object(Context,'process_bot_operation',bridge):
            self.start(app);self.blocked(app)
        self.assertEqual(len(committed),1)

    def test_close_journal_failure_blocks_an_otherwise_matching_process_step(self):
        original=Context.event;failed=[]
        def event(context,kind,payload):
            if kind=='bot.scope.closed' and not failed:
                failed.append(True)
                raise LanguageError('Controlled close journal failure',code='checkpoint_close_failed')
            return original(context,kind,payload)
        source=step(worker()+'let ignored=try{await b()}catch error{null};'
                    'process_step(bot_context(b),42,true)')
        app=self.app(source)
        with patch.object(Context,'event',event):
            self.start(app);self.blocked(app)
        self.assertEqual(failed,[True])

    def test_real_host_exit_after_admission_recovers_blocked_without_replaying_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'state.db';marker=Path(directory)/'prefix.json'
            source=step(worker('tool("fixture.crash",p)')+
                        'let answer=await b();process_step(answer.state,answer.output,answer.done)')
            script='''import json,os,time
from chaos import Framework,Registry,Settings,ToolSpec
registry=Registry()
def crash(value,context):
    with open(MARKER,'w') as handle:json.dump([value],handle)
    os._exit(73)
registry.register_tool(ToolSpec('fixture.crash',crash))
app=Framework(DATABASE,registry=registry,settings=Settings(':memory:'))
app._engine.pause_dispatch(True)
app.create_project('p',source='42',files={'dot.chaos':SOURCE},capabilities=['process.manage','fixture.crash'])
app.create_process('p',1,'dot',DEFINITION,state={'kept':True})
app.guide_process('p','dot',{'text':'survives crash'},message_id='before-crash')
app._engine.pause_dispatch(False)
deadline=time.monotonic()+6
while time.monotonic()<deadline:time.sleep(.01)
raise RuntimeError('Actual action admission did not enter crash fixture')
'''
            prefix='DATABASE='+repr(str(database))+';MARKER='+repr(str(marker))+';SOURCE='+repr(source)+';DEFINITION='+repr(DEFINITION)+'\n'
            result=subprocess.run([sys.executable,'-B','-c',prefix+script],cwd=directory,
                env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1])),
                capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,73,result.stdout+result.stderr)
            self.assertEqual(json.loads(marker.read_text()),[42])
            with Framework(str(database),settings=Settings(':memory:')) as app:
                row=app.get_process('p','dot')
                self.assertEqual(row['status'],'needs_reconciliation');self.assertIsNotNone(row['pending'])
                self.assertEqual(row['state'],{'kept':True});self.assertEqual(row['turn'],0)
                with app._store.lock:pending=app._store.processes._read('p','dot')['pending']
                self.assertIn('fixture.crash',json.dumps(pending));self.assertIn('admitted',json.dumps(pending))
                receipt=app.guide_process('p','dot',{'text':'survives crash'},message_id='before-crash')
                self.assertEqual(receipt['messageId'],'before-crash')
                self.assertEqual(json.loads(marker.read_text()),[42])

    def test_volatile_bot_never_calls_the_process_bridge(self):
        source='let b=bot("volatile",fn(s){42},fn(p,s){p},fn(s){{done:true,context:null,output:42}});await b()'
        with Framework(settings=Settings(':memory:')) as app:
            project=app.create_project('volatile',source=source,capabilities=[])
            with patch.object(Context,'process_bot_operation',side_effect=AssertionError('Volatile Bot used Process bridge')):
                self.assertEqual(app.start(project['id'],1,None,mode='source').result(8)['output'],42)


if __name__=='__main__':unittest.main()
