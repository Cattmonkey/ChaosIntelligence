"""Native conversation receipts on the existing project Store transaction."""
import hashlib
import json
import time
import uuid
from .extensions import json_copy, language_permissions
from .language import LanguageError
from .modes import identifier
from .session_values import message_identifier


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS agent_sessions(project_id TEXT,key TEXT,document TEXT,PRIMARY KEY(project_id,key))')
    db.execute('''CREATE TABLE IF NOT EXISTS session_turns(id TEXT PRIMARY KEY,project_id TEXT,key TEXT,
      message_id TEXT,input_hash TEXT,input TEXT,status TEXT,number INTEGER,run_id TEXT,version INTEGER,output TEXT,revision INTEGER,
      UNIQUE(project_id,key,message_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS session_messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,
      turn_id TEXT,message TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS session_messages_turn ON session_messages(turn_id,seq)')
    db.execute('CREATE TABLE IF NOT EXISTS session_tool_bindings(turn_id TEXT PRIMARY KEY,document TEXT NOT NULL)')


class SessionStore:
    def __init__(self, store): self.store=store

    def _read(self, project_id, key):
        from .store import NotFound
        identifier(key,'Session key')
        row=self.store.db.execute('SELECT document FROM agent_sessions WHERE project_id=? AND key=?',(project_id,key)).fetchone()
        if row is None:raise NotFound('AgentSession not found')
        return json.loads(row['document'])

    @staticmethod
    def public(row):
        row=json.loads(dump(row))
        if row['pending']:row['pending'].pop('lease',None)
        return row

    @staticmethod
    def authority(project, allowed, row=None):
        effective=set(project['capabilities']).intersection(allowed)
        if 'session.manage' not in effective:raise LanguageError('Current context does not grant session.manage',code='session_error')
        if row is not None and not set(row['launchCapabilities']).intersection(project['capabilities']).issubset(effective):
            raise LanguageError('Caller authority is narrower than this AgentSession',code='session_error')
        return effective

    @staticmethod
    def revision(row, expected):
        from .store import version_number, Conflict
        version_number(expected)
        if row['revision']!=expected:raise Conflict('AgentSession revision changed; reload before editing')

    @staticmethod
    def definition(agent):
        from .agent_values import validate_definition
        value=validate_definition(agent)
        if value['config'].get('provider')!='model':raise LanguageError('AgentSession requires provider=model with native backend',code='session_error')
        if set(value['config'].get('tools',[])).intersection(language_permissions() | {'graph.patch'}):
            raise LanguageError('Reserved permissions are not Agent tools',code='session_error')
        return value

    def _write(self,row,event,run_id=None,extra=None):
        row['revision']+=1;row['updated']=time.time()
        self.store.db.execute('INSERT OR REPLACE INTO agent_sessions VALUES(?,?,?)',(row['projectId'],row['key'],dump(row)))
        payload={k:row[k] for k in ('key','revision','status','turn','definitionHash')}
        if extra:payload.update(extra)
        self.store._event(row['projectId'],event,payload,run_id)
        return self.public(row)

    def get(self,project_id,key,allowed=None):
        with self.store.lock:
            project=self.store.project(project_id);row=self._read(project_id,key)
            if allowed is not None:self.authority(project,allowed,row)
            return self.public(row)

    def list(self,project_id,after='',limit=100):
        from .store import page_bounds
        page_bounds(0,limit)
        if limit>100:raise ValueError('Session page limit must be 1–100')
        if type(after)is not str:raise ValueError('Invalid session cursor')
        with self.store.lock:
            project=self.store.project(project_id);self.authority(project,project['capabilities'])
            rows=self.store.db.execute('SELECT document FROM agent_sessions WHERE project_id=? AND key>? ORDER BY key LIMIT ?',(project_id,after,limit+1)).fetchall()
            items=[]
            for r in rows[:limit]:
                item=self.public(json.loads(r['document']))
                if not self._page_fits({'items':items+[item],'nextCursor':item['key']}):break
                items.append(item)
            if rows and not items:raise ValueError('Session metadata exceeds page budget')
            return {'items':items,'nextCursor':items[-1]['key'] if len(rows)>len(items) else None}

    def create(self,project_id,version,key,agent,context_limit,allowed):
        from .agent_values import definition_hash
        from .store import Conflict,version_number
        identifier(key,'Session key');version_number(version)
        agent=self.definition(agent)
        if type(context_limit)is not int or not 1000<=context_limit<=200000:raise ValueError('contextLimit must be 1000–200000')
        with self.store.write():
            project=self.store.project(project_id);effective=self.authority(project,allowed)
            if project['version']!=version:raise Conflict('Project version changed before Session open')
            requested=set(agent['config'].get('tools',[]))
            if not requested.issubset(effective):raise LanguageError('Session Agent requests tools outside current authority',code='session_error')
            existing=self.store.db.execute('SELECT 1 FROM agent_sessions WHERE project_id=? AND key=?',(project_id,key)).fetchone()
            if existing:
                row=self._read(project_id,key);self.authority(project,allowed,row)
                if row['agent']!=agent or row['contextLimit']!=context_limit:raise Conflict('Session key has another Agent definition or context limit')
                return self.public(row)
            self._capacity(project_id)
            now=time.time();row={'key':key,'projectId':project_id,'revision':0,'agent':agent,'definitionHash':definition_hash(agent),
                'contextLimit':context_limit,'contextFrom':0,'status':'idle','turn':0,'pending':None,'lastRunId':None,
                'lastVersion':None,'launchCapabilities':sorted(requested|{'session.manage'}),'error':None,'created':now,'updated':now,'reconciliation':None}
            return self._write(row,'session.opened')

    def _capacity(self,project_id):
        if self.store.db.execute('SELECT count(*) FROM agent_sessions WHERE project_id=?',(project_id,)).fetchone()[0]>=32:raise ValueError('At most 32 retained Sessions per project')
        if self.store.db.execute('SELECT count(*) FROM agent_sessions').fetchone()[0]>=256:raise ValueError('At most 256 retained Sessions per runtime')

    def _context(self,row):
        rows=self.store.db.execute('''SELECT m.message FROM session_messages m JOIN session_turns t ON m.turn_id=t.id
          WHERE t.project_id=? AND t.key=? AND t.status='completed' AND t.number>? ORDER BY m.seq''',(row['projectId'],row['key'],row['contextFrom'])).fetchall()
        return [json.loads(r['message']) for r in rows]

    @staticmethod
    def _page_fits(value):
        try:json_copy(value,max_chars=230000,max_nodes=19000)
        except ValueError:return False
        return len(dump(value))<=220000

    def _tool_binding(self,turn_id):
        row=self.store.db.execute('SELECT document FROM session_tool_bindings WHERE turn_id=?',(turn_id,)).fetchone()
        return json.loads(row['document']) if row else None

    def _check_binding(self,turn_id,binding):
        from .store import Conflict
        previous=self._tool_binding(turn_id)
        previous={k:v for k,v in previous.items()if k not in ('runId','sourceVersion')}if previous is not None else None
        if previous!=binding:raise Conflict('Session messageId has a different language tool binding')

    @staticmethod
    def _validate_binding(binding,row):
        if binding is None:return None
        from .session_tools import validate_binding
        return validate_binding(binding,row['agent'],row['definitionHash'])

    def preflight(self,project_id,key,revision,value,message_id,allowed,*,tool_binding=None):
        from .store import Conflict
        from .session_protocol import input_message,context_size
        input_message(value)
        digest=hashlib.sha256(dump(value).encode()).hexdigest()
        if message_id is not None:message_identifier(message_id,'Message id')
        with self.store.lock:
            row=self._read(project_id,key);self.authority(self.store.project(project_id),allowed,row)
            tool_binding=self._validate_binding(tool_binding,row)
            if message_id is not None:
                old=self.store.db.execute('SELECT * FROM session_turns WHERE project_id=? AND key=? AND message_id=?',(project_id,key,message_id)).fetchone()
                if old:
                    self._check_binding(old['id'],tool_binding)
                    if old['input_hash']!=digest or old['status']!='completed':raise Conflict('Session messageId conflicts with another or uncertain turn')
                    return
            self.definition(row['agent'])
            self.revision(row,revision)
            if row['status']!='idle':raise Conflict('Session is not idle')
            if context_size(self._context(row)+[input_message(value)])>row['contextLimit']:raise ValueError('Session context is full; compact explicitly')

    def claim(self,project_id,key,revision,value,message_id,allowed,run_id,version,redact,*,user_message=None,tool_binding=None):
        from .store import Conflict
        from .session_protocol import input_message,validate_messages
        value=json_copy(value,max_chars=40000)
        from .session_protocol import validate_message
        message=input_message(value)if user_message is None else validate_message(user_message)
        if message['role']!='user':raise ValueError('Session override must be a user message')
        digest=hashlib.sha256(dump(value if user_message is None else {'observedMessage':message}).encode()).hexdigest()
        message_id='message_'+uuid.uuid4().hex if message_id is None else message_identifier(message_id,'Message id')
        with self.store.write():
            row=self._read(project_id,key);project=self.store.project(project_id);effective=self.authority(project,allowed,row)
            tool_binding=self._validate_binding(tool_binding,row)
            old=self.store.db.execute('SELECT * FROM session_turns WHERE project_id=? AND key=? AND message_id=?',(project_id,key,message_id)).fetchone()
            if old:
                self._check_binding(old['id'],tool_binding)
                if old['input_hash']!=digest:raise Conflict('Session messageId refers to different input')
                if old['status']!='completed':raise Conflict('Session messageId is pending or abandoned; reconcile and use a new id')
                return {'receipt':{'key':key,'messageId':message_id,'status':'completed','output':json.loads(old['output']),'revision':old['revision'],'replayed':True},'session':self.public(row)}
            # Existing retained definitions use the same gate as new Sessions.
            # Keep already-completed receipt replay above this no-effect check.
            self.definition(row['agent'])
            if tool_binding is not None and any(not set(tool['capabilities']).issubset(effective)for tool in tool_binding['tools']):
                raise LanguageError('Session language tool capability was revoked',code='capability_error')
            self.revision(row,revision)
            if row['status']!='idle':raise Conflict('Session is not idle; closed or uncertain sessions cannot run')
            history=self._context(row)
            validate_messages(history,complete=True,context_limit=row['contextLimit'])
            # Admission must reject an already oversized new user message before claiming.
            from .session_protocol import context_size
            if context_size(history+[message])>row['contextLimit']:raise ValueError('Session context is full; compact complete turns explicitly')
            lease=uuid.uuid4().hex;turn_id=uuid.uuid4().hex
            row.update(status='running',pending={'lease':lease,'turnId':turn_id,'messageId':message_id,'runId':run_id,'version':version,'turn':row['turn']+1},lastRunId=run_id,lastVersion=version,error=None)
            self.store.db.execute('INSERT INTO session_turns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(turn_id,project_id,key,message_id,digest,dump(redact(value if user_message is None else {'observedMessage':message})),'pending',row['turn']+1,run_id,version,None,None))
            if tool_binding is not None:
                durable={**tool_binding,'runId':run_id,'sourceVersion':version}
                row['pending']['toolBinding']=durable
                self.store.db.execute('INSERT INTO session_tool_bindings VALUES(?,?)',(turn_id,dump(durable)))
            self._write(row,'session.started',run_id,{'messageId':message_id,'sourceVersion':version})
            return {'row':row,'history':history,'userMessage':message if user_message is not None else None,'lease':lease,'turnId':turn_id,'allowed':sorted(set(row['launchCapabilities']).intersection(effective).intersection(row['agent']['config'].get('tools',[])))}

    def _claimed(self,claim):
        from .store import Conflict
        row=self._read(claim['row']['projectId'],claim['row']['key'])
        if not row['pending'] or row['pending']['lease']!=claim['lease'] or row['status']!='running':raise Conflict('Session claim is no longer active')
        return row

    def journal(self,claim,messages,redact):
        from .session_protocol import validate_message,validate_messages,context_size
        messages=[validate_message(redact(m)) for m in messages]
        with self.store.write():
            row=self._claimed(claim)
            previous=[json.loads(r['message']) for r in self.store.db.execute('SELECT message FROM session_messages WHERE turn_id=? ORDER BY seq',(claim['turnId'],))]
            if not previous and claim.get('userMessage') is not None and (not messages or messages[0]!=redact(claim['userMessage'])):raise ValueError('Session journal initial input differs from the claimed visual input')
            pending=previous+messages
            validate_messages(pending,complete=False,context_limit=row['contextLimit'])
            if context_size(claim['history']+pending)>row['contextLimit']:raise ValueError('Session context limit exceeded; pending turn requires reconciliation')
            for message in messages:self.store.db.execute('INSERT INTO session_messages(turn_id,message) VALUES(?,?)',(claim['turnId'],dump(message)))

    def complete(self,claim,result,redact):
        from .session_protocol import validate_messages
        output=redact(result['output']);messages=redact(result['messages'])
        if type(output)is not str:raise ValueError('Session output must be String')
        with self.store.write():
            row=self._claimed(claim)
            actual=[json.loads(r['message']) for r in self.store.db.execute('SELECT message FROM session_messages WHERE turn_id=? ORDER BY seq',(claim['turnId'],))]
            if messages!=actual:raise ValueError('Session final messages do not match its persisted tool journal')
            if not actual or actual[-1].get('role')!='assistant' or actual[-1].get('tool_calls') or actual[-1].get('content')!=output:
                raise ValueError('Session receipt does not match its final assistant answer')
            validate_messages(claim['history']+actual,complete=True,context_limit=row['contextLimit'])
            pending=row['pending']
            if 'toolBinding'in pending:row['lastToolBinding']=pending['toolBinding']
            row.update(status='idle',pending=None,turn=row['turn']+1,error=None)
            metadata=self._write(row,'session.completed',pending['runId'],{'messageId':pending['messageId'],'sourceVersion':pending['version']})
            self.store.db.execute("UPDATE session_turns SET status='completed',output=?,revision=? WHERE id=?",(dump(output),row['revision'],claim['turnId']))
            return {'receipt':{'key':row['key'],'messageId':pending['messageId'],'status':'completed','output':output,'revision':row['revision'],'replayed':False},'session':metadata}

    def fail(self,claim,error):
        with self.store.write():
            row=self._claimed(claim);row['status']='needs_reconciliation';row['error']=error
            self.store.db.execute("UPDATE session_turns SET status='uncertain' WHERE id=?",(claim['turnId'],))
            return self._write(row,'session.uncertain',row['pending']['runId'])

    def history(self,project_id,key,allowed,after=0,limit=100):
        from .store import page_bounds
        page_bounds(after,limit)
        if limit>100:raise ValueError('Session page limit must be 1–100')
        with self.store.lock:
            row=self._read(project_id,key);self.authority(self.store.project(project_id),allowed,row)
            rows=self.store.db.execute('''SELECT m.seq,m.message,t.id AS turn_id,t.number,t.message_id,t.status,t.run_id,t.version
              FROM session_messages m JOIN session_turns t ON t.id=m.turn_id
              WHERE t.project_id=? AND t.key=? AND m.seq>? ORDER BY m.seq LIMIT ?''',(project_id,key,after,limit+1)).fetchall()
            items=[];result={'key':key,'revision':row['revision'],'status':row['status'],'contextFrom':row['contextFrom']}
            for r in rows[:limit]:
                item={'seq':r['seq'],'turn':r['number'],'messageId':r['message_id'],'status':r['status'],'runId':r['run_id'],'sourceVersion':r['version'],'message':json.loads(r['message'])}
                binding=self._tool_binding(r['turn_id'])
                if binding is not None:item['toolBinding']=binding
                if not self._page_fits({**result,'items':items+[item],'nextCursor':item['seq']}):break
                items.append(item)
            if rows and not items:raise ValueError('Session message exceeds page budget')
            return {**result,'items':items,'nextCursor':items[-1]['seq'] if len(rows)>len(items) else None}

    def control(self,project_id,key,revision,operation,payload,allowed):
        from .store import Conflict
        with self.store.write():
            row=self._read(project_id,key);self.authority(self.store.project(project_id),allowed,row);self.revision(row,revision)
            if operation=='reconcile':
                evidence=payload['evidence']
                if type(evidence)is not str or len(evidence)>1000:raise ValueError('evidence must be a String of at most 1000 characters')
                if row['status']!='needs_reconciliation':raise Conflict('Session has no uncertain turn')
                pending=row['pending'];self.store.db.execute("UPDATE session_turns SET status='abandoned' WHERE id=?",(pending['turnId'],))
                row.update(status='idle',pending=None,error=None,reconciliation={'evidence':evidence,'runId':pending['runId'],'messageId':pending['messageId'],'at':time.time()})
            else:
                if row['status']!='idle':raise Conflict('Only an idle Session can be compacted or closed')
                if operation=='close':row['status']='closed'
                elif operation=='compact':
                    keep=payload['keepTurns']
                    if type(keep)is not int or not 0<=keep<=1000000:raise ValueError('keepTurns must be a nonnegative Int')
                    row['contextFrom']=max(row['contextFrom'],row['turn']-keep)
                else:raise ValueError('Unknown Session control')
            return self._write(row,'session.'+operation)

    def fork(self,project_id,key,revision,new_key,agent,allowed):
        from .store import Conflict
        from .agent_values import definition_hash
        identifier(new_key,'New Session key')
        with self.store.write():
            project=self.store.project(project_id);row=self._read(project_id,key);effective=self.authority(project,allowed,row);self.revision(row,revision)
            if row['status'] not in ('idle','closed'):raise Conflict('Only idle or closed Sessions can be forked')
            agent=row['agent'] if agent is None else self.definition(agent)
            requested=set(agent['config'].get('tools',[]))|{'session.manage'}
            ceiling=set(row['launchCapabilities']).intersection(effective)
            if not requested.issubset(ceiling):raise LanguageError('Fork cannot expand Session authority',code='session_error')
            if self.store.db.execute('SELECT 1 FROM agent_sessions WHERE project_id=? AND key=?',(project_id,new_key)).fetchone():raise Conflict('Fork Session key already exists')
            self._capacity(project_id)
            new=json.loads(dump(row));new.update(key=new_key,revision=0,agent=agent,definitionHash=definition_hash(agent),status='idle',pending=None,error=None,turn=0,contextFrom=0,launchCapabilities=sorted(ceiling),created=time.time(),reconciliation=None)
            new.pop('lastToolBinding',None)
            turns=self.store.db.execute("SELECT * FROM session_turns WHERE project_id=? AND key=? AND status='completed' AND number>? ORDER BY number",(project_id,key,row['contextFrom'])).fetchall()
            for turn in turns:
                turn_id=uuid.uuid4().hex;new['turn']+=1
                self.store.db.execute('INSERT INTO session_turns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(turn_id,project_id,new_key,'fork_'+turn_id,turn['input_hash'],turn['input'],'completed',new['turn'],turn['run_id'],turn['version'],turn['output'],1))
                binding=self._tool_binding(turn['id'])
                if binding is not None:
                    self.store.db.execute('INSERT INTO session_tool_bindings VALUES(?,?)',(turn_id,dump(binding)))
                    new['lastToolBinding']=binding
                for message in self.store.db.execute('SELECT message FROM session_messages WHERE turn_id=? ORDER BY seq',(turn['id'],)).fetchall():
                    self.store.db.execute('INSERT INTO session_messages(turn_id,message) VALUES(?,?)',(turn_id,message['message']))
            return self._write(new,'session.forked',extra={'fromKey':key})

    def recover_in_transaction(self):
        for entry in self.store.db.execute('SELECT document FROM agent_sessions').fetchall():
            row=json.loads(entry['document'])
            if row['status']=='running':
                row['status']='needs_reconciliation';row['error']='Runtime restarted during a Session turn; explicit reconciliation required'
                self.store.db.execute("UPDATE session_turns SET status='uncertain' WHERE id=?",(row['pending']['turnId'],))
                self._write(row,'session.interrupted',row['pending']['runId'])
