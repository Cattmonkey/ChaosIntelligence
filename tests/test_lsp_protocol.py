"""Framing and lifecycle tests use a live, concurrent binary transport."""
import io
import json
import queue
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from chaos.lsp import MAX_PENDING_REQUESTS, Server
from chaos.lsp_transport import EOF, FrameError, PayloadError, Writer, read_message


def frame(value):
    body = json.dumps(value, ensure_ascii=False).encode('utf-8')
    return ('Content-Length: %d\r\n\r\n' % len(body)).encode() + body


class Connection:
    def __init__(self, root, initialize=True):
        self.client, self.endpoint = socket.socketpair()
        self.input = self.endpoint.makefile('rb')
        self.output = self.endpoint.makefile('wb')
        self.reader_stream = self.client.makefile('rb')
        self.messages = queue.Queue()
        self.server = Server(self.input, self.output)
        self.status = None
        self.sequence = 0
        self.deferred = []
        self.thread = threading.Thread(target=self.run)
        self.reader = threading.Thread(target=self.read)
        self.thread.start()
        self.reader.start()
        if initialize:
            self.request('initialize', {'rootUri': root.as_uri(), 'capabilities': {}})
            self.send('initialized', {})

    def run(self):
        self.status = self.server.run()
        self.endpoint.shutdown(socket.SHUT_WR)

    def read(self):
        try:
            while True:
                message = read_message(self.reader_stream)
                if message is EOF:
                    break
                self.messages.put(message)
        except (OSError, ValueError):
            pass

    def send(self, method, params=None, request_id=None):
        message = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        if request_id is not None:
            message['id'] = request_id
        self.client.sendall(frame(message))

    def take(self, predicate):
        for index, message in enumerate(self.deferred):
            if predicate(message):
                return self.deferred.pop(index)
        while True:
            message = self.messages.get(timeout=5)
            if predicate(message):
                return message
            self.deferred.append(message)

    def request(self, method, params=None):
        self.sequence += 1
        self.send(method, params, self.sequence)
        return self.take(lambda m: m.get('id') == self.sequence)

    def close(self):
        if self.thread.is_alive():
            try:
                self.request('shutdown')
                self.send('exit')
            except (OSError, queue.Empty):
                self.client.shutdown(socket.SHUT_WR)
        self.thread.join(5)
        self.reader.join(5)
        for stream in (self.input, self.output, self.reader_stream):
            stream.close()
        self.client.close()
        self.endpoint.close()
        if self.thread.is_alive() or self.reader.is_alive():
            raise AssertionError('LSP threads did not terminate')


