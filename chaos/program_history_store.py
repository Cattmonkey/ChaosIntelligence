"""Immutable source-bundle revisions on the existing project Store transaction."""
import json
import time

from .modules import project_language_versions
from .program_values import canonical_hash


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS program_revisions (
        project_id TEXT NOT NULL, version INTEGER NOT NULL,
        definition TEXT NOT NULL, definition_hash TEXT NOT NULL,
        entry TEXT NOT NULL, created REAL NOT NULL, kind TEXT NOT NULL,
        run_id TEXT, reason TEXT NOT NULL,
        PRIMARY KEY(project_id,version))''')


def definition(project):
    return {'kind':'program', 'entry':project['entry'], 'files':project['files'],
            'languageVersions':project_language_versions(project)}


class ProgramHistoryStore:
    """No independent connection, worker, execution engine or permission archive."""
    def __init__(self, store):
        self.store = store

    @staticmethod
    def metadata(row):
        return {'version':row['version'], 'definitionHash':row['definition_hash'],
                'entry':row['entry'], 'created':row['created'], 'kind':row['kind'],
                'runId':row['run_id'], 'reason':row['reason']}

    def capture(self, project, kind, *, run_id=None, reason=''):
        # Called only after the canonical project state is written, within its
        # transaction. A failed INSERT aborts the project and events as well.
        if not self.store.db.in_transaction:
            raise RuntimeError('Program history capture requires a Store transaction')
        if kind not in ('created','saved','patched','adopted','migration'):
            raise ValueError('Invalid Program revision kind')
        if type(reason) is not str or len(reason)>1000:
            raise ValueError('Program revision reason must be at most 1000 characters')
        value = definition(project)
        encoded = json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))
        self.store.db.execute('INSERT INTO program_revisions VALUES(?,?,?,?,?,?,?,?,?)',
            (project['id'],project['version'],encoded,canonical_hash(value),project['entry'],time.time(),kind,run_id,reason))

    def history(self, project_id, after=0, limit=50):
        if type(after) is not int or not 0<=after<=2**63-1 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Program history after must be nonnegative and limit must be 1–100')
        with self.store.lock:
            # Head, coverage and page share one cross-connection read snapshot.
            self.store.db.execute('BEGIN')
            try:
                from .store import NotFound
                head = self.store.db.execute('SELECT version FROM projects WHERE id=?',(project_id,)).fetchone()
                if head is None: raise NotFound('Project not found')
                first = self.store.db.execute('SELECT MIN(version) FROM program_revisions WHERE project_id=?',(project_id,)).fetchone()[0]
                rows = self.store.db.execute('SELECT version,definition_hash,entry,created,kind,run_id,reason FROM program_revisions WHERE project_id=? AND version>? ORDER BY version LIMIT ?',
                    (project_id,after,limit+1)).fetchall()
                items = [self.metadata(row)for row in rows[:limit]]
                return {'items':items,'nextCursor':items[-1]['version']if len(rows)>limit else None,
                        'currentVersion':head['version'],'firstAvailableVersion':first}
            finally:
                self.store.db.rollback()

    def revision(self, project_id, version):
        from .store import version_number, NotFound
        version_number(version)
        if version>2**63-1: raise ValueError('Program revision version exceeds SQLite integer range')
        with self.store.lock:
            if self.store.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone()is None:
                raise NotFound('Project not found')
            row = self.store.db.execute('SELECT * FROM program_revisions WHERE project_id=? AND version=?',(project_id,version)).fetchone()
            if row is None: raise NotFound('Program revision is not available')
            value = json.loads(row['definition'])
            if canonical_hash(value)!=row['definition_hash']:
                raise ValueError('Stored Program revision hash does not match its definition')
            return {'definition':value,'origin':{'projectId':project_id,'version':version,'definitionHash':row['definition_hash']},
                    'metadata':self.metadata(row)}
