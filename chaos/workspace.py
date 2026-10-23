"""Synchronize an editable source directory with one versioned Chaos service."""
import json
import os
from pathlib import Path
import tempfile
import uuid
try:import fcntl
except ImportError:fcntl=None
from contextlib import contextmanager
from .client import Client, ConflictError
from .modules import ProgramBundle, path_name

META = '.chaos-workspace.json'

class WorkspaceConflict(RuntimeError):
    def __init__(self, message, remote=None):
        self.remote = remote
        super().__init__(message)

class Workspace:
    def __init__(self, client, directory, metadata):
        self.client, self.directory, self.metadata = client, Path(directory).resolve(), metadata

    @classmethod
    def checkout(cls, client, project_id, directory):
        root=Path(directory)
        if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
            raise ValueError('Checkout requires a new or empty directory')
        remote=client.get_project(project_id)
        ProgramBundle(remote['source'],remote['files'],remote['entry'])
        root.mkdir(parents=True,exist_ok=True)
        obj=cls(client,root,{'server':client.base_url,'projectId':project_id,'version':remote['version'],'entry':remote['entry'],'files':{}})
        obj._install(remote,{})
        return obj

    @classmethod
    def open(cls, directory, client=None):
        root=Path(directory)
        if root.is_symlink():raise ValueError('Workspace root cannot be a symlink')
        root=root.resolve()
        p=root/META
        if p.is_symlink():raise ValueError('Workspace metadata cannot be a symlink')
        meta=json.loads(p.read_text(encoding='utf-8'))
        if set(meta)!={'server','projectId','version','entry','files'} or type(meta['version'])is not int or meta['version']<1:
            raise ValueError('Invalid workspace metadata')
        ProgramBundle(meta['files'][meta['entry']],meta['files'],meta['entry'])
        if not isinstance(meta['projectId'],str):raise ValueError('Invalid project id')
        client=client or Client(meta['server'])
        if client.base_url!=meta['server']:raise ValueError('Workspace belongs to another server')
        return cls(client,root,meta)

    def _path(self, name):
        if path_name(name)!=name or not name.endswith('.chaos') or name.startswith('std/') or any(part in ('.git','.chaos')or part.startswith('.chaos-replaced-')for part in Path(name).parts):
            raise ValueError('Invalid workspace module path')
        path=self.directory/name
        for part in (path,*path.parents):
            if part==self.directory:break
            if part.is_symlink():raise ValueError('Workspace symlinks are not allowed')
        try:path.resolve().relative_to(self.directory)
        except ValueError:raise ValueError('Workspace path escapes directory')from None
        return path

    def _local(self):
        files={}
        for folder, dirs, names in os.walk(self.directory,followlinks=False):
            dirs[:]=[d for d in dirs if not d.startswith('.chaos-replaced-') and d not in ('.git','.chaos')]
            if any((Path(folder)/d).is_symlink()for d in dirs):raise ValueError('Workspace symlinks are not allowed')
            for name in names:
                if name.endswith('.chaos'):
                    relative=str((Path(folder)/name).relative_to(self.directory))
                    path=self._path(relative)
                    if path.stat().st_size>400000:raise ValueError('Source file exceeds size limit')
                    files[relative]=path.read_text(encoding='utf-8')
                    if len(files)>64:raise ValueError('Too many source files')
        return files

    @staticmethod
    def _atomic(path,text):
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.chaos-write-',dir=str(path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8')as f:f.write(text);f.flush();os.fsync(f.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)

    def _conflict(self, remote, reason):
        backup=self.directory/'.chaos-conflict.json'
        if backup.is_symlink():raise ValueError('Conflict backup cannot be a symlink')
        self._atomic(backup,json.dumps({'reason':reason,'base':self.metadata,'remote':remote},ensure_ascii=False,indent=2))
        raise WorkspaceConflict(reason,remote)

    def _install(self, remote, expected):
        ProgramBundle(remote['source'],remote['files'],remote['entry'])
        for name in remote['files']:self._path(name)
        if self._local()!=expected:self._conflict(remote,'Local files changed while synchronizing')
        # Keep an undo snapshot for interrupted pulls and concurrent editor writes.
        backup=self.directory/'.chaos-before-pull.json'
        if backup.is_symlink():raise ValueError('Pull backup cannot be a symlink')
        recovery=self.directory/('.chaos-replaced-'+uuid.uuid4().hex)
        recovery.mkdir()
        self._atomic(backup,json.dumps({'metadata':self.metadata,'files':expected,'recoveryDirectory':str(recovery)},ensure_ascii=False))
        for name in sorted(set(expected)|set(remote['files'])):
            path=self._path(name)
            actual=path.read_text(encoding='utf-8')if path.exists()else None
            if actual!=expected.get(name):self._conflict(remote,'Local file changed during pull: '+name)
            desired=remote['files'].get(name)
            if actual==desired:continue
            previous=recovery/name;previous.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                os.replace(path,previous)
                if previous.is_symlink() or previous.read_text(encoding='utf-8')!=expected.get(name):
                    if not path.exists():os.link(previous,path)
                    self._conflict(remote,'Concurrent edit preserved in '+str(previous))
            elif actual is not None:self._conflict(remote,'File disappeared during pull: '+name)
            if name in remote['files']:
                path.parent.mkdir(parents=True,exist_ok=True)
                path=self._path(name)
                fd,tmp=tempfile.mkstemp(prefix='.chaos-source-',dir=str(path.parent))
                try:
                    with os.fdopen(fd,'w',encoding='utf-8')as stream:stream.write(desired);stream.flush();os.fsync(stream.fileno())
                    try:os.link(tmp,path)  # Exclusive install: never replace an editor's new file.
                    except FileExistsError:self._conflict(remote,'Concurrent edit preserved: '+name)
                finally:os.unlink(tmp)
        if self._local()!=remote['files']:self._conflict(remote,'Local files changed before sync completed')
        meta={'server':self.client.base_url,'projectId':remote['id'],'version':remote['version'],'entry':remote['entry'],'files':remote['files']}
        self._atomic(self.directory/META,json.dumps(meta,ensure_ascii=False,indent=2))
        self.metadata=meta

    def status(self):
        return {'projectId':self.metadata['projectId'],'version':self.metadata['version'],'entry':self.metadata['entry'],'localChanged':self._local()!=self.metadata['files'],'server':self.client.base_url}

    @contextmanager
    def _locked(self):
        path=self.directory/'.chaos-sync.lock'
        if path.is_symlink():raise ValueError('Workspace lock cannot be a symlink')
        with path.open('a')as lock:
            try:
                if fcntl:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                else:
                    import msvcrt
                    lock.seek(0);lock.write('0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise WorkspaceConflict('Another sync is active')from None
            try:
                self.metadata=Workspace.open(self.directory,self.client).metadata
                yield
            finally:
                if fcntl:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
                else:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

    def sync(self):
        with self._locked():return self._sync()

    def _sync(self):
        local=self._local();base=self.metadata
        remote=self.client.get_project(base['projectId'])
        changed=local!=base['files']
        if remote['version']<base['version']:self._conflict(remote,'Server version regressed; check server/database identity')
        if remote['version']!=base['version']:
            if changed:self._conflict(remote,'Both local files and server changed; resolve explicitly')
            self._install(remote,local)
            return {'status':'pulled','version':remote['version']}
        if not changed:return {'status':'unchanged','version':base['version']}
        if base['entry']not in local:raise ValueError('Entry file is missing')
        ProgramBundle(local[base['entry']],local,base['entry'])
        if self._local()!=local:self._conflict(remote,'Local files changed before publishing')
        draft={**remote,'source':local[base['entry']],'files':local,'entry':base['entry']}
        try:saved=self.client.save_project(draft,from_view='source')
        except ConflictError:
            self._conflict(self.client.get_project(base['projectId']),'Server changed before publishing')
        # Do not overwrite new local input while the request was in flight.
        meta={'server':self.client.base_url,'projectId':saved['id'],'version':saved['version'],'entry':saved['entry'],'files':saved['files']}
        self._atomic(self.directory/META,json.dumps(meta,ensure_ascii=False,indent=2));self.metadata=meta
        return {'status':'pushed','version':saved['version']}

    def run(self,value=None,*,timeout=60):
        with self._locked():
            self._sync()
            if self._local()!=self.metadata['files']:raise WorkspaceConflict('Files changed after sync; run again after saving')
            handle=self.client.start(self.metadata['projectId'],self.metadata['version'],value,mode='source',trace=True)
        return handle.result(timeout=timeout)
