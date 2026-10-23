"""Proposal: Process HTTP/SSE validates selectors before committing stream headers.

Runtime scope, when root executes this file: in-memory Framework/Settings, a port-0
loopback server, isolated temporary control/update directories, deterministic tools.
The proposer has not imported or executed this file or started its fixture server.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from chaos import Client, Framework, Registry, Settings, ToolSpec
from chaos.language import Evaluator
from chaos.server import make_server
from tests.test_process_integration import definition, wait_for


class ProcessStreamHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.secret = 'http-process-stream-fixture-secret'
        settings = Settings(':memory:')
        settings.update({'providers':{'text':{'apiKey':self.secret}}},version=1)
        registry = Registry()
        holder = {}
        self.effects = []
        def public(value,context):
            fixture = holder['app']
            self.effects.append((context.project_id,context.run_id,value))
            fixture._store.event(context.project_id, 'bot.progress',
                fixture._engine.settings.redact({'text':'visible '+self.secret,
                    'apiKey':'fixture-key-value','authorization':'fixture-header-value'}),
                context.run_id)
            fixture._store.event(context.project_id, 'process.bot.adopt',
                fixture._engine.settings.redact({'key':'alpha','messageId':'forged',
                    'guidanceRevision':99,'processOrigin':'lifecycle','forged':True}),
                context.run_id)
            return value
        registry.register_tool(ToolSpec('fixture.public',public))
        self.app = Framework(settings=settings,registry=registry)
        holder['app'] = self.app
        self.addCleanup(self.app.close)
        self.app._engine.pause_dispatch(True)
        source = ('fn step(c){tool("fixture.public",c.turn);'
                  'process_step(c.state+1,c.input)};export {step}')
        for project in ('p','q'):
            self.app.create_project(project,source='42',files={'dot.chaos':source},
                capabilities=['process.manage','fixture.public'])
        self.app.create_process('p',1,'alpha',definition(maxTurns=2),state=0)
        self.server = make_server(port=0,framework=self.app,
            state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = 'http://127.0.0.1:'+str(self.server.server_port)
        self.client = Client(self.origin,timeout=3)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive(),'temporary HTTP server did not join')

    def snapshot(self):
        with self.app._store.lock:
            return {name:[tuple(row) for row in self.app._store.db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                for name in ('processes','process_messages','runs','events',
                             'process_run_lineage','process_event_lineage')}

    def request(self,endpoint,query,headers=None):
        encoded = urllib.parse.urlencode(query)
        return urllib.request.Request(self.origin+'/api/v1/'+endpoint+'?'+encoded,
            headers={'Accept':'text/event-stream',**(headers or {})})

    def assert_json_error(self,endpoint,query,status,headers=None):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            with urllib.request.urlopen(self.request(endpoint,query,headers),timeout=3) as response:
                self.fail('invalid request committed HTTP '+str(response.status)+' '+str(response.headers))
        error = raised.exception
        try:
            self.assertEqual(error.code,status)
            self.assertEqual(error.headers.get_content_type(),'application/json')
            raw = error.read()
            self.assertFalse(raw.startswith((b'id:',b'data:',b': heartbeat')))
            value = json.loads(raw)
            self.assertEqual(value['error']['code'],'not_found' if status==404 else 'validation_error')
            self.assertIsInstance(value['error']['message'],str)
        finally:
            error.close()

    def complete_two_turns(self):
        self.client.send_process('p','alpha','second',message_id='second')
        self.app._engine.pause_dispatch(False)
        row = wait_for(self.app,'p','alpha',lambda item:item['status']=='completed')
        self.assertEqual((row['turn'],row['state']),(2,2))
        self.assertIsNone(row['pending'])
        self.assertEqual(len(self.effects),2)
        return self.client.events('p',process_key='alpha',limit=300)

    def sse(self,*,after=0,last_event_id=None):
        headers = {} if last_event_id is None else {'Last-Event-ID':str(last_event_id)}
        with urllib.request.urlopen(self.request('events/stream',
                [('projectId','p'),('processKey','alpha'),('after',after)],headers),timeout=3) as response:
            self.assertEqual(response.status,200)
            self.assertEqual(response.headers.get_content_type(),'text/event-stream')
            # A genuinely completed Process must close after its authoritative tail.
            # A run-terminal-only close loses later turns and fails the ID comparison.
            raw = response.read().decode('utf-8')
        result = []
        for frame in raw.split('\n\n'):
            fields = {}
            for line in frame.splitlines():
                if line and not line.startswith(':'):
                    name,value = line.split(':',1)
                    fields[name] = value.lstrip()
            if 'data' in fields:
                value = json.loads(fields['data'])
                self.assertEqual(int(fields['id']),value['id'])
                result.append(value)
        return result

    def test_conflicting_missing_cross_project_and_malformed_selectors_are_json_before_sse_headers(self):
        self.app._store.processes.claim('p','alpha','fixture-run')
        before = self.snapshot()
        cases = [([('projectId','p'),('runId','fixture-run'),('processKey','alpha')],400),
                 ([('projectId','p'),('runId',''),('processKey','')],400),
                 ([('projectId','p'),('processKey','missing')],404),
                 ([('projectId','q'),('processKey','alpha')],404),
                 ([('projectId','p'),('processKey','bad key')],400),
                 ([('projectId','p'),('processKey','')],400)]
        for endpoint in ('events','events/stream'):
            for query,status in cases:
                with self.subTest(endpoint=endpoint,query=query):
                    self.assert_json_error(endpoint,query,status)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.effects,[])

    def test_duplicate_queries_invalid_cursors_and_last_event_id_are_json_before_sse_headers(self):
        before = self.snapshot()
        valid = [('projectId','p'),('processKey','alpha')]
        cases = [([('processKey','alpha')],None),
                 (valid+[('projectId','q')],None),
                 (valid+[('processKey','alpha')],None),
                 (valid+[('after','0'),('after','1')],None)]
        for value in ('-1','not-an-integer','1.5','true'):
            cases.append((valid+[('after',value)],None))
            cases.append((valid+[('after','0')],{'Last-Event-ID':value}))
        for endpoint in ('events','events/stream'):
            for query,headers in cases:
                with self.subTest(endpoint=endpoint,query=query,headers=headers):
                    self.assert_json_error(endpoint,query,400,headers)
        for limit in ('0','301','not-an-integer'):
            self.assert_json_error('events',valid+[('limit',limit)],400)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.effects,[])

    def test_original_envelope_openapi_and_http_native_origin_redaction_parity(self):
        selected = self.complete_two_turns()
        original = self.client.events('p',limit=300)
        self.assertEqual(set(original),{'items','nextCursor','lastEventId'})
        self.assertFalse(any('processOrigin' in row for row in original['items']))
        run_id = self.app.get_process('p','alpha')['lastRunId']
        run_page = self.client.events('p',run_id=run_id,limit=300)
        self.assertEqual(set(run_page),{'items','nextCursor','lastEventId'})
        self.assertFalse(any('processOrigin' in row for row in run_page['items']))
        self.assertEqual(set(selected),{'items','nextCursor','lastEventId','historyStartEventId','legacyHistoryIncluded'})
        self.assertFalse(selected['legacyHistoryIncluded'])
        self.assertEqual(selected['historyStartEventId'],selected['items'][0]['id'])
        native = Evaluator(process_handler=lambda operation,payload:
            self.app._engine.process_operation('p',operation,payload,allowed=set())).run(
                'await process_events("alpha",limit=300)')
        expected = {row['id']:row['processOrigin'] for row in selected['items']}
        self.assertEqual({row['id']:row['processOrigin'] for row in native['items']},expected)
        self.assertEqual((native['historyStartEventId'],native['legacyHistoryIncluded']),
                         (selected['historyStartEventId'],False))
        for result in (selected,native):
            self.assertNotIn(self.secret,json.dumps(result))
            progress = [row for row in result['items'] if row['type']=='bot.progress']
            self.assertEqual(len(progress),2)
            self.assertTrue(all(row['payload']=={'text':'visible [redacted]',
                'apiKey':'[redacted]','authorization':'[redacted]'} for row in progress))
            forged = [row for row in result['items'] if row['payload'].get('forged')]
            self.assertEqual(len(forged),2)
            self.assertTrue(all(row['processOrigin']=='run' and row['payload']['processOrigin']=='lifecycle' for row in forged))
            self.assertTrue(all(row['processOrigin']=='lifecycle' for row in result['items'] if row['type']=='process.turn.completed'))
        schema = self.client._request('GET','/openapi.json')
        for path in ('/events','/events/stream'):
            operation = schema['paths'][path]['get']
            parameters = {item['name']:item for item in operation['parameters']}
            self.assertEqual(parameters['processKey']['in'],'query')
            self.assertEqual(parameters['processKey']['schema']['type'],'string')
            self.assertFalse(parameters['processKey'].get('required',False))
            self.assertTrue(parameters['projectId']['required'])
            self.assertIn('mutually exclusive',operation['description'])
            self.assertIn('processOrigin',operation['description'])
        stream = schema['paths']['/events/stream']['get']
        header = next(item for item in stream['parameters'] if item['name']=='Last-Event-ID')
        self.assertEqual((header['in'],header['schema']['type'],header['schema']['minimum']),('header','integer',0))
        self.assertIn('text/event-stream',stream['responses']['200']['content'])

    def test_actual_completed_sse_all_turns_last_event_id_replay_and_committed_terminal_tail(self):
        selected = self.complete_two_turns()
        before = self.snapshot()
        expected = selected['items']
        full = self.sse()
        self.assertEqual(full,expected)
        self.assertEqual(len({row['run_id'] for row in full if row['type']=='run.completed'}),2)
        cursor = expected[len(expected)//2]['id']
        resumed = self.sse(after=0,last_event_id=cursor)
        self.assertEqual(resumed,[row for row in expected if row['id']>cursor])
        self.assertEqual(len(resumed),len({row['id'] for row in resumed}))
        lifecycle_cursor = max(row['id'] for row in expected if row['type']=='process.turn.completed')
        terminal_tail = self.sse(last_event_id=lifecycle_cursor)
        self.assertEqual(terminal_tail,[row for row in expected if row['id']>lifecycle_cursor])
        self.assertTrue(terminal_tail)
        self.assertEqual(terminal_tail[-1]['type'],'run.completed')
        self.assertEqual(self.sse(last_event_id=expected[-1]['id']),[])
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(len(self.effects),2)


if __name__ == '__main__':
    unittest.main()
