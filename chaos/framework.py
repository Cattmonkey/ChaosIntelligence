"""Public in-process SDK: own a runtime or embed it in another application."""
import copy
import math
from pathlib import Path
import time
from .extensions import DEFAULT_CAPABILITIES, TERMINAL_STATES, json_copy
from .graph import validate
from .language import Evaluator, Parser
from .runtime import Engine
from .store import Store
from .workflow import Workflow
from .native_code_cache import DEFAULT_NATIVE_CODE_CACHE


class RunFailed(RuntimeError):
    def __init__(self, run):
        self.run = run
        super().__init__(run.get('error') or 'Run ended with status ' + run['status'])


def timeout_deadline(timeout):
    if timeout is None:
        return None
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError('timeout must be finite, nonnegative or None')
    return time.monotonic() + timeout


def wait_for(get_run, run_id, timeout=60, poll_interval=.02):
    deadline = timeout_deadline(timeout)
    if type(poll_interval) not in (int, float) or not math.isfinite(poll_interval) or not .001 <= poll_interval <= 5:
        raise ValueError('poll_interval must be between .001 and 5 seconds')
    while True:
        run = get_run(run_id)
        if run['status'] in TERMINAL_STATES:
            return run
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError('Waiting timed out; the run was not cancelled')
        time.sleep(poll_interval if remaining is None else min(poll_interval, remaining))


class RunHandle:
    def __init__(self, backend, run):
        self.backend, self.id, self.project_id = backend, run['id'], run['project_id']

    def get(self):
        return self.backend.get_run(self.id)

    def wait(self, timeout=60):
        return self.backend.wait(self.id, timeout=timeout)

    def result(self, timeout=60):
        run = self.wait(timeout)
        if run['status'] != 'completed':
            raise RunFailed(run)
        return run['output']

    def cancel(self):
        return self.backend.cancel(self.id)

    def guide(self, input, message_id=None, *, target=None):
        extra = {} if target is None else {'target': target}
        return self.backend.guide(self.id, input, message_id=message_id, **extra)

    def guidance_scopes(self):
        return self.backend.guidance_scopes(self.id)

    def debugger(self):
        return self.backend.debugger(self.id)

    def debug_command(self, command, *, pause_id=None, breakpoints=None, restart_id=None, args=None, kwargs=None):
        extra={}
        if restart_id is not None:extra['restart_id']=restart_id
        if args is not None:extra['args']=args
        if kwargs is not None:extra['kwargs']=kwargs
        return self.backend.debug_command(self.id, command, pause_id=pause_id, breakpoints=breakpoints,**extra)

    def watch(self, *, after=0, timeout=60):
        return self.backend.watch(self.project_id, run_id=self.id, after=after, timeout=timeout)


def watch_events(backend, project_id, run_id=None, after=0, timeout=60, poll_interval=.05, *, process_key=None, stop_event=None):
    if run_id is not None and process_key is not None:
        raise ValueError('runId and processKey are mutually exclusive')
    deadline = timeout_deadline(timeout)
    selector={'run_id':run_id}
    if process_key is not None:selector['process_key']=process_key
    def observer_stopped():
        return stop_event is not None and stop_event.is_set()
    while True:
        if observer_stopped():return
        page = backend.events(project_id, after=after, **selector)
        if observer_stopped():return
        for event in page['items']:
            if observer_stopped():return
            after = event['id']
            yield event
            if run_id and event['type'] in {'run.' + s for s in TERMINAL_STATES}:
                return
        if page['nextCursor'] is not None:
            # A continuously populated Process must still honor observer timeout.
            if process_key is not None and deadline is not None and time.monotonic()>=deadline:
                raise TimeoutError('Event watch timed out; the Process was not cancelled')
            continue
        run=backend.get_run(run_id) if run_id else None
        if observer_stopped():return
        if run is not None and run['status'] in TERMINAL_STATES:
            # Terminal state and its event commit together; drain after observing it.
            tail = backend.events(project_id, after=after, **selector)
            if observer_stopped():return
            if not tail['items']:
                return
            continue
        if process_key is not None:
            record=backend.get_process(project_id,process_key)
            if observer_stopped():return
            if record['status']=='completed' and record['pending'] is None:
                # Process lifecycle commits before run terminal, in one transaction.
                # Read again after observing drainable state; never stop at an event
                # name, resumable stop, control flag, first turn, or uncertain turn.
                tail=backend.events(project_id,after=after,**selector)
                if observer_stopped():return
                if not tail['items']:return
                if deadline is not None and time.monotonic()>=deadline:
                    raise TimeoutError('Event watch timed out; the Process was not cancelled')
                continue
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            target='Process' if process_key is not None else 'run'
            raise TimeoutError('Event watch timed out; the '+target+' was not cancelled')
        delay=poll_interval if remaining is None else min(poll_interval,remaining)
        if stop_event is None:time.sleep(delay)
        elif stop_event.wait(delay):return


