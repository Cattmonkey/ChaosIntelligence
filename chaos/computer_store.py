"""Immutable Computer evidence on the existing Store; images are bounded DB data."""
import copy
import json
import hashlib
import time
import uuid
from .extensions import json_copy


def dump(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS computer_records(seq INTEGER PRIMARY KEY AUTOINCREMENT,
      id TEXT UNIQUE,project_id TEXT,run_id TEXT,computer_id TEXT,kind TEXT,document TEXT,image TEXT,model_image TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS computer_records_project ON computer_records(project_id,seq)')
    db.execute('''CREATE TABLE IF NOT EXISTS computer_sessions(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, key TEXT NOT NULL,
      generation INTEGER NOT NULL, status TEXT NOT NULL, lease TEXT,
      held_by_run TEXT, requires_reconcile INTEGER NOT NULL,
      storage TEXT NOT NULL, last_url TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
      UNIQUE(project_id,key))''')


class ComputerStore:
    def __init__(self,store):self.store=store

    def _insert(self,project,run_id,computer_id,kind,status,data,location,images=None,record_id=None):
        record_id=record_id or uuid.uuid4().hex
        row={'id':record_id,'kind':kind,'projectId':project['id'],'runId':run_id,'computerId':computer_id,
             'sourceVersion':project['version'],'location':json_copy(location,max_chars=4000),'status':status,'data':json_copy(data,max_chars=100000)}
        image=dump(images['preview']) if images else None
        model=dump(images['model']) if images else None
        cursor=self.store.db.execute('INSERT INTO computer_records(id,project_id,run_id,computer_id,kind,document,image,model_image) VALUES(?,?,?,?,?,?,?,?)',
          (record_id,project['id'],run_id,computer_id,kind,dump(row),image,model))
        row['seq']=cursor.lastrowid
        self.store._event(project['id'],'computer.'+kind,{'recordId':record_id,'computerId':computer_id,'status':status,'sourceVersion':project['version'],'location':row['location']},run_id)
        return row

    def record(self,*args,**kwargs):
        with self.store.write():return self._insert(*args,**kwargs)

    def complete(self,project,run_id,computer_id,data,location,images,action_id=None):
        with self.store.write():
            row=self._insert(project,run_id,computer_id,'observation','completed',data,location,images,data['id'])
            if action_id:self._terminal(project['id'],action_id,'completed',{'resultObservationId':data['id']})
            return row

    def _terminal(self,project_id,record_id,status,extra=None):
        row=self._get(project_id,record_id)
        if row['status']!='started':return row
        row['status']=status
        if extra:row['data'].update(json_copy(extra,max_chars=4000))
        seq=row.pop('seq');self.store.db.execute('UPDATE computer_records SET document=? WHERE id=?',(dump(row),record_id));row['seq']=seq
        self.store._event(project_id,'computer.'+status,{'recordId':record_id,'computerId':row['computerId'],'sourceVersion':row['sourceVersion']},row['runId'])
        return row

    def terminal(self,project_id,record_id,status,extra=None):
        with self.store.write():return self._terminal(project_id,record_id,status,extra)

    def _get(self,project_id,record_id):
        from .store import NotFound
        if type(record_id)is not str or not 1<=len(record_id)<=128:raise ValueError('Invalid computer record id')
        row=self.store.db.execute('SELECT seq,document FROM computer_records WHERE project_id=? AND id=?',(project_id,record_id)).fetchone()
        if row is None:raise NotFound('Computer record not found')
        return {**json.loads(row['document']),'seq':row['seq']}

    def get(self,project_id,record_id):
        with self.store.lock:self.store.project(project_id);return self._get(project_id,record_id)

    def history(self,project_id,run_id=None,after=0,limit=50):
        from .store import page_bounds
        page_bounds(after,limit)
        if limit>100:raise ValueError('Computer history limit must be 1–100')
        if run_id is not None and (type(run_id)is not str or not 1<=len(run_id)<=128):raise ValueError('Invalid run id')
        with self.store.lock:
            self.store.project(project_id)
            query='SELECT seq,document FROM computer_records WHERE project_id=? AND seq>?';args=[project_id,after]
            if run_id is not None:query+=' AND run_id=?';args.append(run_id)
            rows=self.store.db.execute(query+' ORDER BY seq LIMIT ?',args+[limit+1]).fetchall();items=[]
            for row in rows[:limit]:
                item={**json.loads(row['document']),'seq':row['seq']}
                candidate={'items':items+[item],'nextCursor':item['seq']}
                try:json_copy(candidate,max_chars=200000,max_nodes=19000)
                except ValueError:break
                if len(dump(candidate))>200000:break
                items.append(item)
            if rows and not items:raise ValueError('Computer record exceeds history budget')
            return {'items':items,'nextCursor':items[-1]['seq'] if len(rows)>len(items)else None}

    def image(self,project_id,record_id,variant='preview'):
        from .store import NotFound
        if variant not in ('preview','model'):raise ValueError('Image variant must be preview or model')
        with self.store.lock:
            self.store.project(project_id);self._get(project_id,record_id)
            row=self.store.db.execute('SELECT image,model_image FROM computer_records WHERE project_id=? AND id=?',(project_id,record_id)).fetchone()
            data=row['image' if variant=='preview'else 'model_image']
            if data is None:raise NotFound('Observation has no image')
            return json.loads(data)

    @staticmethod
    def _session_metadata(row):
        return {'id':row['id'],'projectId':row['project_id'],'key':row['key'],
                'backend':'browser','generation':row['generation'],'status':row['status'],
                'driverState':'detached' if row['status']=='detached' else 'attached',
                'heldByRun':row['held_by_run'],'requiresReconcile':bool(row['requires_reconcile']),
                'lastUrl':row['last_url'],'createdAt':row['created'],'updatedAt':row['updated']}

    def _session(self,project_id,key):
        from .modes import identifier
        from .store import NotFound
        identifier(key,'Computer session key')
        row=self.store.db.execute('SELECT * FROM computer_sessions WHERE project_id=? AND key=?',(project_id,key)).fetchone()
        if row is None:raise NotFound('Computer session not found')
        return dict(row)

    def get_session(self,project_id,key):
        with self.store.lock:
            self.store.project(project_id)
            return self._session_metadata(self._session(project_id,key))

    def sessions(self,project_id,after=0,limit=50):
        from .store import page_bounds
        page_bounds(after,limit)
        if limit>100:raise ValueError('Computer session limit must be 1–100')
        with self.store.lock:
            self.store.project(project_id)
            rows=self.store.db.execute('SELECT * FROM computer_sessions WHERE project_id=? ORDER BY created,id LIMIT ? OFFSET ?',
                                      (project_id,limit+1,after)).fetchall()
            more=len(rows)>limit
            return {'items':[self._session_metadata(row) for row in rows[:limit]],
                    'after':after+limit if more else None,'hasMore':more}

    def claim_session(self,project_id,key,run_id):
        from .modes import identifier
        from .language import LanguageError
        identifier(key,'Computer session key')
        with self.store.write():
            self.store.project(project_id)
            raw=self.store.db.execute('SELECT * FROM computer_sessions WHERE project_id=? AND key=?',(project_id,key)).fetchone()
            if raw is None:
                if self.store.db.execute('SELECT count(*) FROM computer_sessions WHERE project_id=?',(project_id,)).fetchone()[0]>=64:
                    raise LanguageError('At most 64 retained Computer sessions per project',code='computer_error')
                now=time.time()
                self.store.db.execute('INSERT INTO computer_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex,project_id,key,0,'detached',None,None,0,dump({'cookies':[],'origins':[]}),'about:blank',now,now))
                row=self._session(project_id,key)
            else:row=dict(raw)
            if row['status'] not in ('idle','detached'):raise LanguageError('Computer session is already leased',code='computer_busy')
            token=uuid.uuid4().hex;generation=row['generation']+1
            self.store.db.execute("UPDATE computer_sessions SET generation=?,status='leased',lease=?,held_by_run=?,updated=? WHERE id=?",
                                  (generation,token,run_id,time.time(),row['id']))
            self.store._event(project_id,'computer.session.acquired',{'sessionKey':key,'stableSessionId':row['id'],'generation':generation},run_id)
            row=self._session(project_id,key)
            return {'projectId':project_id,'key':key,'id':row['id'],'generation':generation,'token':token,
                    'runId':run_id,'storage':json.loads(row['storage']),'requiresReconcile':bool(row['requires_reconcile'])}

    def _leased(self,claim):
        from .language import LanguageError
        row=self._session(claim['projectId'],claim['key'])
        if row['status']!='leased' or row['id']!=claim['id'] or row['lease']!=claim['token'] or row['generation']!=claim['generation']:
            raise LanguageError('Computer lease is stale',code='computer_error')
        return row

    def validate_lease(self,claim):
        with self.store.lock:return self._session_metadata(self._leased(claim))

    @staticmethod
    def _checkpoint(checkpoint):
        if type(checkpoint)is not dict or set(checkpoint)!={'storage','url'}:
            raise ValueError('Invalid Computer checkpoint')
        storage=checkpoint['storage'];url=checkpoint['url']
        if type(storage)is not dict or set(storage)!={'cookies','origins'} or type(storage['cookies'])is not list or type(storage['origins'])is not list:
            raise ValueError('Invalid browser storage checkpoint')
        if type(url)is not str or len(url)>8192:raise ValueError('Invalid Computer checkpoint URL')
        encoded=dump(storage)
        if len(encoded.encode('utf-8'))>1024*1024:raise ValueError('Computer storage checkpoint exceeds 1 MiB')
        return encoded,url

    def release_session(self,claim,checkpoint=None,*,uncertain=False,attached=False):
        state=self._checkpoint(checkpoint) if checkpoint is not None else None
        with self.store.write():
            row=self._leased(claim)
            storage,url=state if state is not None else (row['storage'],row['last_url'])
            self.store.db.execute("UPDATE computer_sessions SET status=?,lease=NULL,held_by_run=NULL,storage=?,last_url=?,requires_reconcile=?,updated=? WHERE id=?",
                ('idle' if attached else 'detached',storage,url,int(uncertain or row['requires_reconcile']),time.time(),row['id']))
            self.store._event(claim['projectId'],'computer.session.released',
                {'sessionKey':claim['key'],'stableSessionId':row['id'],'generation':row['generation'],
                 'requiresReconcile':bool(uncertain or row['requires_reconcile'])},claim['runId'])
            return self._session_metadata(self._session(claim['projectId'],claim['key']))

    def checkpoint_idle(self,project_id,key,generation,checkpoint):
        from .store import Conflict
        storage,url=self._checkpoint(checkpoint)
        with self.store.write():
            row=self._session(project_id,key)
            if row['status']!='idle' or row['generation']!=generation:raise Conflict('Computer session changed before checkpoint')
            self.store.db.execute('UPDATE computer_sessions SET storage=?,last_url=?,updated=? WHERE id=?',(storage,url,time.time(),row['id']))

    def detach_idle(self,project_id,key,generation):
        from .store import Conflict
        with self.store.write():
            row=self._session(project_id,key)
            if row['status']!='idle' or row['generation']!=generation:raise Conflict('Computer session changed before detach')
            self.store.db.execute("UPDATE computer_sessions SET status='detached',updated=? WHERE id=?",(time.time(),row['id']))
            self.store._event(project_id,'computer.session.detached',{'sessionKey':key,'stableSessionId':row['id'],'generation':generation})

    def uncertain_session(self,claim,action_id=None,error='Computer action outcome is uncertain'):
        with self.store.write():
            row=self._leased(claim)
            self.store.db.execute('UPDATE computer_sessions SET requires_reconcile=1,updated=? WHERE id=?',(time.time(),row['id']))
            if action_id:self._terminal(claim['projectId'],action_id,'uncertain',{'error':error})
            self.store._event(claim['projectId'],'computer.session.uncertain',
                {'sessionKey':claim['key'],'stableSessionId':row['id'],'generation':row['generation']},claim['runId'])

    def reconcile_session(self,claim,observation_id,evidence):
        if type(evidence)is not str or not evidence.strip() or len(evidence)>1000:raise ValueError('Reconciliation evidence must be a nonempty String of at most 1000 characters')
        with self.store.write():
            row=self._leased(claim)
            # A failed uncertainty receipt may have left an admitted action
            # started. Explicit reconciliation acknowledges it as uncertain,
            # never successful, so recovery must not re-open the same incident.
            for raw in self.store.db.execute("SELECT id,document FROM computer_records WHERE project_id=? AND kind='action'",(claim['projectId'],)).fetchall():
                document=json.loads(raw['document'])
                if document['status']=='started' and document['data'].get('stableSessionId')==row['id']:
                    self._terminal(claim['projectId'],raw['id'],'uncertain',{'reconciledObservationId':observation_id})
            self.store.db.execute('UPDATE computer_sessions SET requires_reconcile=0,updated=? WHERE id=?',(time.time(),row['id']))
            self.store._event(claim['projectId'],'computer.session.reconciled',
                {'sessionKey':claim['key'],'stableSessionId':row['id'],'generation':row['generation'],
                 'observationId':observation_id,'evidence':evidence},claim['runId'])

    def recover_in_transaction(self):
        for raw in self.store.db.execute("SELECT id,project_id,document FROM computer_records WHERE kind='action'").fetchall():
            document=json.loads(raw['document'])
            if document['status']=='started':
                self._terminal(raw['project_id'],raw['id'],'uncertain',{'error':'Runtime restarted before action completion; no action was replayed'})
                stable=document['data'].get('stableSessionId')
                if stable is not None:
                    self.store.db.execute('UPDATE computer_sessions SET requires_reconcile=1,updated=? WHERE project_id=? AND id=?',
                                          (time.time(),raw['project_id'],stable))
        for row in self.store.db.execute("SELECT * FROM computer_sessions WHERE status='leased'").fetchall():
            self.store.db.execute("UPDATE computer_sessions SET status='detached',lease=NULL,held_by_run=NULL,generation=generation+1,requires_reconcile=1,updated=? WHERE id=?",(time.time(),row['id']))
            self.store._event(row['project_id'],'computer.session.interrupted',
                {'sessionKey':row['key'],'stableSessionId':row['id'],'generation':row['generation']+1,
                 'requiresReconcile':True},row['held_by_run'])
        self.store.db.execute("UPDATE computer_sessions SET status='detached',updated=? WHERE status='idle'",(time.time(),))
