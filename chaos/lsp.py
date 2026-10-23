"""Native LSP 3.17 over stdio; analysis never runs a Chaos program.

One reader owns protocol ordering. A bounded worker analyzes immutable overlays;
accepted requests retain that snapshot, while diagnostics require a current
workspace generation and document version. No network or filesystem writes are
performed. All output goes through the framed, locked protocol writer.
"""
import collections
import os
import re
import sys
import threading
from dataclasses import dataclass

from .ide_language import analyze, format_source
from .language import LanguageError, tokenize
from .lsp_adapters import capabilities, convert, diagnostics
from .lsp_transport import EOF, FrameError, PayloadError, Writer, read_message
from .lsp_workspace import TextMap, Workspace, integer, root_path, uri_path
from .version import __version__


LANGUAGE_METHODS = frozenset('textDocument/' + name for name in (
    'completion', 'hover', 'signatureHelp', 'semanticTokens/full',
    'documentSymbol', 'definition', 'formatting', 'references', 'prepareRename', 'rename'))
POSITION_METHODS = frozenset('textDocument/' + name for name in (
    'completion', 'hover', 'signatureHelp', 'definition', 'references', 'prepareRename', 'rename'))
MAX_PENDING_REQUESTS = 32


@dataclass
class Job:
    request_id: object
    method: str
    params: dict
    snapshot: object
    cancelled: bool = False


