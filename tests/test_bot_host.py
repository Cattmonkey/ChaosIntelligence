"""Actual Engine guidance producer races against guarded host action admission."""
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid
import unittest

from chaos import Framework, Registry, ToolSpec, ProviderSpec, ToolCall, Settings
from chaos.bot_host import BotStale, control, admit, finish
from chaos.language import LanguageError, ResourceLimit
from chaos.runtime import Context
from chaos.store import Conflict


class BotHostAdmission(unittest.TestCase):
    def rig(self, handler=None, provider=None, capabilities=None):
        effects = []
        registry = Registry()
        registry.register_tool(ToolSpec('probe', handler or (lambda value, c: effects.append(value) or value), effect='write'))
        if provider is not None:
            registry.register_provider(ProviderSpec('fixture', provider))
        app = Framework(':memory:', registry=registry, settings=Settings(':memory:'))
        self.addCleanup(app.close)
        project = app.create_project('host-bot', source='await sleep(20)', capabilities=capabilities if capabilities is not None else ['probe'])
        handle = app.start(project['id'], project['version'], None, mode='source')
        ctx = Context(app._engine, handle.id, project, app._engine.active[handle.id][0])
        opened = control(ctx, 'bot_open', {'scopeId': 'bot-scope-' + uuid.uuid4().hex})
        ticket = control(ctx, 'bot_decision', {'scopeId': opened['scopeId'], 'revision': opened['revision'], 'phase': 'execution'})
        guard = {'scopeId': opened['scopeId'], 'decisionId': ticket['decisionId'], 'revision': ticket['revision'], 'ticket': ticket['ticket']}
        ctx.bot_guard = guard
        return app, handle, ctx, guard, effects

    def test_accepted_guide_before_host_tool_admission_never_enters_handler(self):
        app, run, ctx, guard, effects = self.rig()
        run.guide('new direction', message_id='new')
        with self.assertRaises(BotStale):
            ctx.tool('probe', 'old', {'probe'}, 'executor')
        self.assertEqual(effects, [])
        self.assertFalse(any(e['type'] == 'bot.action.admitted' for e in app.events('host-bot', run_id=run.id)['items']))

    def test_admitted_work_finishes_once_after_guide_and_suffix_is_refused(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def handler(value, context):
            calls.append(value); entered.set()
            self.assertTrue(release.wait(3))
            return value
        app, run, ctx, guard, unused = self.rig(handler)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ctx.tool, 'probe', 'completed-prefix', {'probe'}, 'executor')
            try:
                self.assertTrue(entered.wait(3))
                run.guide('replace suffix', message_id='replace')
            finally:
                release.set()
            self.assertEqual(future.result(3), 'completed-prefix')
        with self.assertRaises(BotStale):
            ctx.tool('probe', 'old-suffix', {'probe'}, 'executor')
        outcome = control(ctx, 'bot_outcome', {'scopeId': guard['scopeId'], 'decisionId': guard['decisionId']})
        self.assertEqual(calls, ['completed-prefix'])
        self.assertEqual([(r['kind'], r['status']) for r in outcome['actions']], [('tool:probe', 'completed')])

    def test_provider_selected_tool_inherits_guard_and_checks_actual_admission(self):
        holder = {}; calls = []
        def provider(request, context):
            holder['run'].guide('new provider context', message_id='during-provider')
            return ToolCall('probe', 'stale-tool')
        app, run, ctx, guard, effects = self.rig(provider=provider)
        holder['run'] = run
        with self.assertRaises(BotStale):
            ctx.agent('worker', {'provider': 'fixture', 'tools': ['probe']}, 42, {'probe'}, 'executor')
        self.assertEqual(effects, [])

    def test_context_copy_and_public_tool_json_cannot_forge_a_ticket(self):
        app, run, ctx, guard, effects = self.rig()
        fake = {'_bot_guard': {'ticket': 'forged', 'revision': 999}}
        self.assertEqual(ctx.tool('probe', fake, {'probe'}, 'executor'), fake)
        self.assertEqual(effects, [fake])
        run.guide('changed', message_id='changed')
        with self.assertRaises(BotStale):
            ctx.tool('probe', fake, {'probe'}, 'executor')
        self.assertEqual(effects, [fake])

    def test_failed_context_adoption_journal_preserves_pending_input(self):
        app, run, ctx, guard, effects = self.rig()
        run.guide({'x': [1]}, message_id='pending')
        first = control(ctx, 'bot_receive', {'scopeId': guard['scopeId']})
        first['message']['input']['x'].append(2)
        original = ctx.event
        def fail(kind, payload):
            if kind == 'bot.context.applied':
                raise RuntimeError('fixture journal failure')
            return original(kind, payload)
        ctx.event = fail
        with self.assertRaises(RuntimeError):
            control(ctx, 'bot_adopt', {'scopeId': guard['scopeId'], 'messageId': 'pending', 'revision': 1})
        second = control(ctx, 'bot_receive', {'scopeId': guard['scopeId']})
        self.assertEqual(second['message']['input'], {'x': [1]})
        ctx.event = original
        control(ctx, 'bot_close', {'scopeId': guard['scopeId']})
        self.assertEqual(app._engine.receive_guidance(ctx)['message']['input'], {'x': [1]})

    def test_mailbox_lease_refuses_other_consumers_and_closed_ticket_cannot_reenter(self):
        app, run, ctx, guard, effects = self.rig()
        with self.assertRaises(LanguageError):
            app._engine.receive_guidance(ctx)
        with self.assertRaises(LanguageError):
            control(ctx, 'bot_open', {'scopeId': 'bot-scope-' + uuid.uuid4().hex})
        control(ctx, 'bot_outcome', {'scopeId': guard['scopeId'], 'decisionId': guard['decisionId']})
        with self.assertRaises(BotStale):
            admit(ctx, guard, 'tool:probe')
        control(ctx, 'bot_close', {'scopeId': guard['scopeId']})
        with self.assertRaises(LanguageError):
            admit(ctx, guard, 'tool:probe')

    def test_global_resource_limit_outranks_stale_without_an_admission(self):
        app, run, ctx, guard, effects = self.rig()
        run.guide('stale', message_id='limit')
        ctx.budget.limit = -1
        with self.assertRaises(ResourceLimit):
            admit(ctx, guard, 'tool:probe')
        self.assertEqual(effects, [])

    def test_narrowed_scope_never_adds_tool_authority(self):
        app, run, ctx, guard, effects = self.rig(capabilities=['probe', 'text.stats'])
        ctx.agent_capabilities = set()
        with self.assertRaises(LanguageError):
            ctx.tool('probe', 42, {'probe'}, 'executor')
        self.assertEqual(effects, [])

    def test_adoption_is_exact_and_received_is_distinct_from_applied(self):
        app, run, ctx, guard, effects = self.rig()
        run.guide(None, message_id='null')
        item = control(ctx, 'bot_receive', {'scopeId': guard['scopeId']})
        self.assertIsNone(item['message']['input'])
        with self.assertRaises(LanguageError):
            control(ctx, 'bot_adopt', {'scopeId': guard['scopeId'], 'messageId': 'null', 'revision': 2})
        events = app.events('host-bot', run_id=run.id)['items']
        self.assertEqual([e['type'] for e in events if e['type'].startswith('guidance.')], ['guidance.queued', 'guidance.received'])
        self.assertFalse(any(e['type'] == 'bot.context.applied' for e in events))
        control(ctx, 'bot_adopt', {'scopeId': guard['scopeId'], 'messageId': 'null', 'revision': 1})
        self.assertEqual(sum(e['type'] == 'bot.context.applied' for e in app.events('host-bot', run_id=run.id)['items']), 1)

    def test_final_result_commit_compares_current_guidance_under_the_same_lock(self):
        app, run, ctx, guard, effects = self.rig()
        accepted = control(ctx, 'bot_commit', {'scopeId': guard['scopeId'], 'revision': 0, 'terminal': False})
        self.assertTrue(accepted['committed'])
        run.guide('new completion requirement', message_id='after-verify')
        rejected = control(ctx, 'bot_commit', {'scopeId': guard['scopeId'], 'revision': 0, 'terminal': True})
        self.assertFalse(rejected['committed'])
        self.assertEqual(rejected['revision'], 1)
        self.assertEqual(sum(e['type'] == 'bot.context.committed' for e in app.events('host-bot', run_id=run.id)['items']), 1)

    def test_terminal_commit_wins_over_new_guide_until_scope_retires(self):
        app, run, ctx, guard, effects = self.rig()
        self.assertTrue(control(ctx, 'bot_commit', {'scopeId': guard['scopeId'], 'revision': 0, 'terminal': True})['committed'])
        with self.assertRaises(Conflict):
            run.guide('too late for this Bot', message_id='late')
        with self.assertRaises(BotStale):
            admit(ctx, guard, 'tool:probe')
        control(ctx, 'bot_close', {'scopeId': guard['scopeId']})
        self.assertEqual(run.guide('future program work', message_id='future')['status'], 'queued')

    def test_failed_competing_open_cannot_retire_the_current_owner(self):
        app, run, ctx, guard, effects = self.rig()
        other = 'bot-scope-' + uuid.uuid4().hex
        with self.assertRaises(LanguageError):
            control(ctx, 'bot_open', {'scopeId': other})
        with self.assertRaises(LanguageError):
            control(ctx, 'bot_close', {'scopeId': other})
        self.assertEqual(app._engine.guidance[run.id]['botScope']['id'], guard['scopeId'])
        control(ctx, 'bot_close', {'scopeId': guard['scopeId']})
        self.assertTrue(control(ctx, 'bot_close', {'scopeId': other})['closed'])
        self.assertFalse(app._engine.receive_guidance(ctx)['received'])

    def test_close_journal_failure_still_retires_owned_lease_and_restores_message(self):
        app, run, ctx, guard, effects = self.rig()
        run.guide('preserved', message_id='close-failure')
        control(ctx, 'bot_receive', {'scopeId': guard['scopeId']})
        original = ctx.event
        def fail(kind, payload):
            if kind == 'bot.scope.closed':
                raise RuntimeError('fixture closing journal failure')
            return original(kind, payload)
        ctx.event = fail
        with self.assertRaises(RuntimeError):
            control(ctx, 'bot_close', {'scopeId': guard['scopeId']})
        ctx.event = original
        self.assertEqual(app._engine.receive_guidance(ctx)['message']['input'], 'preserved')
        self.assertTrue(control(ctx, 'bot_close', {'scopeId': guard['scopeId']})['closed'])


if __name__ == '__main__':
    unittest.main()
