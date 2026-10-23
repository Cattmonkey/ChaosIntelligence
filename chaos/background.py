"""Durable asynchronous iteration jobs with a dynamically bounded dispatcher."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .extensions import json_copy


class BackgroundCancelled(Exception):
    pass


class BackgroundManager:
    def __init__(self, path, worker, max_workers=2, event=None):
        if not callable(worker):
            raise ValueError('Background worker must be callable')
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.worker, self.on_event = worker, event
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active = {}
        self._closing = False
        self._finalizer = None
        self._db_closed = False
        self._paused = False
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS background_jobs (
              id TEXT PRIMARY KEY, task TEXT NOT NULL, agent_id TEXT NOT NULL,
              status TEXT NOT NULL, iteration INTEGER NOT NULL, iterations INTEGER NOT NULL,
              interval REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
              output TEXT, error TEXT, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS background_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
              type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
        ''')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        rows = self._db.execute("SELECT id FROM background_jobs WHERE status='running'").fetchall()
        self._db.execute("UPDATE background_jobs SET status='interrupted',updated_at=? WHERE status='running'", (time.time(),))
        self._db.commit()
        self.configure(max_workers)
        for row in rows:
            self._event(row['id'], 'task.interrupted', {'summary': '进程已重启，任务等待显式恢复'})
        self._dispatcher = threading.Thread(target=self._dispatch, name='chaos-background-dispatch', daemon=True)
        self._dispatcher.start()

    def configure(self, max_workers):
        if type(max_workers) is not int or not 1 <= max_workers <= 8:
            raise ValueError('Background concurrency must be 1–8')
        with self._condition:
            self.max_workers = max_workers
            self._condition.notify_all()

    def _decode(self, row):
        return {'id': row['id'], 'task': json.loads(row['task']), 'agentId': row['agent_id'],
                'status': row['status'], 'iteration': row['iteration'], 'iterations': row['iterations'],
                'interval': row['interval'], 'createdAt': row['created_at'], 'updatedAt': row['updated_at'],
                'output': json.loads(row['output']) if row['output'] is not None else None,
                'error': row['error'], 'metadata': json.loads(row['metadata'])}

    def get(self, task_id):
        with self._lock:
            row = self._db.execute('SELECT * FROM background_jobs WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._decode(row)

    def list(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Task list limit must be 1–1000')
        with self._lock:
            rows = self._db.execute('SELECT * FROM background_jobs ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
        return [self._decode(row) for row in rows]

    def start(self, task, agent_id='background', iterations=3, interval=1, metadata=None):
        task, metadata = json_copy(task), json_copy(metadata or {})
        if not isinstance(agent_id, str) or not 1 <= len(agent_id) <= 128:
            raise ValueError('Invalid background agent id')
        if type(iterations) is not int or not 1 <= iterations <= 1000:
            raise ValueError('Iterations must be 1–1000')
        if type(interval) not in (int, float) or not 0 <= interval <= 3600:
            raise ValueError('Iteration interval must be 0–3600 seconds')
        now, task_id = time.time(), uuid.uuid4().hex
        with self._condition:
            if self._closing:
                raise RuntimeError('Background manager is closing')
            count = self._db.execute("SELECT COUNT(*) FROM background_jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= 100:
                raise ValueError('Background queue is full (100 active or queued jobs)')
            self._db.execute('INSERT INTO background_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (task_id, json.dumps(task, ensure_ascii=False), agent_id, 'queued', 0, iterations, interval,
                 now, now, None, None, json.dumps(metadata, ensure_ascii=False)))
            self._db.commit()
            self._event(task_id, 'task.queued', {'iterations': iterations, 'summary': '任务已进入后台队列'})
            self._condition.notify_all()
        return self.get(task_id)

    def _event(self, task_id, kind, payload):
        # Persist observable action summaries, never provider reasoning/messages,
        # tool arguments, credentials or arbitrary exception response bodies.
        allowed = {'summary', 'step', 'maxSteps', 'model', 'tool', 'iteration', 'iterations', 'status', 'callId', 'category', 'provider', 'backend', 'configVersion', 'pluginId', 'state', 'pluginRunId'}
        safe = {key: value[:1000] if isinstance(value, str) else value
                for key, value in payload.items() if key in allowed and type(value) in (str, int, float, bool, type(None))}
        safe = json_copy(safe)
        now = time.time()
        with self._lock:
            cursor = self._db.execute('INSERT INTO background_events(task_id,type,payload,created_at) VALUES (?,?,?,?)',
                                     (task_id, str(kind)[:100], json.dumps(safe, ensure_ascii=False), now))
            self._db.commit()
            item = {'id': cursor.lastrowid, 'taskId': task_id, 'type': str(kind)[:100], 'payload': safe, 'createdAt': now}
        if self.on_event:
            try:
                self.on_event(item)
            except Exception:
                # UI notification failure must not change the durable job result.
                pass
        return item

    def events(self, task_id=None, after=0, limit=200):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Invalid event cursor or limit')
        sql, args = 'SELECT * FROM background_events WHERE id>?', [after]
        if task_id is not None:
            sql += ' AND task_id=?'
            args.append(task_id)
        sql += ' ORDER BY id LIMIT ?'
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [{'id': row['id'], 'taskId': row['task_id'], 'type': row['type'],
                 'payload': json.loads(row['payload']), 'createdAt': row['created_at']} for row in rows]

    def cancel(self, task_id):
        with self._condition:
            job = self.get(task_id)
            if job['status'] == 'running':
                state = self._active.get(task_id)
                if state:
                    state['cancel'].set()
            elif job['status'] == 'queued':
                self._db.execute("UPDATE background_jobs SET status='cancelled',updated_at=? WHERE id=?", (time.time(), task_id))
                self._db.commit()
                self._event(task_id, 'task.cancelled', {'summary': '后台任务已取消'})
            self._condition.notify_all()
        return self.get(task_id)

    def resume(self, task_id):
        with self._condition:
            if self._closing:
                raise RuntimeError('Background manager is closing')
            job = self.get(task_id)
            if job['status'] not in ('cancelled', 'failed', 'interrupted') or job['iteration'] >= job['iterations']:
                raise ValueError('Only unfinished interrupted, cancelled or failed jobs can resume')
            count = self._db.execute("SELECT COUNT(*) FROM background_jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= 100:
                raise ValueError('Background queue is full')
            self._db.execute("UPDATE background_jobs SET status='queued',error=NULL,updated_at=? WHERE id=?", (time.time(), task_id))
            self._db.commit()
            self._event(task_id, 'task.queued', {'iteration': job['iteration'], 'summary': '从最近完成的迭代继续执行'})
            self._condition.notify_all()
        return self.get(task_id)

    def pause_dispatch(self, paused=True):
        """Pause admission without interrupting workers or losing queued tasks."""
        with self._condition:
            self._paused = bool(paused)
            self._condition.notify_all()

    def is_idle(self):
        with self._lock:
            return not self._active

    def _dispatch(self):
        while True:
            with self._condition:
                if self._closing:
                    return
                room = 0 if self._paused else self.max_workers - len(self._active)
                rows = self._db.execute("SELECT id FROM background_jobs WHERE status='queued' ORDER BY created_at LIMIT ?", (max(0, room),)).fetchall()
                for row in rows:
                    task_id = row['id']
                    self._db.execute("UPDATE background_jobs SET status='running',updated_at=? WHERE id=?", (time.time(), task_id))
                    self._db.commit()
                    state = {'cancel': threading.Event()}
                    thread = threading.Thread(target=self._run, args=(task_id, state), name='chaos-job-' + task_id[:8], daemon=True)
                    state['thread'] = thread
                    self._active[task_id] = state
                    thread.start()
                self._condition.wait(timeout=.5)

    def _run(self, task_id, state):
        def checkpoint():
            if state['cancel'].is_set() or self._closing:
                raise BackgroundCancelled('Background task cancelled')
        def emit(kind, payload):
            self._event(task_id, kind, payload)
        try:
            job = self.get(task_id)
            emit('task.started', {'iteration': job['iteration'], 'iterations': job['iterations'], 'summary': '后台智能体开始执行'})
            previous = job['output']
            for iteration in range(job['iteration'] + 1, job['iterations'] + 1):
                checkpoint()
                emit('task.iteration', {'iteration': iteration, 'iterations': job['iterations'], 'summary': '正在执行第 %s 轮迭代' % iteration})
                output = json_copy(self.worker(job['task'], job['agentId'], previous, checkpoint, emit))
                checkpoint()
                with self._lock:
                    self._db.execute('UPDATE background_jobs SET iteration=?,output=?,updated_at=? WHERE id=?',
                                     (iteration, json.dumps(output, ensure_ascii=False), time.time(), task_id))
                    self._db.commit()
                previous = output
                if iteration < job['iterations'] and state['cancel'].wait(job['interval']):
                    checkpoint()
            checkpoint()
            status, error, summary = 'completed', None, '所有迭代已完成'
        except BackgroundCancelled:
            status, error = ('interrupted', None) if self._closing else ('cancelled', None)
            summary = '任务已暂停，重启后可继续' if status == 'interrupted' else '后台任务已取消'
        except Exception as exc:
            status, error, summary = 'failed', type(exc).__name__, '本轮执行失败，可检查配置后恢复'
        finally:
            with self._condition:
                # An unexpected BaseException still leaves a recoverable state.
                status = locals().get('status', 'interrupted')
                error = locals().get('error', None)
                # Cancellation acceptance and terminal selection share the same
                # lock, including the interval after the last checkpoint.
                if state['cancel'].is_set() or self._closing:
                    status = 'interrupted' if self._closing else 'cancelled'
                    error = None
                self._db.execute('UPDATE background_jobs SET status=?,error=?,updated_at=? WHERE id=?', (status, error, time.time(), task_id))
                self._db.commit()
            try:
                emit('task.' + status, {'status': status, 'summary': locals().get('summary', '任务已中断')})
            finally:
                # Keep the worker active through the final event, but do not
                # hold the scheduling lock while calling an external observer.
                with self._condition:
                    self._active.pop(task_id, None)
                    self._condition.notify_all()

    def close(self, wait=False):
        with self._condition:
            self._closing = True
            for item in self._active.values():
                item['cancel'].set()
            self._condition.notify_all()
            if self._finalizer is None:
                threads = [item['thread'] for item in self._active.values()]
                def finish():
                    self._dispatcher.join()
                    for thread in threads:
                        thread.join()
                    with self._condition:
                        self._db.close()
                        self._db_closed = True
                        self._condition.notify_all()
                self._finalizer = threading.Thread(target=finish, name='chaos-background-close', daemon=True)
                self._finalizer.start()
            finalizer = self._finalizer
        if wait:
            finalizer.join(timeout=2)
        return not finalizer.is_alive()
