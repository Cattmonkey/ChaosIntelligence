"""Project mode persistence on the existing Store connection and run lifecycle."""
import hashlib
import json
import re
import threading
import time
import uuid

from .extensions import json_copy
from .language import LanguageError
from .modules import project_language_versions

_EVENT_RUN_UNSET = object()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('Mode key must be a 1–128 character identifier')
    return value


def _revision(value):
    if type(value) is not int or value < 1:
        raise ValueError('Mode revision must be a positive integer')
    return value


def _conflict(message):
    from .store import Conflict
    raise Conflict(message)


def _missing():
    from .store import NotFound
    raise NotFound('Mode artifact not found')


def _definition(value):
    from .modes import validate_definition
    return validate_definition(json_copy(value, max_chars=100000))


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mode_artifacts (
        project_id TEXT, key TEXT, kind TEXT, revision INTEGER, status TEXT,
        definition TEXT, checkpoint TEXT, source_hash TEXT, run_id TEXT, lease TEXT,
        initial_input TEXT, created REAL, updated REAL, PRIMARY KEY(project_id,key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS mode_revisions (
        project_id TEXT, key TEXT, revision INTEGER, document TEXT,
        PRIMARY KEY(project_id,key,revision))''')


class ModeStore:
    """No connection, worker or lock of its own; all writes use Store.write."""
    def __init__(self, store):
        self.store = store

    @staticmethod
    def document(row):
        return {'key':row['key'], 'kind':row['kind'], 'revision':row['revision'],
                'status':row['status'], 'definition':json.loads(row['definition']),
                'checkpoint':json.loads(row['checkpoint']), 'sourceHash':row['source_hash'],
                'runId':row['run_id'], 'created':row['created'], 'updated':row['updated']}

    def _row(self, project_id, key):
        return self.store.db.execute('SELECT * FROM mode_artifacts WHERE project_id=? AND key=?', (project_id,key)).fetchone()

    def _write(self, project_id, key, kind, status, definition, checkpoint=None,
               source_hash=None, run_id=None, lease=None, initial_input=None, event='mode.saved',
               event_run_id=_EVENT_RUN_UNSET):
        previous = self._row(project_id,key)
        now = time.time(); revision = previous['revision']+1 if previous else 1
        created = previous['created'] if previous else now
        self.store.db.execute('INSERT OR REPLACE INTO mode_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (project_id,key,kind,revision,status,_dump(definition),_dump(checkpoint),source_hash,run_id,lease,_dump(initial_input),created,now))
        artifact = self.document(self._row(project_id,key))
        self.store.db.execute('INSERT INTO mode_revisions VALUES(?,?,?,?)', (project_id,key,revision,_dump(artifact)))
        payload = {'key':key,'kind':kind,'revision':revision,'status':status}
        if kind == 'goal' and checkpoint is not None:
            payload.update(iteration=checkpoint['iteration'], phase=checkpoint['phase'])
        self.store._event(project_id,event,payload,run_id if event_run_id is _EVENT_RUN_UNSET else event_run_id)
        return artifact

    def _rewrite(self, row, *, status=None, checkpoint=None, lease=None, event, run_id=None):
        return self._write(row['project_id'],row['key'],row['kind'],status or row['status'],
            json.loads(row['definition']),json.loads(row['checkpoint']) if checkpoint is None else checkpoint,
            row['source_hash'],row['run_id'] if run_id is None else run_id,lease,
            json.loads(row['initial_input']),event)

    def get(self, project_id, key, revision=None):
        _key(key)
        if revision is not None:_revision(revision)
        with self.store.lock:
            self.store.project(project_id)
            if revision is None:
                row = self._row(project_id,key)
                if row is None:_missing()
                return self.document(row)
            row = self.store.db.execute('SELECT document FROM mode_revisions WHERE project_id=? AND key=? AND revision=?', (project_id,key,revision)).fetchone()
            if row is None:_missing()
            return json.loads(row['document'])

    def list(self, project_id, kind=None, after='', limit=100):
        if kind is not None and kind not in ('plan','goal','skill'):raise ValueError('Unknown mode kind')
        if not isinstance(after,str) or type(limit) is not int or not 1 <= limit <= 300:raise ValueError('Invalid mode cursor/limit')
        with self.store.lock:
            self.store.project(project_id)
            sql = 'SELECT * FROM mode_artifacts WHERE project_id=? AND key>?'; args=[project_id,after]
            if kind is not None:sql+=' AND kind=?';args.append(kind)
            rows=self.store.db.execute(sql+' ORDER BY key LIMIT ?',(*args,limit+1)).fetchall()
            items=[self.document(row) for row in rows[:limit]]
            return {'items':items,'nextCursor':items[-1]['key'] if len(rows)>limit else None}

    def history(self, project_id, key, after=0, limit=100):
        _key(key)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 300:raise ValueError('Invalid revision cursor/limit')
        with self.store.lock:
            self.get(project_id,key)
            rows=self.store.db.execute('SELECT document FROM mode_revisions WHERE project_id=? AND key=? AND revision>? ORDER BY revision LIMIT ?', (project_id,key,after,limit+1)).fetchall()
            items=[json.loads(row['document']) for row in rows[:limit]]
            return {'items':items,'nextCursor':items[-1]['revision'] if len(rows)>limit else None}

    def source(self, project_id, key, revision=None, format=None, name=None):
        from .modes import definition_hash, mode_source_definition, skill_source_definition
        artifact = self.get(project_id,key,revision)
        if artifact['kind']=='skill' and artifact['status']!='ready':
            raise ValueError('Only a ready Skill can export source')
        if format is None:format='function' if artifact['kind']=='skill' else 'value'
        if format not in ('value','function'):raise ValueError('Unknown mode source format')
        if format=='function':
            if artifact['kind']!='skill':raise ValueError('Function source requires a Skill')
            source=skill_source_definition(artifact['definition'],'recorded' if name is None else name)
        else:
            source=mode_source_definition(artifact['definition'],'saved_mode' if name is None else name)
        return {'source':source,'key':artifact['key'],'revision':artifact['revision'],
                'definitionHash':definition_hash(artifact['definition']),'format':format}

    def save(self, project_id, key, definition, revision=None, run_id=None):
        _key(key);definition=_definition(definition)
        if revision is not None:_revision(revision)
        with self.store.write():
            self.store.project(project_id);row=self._row(project_id,key)
            if row is None:
                if revision is not None:_conflict('Mode does not exist at expected revision')
                return self._write(project_id,key,definition['kind'],'ready',definition,run_id=run_id)
            if revision != row['revision']:_conflict('Mode revision changed')
            if row['lease'] is not None or row['status']=='recording':_conflict('Mode is currently in use')
            if definition['kind'] != row['kind']:raise ValueError('Mode kind cannot change')
            checkpoint=json.loads(row['checkpoint'])
            if row['kind']=='skill' and checkpoint is not None and _dump(definition)!=row['definition']:
                _conflict('A captured Skill definition is fixed; save an authored copy under a new key')
            if row['kind']=='goal' and checkpoint is not None and _dump(definition)!=row['definition']:
                _conflict('A pursued goal definition is fixed; use a new key')
            if row['kind']=='skill' and checkpoint is not None:
                # A no-op definition save is a new revision, not a new recording.
                return self._write(project_id,key,row['kind'],row['status'],definition,checkpoint,
                    row['source_hash'],row['run_id'],None,json.loads(row['initial_input']),event_run_id=run_id)
            return self._write(project_id,key,row['kind'],row['status'],definition,checkpoint,row['source_hash'],run_id or row['run_id'],None,json.loads(row['initial_input']))

    def reconcile(self, project_id, key, revision, state, evidence=None):
        _key(key);_revision(revision);state=json_copy(state,max_chars=100000);evidence=json_copy(evidence,max_chars=100000)
        with self.store.write():
            self.store.project(project_id);row=self._row(project_id,key)
            if row is None:_missing()
            if row['revision']!=revision:_conflict('Mode revision changed')
            if row['kind']!='goal' or row['status']!='needs_reconciliation' or row['lease'] is not None:raise ValueError('Goal does not need reconciliation')
            checkpoint=json.loads(row['checkpoint']);definition=json.loads(row['definition'])
            exhausted=checkpoint['iteration']>=definition['max_iterations']
            checkpoint.update(state=state,evidence=evidence,phase='exhausted' if exhausted else 'ready')
            return self._rewrite(row,status='exhausted' if exhausted else 'ready',checkpoint=checkpoint,event='goal.reconciled')

    def recover_in_transaction(self):
        for row in self.store.db.execute("SELECT * FROM mode_artifacts WHERE lease IS NOT NULL OR status='recording'").fetchall():
            if row['kind']=='goal':
                checkpoint=json.loads(row['checkpoint'])
                status='needs_reconciliation' if checkpoint['phase']=='pending' else 'ready'
                self._rewrite(row,status=status,event='goal.interrupted')
            else:
                checkpoint=json.loads(row['checkpoint'])
                for receipt in checkpoint.get('receipts',[]):
                    if receipt['status']=='started':receipt['status']='interrupted'
                checkpoint.update(pending=[],complete=False)
                self._rewrite(row,status='incomplete',checkpoint=checkpoint,event='skill.recording.interrupted')


class ModeHost:
    """One shared host per run; Context injects scope and enforces capabilities."""
    def __init__(self, store, project, run_id, redact=lambda value:value):
        self.store,self.repository,self.project,self.run_id=store,store.modes,project,run_id
        self.redact=redact;self.lock=threading.RLock();self.scopes={};self.claims={};self.closed=False
        bundle={'files':project['files'],'entry':project['entry'],'sourceVersions':project_language_versions(project)}
        self.source_hash=hashlib.sha256(_dump(bundle).encode()).hexdigest()

    def clean(self, value):
        return json_copy(self.redact(json_copy(value,max_chars=100000)),max_chars=100000)

    @staticmethod
    def _fields(payload, required, optional=()):
        if type(payload) is not dict or set(payload)-set(required)-set(optional) or not set(required)<=set(payload):raise ValueError('Invalid mode operation fields')

    def handle(self, operation, payload):
        with self.lock:
            if self.closed:raise LanguageError('Mode host is closed')
            payload=json_copy(payload,max_chars=100000)
            if operation=='artifact.save':
                self._fields(payload,('key','definition'),('revision',))
                return self.repository.save(self.project['id'],payload['key'],self.clean(payload['definition']),payload.get('revision'),self.run_id)
            if operation=='artifact.load':
                self._fields(payload,('key',),('revision',));return self._load(payload)
            if operation=='goal.claim':return self._claim(payload)
            if operation=='goal.commit':return self._commit(payload)
            if operation=='goal.release':
                self._fields(payload,('key','lease','outcome'));return self._release(payload['key'],payload['lease'],payload['outcome'])
            if operation=='recording.begin':
                self._fields(payload,());return self._begin()
            if operation=='recording.finish':
                self._fields(payload,('scopeId','parameters'));return self._finish(payload['scopeId'],payload['parameters'])
            if operation=='recording.abort':
                self._fields(payload,('scopeId',),('reason',));self._abort(payload['scopeId']);return {'ok':True}
            raise ValueError('Unknown mode operation')

    def _load(self, payload):
        from .modes import definition_hash
        # Resolving latest and persisting its evidence share one transaction.
        # SDK inspection remains a pure get; only this run-owned path emits it.
        with self.store.write():
            artifact=self.repository.get(self.project['id'],payload['key'],payload.get('revision'))
            if artifact['kind']=='skill' and artifact['status']!='ready':
                raise LanguageError('Only a ready Skill can be loaded',code='mode_error')
            origin={'projectId':self.project['id'],'key':artifact['key'],
                    'revision':artifact['revision'],'definitionHash':definition_hash(artifact['definition'])}
            self.store._event(self.project['id'],'mode.loaded',
                              {**origin,'kind':artifact['kind'],'sourceHash':self.source_hash},self.run_id)
            return {**artifact,'origin':origin}

    def _claim(self, payload):
        self._fields(payload,('key','definition','input'));key=_key(payload['key']);definition=_definition(self.clean(payload['definition']));value=self.clean(payload['input'])
        if definition['kind']!='goal':raise ValueError('Goal definition required')
        with self.store.write():
            self.store.project(self.project['id']);row=self.repository._row(self.project['id'],key)
            checkpoint={'iteration':0,'state':value,'results':{},'history':[],'phase':'ready','evidence':None}
            if row is not None:
                if row['kind']!='goal' or row['definition']!=_dump(definition):_conflict('Goal definition changed')
                saved=json.loads(row['checkpoint'])
                if saved is not None:
                    if row['source_hash']!=self.source_hash or row['initial_input']!=_dump(value):_conflict('Goal source bundle or initial input changed')
                    checkpoint=saved
                if row['lease'] is not None:_conflict('Goal already has an active run')
                if row['status']=='needs_reconciliation':_conflict('Goal has an interrupted pending iteration; explicit reconciliation required')
                if row['status'] in ('completed','exhausted'):
                    return {**self.repository.document(row),'lease':None}
            lease=uuid.uuid4().hex
            artifact=self.repository._write(self.project['id'],key,'goal','running',definition,checkpoint,self.source_hash,self.run_id,lease,value,'goal.claimed')
        self.claims[key]=lease
        return {**artifact,'lease':lease}

    def _commit(self, payload):
        self._fields(payload,('key','lease','revision','checkpoint'));key=_key(payload['key']);_revision(payload['revision']);checkpoint=self.clean(payload['checkpoint'])
        fields={'iteration','state','results','history','phase','evidence'}
        if type(checkpoint) is not dict or set(checkpoint)!=fields or type(checkpoint['iteration']) is not int or checkpoint['iteration']<0 or type(checkpoint['results']) is not dict or type(checkpoint['history']) is not list or len(checkpoint['history'])>100 or checkpoint['phase'] not in ('ready','pending','done','exhausted'):raise ValueError('Invalid goal checkpoint')
        with self.store.write():
            row=self.repository._row(self.project['id'],key)
            if row is None:_missing()
            if row['kind']!='goal' or not row['lease'] or row['lease']!=payload['lease'] or row['run_id']!=self.run_id or self.claims.get(key)!=payload['lease']:_conflict('Goal lease does not belong to this run')
            if row['revision']!=payload['revision']:_conflict('Goal revision changed')
            old=json.loads(row['checkpoint']);definition=json.loads(row['definition']);phase=checkpoint['phase'];iteration=checkpoint['iteration']
            if old['phase']=='ready':
                valid=phase=='pending' and iteration==old['iteration']+1 and iteration<=definition['max_iterations']
            elif old['phase']=='pending':
                valid=phase in ('ready','done','exhausted') and iteration==old['iteration']
                if phase=='ready' and iteration>=definition['max_iterations']:valid=False
                if phase=='exhausted' and iteration<definition['max_iterations']:valid=False
            else:valid=False
            if not valid:raise ValueError('Invalid goal checkpoint transition or iteration budget')
            status={'done':'completed','exhausted':'exhausted'}.get(phase,'running');lease=payload['lease'] if status=='running' else None
            artifact=self.repository._rewrite(row,status=status,checkpoint=checkpoint,lease=lease,event='goal.checkpoint')
        if lease is None:self.claims.pop(key,None)
        return {**artifact,'lease':lease}

    def _release(self, key, lease, outcome):
        _key(key)
        if not isinstance(outcome,str):raise ValueError('Release outcome must be text')
        with self.store.write():
            row=self.repository._row(self.project['id'],key)
            if row is not None and row['kind']=='goal' and row['lease'] and row['lease']==lease and row['run_id']==self.run_id and self.claims.get(key)==lease:
                checkpoint=json.loads(row['checkpoint'])
                status='needs_reconciliation' if checkpoint['phase']=='pending' else 'ready'
                self.repository._rewrite(row,status=status,event='goal.released')
        if self.claims.get(key)==lease:self.claims.pop(key,None)
        return {'ok':True}

    def _begin(self):
        scope=uuid.uuid4().hex;key='recording_'+scope
        self.scopes[scope]={'key':key,'actions':[],'receipts':[],'pending':{},'unsafe':{},'status':'recording'}
        self._persist_scope(scope,'skill.recording.started')
        return {'scopeId':scope}

    def _scope(self, scope):
        if not isinstance(scope,str) or scope not in self.scopes:raise ValueError('Recording scope does not belong to this run')
        return self.scopes[scope]

    def _persist_scope(self, scope, event, definition=None):
        state=self._scope(scope)
        definition=definition or {'kind':'skill','actions':state['actions'],'parameters':[]}
        checkpoint={'receipts':state['receipts'],'pending':list(state['pending']), 'complete':state['status']=='ready'}
        oversized=False
        try:
            definition=json_copy(definition,max_chars=100000)
            checkpoint=json_copy(checkpoint,max_chars=100000)
        except ValueError:
            oversized=True;state['status']='incomplete'
            definition={'kind':'skill','actions':[],'parameters':[]}
            checkpoint={'receipts':[{'operationId':row['operationId'],'tool':row['tool'],'status':row['status']} for row in state['receipts']],
                        'pending':list(state['pending']),'complete':False,'limitReached':True}
        with self.store.write():
            self.store.project(self.project['id'])
            artifact=self.repository._write(self.project['id'],state['key'],'skill',state['status'],definition,checkpoint,self.source_hash,self.run_id,None,None,event)
        if oversized:raise LanguageError('Recording exceeds persistent JSON budget; incomplete evidence retained')
        return artifact

    def tool_begin(self, scope, name, value, tool_version='1'):
        with self.lock:
            if self.closed:raise LanguageError('Mode host is closed')
            state=self._scope(scope)
            if state['status']!='recording':raise LanguageError('Recording scope is not active')
            if state['pending']:
                state['status']='incomplete';self._persist_scope(scope,'skill.recording.failed')
                raise LanguageError('Overlapping recorded tool operations are not supported')
            if len(state['receipts'])>=128:
                state['status']='incomplete';self._persist_scope(scope,'skill.recording.failed');raise LanguageError('Recorded skill exceeds 128 actions')
            operation=uuid.uuid4().hex
            try:original=json_copy(value,max_chars=100000);clean=self.clean(original)
            except BaseException:
                state['status']='incomplete';self._persist_scope(scope,'skill.recording.failed');raise
            row={'operationId':operation,'tool':name,'toolVersion':str(tool_version),'input':clean,'status':'started'}
            state['receipts'].append(row);state['pending'][operation]={'row':row,'original':original}
            self._persist_scope(scope,'skill.recording.step')
            return (scope,operation)

    @staticmethod
    def _changed_paths(original, clean, path=()):
        if type(original) is dict and type(clean) is dict and original.keys()==clean.keys():
            return [p for key in original for p in ModeHost._changed_paths(original[key],clean[key],path+(key,))]
        if type(original) is list and type(clean) is list and len(original)==len(clean):
            return [p for i in range(len(original)) for p in ModeHost._changed_paths(original[i],clean[i],path+(i,))]
        return [] if _dump(original)==_dump(clean) else [path]

    def tool_finish(self, token, result, status='completed', replay_input=None):
        with self.lock:
            if not isinstance(token,tuple) or len(token)!=2:raise ValueError('Invalid recording operation token')
            scope,operation=token;state=self._scope(scope);pending=state['pending'].pop(operation,None)
            if pending is None:
                if status!='completed' and any(row['operationId']==operation for row in state['receipts']):return
                raise ValueError('Recording operation already finished or unknown')
            if status not in ('completed','failed','cancelled','interrupted'):raise ValueError('Invalid recorded tool status')
            row=pending['row'];row['status']=status
            try:row['result']=self.clean(result)
            except BaseException:
                row['status']='failed';state['status']='incomplete';self._persist_scope(scope,'skill.recording.failed');raise
            if status=='completed':
                original=pending['original'] if replay_input is None else json_copy(replay_input,max_chars=100000)
                clean=self.clean(original);index=len(state['actions'])
                state['actions'].append({'tool':row['tool'],'input':clean})
                state['unsafe'][index]=self._changed_paths(original,clean)
            else:state['status']='incomplete'
            self._persist_scope(scope,'skill.recording.step')

    def _finish(self, scope, parameters):
        state=self._scope(scope)
        if state['status']!='recording' or state['pending'] or len(state['actions'])!=len(state['receipts']) or any(row['status']!='completed' for row in state['receipts']):raise LanguageError('Recording is incomplete or still has active operations')
        definition=_definition({'kind':'skill','actions':state['actions'],'parameters':parameters})
        for step,paths in state['unsafe'].items():
            bindings=[tuple(p['path']) for p in definition['parameters'] if p['step']==step]
            if any(not any(path[:len(bound)]==bound for bound in bindings) for path in paths):raise LanguageError('Redacted recorded inputs require explicit parameter bindings')
        state['status']='ready';artifact=self._persist_scope(scope,'skill.recording.finished',definition)
        return {'definition':definition,'artifact':artifact}

    def _abort(self, scope):
        state=self._scope(scope)
        if state['status']=='ready':return
        for pending in state['pending'].values():pending['row']['status']='interrupted'
        state['pending'].clear();state['status']='incomplete';self._persist_scope(scope,'skill.recording.failed')

    def close(self, outcome='interrupted'):
        with self.lock:
            if self.closed:return
            failure=None
            for key,lease in list(self.claims.items()):
                try:self._release(key,lease,outcome)
                except BaseException as error:
                    if failure is None:failure=error
            for scope,state in self.scopes.items():
                if state['status']=='recording' or state['pending']:
                    try:self._abort(scope)
                    except BaseException as error:
                        if failure is None:failure=error
            self.closed=True
            if failure is not None:raise failure
