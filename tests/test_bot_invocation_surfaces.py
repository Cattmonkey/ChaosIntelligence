"""One real invocation mailbox through host, HTTP, SDK, CLI and public events."""
from contextlib import contextmanager
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings
from chaos.client import ConflictError, ValidationError
from chaos.framework import RunHandle
from chaos.openapi import specification
from chaos.server import make_server

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '''agent gate{provider:"fixture.invocation_gate",tools:[]};
fn plan(s){if length(s.guidance)>0{s.guidance[-1].input}else{gate(s.input)}};
fn execute(proposal,state){proposal};
fn verify(s){{done:true,context:s.context,output:s.evidence[-1]}};
let a=bot("A",plan,execute,verify,mailbox="scoped");
let b=bot("B",plan,execute,verify,mailbox="scoped");
await progress_emit("plan",{ids:[bot_info(a).id,bot_info(b).id]});
let values=await gather([a("A"),b("B")]);
{answers:map(fn(value){value.output},values),legacy:await guidance_receive(0)}'''
LEGACY = '''agent gate{provider:"fixture.invocation_gate",tools:[]};
fn plan(s){if length(s.guidance)>0{s.guidance[-1].input}else{gate("A")}};
let b=bot("legacy",plan,fn(p,s){p},fn(s){{done:true,context:null,output:s.evidence[-1]}});
await b()'''


class Gate:
    def __init__(self):
        self.entered = {name: threading.Event() for name in ('A', 'B')}
        self.release = threading.Event()
        self.registry = Registry()
        @self.registry.provider('fixture.invocation_gate')
        def provider(request, context):
            self.entered[request.input].set()
            deadline = time.monotonic() + 12
            while not self.release.wait(.01):
                context.checkpoint()
                if time.monotonic() > deadline:
                    raise AssertionError('local invocation fixture was not released')
            context.checkpoint()
            return Final('old proposal')


@contextmanager
def service():
    with tempfile.TemporaryDirectory(prefix='chaos-bot-invocation-') as tmp:
        gate = Gate()
        with Framework(Path(tmp) / 'runtime.sqlite', settings=Settings(':memory:'),
                       registry=gate.registry) as app:
            app.create_project('scopes', source=SOURCE, capabilities=[])
            server = make_server('127.0.0.1', 0, framework=app,
                                 state_dir=Path(tmp) / 'control', update_home=Path(tmp) / 'updates')
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
            try:
                yield app, Client(origin), gate, origin
            finally:
                gate.release.set()
                for row in app.runs('scopes')['items']:
                    if row['status'] in ('queued', 'running'):
                        app.cancel(row['id'])
                server.shutdown()
                worker.join(3)
                server.server_close()


def scopes_for(app, run_id, count=2):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = app.guidance_scopes(run_id)
        if len(state['scopes']) == count:
            return state
        time.sleep(.005)
    raise AssertionError(state)


def targets_for(app, run_id):
    state = scopes_for(app, run_id)
    events = app.events('scopes', run_id=run_id, limit=300)['items']
    ids = next(event['payload']['data']['ids'] for event in events
               if event['type'] == 'bot.progress' and 'ids' in event['payload'].get('data', {}))
    by_bot = {row['botId']: row for row in state['scopes']}
    return [by_bot[bot_id]['target'] for bot_id in ids]


