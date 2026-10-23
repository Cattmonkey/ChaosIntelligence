import argparse
import json
import os
from pathlib import Path
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .extensions import json_copy, TERMINAL_STATES
from .framework import Framework
from .graph import validate, GraphError
from .language import LanguageError, Parser
from .store import Conflict, NotFound
from .version import __version__
from .modules import ProgramBundle, STDLIB
from .program import parse_program, format_program, project_program, SHAPES, templates

from .resources import resource_directory


def static_path(filename):
    return resource_directory('web') / filename


def wire_run(value):
    result=dict(value)
    for key in ('input','output'):result[key+'Json']=json.dumps(value.get(key),ensure_ascii=False,allow_nan=False,indent=2)
    return result


def wire_event(value):
    # Copy top-level Store-derived provenance; never promote payload keys.
    result=dict(value);payload=dict(value['payload'])
    if 'output'in payload:payload['outputJson']=json.dumps(payload['output'],ensure_ascii=False,allow_nan=False,indent=2)
    result['payload']=payload
    return result


def make_server(host='127.0.0.1', port=8765, db=None, *, framework=None, settings=None, state_dir=None, update_home=None, registry=None):
    """A borrowed Framework stays owned by its caller; otherwise server owns it."""
    owned = framework is None
    if framework is not None and db is not None:
        raise ValueError('Pass framework or db, not both')
    if framework is not None and registry is not None:
        raise ValueError('Pass framework or registry, not both')
    from .settings import Settings
    from .control import ControlPlane
    from .ide_agent import IDEAgentService
    app = framework if framework is not None else Framework(db or Path.cwd() / '.chaos' / 'workspace.sqlite3',settings=settings or Settings(),registry=registry)
    if settings is not None:app._engine.settings=settings
    store, engine = app._store, app._engine
    stopping = threading.Event()
    control = None
    try:
        control = ControlPlane(app._engine.settings,state_dir or (Path(store.path).parent / 'control' if store.path != ':memory:' else __import__('tempfile').mkdtemp(prefix='chaos-control-')))
        ide = IDEAgentService(app, control.state_dir / 'ide')
    except Exception:
        if control is not None: control.close()
        if owned:app.close()
        raise

    from .updater import UpdateManager, UpdateError
    from .documentation import Documentation
    admission = threading.RLock()
    maintenance = threading.Event()

    def resume_admission(*args, **kwargs):
        with admission:
            maintenance.clear()
            control.manager.pause_dispatch(False)
            ide.manager.pause_dispatch(False)
            engine.pause_dispatch(False)

    def can_activate():
        # Existing requests finish before this gate. Active computations finish
        # naturally; queued background jobs remain persisted for the new process.
        with admission:
            maintenance.set()
            control.manager.pause_dispatch(True)
            ide.manager.pause_dispatch(True)
            engine.pause_dispatch(True)
            with engine.lock:
                return not engine.active and not engine.operations and control.manager.is_idle() and ide.manager.is_idle()

    def activate(version):
        server.restart_requested = True
        threading.Thread(target=server.shutdown, name='chaos-update-restart', daemon=True).start()

    def update_event(*args, **kwargs):
        # An update failure must never strand ordinary programming operations.
        if any(isinstance(item, dict) and item.get('type') == 'update.failed' for item in args):
            resume_admission()

    try:
        documentation = Documentation()
        updates = UpdateManager(home=update_home, current_version=__version__,
                                can_activate=can_activate, on_activate=activate,
                                on_event=update_event)
    except Exception:
        control.close()
        ide.close()
        if owned: app.close()
        raise

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            pass

        @property
        def versioned(self):
            return urllib.parse.urlsplit(self.path).path.startswith('/api/v1/')

        def respond(self, status, value, headers=None):
            value=app._engine.settings.redact(value)
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if status >= 400:
                self.close_connection = True
                self.send_header('Connection', 'close')
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def error(self, status, code, message, details=None, headers=None):
            error = {'code': code, 'message': str(message), 'details': details} if self.versioned else str(message)
            self.respond(status, {'error': error}, headers)

        def require_method(self, *allowed):
            if self.command not in allowed:
                self.error(405, 'method_not_allowed', 'Method not allowed', headers={'Allow': ', '.join(allowed)})
                return False
            return True

        def body(self):
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Chunked request bodies are not supported')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4000000:
                raise ValueError('Request body must contain 1 byte–4 MB')
            self.connection.settimeout(15)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('Incomplete request body')
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
            if not isinstance(value, dict):
                raise ValueError('Request body must be a JSON object')
            return json_copy(value, max_chars=4000000, max_nodes=300000, max_depth=400)

        @staticmethod
        def required(body, *keys):
            missing = set(keys) - set(body)
            if missing:
                raise ValueError('Missing fields: ' + ', '.join(sorted(missing)))

        def check_host(self):
            port = self.server.server_port
            allowed = {f'localhost:{port}', f'127.0.0.1:{port}', f'[::1]:{port}'}
            if host not in ('0.0.0.0', '::'):
                allowed.add(f'{host}:{port}')
            authority = self.headers.get('Host', '').lower()
            if port == 80:
                allowed.update({'localhost', '127.0.0.1', '[::1]'})
            origin = self.headers.get('Origin')
            if authority not in allowed or (origin and origin != 'http://' + authority):
                raise PermissionError('Untrusted Host or Origin')

        def dispatch(self):
            try:
                self.check_host()
                request_path = urllib.parse.urlsplit(self.path).path
                update_request = request_path.startswith(('/api/updates', '/api/v1/updates'))
                if self.command not in ('GET', 'OPTIONS') and not update_request:
                    with admission:
                        if maintenance.is_set() and not request_path.endswith('/cancel'):
                            self.error(503, 'updating', 'Update is waiting for active work to finish; retry after restart')
                            return
                        self.route()
                else:
                    self.route()
            except UpdateError as e:
                if getattr(e, 'code', '') in ('conflict', 'busy'):
                    self.error(409, getattr(e, 'code', 'update_error'), e)
                else:
                    self.error(400, getattr(e, 'code', 'update_error'), e)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Conflict as e:
                self.error(409, 'conflict', e)
            except NotFound as e:
                self.error(404, 'not_found', e.args[0])
            except PermissionError as e:
                self.error(403, 'forbidden', e)
            except (ValueError, TypeError, KeyError, LanguageError, RecursionError) as e:
                self.error(400, 'validation_error', str(e)[:1000], e.record() if isinstance(e,LanguageError) else None)
            except TimeoutError:
                self.error(408, 'request_timeout', 'Request timed out')
            except RuntimeError as e:
                self.error(503, 'unavailable', str(e)[:1000])
            except Exception:
                self.error(500, 'internal_error', 'Internal service error')

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = dispatch

        def event_cursor(self, query):
            return int(self.headers.get('Last-Event-ID') or query.get('after', '0'))

        def stream_events(self, read, after, finished=None):
            # Every selector/cursor must be checked before SSE headers are committed.
            read(after, 1)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            deadline = time.monotonic() + 25
            while not stopping.is_set() and time.monotonic() < deadline:
                events = read(after, 100)
                if events:
                    for event in events:
                        public = engine.settings.redact(wire_event(event))
                        self.wfile.write(('id: ' + str(event['id']) + '\ndata: ' + json.dumps(public, ensure_ascii=False) + '\n\n').encode())
                        after = event['id']
                else:
                    self.wfile.write(b': heartbeat\n\n')
                self.wfile.flush()
                if finished is not None and finished():
                    # Status can become terminal between reads. Drain the committed
                    # tail rather than closing at the first terminal-looking event.
                    if not read(after, 1):
                        return
                if len(events) < 100:
                    stopping.wait(.2)

        def stream(self, project_id, after, run_id=None, process_key=None):
            def read(cursor, limit):
                return store.events(project_id, cursor, limit, run_id, process_key=process_key)
            def finished():
                if run_id:
                    return store.run(run_id)['status'] in TERMINAL_STATES
                if process_key is not None:
                    record = store.processes.get(project_id, process_key)
                    return record['status'] == 'completed' and record['pending'] is None
                return False
            return self.stream_events(read, after, finished)

        def activity_stream(self, manager, after, task_id=None, read=None):
            if task_id is not None:
                manager.get(task_id)
            def finished():
                if task_id is None:
                    return False
                # Background status commits before its final event. The worker is
                # retained in _active through that event; other active jobs must not
                # keep this selected job's observer open.
                with manager._lock:
                    return (manager.get(task_id)['status'] in
                            ('completed', 'failed', 'cancelled', 'interrupted') and
                            task_id not in manager._active)
            if read is None:
                read = lambda cursor, limit: manager.events(task_id=task_id, after=cursor, limit=limit)
            return self.stream_events(read, after, finished)

        def route(self):
            url = urllib.parse.urlsplit(self.path)
            values = urllib.parse.parse_qs(url.query, keep_blank_values=True)
            if any(len(v) != 1 for v in values.values()):
                raise ValueError('Duplicate query parameter')
            query = {k: v[0] for k, v in values.items()}
            path = url.path
            versioned = self.versioned
            if versioned:
                path = '/api/' + path[len('/api/v1/'):]
            parts = [urllib.parse.unquote(p) for p in path.strip('/').split('/')]
            if path == '/api/ui/schema':
                if self.require_method('GET'): self.respond(200, Framework.ui_schema())
                return
            if path in ('/api/ui/compile', '/api/ui/export', '/api/ui/validate'):
                if not self.require_method('POST'): return
                body = self.body()
                allowed_fields = {'/api/ui/compile': {'source', 'kind', 'title'},
                                  '/api/ui/export': {'definition', 'format'},
                                  '/api/ui/validate': {'definition', 'requirements'}}[path]
                if set(body) - allowed_fields: raise ValueError('Unknown UI request fields')
                if path == '/api/ui/compile':
                    self.required(body, 'source')
                    result = Framework.ui_compile(body['source'], kind=body.get('kind', 'page'), title=body.get('title', ''))
                elif path == '/api/ui/export':
                    self.required(body, 'definition')
                    result = Framework.ui_export(body['definition'], format=body.get('format', 'html'))
                else:
                    self.required(body, 'definition')
                    result = Framework.ui_validate(body['definition'], requirements=body.get('requirements', []))
                # Never return a silently redacted, hash-inconsistent artifact.
                if app._engine.settings.redact(result) != result:
                    raise PermissionError('UI content contains configured private data')
                return self.respond(200, result)
            if path == '/api/docs':
                if self.require_method('GET'): self.respond(200, documentation.catalog())
                return
            if len(parts) == 3 and parts[:2] == ['api', 'docs']:
                if not self.require_method('GET'): return
                try: page = documentation.page(parts[2])
                except KeyError: raise NotFound(parts[2]) from None
                return self.respond(200, page)
            if path.startswith('/docs/files/examples/'):
                if not self.require_method('GET'): return
                try: body = documentation.attachment(urllib.parse.unquote(path[len('/docs/files/examples/'):]))
                except ValueError: raise NotFound('Example file') from None
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
                return
            if path == '/api/updates':
                if self.require_method('GET'):
                    value = updates.status()
                    value['maintenance'] = maintenance.is_set()
                    self.respond(200, value)
                return
            if path == '/api/updates/config':
                if not self.require_method('PATCH'): return
                body = self.body()
                if 'publicKey' in body:
                    raise PermissionError('Publisher trust must be configured locally with chaos update configure')
                revision = body.pop('version', None)
                if revision is None: raise ValueError('version is required')
                return self.respond(200, updates.configure(body, version=revision))
            if path in ('/api/updates/check', '/api/updates/apply', '/api/updates/rollback'):
                if not self.require_method('POST'): return
                body = self.body()
                if body: raise ValueError('Update actions take an empty object')
                action = path.rsplit('/', 1)[1]
                if action != 'check' and (not owned or os.environ.get('CHAOS_SUPERVISED') != '1'):
                    raise ValueError('Start the managed launcher before applying updates')
                return self.respond(202, updates.start(action))
            if path == '/api/settings':
                if not self.require_method('GET','PATCH'):return
                if self.command=='GET':return self.respond(200,engine.settings.public())
                body=self.body()
                if 'plugins'in body:
                    # Validate registered ids before persistence or lifecycle changes.
                    known={p['id']for p in control.plugins.inventory()}
                    if not isinstance(body['plugins'],list)or any(not isinstance(p,dict)or p.get('id')not in known for p in body['plugins']):raise ValueError('Unknown plugin')
                result=engine.settings.update(body)
                control.manager.configure(result['runtime']['maxConcurrent'])
                control.plugins.refresh(result['plugins'])
                return self.respond(200,result)
            if path == '/api/models/discover':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'provider')
                return self.respond(200,engine.settings.discover(body['provider']))
            if path == '/api/harness/status':
                if self.require_method('GET'):self.respond(200,control.status())
                return
            if path == '/api/tasks/events/stream':
                if not self.require_method('GET'): return
                if set(query) - {'taskId', 'after'}: raise ValueError('Unknown task stream query fields')
                return self.activity_stream(control.manager, self.event_cursor(query), query.get('taskId'))
            if path == '/api/tasks/events':
                if not self.require_method('GET'):return
                events=control.manager.events(task_id=query.get('taskId'),after=int(query.get('after',0)),limit=int(query.get('limit',200)))
                return self.respond(200,{'items':events,'lastEventId':events[-1]['id']if events else int(query.get('after',0))})
            if path == '/api/tasks':
                if not self.require_method('GET','POST'):return
                if self.command=='GET':return self.respond(200,{'items':control.manager.list()})
                return self.respond(202,control.start(self.body()))
            if len(parts)==3 and parts[:2]==['api','tasks']:
                if self.require_method('GET'):self.respond(200,control.manager.get(parts[2]))
                return
            if len(parts)==4 and parts[:2]==['api','tasks'] and parts[3]in ('cancel','resume'):
                if not self.require_method('POST'):return
                self.body()
                if parts[3]=='cancel':return self.respond(200,{'accepted':control.manager.cancel(parts[2])})
                return self.respond(202,control.manager.resume(parts[2]))
            if path == '/api/health':
                if self.require_method('GET'):
                    health = {'status': 'ok', 'version': __version__, 'apiVersion': 'v1', 'instanceId': os.environ.get('CHAOS_INSTANCE_ID', ''), 'pid': os.getpid()}
                    if os.environ.get('CHAOS_SERVICE_ID'):
                        health['serviceId'] = os.environ['CHAOS_SERVICE_ID']
                    self.respond(200, health)
                return
            if path == '/api/openapi.json':
                if self.require_method('GET'):
                    from .openapi import specification
                    self.respond(200, specification())
                return
            if path == '/api/capabilities':
                if self.require_method('GET'):
                    self.respond(200, app.capabilities())
                return
            if path == '/api/bootstrap':
                if self.require_method('GET'):
                    project_id = query.get('projectId', 'workspace')
                    self.respond(200, {**app.snapshot(project_id), 'tools': app.capabilities()['tools'], 'modelConfigured': bool(engine.settings.public()['providers'].get('text',{}).get('hasKey'))})
                return
            if path in ('/api/events', '/api/events/stream'):
                if not self.require_method('GET'):
                    return
                if versioned and 'projectId' not in query:
                    raise ValueError('projectId is required')
                project_id = query.get('projectId', 'workspace')
                after = self.event_cursor(query)
                if not versioned or path.endswith('/stream'):
                    return self.stream(project_id,after,query.get('runId'),query.get('processKey'))
                page=app.events(project_id,after=after,limit=int(query.get('limit',100)),
                                run_id=query.get('runId'),process_key=query.get('processKey'))
                page['items']=[wire_event(event)for event in page['items']]
                return self.respond(200,page)
            if path == '/api/language':
                if not self.require_method('GET'):return
                return self.respond(200,{'version':__version__,'schema':'chaos.program/1','syntax':SHAPES,'templates':templates(),'standardModules':sorted(STDLIB)})
            if path == '/api/language/analyze':
                if not self.require_method('POST'):return
                from .ide_language import analyze
                body=self.body();self.required(body,'source')
                return self.respond(200,analyze(body['source'],file=body.get('file','main.chaos'),files=body.get('files'),entry=body.get('entry','main.chaos'),position=body.get('position'),language_versions=body.get('languageVersions')))
            if path == '/api/language/compile-artifact':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'source')
                if set(body)-{'source','files','entry','languageVersions'}:raise ValueError('Unknown artifact compilation request fields')
                result=app.compile_artifact(body['source'],files=body.get('files'),entry=body.get('entry','main.chaos'),language_versions=body.get('languageVersions'))
                # An artifact contains source. Never silently redact bytes that
                # participate in its identity or deterministic validation.
                if app._engine.settings.redact(result)!=result:raise PermissionError('Artifact contains configured private data')
                return self.respond(200,result)
            if path == '/api/language/validate-artifact':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'artifact')
                if set(body)!={'artifact'}:raise ValueError('Unknown artifact validation request fields')
                return self.respond(200,app.validate_artifact(body['artifact']))
            if path == '/api/language/compile':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'source')
                if set(body)-{'source','files','entry','languageVersions'}:raise ValueError('Unknown compilation request fields')
                return self.respond(200,app.compile(body['source'],files=body.get('files'),entry=body.get('entry','main.chaos'),language_versions=body.get('languageVersions')))
            if path in ('/api/language/references','/api/language/rename'):
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'source','position')
                rename=path.endswith('/rename')
                allowed={'source','file','files','entry','position','languageVersions', 'newName' if rename else 'includeDeclaration'}
                if set(body)-allowed:raise ValueError('Unknown symbol request fields')
                options={'file':body.get('file','main.chaos'),'files':body.get('files'),'entry':body.get('entry','main.chaos'),'position':body['position'],'language_versions':body.get('languageVersions')}
                if rename:
                    self.required(body,'newName')
                    return self.respond(200,app.rename(body['source'],new_name=body['newName'],**options))
                return self.respond(200,app.references(body['source'],include_declaration=body.get('includeDeclaration',True),**options))
            if path == '/api/language/format-source':
                if not self.require_method('POST'):return
                from .ide_language import format_source
                body=self.body();self.required(body,'source')
                return self.respond(200,{'source':format_source(body['source'],body.get('file','main.chaos'),language_version=body.get('languageVersion',2),line_width=body.get('lineWidth',88),indent_size=body.get('indentSize',2))})
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='files':
                if not self.require_method('POST'):return
                from .ide_files import edit_project_files
                return self.respond(200,edit_project_files(app,parts[2],self.body()))
            if len(parts)>=4 and parts[:2]==['api','projects'] and parts[3]=='assistant':
                project_id=parts[2]
                if len(parts)==4:
                    if not self.require_method('GET','POST'):return
                    if self.command=='GET':return self.respond(200,ide.history(project_id,session_id=query.get('sessionId')))
                    body=self.body();self.required(body,'version','prompt')
                    return self.respond(202,ide.start(project_id,body['version'],body['prompt'],session_id=body.get('sessionId'),editor_context=body.get('editorContext')))
                if len(parts)==5:
                    if self.require_method('GET'):self.respond(200,ide.get(project_id,parts[4]))
                    return
                if len(parts)==7 and parts[5:] == ['events', 'stream']:
                    if not self.require_method('GET'): return
                    if set(query) - {'after'}: raise ValueError('Unknown assistant stream query fields')
                    job_id = parts[4]
                    # Keep the same project ownership check as the JSON event API.
                    def read(cursor, limit):
                        return ide.events(project_id, job_id, after=cursor, limit=limit)['items']
                    return self.activity_stream(ide.manager, self.event_cursor(query), job_id, read)
                if len(parts)==6 and parts[5]=='events':
                    if not self.require_method('GET'):return
                    return self.respond(200,ide.events(project_id,parts[4],after=int(query.get('after',0)),limit=int(query.get('limit',200))))
                if len(parts)==6 and parts[5] in ('cancel','apply'):
                    if not self.require_method('POST'):return
                    body=self.body()
                    if parts[5]=='cancel':return self.respond(200,ide.cancel(project_id,parts[4]))
                    self.required(body,'version')
                    return self.respond(200,ide.apply(project_id,parts[4],body['version'],editor_context=body.get('editorContext')))
                return self.error(404,'not_found','Unknown assistant route')
            if path == '/api/language/format':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'program')
                return self.respond(200,{'source':format_program(body['program'])})
            if path == '/api/language/parse':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'source')
                bundle=ProgramBundle(body['source'],body.get('files'),body.get('entry','main.chaos'))
                ir=parse_program(body['source'],bundle.entry)
                return self.respond(200,{'program':ir,'projection':project_program(ir),'formatted':format_program(ir),'modules':bundle.summary()})
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='runtime-view':
                if not self.require_method('GET'):return
                if set(query)-{'runId'}:raise ValueError('Unknown runtime view query fields')
                return self.respond(200,app.runtime_view(parts[2],run_id=query.get('runId')))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='snapshot':
                if self.require_method('GET'):self.respond(200,app.snapshot(parts[2]))
                return
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='program':
                if not self.require_method('GET','POST'):return
                if self.command=='GET':return self.respond(200,app.program(parts[2],file=query.get('file')))
                body=self.body();self.required(body,'version')
                return self.respond(200,app.edit_program(parts[2],body['version'],source=body.get('source'),program=body.get('program'),operations=body.get('operations'),file=body.get('file'),capabilities=body.get('capabilities')))
            if path == '/api/computers/schema':
                if self.require_method('GET'):self.respond(200,app.computer_schema())
                return
            if path == '/api/computers/validate':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'spec')
                if set(body)-{'spec','registered'}:raise ValueError('Unknown computer validation fields')
                return self.respond(200,app.validate_computer_action(body['spec'],registered=body.get('registered',False)))
            if len(parts)>=4 and parts[:2]==['api','projects'] and parts[3]=='computer-sessions':
                project_id=parts[2]
                if len(parts)==4:
                    if not self.require_method('GET'):return
                    if set(query)-{'after','limit'}:raise ValueError('Unknown computer sessions query')
                    return self.respond(200,app.computer_sessions(project_id,after=int(query.get('after',0)),limit=int(query.get('limit',50))))
                if len(parts)==5:
                    if not self.require_method('GET'):return
                    if query:raise ValueError('Unknown computer session query')
                    return self.respond(200,app.computer_session(project_id,parts[4]))
            if len(parts)>=4 and parts[:2]==['api','projects'] and parts[3]=='computer-records':
                if not self.require_method('GET'):return
                project_id=parts[2]
                if len(parts)==4:
                    if set(query)-{'runId','after','limit'}:raise ValueError('Unknown computer history query')
                    return self.respond(200,app.computer_history(project_id,run_id=query.get('runId'),after=int(query.get('after',0)),limit=int(query.get('limit',50))))
                if len(parts)==5:
                    if query:raise ValueError('Unknown computer record query')
                    return self.respond(200,app.computer_record(project_id,parts[4]))
                if len(parts)==6 and parts[5]=='image':
                    if set(query)-{'variant'}:raise ValueError('Unknown computer image query')
                    return self.respond(200,app.computer_image(project_id,parts[4],variant=query.get('variant','preview')))
            if path == '/api/agent-sessions/schema':
                if self.require_method('GET'):self.respond(200,app.session_schema())
                return
            if len(parts)>=4 and parts[:2]==['api','projects'] and parts[3]=='agent-sessions':
                project_id=parts[2]
                if len(parts)==4:
                    if not self.require_method('GET','POST'):return
                    if self.command=='GET':return self.respond(200,app.agent_sessions(project_id,after=query.get('after',''),limit=int(query.get('limit',100))))
                    body=self.body();self.required(body,'version','key','agent')
                    if set(body)-{'version','key','agent','contextLimit'}:raise ValueError('Unknown session creation fields')
                    return self.respond(201,app.open_agent_session(project_id,body['version'],body['key'],body['agent'],context_limit=body.get('contextLimit',100000)))
                key=parts[4]
                if len(parts)==5:
                    if self.require_method('GET'):self.respond(200,app.agent_session(project_id,key))
                    return
                if len(parts)==6 and parts[5]=='history':
                    if self.require_method('GET'):self.respond(200,app.agent_session_history(project_id,key,after=int(query.get('after',0)),limit=int(query.get('limit',100))))
                    return
                if len(parts)==6 and parts[5] in ('turns','fork','compact','close','reconcile'):
                    if not self.require_method('POST'):return
                    body=self.body();self.required(body,'revision');action=parts[5]
                    if action=='turns':
                        self.required(body,'version','input')
                        if set(body)-{'version','revision','input','messageId'}:raise ValueError('Unknown session turn fields')
                        run=app.start_session_turn(project_id,body['version'],key,body['revision'],body['input'],message_id=body.get('messageId'))
                        return self.respond(202,run.get())
                    if action=='fork':
                        self.required(body,'newKey')
                        if set(body)-{'revision','newKey','agent'}:raise ValueError('Unknown session fork fields')
                        return self.respond(201,app.fork_agent_session(project_id,key,body['revision'],body['newKey'],agent=body.get('agent')))
                    if action=='compact':
                        self.required(body,'keepTurns')
                        if set(body)-{'revision','keepTurns'}:raise ValueError('Unknown session compaction fields')
                        return self.respond(200,app.compact_agent_session(project_id,key,body['revision'],body['keepTurns']))
                    if action=='close':
                        if set(body)-{'revision'}:raise ValueError('Unknown session close fields')
                        return self.respond(200,app.close_agent_session(project_id,key,body['revision']))
                    self.required(body,'evidence')
                    if set(body)-{'revision','evidence'}:raise ValueError('Unknown session reconciliation fields')
                    return self.respond(200,app.reconcile_agent_session(project_id,key,body['revision'],body['evidence']))
            if path == '/api/processes/schema':
                if self.require_method('GET'):self.respond(200,app.process_schema())
                return
            if path == '/api/processes/validate':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'definition')
                if set(body)-{'definition','name'}:raise ValueError('Unknown process validation fields')
                return self.respond(200,app.validate_process(body['definition'],name=body.get('name','saved_process')))
            if len(parts)>=4 and parts[:2]==['api','projects'] and parts[3]=='processes':
                project_id=parts[2]
                if len(parts)==4:
                    if not self.require_method('GET','POST'):return
                    if self.command=='GET':return self.respond(200,app.processes(project_id,after=query.get('after',''),limit=int(query.get('limit',100))))
                    body=self.body();self.required(body,'version','key','definition')
                    if set(body)-{'version','key','definition','state'}:raise ValueError('Unknown process creation fields')
                    return self.respond(201,app.create_process(project_id,body['version'],body['key'],body['definition'],state=body.get('state')))
                key=parts[4]
                if len(parts)==5:
                    if self.require_method('GET'):self.respond(200,app.get_process(project_id,key))
                    return
                if len(parts)==7 and parts[5]=='messages':
                    if not self.require_method('GET'):return
                    if query:raise ValueError('Unknown process message query fields')
                    return self.respond(200,app.get_process_message(project_id,key,parts[6]))
                if len(parts)==6 and parts[5] in ('messages','guidance','control','configure','reconcile'):
                    if parts[5]=='guidance':
                        if not self.require_method('GET','POST'):return
                        if self.command=='GET':
                            if query:raise ValueError('Unknown process guidance query fields')
                            return self.respond(200,app.process_guidance(project_id,key))
                    elif not self.require_method('POST'):return
                    body=self.body();action=parts[5]
                    if action=='messages':
                        self.required(body,'input')
                        if set(body)-{'input','messageId'}:raise ValueError('Unknown process message fields')
                        return self.respond(202,app.send_process(project_id,key,body['input'],message_id=body.get('messageId')))
                    if action=='guidance':
                        self.required(body,'input')
                        if query:raise ValueError('Unknown process guidance query fields')
                        if set(body)-{'input','messageId','target'}:raise ValueError('Unknown process guidance fields')
                        if 'target' in body and body['target'] is None:raise ValueError('Guidance target must be a Process role object')
                        extra={} if 'target' not in body else {'target':body['target']}
                        return self.respond(202,app.guide_process(project_id,key,body['input'],message_id=body.get('messageId'),**extra))
                    self.required(body,'revision')
                    if action=='control':
                        self.required(body,'action')
                        if set(body)-{'revision','action'}:raise ValueError('Unknown process control fields')
                        return self.respond(200,app.control_process(project_id,key,body['revision'],body['action']))
                    if action=='configure':
                        self.required(body,'definition')
                        if set(body)-{'revision','definition'}:raise ValueError('Unknown process configuration fields')
                        return self.respond(200,app.configure_process(project_id,key,body['revision'],body['definition']))
                    self.required(body,'state')
                    if set(body)-{'revision','state','evidence','resume'}:raise ValueError('Unknown process reconciliation fields')
                    return self.respond(200,app.reconcile_process(project_id,key,body['revision'],body['state'],evidence=body.get('evidence',''),resume=body.get('resume',False)))
            if path == '/api/programs/schema':
                if self.require_method('GET'):self.respond(200,app.program_schema())
                return
            if path == '/api/programs/validate':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'definition')
                if set(body)-{'definition','name'}:raise ValueError('Unknown program validation fields')
                return self.respond(200,app.validate_program(body['definition'],name=body.get('name','saved_program')))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='program-history':
                if not self.require_method('GET'):return
                if set(query)-{'after','limit'}:raise ValueError('Unknown program history query fields')
                return self.respond(200,app.program_history(parts[2],after=int(query.get('after',0)),limit=int(query.get('limit',50))))
            if len(parts)==5 and parts[:2]==['api','projects'] and parts[3]=='program-revisions':
                if not self.require_method('GET'):return
                if query:raise ValueError('Program revision accepts no query fields')
                return self.respond(200,app.program_revision(parts[2],int(parts[4])))
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='program-snapshot':
                if self.require_method('GET'):self.respond(200,app.program_snapshot(parts[2]))
                return
            if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='program-adopt':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'version','definition','expectedHash','cases')
                if set(body)-{'version','definition','expectedHash','cases','reason'}:raise ValueError('Unknown program adoption fields')
                return self.respond(200,app.adopt_program(parts[2],body['version'],body['definition'],expected_hash=body['expectedHash'],cases=body['cases'],reason=body.get('reason','')))
            if path == '/api/teams/schema':
                if self.require_method('GET'):self.respond(200,app.team_schema())
                return
            if path == '/api/teams/validate':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'definition')
                if set(body)-{'definition','name'}:raise ValueError('Unknown team validation fields')
                return self.respond(200,app.validate_team(body['definition'],name=body.get('name','saved_team')))
            if path == '/api/agents/schema':
                if self.require_method('GET'):self.respond(200,app.agent_schema())
                return
            if path == '/api/agents/validate':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'definition')
                if set(body)-{'definition','name'}:raise ValueError('Unknown agent validation fields')
                return self.respond(200,app.validate_agent(body['definition'],name=body.get('name','saved_agent')))
            if path == '/api/modes/validate':
                if not self.require_method('POST'):return
                body=self.body();self.required(body,'definition')
                if set(body)-{'definition','name'}:raise ValueError('Unknown mode validation fields')
                return self.respond(200,app.validate_mode(body['definition'],name=body.get('name','saved_mode')))
            if len(parts)>=4 and parts[:2]==['api','projects'] and parts[3]=='modes':
                project_id=parts[2]
                if len(parts)==4:
                    if self.require_method('GET'):self.respond(200,app.modes(project_id,kind=query.get('kind'),after=query.get('after',''),limit=int(query.get('limit',100))))
                    return
                key=parts[4]
                if len(parts)==5:
                    if not self.require_method('GET','PUT'):return
                    if self.command=='GET':return self.respond(200,app.mode(project_id,key,revision=int(query['revision']) if 'revision'in query else None))
                    body=self.body();self.required(body,'definition')
                    return self.respond(200,app.save_mode(project_id,key,body['definition'],revision=body.get('revision')))
                if len(parts)==6 and parts[5]=='history':
                    if self.require_method('GET'):self.respond(200,app.mode_history(project_id,key,after=int(query.get('after',0)),limit=int(query.get('limit',100))))
                    return
                if len(parts)==6 and parts[5]=='reconcile':
                    if not self.require_method('POST'):return
                    body=self.body();self.required(body,'revision','state')
                    return self.respond(200,app.reconcile_goal(project_id,key,body['revision'],body['state'],evidence=body.get('evidence')))
                if len(parts)==6 and parts[5]=='source':
                    if not self.require_method('GET'):return
                    return self.respond(200,app.mode_source(project_id,key,revision=int(query['revision']) if 'revision'in query else None,format=query.get('format'),name=query.get('name')))
                return self.error(404,'not_found','Unknown mode route')
            if path == '/api/projects':
                if not self.require_method('GET', 'POST'):
                    return
                if self.command == 'GET':
                    return self.respond(200, app.projects(after=query.get('after', ''), limit=int(query.get('limit', 100))))
                body = self.body(); self.required(body, 'id')
                return self.respond(201, app.create_project(body['id'], name=body.get('name'), graph=body.get('graph'), source=body.get('source'), capabilities=body.get('capabilities'),files=body.get('files'),entry=body.get('entry','main.chaos')))
            if len(parts) == 3 and parts[:2] == ['api', 'projects']:
                if not self.require_method('GET', 'PUT'):
                    return
                if self.command == 'GET':
                    return self.respond(200, app.get_project(parts[2]))
                body = self.body(); self.required(body, 'version', 'graph', 'source', 'name')
                if 'id' in body and body['id'] != parts[2]:
                    raise ValueError('Body project id differs from path')
                return self.respond(200, app.save_project({**body, 'id': parts[2]}))
            if len(parts) == 4 and parts[:2] == ['api', 'projects'] and parts[3] in ('patch', 'optimize', 'validate'):
                if not self.require_method('POST'):
                    return
                body = self.body()
                if parts[3] == 'validate':
                    if type(body.get('complete', True)) is not bool:
                        raise ValueError('complete must be a boolean')
                    return self.respond(200, app.validate(parts[2], complete=body.get('complete', True)))
                self.required(body, 'version')
                if parts[3] == 'optimize':
                    return self.respond(200, app.optimize(parts[2], body['version']))
                self.required(body, 'operations')
                return self.respond(200, app.patch(parts[2], body['version'], body['operations'], actor=body.get('actor'), reason=body.get('reason', '用户编辑')))
            if path == '/api/validate':
                if not self.require_method('POST'):
                    return
                body = self.body(); self.required(body, 'graph')
                if type(body.get('complete', False)) is not bool:
                    raise ValueError('complete must be a boolean')
                result = validate(body['graph'], body.get('complete', False))
                if 'source' in body:
                    if not isinstance(body['source'], str):
                        raise ValueError('source must be a string')
                    ProgramBundle(body['source'],body.get('files'),body.get('entry','main.chaos'))
                return self.respond(200, result)
            if path == '/api/runs':
                if not self.require_method('GET', 'POST'):
                    return
                if self.command == 'GET':
                    if versioned and 'projectId' not in query:
                        raise ValueError('projectId is required')
                    project_id = query.get('projectId', 'workspace')
                    return self.respond(200, app.runs(project_id, after=int(query.get('after', 0)), limit=int(query.get('limit', 100))) if versioned else store.runs(project_id))
                body = self.body(); self.required(body, 'version')
                if versioned:
                    self.required(body, 'projectId')
                value=body.get('input')
                if 'inputJson'in body:
                    if 'input'in body or not isinstance(body['inputJson'],str):raise ValueError('inputJson must be text and is exclusive with input')
                    value=json_copy(json.loads(body['inputJson'],parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite input'))))
                if 'executable' in body:
                    if body.get('mode', 'executable') != 'executable' or body.get('autoOptimize', False) is not False or body.get('debug') is not None:
                        raise ValueError('Executable runs require mode executable, autoOptimize false and no source debugger')
                    handle = app.start_executable(body.get('projectId', 'workspace'), body['version'], body['executable'], value, trace=body.get('trace', False))
                else:
                    handle = app.start(body.get('projectId', 'workspace'), body['version'], value, mode=body.get('mode', 'graph'), auto_optimize=body.get('autoOptimize', not versioned),trace=body.get('trace',False),debug=body.get('debug'))
                return self.respond(202, wire_run(handle.get()))
            if len(parts) == 3 and parts[:2] == ['api', 'runs']:
                if self.require_method('GET'):
                    self.respond(200, wire_run(app.get_run(parts[2])))
                return
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'debugger':
                if not self.require_method('GET', 'POST'):return
                if self.command == 'GET':return self.respond(200, app.debugger(parts[2]))
                body = self.body(); self.required(body, 'command')
                return self.respond(200, app.debug_command(parts[2], body['command'], pause_id=body.get('pauseId'), breakpoints=body.get('breakpoints'),restart_id=body.get('restartId'),args=body.get('args'),kwargs=body.get('kwargs')))
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'guidance':
                if not self.require_method('GET', 'POST'): return
                if query: raise ValueError('Unknown guidance query fields')
                if self.command == 'GET': return self.respond(200, app.guidance_scopes(parts[2]))
                body = self.body(); self.required(body, 'input')
                if set(body) - {'input', 'messageId', 'target'}: raise ValueError('Unknown guidance fields')
                if 'target' in body and body['target'] is None: raise ValueError('Guidance target must be a Bot scope object')
                extra = {} if 'target' not in body else {'target': body['target']}
                return self.respond(202, app.guide(parts[2], body['input'], message_id=body.get('messageId'), **extra))
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'cancel':
                if self.require_method('POST'):
                    self.body()
                    self.respond(200, {'accepted': app.cancel(parts[2])})
                return
            static = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/language.js': ('language.js', 'text/javascript'), '/sync.js': ('sync.js','text/javascript'), '/json.js': ('json.js','text/javascript'), '/style.css': ('style.css', 'text/css')}
            static.update({'/artifacts.js':('artifacts.js','text/javascript'),'/artifacts.css':('artifacts.css','text/css'),'/vendor/openui-bundle.min.js':('vendor/openui-bundle.min.js','text/javascript')})
            static.update({'/runtime-network.js':('runtime-network.js','text/javascript'),'/runtime-network.css':('runtime-network.css','text/css')})
            static.update({'/theme.css': ('theme.css', 'text/css'), '/docs/theme.css': ('theme.css', 'text/css')})
            static.update({'/control':('control.html','text/html'),'/control.html':('control.html','text/html'),'/control.js':('control.js','text/javascript'),'/control.css':('control.css','text/css'),'/activity.js':('activity.js','text/javascript'),'/activity.css':('activity.css','text/css'),'/dsh-state.js':('dsh-state.js','text/javascript')})
            static.update({'/updates.js':('updates.js','text/javascript'),'/updates.css':('updates.css','text/css')})
            static.update({'/docs':('docs.html','text/html'),'/docs/':('docs.html','text/html'),'/docs.css':('docs.css','text/css'),'/docs.js':('docs.js','text/javascript'),'/docs/docs.css':('docs.css','text/css'),'/docs/docs.js':('docs.js','text/javascript')})
            static.update({'/processes.js':('processes.js','text/javascript'),'/processes.css':('processes.css','text/css')})
            static.update({'/computers.js':('computers.js','text/javascript'),'/computers.css':('computers.css','text/css')})
            static.update({'/sessions.js':('sessions.js','text/javascript'),'/sessions.css':('sessions.css','text/css')})
            static.update({'/modes.js':('modes.js','text/javascript'),'/modes.css':('modes.css','text/css')})
            static.update({'/debugger.js':('debugger.js','text/javascript'),'/debugger.css':('debugger.css','text/css')})
            static.update({prefix+name:(name,'text/css' if name.endswith('.css') else 'text/javascript') for prefix in ('/','/docs/') for name in ('code.js','code.css')})
            static.update({'/editor-assist.js':('editor-assist.js','text/javascript'),'/editor.js':('editor.js','text/javascript'),'/editor.css':('editor.css','text/css')})
            static.update({'/ide.js':('ide.js','text/javascript'),'/ide.css':('ide.css','text/css'),'/vendor/codemirror.js':('vendor/codemirror.js','text/javascript')})
            static.update({'/realtime.js':('realtime.js','text/javascript')})
            static.update({'/workbench.js':('workbench.js','text/javascript'),'/workbench.css':('workbench.css','text/css')})
            if path in static:
                if not self.require_method('GET'):
                    return
                filename, mime = static[path]
                body = static_path(filename).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mime + '; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
                self.end_headers(); self.wfile.write(body)
                return
            self.error(404, 'not_found', 'Route not found')

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def server_close(self):
            stopping.set()
            updates.close()
            control.close()
            ide.close()
            super().server_close()
            if owned:
                app.close()

    try:
        server = Server((host, port), Handler)
    except Exception:
        updates.close()
        control.close()
        ide.close()
        if owned:
            app.close()
        raise
    server.store, server.engine, server.framework, server.control = store, engine, app, control
    server.ide = ide
    server.updates = updates
    server.restart_requested = False
    if owned: updates.start_auto_check()
    return server


def main():
    parser = argparse.ArgumentParser(description='Chaos Intelligence local framework and programming environment')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db')
    parser.add_argument('--config',help='Private server configuration file')
    parser.add_argument('--state-dir',help='Background task database directory')
    parser.add_argument('--devices',help='Trusted serial/Linux HID device configuration JSON file')
    parser.add_argument('--robots',help='Trusted registered Robot configuration JSON file; registration is not authorization')
    parser.add_argument('--computers',help='Trusted Computer/Mobile MCP configuration JSON file; registration is not authorization')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()
    from .settings import Settings
    from .device_serial import load_device_registry
    registry = load_device_registry(args.devices) if args.devices else None
    if args.robots:
        from .robot_config import load_robot_registry
        registry = load_robot_registry(args.robots, registry)
    if args.computers:
        from .computer_config import load_computer_registry
        registry=load_computer_registry(args.computers,registry)
    server = make_server(args.host, args.port, args.db,settings=Settings(args.config),state_dir=args.state_dir,registry=registry)
    print(f'Chaos Intelligence {__version__} → http://{args.host}:{server.server_port}', flush=True)
    import signal
    previous_term = None
    installed_term = threading.current_thread() is threading.main_thread()
    def terminate(signum, frame):
        # Raising unwinds serve_forever on its own thread, then closes the same
        # owned runtime. No watcher, second supervisor or cross-thread deadlock.
        raise KeyboardInterrupt()
    if installed_term:
        previous_term = signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        finally:
            if installed_term:
                signal.signal(signal.SIGTERM, previous_term)
    return 75 if server.restart_requested else 0


if __name__ == '__main__':
    raise SystemExit(main())
