import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from chaos.mcp_http import MCPHttpClient, MCPToolError, MCPTransportError, RemoteOutcomeUnknown, decode_response, endpoint_url


class Fixture:
    def __init__(self, mode='json', delay=0, wrong_id=False, tool_error=False, slow_drip=False):
        self.mode, self.delay, self.wrong_id, self.tool_error = mode, delay, wrong_id, tool_error
        self.slow_drip = slow_drip
        self.calls = []
        self.dispatched = threading.Event()
        self.release = threading.Event()
        self.hold = False
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.calls.append({'request': request, 'authorization': self.headers.get('Authorization')})
                if request['method'] == 'notifications/initialized':
                    self.send_response(202)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if request['method'] == 'initialize':
                    result = {'protocolVersion': '2025-11-25', 'serverInfo': {'name': 'mobile-mcp', 'version': 'fixture'}}
                else:
                    fixture.dispatched.set()
                    if fixture.hold:
                        fixture.release.wait(3)
                    if fixture.delay:
                        time.sleep(fixture.delay)
                    result = {'content': [{'type': 'text', 'text': 'done'}]}
                    if fixture.tool_error:
                        result['isError'] = True
                reply = {'jsonrpc': '2.0', 'id': 'foreign' if fixture.wrong_id and request['method'] != 'initialize' else request['id'], 'result': result}
                raw = json.dumps(reply).encode()
                kind = 'application/json'
                if fixture.mode == 'sse':
                    kind = 'text/event-stream'
                    raw = b': keepalive\n\nevent: message\ndata: ' + raw + b'\n\n'
                if fixture.slow_drip and request['method'] == 'tools/call':
                    raw = b': slow progress\n\n' * 40 + raw
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    if fixture.slow_drip and request['method'] == 'tools/call':
                        for offset in range(len(raw)):
                            self.wfile.write(raw[offset:offset + 1])
                            self.wfile.flush()
                            time.sleep(0.02)
                    else:
                        self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = 'http://127.0.0.1:%d/mcp' % self.server.server_port
    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self
    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class MCPHTTPTests(unittest.TestCase):
    def test_json_response_is_matched_and_not_prose_parsed(self):
        reply = decode_response(b'{"jsonrpc":"2.0","id":"a","result":{"content":[]}}', 'application/json; charset=utf-8', 'a')
        self.assertEqual(reply['result'], {'content': []})
        for raw in (b'{"jsonrpc":"2.0","id":"b","result":{}}',
                    b'{"jsonrpc":"2.0","id":"a","id":"a","result":{}}',
                    b'{"jsonrpc":"2.0","id":"a","result":{},"error":{}}'):
            with self.assertRaises(MCPTransportError):
                decode_response(raw, 'application/json', 'a')

    def test_sse_complete_events_and_notification(self):
        raw = (b': comment\r\n\r\ndata: {"jsonrpc":"2.0","method":"notifications/message","params":{}}\r\n\r\n'
               b'event: message\r\ndata: {"jsonrpc":"2.0","id":"a",\r\ndata: "result":{"content":[]}}\r\n\r\n')
        self.assertEqual(decode_response(raw, 'text/event-stream', 'a')['result'], {'content': []})
        with self.assertRaises(MCPTransportError):
            decode_response(raw + b'data: {"jsonrpc":"2.0","id":"a","result":{}}\n\n', 'text/event-stream', 'a')

    def test_http_json_and_sse_work_with_real_fixture(self):
        for mode in ('json', 'sse'):
            with self.subTest(mode=mode), Fixture(mode) as fixture:
                client = MCPHttpClient(fixture.url, token='fixture-secret')
                result = client.call_tool('mobile_press_button', {'device': 'fixed', 'button': 'HOME'}, lambda: None, mutation=True)
                self.assertEqual(result['content'][0]['text'], 'done')
                self.assertEqual([row['request']['method'] for row in fixture.calls], ['initialize', 'notifications/initialized', 'tools/call'])
                self.assertTrue(all(row['authorization'] == 'Bearer fixture-secret' for row in fixture.calls))
                self.assertEqual(fixture.calls[-1]['request']['params']['arguments']['device'], 'fixed')
                client.close()

    def test_tool_error_is_terminal_but_not_success(self):
        with Fixture(tool_error=True) as fixture:
            client = MCPHttpClient(fixture.url)
            with self.assertRaises(MCPToolError):
                client.call_tool('mobile_press_button', {'device': 'fixed', 'button': 'HOME'}, lambda: None, mutation=True)
            client.close()
            self.assertEqual(len(fixture.calls), 3)

    def test_foreign_reply_quarantines_without_retry(self):
        with Fixture(wrong_id=True) as fixture:
            client = MCPHttpClient(fixture.url)
            with self.assertRaises(MCPTransportError):
                client.call_tool('mobile_press_button', {'device': 'fixed', 'button': 'HOME'}, lambda: None, mutation=True)
            with self.assertRaises(RemoteOutcomeUnknown):
                client.close()
            self.assertEqual(len(fixture.calls), 3)

    def test_cancel_before_dispatch_has_no_network_effect(self):
        class Canceled(BaseException):
            pass
        with Fixture() as fixture:
            client = MCPHttpClient(fixture.url)
            def checkpoint():
                raise Canceled()
            with self.assertRaises(Canceled):
                client.call_tool('mobile_press_button', {}, checkpoint, mutation=True)
            self.assertEqual(fixture.calls, [])
            client.close()

    def test_cancel_after_dispatch_retains_worker_until_actual_reply(self):
        class Canceled(BaseException):
            pass
        with Fixture() as fixture:
            fixture.hold = True
            client = MCPHttpClient(fixture.url, timeout=2)
            def checkpoint():
                if fixture.dispatched.is_set():
                    raise Canceled()
            with self.assertRaises(Canceled):
                client.call_tool('mobile_press_button', {}, checkpoint, mutation=True)
            with self.assertRaises(RemoteOutcomeUnknown):
                client.close()
            self.assertEqual(len(fixture.calls), 3)
            fixture.release.set()
            self.assertTrue(client._active['done'].wait(2))
            client.close()
            self.assertEqual(len(fixture.calls), 3)

    def test_timeout_does_not_claim_remote_stop_after_worker_drains(self):
        with Fixture(delay=0.35) as fixture:
            client = MCPHttpClient(fixture.url, timeout=0.1)
            with self.assertRaises(MCPTransportError):
                client.call_tool('mobile_press_button', {}, lambda: None, mutation=True)
            time.sleep(0.2)
            with self.assertRaises(RemoteOutcomeUnknown):
                client.close()
            self.assertEqual(len(fixture.calls), 3)

    def test_bounded_response_fails_without_retry(self):
        with Fixture() as fixture:
            client = MCPHttpClient(fixture.url)
            client.initialize(lambda: None)
            client.MAX_RESPONSE = 8
            with self.assertRaises(MCPTransportError):
                client.call_tool('mobile_press_button', {}, lambda: None, mutation=True)
            with self.assertRaises(RemoteOutcomeUnknown):
                client.close()
            self.assertEqual(len(fixture.calls), 3)

    def test_endpoint_validation_rejects_credential_and_remote_plain_http(self):
        for value in ('http://example.org/mcp', 'https://user:pass@example.org/mcp',
                      'https://example.org/mcp?token=secret', 'https://example.org/mcp#token',
                      'http://127.0.0.1/mcp\r\nx:1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                endpoint_url(value)
        self.assertEqual(endpoint_url('http://127.0.0.1:3000/mcp'), 'http://127.0.0.1:3000/mcp')

    def test_slow_sse_drip_does_not_keep_worker_alive_past_global_deadline(self):
        with Fixture(mode='sse', slow_drip=True) as fixture:
            client = MCPHttpClient(fixture.url, timeout=0.15)
            observed_slots = []
            def checkpoint():
                if fixture.dispatched.is_set() and client._active is not None and not observed_slots:
                    observed_slots.append(client._active)
            with self.assertRaises(MCPTransportError):
                client.call_tool('mobile_press_button', {}, checkpoint, mutation=True)
            self.assertTrue(observed_slots, 'Must observe the dispatched tools/call worker')
            self.assertTrue(observed_slots[0]['done'].wait(0.4),
                            'Small chunks must not indefinitely reset the worker read deadline')
            self.assertFalse(observed_slots[0]['terminal'])
            with self.assertRaises(RemoteOutcomeUnknown):
                client.close()
            self.assertEqual([row['request']['method'] for row in fixture.calls],
                             ['initialize', 'notifications/initialized', 'tools/call'])
