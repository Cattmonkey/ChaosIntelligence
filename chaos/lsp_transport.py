"""Bounded binary framing for the Language Server Protocol (JSON-RPC 2.0)."""
import json
import re
import threading


MAX_HEADER_BYTES = 8192
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
EOF = object()


class FrameError(ValueError):
    """The stream cannot safely be synchronized after an invalid frame."""


class PayloadError(ValueError):
    """A complete frame contained invalid JSON or UTF-8; framing is intact."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Non-finite JSON number')


def read_message(stream):
    """Read exactly one framed message, or return EOF at a clean EOF."""
    headers, size = {}, 0
    while True:
        line = stream.readline(MAX_HEADER_BYTES - size + 1)
        if not line:
            if not size:
                return EOF
            raise FrameError('Truncated message header')
        size += len(line)
        if size > MAX_HEADER_BYTES or not line.endswith(b'\r\n'):
            raise FrameError('Invalid or oversized message header')
        if line == b'\r\n':
            break
        try:
            name, value = line[:-2].decode('ascii').split(':', 1)
        except (UnicodeError, ValueError):
            raise FrameError('Invalid ASCII header') from None
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', name):
            raise FrameError('Invalid header name')
        if any(ord(char) < 32 and char != '\t' or ord(char) == 127 for char in value):
            raise FrameError('Invalid control character in header')
        name, value = name.lower(), value.strip()
        if name in headers:
            raise FrameError('Duplicate message header')
        headers[name] = value
    length = headers.get('content-length', '')
    if not re.fullmatch(r'[0-9]{1,10}', length):
        raise FrameError('Missing or invalid Content-Length')
    length = int(length)
    if not 0 < length <= MAX_MESSAGE_BYTES:
        raise FrameError('Message body exceeds limit')
    content_type = headers.get('content-type', 'application/vscode-jsonrpc; charset=utf-8')
    for parameter in content_type.split(';')[1:]:
        key, separator, value = parameter.strip().partition('=')
        if key.lower() == 'charset' and (not separator or value.strip(' "').lower() not in ('utf-8', 'utf8')):
            raise FrameError('Only UTF-8 content is supported')
    chunks, remaining = [], length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise FrameError('Truncated message body')
        chunks.append(chunk)
        remaining -= len(chunk)
    try:
        # json.loads also rejects unescaped control characters and trailing data.
        return json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=_object,
                          parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise PayloadError('Invalid UTF-8 JSON message') from None


class Writer:
    """One lock covers the complete header/body write and flush."""
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()
        self.failed = False

    def send(self, message):
        data = json.dumps(message, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(data) > MAX_MESSAGE_BYTES:
            if 'id' not in message:
                message = {'jsonrpc': '2.0', 'method': 'window/logMessage',
                           'params': {'type': 2, 'message': 'Notification exceeds protocol size limit'}}
            else:
                message = {'jsonrpc': '2.0', 'id': message['id'],
                           'error': {'code': -32803, 'message': 'Response exceeds protocol size limit'}}
            data = json.dumps(message, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
            if len(data) > MAX_MESSAGE_BYTES:
                data = b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Request id exceeds protocol size limit"}}'
        frame = ('Content-Length: %d\r\n\r\n' % len(data)).encode('ascii') + data
        with self.lock:
            if self.failed:
                return
            try:
                view = memoryview(frame)
                while view:
                    written = self.stream.write(view)
                    if written is None or written <= 0:
                        raise OSError('Output stream stopped accepting data')
                    view = view[written:]
                self.stream.flush()
            except (OSError, ValueError):
                self.failed = True
