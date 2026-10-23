"""Explicit Agent definitions retain the existing host effect and capability chain."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Registry, ProviderSpec, ToolSpec, ToolCall, Final
from chaos.agent_values import definition_hash
from chaos.language import LanguageError
from chaos.settings import Settings
from tests.test_harness_runtime import FakeClient, message


DEFINITION = {'kind': 'agent', 'name': 'worker',
              'config': {'provider': 'app.worker', 'prompt': 'original', 'tools': []}}


class AgentValuesIntegration(unittest.TestCase):
    def events(self, app, project, run=None):
        result, after = [], 0
        while True:
            page = app.events(project, run_id=run, after=after, limit=300)
            result.extend(page['items'])
            if page['nextCursor'] is None: return result
            after = page['nextCursor']

    def test_framework_pure_validation_export_and_schema_are_copies(self):
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('pure', source='null', capabilities=[])
            before = self.events(app, 'pure')
            supplied = copy.deepcopy(DEFINITION)
            checked = app.validate_agent(supplied, name='助手')
            self.assertEqual(checked['definitionHash'], definition_hash(DEFINITION))
            self.assertEqual(Framework.evaluate('import "agent.chaos" as A;agent_info(A.助手)',
                             files={'agent.chaos': checked['source']}), DEFINITION)
            supplied['config']['tools'].append('memory.put')
            self.assertEqual(checked['definition'], DEFINITION)
            schema = app.agent_schema()
            self.assertFalse(schema['additionalProperties'])
            self.assertEqual(schema['required'], [])
            self.assertIn('provider', schema['properties'])
            schema['properties'].clear()
            self.assertIn('provider', app.agent_schema()['properties'])
            self.assertNotIn('maxSteps', checked['definition']['config'])
            self.assertEqual(self.events(app, 'pure'), before)
            self.assertEqual(app.modes('pure')['items'], [])
        with self.assertRaises(RuntimeError): app.agent_schema()
        with self.assertRaises(RuntimeError): app.validate_agent(DEFINITION)

    def test_sdk_bounds_and_invalid_definitions_are_effect_free(self):
        calls = []; registry = Registry()
        registry.register_provider(ProviderSpec('app.worker', lambda r, c: calls.append(r) or Final(42)))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('invalid', source='agent_from(input)(1)', capabilities=[])
            for field in ('_platformProposal', 'apiKey', 'baseURL', 'backend'):
                bad = {**DEFINITION, 'config': {**DEFINITION['config'], field: 'forbidden'}}
                with self.subTest(field=field):
                    with self.assertRaises((ValueError, LanguageError)): app.validate_agent(bad)
                    self.assertEqual(app.start('invalid', 1, bad, mode='source').wait()['status'], 'failed')
            with self.assertRaises(ValueError):
                app.validate_agent({**DEFINITION, 'config': {'prompt': 'x' * 100001}})
            self.assertEqual(calls, [])
            self.assertFalse(any(e['type'] == 'agent.observing' for e in self.events(app, 'invalid')))

    def test_client_uses_one_pure_validation_and_schema_contract(self):
        client = Client('http://127.0.0.1:1')
        with patch.object(client, '_request', return_value={'validated': True}) as request:
            self.assertEqual(client.validate_agent(DEFINITION, name='example'), {'validated': True})
            request.assert_called_once_with('POST', '/agents/validate', {'definition': DEFINITION, 'name': 'example'})
            request.reset_mock(); client.agent_schema()
            request.assert_called_once_with('GET', '/agents/schema')

    def test_provider_definition_becomes_new_plan_binding_without_mutating_old_agent(self):
        proposal = {**DEFINITION, 'name': 'improved', 'config': {**DEFINITION['config'], 'prompt': 'double'}}
        seen = []; registry = Registry()
        registry.register_provider(ProviderSpec('app.designer', lambda r, c: Final(proposal)))
        def worker(request, context):
            seen.append((request.name, request.prompt, request.input))
            return Final(request.input['input'] * 2 if request.prompt == 'double' else request.input['input'] + 1)
        registry.register_provider(ProviderSpec('app.worker', worker))
        source = '''let original=agent_from(input);
agent designer{provider:"app.designer"};
let replacement=agent_from(designer({current:agent_info(original),target:"double"}));
let p=plan([{id:"answer",call:"worker"}]);
let oldBindings={worker:original};
[await p(21,oldBindings),await p(21,{worker:replacement}),await p(21,oldBindings),
 agent_info(original),agent_info(replacement)]'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('derive', source=source, capabilities=[])
            run = app.start('derive', 1, DEFINITION, mode='source'); result = run.result()
            self.assertEqual(result, [{'answer': 22}, {'answer': 42}, {'answer': 22}, DEFINITION, proposal])
            events = self.events(app, 'derive', run.id)
            for kind in ('agent.observing', 'agent.completed'):
                rows = [e['payload'] for e in events if e['type'] == kind and e['payload']['agent'] != 'designer']
                self.assertEqual([r['agentDefinitionHash'] for r in rows],
                                 [definition_hash(DEFINITION), definition_hash(proposal), definition_hash(DEFINITION)])
                self.assertTrue(all(set(r) == {'version', 'nodeId', 'agent', 'provider', 'agentDefinitionHash'} for r in rows))
            self.assertEqual([x[:2] for x in seen], [('worker', 'original'), ('improved', 'double'), ('worker', 'original')])

    def test_legacy_unknown_config_keeps_call_semantics_and_omits_public_hash(self):
        registry = Registry(); registry.register_provider(ProviderSpec('app.legacy', lambda r, c: Final(r.input)))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('legacy', source='agent old {provider:"app.legacy",_legacySetting:{anything:true}};old(42)', capabilities=[])
            run = app.start('legacy', 1, None, mode='source'); self.assertEqual(run.result(), 42)
            rows = [e for e in self.events(app, 'legacy', run.id) if e['type'] in ('agent.observing', 'agent.completed')]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all('agentDefinitionHash' not in row['payload'] for row in rows))

    def test_public_metadata_failure_cannot_change_legacy_invocation(self):
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('metadata', source='agent old {provider:"local",mode:"review"};old(42)', capabilities=[])
            with patch('chaos.agent_values.definition_hash', side_effect=ValueError('metadata unavailable')):
                result = app.start('metadata', 1, None, mode='source').result()
            self.assertEqual(result['result'], 42)
            self.assertFalse(any('agentDefinitionHash' in e['payload'] for e in self.events(app, 'metadata')))

    def test_import_export_and_platform_label_do_not_grant_tools(self):
        seen = []; registry = Registry()
        registry.register_tool(ToolSpec('app.echo', lambda v, c: seen.append(v) or v))
        registry.register_provider(ProviderSpec('app.worker', lambda r, c: ToolCall('app.echo', r.input)))
        definition = {**DEFINITION, 'config': {'provider': 'app.worker', 'tools': ['app.echo'], 'level': 'platform'}}
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            source = app.validate_agent(definition)['source']
            app.create_project('denied', source='import "agent.chaos" as A;A.saved_agent(42)',
                               files={'agent.chaos': source}, capabilities=[])
            failed = app.start('denied', 1, None, mode='source').wait()
            self.assertEqual(failed['status'], 'failed'); self.assertIn('未授予', failed['error'])
            self.assertEqual(seen, [])
            self.assertFalse(any(e['type'] == 'agent.observing' for e in self.events(app, 'denied')))

    def test_macro_decorator_generator_and_async_use_the_same_callable(self):
        registry = Registry(); registry.register_provider(ProviderSpec('app.worker', lambda r, c: Final(r.input * 2)))
        source = '''macro create(factory,definition){quote{unquote(factory)(unquote(definition))}}
fn make(){create!(agent_from,input)}
fn keep(value){value}
@keep
fn job(value){let worker=make();worker(input=value)}
fn* values(){yield 20;yield 21}
async fn work(value){job(value)}
let jobs=map(fn(value){work(value)},collect(values()));await gather(jobs)'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('composition', source=source, capabilities=[])
            self.assertEqual(app.start('composition', 1, DEFINITION, mode='source').result(), [40, 42])

    def test_recording_agent_tools_creates_real_skill_and_replay_checks_current_grants(self):
        seen = []; registry = Registry()
        registry.register_tool(ToolSpec('app.echo', lambda v, c: seen.append(v) or v, version='3'))
        def worker(request, context):
            return Final(request.history[-1]['output']) if request.history else ToolCall('app.echo', request.input)
        registry.register_provider(ProviderSpec('app.worker', worker))
        definition = {**DEFINITION, 'config': {'provider': 'app.worker', 'tools': ['app.echo']}}
        source = '''let worker=agent_from(input);async fn action(value){worker(value)}
let captured=await record_skill(action,{text:"original"},[{name:"text",step:0,path:["text"]}]);
[captured.output,await replay_skill(captured.skill,{text:"changed"}),mode_info(captured.skill)]'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('record', source=source, capabilities=['app.echo'])
            result = app.start('record', 1, definition, mode='source').result()
            self.assertEqual(result[:2], [{'text': 'original'}, [{'text': 'changed'}]])
            capture = app.modes('record')['items'][0]
            self.assertEqual(capture['status'], 'ready')
            self.assertEqual(capture['checkpoint']['receipts'][0]['toolVersion'], '3')
            app.create_project('replay', source='await replay_skill(mode_from(input),{text:"blocked"})', capabilities=[])
            self.assertEqual(app.start('replay', 1, result[2], mode='source').wait()['status'], 'failed')
            self.assertEqual(seen, [{'text': 'original'}, {'text': 'changed'}])

    def test_cancelled_agent_recording_never_seals_skill(self):
        entered = threading.Event(); registry = Registry()
        def wait(value, context):
            entered.set()
            while True: context.checkpoint(); time.sleep(.002)
        registry.register_tool(ToolSpec('app.wait', wait))
        registry.register_provider(ProviderSpec('app.worker', lambda r, c: ToolCall('app.wait', r.input)))
        definition = {**DEFINITION, 'config': {'provider': 'app.worker', 'tools': ['app.wait']}}
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('cancel', source='let a=agent_from(input);async fn action(x){a(x)};await record_skill(action,42)', capabilities=['app.wait'])
            run = app.start('cancel', 1, definition, mode='source')
            self.assertTrue(entered.wait(3)); run.cancel()
            self.assertEqual(run.wait(5)['status'], 'cancelled')
            capture = app.modes('cancel')['items'][0]
            self.assertEqual(capture['status'], 'incomplete')
            self.assertTrue(all(r['status'] != 'completed' for r in capture['checkpoint']['receipts']))
            self.assertFalse(any(e['type'] == 'agent.completed' for e in self.events(app, 'cancel', run.id)))

    def test_goal_checkpoint_restores_definition_data_by_explicit_reconstruction(self):
        entered = threading.Event(); calls = []; registry = Registry()
        def advance(request, context):
            calls.append(request.input)
            if len(calls) == 2:
                entered.set()
                while True: context.checkpoint(); time.sleep(.002)
            return Final(request.input + 1)
        registry.register_provider(ProviderSpec('app.worker', advance))
        source = '''fn planner(ctx){plan([{id:"advance",call:"advance"}])}
fn advance(ctx){let a=agent_from(ctx.input.definition);{definition:agent_info(a),value:a(ctx.input.value)}}
fn verify(ctx){{done:ctx.results.advance.value>=42,state:ctx.results.advance,evidence:{observed:ctx.results.advance.value}}}
await pursue(goal("reach 42","planner","verify",3),{planner:planner,advance:advance,verify:verify},input,key="answer")'''
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'project.sqlite3'
            with Framework(database, registry=registry, settings=Settings(':memory:')) as app:
                app.create_project('resume', source=source, capabilities=[])
                run = app.start('resume', 1, {'definition': DEFINITION, 'value': 40}, mode='source')
                self.assertTrue(entered.wait(3)); run.cancel()
                self.assertEqual(run.wait(5)['status'], 'cancelled')
                captured = app.mode('resume', 'answer')
                self.assertEqual(captured['status'], 'needs_reconciliation')
                self.assertEqual(captured['checkpoint']['state'], {'definition': DEFINITION, 'value': 41})
                self.assertEqual(captured['checkpoint']['iteration'], 2)
                self.assertNotIn('$callable', json.dumps(captured['checkpoint']))
            with Framework(database, registry=registry, settings=Settings(':memory:')) as app:
                captured = app.mode('resume', 'answer')
                app.reconcile_goal('resume', 'answer', captured['revision'], captured['checkpoint']['state'], evidence={'checked': True})
                result = app.start('resume', 1, {'definition': DEFINITION, 'value': 40}, mode='source').result()
                self.assertEqual(result['state'], {'definition': DEFINITION, 'value': 42})
                self.assertEqual(result['iterations'], 3); self.assertTrue(result['done'])
                self.assertEqual(calls, [40, 41, 41])
                self.assertEqual(app.mode('resume', 'answer')['status'], 'completed')

    def test_definition_hash_does_not_pin_settings_and_native_plugin_still_required(self):
        selected = []; clients = []
        def client(config):
            selected.append((config['model'], config['maxSteps']))
            result = FakeClient([message(config['model'])]); clients.append(result); return result
        settings = Settings(':memory:', client_factory=client)
        settings.update({'providers': {'text': {'apiKey': 'fictional-agent-values-secret'}},
                         'roles': {'executor': {'model': 'fixture-a', 'autoUpgrade': False}},
                         'runtime': {'maxSteps': 7}}, 1)
        definition = {'kind': 'agent', 'name': 'dynamic', 'config': {'provider': 'model', 'tools': []}}
        with Framework(settings=settings) as app:
            app.create_project('routing', source='agent_from(input)(42)', capabilities=[])
            first = app.start('routing', 1, definition, mode='source'); self.assertEqual(first.result(), 'fixture-a')
            settings.update({'roles': {'executor': {'model': 'fixture-b'}}, 'runtime': {'maxSteps': 8}}, settings.public()['version'])
            second = app.start('routing', 1, definition, mode='source'); self.assertEqual(second.result(), 'fixture-b')
            self.assertEqual(selected, [('fixture-a', 7), ('fixture-b', 8)])
            hashes = []; routes = []
            for run in (first, second):
                events = self.events(app, 'routing', run.id)
                hashes.append(next(e['payload']['agentDefinitionHash'] for e in events
                                   if e['type'] == 'agent.completed' and e['payload'].get('agent') == 'dynamic'))
                routes.append(next(e['payload'] for e in events if e['type'] == 'route.selected'))
                self.assertNotIn('fictional-agent-values-secret', json.dumps(events))
            self.assertEqual(hashes, [definition_hash(definition)] * 2)
            self.assertEqual([r['model'] for r in routes], ['fixture-a', 'fixture-b'])
            self.assertNotEqual(routes[0]['configVersion'], routes[1]['configVersion'])
            settings.update({'plugins': [{'id': 'native', 'enabled': False}]}, settings.public()['version'])
            self.assertEqual(app.start('routing', 1, definition, mode='source').wait()['status'], 'failed')
            self.assertEqual(len(clients), 2); self.assertTrue(all(c.closed for c in clients))


if __name__ == '__main__': unittest.main()
