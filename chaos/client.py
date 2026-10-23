"""Dependency-free HTTP SDK for the versioned Chaos API."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from .extensions import json_copy
from .framework import RunHandle, wait_for, watch_events, timeout_deadline
from .workflow import Workflow


class APIError(RuntimeError):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.details = status, code, details
        super().__init__(message)


class ValidationError(APIError):
    pass


class NotFoundError(APIError):
    pass


class ConflictError(APIError):
    pass


class TransportError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, base_url='http://127.0.0.1:8765', *, timeout=30):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('base_url must be an HTTP(S) origin, without credentials, path, query or fragment')
        timeout_deadline(timeout)
        if timeout is None or timeout == 0:
            raise ValueError('HTTP timeout must be positive and finite')
        self.base_url, self.timeout = base_url.rstrip('/'), timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not value:
            raise ValueError('Identifier must be a nonempty string')
        return urllib.parse.quote(value, safe='')

    def _request(self, method, path, body=None, query=None, timeout=None):
        suffix = '?' + urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None}) if query else ''
        data = None if body is None else json.dumps(json_copy(body, max_chars=4000000, max_nodes=300000, max_depth=400), ensure_ascii=False, allow_nan=False).encode('utf-8')
        request = urllib.request.Request(self.base_url + '/api/v1' + path + suffix, data=data, method=method,
                                         headers={'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with self._opener.open(request, timeout=self.timeout if timeout is None else max(.001, min(timeout, self.timeout))) as response:
                raw = response.read(8000001)
            if len(raw) > 8000000:
                raise TransportError('Response exceeds SDK size limit')
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read(100000))
                error = body['error']
                if not isinstance(error, dict):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                error = {'code': 'http_error', 'message': 'HTTP ' + str(e.code)}
            cls = {400: ValidationError, 404: NotFoundError, 409: ConflictError}.get(e.code, APIError)
            raise cls(e.code, error.get('code', 'http_error'), error.get('message', 'HTTP request failed'), error.get('details')) from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise TransportError('Chaos service connection failed or timed out') from e
        except (ValueError, UnicodeError) as e:
            raise TransportError('Chaos service returned invalid JSON') from e

    def updates(self):return self._request('GET','/updates')
    def configure_updates(self, patch):return self._request('PATCH','/updates/config',patch)
    def check_updates(self):return self._request('POST','/updates/check',{})
    def apply_update(self):return self._request('POST','/updates/apply',{})
    def rollback_update(self):return self._request('POST','/updates/rollback',{})

    def settings(self):
        return self._request('GET','/settings')

    def runtime_view(self, project_id, *, run_id=None):
        return self._request('GET','/projects/'+self._id(project_id)+'/runtime-view',query={} if run_id is None else {'runId':run_id})

    def ui_schema(self):
        return self._request('GET', '/ui/schema')

    def ui_compile(self, source, *, kind='page', title=''):
        return self._request('POST', '/ui/compile', {'source': source, 'kind': kind, 'title': title})

    def ui_export(self, definition, *, format='html'):
        return self._request('POST', '/ui/export', {'definition': definition, 'format': format})

    def ui_validate(self, definition, *, requirements=None):
        return self._request('POST', '/ui/validate', {'definition': definition, 'requirements': [] if requirements is None else requirements})

    def configure(self, patch):
        return self._request('PATCH','/settings',patch)

    def discover_models(self, provider='text'):
        return self._request('POST','/models/discover',{'provider':provider})

    def start_task(self,prompt,*,agent_id='background',role='executor',iterations=1,interval=15):
        return self._request('POST','/tasks',{'prompt':prompt,'agentId':agent_id,'role':role,'iterations':iterations,'intervalSeconds':interval})

    def tasks(self):return self._request('GET','/tasks')
    def task(self,id):return self._request('GET','/tasks/'+self._id(id))
    def cancel_task(self,id):return self._request('POST','/tasks/'+self._id(id)+'/cancel',{})
    def resume_task(self,id):return self._request('POST','/tasks/'+self._id(id)+'/resume',{})
    def task_events(self,*,after=0,task_id=None):return self._request('GET','/tasks/events',query={'after':after,'taskId':task_id})
    def harness_status(self):return self._request('GET','/harness/status')

    def capabilities(self):
        return self._request('GET', '/capabilities')

    def create_project(self, id, *, name=None, graph=None, source=None, capabilities=None, files=None, entry='main.chaos'):
        body = {'id': id, 'name': name or id, 'source': source, 'files': files, 'entry': entry}
        if graph is not None:
            body['graph'] = graph.build(complete=False) if isinstance(graph, Workflow) else graph
        if capabilities is not None:
            body['capabilities'] = list(capabilities)
        return self._request('POST', '/projects', body)

    def projects(self, *, after='', limit=100):
        return self._request('GET', '/projects', query={'after': after, 'limit': limit})

    def get_project(self, id):
        return self._request('GET', '/projects/' + self._id(id))

    def validate_agent(self, definition, *, name='saved_agent'):
        return self._request('POST', '/agents/validate', {'definition': definition, 'name': name})

    def agent_schema(self):
        return self._request('GET', '/agents/schema')

    def validate_team(self, definition, *, name='saved_team'):
        return self._request('POST', '/teams/validate', {'definition': definition, 'name': name})

    def team_schema(self):
        return self._request('GET', '/teams/schema')

    def validate_process(self, definition, *, name='saved_process'):
        return self._request('POST','/processes/validate',{'definition':definition,'name':name})

    def process_schema(self):
        return self._request('GET','/processes/schema')

    def create_process(self, project_id, version, key, definition, *, state=None):
        return self._request('POST','/projects/'+self._id(project_id)+'/processes',
                             {'version':version,'key':key,'definition':definition,'state':state})

    def processes(self, project_id, *, after='', limit=100):
        return self._request('GET','/projects/'+self._id(project_id)+'/processes',query={'after':after,'limit':limit})

    def get_process(self, project_id, key):
        return self._request('GET','/projects/'+self._id(project_id)+'/processes/'+self._id(key))

    def get_process_message(self, project_id, key, message_id):
        return self._request('GET','/projects/'+self._id(project_id)+'/processes/'+self._id(key)+'/messages/'+self._id(message_id))

    def send_process(self, project_id, key, value, *, message_id=None):
        return self._request('POST','/projects/'+self._id(project_id)+'/processes/'+self._id(key)+'/messages',{'input':value,'messageId':message_id})

    def guide_process(self, project_id, key, value, *, message_id=None, target=None):
        body = {'input':value,'messageId':message_id}
        if target is not None: body['target'] = target
        return self._request('POST','/projects/'+self._id(project_id)+'/processes/'+self._id(key)+'/guidance',body)

    def process_guidance(self, project_id, key):
        return self._request('GET','/projects/'+self._id(project_id)+'/processes/'+self._id(key)+'/guidance')

    def control_process(self, project_id, key, revision, action):
        return self._request('POST','/projects/'+self._id(project_id)+'/processes/'+self._id(key)+'/control',{'revision':revision,'action':action})

    def configure_process(self, project_id, key, revision, definition):
        return self._request('POST','/projects/'+self._id(project_id)+'/processes/'+self._id(key)+'/configure',{'revision':revision,'definition':definition})

    def reconcile_process(self, project_id, key, revision, state, *, evidence='', resume=False):
        return self._request('POST','/projects/'+self._id(project_id)+'/processes/'+self._id(key)+'/reconcile',
                             {'revision':revision,'state':state,'evidence':evidence,'resume':resume})

    def computer_schema(self):
        return self._request('GET','/computers/schema')

    def validate_computer_action(self, spec, *, registered=False):
        body={'spec':spec}
        if registered is not False:body['registered']=registered
        return self._request('POST','/computers/validate',body)

    def computer_sessions(self, project_id, *, after=0, limit=50):
        return self._request('GET','/projects/'+self._id(project_id)+'/computer-sessions',
                             query={'after':after,'limit':limit})

    def computer_session(self, project_id, key):
        return self._request('GET','/projects/'+self._id(project_id)+'/computer-sessions/'+self._id(key))

    def computer_history(self, project_id, *, run_id=None, after=0, limit=50):
        query={'after':after,'limit':limit}
        if run_id is not None:query['runId']=run_id
        return self._request('GET','/projects/'+self._id(project_id)+'/computer-records?'+urllib.parse.urlencode(query))

    def computer_record(self, project_id, record_id):
        return self._request('GET','/projects/'+self._id(project_id)+'/computer-records/'+self._id(record_id))

    def computer_image(self, project_id, record_id, *, variant='preview'):
        return self._request('GET','/projects/'+self._id(project_id)+'/computer-records/'+self._id(record_id)+'/image?'+urllib.parse.urlencode({'variant':variant}))

    def session_schema(self):
        return self._request('GET','/agent-sessions/schema')

    def open_agent_session(self, project_id, version, key, agent, *, context_limit=100000):
        return self._request('POST','/projects/'+self._id(project_id)+'/agent-sessions',{'version':version,'key':key,'agent':agent,'contextLimit':context_limit})

    def agent_sessions(self, project_id, *, after='', limit=100):
        return self._request('GET','/projects/'+self._id(project_id)+'/agent-sessions',query={'after':after,'limit':limit})

    def agent_session(self, project_id, key):
        return self._request('GET','/projects/'+self._id(project_id)+'/agent-sessions/'+self._id(key))

    def agent_session_history(self, project_id, key, *, after=0, limit=100):
        return self._request('GET','/projects/'+self._id(project_id)+'/agent-sessions/'+self._id(key)+'/history',query={'after':after,'limit':limit})

    def start_session_turn(self, project_id, version, key, revision, value, *, message_id=None):
        run=self._request('POST','/projects/'+self._id(project_id)+'/agent-sessions/'+self._id(key)+'/turns',{'version':version,'revision':revision,'input':value,'messageId':message_id})
        return RunHandle(self,run)

    def fork_agent_session(self, project_id, key, revision, new_key, *, agent=None):
        return self._request('POST','/projects/'+self._id(project_id)+'/agent-sessions/'+self._id(key)+'/fork',{'revision':revision,'newKey':new_key,'agent':agent})

    def compact_agent_session(self, project_id, key, revision, keep_turns):
        return self._request('POST','/projects/'+self._id(project_id)+'/agent-sessions/'+self._id(key)+'/compact',{'revision':revision,'keepTurns':keep_turns})

    def close_agent_session(self, project_id, key, revision):
        return self._request('POST','/projects/'+self._id(project_id)+'/agent-sessions/'+self._id(key)+'/close',{'revision':revision})

    def reconcile_agent_session(self, project_id, key, revision, evidence):
        return self._request('POST','/projects/'+self._id(project_id)+'/agent-sessions/'+self._id(key)+'/reconcile',{'revision':revision,'evidence':evidence})

    def modes(self, project_id, *, kind=None, after='', limit=100):
        return self._request('GET','/projects/'+self._id(project_id)+'/modes',query={'kind':kind,'after':after,'limit':limit})

    def mode(self, project_id, key, *, revision=None):
        return self._request('GET','/projects/'+self._id(project_id)+'/modes/'+self._id(key),query={'revision':revision})

    def validate_mode(self, definition, *, name='saved_mode'):
        return self._request('POST','/modes/validate',{'definition':definition,'name':name})

    def mode_source(self, project_id, key, *, revision=None, format=None, name=None):
        return self._request('GET','/projects/'+self._id(project_id)+'/modes/'+self._id(key)+'/source',
                             query={'revision':revision,'format':format,'name':name})

    def save_mode(self, project_id, key, definition, *, revision=None):
        body={'definition':definition}
        if revision is not None:body['revision']=revision
        return self._request('PUT','/projects/'+self._id(project_id)+'/modes/'+self._id(key),body)

    def mode_history(self, project_id, key, *, after=0, limit=100):
        return self._request('GET','/projects/'+self._id(project_id)+'/modes/'+self._id(key)+'/history',query={'after':after,'limit':limit})

    def reconcile_goal(self, project_id, key, revision, state, *, evidence=None):
        return self._request('POST','/projects/'+self._id(project_id)+'/modes/'+self._id(key)+'/reconcile',{'revision':revision,'state':state,'evidence':evidence})

    def save_project(self, project, *, from_view=None):
        body={key:project[key]for key in ('id','version','name','graph','source','files','entry','capabilities','editFrom','sourceVersions')if key in project}
        if from_view is not None:body['editFrom']=from_view
        return self._request('PUT', '/projects/' + self._id(project['id']), body)

    def snapshot(self, project_id='workspace'):
        return self._request('GET','/projects/'+self._id(project_id)+'/snapshot')

    def validate_program(self, definition, *, name='saved_program'):
        return self._request('POST','/programs/validate',{'definition':definition,'name':name})

    def program_schema(self):
        return self._request('GET','/programs/schema')

    def program_snapshot(self, project_id):
        return self._request('GET','/projects/'+self._id(project_id)+'/program-snapshot')

    def program_history(self, project_id, *, after=0, limit=50):
        return self._request('GET','/projects/'+self._id(project_id)+'/program-history',query={'after':after,'limit':limit})

    def program_revision(self, project_id, version):
        from .store import version_number
        version_number(version)
        if version>2**63-1:raise ValueError('Program revision version exceeds SQLite integer range')
        return self._request('GET','/projects/'+self._id(project_id)+'/program-revisions/'+str(version))

    def adopt_program(self, project_id, version, definition, *, expected_hash, cases, reason=''):
        return self._request('POST','/projects/'+self._id(project_id)+'/program-adopt',
                             {'version':version,'definition':definition,'expectedHash':expected_hash,'cases':cases,'reason':reason})

    def program(self,project_id,*,file=None):
        return self._request('GET','/projects/'+self._id(project_id)+'/program',query={'file':file} if file else {})

    def edit_program(self,project_id,version,*,source=None,program=None,operations=None,file=None,capabilities=None):
        body={'version':version}
        for key,value in [('source',source),('program',program),('operations',operations),('file',file),('capabilities',capabilities)]:
            if value is not None:body[key]=value
        return self._request('POST','/projects/'+self._id(project_id)+'/program',body)

    def analyze(self, source, *, file='main.chaos', files=None, entry='main.chaos', position=None, language_versions=None):
        return self._request('POST','/language/analyze',{'source':source,'file':file,'files':files,'entry':entry,'position':position,'languageVersions':language_versions})

    def compile_artifact(self, source, files=None, entry='main.chaos', *, language_versions=None):
        return self._request('POST','/language/compile-artifact',{'source':source,'files':files,'entry':entry,'languageVersions':language_versions})

    def validate_artifact(self, artifact):
        return self._request('POST','/language/validate-artifact',{'artifact':artifact})

    def compile(self, source, *, files=None, entry='main.chaos', language_versions=None):
        return self._request('POST','/language/compile',{'source':source,'files':files,'entry':entry,'languageVersions':language_versions})

    def references(self, source, *, file='main.chaos', files=None, entry='main.chaos', position, language_versions=None, include_declaration=True):
        return self._request('POST','/language/references',{'source':source,'file':file,'files':files,'entry':entry,'position':position,'languageVersions':language_versions,'includeDeclaration':include_declaration})

    def rename(self, source, *, file='main.chaos', files=None, entry='main.chaos', position, new_name, language_versions=None):
        return self._request('POST','/language/rename',{'source':source,'file':file,'files':files,'entry':entry,'position':position,'newName':new_name,'languageVersions':language_versions})

    def format_source(self, source, *, file='main.chaos', language_version=2, line_width=88, indent_size=2):
        return self._request('POST','/language/format-source',{'source':source,'file':file,'languageVersion':language_version,'lineWidth':line_width,'indentSize':indent_size})['source']

    def edit_files(self, project_id, version, operation, path, *, new_path=None, source=None, directory=False):
        body={'version':version,'operation':operation,'path':path,'directory':directory}
        if new_path is not None:body['newPath']=new_path
        if source is not None:body['source']=source
        return self._request('POST','/projects/'+self._id(project_id)+'/files',body)

    def assistant_history(self, project_id, *, session_id=None):
        return self._request('GET','/projects/'+self._id(project_id)+'/assistant',query={'sessionId':session_id})

    def start_assistant(self, project_id, version, prompt, *, session_id=None, editor_context=None):
        body = {'version':version,'prompt':prompt,'sessionId':session_id}
        if editor_context is not None: body['editorContext'] = editor_context
        return self._request('POST','/projects/'+self._id(project_id)+'/assistant',body)

    def assistant(self, project_id, job_id):
        return self._request('GET','/projects/'+self._id(project_id)+'/assistant/'+self._id(job_id))

    def assistant_events(self, project_id, job_id, *, after=0, limit=200):
        return self._request('GET','/projects/'+self._id(project_id)+'/assistant/'+self._id(job_id)+'/events',query={'after':after,'limit':limit})

    def cancel_assistant(self, project_id, job_id):
        return self._request('POST','/projects/'+self._id(project_id)+'/assistant/'+self._id(job_id)+'/cancel',{})

    def apply_assistant(self, project_id, job_id, version, *, editor_context=None):
        body = {'version':version}
        if editor_context is not None: body['editorContext'] = editor_context
        return self._request('POST','/projects/'+self._id(project_id)+'/assistant/'+self._id(job_id)+'/apply',body)

    def validate(self, project_id, *, complete=True):
        return self._request('POST', '/projects/' + self._id(project_id) + '/validate', {'complete': complete})

    def patch(self, project_id, version, operations, *, actor=None, reason='SDK edit'):
        return self._request('POST', '/projects/' + self._id(project_id) + '/patch', {'version': version, 'operations': operations, 'actor': actor, 'reason': reason})

    def optimize(self, project_id, version):
        return self._request('POST', '/projects/' + self._id(project_id) + '/optimize', {'version': version})

    def start(self, project_id, version, value, *, mode='graph', auto_optimize=False, trace=False, debug=None):
        run = self._request('POST', '/runs', {'projectId': project_id, 'version': version, 'input': value, 'mode': mode, 'autoOptimize': auto_optimize,'trace':trace,'debug':debug})
        return RunHandle(self, run)

    def start_executable(self, project_id, version, image, value=None, *, trace=False):
        run = self._request('POST', '/runs', {'projectId': project_id, 'version': version,
            'input': value, 'mode': 'executable', 'autoOptimize': False,
            'trace': trace, 'executable': image})
        return RunHandle(self, run)

    def start_debug(self, project_id, version, value=None, *, breakpoints=None, pause_on_entry=True, pause_timeout=300, pause_on_condition=False):
        debug={'breakpoints': [] if breakpoints is None else breakpoints,
               'pauseOnEntry': pause_on_entry, 'pauseTimeout': pause_timeout}
        # Default calls retain the v1 wire shape accepted by older servers.
        # Non-booleans must still reach the ordinary strict options validator.
        if pause_on_condition is not False:debug['pauseOnCondition']=pause_on_condition
        return self.start(project_id, version, value, mode='source', auto_optimize=False,debug=debug)

    def debugger(self, run_id):
        return self._request('GET', '/runs/' + self._id(run_id) + '/debugger')

    def debug_command(self, run_id, command, *, pause_id=None, breakpoints=None, restart_id=None, args=None, kwargs=None):
        body = {'command': command}
        if pause_id is not None: body['pauseId'] = pause_id
        if breakpoints is not None: body['breakpoints'] = breakpoints
        if restart_id is not None:body['restartId']=restart_id
        if args is not None:body['args']=args
        if kwargs is not None:body['kwargs']=kwargs
        return self._request('POST', '/runs/' + self._id(run_id) + '/debugger', body)

    def get_run(self, run_id):
        return self._request('GET', '/runs/' + self._id(run_id))

    def runs(self, project_id, *, after=0, limit=100):
        return self._request('GET', '/runs', query={'projectId': project_id, 'after': after, 'limit': limit})

    def cancel(self, run_id):
        return self._request('POST', '/runs/' + self._id(run_id) + '/cancel', {})['accepted']

    def guide(self, run_id, input, message_id=None, *, target=None):
        body = {'input': input}
        if message_id is not None: body['messageId'] = message_id
        if target is not None: body['target'] = target
        return self._request('POST', '/runs/' + self._id(run_id) + '/guidance', body)

    def guidance_scopes(self, run_id):
        return self._request('GET', '/runs/' + self._id(run_id) + '/guidance')

    def wait(self, run_id, *, timeout=60):
        deadline = timeout_deadline(timeout)
        def get(id):
            remaining = self.timeout if deadline is None else max(.001, deadline - time.monotonic())
            try:
                return self._request('GET', '/runs/' + self._id(id), timeout=remaining)
            except TransportError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError('Waiting timed out; the run was not cancelled') from None
                raise
        return wait_for(get, run_id, timeout, .05)

    def events(self, project_id, *, after=0, limit=100, run_id=None, process_key=None):
        if run_id is not None and process_key is not None:
            raise ValueError('runId and processKey are mutually exclusive')
        query={'projectId':project_id,'after':after,'limit':limit,'runId':run_id}
        if process_key is not None:query['processKey']=process_key
        return self._request('GET','/events',query=query)

    def watch(self, project_id, *, run_id=None, process_key=None, after=0, timeout=60, stop_event=None):
        if run_id is not None and process_key is not None:
            raise ValueError('runId and processKey are mutually exclusive')
        deadline = timeout_deadline(timeout)
        client = self
        class BoundedTransport:
            def request(self, path, query=None):
                if stop_event is not None and stop_event.is_set():return None
                remaining = client.timeout if deadline is None else max(.001, deadline - time.monotonic())
                if stop_event is not None:remaining=min(remaining,1.0)
                try:
                    return client._request('GET', path, query=query, timeout=remaining)
                except TransportError:
                    if stop_event is not None and stop_event.is_set():return None
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError('Event watch timed out; the run was not cancelled') from None
                    raise
            def events(self, project_id, after=0, run_id=None, process_key=None):
                query={'projectId':project_id,'after':after,'runId':run_id}
                if process_key is not None:query['processKey']=process_key
                return self.request('/events',query)
            def get_run(self, id):
                return self.request('/runs/' + client._id(id))
            def get_process(self, project_id, key):
                return self.request('/projects/'+client._id(project_id)+'/processes/'+client._id(key))
        return watch_events(BoundedTransport(),project_id,run_id,after,timeout,process_key=process_key,stop_event=stop_event)

    def watch_process(self, project_id, key, *, after=0, timeout=60, stop_event=None):
        return self.watch(project_id,process_key=key,after=after,timeout=timeout,stop_event=stop_event)