class Server:
    def __init__(self, input_stream, output_stream, root=None, entry=None, language_version=2):
        if type(language_version) is not int or language_version not in (1, 2):
            raise ValueError('language_version must be 1 or 2')
        self.input = input_stream
        self.writer = Writer(output_stream)
        self.configured_root = root
        self.entry = entry
        self.language_version = language_version
        self.state = 'new'
        self.workspace = None
        self.hierarchical_symbols = False
        self.condition = threading.Condition(threading.RLock())
        self.pending = {}
        self.requests = collections.deque()
        self.diagnostic_jobs = {}
        self.stopping = False
        self.worker = threading.Thread(target=self._work, name='chaos-lsp-analysis')

    def response(self, request_id, result=None, code=None, message=None):
        body = {'jsonrpc': '2.0', 'id': request_id}
        if code is None:
            body['result'] = result
        else:
            body['error'] = {'code': code, 'message': message}
        self.writer.send(body)

    def notify(self, method, params):
        self.writer.send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def log(self, message):
        self.notify('window/logMessage', {'type': 2, 'message': message})

    def _initialize(self, params):
        if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY') or os.open not in os.supports_dir_fd:
            raise ValueError('Chaos LSP requires POSIX descriptor-relative file access (macOS or Linux)')
        if self.configured_root is not None:
            roots = [root_path(self.configured_root)]
        else:
            folders = params.get('workspaceFolders')
            if folders is not None and not isinstance(folders, list):
                raise ValueError('workspaceFolders must be an array or null')
            if folders:
                if len(folders) > 16 or any(not isinstance(f, dict) for f in folders):
                    raise ValueError('At most 16 workspace folders are supported')
                roots = [root_path(uri_path(folder.get('uri'))) for folder in folders]
            elif params.get('rootUri') is not None:
                roots = [root_path(uri_path(params['rootUri']))]
            else:
                # A rootless session has no implicit permission to read cwd.
                roots = []
        client = params.get('capabilities')
        text = client.get('textDocument') if isinstance(client, dict) else None
        symbols = text.get('documentSymbol') if isinstance(text, dict) else None
        self.hierarchical_symbols = isinstance(symbols, dict) and symbols.get('hierarchicalDocumentSymbolSupport') is True
        self.workspace = Workspace(roots, self.entry, self.language_version)
        self.state = 'initialized'
        return {'capabilities': capabilities(), 'serverInfo': {'name': 'chaos-language-server', 'version': __version__}}

    def _refresh_diagnostics(self):
        for uri in self.workspace.documents:
            self.diagnostic_jobs[uri] = Job(None, 'diagnostics', {}, self.workspace.snapshot(uri))
        self.condition.notify()

    def _cancel(self, request_id, code=-32800, message='Request cancelled'):
        job = self.pending.pop(request_id, None)
        if job is not None:
            job.cancelled = True
            try:
                self.requests.remove(job)
            except ValueError:
                pass
            self.response(request_id, code=code, message=message)

    def dispatch(self, message):
        """Process one inbound message under the same lock as result commits."""
        with self.condition:
            if not isinstance(message, dict):
                self.response(None, code=-32600, message='A JSON-RPC message must be an object')
                return
            has_id = 'id' in message
            request_id = message.get('id')
            valid_id = isinstance(request_id, str) or integer(request_id)
            method = message.get('method')
            if message.get('jsonrpc') != '2.0' or not isinstance(method, str) or (has_id and not valid_id):
                # Client responses are never expected: this server sends no requests.
                if not has_id and isinstance(method, str):
                    if self.state != 'new':
                        self.log('Invalid JSON-RPC notification')
                else:
                    self.response(request_id if valid_id else None, code=-32600, message='Invalid JSON-RPC request')
                return
            params = message.get('params', {})
            if params is None and method in ('shutdown', 'exit', 'initialized'):
                params = {}
            if not isinstance(params, dict):
                if has_id:
                    self.response(request_id, code=-32602, message='params must be an object')
                elif self.state != 'new':
                    self.log('Ignored notification: params must be an object')
                return
            if method == 'exit' and not has_id:
                self.stopping = True
                return
            if self.state == 'new' and method != 'initialize':
                if has_id:
                    self.response(request_id, code=-32002, message='Server is not initialized')
                return
            if self.state == 'shutdown':
                if has_id:
                    self.response(request_id, code=-32600, message='Server has shut down')
                return
            try:
                if has_id and request_id in self.pending:
                    # Keep the first in-flight request; never respond twice to it.
                    self.log('Ignored duplicate in-flight request id')
                    return
                if method == 'initialize':
                    if not has_id:
                        return
                    if self.state != 'new':
                        self.response(request_id, code=-32600, message='initialize may only be sent once')
                    else:
                        self.response(request_id, self._initialize(params))
                    return
                if method == 'shutdown':
                    if not has_id:
                        self.log('shutdown must be a request')
                        return
                    self.state = 'shutdown'
                    for pending_id in list(self.pending):
                        self._cancel(pending_id, -32803, 'Server is shutting down')
                    self.diagnostic_jobs.clear()
                    self.response(request_id)
                    return
                if method == '$/cancelRequest':
                    if has_id:
                        self.response(request_id, code=-32601, message='Method not found')
                    elif isinstance(params.get('id'), str) or integer(params.get('id')):
                        self._cancel(params['id'])
                    return
                if method in ('initialized', 'textDocument/didOpen', 'textDocument/didChange', 'textDocument/didClose'):
                    if has_id:
                        self.response(request_id, code=-32600, message='This method must be a notification')
                        return
                    if method == 'initialized':
                        self.state = 'ready'
                        return
                    item = params.get('textDocument')
                    if method == 'textDocument/didOpen':
                        self.workspace.open(item)
                    elif method == 'textDocument/didChange':
                        self.workspace.change(item, params.get('contentChanges'))
                    else:
                        uri = self.workspace.close(item)
                        self.diagnostic_jobs.pop(self.workspace.locate(uri)[0], None)
                        self.notify('textDocument/publishDiagnostics', {'uri': uri, 'diagnostics': []})
                    self._refresh_diagnostics()
                    return
                if method in LANGUAGE_METHODS:
                    if not has_id:
                        self.log('Ignored language request without an id')
                        return
                    if len(self.pending) >= MAX_PENDING_REQUESTS:
                        self.response(request_id, code=-32803, message='Analysis request queue is full')
                        return
                    document = params.get('textDocument')
                    if not isinstance(document, dict):
                        raise ValueError('textDocument must be an object')
                    snapshot = self.workspace.snapshot(document.get('uri'))
                    job = Job(request_id, method, params, snapshot)
                    self.pending[request_id] = job
                    self.requests.append(job)
                    self.condition.notify()
                    return
                if has_id:
                    self.response(request_id, code=-32601, message='Method not found')
                # Unknown notifications (including $/) are intentionally ignored.
            except (ValueError, OSError, LanguageError) as error:
                if has_id:
                    self.response(request_id, code=-32602, message=str(error))
                else:
                    self.log('Ignored invalid notification: ' + str(error))

    def _calculate(self, job):
        snapshot = job.snapshot
        symbol_method=job.method in ('textDocument/references','textDocument/prepareRename','textDocument/rename')
        def checkpoint():
            if job.cancelled or self.stopping:raise LanguageError('Request cancelled',code='cancelled')
        files, entry = snapshot.load_package(checkpoint) if symbol_method else snapshot.load()
        source = files[snapshot.name]
        mapping = TextMap(source)
        position = None
        if job.method in POSITION_METHODS:
            position = mapping.units[mapping.offset(job.params.get('position'))]
        if symbol_method:
            from .symbols import references,rename
            options={'file':snapshot.name,'files':files,'entry':entry,'position':position,'language_versions':{name:snapshot.language_version for name in files},'checkpoint':checkpoint}
            if job.method=='textDocument/rename':
                if not isinstance(job.params.get('newName'),str):raise ValueError('rename requires newName')
                result=rename(source,new_name=job.params['newName'],**options)
            else:
                context=job.params.get('context',{})
                if not isinstance(context,dict):raise ValueError('references context must be an object')
                result=references(source,include_declaration=context.get('includeDeclaration',True),**options)
                if not result['complete']:raise LanguageError('Complete symbol references are unproved: '+'; '.join(result.get('limitations',[])),code='symbol_unsupported')
            if job.method in ('textDocument/rename','textDocument/prepareRename'):
                current,_=snapshot.load_package(checkpoint)
                if current!=files:raise LanguageError('Workspace files changed while preparing rename',code='content_modified')
            return convert(job.method,result,snapshot,files,position=position)
        if job.method == 'textDocument/formatting':
            options = job.params.get('options')
            if not isinstance(options, dict) or not integer(options.get('tabSize')) or not 1 <= options['tabSize'] <= 8 or type(options.get('insertSpaces')) is not bool:
                raise ValueError('Formatting requires tabSize 1..8 and insertSpaces')
            formatted = format_source(source, snapshot.name, language_version=snapshot.language_version, indent_size=options['tabSize'])
            if not options['insertSpaces']:
                parts = []
                size = options['tabSize']
                for token in tokenize(formatted, trivia=True):
                    if token.kind == 'eof':
                        continue
                    value = token.value
                    if token.kind == 'space':
                        def indent(match):
                            if match.start() == 0 and token.start and formatted[token.start - 1] not in '\r\n':
                                return match.group()
                            width = len(match.group())
                            return '\t' * (width // size) + ' ' * (width % size)
                        value = re.sub(r'(?m)^ +', indent, value)
                    parts.append(value)
                formatted = ''.join(parts)
            return [] if formatted == source else [{'range': mapping.span(0, mapping.units[-1]), 'newText': formatted}]
        result = analyze(source, file=snapshot.name, files=files, entry=entry, position=position,
                         language_versions={name: snapshot.language_version for name in files})
        if job.method == 'diagnostics':
            return diagnostics(result, mapping)
        return convert(job.method, result, snapshot, files, hierarchical_symbols=self.hierarchical_symbols)

    def _work(self):
        while True:
            with self.condition:
                while not self.stopping and not self.requests and not self.diagnostic_jobs:
                    self.condition.wait()
                if self.stopping:
                    return
                if self.requests:
                    job = self.requests.popleft()
                else:
                    uri = next(iter(self.diagnostic_jobs))
                    job = self.diagnostic_jobs.pop(uri)
                if job.cancelled:
                    continue
            result, code, message = None, None, None
            try:
                result = self._calculate(job)
            except (ValueError, OSError) as error:
                code, message = -32602, str(error)
            except LanguageError as error:
                code, message = (-32801 if error.code=='content_modified' else -32803), str(error)
            except Exception:
                code, message = -32603, 'Language analysis failed'
            with self.condition:
                if self.stopping or job.cancelled:
                    continue
                if job.method == 'diagnostics':
                    document = self.workspace.documents.get(job.snapshot.key)
                    if self.state == 'shutdown' or document is None or self.workspace.generation != job.snapshot.generation or document.version != job.snapshot.version:
                        continue
                    if code is not None:
                        result = [{'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 0}},
                                   'severity': 1, 'code': 'workspace_error', 'source': 'chaos', 'message': message}]
                    self.notify('textDocument/publishDiagnostics', {'uri': document.uri, 'version': document.version, 'diagnostics': result})
                elif self.pending.get(job.request_id) is job:
                    if job.method in ('textDocument/rename','textDocument/prepareRename') and self.workspace.generation!=job.snapshot.generation:
                        result,code,message=None,-32801,'Workspace changed while preparing rename'
                    del self.pending[job.request_id]
                    self.response(job.request_id, result, code, message)

    def run(self):
        self.worker.start()
        malformed = False
        try:
            while not self.stopping and not self.writer.failed:
                try:
                    message = read_message(self.input)
                except PayloadError as error:
                    self.response(None, code=-32700, message=str(error))
                    continue
                except FrameError as error:
                    self.response(None, code=-32700, message=str(error))
                    malformed = True
                    break
                if message is EOF:
                    break
                self.dispatch(message)
        except (OSError, ValueError):
            malformed = True
        finally:
            with self.condition:
                self.stopping = True
                for pending_id in list(self.pending):
                    self._cancel(pending_id, -32803, 'Transport closed')
                self.requests.clear()
                self.diagnostic_jobs.clear()
                self.condition.notify_all()
            self.worker.join()
        return 0 if self.state == 'shutdown' and not malformed and not self.writer.failed else 1


def run_stdio(input_stream=None, output_stream=None, root=None, entry=None, language_version=2):
    """Run the server using binary streams; return a process-compatible status."""
    input_stream = sys.stdin.buffer if input_stream is None else input_stream
    output_stream = sys.stdout.buffer if output_stream is None else output_stream
    return Server(input_stream, output_stream, root, entry, language_version).run()
