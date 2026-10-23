"""Actual durable claims: child results never substitute the root checkpoint."""
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Framework, Registry, Settings, ToolSpec
from chaos.runtime import Context

DEFINITION={'kind':'process','module':'dot.chaos','export':'step','name':'nested bot','intervalSeconds':None,'maxTurns':None}
VERIFY='fn(s){{done:true,context:null,output:s.evidence[0]}}'


class BotInvocationProcess(unittest.TestCase):
    def app(self,body,registry=None,caps=()):
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        app.create_project('p',source='42',files={'dot.chaos':'async fn step(c){'+body+'};export {step}'},
                           capabilities=['process.manage']+list(caps))
        return app

    def finish(self,app):
        app.create_process('p',1,'dot',DEFINITION,state={'kept':True})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            row=app.get_process('p','dot')
            if row['status']in ('completed','needs_reconciliation'):return row
            time.sleep(.005)
        self.fail('Actual nested Process did not finish or protect uncertain work')

    def test_child_commit_cannot_overwrite_root_checkpoint_or_duplicate_admission(self):
        calls=[];records=[];registry=Registry();original=Context.process_bot_operation
        registry.register_tool(ToolSpec('fixture.act',lambda value,ctx:calls.append(value)or value))
        body=('let child=bot("child",fn(s){40},fn(p,s){tool("fixture.act",p)+2},'+VERIFY+');'
              'let root=bot("root",fn(s){null},async fn(p,s){(await child()).output},'+VERIFY+');'
              'let answer=await root();process_step(answer.state,answer.output,answer.done)')
        app=self.app(body,registry,['fixture.act'])
        def observe(context,operation,payload):
            value=original(context,operation,payload)
            with context.engine.store.lock:
                work=context.engine.store.processes._read('p','dot')['pending']['botWork']
                checkpoint=work['checkpoint'];records.append((operation,payload.get('parentScopeId'),
                    None if checkpoint is None else checkpoint['state']['goal'],work['checkpointRevision'],
                    [(a['id'],a.get('ownerScopeId'),a.get('ancestry'))for a in work['actions']]))
            return value
        with patch.object(Context,'process_bot_operation',observe):row=self.finish(app)
        self.assertEqual(row['status'],'completed');self.assertEqual(row['output'],42);self.assertEqual(row['state']['goal'],'root')
        self.assertEqual(calls,[40]);child=[r for r in records if r[0]=='commit'and r[1]is not None]
        root=[r for r in records if r[0]=='commit'and r[1]is None]
        self.assertEqual([(r[2],r[3])for r in child],[(None,0)])
        self.assertEqual([(r[2],r[3])for r in root],[('root',1)])
        admissions=[r for r in records if r[0]=='admit'];self.assertEqual(len(admissions),1)
        self.assertEqual(len(admissions[0][4]),1);self.assertTrue(admissions[0][4][0][2])

    def test_parallel_nested_children_share_one_durable_ledger_without_false_block(self):
        barrier=threading.Barrier(2);calls=[];registry=Registry()
        def action(value,ctx):calls.append(value);barrier.wait(timeout=4);return value+40
        registry.register_tool(ToolSpec('fixture.barrier',action))
        body=('fn make(goal){bot(goal,fn(s){s.input},fn(p,s){tool("fixture.barrier",p)},'+VERIFY+')};'
              'let left=make("left");let right=make("right");'
              'let root=bot("root",fn(s){null},async fn(p,s){let results=await gather([left(1),right(2)]);'
              '[results[0].output,results[1].output]},'+VERIFY+');'
              'let answer=await root();process_step(answer.state,answer.output,answer.done)')
        row=self.finish(self.app(body,registry,['fixture.barrier']))
        self.assertEqual(row['status'],'completed');self.assertEqual(row['output'],[41,42]);self.assertCountEqual(calls,[1,2])

    def test_uncertain_descendant_effect_blocks_durable_completion_without_replay(self):
        calls=[];registry=Registry()
        def action(value,ctx):calls.append(value);raise RuntimeError('unknown effect outcome')
        registry.register_tool(ToolSpec('fixture.act',action))
        body=('let child=bot("child",fn(s){42},fn(p,s){tool("fixture.act",p)},'+VERIFY+');'
              'let root=bot("root",fn(s){null},async fn(p,s){await child();42},'+VERIFY+');'
              'let answer=await root();process_step(answer.state,answer.output,answer.done)')
        row=self.finish(self.app(body,registry,['fixture.act']))
        self.assertEqual(row['status'],'needs_reconciliation');self.assertEqual(row['state'],{'kept':True});self.assertEqual(row['turn'],0)
        self.assertEqual(calls,[42]);self.assertIsNotNone(row['pending'])

    def test_child_checkpoint_cannot_be_returned_as_the_root_process_step(self):
        body=('let child=bot("child",fn(s){42},fn(p,s){p},'+VERIFY+');'
              'let root=bot("root",fn(s){null},async fn(p,s){await child()},'+VERIFY+');'
              'let answer=await root();let nested=answer.output;process_step(nested.state,nested.output,nested.done)')
        row=self.finish(self.app(body));self.assertEqual(row['status'],'needs_reconciliation')
        self.assertEqual(row['state'],{'kept':True});self.assertEqual(row['turn'],0)

    def test_independent_scoped_root_cannot_anchor_a_process_checkpoint(self):
        body=('let independent=bot("private",fn(s){42},fn(p,s){p},'+VERIFY+',mailbox="scoped");'
              'let answer=await independent();process_step(answer.state,answer.output,answer.done)')
        row=self.finish(self.app(body));self.assertEqual(row['status'],'needs_reconciliation')
        self.assertEqual(row['state'],{'kept':True});self.assertEqual(row['turn'],0)
