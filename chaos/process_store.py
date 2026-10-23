"""Durable Process state and mailboxes on the existing project Store transaction."""
import hashlib
import json
import math
import re
import time
import uuid

from .extensions import json_copy
from .language import LanguageError
from .modes import identifier
from .modules import ProgramBundle, project_language_versions


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def data(value):
    value = json_copy(value, max_chars=40000)
    if len(dump(value)) > 40000:
        raise ValueError('Process data exceeds 40000 JSON characters')
    return value


def message_identity(value):
    if value is None: return 'message-' + uuid.uuid4().hex
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',value) is None:
        raise ValueError('Message id must be a 1–128 character identifier')
    return value


MESSAGE_LINK_COLUMNS = ('run_id', 'claim_event_id', 'reconciled_event_id')


def _payload(raw):
    if type(raw) is not str or len(raw) > 250000:
        return None
    try:
        value = json_copy(json.loads(raw), max_chars=250000)
        return value if type(value) is dict else None
    except (ValueError, TypeError, RecursionError):
        return None


def _claim_payload(value, project, key, run_id, message_id, version):
    return (type(value) is dict and value.get('key') == key and
            value.get('kind') == 'message' and value.get('messageId') == message_id and
            value.get('runId') == run_id and type(value.get('version')) is int and
            value['version'] == version and type(value.get('turn')) is int and value['turn'] > 0)


def _rows(cursor):
    """Bounded memory even on old connections without sqlite3.Row."""
    names = [column[0] for column in cursor.description]
    while True:
        batch = cursor.fetchmany(128)
        if not batch:
            return
        for row in batch:
            yield dict(zip(names, row))


def _migrate_message_links(db):
    """One atomic legacy backfill from private host lifecycle certificates.

    TEMP rows let SQLite group ambiguity without retaining history in Python.
    No new persistent table/index or copied output is created. Caller owns the
    migration savepoint and the stopped-runtime lease.
    """
    db.execute('CREATE TEMP TABLE _chaos_message_claims(seq INTEGER, run_id TEXT, event_id INTEGER, valid INTEGER)')
    db.execute('CREATE TEMP TABLE _chaos_message_reconciled(seq INTEGER, event_id INTEGER, valid INTEGER)')
    db.execute('CREATE INDEX temp._chaos_message_claims_seq ON _chaos_message_claims(seq)')
    db.execute('CREATE INDEX temp._chaos_message_reconciled_seq ON _chaos_message_reconciled(seq)')
    try:
        cursor = db.execute('''SELECT pe.project_id AS project_id,pe.key AS process_key,
            e.id,e.project_id AS event_project,e.run_id,e.payload,
            pr.project_id AS run_project,pr.key AS run_key,r.project_id AS actual_project,r.version
            FROM process_event_lineage pe JOIN events e ON e.id=pe.event_id
            LEFT JOIN process_run_lineage pr ON pr.run_id=e.run_id
            LEFT JOIN runs r ON r.id=e.run_id WHERE e.type='process.turn.queued' ''')
        for row in _rows(cursor):
            value = _payload(row['payload'])
            if value is None or type(value.get('messageId')) is not str:
                continue
            try: message_identity(value['messageId'])
            except ValueError: continue
            message = db.execute('''SELECT seq,status FROM process_messages
                WHERE project_id=? AND key=? AND message_id=?''',
                (row['project_id'],row['process_key'],value['messageId'])).fetchone()
            if message is None or message[1] not in ('queued','claimed','consumed'):
                continue
            valid = (row['event_project'] == row['run_project'] == row['actual_project'] == row['project_id']
                     and row['run_key'] == row['process_key'] and
                     _claim_payload(value,row['project_id'],row['process_key'],row['run_id'],value['messageId'],row['version']))
            db.execute('INSERT INTO _chaos_message_claims VALUES(?,?,?,?)',
                       (message[0],row['run_id'],row['id'],int(valid)))
        db.execute('''UPDATE process_messages SET
            run_id=(SELECT c.run_id FROM _chaos_message_claims c WHERE c.seq=process_messages.seq),
            claim_event_id=(SELECT c.event_id FROM _chaos_message_claims c WHERE c.seq=process_messages.seq)
            WHERE run_id IS NULL AND claim_event_id IS NULL AND reconciled_event_id IS NULL
            AND seq IN (SELECT seq FROM _chaos_message_claims GROUP BY seq HAVING count(*)=1 AND min(valid)=1)
            AND (SELECT c.run_id FROM _chaos_message_claims c WHERE c.seq=process_messages.seq)
                IN (SELECT run_id FROM _chaos_message_claims GROUP BY run_id HAVING count(*)=1 AND min(valid)=1)''')
        cursor = db.execute('''SELECT m.seq,m.project_id,m.key,m.run_id,e.id,e.payload,
            e.project_id AS event_project,pe.project_id AS certified_project,pe.key AS certified_key
            FROM _chaos_message_claims c JOIN process_messages m ON m.seq=c.seq AND m.claim_event_id=c.event_id
            JOIN events e ON e.run_id=m.run_id AND e.type='process.reconciled'
            JOIN process_event_lineage pe ON pe.event_id=e.id''')
        for row in _rows(cursor):
            value = _payload(row['payload'])
            valid = (value is not None and value.get('key') == row['key'] and
                     row['event_project'] == row['certified_project'] == row['project_id'] and
                     row['certified_key'] == row['key'] and
                     (value.get('evidence') is None or type(value.get('evidence')) is str and len(value['evidence']) <= 1000))
            db.execute('INSERT INTO _chaos_message_reconciled VALUES(?,?,?)',(row['seq'],row['id'],int(valid)))
        db.execute('''UPDATE process_messages SET reconciled_event_id=
            (SELECT c.event_id FROM _chaos_message_reconciled c WHERE c.seq=process_messages.seq)
            WHERE seq IN (SELECT seq FROM _chaos_message_reconciled GROUP BY seq HAVING count(*)=1 AND min(valid)=1)''')
        # A consumed unsuccessful run requires its actual reconciliation proof.
        # Ambiguous reconciliation is never silently projected as no reconciliation.
        db.execute('''UPDATE process_messages SET run_id=NULL,claim_event_id=NULL,reconciled_event_id=NULL
            WHERE seq IN (SELECT seq FROM _chaos_message_reconciled GROUP BY seq HAVING count(*)!=1 OR min(valid)!=1)
            OR (status='consumed' AND run_id IS NOT NULL AND reconciled_event_id IS NULL AND
                EXISTS(SELECT 1 FROM runs r WHERE r.id=process_messages.run_id AND r.status!='completed'))''')
    finally:
        db.execute('DROP TABLE _chaos_message_reconciled')
        db.execute('DROP TABLE _chaos_message_claims')


