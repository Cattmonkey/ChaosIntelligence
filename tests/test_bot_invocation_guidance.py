"""Native ancestor staleness and run-wide mailbox bounds, without paid models."""
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Final, Framework, Registry, Settings
from chaos.store import Conflict
from chaos.runtime import Context
from test_bot_invocation_surfaces import service, targets_for


class HeldChild:
    def __init__(self):
        self.registry = Registry()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.provider_calls = []
        self.actions = []
        @self.registry.provider('fixture.held_child')
        def provider(request, context):
            self.provider_calls.append(request.input)
            self.entered.set()
            deadline = time.monotonic() + 12
            while not self.release.wait(.01):
                context.checkpoint()
                if time.monotonic() > deadline:
                    raise AssertionError('child planner fixture release missing')
            context.checkpoint()
            return Final('old child proposal')
        @self.registry.tool('fixture.action')
        def action(value, context):
            context.checkpoint()
            self.actions.append(value)
            return value

    def start(self, source):
        app = Framework(registry=self.registry, settings=Settings(':memory:'))
        app.create_project('ancestry', source=source, capabilities=['fixture.action'])
        run = app.start('ancestry', 1, None, mode='source')
        return app, run


def invocation_pair(app, run_id):
    rows = app.guidance_scopes(run_id)['scopes']
    root = next(row for row in rows if row['mailbox'] == 'run')
    child = next(row for row in rows if row['parentScopeId'] == root['scopeId'])
    return root, child