class Framing(unittest.TestCase):
    def test_utf8_byte_lengths_back_to_back_and_partial_reads(self):
        class Partial(io.BytesIO):
            def read(self, count=-1):
                return super().read(min(3, count))
        value = {'jsonrpc': '2.0', 'id': 1, 'result': '你好 🧠'}
        stream = Partial(frame(value) + frame({'a': 2}))
        self.assertEqual(read_message(stream), value)
        self.assertEqual(read_message(stream), {'a': 2})
        self.assertIs(read_message(stream), EOF)
        output = io.BytesIO()
        Writer(output).send(value)
        self.assertEqual(read_message(io.BytesIO(output.getvalue())), value)

    def test_malformed_bounded_framing_and_unsupported_encoding(self):
        cases = [b'Content-Length: 2\n\n{}', b'Other: 2\r\n\r\n{}',
                 b'Content-Length: -2\r\n\r\n', b'Content-Length: 999999999\r\n\r\n',
                 b'Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
                 b'Content-Length: 2\r\nContent-Type: application/vscode-jsonrpc; charset=utf-16\r\n\r\n{}',
                 b'Content-Length: 3\r\n\r\n{}', b'x' * 9000]
        for data in cases:
            with self.subTest(data=data[:60]), self.assertRaises(FrameError):
                read_message(io.BytesIO(data))

    def test_invalid_json_is_recoverable_and_null_is_not_eof(self):
        for body in (b'{', b'\xff', b'{"a":1,"a":2}', b'NaN'):
            data = ('Content-Length: %d\r\n\r\n' % len(body)).encode() + body + frame({})
            stream = io.BytesIO(data)
            with self.assertRaises(PayloadError):
                read_message(stream)
            self.assertEqual(read_message(stream), {})
        self.assertIsNone(read_message(io.BytesIO(frame(None))))

    def test_writer_serializes_frames_from_concurrent_threads(self):
        output = io.BytesIO()
        writer = Writer(output)
        threads = [threading.Thread(target=lambda n=n: [writer.send({'id': n, 'result': i}) for i in range(50)]) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        stream = io.BytesIO(output.getvalue())
        values = []
        while True:
            value = read_message(stream)
            if value is EOF:
                break
            values.append(value)
        self.assertEqual(len(values), 200)

    def test_large_response_is_replaced_with_a_bounded_error(self):
        output = io.BytesIO()
        Writer(output).send({'jsonrpc': '2.0', 'id': 42, 'result': 'x' * (5 * 1024 * 1024)})
        response = read_message(io.BytesIO(output.getvalue()))
        self.assertEqual(response['id'], 42)
        self.assertEqual(response['error']['code'], -32803)


class Protocol(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.uri = (self.root / 'main.chaos').as_uri()

    def connect(self, initialize=True):
        connection = Connection(self.root, initialize)
        self.addCleanup(connection.close)
        return connection

    def open(self, connection, text='let answer=42;answer'):
        connection.send('textDocument/didOpen', {'textDocument': {'uri': self.uri,
                        'languageId': 'chaos', 'version': 1, 'text': text}})

    def test_lifecycle_errors_and_notifications_never_get_responses(self):
        connection = self.connect(False)
        self.assertEqual(connection.request('textDocument/hover', {})['error']['code'], -32002)
        connection.send('unknownNotification', {})
        response = connection.request('initialize', {'rootUri': self.root.as_uri()})
        self.assertEqual(response['result']['capabilities']['positionEncoding'], 'utf-16')
        self.assertEqual(connection.request('initialize', {})['error']['code'], -32600)
        connection.client.sendall(frame(None))
        self.assertEqual(connection.take(lambda m: m.get('id', 'absent') is None)['error']['code'], -32600)
        connection.send('textDocument/didChange', {'textDocument': {'uri': self.uri, 'version': 2}, 'contentChanges': []})
        self.assertEqual(connection.request('unknown')['error']['code'], -32601)
        self.assertEqual(connection.request('shutdown')['result'], None)
        self.assertEqual(connection.request('unknown')['error']['code'], -32600)
        connection.send('exit')
        connection.thread.join(3)
        self.assertEqual(connection.status, 0)
        self.assertTrue(all('id' not in value for value in connection.deferred))

    def test_exit_without_shutdown_and_eof_terminate_cleanly(self):
        for action in ('exit', 'eof'):
            connection = self.connect(False)
            if action == 'exit':
                connection.send('exit')
            else:
                connection.client.shutdown(socket.SHUT_WR)
            connection.thread.join(3)
            self.assertFalse(connection.thread.is_alive())
            self.assertEqual(connection.status, 1)

    def test_null_lifecycle_params_and_original_client_diagnostic_uri(self):
        connection = self.connect()
        original_uri = self.uri.replace('main.chaos', '%6dain.chaos')
        connection.send('textDocument/didOpen', {'textDocument': {'uri': original_uri,
                        'languageId': 'chaos', 'version': 1, 'text': 'unknown'}})
        notification = connection.take(lambda m: m.get('method') == 'textDocument/publishDiagnostics')
        self.assertEqual(notification['params']['uri'], original_uri)
        response = connection.request('$/cancelRequest', {'id': 'missing'})
        self.assertEqual(response['error']['code'], -32601)
        connection.client.sendall(frame({'jsonrpc': '2.0', 'id': 'bye', 'method': 'shutdown', 'params': None}))
        self.assertIsNone(connection.take(lambda m: m.get('id') == 'bye')['result'])
        connection.client.sendall(frame({'jsonrpc': '2.0', 'method': 'exit', 'params': None}))
        connection.thread.join(3)
        self.assertEqual(connection.status, 0)

    def test_cancel_is_prompt_and_exactly_once_while_worker_is_busy(self):
        connection = self.connect()
        self.open(connection)
        original = connection.server._calculate
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(job):
            if job.method == 'textDocument/hover':
                started.set()
                release.wait(5)
            return original(job)
        connection.server._calculate = blocked
        params = {'textDocument': {'uri': self.uri}, 'position': {'line': 0, 'character': 18}}
        connection.send('textDocument/hover', params, 'cancel-me')
        self.assertTrue(started.wait(3))
        connection.send('$/cancelRequest', {'id': 'cancel-me'})
        response = connection.take(lambda m: m.get('id') == 'cancel-me')
        self.assertEqual(response['error']['code'], -32800)
        for index in range(60):
            connection.send('textDocument/hover', params, 'queued-' + str(index))
            connection.send('$/cancelRequest', {'id': 'queued-' + str(index)})
            connection.take(lambda m: m.get('id') == 'queued-' + str(index))
        with connection.server.condition:
            self.assertLessEqual(len(connection.server.requests), MAX_PENDING_REQUESTS)
        release.set()
        self.assertIn('result', connection.request('textDocument/hover', params))
        self.assertFalse(any(m.get('id') == 'cancel-me' for m in connection.deferred))

    def test_accepted_request_keeps_snapshot_but_stale_diagnostics_are_discarded(self):
        connection = self.connect()
        original = connection.server._calculate
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(job):
            if job.method == 'diagnostics' and job.snapshot.version == 1:
                started.set()
                release.wait(5)
            return original(job)
        connection.server._calculate = blocked
        self.open(connection, 'let answer=missing;answer')
        self.assertTrue(started.wait(3))
        connection.send('textDocument/didChange', {'textDocument': {'uri': self.uri, 'version': 2},
                         'contentChanges': [{'text': 'let answer=42;answer'}]})
        # A synchronous unknown request establishes that the preceding change ran.
        connection.request('unknown')
        release.set()
        diagnostic = connection.take(lambda m: m.get('method') == 'textDocument/publishDiagnostics')
        self.assertEqual(diagnostic['params']['version'], 2)
        self.assertEqual(diagnostic['params']['diagnostics'], [])
        started.clear(); release.clear()
        def blocked_hover(job):
            if job.method == 'textDocument/hover':
                started.set()
                release.wait(5)
            return original(job)
        connection.server._calculate = blocked_hover
        connection.send('textDocument/hover', {'textDocument': {'uri': self.uri}, 'position': {'line': 0, 'character': 18}}, 'old')
        self.assertTrue(started.wait(3))
        connection.send('textDocument/didChange', {'textDocument': {'uri': self.uri, 'version': 3},
                         'contentChanges': [{'text': 'let answer="new";answer'}]})
        connection.request('unknown')
        release.set()
        response = connection.take(lambda m: m.get('id') == 'old')
        self.assertEqual(response['result']['contents']['value'], 'answer: Int')

    def test_queue_backpressure_and_malformed_stream(self):
        connection = self.connect()
        self.open(connection)
        original = connection.server._calculate
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(job):
            if job.method != 'diagnostics':
                started.set(); release.wait(5)
            return original(job)
        connection.server._calculate = blocked
        params = {'textDocument': {'uri': self.uri}, 'position': {'line': 0, 'character': 0}}
        connection.send('textDocument/hover', params, 'held')
        self.assertTrue(started.wait(3))
        for index in range(MAX_PENDING_REQUESTS):
            connection.send('textDocument/hover', params, 'load-' + str(index))
        response = connection.take(lambda m: m.get('id') == 'load-' + str(MAX_PENDING_REQUESTS - 1))
        self.assertEqual(response['error']['code'], -32803)
        release.set()
        connection.client.sendall(b'Content-Length: invalid\r\n\r\n')
        self.assertEqual(connection.take(lambda m: m.get('id', 'absent') is None)['error']['code'], -32700)
        connection.thread.join(5)
        self.assertEqual(connection.status, 1)
