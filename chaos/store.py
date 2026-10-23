import copy
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from .examples import DEFAULT_SOURCE, initial_graph
from .extensions import DEFAULT_CAPABILITIES, json_copy, valid_name
from .graph import validate, apply_patch
from .modules import ProgramBundle, bundle_files, project_language_versions, updated_language_versions
from .ownership import RuntimeLease
from .mode_store import ModeStore, initialize as initialize_modes
from .process_store import ProcessStore, initialize as initialize_processes
from .session_store import SessionStore, initialize as initialize_sessions
from .computer_store import ComputerStore, initialize as initialize_computers
from .program_history_store import ProgramHistoryStore, initialize as initialize_program_history
from .program import parse_program, workflow_source, extract_workflow, format_program, patch_program


MAX_MEMORY_REVISION = 9007199254740991


class Conflict(ValueError):
    pass


class NotFound(KeyError):
    pass


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def version_number(value):
    if type(value) is not int or value < 1:
        raise ValueError('version must be a positive integer')
    return value


def page_bounds(after, limit):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 300:
        raise ValueError('after must be nonnegative; limit must be 1–300')


def capabilities_value(value):
    if not isinstance(value, (list, tuple)) or len(value) > 128:
        raise ValueError('capabilities must contain at most 128 tool names')
    for name in value:
        valid_name(name)
        if name == 'graph.patch':
            raise ValueError('graph.patch is reserved for platform transactions')
    if len(set(value)) != len(value):
        raise ValueError('Duplicate capability')
    return sorted(value)


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        self.runtime_owned = False
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        migration_lease=None
        try:
            table=self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()
            columns={r['name']for r in self.db.execute('PRAGMA table_info(projects)')}if table else set()
            mode_tables=self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('mode_artifacts','mode_revisions')").fetchone()[0]
            process_tables=self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('processes','process_messages','process_run_lineage','process_event_lineage','process_role_messages')").fetchone()[0]
            message_columns={row[1] for row in self.db.execute('PRAGMA table_info(process_messages)')}
            message_links_missing=not {'run_id','claim_event_id','reconciled_event_id'}.issubset(message_columns)
            session_tables=self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('agent_sessions','session_turns','session_messages','session_tool_bindings')").fetchone()[0]
            computer_tables=self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('computer_records','computer_sessions')").fetchone()[0]
            history_tables=self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='program_revisions'").fetchone()[0]
            memory_version_tables=self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='memory_revisions'").fetchone()[0]
            history_missing=history_tables!=1 or not table or bool(self.db.execute('SELECT 1 FROM projects p LEFT JOIN program_revisions h ON p.id=h.project_id AND p.version=h.version WHERE h.version IS NULL LIMIT 1').fetchone())
            needs_migration=message_links_missing or memory_version_tables!=1 or history_missing or computer_tables!=2 or session_tables!=4 or process_tables!=5 or 'program'not in columns or bool(self.db.execute('SELECT 1 FROM projects WHERE program IS NULL LIMIT 1').fetchone()) or mode_tables!=2
            if needs_migration:migration_lease=RuntimeLease(self.path)
            self.history=ProgramHistoryStore(self)
            self._initialize()
            self.modes=ModeStore(self)
            self.processes=ProcessStore(self)
            self.sessions=SessionStore(self)
            self.computers=ComputerStore(self)
        except Exception:
            self.db.close()
            raise
        finally:
            if migration_lease:migration_lease.close()

    def _initialize(self):
        with self.lock:
            self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT, version INTEGER, graph TEXT, source TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, mode TEXT, status TEXT, snapshot TEXT, input TEXT, output TEXT, error TEXT, created REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, run_id TEXT, type TEXT, payload TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS events_project ON events(project_id,id);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id,id);
            CREATE TABLE IF NOT EXISTS module_programs(project_id TEXT, file TEXT, source TEXT, program TEXT, PRIMARY KEY(project_id,file));
            CREATE TABLE IF NOT EXISTS memory(project_id TEXT, key TEXT, value TEXT, PRIMARY KEY(project_id,key));
            CREATE TABLE IF NOT EXISTS memory_revisions(project_id TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision BETWEEN 1 AND 9007199254740991), PRIMARY KEY(project_id,key));
            ''')
            initialize_modes(self.db)
            initialize_processes(self.db)
            initialize_sessions(self.db)
            initialize_computers(self.db)
            initialize_program_history(self.db)
        with self.write():
            existing_ids={row['id']for row in self.db.execute('SELECT id FROM projects')}
            columns = {r['name'] for r in self.db.execute('PRAGMA table_info(projects)')}
            if 'capabilities' not in columns:
                self.db.execute('ALTER TABLE projects ADD COLUMN capabilities TEXT')
            self.db.execute('UPDATE projects SET capabilities=? WHERE capabilities IS NULL', (dump(DEFAULT_CAPABILITIES),))
            self.db.execute('INSERT OR IGNORE INTO projects(id,name,version,graph,source,capabilities) VALUES(?,?,?,?,?,?)',
                            ('workspace', '多智能体研究工作流', 1, dump(initial_graph()), DEFAULT_SOURCE, dump(DEFAULT_CAPABILITIES)))
            for column, sqltype in [('files','TEXT'),('entry','TEXT'),('program','TEXT'),('authoring','TEXT'),('legacy_source','TEXT')]:
                if column not in columns: self.db.execute('ALTER TABLE projects ADD COLUMN '+column+' '+sqltype)
            for row in self.db.execute('SELECT * FROM projects WHERE program IS NULL').fetchall():
                graph=json.loads(row['graph']); previous=row['source']
                state=self.program_state(graph,previous,{},'main.chaos','graph')
                self.db.execute('UPDATE projects SET graph=?,source=?,files=?,entry=?,program=?,authoring=?,legacy_source=?,version=? WHERE id=?',
                                (dump(state['graph']),state['source'],dump(state['files']),state['entry'],dump(state['program']),state['authoring'],previous,row['version']+int(row['id']in existing_ids),row['id']))
                if row['id']in existing_ids:self._event(row['id'],'program.migrated',{'previousVersion':row['version'],'version':row['version']+1,'reason':'统一源码、工作流与程序 IR；旧源码保留在 legacySource'})
            for row in self.db.execute('SELECT p.* FROM projects p LEFT JOIN program_revisions h ON p.id=h.project_id AND p.version=h.version WHERE h.version IS NULL').fetchall():
                self.history.capture(self.project_row(row),'migration'if row['id']in existing_ids else'created',
                    reason='History begins at the existing current version'if row['id']in existing_ids else'Project created')


    @contextmanager
    def write(self):
        # BEGIN IMMEDIATE makes read/validate/write CAS atomic across connections.
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self.lock:
            if self.runtime_owned:
                raise RuntimeError('Close the runtime before closing its Store')
            self.db.close()

    @staticmethod
    def document(graph, source, name):
        graph = json_copy(graph, max_chars=750000)
        validate(graph)
        if not isinstance(source, str) or len(source) > 100000 or not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise ValueError('Project name/source exceeds limits')
        return graph

    @staticmethod
    def project_row(row):
        result=dict(row)
        for field in ('graph','capabilities','files','program'):result[field]=json.loads(result[field])
        from .program import normalize_numbers
        result['program']=normalize_numbers(result['program'])
        result['legacySource']=result.pop('legacy_source') or ''
        return result

    @staticmethod
    def program_state(graph, source, files, entry, from_view, source_versions=None, *, checkpoint=None):
        if from_view not in ('source','graph'):raise ValueError('editFrom must be source or graph')
        if from_view=='graph':source=workflow_source(graph,files=files,entry=entry)
        return Store._source_state(graph,source,files,entry,source_versions,checkpoint=checkpoint)

    @staticmethod
    def _source_state(graph, source, files, entry, source_versions=None, *, checkpoint=None):
        """Compile already prepared text once, retaining the supplied positions."""
        bundle=ProgramBundle(source,files,entry,language_versions=source_versions,checkpoint=checkpoint)
        program=parse_program(source,bundle.entry,language_version=bundle.language_versions[bundle.entry])
        program['sourceVersions']=bundle.language_versions
        workflow=extract_workflow(source)
        if workflow is None:
            graph={'nodes':[],'edges':[]};authoring='source'
        else:
            positions={n['id']:(n.get('x'),n.get('y'))for n in graph['nodes']}
            graph=workflow
            for i,node in enumerate(graph['nodes']):
                x,y=positions.get(node['id'],(None,None))
                node['x']=x if x is not None else 70+(i%4)*290
                node['y']=y if y is not None else 160+(i//4)*190
            authoring='workflow'
        return {'graph':graph,'source':source,'files':bundle.files,'entry':bundle.entry,'program':program,'authoring':authoring}


    def project(self, id='workspace'):
        with self.lock:
            row = self.db.execute('SELECT * FROM projects WHERE id=?', (id,)).fetchone()
            if not row:
                raise NotFound('Project not found')
            return self.project_row(row)

    def snapshot(self, project_id='workspace'):
        # A SQLite transaction, not just the per-connection lock, fixes one read point.
        with self.lock:
            self.db.execute('BEGIN')
            try:
                project=self.project(project_id)
                cursor=self.db.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE project_id=?',(project_id,)).fetchone()[0]
                runs=self.runs(project_id)
                modes=self.modes.list(project_id)
                return {'project':project,'eventCursor':cursor,'runs':runs,'modes':modes,'processes':self.processes.list(project_id)['items']}
            finally:self.db.rollback()

    def projects(self, after='', limit=100):
        if not isinstance(after, str) or type(limit) is not int or not 1 <= limit <= 300:
            raise ValueError('Invalid projects cursor/limit')
        with self.lock:
            rows = self.db.execute('SELECT * FROM projects WHERE id>? ORDER BY id LIMIT ?', (after, limit + 1)).fetchall()
            items = [self.project_row(row) for row in rows[:limit]]
            return {'items': items, 'nextCursor': items[-1]['id'] if len(rows) > limit else None}

    def create_project(self, id, name, graph, source='input', capabilities=DEFAULT_CAPABILITIES, files=None, entry='main.chaos', from_view='source'):
        if not isinstance(id, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', id):
            raise ValueError('Project id must be an ASCII identifier of 1–64 characters')
        graph = self.document(graph, source, name)
        capabilities = capabilities_value(capabilities)
        state = self.program_state(graph,source,files,entry,from_view)
        with self.write():
            if self.db.execute('SELECT 1 FROM projects WHERE id=?', (id,)).fetchone():
                raise Conflict('Project already exists')
            self.db.execute('INSERT INTO projects(id,name,version,graph,source,capabilities,files,entry,program,authoring,legacy_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (id,name,1,dump(state['graph']),state['source'],dump(capabilities),dump(state['files']),state['entry'],dump(state['program']),state['authoring'],''))
            self.history.capture(self.project(id),'created',reason='Project created')
            self._event(id, 'project.created', {'version': 1, 'actor': 'user'})
            return self.project(id)

    def _event(self, project_id, kind, payload, run_id=None):
        cursor = self.db.execute('INSERT INTO events(project_id,run_id,type,payload,created) VALUES(?,?,?,?,?)', (project_id, run_id, kind, dump(payload), time.time()))
        return cursor.lastrowid

    def event(self, project_id, kind, payload, run_id=None):
        with self.write():
            self.project(project_id)
            return self._event(project_id, kind, payload, run_id)

    def save(self, project_id, version, graph, source, name, capabilities=None, files=None, entry=None, from_view=None, program=None, program_file=None, source_versions=None, *, _adoption=None, _checkpoint=None):
        version_number(version)
        if from_view=='source':graph=self.project(project_id)['graph']
        graph = self.document(graph, source, name)
        if capabilities is not None:
            capabilities = capabilities_value(capabilities)
        with self.write():
            current = self.project(project_id)
            if current['version'] != version:
                raise Conflict('Project version changed; reload before saving')
            if _adoption is not None:
                if 'program.adopt' not in current['capabilities']:
                    raise ValueError('Project does not grant program.adopt')
                if self.program_snapshot(project_id)['definitionHash'] != _adoption['previousDefinitionHash']:
                    raise Conflict('Program source hash changed; reload before adopting')
                if _adoption['definitionHash'] == _adoption['previousDefinitionHash']:
                    raise ValueError('Program adoption requires a changed definition')
                # Adoption edits source only; even trusted callers cannot use it
                # to replace the project's name or permission envelope.
                name = current['name']; capabilities = None
            granted = current['capabilities'] if capabilities is None else capabilities
            if from_view is None:
                graph_changed=dump(graph)!=dump(current['graph']);source_changed=source!=current['source']
                if graph_changed and source_changed:
                    raise ValueError('同时修改图和源码必须明确 editFrom')
                from_view='graph' if graph_changed else 'source'
            if from_view not in ('source','graph'):raise ValueError('editFrom must be source or graph')
            candidate_source=workflow_source(graph,previous_source=current['source'] if current['authoring']=='workflow' else None,files=current['files'] if files is None else files,entry=entry or current['entry']) if from_view=='graph' else source
            candidate_files,candidate_entry=bundle_files(candidate_source,current['files'] if files is None else files,entry or current['entry'])
            versions=updated_language_versions(current,candidate_files,source_versions)
            state=self._source_state(graph,candidate_source,candidate_files,candidate_entry,versions,checkpoint=_checkpoint)
            if (state['entry']==current['entry'] and state['source']==current['source']
                    and versions[state['entry']]==project_language_versions(current).get(current['entry'])):
                state['program']=current['program']
            state['program']['sourceVersions']=versions
            state['program']['languageVersion']=versions[state['entry']]
            if program is not None:
                from .program import plain, decode_program, digest
                target=program_file or state['entry']
                if program.get('file')!=target:raise ValueError('Program file differs from edited module')
                expected=state['program']if target==state['entry']else parse_program(state['files'][target],target,language_version=versions[target])
                if digest(plain(decode_program(program)))!=digest(plain(decode_program(expected))):raise ValueError('IR differs from source')
                canonical=patch_program(program,[])
                canonical['languageVersion']=versions[target]
                canonical.pop('sourceVersions',None)
                if target==state['entry']:canonical['sourceVersions']=versions
                if target==state['entry']:state['program']=canonical
                else:self.db.execute('INSERT OR REPLACE INTO module_programs VALUES(?,?,?,?)',(project_id,target,state['files'][target],dump(canonical)))
            return self._write_program_state_in_transaction(project_id,version,state,name,granted,_adoption,_checkpoint)

    def _write_program_state_in_transaction(self,project_id,version,state,name,granted,adoption=None,checkpoint=None):
        """Persist precompiled source inside the caller's existing transaction."""
        self.db.execute('DELETE FROM module_programs WHERE project_id=? AND file NOT IN ('+','.join('?' for _ in state['files'])+')',(project_id,*state['files']))
        if checkpoint is not None:checkpoint()
        self.db.execute('UPDATE projects SET name=?,version=?,graph=?,source=?,capabilities=?,files=?,entry=?,program=?,authoring=? WHERE id=? AND version=?',
                        (name,version+1,dump(state['graph']),state['source'],dump(granted),dump(state['files']),state['entry'],dump(state['program']),state['authoring'],project_id,version))
        self.history.capture(self.project(project_id),'adopted' if adoption is not None else 'saved',
            run_id=adoption['runId'] if adoption is not None else None,reason=adoption['reason'] if adoption is not None else '保存工作区')
        self._event(project_id,'graph.saved',{'version':version+1,'actor':'program' if adoption is not None else 'user','reason':'采纳已验证程序' if adoption is not None else '保存工作区'})
        if adoption is not None:self._event(project_id,'program.adopted',adoption,adoption['runId'])
        return self.project(project_id)

    def _evolve_process_in_transaction(self,claim,evolution):
        from .process_values import _EvolutionCommit
        from .program_values import canonical_hash, cases_hash
        if type(evolution) is not _EvolutionCommit or evolution.decision.claim is not claim:
            raise ValueError('Process evolution requires its private prepared claim')
        if time.monotonic()>=evolution.deadline:
            from .language import ResourceLimit
            raise ResourceLimit('Process evolution deadline expired before commit')
        decision=evolution.decision;payload=decision.payload;receipt=evolution.receipt
        current=self.project(claim['project']['id']);pending=claim['pending'];base=payload['base']
        row=self.processes._read(current['id'],claim['key'])
        if (row['pending'] is None or row['pending']['lease']!=pending['lease']
                or row['pending']['runId']!=pending['runId']):
            raise Conflict('Process pending claim changed before evolution')
        if row['definitionHash']!=pending['processDefinitionHash']:
            raise Conflict('Process definition changed before evolution')
        expected={'projectId':current['id'],'version':pending['version'],'definitionHash':pending['definitionHash']}
        if base!=expected or current['version']!=base['version'] or self.program_snapshot(current['id'])['definitionHash']!=base['definitionHash']:
            raise Conflict('Program source changed before Process evolution')
        allowed=set(decision.capabilities)&set(pending['effectiveCapabilities'])&set(current['capabilities'])
        if not {'process.manage','program.adopt'}<=allowed:
            raise ValueError('Process evolution authority was revoked')
        definition=payload['definition']
        if (receipt['definitionHash']!=canonical_hash(definition) or receipt['casesHash']!=cases_hash(payload['cases'])
                or receipt['previousDefinitionHash']!=base['definitionHash'] or receipt['previousVersion']!=base['version']
                or receipt['version']!=base['version']+1 or receipt['runId']!=pending['runId']
                or receipt['testCount']!=len(payload['cases']) or receipt['definitionHash']==base['definitionHash']):
            raise ValueError('Invalid prepared Process evolution receipt')
        self._write_program_state_in_transaction(current['id'],current['version'],evolution.state,current['name'],current['capabilities'],receipt)
        return receipt

    def program_snapshot(self, project_id):
        from .program_values import canonical_hash
        with self.lock:
            project = self.project(project_id)
            definition = {'kind':'program','entry':project['entry'],'files':project['files'],
                          'languageVersions':project_language_versions(project)}
            return {'definition':definition,'definitionHash':canonical_hash(definition),'version':project['version']}

    def adopt_program(self, project_id, version, definition, expected_hash, report, reason='', run_id=None, checkpoint=None):
        from .program_values import canonical_hash
        current = self.project(project_id)
        receipt = {'previousVersion':version,'version':version+1,'previousDefinitionHash':expected_hash,
                   'definitionHash':canonical_hash(definition),'casesHash':report['casesHash'],
                   'testCount':len(report['checks']),'runId':run_id,'reason':reason}
        project = self.save(project_id,version,current['graph'],definition['files'][definition['entry']],current['name'],
                            files=definition['files'],entry=definition['entry'],from_view='source',
                            source_versions=definition['languageVersions'],_adoption=receipt,_checkpoint=checkpoint)
        return {'project':project,'receipt':receipt}

    def patch(self, project_id, version, operations, actor, reason):
        version_number(version)
        if actor is not None and not isinstance(actor, str):
            raise ValueError('actor must be a node id or null')
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError('reason must be at most 1000 characters')
        operations = json_copy(operations, max_chars=750000)
        with self.write():
            current = self.project(project_id)
            if current['version'] != version:
                raise Conflict('Platform patch version conflict')
            if current['authoring']!='workflow':raise ValueError('当前程序不是工作流；请使用程序结构编辑')
            graph = apply_patch(current['graph'], operations, actor)
            validate(graph, complete=True)
            graph = self.document(graph, current['source'], current['name'])
            candidate_source=workflow_source(graph,previous_source=current['source'] if current['authoring']=='workflow' else None,files=current['files'],entry=current['entry'])
            candidate_files,_=bundle_files(candidate_source,current['files'],current['entry'])
            state=self._source_state(graph,candidate_source,candidate_files,current['entry'],updated_language_versions(current,candidate_files))
            if state['source']==current['source']:state['program']=current['program']
            self.db.execute('UPDATE projects SET graph=?,source=?,files=?,program=?,version=? WHERE id=?',
                            (dump(state['graph']),state['source'],dump(state['files']),dump(state['program']),version+1,project_id))
            self.history.capture(self.project(project_id),'patched',reason=reason)
            self._event(project_id, 'graph.revised', {'version': version + 1, 'previousVersion': version, 'actor': actor or 'user', 'reason': reason, 'operations': operations})
            return self.project(project_id)

    def program(self, project_id, file=None):
        with self.lock:
            current=self.project(project_id);file=file or current['entry']
            if file not in current['files']:raise ValueError('Module does not exist')
            if file==current['entry']:ir=current['program']
            else:
                row=self.db.execute('SELECT source,program FROM module_programs WHERE project_id=? AND file=?',(project_id,file)).fetchone()
                cached=json.loads(row['program']) if row and row['source']==current['files'][file] else None
                language_version=project_language_versions(current)[file]
                ir=cached if cached is not None and cached.get('languageVersion',1)==language_version else parse_program(current['files'][file],file,language_version=language_version)
            from .program import normalize_numbers
            return current['version'],normalize_numbers(ir)

    def edit_program(self, project_id, version, *, source=None, program=None, operations=None, file=None, capabilities=None):
        current=self.project(project_id)
        version_number(version)
        if current['version']!=version:raise Conflict('Program version changed')
        file=file or current['entry']
        if file not in current['files']:raise ValueError('模块文件不存在')
        if sum(v is not None for v in (source,program,operations))!=1:raise ValueError('Specify exactly one of source, program or operations')
        if operations is not None:program=patch_program(self.program(project_id,file)[1],operations)
        if program is not None:
            source=format_program(program)
        files=dict(current['files']);files[file]=source
        return self.save(project_id,version,current['graph'],files[current['entry']],current['name'],current['capabilities'] if capabilities is None else capabilities,files,current['entry'],'source',program,file)

    def create_run(self, id, project, mode, value, *, _check_authority=False):
        with self.write():
            if _check_authority:
                # This read and INSERT share BEGIN IMMEDIATE, including callers
                # that save a new capability version through another connection.
                current = self.project(project['id'])
                if (current['version'] != project['version'] or
                        current['capabilities'] != project['capabilities']):
                    raise Conflict('Executable run authority changed before admission')
            now = time.time()
            self.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)', (id, project['id'], project['version'], mode, 'queued', dump(project), dump(value), None, None, now, now))
            provenance = ({'executionKind': 'executable', 'imageHash': project['execution']['imageHash']}
                          if mode == 'executable' else {})
            self._event(project['id'], 'run.queued', {'version': project['version'], 'mode': mode, **provenance}, id)

    def transition_run(self, id, status, payload, output=None, error=None, *, _process=None, _evolution=None):
        with self.write():
            row = self.db.execute('SELECT project_id FROM runs WHERE id=?', (id,)).fetchone()
            if not row:
                raise NotFound('Run not found')
            receipt=None
            if _evolution is not None:
                if _process is None or status!='completed':raise ValueError('Evolution requires a successful Process turn')
                receipt=self._evolve_process_in_transaction(_process,_evolution)
            if _process is not None and status in ('completed','failed','cancelled','interrupted'):
                self.processes.finish_in_transaction(_process,status,output,error,evolution=receipt)
            self.db.execute('UPDATE runs SET status=?,output=?,error=?,updated=? WHERE id=?', (status, dump(output), error, time.time(), id))
            self._event(row['project_id'], 'run.' + status, payload, id)
            if _evolution is not None and time.monotonic()>=_evolution.deadline:
                from .language import ResourceLimit
                raise ResourceLimit('Process evolution deadline expired before commit')

    def run(self, id):
        with self.lock:
            row = self.db.execute('SELECT * FROM runs WHERE id=?', (id,)).fetchone()
            if not row:
                raise NotFound('Run not found')
            result = dict(row)
            for key in ('snapshot', 'input', 'output'):
                result[key] = json.loads(result[key]) if result[key] is not None else None
            return result

    def runs(self, project_id):
        with self.lock:
            self.project(project_id)
            return [dict(row) for row in self.db.execute('SELECT id,version,mode,status,error,created,updated FROM runs WHERE project_id=? ORDER BY created DESC LIMIT 30', (project_id,))]

    def run_page(self, project_id, after=0, limit=100):
        page_bounds(after, limit)
        with self.lock:
            self.project(project_id)
            rows = self.db.execute('SELECT rowid AS cursor,id,project_id,version,mode,status,error,created,updated FROM runs WHERE project_id=? AND rowid>? ORDER BY rowid LIMIT ?', (project_id, after, limit + 1)).fetchall()
            items = [dict(row) for row in rows[:limit]]
            return {'items': items, 'nextCursor': items[-1]['cursor'] if len(rows) > limit else None}

    def events(self, project_id, after=0, limit=300, run_id=None, *, process_key=None):
        page_bounds(after, limit)
        if run_id is not None and process_key is not None:
            raise ValueError('runId and processKey are mutually exclusive')
        with self.lock:
            self.project(project_id)
            if run_id is not None and self.run(run_id)['project_id'] != project_id:
                raise NotFound('Run does not belong to project')
            if process_key is not None:
                self.processes._read(project_id,process_key)
                sql = """SELECT e.*,
                    CASE WHEN pe.event_id IS NOT NULL THEN 'lifecycle' ELSE 'run' END AS processOrigin
                    FROM events e
                    LEFT JOIN process_event_lineage pe
                      ON pe.event_id=e.id AND pe.project_id=e.project_id AND pe.key=?
                    LEFT JOIN process_run_lineage pr
                      ON pr.run_id=e.run_id AND pr.project_id=e.project_id AND pr.key=?
                    WHERE e.project_id=? AND e.id>?
                      AND (pe.event_id IS NOT NULL OR pr.run_id IS NOT NULL)"""
                args = [process_key,process_key,project_id,after]
            else:
                sql = 'SELECT e.* FROM events e WHERE e.project_id=? AND e.id>?'
                args = [project_id, after]
            if run_id is not None:
                sql += ' AND e.run_id=?'; args.append(run_id)
            sql += ' ORDER BY e.id LIMIT ?'; args.append(limit)
            rows = self.db.execute(sql, args).fetchall()
            return [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]

    def event_page(self, project_id, after=0, limit=100, run_id=None, *, process_key=None):
        with self.lock:
            items=self.events(project_id,after,limit,run_id,process_key=process_key)
            page={'items':items,'nextCursor':items[-1]['id'] if len(items)==limit else None,
                  'lastEventId':items[-1]['id'] if items else after}
            if process_key is not None:
                first=self.events(project_id,0,1,process_key=process_key)
                page.update(historyStartEventId=first[0]['id'] if first else None,
                            legacyHistoryIncluded=False)
            return page

    def memory_get(self, project_id, key):
        with self.lock:
            self.project(project_id)
            row = self.db.execute('SELECT value FROM memory WHERE project_id=? AND key=?', (project_id, key)).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def _memory_key(key):
        if type(key) is not str or not 1 <= len(key) <= 128:
            raise ValueError('Memory key must be a String of 1–128 characters')
        return key

    def _memory_cell(self, project_id, key):
        # One SQLite statement gives a coherent value/version snapshot even
        # while another connection commits. Existing pre-version rows are v0.
        row = self.db.execute(
            'SELECT m.value, COALESCE(r.revision,0) AS revision FROM memory m '
            'LEFT JOIN memory_revisions r ON r.project_id=m.project_id AND r.key=m.key '
            'WHERE m.project_id=? AND m.key=?', (project_id,key)).fetchone()
        return {'key':key,'exists':row is not None,
                'value':json.loads(row['value']) if row is not None else None,
                'revision':row['revision'] if row is not None else 0}

    def memory_read(self, project_id, key):
        self._memory_key(key)
        with self.lock:
            self.project(project_id)
            return self._memory_cell(project_id,key)

    def memory_compare_exchange(self, project_id, key, expected_revision, value):
        self._memory_key(key)
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_MEMORY_REVISION:
            raise ValueError('Memory expected revision must be an Int from 0 to 9007199254740991')
        value = json_copy(value,max_chars=100000)
        encoded = dump(value)
        if len(encoded)>100000:
            raise ValueError('Serialized memory value exceeds 100000 characters')
        with self.write():
            self.project(project_id)
            cell = self._memory_cell(project_id,key)
            if cell['revision'] != expected_revision:
                return {**cell,'swapped':False}
            if cell['revision'] >= MAX_MEMORY_REVISION:
                raise ValueError('Memory revision limit exhausted')
            revision = cell['revision']+1
            self.db.execute('INSERT OR REPLACE INTO memory VALUES(?,?,?)',(project_id,key,encoded))
            self.db.execute('INSERT OR REPLACE INTO memory_revisions VALUES(?,?,?)',(project_id,key,revision))
            return {'key':key,'exists':True,'value':value,'revision':revision,'swapped':True}

    def memory_put(self, project_id, key, value):
        # Preserve the legacy input/return contract, including same-value writes.
        value = json_copy(value, max_chars=100000)
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError('Invalid memory key')
        encoded = dump(value)
        with self.write():
            self.project(project_id)
            row = self.db.execute('SELECT revision FROM memory_revisions WHERE project_id=? AND key=?',
                                  (project_id,key)).fetchone()
            revision = row['revision'] if row is not None else 0
            if revision >= MAX_MEMORY_REVISION:
                raise ValueError('Memory revision limit exhausted')
            self.db.execute('INSERT OR REPLACE INTO memory VALUES(?,?,?)', (project_id, key, encoded))
            self.db.execute('INSERT OR REPLACE INTO memory_revisions VALUES(?,?,?)', (project_id,key,revision+1))
        return value

    def recover(self):
        with self.write():
            interrupted = self.db.execute("SELECT id,project_id,version FROM runs WHERE status IN ('queued','running')").fetchall()
            for row in interrupted:
                self.db.execute("UPDATE runs SET status='interrupted', error='服务重启，执行已中断',updated=? WHERE id=?", (time.time(), row['id']))
                self._event(row['project_id'], 'run.interrupted', {'version': row['version'], 'error': '服务重启，执行已中断'}, row['id'])
            self.modes.recover_in_transaction()
            self.processes.recover_in_transaction()
            self.sessions.recover_in_transaction()
            self.computers.recover_in_transaction()