MESSAGE_OUTCOME_SQL = '''SELECT m.status AS mailbox_status,m.run_id AS certified_run,
    m.claim_event_id,m.reconciled_event_id,r.id AS actual_run,r.project_id AS actual_project,
    r.version,r.status AS run_status,r.output,r.error,r.created,r.updated,
    pr.project_id AS run_project,pr.key AS run_key,
    ce.id AS claim_id,ce.project_id AS claim_project,ce.run_id AS claim_run,ce.type AS claim_type,ce.payload AS claim_payload,
    cp.project_id AS claim_private_project,cp.key AS claim_private_key,
    re.id AS reconcile_id,re.project_id AS reconcile_project,re.run_id AS reconcile_run,re.type AS reconcile_type,
    re.payload AS reconcile_payload,re.created AS reconcile_at,rp.project_id AS reconcile_private_project,rp.key AS reconcile_private_key
    FROM process_messages m JOIN projects p ON p.id=m.project_id
    JOIN processes owner ON owner.project_id=m.project_id AND owner.key=m.key
    LEFT JOIN runs r ON r.id=m.run_id LEFT JOIN process_run_lineage pr ON pr.run_id=m.run_id
    LEFT JOIN events ce ON ce.id=m.claim_event_id LEFT JOIN process_event_lineage cp ON cp.event_id=m.claim_event_id
    LEFT JOIN events re ON re.id=m.reconciled_event_id LEFT JOIN process_event_lineage rp ON rp.event_id=m.reconciled_event_id
    WHERE m.project_id=? AND m.key=? AND m.message_id=?'''


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS processes (
        project_id TEXT, key TEXT, document TEXT, status TEXT, wake REAL,
        PRIMARY KEY(project_id,key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS process_messages (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, key TEXT,
        message_id TEXT, input TEXT, input_hash TEXT, status TEXT,
        UNIQUE(project_id,key,message_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS process_messages_fifo ON process_messages(project_id,key,status,seq)')
    # Private provenance. No payload/run-input backfill; no FK cascade against
    # processes, whose revision writes use INSERT OR REPLACE.
    db.execute('SAVEPOINT process_lineage_migration')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS process_run_lineage (
            run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, key TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS process_event_lineage (
            event_id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, key TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS process_run_lineage_process ON process_run_lineage(project_id,key,run_id)')
        db.execute('CREATE INDEX IF NOT EXISTS process_event_lineage_process ON process_event_lineage(project_id,key,event_id)')
        db.execute('''CREATE TABLE IF NOT EXISTS process_role_messages (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, key TEXT,
            role_id TEXT, message_id TEXT, revision INTEGER, input TEXT,
            input_hash TEXT, status TEXT, offered INTEGER NOT NULL DEFAULT 0,
            UNIQUE(project_id,key,role_id,message_id))''')
        db.execute('CREATE INDEX IF NOT EXISTS process_role_messages_fifo ON process_role_messages(project_id,key,offered,status,seq)')
        columns={row[1] for row in db.execute('PRAGMA table_info(process_messages)')}
        missing=[name for name in MESSAGE_LINK_COLUMNS if name not in columns]
        if missing:
            for name in missing:
                db.execute('ALTER TABLE process_messages ADD COLUMN '+name+(' TEXT' if name=='run_id' else ' INTEGER'))
            _migrate_message_links(db)
        db.execute('RELEASE SAVEPOINT process_lineage_migration')
    except BaseException:
        db.execute('ROLLBACK TO SAVEPOINT process_lineage_migration')
        db.execute('RELEASE SAVEPOINT process_lineage_migration')
        raise


class ProcessStore:
    """No connection, lock, worker or independent definition registry."""
    def __init__(self, store): self.store = store

    def _read(self, project_id, key):
        from .store import NotFound
        identifier(key, 'Process key')
        row = self.store.db.execute('SELECT document FROM processes WHERE project_id=? AND key=?', (project_id,key)).fetchone()
        if row is None: raise NotFound('Process not found')
        return json.loads(row['document'])

    @staticmethod
    def public(document):
        document = json.loads(dump(document))
        if document['pending']: document['pending'].pop('lease', None)
        return document

    def get(self, project_id, key):
        with self.store.lock:
            self.store.project(project_id)
            return self.public(self._read(project_id,key))

    def list(self, project_id, after='', limit=100):
        from .store import page_bounds
        page_bounds(0,limit)
        if type(after) is not str: raise ValueError('Invalid Process cursor')
        with self.store.lock:
            self.store.project(project_id)
            rows = self.store.db.execute('SELECT document FROM processes WHERE project_id=? AND key>? ORDER BY key LIMIT ?', (project_id,after,limit+1)).fetchall()
            items = [self.public(json.loads(row['document'])) for row in rows[:limit]]
            return {'items':items, 'nextCursor':items[-1]['key'] if len(rows)>limit else None}

    def _write(self, row, event, run_id=None, extra=None):
        return self._publish(row,event,run_id,extra)[0]

    def _publish(self, row, event, run_id=None, extra=None):
        row['revision'] += 1
        row['updated'] = time.time()
        self.store.db.execute('INSERT OR REPLACE INTO processes VALUES(?,?,?,?,?)',
            (row['projectId'],row['key'],dump(row),row['status'],row['nextWakeAt']))
        payload = {'key':row['key'],'revision':row['revision'],'status':row['status'],'turn':row['turn'],
                   'sourceVersion':row['lastVersion'],'processDefinitionHash':row['definitionHash']}
        if extra: payload.update(extra)
        event_id=self.store._event(row['projectId'],event,payload,run_id)
        self.store.db.execute('INSERT INTO process_event_lineage(event_id,project_id,key) VALUES(?,?,?)',
                              (event_id,row['projectId'],row['key']))
        return self.public(row),event_id

    def message(self, project_id, key, message_id):
        """Read one ordinary message and its certified run in one SQL snapshot."""
        from .store import Conflict, NotFound
        if type(project_id) is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}',project_id) is None:
            raise ValueError('Project id must be an ASCII identifier of 1–64 characters')
        identifier(key,'Process key')
        if type(message_id) is not str:
            raise ValueError('Message id must be a 1–128 character identifier')
        message_identity(message_id)
        with self.store.lock:
            row = self.store.db.execute(MESSAGE_OUTCOME_SQL,(project_id,key,message_id)).fetchone()
            if row is None:
                raise NotFound('Process message not found')
            status = row['mailbox_status']
            if type(status) is str and status.startswith('guide_'):
                raise NotFound('Process message not found')
            if status not in ('queued','claimed','consumed'):
                raise Conflict('Invalid ordinary Process message status')
            result = {'key':key,'messageId':message_id,'mailboxStatus':status,
                      'linkStatus':'unclaimed' if status=='queued' else 'unavailable',
                      'run':None,'reconciliation':None}
            certificate = (row['certified_run'],row['claim_event_id'],row['reconciled_event_id'])
            if all(value is None for value in certificate):
                return result
            def invalid():
                raise Conflict('Process message certificate is missing or inconsistent')
            if status == 'queued' or type(row['certified_run']) is not str or not row['certified_run'] or len(row['certified_run']) > 200:
                invalid()
            if type(row['claim_event_id']) is not int or row['claim_event_id'] < 1:
                invalid()
            if row['reconciled_event_id'] is not None and (type(row['reconciled_event_id']) is not int or row['reconciled_event_id'] < 1):
                invalid()
            if (row['actual_run'] != row['certified_run'] or row['actual_project'] != project_id or
                type(row['version']) is not int or row['version'] < 1 or
                row['run_project'] != project_id or row['run_key'] != key or
                row['claim_id'] != row['claim_event_id'] or row['claim_project'] != project_id or
                row['claim_run'] != row['certified_run'] or row['claim_type'] != 'process.turn.queued' or
                row['claim_private_project'] != project_id or row['claim_private_key'] != key or
                not _claim_payload(_payload(row['claim_payload']),project_id,key,row['certified_run'],message_id,row['version'])):
                invalid()
            terminal = ('completed','failed','cancelled','interrupted')
            if row['run_status'] not in ('queued','running') + terminal:
                invalid()
            if any(type(row[name]) not in (int,float) or not math.isfinite(row[name]) for name in ('created','updated')):
                invalid()
            if status == 'claimed' and (row['run_status'] == 'completed' or row['reconciled_event_id'] is not None):
                invalid()
            if status == 'consumed' and (row['run_status'] not in terminal or row['run_status'] != 'completed' and row['reconciled_event_id'] is None):
                invalid()
            if row['reconciled_event_id'] is not None:
                value = _payload(row['reconcile_payload'])
                if (row['run_status'] not in ('failed','cancelled','interrupted') or
                    row['reconcile_id'] != row['reconciled_event_id'] or row['reconcile_project'] != project_id or
                    row['reconcile_run'] != row['certified_run'] or row['reconcile_type'] != 'process.reconciled' or
                    row['reconcile_private_project'] != project_id or row['reconcile_private_key'] != key or
                    type(row['reconcile_at']) not in (int,float) or not math.isfinite(row['reconcile_at']) or
                    value is None or value.get('key') != key or
                    value.get('evidence') is not None and (type(value['evidence']) is not str or len(value['evidence']) > 1000)):
                    invalid()
                result['reconciliation'] = {'eventId':row['reconciled_event_id'],'runId':row['certified_run'],
                                            'evidence':value.get('evidence'),'at':row['reconcile_at']}
            try:
                output = json_copy(json.loads(row['output'])) if row['output'] is not None else None
            except (ValueError, TypeError, RecursionError):
                invalid()
            result['linkStatus'] = 'linked'
            result['run'] = {'id':row['actual_run'],'version':row['version'],'status':row['run_status'],
                             'output':output,'error':row['error'],'created':row['created'],'updated':row['updated']}
            return result

    @staticmethod
    def authority(project, allowed, row=None):
        effective = set(project['capabilities']).intersection(allowed)
        if 'process.manage' not in effective:
            raise LanguageError('Current context does not grant process.manage',code='process_error')
        if row is not None and not set(row['launchCapabilities']).intersection(project['capabilities']).issubset(effective):
            raise LanguageError('Caller authority is narrower than this Process launch ceiling',code='process_error')
        return effective

    @staticmethod
    def revision(row, expected):
        from .store import version_number, Conflict
        version_number(expected)
        if row['revision'] != expected: raise Conflict('Process revision changed; reload before editing')

    @staticmethod
    def binding(project, definition):
        from .process_values import validate_binding
        bundle=ProgramBundle(project['source'],project['files'],project['entry'],language_versions=project_language_versions(project))
        return validate_binding(bundle,definition)

    def _configure_roles(self,row,definition):
        """Explicit declaration identity survives code edits, never role removal."""
        from .store import Conflict
        previous=row.get('botRoles',{});keys=definition.get('roles',[])
        removed=[role for key,role in previous.items() if key not in keys]
        for role in removed:
            if self.store.db.execute("SELECT 1 FROM process_role_messages WHERE project_id=? AND key=? AND role_id=? AND status!='guide_consumed' LIMIT 1",
                    (row['projectId'],row['key'],role['roleId'])).fetchone():
                raise Conflict('Role with unconsumed guidance cannot be removed')
        retired=list(row.get('retiredBotRoleIds',[]))
        retired.extend(role['roleId'] for role in removed)
        if len(retired)+len(keys)>128:raise ValueError('Process role lifetime identity budget exhausted')
        roles={}
        for key in keys:
            if key in previous:roles[key]=previous[key]
            else:
                role_id='bot-role-'+uuid.uuid4().hex
                roles[key]={'roleKey':key,'roleId':role_id,'revision':0,
                            'target':{'kind':'processRole','roleId':role_id}}
        if 'roles'in definition or 'botRoles'in row:row['botRoles']=roles
        if retired:row['retiredBotRoleIds']=retired

    @staticmethod
    def _role(row,role_key=None,role_id=None):
        roles=row.get('botRoles',{})
        role=roles.get(role_key) if type(role_key)is str and role_id is None else next(
            (item for item in roles.values() if item['roleId']==role_id),None) if role_key is None and type(role_id)is str else None
        if role is None:raise LanguageError('Unknown current Process role identity',code='ownership_error')
        return role

    def role_registry(self,project_id,key):
        from .store import Conflict
        with self.store.lock:
            self.store.project(project_id);row=self._read(project_id,key);roles=[]
            try:self._guidance_open(row);addressable=True
            except (ValueError,Conflict):addressable=False
            for role in row.get('botRoles',{}).values():
                count=self.store.db.execute("SELECT count(*) FROM process_role_messages WHERE project_id=? AND key=? AND role_id=? AND status!='guide_consumed'",
                    (project_id,key,role['roleId'])).fetchone()[0]
                roles.append({**role,'pendingCount':count,'activeScopeId':None,'addressable':addressable})
            return {'projectId':project_id,'key':key,'status':row['status'],'roles':json.loads(dump(roles))}

    def role_box(self,claim,role_key=None,*,role_id=None):
        with self.store.lock:
            row=self._claimed(claim);role=self._role(row,role_key,role_id);messages=[]
            for item in self.store.db.execute("SELECT * FROM process_role_messages WHERE project_id=? AND key=? AND role_id=? AND status IN ('guide_queued','guide_received') ORDER BY seq",
                    (row['projectId'],row['key'],role['roleId'])).fetchall():
                packet=json.loads(item['input'])
                messages.append({'hash':item['input_hash'],'receipt':packet['receipt'],'input':packet['input']})
            return {'roleKey':role['roleKey'],'roleId':role['roleId'],'revision':role['revision'],'messages':messages}

    def _guidance_count(self,row):
        args=(row['projectId'],row['key'])
        ordinary=self.store.db.execute("SELECT count(*) FROM process_messages WHERE project_id=? AND key=? AND status IN ('guide_queued','guide_received','guide_adopted')",args).fetchone()[0]
        roles=self.store.db.execute("SELECT count(*) FROM process_role_messages WHERE project_id=? AND key=? AND status!='guide_consumed'",args).fetchone()[0]
        return ordinary+roles

    def _role_guide(self,row,value,digest,message_id,run_id,target):
        from .store import Conflict
        if (type(target)is not dict or set(target)!={'kind','roleId'} or target['kind']!='processRole'
                or type(target['roleId'])is not str or re.fullmatch(r'bot-role-[0-9a-f]{32}',target['roleId'])is None):
            raise ValueError('Process role guidance requires its exact canonical target')
        try:role=self._role(row,role_id=target['roleId'])
        except LanguageError:raise Conflict('Process role is unknown, retired, or belongs to another Process') from None
        args=(row['projectId'],row['key'],role['roleId'])
        previous=self.store.db.execute('SELECT * FROM process_role_messages WHERE project_id=? AND key=? AND role_id=? AND message_id=?',args+(message_id,)).fetchone()
        if previous:
            if previous['input_hash']!=digest:raise Conflict('Role message id already refers to different input')
            return json.loads(previous['input'])['receipt']
        self._guidance_open(row)
        if self._guidance_count(row)>=64:raise ValueError('Process guidance permits at most 64 uncommitted inputs')
        if self.store.db.execute('SELECT count(*) FROM process_role_messages WHERE project_id=? AND key=?',args[:2]).fetchone()[0]>=4096:
            raise ValueError('Process role guidance identity budget exhausted')
        revision=role['revision']+1
        receipt={'key':row['key'],'runId':run_id,'messageId':message_id,'revision':revision,'status':'queued','target':dict(role['target'])}
        packet={'input':value,'receipt':receipt}
        self.store.db.execute('INSERT INTO process_role_messages(project_id,key,role_id,message_id,revision,input,input_hash,status,offered) VALUES(?,?,?,?,?,?,?,?,0)',
            args+(message_id,revision,dump(packet),digest,'guide_queued'))
        role['revision']=revision
        self._write(row,'process.guidance.queued',run_id,{'messageId':message_id,'guidanceRevision':revision,
            'roleKey':role['roleKey'],'roleId':role['roleId'],'receipt':receipt})
        return receipt

    @staticmethod
    def _guidance_open(row):
        if row['status']=='completed':raise ValueError('Completed Process does not accept guidance')
        work=(row.get('pending') or {}).get('botWork')
        if work and ((work.get('scope') and work['scope'].get('terminal')) or
                     row['status']=='running' and work.get('checkpoint') and work['checkpoint']['done']):
            from .store import Conflict
            raise Conflict('Bot has committed a terminal result; guidance was not accepted')

    def create(self, project_id, version, key, definition, state, allowed):
        from .store import version_number, Conflict
        from .process_values import definition_hash
        identifier(key,'Process key'); version_number(version); state=data(state)
        with self.store.write():
            project=self.store.project(project_id)
            if project['version'] != version: raise Conflict('Project version changed before Process launch')
            ceiling=self.authority(project,allowed)
            if self.store.db.execute('SELECT 1 FROM processes WHERE project_id=? AND key=?',(project_id,key)).fetchone():
                raise Conflict('Process key already exists')
            if self.store.db.execute('SELECT count(*) FROM processes WHERE project_id=?',(project_id,)).fetchone()[0]>=32:
                raise ValueError('At most 32 retained Processes per project')
            if self.store.db.execute('SELECT count(*) FROM processes').fetchone()[0]>=256:
                raise ValueError('At most 256 retained Processes per runtime')
            definition=self.binding(project,definition)
            now=time.time()
            row={'key':key,'projectId':project_id,'revision':0,'definition':definition,'definitionHash':definition_hash(definition),
                 'status':'waiting','turn':0,'state':state,'output':None,'nextWakeAt':now,'pending':None,
                 'lastRunId':None,'lastVersion':None,'error':None,'launchCapabilities':sorted(ceiling),
                 'control':'running','started':False,'created':now,'updated':now,'reconciliation':None}
            self._configure_roles(row,definition)
            return self._write(row,'process.created')

    def send(self, project_id, key, value, message_id, allowed, redact):
        value=data(value)
        digest=hashlib.sha256(dump(value).encode()).hexdigest()
        value=data(redact(value))
        message_id=message_identity(message_id)
        with self.store.write():
            row=self._read(project_id,key); self.authority(self.store.project(project_id),allowed,row)
            previous=self.store.db.execute('SELECT * FROM process_messages WHERE project_id=? AND key=? AND message_id=?',(project_id,key,message_id)).fetchone()
            if previous:
                if previous['input_hash'] != digest or previous['status'].startswith('guide_'):
                    from .store import Conflict
                    raise Conflict('Message id already refers to different input')
                return {'key':key,'messageId':message_id,'status':previous['status']}
            if row['status']=='completed': raise ValueError('Completed Process does not accept new messages')
            if self.store.db.execute("SELECT count(*) FROM process_messages WHERE project_id=? AND key=? AND status IN ('queued','claimed')",(project_id,key)).fetchone()[0]>=256:
                raise ValueError('Process mailbox permits at most 256 pending messages')
            self.store.db.execute('INSERT INTO process_messages(project_id,key,message_id,input,input_hash,status) VALUES(?,?,?,?,?,?)',
                                  (project_id,key,message_id,dump(value),digest,'queued'))
            self._write(row,'process.message.queued',extra={'messageId':message_id})
            return {'key':key,'messageId':message_id,'status':'queued'}

    def guide(self,project_id,key,value,message_id,allowed,redact,*,run_id=None,target=None):
        """Durable input on the original mailbox; never ordinary next-turn input."""
        value=data(value);digest=hashlib.sha256(dump(value).encode()).hexdigest()
        value=data(redact(value));message_id=message_identity(message_id)
        with self.store.write():
            row=self._read(project_id,key);self.authority(self.store.project(project_id),allowed,row)
            if target is not None:return self._role_guide(row,value,digest,message_id,run_id,target)
            previous=self.store.db.execute('SELECT * FROM process_messages WHERE project_id=? AND key=? AND message_id=?',(project_id,key,message_id)).fetchone()
            if previous:
                if previous['input_hash']!=digest or not previous['status'].startswith('guide_'):
                    from .store import Conflict
                    raise Conflict('Message id already refers to different input or delivery purpose')
                return json.loads(previous['input'])['receipt']
            self._guidance_open(row)
            count=self._guidance_count(row)
            if count>=64:raise ValueError('Process guidance permits at most 64 uncommitted inputs')
            revision=row.get('guidanceRevision',0)+1
            receipt={'key':key,'runId':run_id,'messageId':message_id,'revision':revision,'status':'queued'}
            packet={'input':value,'receipt':receipt}
            self.store.db.execute('INSERT INTO process_messages(project_id,key,message_id,input,input_hash,status) VALUES(?,?,?,?,?,?)',
                                  (project_id,key,message_id,dump(packet),digest,'guide_queued'))
            row['guidanceRevision']=revision
            self._write(row,'process.guidance.queued',run_id,{'messageId':message_id,'guidanceRevision':revision})
            return receipt

    def guidance_box(self,claim):
        with self.store.lock:
            row=self._claimed(claim)
            messages=[]
            for item in self.store.db.execute("SELECT * FROM process_messages WHERE project_id=? AND key=? AND status IN ('guide_queued','guide_received') ORDER BY seq",(row['projectId'],row['key'])).fetchall():
                packet=json.loads(item['input'])
                messages.append({'hash':item['input_hash'],'receipt':packet['receipt'],'input':packet['input']})
            return {'revision':row.get('guidanceRevision',0),'messages':messages}

    def _claimed(self,claim):
        from .store import Conflict
        row=self._read(claim['project']['id'],claim['key']);pending=row['pending']
        if pending is None or pending['lease']!=claim['pending']['lease'] or pending['runId']!=claim['pending']['runId']:
            raise Conflict('Process turn lease changed')
        return row

    def _guide_receipt(self,row,message_id,revision,status):
        item=self.store.db.execute('SELECT * FROM process_messages WHERE project_id=? AND key=? AND message_id=?',(row['projectId'],row['key'],message_id)).fetchone()
        if item is None or not item['status'].startswith('guide_'):
            raise LanguageError('Unknown durable guidance identity',code='ownership_error')
        packet=json.loads(item['input'])
        if packet['receipt']['revision']!=revision or item['status']=='guide_consumed':
            raise LanguageError('Durable guidance revision is not pending',code='ownership_error')
        packet['receipt']['status']=status
        self.store.db.execute('UPDATE process_messages SET status=?,input=? WHERE seq=?',
                              ('guide_adopted' if status=='applied' else 'guide_received',dump(packet),item['seq']))

    def _role_receipt(self,row,role_id,message_id,revision,status):
        role=self._role(row,role_id=role_id)
        item=self.store.db.execute('SELECT * FROM process_role_messages WHERE project_id=? AND key=? AND role_id=? AND message_id=?',
             (row['projectId'],row['key'],role_id,message_id)).fetchone()
        if item is None or item['revision']!=revision or item['status']=='guide_consumed':
            raise LanguageError('Durable role guidance revision is not pending',code='ownership_error')
        packet=json.loads(item['input']);packet['receipt']['status']=status
        self.store.db.execute('UPDATE process_role_messages SET status=?,input=? WHERE seq=?',
            ('guide_adopted'if status=='applied'else'guide_received',dump(packet),item['seq']))
        return role,packet['receipt']

    def _unadopted_roles(self,row):
        return self.store.db.execute("SELECT 1 FROM process_role_messages WHERE project_id=? AND key=? AND status IN ('guide_queued','guide_received') LIMIT 1",
            (row['projectId'],row['key'])).fetchone() is not None

    def receive_guide(self,claim,message_id,revision,receipt):
        """Ordinary receive retains its legacy destructive-read semantics."""
        with self.store.write():
            row=self._claimed(claim);self._guide_receipt(row,message_id,revision,'applied')
            pending=row['pending'];ids=pending.setdefault('guideAdopted',[])
            if message_id not in ids:ids.append(message_id)
            self.store._event(row['projectId'],'guidance.received',{'version':claim['project']['version'],**receipt},pending['runId'])
            self._write(row,'process.guidance.received',pending['runId'],{'messageId':message_id,'guidanceRevision':revision})

    def bot_operation(self,claim,operation,payload,redact):
        """One durable root checkpoint; descendant invocations share its ledger."""
        with self.store.write():
            row=self._claimed(claim);pending=row['pending']
            if row['status']!='running':raise LanguageError('Process turn is not running',code='process_error')
            work=pending.setdefault('botWork',{'phase':'pending','blocked':False,'scope':None,'actions':[],
                       'checkpoint':None,'checkpointRevision':row.get('botCheckpointRevision',0),'scopes':0})
            children=work.setdefault('children',{});root=work['scope'];scope_id=payload['scopeId'];durable_receipt=None
            ancestry=data(payload.get('ancestry',[]));parent_id=payload.get('parentScopeId')
            child=bool(ancestry)
            if (type(ancestry)is not list or len(ancestry)>=16 or any(type(part)is not dict or
                    set(part)!={'scopeId','decisionId','revision'}or type(part['scopeId'])is not str or
                    type(part['decisionId'])is not str or type(part['revision'])is not int or part['revision']<0
                    for part in ancestry)):
                raise LanguageError('Invalid durable Bot ancestry',code='ownership_error')
            if child:
                if (root is None or payload.get('rootScopeId')!=root['id'] or ancestry[0]['scopeId']!=root['id']
                        or parent_id!=ancestry[-1]['scopeId']):
                    raise LanguageError('Process child has no current root Bot',code='ownership_error')
                expected=[];previous=None
                for index,part in enumerate(ancestry):
                    ancestor=root if index==0 else children.get(part['scopeId'])
                    if (ancestor is None or not ancestor.get('active',True) or ancestor.get('terminal')
                            or ancestor.get('parentScopeId')!=previous):
                        raise LanguageError('Process Bot ancestry is not active',code='ownership_error')
                    expected.append(part['scopeId']);previous=part['scopeId']
                if len(set(expected))!=len(expected)or scope_id in expected:
                    raise LanguageError('Cyclic Process Bot ancestry',code='ownership_error')
            revision=ancestry[0]['revision']if child else payload.get('revision')
            def belongs(action):
                return action.get('ownerScopeId',root['id']if root else None)==scope_id or any(
                    part['scopeId']==scope_id for part in action.get('ancestry',[]))
            if operation=='open':
                if work['blocked']:raise LanguageError('Process Bot requires explicit durable reconciliation',code='process_error')
                if work['scopes']>=16:raise LanguageError('Process Bot scope budget exhausted',code='process_error')
                if child:
                    if scope_id in children:raise LanguageError('Duplicate Process Bot child',code='ownership_error')
                    children[scope_id]={'id':scope_id,'parentScopeId':parent_id,'rootScopeId':root['id'],
                       'ancestry':ancestry,'terminal':False,'committed':False,'active':True,'blocked':False}
                    if 'roleId'in payload:
                        role=self._role(row,role_key=payload.get('roleKey'))
                        if role['roleId']!=payload['roleId'] or payload.get('revision')!=role['revision']:
                            raise LanguageError('Process role binding changed',code='ownership_error')
                        if any(c['active']and c.get('roleId')==role['roleId'] for c in children.values()):
                            raise LanguageError('Process role already has an active Bot invocation',code='process_error')
                        children[scope_id].update(roleKey=role['roleKey'],roleId=role['roleId'])
                else:
                    if root is not None:raise LanguageError('Process Bot scope budget exhausted',code='process_error')
                    if payload.get('mailboxKind','run')!='run':raise LanguageError('Process needs its legacy root Bot',code='process_error')
                    work['scope']={'id':scope_id,'parentScopeId':None,'terminal':False,'committed':False,'active':True}
                    children.clear();work['phase']='pending';work['checkpoint']=None
                work['scopes']+=1
            else:
                scope=children.get(scope_id)if child else root
                if (scope is None or scope['id']!=scope_id or not scope.get('active',True)
                        or child and scope['ancestry']!=ancestry):
                    raise LanguageError('Process Bot scope is not owned by this turn',code='ownership_error')
                if operation in ('receive','adopt'):
                    if child:
                        if scope.get('roleId')is None or payload.get('roleId')!=scope['roleId']:
                            raise LanguageError('Ephemeral child guidance cannot enter a durable Process',code='process_error')
                        unused,durable_receipt=self._role_receipt(row,scope['roleId'],payload['messageId'],payload['revision'],'received'if operation=='receive'else'applied')
                        if operation=='adopt':
                            identity={'roleId':scope['roleId'],'messageId':payload['messageId'],'revision':payload['revision']}
                            ids=pending.setdefault('roleGuideAdopted',[])
                            if identity not in ids:ids.append(identity)
                    else:
                        self._guide_receipt(row,payload['messageId'],payload['revision'],'received'if operation=='receive'else'applied')
                        if operation=='adopt':
                            ids=pending.setdefault('guideAdopted',[])
                            if payload['messageId']not in ids:ids.append(payload['messageId'])
                elif operation=='admit':
                    if work['blocked']:raise LanguageError('Process Bot requires explicit durable reconciliation',code='process_error')
                    if revision!=row.get('guidanceRevision',0):
                        from .bot_host import BotStale
                        raise BotStale(scope_id=root['id'])
                    self._role_revisions(row,root,children,ancestry,scope,payload['revision'])
                    if len(work['actions'])>=256:raise LanguageError('Durable Bot action budget exhausted',code='resource_limit')
                    action=data(payload['action'])
                    if any(item['id']==action['id']for item in work['actions']):raise LanguageError('Duplicate Bot action identity',code='ownership_error')
                    work['actions'].append({**action,'decisionId':payload['decisionId'],'revision':payload['revision'],
                        'phase':payload['phase'],'ownerScopeId':scope_id,'ancestry':ancestry})
                    work['phase']='pending'
                elif operation=='finish':
                    action=payload['action'];prior=next((item for item in work['actions']if item['id']==action['id']),None)
                    if (prior is None or prior['decisionId']!=payload['decisionId']or prior['revision']!=payload['revision']
                            or prior.get('ownerScopeId',scope_id)!=scope_id or prior.get('ancestry',[])!=ancestry):
                        raise LanguageError('Unknown durable Bot admission',code='ownership_error')
                    prior.update(data(action))
                    if action['status']!='completed':work['blocked']=True
                elif operation=='commit':
                    if work['blocked']:raise LanguageError('Process Bot requires explicit durable reconciliation',code='process_error')
                    owned=[action for action in work['actions']if belongs(action)]
                    if any(action['status']!='completed'for action in owned):raise LanguageError('Process Bot has an uncertain admitted action',code='process_error')
                    if revision!=row.get('guidanceRevision',0):
                        from .bot_host import BotStale
                        raise BotStale(scope_id=root['id'])
                    self._role_revisions(row,root,children,ancestry,scope,payload['revision'])
                    if any(c['active']and any(part['scopeId']==scope_id for part in c['ancestry'])for c in children.values()):
                        raise LanguageError('Process Bot has active descendants',code='process_error')
                    if not child:
                        if payload['done']and self._unadopted_roles(row):
                            raise LanguageError('Terminal Process checkpoint has unadopted role guidance',code='process_error')
                        checkpoint={'state':data(payload['snapshot']),'output':data(payload['output']),'done':payload['done']}
                        work['checkpoint']=data(redact(data(checkpoint)));work['checkpointRevision']+=1
                        row['botCheckpointRevision']=work['checkpointRevision']
                    # A child result verifies its own lifecycle, never the root
                    # ProcessStep snapshot or its checkpoint revision.
                    scope['committed']=True;scope['terminal']=payload['terminal']
                elif operation=='close':
                    success=payload['success']and scope['committed']and scope['terminal']and all(
                        action['status']=='completed'for action in work['actions']if belongs(action))
                    success=success and not any(c['active']and any(part['scopeId']==scope_id for part in c['ancestry'])for c in children.values())
                    work['blocked']=work['blocked']or not success;scope['active']=False
                    if child:scope['blocked']=not success
                    else:
                        work['phase']='blocked'if work['blocked']else'ready';work['scope']=None
                else:raise LanguageError('Unknown Process Bot operation',code='process_error')
            work['guidanceRevision']=row.get('guidanceRevision',0)
            data(work)
            extra={'guidanceRevision':work['guidanceRevision'],'checkpointRevision':work['checkpointRevision']}
            if child:extra.update(scopeId=scope_id,parentScopeId=parent_id,rootScopeId=payload['rootScopeId'])
            if 'roleId'in payload:extra.update(roleId=payload['roleId'],roleKey=payload['roleKey'])
            if durable_receipt is not None:extra['receipt']=durable_receipt
            if 'messageId'in payload:extra.update(messageId=payload['messageId'],messageRevision=payload['revision'])
            return self._write(row,'process.bot.'+operation,pending['runId'],extra)

    def _role_revisions(self,row,root,children,ancestry,scope,revision):
        from .bot_host import BotStale
        chain=[(root if part['scopeId']==root['id']else children[part['scopeId']],part['revision'])for part in ancestry]
        chain.append((scope,revision))
        for current,expected in chain:
            if current.get('roleId')is not None:
                role=self._role(row,role_id=current['roleId'])
                if role['revision']!=expected:raise BotStale(scope_id=current['id'])

    def control(self, project_id, key, revision, action, allowed):
        if action not in ('pause','resume','stop'): raise ValueError('Process action must be pause, resume or stop')
        with self.store.write():
            row=self._read(project_id,key); self.authority(self.store.project(project_id),allowed,row); self.revision(row,revision)
            if row['status'] in ('completed','needs_reconciliation'):
                raise ValueError('Completed or uncertain Process cannot be controlled; reconcile an uncertain turn first')
            if action=='resume':
                if row['pending'] is not None: raise ValueError('Cannot resume an active Process')
                row['control']='running';row['status']='waiting'
                if not row['started']:row['nextWakeAt']=time.time()
                elif row['definition']['intervalSeconds'] is not None:row['nextWakeAt']=time.time()+row['definition']['intervalSeconds']
                self.store.db.execute("UPDATE process_role_messages SET offered=0 WHERE project_id=? AND key=? AND status IN ('guide_queued','guide_received')",(project_id,key))
            else:
                row['control']='paused' if action=='pause' else 'stopped'
                if row['pending'] is None:row['status']=row['control']
            return self._write(row,'process.'+action)

    def configure(self, project_id, key, revision, definition, allowed):
        from .process_values import definition_hash
        with self.store.write():
            row=self._read(project_id,key); project=self.store.project(project_id)
            self.authority(project,allowed,row); self.revision(row,revision)
            definition=self.binding(project,definition)
            if row['definition'].get('roles',[])!=definition.get('roles',[]) or ('roles'in row['definition'])!=('roles'in definition):
                if row['pending']is not None:
                    from .store import Conflict
                    raise Conflict('Process roles can change only between turns')
                self._configure_roles(row,definition)
            row['definition']=definition;row['definitionHash']=definition_hash(definition)
            if row['pending'] is None:
                if definition['maxTurns'] is not None and row['turn']>=definition['maxTurns']:
                    if self._unadopted_roles(row):
                        from .store import Conflict
                        raise Conflict('Process maxTurns would strand unadopted role guidance')
                    row['status']='completed';row['nextWakeAt']=None
                elif row['started']:
                    row['nextWakeAt']=None if definition['intervalSeconds'] is None else time.time()+definition['intervalSeconds']
            return self._write(row,'process.configured')

    def reconcile(self, project_id, key, revision, state, evidence, resume, allowed):
        state=data(state)
        if type(evidence) is not str or len(evidence)>1000: raise ValueError('Reconciliation evidence must be a String of at most 1000 characters')
        if type(resume) is not bool: raise ValueError('resume must be Bool')
        with self.store.write():
            row=self._read(project_id,key); self.authority(self.store.project(project_id),allowed,row);self.revision(row,revision)
            if row['status']!='needs_reconciliation' or row['pending'] is None:
                raise ValueError('Process has no uncertain turn to reconcile')
            pending=row['pending']
            if pending.get('botWork') and not evidence.strip():raise ValueError('Bot reconciliation requires explicit evidence')
            link_reconciliation=False
            if pending['messageId'] is not None:
                outcome=self.message(project_id,key,pending['messageId'])
                if outcome['mailboxStatus']!='claimed':
                    from .store import Conflict
                    raise Conflict('Pending Process message is not claimed')
                if outcome['linkStatus']=='linked':
                    if outcome['run']['id']!=pending['runId']:
                        from .store import Conflict
                        raise Conflict('Pending Process message certificate differs from its run')
                    link_reconciliation=True
            self._ack(row,pending)
            row['state']=state;row['turn']=pending['turn'];row['started']=True;row['pending']=None
            row['error']=None;row['reconciliation']={'runId':pending['runId'],'evidence':evidence,'at':time.time()}
            row['control']='running' if resume else 'paused';row['status']='waiting' if resume else 'paused'
            if resume:self.store.db.execute("UPDATE process_role_messages SET offered=0 WHERE project_id=? AND key=? AND status IN ('guide_queued','guide_received')",(project_id,key))
            interval=row['definition']['intervalSeconds'];row['nextWakeAt']=None if interval is None else time.time()+interval
            if row['definition']['maxTurns'] is not None and row['turn']>=row['definition']['maxTurns']:
                if self._unadopted_roles(row):
                    from .store import Conflict
                    raise Conflict('Reconciled Process maxTurns would strand unadopted role guidance')
                row['status']='completed';row['nextWakeAt']=None
            result,event_id=self._publish(row,'process.reconciled',pending['runId'],{'definitionHash':pending['definitionHash'],'evidence':evidence})
            if link_reconciliation:
                self.store.db.execute('UPDATE process_messages SET reconciled_event_id=? WHERE project_id=? AND key=? AND message_id=?',
                                      (event_id,project_id,key,pending['messageId']))
            return result

    def candidates(self):
        with self.store.lock:
            # Do not deserialize large state/output documents merely to notice idle mailboxes.
            # INSERT OR REPLACE moves updated rows to the tail for bounded fair admission.
            rows=self.store.db.execute("""SELECT p.project_id,p.key FROM processes p
                WHERE p.status='waiting' AND (p.wake<=? OR EXISTS (
                    SELECT 1 FROM process_messages m WHERE m.project_id=p.project_id
                    AND m.key=p.key AND m.status IN ('queued','guide_queued','guide_received')) OR EXISTS (
                    SELECT 1 FROM process_role_messages r WHERE r.project_id=p.project_id AND r.key=p.key
                    AND r.offered=0 AND r.status IN ('guide_queued','guide_received'))) ORDER BY p.rowid LIMIT 256""",(time.time(),)).fetchall()
            return [(row['project_id'],row['key']) for row in rows]

    def has_waiting(self):
        with self.store.lock:
            return self.store.db.execute("SELECT 1 FROM processes WHERE status='waiting' LIMIT 1").fetchone() is not None

    def claim(self, project_id, key, run_id):
        """Engine admission lock is held. Claim and run creation share one transaction."""
        from .program_values import canonical_hash
        with self.store.write():
            row=self._read(project_id,key)
            if row['status']!='waiting' or row['pending'] is not None:return None
            project=self.store.project(project_id)
            effective=set(row['launchCapabilities']).intersection(project['capabilities'])
            if 'process.manage' not in effective:
                row['status']='paused';row['control']='paused';row['error']='process.manage was revoked; admission paused'
                self._write(row,'process.authority.paused');return None
            if row['definition']['maxTurns'] is not None and row['turn']>=row['definition']['maxTurns']:
                if self._unadopted_roles(row):
                    from .store import Conflict
                    raise Conflict('Process maxTurns has unadopted role guidance')
                row['status']='completed';row['nextWakeAt']=None;self._write(row,'process.completed');return None
            message=None
            if not row['started']:kind='start'
            else:
                message=self.store.db.execute("SELECT * FROM process_messages WHERE project_id=? AND key=? AND status='queued' ORDER BY seq LIMIT 1",(project_id,key)).fetchone()
                if message is not None:
                    if any(message[column] is not None for column in MESSAGE_LINK_COLUMNS):
                        from .store import Conflict
                        raise Conflict('Previously claimed Process message cannot be replayed')
                    kind='message'
                elif self.store.db.execute("SELECT 1 FROM process_messages WHERE project_id=? AND key=? AND status IN ('guide_queued','guide_received') LIMIT 1",(project_id,key)).fetchone():kind='guidance'
                elif self.store.db.execute("SELECT 1 FROM process_role_messages WHERE project_id=? AND key=? AND offered=0 AND status IN ('guide_queued','guide_received') LIMIT 1",(project_id,key)).fetchone():kind='guidance'
                elif row['nextWakeAt'] is not None and row['nextWakeAt']<=time.time():kind='timer'
                else:return None
            source={'kind':'program','entry':project['entry'],'files':project['files'],'languageVersions':project_language_versions(project)}
            pending={'runId':run_id,'messageId':message['message_id'] if message else None,'turn':row['turn']+1,
                     'version':project['version'],'definitionHash':canonical_hash(source),'kind':kind,'lease':uuid.uuid4().hex,
                     'processDefinitionHash':row['definitionHash'],'effectiveCapabilities':sorted(effective)}
            value={'state':row['state'],'input':json.loads(message['input']) if message else None,'turn':pending['turn'],
                   'process':{'key':key,'kind':kind,'messageId':pending['messageId']},'programVersion':project['version']}
            value=json_copy(value,max_chars=100000)
            if len(dump(value))>100000:raise ValueError('Process turn input exceeds 100000 JSON characters')
            if message:self.store.db.execute("UPDATE process_messages SET status='claimed' WHERE seq=?",(message['seq'],))
            self.store.db.execute("UPDATE process_role_messages SET offered=1 WHERE project_id=? AND key=? AND status IN ('guide_queued','guide_received')",(project_id,key))
            row['pending']=pending;row['status']='running';row['lastRunId']=run_id;row['lastVersion']=project['version'];row['error']=None
            now=time.time()
            self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (run_id,project_id,project['version'],'source','queued',dump(project),dump(value),None,None,now,now))
            self.store.db.execute('INSERT INTO process_run_lineage(run_id,project_id,key) VALUES(?,?,?)',
                                  (run_id,project_id,key))
            self.store._event(project_id,'run.queued',{'version':project['version'],'mode':'source','processKey':key},run_id)
            _,event_id=self._publish(row,'process.turn.queued',run_id,{k:v for k,v in pending.items() if k not in ('lease','effectiveCapabilities')})
            if message is not None:
                self.store.db.execute('UPDATE process_messages SET run_id=?,claim_event_id=? WHERE seq=?',
                                      (run_id,event_id,message['seq']))
            return {'project':project,'input':value,'definition':row['definition'],'pending':pending,'key':key}

    def _ack(self,row,pending):
        if pending['messageId'] is not None:
            self.store.db.execute("UPDATE process_messages SET status='consumed',input='null' WHERE project_id=? AND key=? AND message_id=?",
                                  (row['projectId'],row['key'],pending['messageId']))
        for message_id in pending.get('guideAdopted',[]):
            item=self.store.db.execute('SELECT * FROM process_messages WHERE project_id=? AND key=? AND message_id=?',(row['projectId'],row['key'],message_id)).fetchone()
            if item is not None and item['status']=='guide_adopted':
                packet=json.loads(item['input']);packet['input']=None
                self.store.db.execute("UPDATE process_messages SET status='guide_consumed',input=? WHERE seq=?",(dump(packet),item['seq']))
        for identity in pending.get('roleGuideAdopted',[]):
            item=self.store.db.execute('SELECT * FROM process_role_messages WHERE project_id=? AND key=? AND role_id=? AND message_id=? AND revision=?',
                (row['projectId'],row['key'],identity['roleId'],identity['messageId'],identity['revision'])).fetchone()
            if item is not None and item['status']=='guide_adopted':
                packet=json.loads(item['input']);packet['input']=None
                self.store.db.execute("UPDATE process_role_messages SET status='guide_consumed',input=? WHERE seq=?",(dump(packet),item['seq']))

    def finish_in_transaction(self, claim, status, output, error, *, evolution=None):
        """Called inside Store.transition_run: never opens a nested transaction."""
        from .store import Conflict
        row=self._read(claim['project']['id'],claim['key']); pending=row['pending']
        if pending is None or pending['lease']!=claim['pending']['lease'] or pending['runId']!=claim['pending']['runId']:
            raise Conflict('Process turn lease changed')
        if status=='completed':
            if (output['done']or row['definition']['maxTurns']is not None and pending['turn']>=row['definition']['maxTurns'])and self._unadopted_roles(row):
                raise Conflict('Terminal Process turn has unadopted role guidance')
            work=pending.get('botWork')
            if work is not None and (work['blocked'] or work['phase']!='ready' or work['scope'] is not None or work['checkpoint']!=output or any(action['status']!='completed' for action in work['actions'])):
                raise Conflict('Native Bot checkpoint is uncertain or differs from ProcessStep; reconcile before continuing')
            if evolution is not None:row['lastEvolution']=evolution
            row['state']=output['state'];row['output']=output['output'];row['turn']=pending['turn'];row['started']=True
            self._ack(row,pending);row['pending']=None;row['error']=None
            limit=row['definition']['maxTurns']; interval=row['definition']['intervalSeconds']
            row['status']='completed' if output['done'] or (limit is not None and row['turn']>=limit) else ('waiting' if row['control']=='running' else row['control'])
            row['nextWakeAt']=None if row['status']=='completed' or interval is None else time.time()+interval
        else:
            row['status']='needs_reconciliation';row['error']=error;row['nextWakeAt']=None
        self._write(row,'process.turn.'+status,pending['runId'],
                    {'definitionHash':pending['definitionHash'],'kind':pending['kind'],'attemptedTurn':pending['turn']})

    def recover_in_transaction(self):
        for result in self.store.db.execute("SELECT document FROM processes WHERE status='running'").fetchall():
            row=json.loads(result['document']);row['status']='needs_reconciliation';row['nextWakeAt']=None
            row['error']='Host restarted during an uncertain turn; explicit reconciliation required'
            self._write(row,'process.interrupted',row['lastRunId'])