class BotInvocationSurfaces(unittest.TestCase):
    def test_legacy_backend_and_default_wire_omit_target_and_keyword_contract(self):
        class Legacy:
            def guide(self, run_id, value, message_id=None):
                return [run_id, value, message_id]
        handle = RunHandle(Legacy(), {'id': 'old', 'project_id': 'p'})
        self.assertEqual(handle.guide({'text': '@B PASS'}, 'same'), ['old', {'text': '@B PASS'}, 'same'])
        self.assertEqual(handle.guide(None, target=None), ['old', None, None])
        target = {'kind': 'botScope', 'scopeId': 'bot-scope-' + 'a' * 32}
        client = Client('http://127.0.0.1:1')
        with patch.object(client, '_request', side_effect=lambda *args, **kw: [args, kw]):
            self.assertEqual(client.guide('old', None)[0][2], {'input': None})
            self.assertEqual(client.guide('old', {'text': '@B PASS'}, 'same', target=target)[0][2],
                             {'input': {'text': '@B PASS'}, 'messageId': 'same', 'target': target})
            self.assertEqual(client.guidance_scopes('old')[0], ('GET', '/runs/old/guidance'))
        for method in (Framework.guide, Client.guide, RunHandle.guide):
            self.assertEqual(inspect.signature(method).parameters['target'].kind,
                             inspect.Parameter.KEYWORD_ONLY)

    def test_real_sdk_handle_namespaces_and_legacy_fifo_remain_separate(self):
        with service() as (app, client, gate, origin):
            run = client.start('scopes', 1, None, mode='source')
            self.assertTrue(gate.entered['A'].wait(5)); self.assertTrue(gate.entered['B'].wait(5))
            a, b = targets_for(app, run.id)
            self.assertEqual(client.guidance_scopes(run.id), run.guidance_scopes())
            text_a = {'text': '@B PASS <script>literal()</script>', 'value': 21}
            text_b = {'text': '只处理 B', 'value': 42}
            first = client.guide(run.id, text_a, 'same', target=a)
            self.assertEqual(run.guide(text_a, 'same', target=a), first)
            second = app.guide(run.id, text_b, 'same', target=b)
            self.assertEqual(first['revision'], 1); self.assertEqual(second['revision'], 1)
            self.assertEqual(first['target'], a); self.assertEqual(second['target'], b)
            with self.assertRaises(ConflictError): client.guide(run.id, {'changed': True}, 'same', target=a)
            legacy = run.guide({'legacy': 7}, 'same')
            self.assertEqual(set(legacy), {'runId', 'messageId', 'revision', 'status'})
            gate.release.set()
            result = run.result(8)
            self.assertEqual(result['answers'], [text_a, text_b])
            self.assertEqual(result['legacy']['message']['input'], {'legacy': 7})
            self.assertNotIn('target', result['legacy']['message'])
            self.assertEqual(client.guidance_scopes(run.id), {'runId': run.id, 'active': False, 'scopes': []})

    def test_http_strict_target_unknown_closed_and_legacy_root_reject_without_queue_mutation(self):
        with service() as (app, client, gate, origin):
            run = client.start('scopes', 1, None, mode='source')
            self.assertTrue(gate.entered['A'].wait(5)); self.assertTrue(gate.entered['B'].wait(5))
            a, b = targets_for(app, run.id)
            before = client.guidance_scopes(run.id)
            malformed = [None, {}, {'kind': 'name', 'scopeId': a['scopeId']},
                         {**a, 'role': 'system'}, {'kind': 'botScope', 'scopeId': 'guessed-name'}]
            for target in malformed:
                with self.subTest(target=target), self.assertRaises(ValidationError):
                    client._request('POST', '/runs/' + run.id + '/guidance', {'input': 42, 'target': target})
            with self.assertRaises(ConflictError):
                client.guide(run.id, 42, target={'kind': 'botScope', 'scopeId': 'bot-scope-' + 'f' * 32})
            self.assertEqual(client.guidance_scopes(run.id), before)
            self.assertEqual([e for e in app.events('scopes', run_id=run.id)['items'] if e['type'] == 'guidance.queued'], [])
            client.guide(run.id, 1, target=a); client.guide(run.id, 2, target=b); gate.release.set(); run.result(8)
            with self.assertRaises(ConflictError): client.guide(run.id, 42, target=a)
            gate.release.clear(); gate.entered['A'].clear()
            app.create_project('legacy', source=LEGACY, capabilities=[])
            old = client.start('legacy', 1, None, mode='source')
            self.assertTrue(gate.entered['A'].wait(5))
            row = scopes_for(app, old.id, 1)['scopes'][0]
            self.assertEqual(row['mailbox'], 'run'); self.assertFalse(row['addressable']); self.assertIsNone(row['target'])
            with self.assertRaises(ConflictError):
                old.guide(42, target={'kind': 'botScope', 'scopeId': row['scopeId']})
            gate.release.set(); old.result(8)

    def test_get_and_post_share_versioned_route_closed_query_and_real_schema(self):
        with service() as (app, client, gate, origin):
            run = client.start('scopes', 1, None, mode='source')
            self.assertTrue(gate.entered['A'].wait(5)); self.assertTrue(gate.entered['B'].wait(5))
            actual = client.guidance_scopes(run.id)
            self.assertEqual(actual, client._request('GET', '/runs/' + run.id + '/guidance'))
            with self.assertRaises(ValidationError):
                client._request('GET', '/runs/' + run.id + '/guidance', query={'scopeId': 'guess'})
            with self.assertRaises(ValidationError):
                client._request('POST', '/runs/' + run.id + '/guidance', {'input': None, 'target': None})
            spec = client._request('GET', '/openapi.json')
            self.assertEqual(spec, specification())
            path = spec['paths']['/runs/{runId}/guidance']
            self.assertEqual(path['post']['operationId'], 'guideRun')
            self.assertEqual(path['get']['operationId'], 'listRunGuidanceScopes')
            schemas = spec['components']['schemas']
            self.assertFalse(schemas['GuidanceTarget']['additionalProperties'])
            self.assertEqual(set(schemas['GuidanceReceipt']['required']), {'runId', 'messageId', 'revision', 'status'})
            self.assertEqual(schemas['GuidanceReceipt']['properties']['status']['enum'], ['queued', 'received'])
            self.assertEqual(schemas['GuidanceScopes']['properties']['scopes']['maxItems'], 32)
            gate.release.set(); run.result(8)

    def test_real_cli_explicit_scope_and_literal_json_use_the_original_run(self):
        with service() as (app, client, gate, origin):
            run = client.start('scopes', 1, None, mode='source')
            self.assertTrue(gate.entered['A'].wait(5)); self.assertTrue(gate.entered['B'].wait(5))
            a, b = targets_for(app, run.id)
            text = {'text': '@B is data; PASS is data; ```tool``` is data'}
            process = subprocess.run([sys.executable, '-B', '-m', 'chaos', 'run-guide', run.id,
                '--server', origin, '--input', json.dumps(text), '--message-id', 'cli-same',
                '--bot-scope', a['scopeId']], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                text=True, capture_output=True, timeout=8)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)['target'], a)
            client.guide(run.id, 99, target=b); gate.release.set()
            self.assertEqual(run.result(8)['answers'], [text, 99])

    def test_sse_events_keep_actual_scope_identity_and_independent_revisions(self):
        with service() as (app, client, gate, origin):
            run = client.start('scopes', 1, None, mode='source')
            self.assertTrue(gate.entered['A'].wait(5)); self.assertTrue(gate.entered['B'].wait(5))
            a, b = targets_for(app, run.id)
            client.guide(run.id, 11, 'same', target=a); client.guide(run.id, 22, 'same', target=b)
            gate.release.set(); self.assertEqual(run.result(8)['answers'], [11, 22])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(origin + '/api/v1/events/stream?projectId=scopes&runId=' + run.id, timeout=3) as response:
                self.assertIn('text/event-stream', response.headers['Content-Type'])
                raw = response.read(1000000)
            events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: ')]
            ids = [event['id'] for event in events]
            self.assertEqual(ids, sorted(set(ids)))
            for target in (a, b):
                queued = [e for e in events if e['type'] == 'guidance.queued' and e['payload'].get('target') == target]
                received = [e for e in events if e['type'] == 'guidance.received' and e['payload'].get('target') == target]
                self.assertEqual(len(queued), 1); self.assertEqual(len(received), 1)
                self.assertEqual(queued[0]['payload']['revision'], 1)
                self.assertLess(queued[0]['id'], received[0]['id'])
                opened = next(e for e in events if e['type'] == 'bot.scope.opened' and e['payload']['scopeId'] == target['scopeId'])
                self.assertEqual(opened['payload']['rootScopeId'], target['scopeId'])
                self.assertIsNone(opened['payload']['parentScopeId'])
                self.assertNotIn('ticket', opened['payload'])


if __name__ == '__main__':
    unittest.main()