class Framework:
    """One runtime per database. Custom callbacks run as trusted host code."""
    def __init__(self, database=':memory:', *, registry=None, trace_handler=None, settings=None, native_code_cache=DEFAULT_NATIVE_CODE_CACHE):
        if str(database) != ':memory:':
            Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            database = Path(database).expanduser().resolve()
        self._store = Store(database)
        self._closed = False
        try:
            self._engine = Engine(self._store, registry=registry, trace_handler=trace_handler, settings=settings, native_code_cache=native_code_cache)
        except BaseException:
            self._store.db.close()
            raise

    def _open(self):
        if self._closed or self._engine.closing:
            raise RuntimeError('Framework is closing or closed')

    def close(self, timeout=5):
        if not self._closed:
            timeout_deadline(timeout)
            self._engine.close(timeout)
            self._store.close()
            self._closed = True

    def __enter__(self):
        self._open()
        return self

    def __exit__(self, *args):
        self.close()

    def capabilities(self):
        self._open()
        from .extensions import capability_inventory
        return {'apiVersion': 'v1', 'tools': capability_inventory(self._engine.tools),
                'devices': {name: spec.describe() for name, spec in self._engine.device_specs.items()},
                'robots': {name: spec.describe() for name, spec in self._engine.robot_specs.items()},
                'computers': {name: spec.describe() for name, spec in self._engine.computer_specs.items()},
                'providers': {'local': {'description': 'Deterministic demonstration'}, 'model': {'description': 'Configured chat-completions endpoint'},
                              **{name: {'description': spec.description, 'version': spec.version} for name, spec in self._engine.custom_providers.items()}},
                'limits': {'maxActiveRuns': 4, 'maxParallelNodes': 4, 'runSteps': 100000, 'runSeconds': 60},
                'debugger': {'sourceBreakpoints': True, 'generatedSources':True, 'stepInto': True, 'stepOver': True, 'stepOut': True, 'expressionEvaluation': False, 'pauseTimeout': 300, 'restartResumable': False, 'conditionRecovery':True, 'pauseOnConditionDefault':False},
                'extensionModel': 'trusted-in-process'}

    def runtime_view(self, project_id, *, run_id=None):
        """Bounded read-only source/event projection; never evaluates source."""
        self._open()
        from .runtime_view import snapshot
        return snapshot(self._store,project_id,run_id=run_id)

    @staticmethod
    def ui_schema():
        """Execution-free shared OpenUI component and positional-prop schema."""
        from .ui_artifacts import catalog, LIBRARY_HASH, LEGACY_LIBRARIES
        schema = catalog()
        return {'catalog': schema, 'libraryJSONSchema': schema, 'libraryHash': LIBRARY_HASH, 'compatibleLibraries':{key:sorted(names) for key,names in LEGACY_LIBRARIES.items()}}

    @staticmethod
    def ui_compile(source, *, kind='page', title=''):
        from .ui_artifacts import compile_ui
        return compile_ui(source, kind=kind, title=title)

    @staticmethod
    def ui_export(definition, *, format='html'):
        from .ui_artifacts import export_ui
        content = export_ui(definition, format=format)
        return {'format': format, 'content': content, 'hash': definition['hash']}

    @staticmethod
    def ui_validate(definition, *, requirements=None):
        from .ui_artifacts import validate_ui
        return validate_ui(definition, requirements=requirements)

    def create_project(self, id, *, name=None, graph=None, source=None, capabilities=None, files=None, entry='main.chaos'):
        self._open()
        from_view='graph' if graph is not None and source is None else 'source'
        source='input' if source is None else source
        if isinstance(graph, Workflow):
            graph = graph.build(complete=False)
        graph = Workflow.identity().build() if graph is None else graph
        granted = list(DEFAULT_CAPABILITIES) if capabilities is None else capabilities
        return self._store.create_project(id, name or id, graph, source, granted, files, entry, from_view)

    def projects(self, *, after='', limit=100):
        self._open()
        return self._store.projects(after, limit)

    def get_project(self, id):
        self._open()
        return self._store.project(id)

    def validate_agent(self, definition, *, name='saved_agent'):
        self._open()
        from .agent_values import validate_definition as validate_agent_definition, definition_hash, source_definition
        definition = validate_agent_definition(json_copy(definition, max_chars=100000))
        return {'definition': definition, 'definitionHash': definition_hash(definition),
                'source': source_definition(definition, name)}

    def agent_schema(self):
        self._open()
        from .agent_values import configuration_schema
        return json_copy(configuration_schema())

    def validate_team(self, definition, *, name='saved_team'):
        self._open()
        from .team_values import validate_definition, definition_hash, source_definition
        definition = validate_definition(json_copy(definition, max_chars=100000))
        return {'definition': definition, 'definitionHash': definition_hash(definition),
                'source': source_definition(definition, name)}

    def team_schema(self):
        self._open()
        from .team_values import configuration_schema
        return json_copy(configuration_schema())

    def validate_process(self, definition, *, name='saved_process'):
        self._open()
        from .process_values import validate_definition, definition_hash, source_definition
        definition=validate_definition(definition)
        return {'definition':definition,'definitionHash':definition_hash(definition),'source':source_definition(definition,name)}

    def process_schema(self):
        self._open()
        from .process_values import configuration_schema
        return json_copy(configuration_schema())

    def create_process(self, project_id, version, key, definition, *, state=None):
        self._open()
        return self._engine.process_operation(project_id,'start',{'key':key,'definition':definition,'state':state},version=version)

    def processes(self, project_id, *, after='', limit=100):
        self._open()
        return self._store.processes.list(project_id,after,limit)

    def get_process(self, project_id, key):
        self._open()
        return self._store.processes.get(project_id,key)

    def get_process_message(self, project_id, key, message_id):
        self._open()
        return self._engine.process_operation(project_id,'message',{'key':key,'messageId':message_id})

    def send_process(self, project_id, key, value, *, message_id=None):
        self._open()
        return self._engine.process_operation(project_id,'send',{'key':key,'input':value,'messageId':message_id})

    def guide_process(self, project_id, key, value, *, message_id=None, target=None):
        self._open()
        payload = {'key':key,'input':value,'messageId':message_id}
        if target is not None: payload['target'] = target
        return self._engine.process_operation(project_id,'guide',payload)

    def process_guidance(self, project_id, key):
        self._open()
        return self._engine.process_operation(project_id,'guidance',{'key':key})

    def control_process(self, project_id, key, revision, action):
        self._open()
        return self._engine.process_operation(project_id,'control',{'key':key,'revision':revision,'action':action})

    def configure_process(self, project_id, key, revision, definition):
        self._open()
        return self._engine.process_operation(project_id,'configure',{'key':key,'revision':revision,'definition':definition})

    def reconcile_process(self, project_id, key, revision, state, *, evidence='', resume=False):
        self._open()
        return self._engine.process_operation(project_id,'reconcile',{'key':key,'revision':revision,'state':state,'evidence':evidence,'resume':resume})

    def computer_schema(self):
        self._open()
        from .computer_schema import configuration_schema
        return json_copy(configuration_schema())

    def validate_computer_action(self, spec, *, registered=False):
        self._open()
        from .computer_schema import validate_action
        return validate_action(spec,registered=registered)

    def computer_sessions(self, project_id, *, after=0, limit=50):
        self._open()
        return self._store.computers.sessions(project_id,after,limit)

    def computer_session(self, project_id, key):
        self._open()
        return self._store.computers.get_session(project_id,key)

    def computer_history(self, project_id, *, run_id=None, after=0, limit=50):
        self._open()
        return self._store.computers.history(project_id,run_id,after,limit)

    def computer_record(self, project_id, record_id):
        self._open()
        return self._store.computers.get(project_id,record_id)

    def computer_image(self, project_id, record_id, *, variant='preview'):
        self._open()
        return self._store.computers.image(project_id,record_id,variant)

    def session_schema(self):
        self._open()
        from .session_values import configuration_schema
        return json_copy(configuration_schema())

    def open_agent_session(self, project_id, version, key, agent, *, context_limit=100000):
        self._open()
        return self._engine.session_operation(project_id,'open',{'key':key,'agent':agent,'contextLimit':context_limit},version=version)

    def agent_sessions(self, project_id, *, after='', limit=100):
        self._open()
        return self._store.sessions.list(project_id,after,limit)

    def agent_session(self, project_id, key):
        self._open()
        return self._engine.session_operation(project_id,'status',{'key':key})

    def agent_session_history(self, project_id, key, *, after=0, limit=100):
        self._open()
        return self._engine.session_operation(project_id,'history',{'key':key,'after':after,'limit':limit})

    def start_session_turn(self, project_id, version, key, revision, value, *, message_id=None):
        self._open()
        return RunHandle(self,self._engine.start_session_turn(project_id,version,key,revision,value,message_id))

    def fork_agent_session(self, project_id, key, revision, new_key, *, agent=None):
        self._open()
        return self._engine.session_operation(project_id,'fork',{'key':key,'revision':revision,'newKey':new_key,'agent':agent})

    def compact_agent_session(self, project_id, key, revision, keep_turns):
        self._open()
        return self._engine.session_operation(project_id,'compact',{'key':key,'revision':revision,'keepTurns':keep_turns})

    def close_agent_session(self, project_id, key, revision):
        self._open()
        return self._engine.session_operation(project_id,'close',{'key':key,'revision':revision})

    def reconcile_agent_session(self, project_id, key, revision, evidence):
        self._open()
        return self._engine.session_operation(project_id,'reconcile',{'key':key,'revision':revision,'evidence':evidence})

    def modes(self, project_id, *, kind=None, after='', limit=100):
        self._open()
        return self._store.modes.list(project_id,kind,after,limit)

    def validate_program(self, definition, *, name='saved_program'):
        self._open()
        from .program_values import validate_definition, canonical_hash, source_definition
        from .modules import ProgramBundle
        from .language import ResourceLimit
        definition=validate_definition(definition)
        bundle=ProgramBundle(definition['files'][definition['entry']],definition['files'],definition['entry'],
                             language_versions=definition['languageVersions'])
        result={'definition':definition,'definitionHash':canonical_hash(definition),'summary':bundle.summary()}
        try:result['source']=source_definition(definition,name)
        except ResourceLimit as error:
            result.update(source=None,sourceError=str(error)[:1000])
        return result

    def program_schema(self):
        self._open()
        from .program_values import configuration_schema
        return json_copy(configuration_schema())

    def program_snapshot(self, project_id):
        self._open()
        return self._store.program_snapshot(project_id)

    def program_history(self, project_id, *, after=0, limit=50):
        self._open()
        return self._store.history.history(project_id,after,limit)

    def program_revision(self, project_id, version):
        self._open()
        return self._store.history.revision(project_id,version)

    def adopt_program(self, project_id, version, definition, *, expected_hash, cases, reason=''):
        self._open()
        return self._engine.adopt_program(project_id,version,definition,expected_hash,cases,reason)

    def mode(self, project_id, key, *, revision=None):
        self._open()
        return self._store.modes.get(project_id,key,revision)

    def validate_mode(self, definition, *, name='saved_mode'):
        self._open()
        from .modes import validate_definition, definition_hash, mode_source_definition
        definition=validate_definition(json_copy(definition,max_chars=100000))
        return {'definition':definition,'definitionHash':definition_hash(definition),
                'source':mode_source_definition(definition,name)}

    def mode_source(self, project_id, key, *, revision=None, format=None, name=None):
        self._open()
        return self._store.modes.source(project_id,key,revision,format,name)

    def save_mode(self, project_id, key, definition, *, revision=None):
        self._open()
        return self._store.modes.save(project_id,key,self._engine.settings.redact(definition),revision)

    def mode_history(self, project_id, key, *, after=0, limit=100):
        self._open()
        return self._store.modes.history(project_id,key,after,limit)

    def reconcile_goal(self, project_id, key, revision, state, *, evidence=None):
        self._open()
        return self._store.modes.reconcile(project_id,key,revision,self._engine.settings.redact(state),self._engine.settings.redact(evidence))

    def save_project(self, project, *, from_view=None):
        self._open()
        required = {'id', 'version', 'graph', 'source', 'name'}
        if not isinstance(project, dict) or not required.issubset(project):
            raise ValueError('Project requires id, version, graph, source and name')
        return self._store.save(project['id'], project['version'], project['graph'], project['source'], project['name'], project.get('capabilities'), project.get('files'), project.get('entry'), from_view or project.get('editFrom'),source_versions=project.get('sourceVersions'))

    def edit_program(self, project_id, version, *, source=None, program=None, operations=None, file=None, capabilities=None):
        self._open()
        return self._store.edit_program(project_id,version,source=source,program=program,operations=operations,file=file,capabilities=capabilities)

    def edit_files(self, project_id, version, operation, path, *, new_path=None, source=None, directory=False):
        self._open()
        from .ide_files import edit_project_files
        body={'version':version,'operation':operation,'path':path,'directory':directory}
        if new_path is not None:body['newPath']=new_path
        if source is not None:body['source']=source
        return edit_project_files(self,project_id,body)

    @staticmethod
    def analyze(source, *, file='main.chaos', files=None, entry='main.chaos', position=None, language_versions=None):
        from .ide_language import analyze
        return analyze(source,file=file,files=files,entry=entry,position=position,language_versions=language_versions)

    @staticmethod
    def compile_objects(source, files=None, entry='main.chaos', *, language_versions=None, libraries=None, macro_interfaces=False):
        """Compile local source with optional validated source-free libraries."""
        from .executable_image import compile_objects
        return compile_objects(source, files, entry, language_versions=language_versions, libraries=libraries, macro_interfaces=macro_interfaces)

    @staticmethod
    def link_objects(objects, entry='main.chaos'):
        """Validate and link module objects without evaluating their code."""
        from .executable_image import link_objects
        return link_objects(objects, entry=entry)

    @staticmethod
    def mount_objects(objects, prefix):
        """Relocate a validated compiled library without source or execution."""
        from .executable_image import mount_objects
        return mount_objects(objects, prefix)

    @staticmethod
    def compile_executable(source, files=None, entry='main.chaos', *, language_versions=None, libraries=None, macro_interfaces=False):
        from .executable_image import compile_executable
        return compile_executable(source, files, entry, language_versions=language_versions, libraries=libraries, macro_interfaces=macro_interfaces)

    @staticmethod
    def load_executable(data):
        """Load fresh executable IR without parsing source or expanding macros."""
        from .executable_image import load_executable
        return load_executable(data)

    @staticmethod
    def compile_artifact(source, files=None, entry='main.chaos', *, language_versions=None):
        """Build closed portable templates without running module effects."""
        from .compilation_artifact import compile_artifact
        return compile_artifact(source, files, entry, language_versions=language_versions)

    @staticmethod
    def load_artifact(data):
        """Revalidate source/macros/templates and return a fresh ProgramBundle."""
        from .compilation_artifact import load_artifact
        return load_artifact(data)

    @staticmethod
    def validate_artifact(data):
        """Return the original inert bundle summary; no execution or permission."""
        return Framework.load_artifact(data).summary()

    @staticmethod
    def compile(source, *, files=None, entry='main.chaos', language_versions=None):
        from .compiler import compile_program
        return compile_program(source, files=files, entry=entry, language_versions=language_versions)

    @staticmethod
    def references(source, *, file='main.chaos', files=None, entry='main.chaos', position, language_versions=None, include_declaration=True):
        from .symbols import references
        return references(source,file=file,files=files,entry=entry,position=position,language_versions=language_versions,include_declaration=include_declaration)

    @staticmethod
    def rename(source, *, file='main.chaos', files=None, entry='main.chaos', position, new_name, language_versions=None):
        from .symbols import rename
        return rename(source,file=file,files=files,entry=entry,position=position,new_name=new_name,language_versions=language_versions)

    @staticmethod
    def format_source(source, *, file='main.chaos', language_version=2, line_width=88, indent_size=2):
        from .ide_language import format_source
        return format_source(source,file,language_version=language_version,line_width=line_width,indent_size=indent_size)

    def snapshot(self, project_id='workspace'):
        self._open()
        return self._store.snapshot(project_id)

    def program(self, project_id, *, file=None):
        self._open()
        from .program import project_program
        version,ir=self._store.program(project_id,file)
        return {'version':version,'program':ir,'projection':project_program(ir)}

    def patch(self, project_id, version, operations, *, actor=None, reason='SDK edit'):
        self._open()
        return self._store.patch(project_id, version, operations, actor, reason)

    def optimize(self, project_id, version):
        self._open()
        return self._engine.optimize(project_id, version)

    def validate(self, project_id, *, complete=True):
        self._open()
        return self._engine.validate_project(self._store.project(project_id), complete)

    def start(self, project_id, version, value, *, mode='graph', auto_optimize=False, trace=False, debug=None):
        self._open()
        return RunHandle(self, self._engine.start(project_id, version, value, mode, auto_optimize, trace, debug))

    def start_executable(self, project_id, version, image, value=None, *, trace=False):
        """Start an immutable executable in the project's existing authority context."""
        self._open()
        return RunHandle(self, self._engine.start_executable(project_id, version, image, value, trace=trace))

    def start_debug(self, project_id, version, value=None, *, breakpoints=None, pause_on_entry=True, pause_timeout=300, pause_on_condition=False):
        debug={'breakpoints': [] if breakpoints is None else breakpoints,
               'pauseOnEntry': pause_on_entry, 'pauseTimeout': pause_timeout}
        # Default calls retain the v1 wire shape accepted by older servers.
        # Non-booleans must still reach the ordinary strict options validator.
        if pause_on_condition is not False:debug['pauseOnCondition']=pause_on_condition
        return self.start(project_id, version, value, mode='source', auto_optimize=False,debug=debug)

    def debugger(self, run_id):
        self._open()
        return self._engine.debugger(run_id)

    def debug_command(self, run_id, command, *, pause_id=None, breakpoints=None, restart_id=None, args=None, kwargs=None):
        self._open()
        return self._engine.debug_command(run_id, command, pause_id=pause_id, breakpoints=breakpoints,restart_id=restart_id,args=args,kwargs=kwargs)

    def get_run(self, run_id):
        self._open()
        return self._store.run(run_id)

    def runs(self, project_id, *, after=0, limit=100):
        self._open()
        return self._store.run_page(project_id, after, limit)

    def cancel(self, run_id):
        self._open()
        return self._engine.cancel(run_id)

    def guide(self, run_id, input, message_id=None, *, target=None):
        self._open()
        extra = {} if target is None else {'target': target}
        return self._engine.guide(run_id, input, message_id=message_id, **extra)

    def guidance_scopes(self, run_id):
        self._open()
        return self._engine.guidance_scopes(run_id)

    def wait(self, run_id, *, timeout=60):
        self._open()
        return wait_for(self.get_run, run_id, timeout)

    def events(self, project_id, *, after=0, limit=100, run_id=None, process_key=None):
        self._open()
        return self._store.event_page(project_id,after,limit,run_id,process_key=process_key)

    def watch(self, project_id, *, run_id=None, process_key=None, after=0, timeout=60, stop_event=None):
        self._open()
        return watch_events(self,project_id,run_id,after,timeout,process_key=process_key,stop_event=stop_event)

    def watch_process(self, project_id, key, *, after=0, timeout=60, stop_event=None):
        return self.watch(project_id,process_key=key,after=after,timeout=timeout,stop_event=stop_event)

    @staticmethod
    def evaluate(source, bindings=None, *, step_limit=30000, timeout=20, files=None, entry='main.chaos', trace_handler=None, optimize_literals=True, native_code_cache=DEFAULT_NATIVE_CODE_CACHE):
        """Pure, bounded functional evaluation, without starting a stored run."""
        value = Evaluator(step_limit=step_limit, timeout=timeout,trace_handler=trace_handler,optimize_literals=optimize_literals,native_code_cache=native_code_cache).run(source, bindings,files=files,entry=entry)
        from .language import serializable
        return serializable(value)
