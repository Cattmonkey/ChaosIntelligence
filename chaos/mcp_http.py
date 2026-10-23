"""Bounded, non-retrying MCP 2025-11-25 Streamable HTTP client.

This client talks only to an already-running trusted server. No dependency
installation, process launch, device discovery policy or model keys live here.
A completed local worker does not establish remote mutation completion.
"""
import ipaddress
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class MCPTransportError(RuntimeError):
    pass


class MCPToolError(MCPTransportError):
    pass


class RemoteOutcomeUnknown(MCPTransportError):
    pass


def endpoint_url(value):
    if type(value) is not str or not 1 <= len(value) <= 2048:
        raise ValueError('MCP endpoint must be a bounded host-configured URL')
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError()
        parsed.port
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname.lower() == 'localhost'
        if parsed.scheme == 'http' and not local:
            raise ValueError()
    except ValueError:
        raise ValueError('MCP requires HTTPS, or loopback HTTP, without credentials/query/fragment') from None
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MCPTransportError('MCP redirects are not followed')


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MCPTransportError('Duplicate MCP JSON object key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise MCPTransportError('Malformed MCP JSON response') from None


def decode_response(raw, content_type, request_id):
    """Decode JSON or complete SSE events; never search prose for a result."""
    try:
        value = raw.decode('utf-8')
    except UnicodeError:
        raise MCPTransportError('MCP response is not UTF-8') from None
    kind = content_type.split(';', 1)[0].strip().lower()
    if kind == 'application/json':
        messages = [_json(value)]
    elif kind == 'text/event-stream':
        messages = []
        fields = []
        # EOF dispatch is accepted because stateless servers close after a reply.
        for line in value.replace('\r\n', '\n').replace('\r', '\n').split('\n') + ['']:
            if not line:
                if fields:
                    messages.append(_json('\n'.join(fields)))
                    fields = []
            elif line.startswith('data:'):
                fields.append(line[5:].removeprefix(' '))
            elif line.startswith(':') or line.startswith(('event:', 'id:', 'retry:')):
                continue
            else:
                raise MCPTransportError('Unsupported MCP SSE field')
    else:
        raise MCPTransportError('Unsupported MCP response Content-Type')
    replies = []
    for message in messages:
        if type(message) is not dict or message.get('jsonrpc') != '2.0':
            raise MCPTransportError('Invalid MCP JSON-RPC envelope')
        if 'id' not in message:
            # Notifications are not results and never authorize input.
            if type(message.get('method')) is not str or 'result' in message or 'error' in message:
                raise MCPTransportError('Invalid MCP notification')
            continue
        if type(message['id']) is not type(request_id) or message['id'] != request_id:
            raise MCPTransportError('MCP response identity does not match the request')
        if ('result' in message) == ('error' in message):
            raise MCPTransportError('MCP response needs exactly one result or error')
        replies.append(message)
    if len(replies) != 1:
        raise MCPTransportError('MCP needs one matching terminal response')
    return replies[0]


class MCPHttpClient:
    PROTOCOL = '2025-11-25'
    MAX_RESPONSE = 2 * 1024 * 1024

    def __init__(self, endpoint, *, token=None, timeout=15.0):
        self.endpoint = endpoint_url(endpoint)
        if token is not None and (type(token) is not str or not 1 <= len(token) <= 8192 or '\r' in token or '\n' in token):
            raise ValueError('Invalid MCP authorization token')
        if type(timeout) not in (int, float) or not 0.1 <= timeout <= 60:
            raise ValueError('MCP timeout must be 0.1..60 seconds')
        self._token = token
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._active = None
        self._closed = False
        self._ready = False
        self._tainted = False
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, method, params, checkpoint, *, mutation=False, notification=False):
        checkpoint()
        with self._lock:
            if self._closed or self._tainted:
                raise MCPTransportError('MCP connection is closed or requires trusted reconciliation')
            if self._active is not None:
                raise MCPTransportError('MCP request is already in flight')
            request_id = uuid.uuid4().hex
            document = {'jsonrpc': '2.0', 'method': method, 'params': params}
            if not notification:
                document['id'] = request_id
            body = json.dumps(document, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if len(body) > 160000:
                raise MCPTransportError('MCP request exceeds its byte budget')
            slot = {'done': threading.Event(), 'terminal': False, 'dispatched': False,
                    'result': None, 'error': None, 'mutation': mutation}
            self._active = slot
        started = time.monotonic()
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': self.PROTOCOL}
        if self._token is not None:
            headers['Authorization'] = 'Bearer ' + self._token
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method='POST')
        def worker():
            try:
                slot['dispatched'] = True
                with self._opener.open(request, timeout=self.timeout) as response:
                    if response.headers.get('Mcp-Session-Id') is not None:
                        raise MCPTransportError('This Mobile MCP adapter requires a stateless HTTP endpoint')
                    if notification:
                        if response.status != 202 or response.read(1):
                            raise MCPTransportError('MCP notification was not accepted without a response body')
                        slot['terminal'] = True
                        slot['result'] = {'jsonrpc': '2.0', 'id': request_id, 'result': {}}
                        return
                    if response.status != 200:
                        raise MCPTransportError('MCP request returned an unexpected HTTP status')
                    chunks = []
                    received = 0
                    while True:
                        # A socket timeout limits a stalled read; this also bounds
                        # a peer that continually streams new chunks.
                        if time.monotonic() - started >= self.timeout:
                            raise MCPTransportError('MCP response deadline expired')
                        chunk = response.read1(8192)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > self.MAX_RESPONSE:
                            raise MCPTransportError('MCP response exceeds its byte budget')
                        chunks.append(chunk)
                    reply = decode_response(b''.join(chunks), response.headers.get('Content-Type', ''), request_id)
                    slot['terminal'] = True
                    slot['result'] = reply
            except BaseException as error:
                slot['error'] = error
            finally:
                slot['done'].set()
        thread = threading.Thread(target=worker, name='chaos-mcp-http', daemon=True)
        slot['thread'] = thread
        thread.start()
        try:
            while not slot['done'].wait(0.025):
                checkpoint()
                if time.monotonic() - started >= self.timeout:
                    raise MCPTransportError('MCP request timed out; remote outcome may be unknown')
            checkpoint()
            if slot['error'] is not None:
                raise MCPTransportError('MCP request failed; remote outcome may be unknown') from None
            reply = slot['result']
            if 'error' in reply:
                raise MCPToolError('MCP server rejected the request')
            if type(reply['result']) is not dict:
                raise MCPTransportError('MCP result must be a record')
            return reply['result']
        except BaseException:
            if slot['dispatched'] and not slot['terminal']:
                self._tainted = True
            raise
        finally:
            # A canceled worker remains tracked for close(), never replaced by a
            # later request. The resource host owns the physical lease.
            if slot['done'].is_set():
                with self._lock:
                    if self._active is slot:
                        self._active = None

    def initialize(self, checkpoint):
        if self._ready:
            return
        result = self._request('initialize', {
            'protocolVersion': self.PROTOCOL, 'capabilities': {},
            'clientInfo': {'name': 'chaos-intelligence', 'version': '1'}}, checkpoint)
        if (result.get('protocolVersion') != self.PROTOCOL or type(result.get('serverInfo')) is not dict
                or result['serverInfo'].get('name') != 'mobile-mcp'):
            raise MCPTransportError('Unsupported MCP server identity or protocol revision')
        self._request('notifications/initialized', {}, checkpoint, notification=True)
        self._ready = True

    def call_tool(self, name, arguments, checkpoint, *, mutation=False):
        self.initialize(checkpoint)
        result = self._request('tools/call', {'name': name, 'arguments': arguments}, checkpoint, mutation=mutation)
        if result.get('isError', False) is not False:
            raise MCPToolError('Mobile MCP reported a tool error')
        if type(result.get('content')) is not list or len(result['content']) > 4:
            raise MCPTransportError('Invalid MCP tool content blocks')
        return result

    def close(self):
        self._closed = True
        slot = self._active
        if slot is not None and not slot['done'].is_set():
            raise RemoteOutcomeUnknown('MCP request has not reached a confirmed remote terminal response')
        if self._tainted:
            # Even after the socket worker stops, a remote handler may continue.
            # Only a genuine matched terminal reply can discharge that concern.
            if slot is None or not slot['terminal']:
                raise RemoteOutcomeUnknown('Remote MCP completion is unknown; retain the physical lease')
            self._tainted = False
        self._active = None