class BotInvocationGuidance(unittest.TestCase):
    def test_parent_planning_guidance_propagates_out_of_child_without_spinning_or_stealing(self):
        fixture = HeldChild()
        source = '''agent gate{provider:"fixture.held_child",tools:[]};
var parentPlans=0;
fn childPlan(s){gate(s.input)};
fn childExecute(p,s){tool("fixture.action",{role:"child",proposal:p})};
fn verify(s){{done:true,context:null,output:s.evidence[-1]}};
async fn parentPlan(s){parentPlans+=1;if length(s.guidance)>0{s.guidance[-1].input}else{
  let child=bot("nested",childPlan,childExecute,verify);
  (await child("child-input")).output
}};
let parent=bot("parent",parentPlan,fn(p,s){tool("fixture.action",{role:"parent",proposal:p})},verify);
let actual=await parent();{actual:actual,plans:parentPlans}'''
        app, run = fixture.start(source)
        try:
            self.assertTrue(fixture.entered.wait(5))
            root, child = invocation_pair(app, run.id)
            self.assertEqual(root['revision'], 0); self.assertEqual(child['revision'], 0)
            guidance = {'text': '新的父目标，不交给子邮箱 @child PASS', 'answer': 42}
            receipt = run.guide(guidance, 'parent-change')
            self.assertNotIn('target', receipt)
            fresh_root, fresh_child = invocation_pair(app, run.id)
            self.assertEqual(fresh_root['revision'], 1); self.assertEqual(fresh_child['revision'], 0)
            fixture.release.set()
            value = run.result(8)
            self.assertEqual(value['actual']['status'], 'achieved')
            self.assertEqual(value['plans'], 2)
            self.assertEqual(value['actual']['output'], {'role': 'parent', 'proposal': guidance})
            self.assertEqual(fixture.actions, [{'role': 'parent', 'proposal': guidance}])
            self.assertEqual(fixture.provider_calls, ['child-input'])
            events = app.events('ancestry', run_id=run.id, limit=300)['items']
            child_opens = [event for event in events if event['type'] == 'bot.scope.opened'
                           and event['payload'].get('parentScopeId') == root['scopeId']]
            self.assertEqual(len(child_opens), 1)
            adopted = [event['payload'] for event in events if event['type'] == 'bot.context.applied']
            self.assertEqual([item['scopeId'] for item in adopted], [root['scopeId']])
            self.assertEqual(adopted[0]['messageId'], 'parent-change')
        finally:
            fixture.release.set(); run.cancel(); app.close()

    def test_parent_execution_guidance_preserves_reconciliation_and_the_original_queue(self):
        fixture = HeldChild()
        source = '''agent gate{provider:"fixture.held_child",tools:[]};
var parentExecutions=0;
fn verify(s){{done:true,context:null,output:s.evidence[-1]}};
let child=bot("child",fn(s){gate(s.input)},fn(p,s){tool("fixture.action",{role:"child",proposal:p})},verify);
async fn execute(p,s){parentExecutions+=1;let actual=await child("child-input");tool("fixture.action",{role:"parent",proposal:actual.output})};
let parent=bot("parent",fn(s){42},execute,verify);
let actual=await parent();let remaining=await guidance_receive(0);
{status:actual.status,done:actual.done,executions:parentExecutions,remaining:remaining,child:bot_info(child)}'''
        app, run = fixture.start(source)
        try:
            self.assertTrue(fixture.entered.wait(5))
            root, child = invocation_pair(app, run.id)
            guidance = {'text': '执行已经进入，保留指导等待显式协调'}
            run.guide(guidance, 'after-execute-entry')
            fixture.release.set()
            value = run.result(8)
            self.assertEqual(value['status'], 'needs_reconciliation')
            self.assertFalse(value['done']); self.assertEqual(value['executions'], 1)
            self.assertEqual(value['child']['status'], 'failed')
            self.assertEqual(fixture.actions, [])
            self.assertEqual(fixture.provider_calls, ['child-input'])
            self.assertTrue(value['remaining']['received'])
            self.assertEqual(value['remaining']['message']['input'], guidance)
            self.assertNotIn('target', value['remaining']['message'])
            events = app.events('ancestry', run_id=run.id, limit=300)['items']
            self.assertEqual([event for event in events if event['type'] == 'bot.context.applied'], [])
            self.assertEqual(app.guidance_scopes(run.id), {'runId': run.id, 'active': False, 'scopes': []})
        finally:
            fixture.release.set(); run.cancel(); app.close()

    def test_queued_limit_is_global_across_two_actual_private_mailboxes(self):
        received = threading.Event()
        resume = threading.Event()
        original = Context.guidance_operation
        def hold_actual_receive(context, operation, payload, *args, **kwargs):
            packet = original(context, operation, payload, *args, **kwargs)
            if operation == 'bot_receive' and packet.get('received'):
                # The real host has popped its queue and set scope.pending.
                # Hold delivery outside Engine.idle so the native Bot cannot
                # adopt yet, while the caller can test real admission bounds.
                received.set()
                deadline = time.monotonic() + 8
                while not resume.wait(.01):
                    context.budget.tick()
                    if time.monotonic() > deadline:
                        raise AssertionError('actual receive fixture was not resumed')
            return packet
        with patch.object(Context, 'guidance_operation', hold_actual_receive):
            with service() as (app, client, gate, origin):
                run = client.start('scopes', 1, None, mode='source')
                try:
                    self.assertTrue(gate.entered['A'].wait(5)); self.assertTrue(gate.entered['B'].wait(5))
                    targets = targets_for(app, run.id)
                    for index in range(64):
                        receipt = app.guide(run.id, {'index': index}, 'queued-' + str(index),
                                            target=targets[index % 2])
                        self.assertEqual(receipt['revision'], index // 2 + 1)
                    before = app.guidance_scopes(run.id)
                    self.assertEqual(sorted(row['revision'] for row in before['scopes']), [32, 32])
                    gate.release.set()
                    self.assertTrue(received.wait(5), 'actual native Bot must receive an envelope')
                    with app._engine.idle:
                        boxes = list(app._engine.guidance[run.id]['botScopes'].values())
                        pending = sum(scope['pending'] is not None for scope in boxes)
                        queued_count = sum(len(scope['mailbox']['queue']) for scope in boxes)
                    self.assertGreater(pending, 0)
                    self.assertLess(queued_count, 64)
                    self.assertEqual(queued_count + pending, 64)
                    with self.assertRaises(Conflict):
                        app.guide(run.id, {'index': 64}, 'one-too-many', target=targets[0])
                    self.assertEqual(app.guidance_scopes(run.id), before)
                    events = app.events('scopes', run_id=run.id, limit=300)['items']
                    queued = [event for event in events if event['type'] == 'guidance.queued']
                    self.assertEqual(len(queued), 64)
                    self.assertTrue(any(event['type'] == 'guidance.received' for event in events))
                    self.assertEqual({event['payload']['target']['scopeId'] for event in queued},
                                     {target['scopeId'] for target in targets})
                finally:
                    run.cancel(); resume.set(); gate.release.set()
                self.assertEqual(run.wait(8)['status'], 'cancelled')

    def test_child_progress_uses_host_ancestry_and_keeps_public_identity_text_in_data(self):
        source = '''async fn childPlan(s){await progress_emit("plan",{summary:"child work",scopeId:"fake",parentScopeId:"fake",mailbox:"run"});42};
fn verify(s){{done:true,context:null,output:s.evidence[-1]}};
let child=bot("child",childPlan,fn(p,s){p},verify);
async fn execute(p,s){(await child()).output};
let parent=bot("parent",fn(s){null},execute,verify);await parent()'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('progress', source=source, capabilities=[])
            run = app.start('progress', 1, None, mode='source')
            self.assertEqual(run.result(8)['output'], 42)
            events = app.events('progress', run_id=run.id, limit=300)['items']
            opened = [event['payload'] for event in events if event['type'] == 'bot.scope.opened']
            root = next(row for row in opened if row['parentScopeId'] is None)
            child = next(row for row in opened if row['parentScopeId'] == root['scopeId'])
            progress = next(event['payload'] for event in events if event['type'] == 'bot.progress')
            self.assertEqual(progress['scopeId'], child['scopeId'])
            self.assertEqual(progress['parentScopeId'], root['scopeId'])
            self.assertEqual(progress['rootScopeId'], root['scopeId'])
            self.assertEqual(progress['botId'], child['botId'])
            self.assertEqual(progress['data']['scopeId'], 'fake')
            self.assertEqual(progress['data']['parentScopeId'], 'fake')
            self.assertNotIn('ticket', progress)


if __name__ == '__main__':
    unittest.main()
