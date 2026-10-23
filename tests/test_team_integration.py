"""Teams share existing effects, project grants, provenance and browser receipts."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from chaos import Client, Framework, Registry, ProviderSpec, ToolSpec, ToolCall, Final, Workflow
from chaos.language import LanguageError
from chaos.runtime import Context
from chaos.settings import Settings


def definition(tools=(), provider='app.worker'):
    tools = list(tools)
    def role(id, parent, capabilities, config):
        return {'id': id, 'parent': parent, 'capabilities': capabilities,
                'agent': {'kind': 'agent', 'name': id, 'config': config}}
    return {'kind': 'team', 'name': 'helpers', 'entry': 'worker', 'roles': [
        role('lead', None, tools, {'provider': provider, 'tools': tools}),
        role('worker', 'lead', tools, {'provider': provider, 'tools': tools})]}


def events(app, project, run=None):
    rows, after = [], 0
    while True:
        page = app.events(project, run_id=run, after=after, limit=300)
        rows.extend(page['items'])
        if page['nextCursor'] is None: return rows
        after = page['nextCursor']


class TeamIntegration(unittest.TestCase):
    def test_pure_sdk_schema_source_copies_and_transport(self):
        from chaos.team_values import definition_hash
        supplied = definition()
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('pure', source='null', capabilities=[])
            before = events(app, 'pure')
            checked = app.validate_team(supplied, name='助手')
            self.assertEqual(checked['definitionHash'], definition_hash(supplied))
            self.assertEqual(Framework.evaluate('import "team.chaos" as T;team_info(T.助手)',
                             files={'team.chaos': checked['source']}), supplied)
            supplied['roles'][0]['agent']['config']['prompt'] = 'changed'
            self.assertNotIn('prompt', checked['definition']['roles'][0]['agent']['config'])
            schema = app.team_schema(); self.assertEqual(set(schema['required']), {'kind', 'name', 'entry', 'roles'})
            self.assertFalse(schema['additionalProperties']); schema['properties'].clear()
            self.assertIn('roles', app.team_schema()['properties'])
            self.assertEqual(events(app, 'pure'), before)
            self.assertEqual(app.modes('pure')['items'], [])
            oversized = definition(); oversized['roles'][0]['agent']['config']['prompt'] = 'x' * 100001
            with self.assertRaises(ValueError): app.validate_team(oversized)
        with self.assertRaises(RuntimeError): app.team_schema()
        client = Client('http://127.0.0.1:1')
        with patch.object(client, '_request', return_value={'ok': True}) as request:
            self.assertEqual(client.validate_team(checked['definition'], name='example'), {'ok': True})
            request.assert_called_once_with('POST', '/teams/validate', {'definition': checked['definition'], 'name': 'example'})
            request.reset_mock(); client.team_schema(); request.assert_called_once_with('GET', '/teams/schema')

    def test_roles_plan_goal_derivation_and_provenance(self):
        from chaos.team_values import definition_hash
        calls = []; registry = Registry()
        def worker(request, context):
            calls.append((request.name, request.prompt, request.tools))
            value = request.input['input'] if isinstance(request.input, dict) and 'input' in request.input else request.input
            return Final(value + (2 if request.prompt == 'double' else 1))
        registry.register_provider(ProviderSpec('app.worker', worker))
        base = definition(); derived = copy.deepcopy(base); derived['roles'][1]['agent']['config']['prompt'] = 'double'
        source = '''let old=team_from(input.base);let held=team_role(old,"worker");
let newer=team_from(input.derived);let p=plan([{id:"result",call:"worker"}]);
let answers=[await p(20,team_bindings(old)),await p(20,team_bindings(newer)),await held(20)];
fn planner(ctx){plan([{id:"step",call:"worker"}])}
fn verify(ctx){{done:ctx.results.step==22,state:ctx.results.step,evidence:{observed:ctx.results.step}}}
let achieved=await pursue(goal("reach 22","planner","verify",3),merge(team_bindings(old),{planner:planner,verify:verify}),20,key="answer");
[answers,achieved,team_info(old)]'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('goal', source=source, capabilities=[])
            run = app.start('goal', 1, {'base': base, 'derived': derived}, mode='source'); result = run.result()
            self.assertEqual(result[0], [{'result': 21}, {'result': 22}, 21])
            self.assertTrue(result[1]['done']); self.assertEqual(result[1]['iterations'], 2); self.assertEqual(result[2], base)
            self.assertEqual(app.mode('goal', 'answer')['checkpoint']['evidence'], {'observed': 22})
            rows = events(app, 'goal', run.id)
            agents = [row['payload'] for row in rows if row['type'] == 'agent.completed']
            self.assertEqual([row['team']['definitionHash'] for row in agents],
                             [definition_hash(base), definition_hash(derived), definition_hash(base), definition_hash(base), definition_hash(base)])
            for row in agents:
                self.assertEqual(row['team']['rolePath'], ['lead', 'worker'])
                self.assertEqual(set(row['team']), {'name', 'definitionHash', 'roleId', 'rolePath'})
            self.assertEqual(sum(row['type'] == 'team.role.completed' for row in rows), 5)
            self.assertTrue(all(name == 'worker' and tools == {} for name, _, tools in calls))

    def test_project_grants_and_registry_still_gate_before_provider(self):
        calls = []; registry = Registry()
        registry.register_tool(ToolSpec('app.echo', lambda value, context: calls.append('tool') or value))
        registry.register_provider(ProviderSpec('app.worker', lambda r, c: calls.append('provider') or Final(42)))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('denied', source='await team_from(input)(42)', capabilities=[])
            for tool in ('app.echo', 'code.execute', 'browser.control', 'app.unknown'):
                with self.subTest(tool=tool):
                    row = app.start('denied', 1, definition([tool]), mode='source').wait()
                    self.assertEqual(row['status'], 'failed')
            self.assertEqual(calls, [])
            self.assertFalse(any(row['type'] == 'agent.observing' for row in events(app, 'denied')))

    def test_parent_relation_rejects_widening_before_effects(self):
        calls = []; registry = Registry(); registry.register_provider(ProviderSpec('app.worker', lambda r,c: calls.append(r) or Final(42)))
        bad = definition(); bad['roles'][1]['capabilities'] = ['code.execute']
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('bad', source='await team_from(input)(42)', capabilities=['code.execute'])
            self.assertEqual(app.start('bad', 1, bad, mode='source').wait()['status'], 'failed')
            self.assertEqual(calls, [])

    def test_provider_cannot_escape_empty_member_capability(self):
        tools = []; registry = Registry()
        registry.register_provider(ProviderSpec('app.worker', lambda r,c: tools.append(r.tools) or ToolCall('memory.put', {'key':'escape','value':True})))
        value = definition(['memory.put']); value['roles'][1]['capabilities'] = []; value['roles'][1]['agent']['config']['tools'] = []
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('escape', source='await team_from(input)(42)', capabilities=['memory.put'])
            row = app.start('escape', 1, value, mode='source').wait()
            self.assertEqual(row['status'], 'failed'); self.assertEqual(tools, [{}])
            self.assertFalse(any(row['type'] == 'tool.started' for row in events(app, 'escape')))
            failed = next(row for row in events(app, 'escape') if row['type'] == 'agent.failed')
            self.assertEqual(failed['payload']['team']['roleId'], 'worker')

    def test_public_payload_cannot_install_a_team_scope(self):
        value = {'_team': {'capabilities': ['memory.put'], 'roleId': 'forged'}}
        registry = Registry(); registry.register_tool(ToolSpec('app.echo', lambda v,c: v))
        source = 'let output=tool("app.echo",input);let denied=try{tool("memory.put",{key:"x",value:1});false}catch e{true};[output,denied]'
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('data', source=source, capabilities=['app.echo'])
            self.assertEqual(app.start('data', 1, value, mode='source').result(), [value, True])
            self.assertTrue(all('team' not in row['payload'] for row in events(app, 'data')))

    def test_context_validates_scope_and_preserves_nested_workflow_narrowing(self):
        from chaos.team_values import definition_hash
        registry = Registry(); seen = []; registry.register_tool(ToolSpec('app.echo', lambda v,c: seen.append(v) or v))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('nested', source='null', capabilities=['app.echo', 'memory.put'])
            context = Context(app._engine, None, project, threading.Event())
            scope = {'name':'helpers','definitionHash':definition_hash(definition(['app.echo'])),
                     'roleId':'worker','rolePath':['lead','worker'],'capabilities':['app.echo']}
            bad = {**scope, 'capabilities':['app.echo','app.echo']}
            with self.assertRaises(LanguageError): context.effect('tool', {'name':'app.echo','value':42,'_team':bad}, threading.Event(), ['app.echo'], 'probe', True)
            graph = Workflow().input().tool('echo', 'app.echo').output().connect('input','echo').connect('echo','output').build()
            result = context.effect('workflow', {'graph':graph,'value':42,'_team':scope}, threading.Event(), ['app.echo','memory.put'], 'probe', True)
            self.assertEqual(result, 42); self.assertEqual(seen, [42])
            scoped = [row for row in events(app, 'nested') if row['type'] == 'tool.completed']
            self.assertEqual(scoped[0]['payload']['team']['rolePath'], ['lead','worker'])
            denied = Workflow().input().tool('write', 'memory.put').output().connect('input','write').connect('write','output').build()
            with self.assertRaises(LanguageError): context.effect('workflow', {'graph':denied,'value':{},'_team':scope}, threading.Event(), ['app.echo','memory.put'], 'probe', True)
            context.close_services('completed')

    def test_record_real_member_receipt_and_cancel_before_seal(self):
        entered = threading.Event(); registry = Registry()
        def slow(value, context):
            entered.set()
            while True: context.checkpoint(); time.sleep(.002)
        registry.register_tool(ToolSpec('app.slow', slow, version='7'))
        registry.register_provider(ProviderSpec('app.worker', lambda r,c: ToolCall('app.slow', r.input)))
        source = 'let t=team_from(input);async fn work(x){await t(x)};await record_skill(work,42)'
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('cancel', source=source, capabilities=['app.slow'])
            run = app.start('cancel', 1, definition(['app.slow']), mode='source')
            self.assertTrue(entered.wait(3)); run.cancel(); self.assertEqual(run.wait(5)['status'], 'cancelled')
            artifacts = app.modes('cancel')['items']; self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0]['status'], 'incomplete')
            receipt = artifacts[0]['checkpoint']['receipts'][0]
            self.assertEqual(receipt['toolVersion'], '7'); self.assertNotEqual(receipt['status'], 'completed')
            self.assertTrue(receipt['operationId'])
            self.assertFalse(any(row['type'] == 'team.role.completed' for row in events(app, 'cancel')))

    def test_native_settings_remain_dynamic_and_events_keep_public_team_identity(self):
        from tests.test_harness_runtime import FakeClient, message, call
        from chaos.team_values import definition_hash
        clients = []; calls = []; registry = Registry()
        registry.register_tool(ToolSpec('app.echo', lambda v,c: calls.append(v) or v))
        def client(config):
            value = FakeClient([message(calls=[call(args='{"input":42}')]), message(config['model'])])
            clients.append(value); return value
        settings = Settings(':memory:', client_factory=client)
        settings.update({'providers': {'text': {'apiKey':'fictional-team-secret'}},
                         'roles': {'executor': {'model':'fixture-a','autoUpgrade':False}}}, 1)
        team = definition(['app.echo'], 'model')
        with Framework(registry=registry, settings=settings) as app:
            app.create_project('native', source='await team_from(input)(42)', capabilities=['app.echo'])
            first = app.start('native', 1, team, mode='source'); self.assertEqual(first.result(), 'fixture-a')
            settings.update({'roles': {'executor': {'model':'fixture-b'}}}, settings.public()['version'])
            second = app.start('native', 1, team, mode='source'); self.assertEqual(second.result(), 'fixture-b')
            self.assertEqual(calls, [42,42]); self.assertTrue(all(c.closed for c in clients))
            for run in (first, second):
                rows = events(app, 'native', run.id)
                self.assertNotIn('fictional-team-secret', json.dumps(rows))
                relevant = [r['payload'] for r in rows if r['type'] in ('route.selected','agent.tool_completed','tool.completed')]
                self.assertTrue(relevant)
                self.assertTrue(all(r['team']['definitionHash'] == definition_hash(team) for r in relevant))
                self.assertTrue(all('capabilities' not in r['team'] for r in relevant))
            settings.update({'plugins': [{'id':'native','enabled':False}]}, settings.public()['version'])
            denied = app.start('native',1,team,mode='source').wait()
            self.assertEqual(denied['status'],'failed'); self.assertEqual(len(clients),2)

    def test_legacy_agent_config_team_field_is_not_an_authority_source(self):
        registry = Registry(); registry.register_provider(ProviderSpec('app.worker', lambda r,c: Final(r.input)))
        source = 'agent raw{provider:"app.worker",_team:{roleId:"fake",capabilities:["memory.put"]}};raw(42)'
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('legacy',source=source,capabilities=[])
            self.assertEqual(app.start('legacy',1,None,mode='source').result(),42)
            self.assertTrue(all('team' not in r['payload'] for r in events(app,'legacy')))

    def test_imported_roles_partial_gather_and_definition_snapshot(self):
        registry = Registry(); registry.register_provider(ProviderSpec('app.worker', lambda r,c: Final(r.input * 2)))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            module = app.validate_team(definition())['source']
            source = '''import "team.chaos" as T;let role=team_role(T.saved_team,"worker");
let call=partial(role);await gather([call(20),apply(role,[21]),T.saved_team(22)])'''
            app.create_project('imported', source=source, files={'team.chaos':module}, capabilities=[])
            self.assertEqual(app.start('imported', 1, None, mode='source').result(), [40,42,44])


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class TeamBrowserIntegration(unittest.TestCase):
    def setUp(self):
        self.navigation_entered, self.navigation_release = threading.Event(), threading.Event()
        entered, release = self.navigation_entered, self.navigation_release
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/slow':
                    entered.set(); release.wait(5)
                body = b'<title>Team browser fixture</title><button data-testid="confirm" onclick="document.querySelector(\'output\').textContent=\'verified actual page\'">Confirm</button><output>pending</output>'
                self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.folder = tempfile.TemporaryDirectory()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.registry = Registry(); self.decisions = []
        def browser(request, context):
            return Final(request.history[-1]['output']) if request.history else ToolCall('browser.control', request.input)
        def decide(request, context):
            observed = request.input; self.decisions.append(observed)
            target = next(row for row in observed['targets'] if row['text'] == 'Confirm')
            return Final({'action':'click', 'ref':target['ref'], **{k:observed[k] for k in ('sessionId','controlEpoch','snapshotId')}})
        self.registry.register_provider(ProviderSpec('app.browser', browser)); self.registry.register_provider(ProviderSpec('app.decide', decide))
        self.team = definition(['browser.control'], 'app.browser')
        self.team['roles'][0]['id'] = 'observe'; self.team['roles'][0]['agent']['name'] = 'observer'
        self.team['roles'][1]['id'] = 'act'; self.team['roles'][1]['parent'] = 'observe'; self.team['entry'] = 'act'
        self.team['roles'].append({'id':'decide','parent':'observe','capabilities':[],
                                  'agent':{'kind':'agent','name':'decider','config':{'provider':'app.decide','tools':[]}}})

    def tearDown(self):
        self.navigation_release.set()
        self.server.shutdown(); self.thread.join(2); self.server.server_close(); self.folder.cleanup()

    def app(self):
        settings = Settings(':memory:'); settings.update({'runtime':{'workspace':self.folder.name}}, version=settings.public()['version'])
        return Framework(Path(self.folder.name)/'store.sqlite', registry=self.registry, settings=settings)

    def result(self, run):
        row = run.wait(timeout=25)
        if row['status'] == 'failed' and "Executable doesn't exist" in row.get('error',''): self.skipTest('Chromium unavailable')
        self.assertEqual(row['status'], 'completed', row.get('error'))
        return row['output']

    def test_observe_decide_act_observe_verify_goal_record_and_replay(self):
        source = '''if input.replay {let saved=await load_mode("browser_skill");(await replay_skill(saved,{}))[-1]}else{
let members=team_bindings(team_from(input.team));
async fn visit(url){
 let before=await members.observe({action:"navigate",url:url});
 let action=await members.decide(before);
 let changed=await members.act(action);
 let after=await members.observe({action:"snapshot"});
 if after.sessionId!=before.sessionId || after.snapshotId<=before.snapshotId {throw "invalid observation sequence"}else{null};
 {verified:contains(after.snapshot,"verified actual page"),snapshot:after.snapshot,sessionId:after.sessionId}
}
fn planner(ctx){plan([{id:"visit",call:"visit"}])}
async fn runVisit(ctx){let captured=await record_skill(visit,ctx.input.url);await save_mode(captured.skill,"browser_skill");captured.output}
fn verify(ctx){{done:ctx.results.visit.verified,state:ctx.state,evidence:ctx.results.visit}}
await pursue(goal("change actual page","planner","verify",1),{planner:planner,visit:runVisit,verify:verify},{url:input.url},key="browser_goal")
}'''
        with self.app() as app:
            app.create_project('browser', source=source, capabilities=['browser.control'])
            first = self.result(app.start('browser', 1, {'team':self.team,'url':self.url,'replay':False}, mode='source'))
            self.assertTrue(first['done']); self.assertTrue(first['evidence']['verified']); self.assertEqual(len(self.decisions),1)
            artifact = app.mode('browser','browser_skill'); actions = artifact['definition']['actions']
            self.assertEqual(len(actions),3); self.assertEqual(actions[1]['input']['locator'],{'kind':'testId','value':'confirm'})
            self.assertNotIn('ref',actions[1]['input'])
            capture = next(row for row in app.modes('browser')['items'] if row['key'].startswith('recording_'))
            self.assertEqual([row['status'] for row in capture['checkpoint']['receipts']],['completed']*3)
            self.assertTrue(all(row['operationId'] for row in capture['checkpoint']['receipts']))
            replay = self.result(app.start('browser',1,{'replay':True},mode='source'))
            self.assertIn('verified actual page',replay['snapshot']); self.assertNotEqual(first['evidence']['sessionId'], replay['sessionId'])
            tool_events = [row for row in events(app,'browser') if row['type']=='tool.completed' and 'team' in row['payload']]
            self.assertEqual([row['payload']['team']['roleId'] for row in tool_events], ['observe','act','observe'])

    def test_cancel_inflight_real_navigation_drains_and_does_not_seal(self):
        source = 'let member=team_role(team_from(input.team),"observe");async fn action(url){await member({action:"navigate",url:url})};await record_skill(action,input.url)'
        with self.app() as app:
            app.create_project('cancel_browser',source=source,capabilities=['browser.control'])
            run=app.start('cancel_browser',1,{'team':self.team,'url':self.url+'/slow'},mode='source')
            try:
                if not self.navigation_entered.wait(4):
                    row=run.wait(5)
                    if "Executable doesn't exist" in row.get('error',''):self.skipTest('Chromium unavailable')
                    self.fail('browser never reached local navigation fixture: '+str(row.get('error')))
                run.cancel()
            finally:self.navigation_release.set()
            self.assertEqual(run.wait(10)['status'],'cancelled')
            capture=app.modes('cancel_browser')['items'][0]
            self.assertEqual(capture['status'],'incomplete')
            self.assertEqual(len(capture['checkpoint']['receipts']),1)
            self.assertNotEqual(capture['checkpoint']['receipts'][0]['status'],'completed')
            self.assertFalse(any(row['type']=='team.role.completed' for row in events(app,'cancel_browser')))

    def test_stale_real_reference_failure_never_seals_recording(self):
        source = '''let m=team_bindings(team_from(input.team));
async fn action(url){let before=await m.observe({action:"navigate",url:url});let decision=await m.decide(before);
await m.act(decision);await m.act(decision)}
await record_skill(action,input.url)'''
        with self.app() as app:
            app.create_project('stale',source=source,capabilities=['browser.control'])
            row=app.start('stale',1,{'team':self.team,'url':self.url},mode='source').wait(25)
            if "Executable doesn't exist" in row.get('error',''):self.skipTest('Chromium unavailable')
            self.assertEqual(row['status'],'failed');self.assertIn('Stale browser reference',row['error'])
            capture=app.modes('stale')['items'][0];self.assertEqual(capture['status'],'incomplete')
            self.assertEqual([r['status'] for r in capture['checkpoint']['receipts']],['completed','completed','failed'])
            self.assertEqual(sum(row['type']=='team.role.failed' for row in events(app,'stale')),1)


if __name__ == '__main__': unittest.main()
