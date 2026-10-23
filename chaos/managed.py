"""Stable, stdlib-only managed launcher. Copied outside replaceable releases."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from contextlib import contextmanager
import threading

RESTART_EXIT = 75


def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'-',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w')as f:json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def current(root):
    value=json.loads((Path(root)/'current.json').read_text())
    if not isinstance(value,dict)or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?',value.get('version','')):raise RuntimeError('Invalid managed release pointer')
    return value


def release_python(root,version):
    if not isinstance(version,str)or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?',version):raise RuntimeError('Invalid release version')
    return Path(root)/'releases'/version/'venv'/('Scripts/python.exe'if os.name=='nt'else 'bin/python')


def write_launcher(root):
    """One-time enrollment only; the launcher ABI is versioned independently."""
    root=Path(root).expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).read_text();target=root/'launcher.py'
    if not target.exists():target.write_text(source,encoding='utf-8')
    bin_dir=root/'bin';bin_dir.mkdir(exist_ok=True)
    if os.name=='nt':
        wrapper=bin_dir/'chaos.cmd';wrapper.write_text('@echo off\r\npy -3 "'+str(target)+'" %*\r\n')
    else:
        wrapper=bin_dir/'chaos'
        wrapper.write_text('#!/usr/bin/env python3\nimport runpy\nrunpy.run_path('+repr(str(target))+',run_name="__main__")\n');wrapper.chmod(0o755)
    return str(wrapper)


def _server_args(args):
    if not args:return True
    if any(v in args for v in ('--version','--help','-h')):return False
    if args[0]=='serve':return True
    return args[0].startswith('-') and not any(v in args for v in ('--version','--help','-h'))


def _health_url(args):
    host,port='127.0.0.1','8765'
    for i,arg in enumerate(args):
        if arg=='--host'and i+1<len(args):host=args[i+1]
        if arg=='--port'and i+1<len(args):port=args[i+1]
        if arg.startswith('--host='):host=arg.split('=',1)[1]
        if arg.startswith('--port='):port=arg.split('=',1)[1]
    if host in ('0.0.0.0','::'):host='127.0.0.1'
    if ':'in host and not host.startswith('['):host='['+host+']'
    return 'http://'+host+':'+port+'/api/v1/health'


def _healthy(proc,url,version,instance,timeout):
    deadline=time.monotonic()+timeout
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic()<deadline:
        if proc.poll()is not None:return False
        try:
            with opener.open(url,timeout=.5)as response:body=json.loads(response.read(10000))
            if body.get('version')==version and body.get('instanceId')==instance:return True
        except (OSError,ValueError):pass
        time.sleep(.1)
    return False


@contextmanager
def file_lock(path):
    """Same cross-process update lock as the package updater (stdlib ABI 1)."""
    with Path(path).open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.write(b'0'); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle, fcntl.LOCK_UN)


def _stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()


def _supervise(root,args,health_timeout):
    server=_server_args(args);attempted=set()
    while True:
        pointer=current(root);version=pointer['version'];python=release_python(root,version)
        if not python.is_file():raise RuntimeError('Managed release is missing its Python runtime')
        env=os.environ.copy();env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
        instance=uuid.uuid4().hex
        env.update(CHAOS_MANAGED_HOME=str(root),CHAOS_SUPERVISED='1',CHAOS_INSTANCE_ID=instance)
        proc=subprocess.Popen([str(python),'-I','-m','chaos',*args],env=env)
        try:
            if server:
                if not _healthy(proc,_health_url(args),version,instance,health_timeout):
                    _stop(proc)
                    previous=pointer.get('previousVersion')
                    if previous and version not in attempted and release_python(root,previous).is_file():
                        attempted.add(version)
                        with file_lock(root/'.update.lock'):
                            latest=current(root)
                            if latest['version']!=version:continue
                            atomic_json(root/'current.json',{**pointer,'version':previous,'previousVersion':None,'rolledBackFrom':version})
                            atomic_json(root/'health.json',{'version':previous,'ok':False,'rolledBackFrom':version,'time':time.time(),'message':'New release failed startup health check; restored previous release'})
                        continue
                    atomic_json(root/'health.json',{'version':version,'ok':False,'time':time.time(),'message':'Service failed startup health check'})
                    return proc.returncode or 1
                atomic_json(root/'health.json',{'version':version,'ok':True,'time':time.time(),'instanceId':instance,'rolledBackFrom':pointer.get('rolledBackFrom')})
            code=proc.wait()
        finally:
            # Includes startup Ctrl-C and SIGTERM from service managers.
            _stop(proc)
        if code!=RESTART_EXIT or not server:return code


def supervise(root,args,*,health_timeout=30):
    root=Path(root).expanduser().resolve()
    old_handlers={}
    def stop_signal(signum, frame): raise KeyboardInterrupt()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig]=signal.signal(sig,stop_signal)
    try:
        if _server_args(args):
            with file_lock(root/'.supervisor.lock'):
                return _supervise(root,args,health_timeout)
        return _supervise(root,args,health_timeout)
    except KeyboardInterrupt: return 130
    finally:
        for sig,handler in old_handlers.items():signal.signal(sig,handler)


def service_main(config_path):
    """OS-owned entry: one canonical profile, no second supervisor loop.

    Source uses an explicit checkout path under isolated Python; managed uses
    the existing enrolled stable launcher and its release/rollback supervisor.
    """
    import runpy
    config=Path(config_path)
    if not config.is_absolute() or config.is_symlink():
        raise RuntimeError('Service configuration must be an absolute regular file')
    for part in config.parents:
        if part.is_symlink():raise RuntimeError('Service configuration contains a symbolic link')
    value=json.loads(config.read_text(encoding='utf-8'))
    if value.get('schema')!='chaos.service.install/1' or value.get('uid')!=os.getuid():
        raise RuntimeError('Invalid service installation owner')
    nonce=value.get('nonce','')
    if not re.fullmatch(r'[0-9a-f]{32}',nonce):raise RuntimeError('Invalid service installation identity')
    spec=value.get('spec',{})
    expected={'name','profile','python','cwd','db','state_dir','config','managed_home','host','port'}
    if type(spec)is not dict or set(spec)!=expected:raise RuntimeError('Invalid service specification')
    import hashlib
    encoded=(json.dumps(spec,sort_keys=True,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    if hashlib.sha256(encoded).hexdigest()!=value.get('specSha256'):
        raise RuntimeError('Service specification identity mismatch')
    if os.environ.get('CHAOS_SERVICE_ID')!=nonce:raise RuntimeError('Service OS identity mismatch')
    for key in ('python','cwd','db','state_dir','config'):
        path=Path(spec[key])
        if not path.is_absolute()or'..'in path.parts:raise RuntimeError('Service paths must be absolute')
        for part in (path,*path.parents):
            if part.is_symlink():raise RuntimeError('Service paths contain a symbolic link')
    os.chdir(spec['cwd'])
    os.environ.pop('PYTHONPATH',None);os.environ.pop('PYTHONHOME',None)
    args=['serve','--host',spec['host'],'--port',str(spec['port']),'--db',spec['db'],
          '--config',spec['config'],'--state-dir',spec['state_dir']]
    if spec['profile']=='managed':
        root=Path(spec['managed_home'])
        if not root.is_absolute()or'..'in root.parts:raise RuntimeError('Invalid managed service home')
        launcher=root/'launcher.py'
        for part in (launcher,*launcher.parents):
            if part.is_symlink():raise RuntimeError('Managed service launcher contains a symbolic link')
        if not launcher.is_file():raise RuntimeError('Managed service launcher is missing')
        # Keep the enrolled launcher ABI, exit75 restart and startup rollback.
        sys.argv=[str(root/'launcher.py'),*args]
        runpy.run_path(str(root/'launcher.py'),run_name='__main__')
        return 0
    if spec['profile']!='source'or spec['managed_home']is not None:
        raise RuntimeError('Invalid source service profile')
    # Each source service gets a stable private updater namespace. Set the
    # existing supported home before importing Chaos: never read a user's global
    # app updater/feed, and never inherit a managed supervisor claim.
    import stat
    if not isinstance(spec['name'],str)or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}',spec['name']):
        raise RuntimeError('Invalid source service name')
    updater_namespace=config.parent.parent/'updaters'
    updater_home=updater_namespace/spec['name']
    for directory in (updater_namespace,updater_home):
        for part in (directory,*directory.parents):
            if part.is_symlink():raise RuntimeError('Source updater path contains a symbolic link')
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=directory.stat()
        if not stat.S_ISDIR(info.st_mode)or info.st_uid!=os.getuid()or stat.S_IMODE(info.st_mode)!=0o700:
            raise RuntimeError('Source updater directory must be privately owned mode0700')
    updater_state=updater_home/'updater.json'
    if updater_state.is_symlink():raise RuntimeError('Source updater state contains a symbolic link')
    if updater_state.exists():
        info=updater_state.stat()
        if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid()or stat.S_IMODE(info.st_mode)!=0o600:
            raise RuntimeError('Source updater state must be privately owned mode0600')
    os.environ['CHAOS_MANAGED_HOME']=str(updater_home)
    os.environ.pop('CHAOS_SUPERVISED',None)
    os.environ['CHAOS_INSTANCE_ID']=uuid.uuid4().hex
    sys.path.insert(0,spec['cwd'])
    sys.argv=['chaos',*args]
    runpy.run_module('chaos',run_name='__main__',alter_sys=True)
    return 0


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--service-spec':
        return service_main(sys.argv[2])
    root=Path(__file__).resolve().parent
    return supervise(root,sys.argv[1:])


if __name__=='__main__':sys.exit(main())
