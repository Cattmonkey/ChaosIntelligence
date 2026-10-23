"""A relocatable local directory around the original CLI and Workspace protocol."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import webbrowser

MARKER='chaos.project.json'
SCHEMA='chaos.project/1'


class ProjectError(ValueError):
    pass


def _json(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ProjectError('Duplicate project metadata key')
            result[key]=value
        return result
    return json.loads(text,object_pairs_hook=pairs)


def _regular(path,limit=1000000):
    if path.is_symlink():raise ProjectError('Project file cannot be a symlink: '+str(path))
    status=path.stat()
    if not stat.S_ISREG(status.st_mode)or status.st_size>limit:raise ProjectError('Invalid project file: '+str(path))
    return path.read_bytes()


def _root(directory):
    path=Path(directory).expanduser().absolute()
    if path.is_symlink()or not path.is_dir():raise ProjectError('Project directory must be an existing nonsymlink directory')
    return path.resolve()


def project_files():
    """The same three editable templates used by init and project packaging."""
    return {MARKER:(json.dumps({'schema':SCHEMA,'entry':'main.chaos'},indent=2)+'\n').encode(),
            'main.chaos':b'42\n',
            'chaos.py':Path(__file__).with_name('project_launcher.py').read_bytes()}


def _new_file(path,data):
    with path.open('xb')as stream:
        status=os.fstat(stream.fileno())
        try:stream.write(data);stream.flush();os.fsync(stream.fileno())
        except BaseException:
            try:
                current=path.lstat()
                if current.st_dev==status.st_dev and current.st_ino==status.st_ino:path.unlink()
            except OSError:pass
            raise
    return status


def init_project(directory):
    path=Path(directory).expanduser().absolute()
    if path.is_symlink():raise ProjectError('Project directory cannot be a symlink')
    if path.exists()and not path.is_dir():raise ProjectError('Project destination must be a directory')
    templates=project_files()
    # Never mix editable user files into an immutable portable payload root.
    if (path/'manifest.json').exists()and(path/'chaos').is_dir():raise ProjectError('Initialize outside the immutable runtime directory')
    for name in templates:
        target=path/name
        if target.exists()or target.is_symlink():raise ProjectError('Refusing existing project file: '+str(target))
    created=not path.exists();written=[]
    try:
        path.mkdir(parents=True,exist_ok=True)
        path=_root(path)
        for name in('main.chaos','chaos.py',MARKER):
            target=path/name;status=_new_file(target,templates[name]);written.append((target,status.st_dev,status.st_ino))
    except BaseException:
        for target,device,inode in reversed(written):
            try:
                status=target.lstat()
                if status.st_dev==device and status.st_ino==inode and not target.is_symlink():target.unlink()
            except FileNotFoundError:pass
        if created:
            try:path.rmdir()
            except OSError:pass
        raise
    return {'schema':SCHEMA,'root':str(path),'entry':'main.chaos','created':list(templates)}


class Project:
    def __init__(self,directory):
        self.root=_root(directory)
        data=_json(_regular(self.root/MARKER,16384).decode('utf-8'))
        if type(data)is not dict or set(data)!={'schema','entry'}or data['schema']!=SCHEMA:raise ProjectError('Invalid chaos.project.json')
        from .modules import path_name
        entry=data['entry']
        if type(entry)is not str or path_name(entry)!=entry or not entry.endswith('.chaos')or entry.startswith('std/')or any(part in('.chaos','.git')for part in Path(entry).parts):raise ProjectError('Invalid project entry')
        self.entry=entry;self.path(entry)
    def path(self,name):
        from .modules import path_name
        if type(name)is not str or path_name(name)!=name:raise ProjectError('Project path must be canonical and relative')
        target=self.root/name
        current=self.root
        for part in Path(name).parts:
            current=current/part
            if current.is_symlink():raise ProjectError('Project symlinks are not allowed')
        try:target.resolve().relative_to(self.root)
        except ValueError:raise ProjectError('Project path escapes directory')from None
        return target
    def state(self):
        target=self.path('.chaos')
        target.mkdir(mode=0o700,exist_ok=True)
        if not target.is_dir():raise ProjectError('.chaos must be a directory')
        return target


def discover(directory=None):
    current=_root(directory or Path.cwd())
    for path in(current,*current.parents):
        marker=path/MARKER
        if marker.exists()or marker.is_symlink():return Project(path)
    raise ProjectError('No chaos.project.json found; run chaos project init DIRECTORY')


def select_project(directory=None, root=None):
    """Resolve an explicit selection without crossing a bound launcher's root.

    A directory launcher has already selected its runtime and environment lock.
    Only the installed global CLI may select a different project directory.
    """
    project = Project(directory or root) if directory or root else discover()
    if root is not None and project.root != Path(root).expanduser().resolve():
        raise ProjectError('This launcher is bound to its own project; use the target directory chaos.py or global chaos project COMMAND --directory DIR')
    return project


@contextmanager
def working_directory(root):
    previous=Path.cwd();os.chdir(root)
    try:yield
    finally:os.chdir(previous)


@contextmanager
def project_lock(project):
    path=project.state()/'ide.lock'
    nofollow=getattr(os,'O_NOFOLLOW',0)
    try:fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|nofollow,0o600)
    except FileExistsError:fd=os.open(path,os.O_RDWR|nofollow)
    try:
        status=os.fstat(fd)
        if not stat.S_ISREG(status.st_mode)or status.st_nlink!=1:raise ProjectError('Invalid project IDE lock')
        try:
            if os.name=='nt':
                import msvcrt
                if not status.st_size:os.write(fd,b'0')
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:raise ProjectError('Another project IDE is already active')from error
        try:yield
        finally:
            if os.name=='nt':
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:fcntl.flock(fd,fcntl.LOCK_UN)
    finally:os.close(fd)


class LocalProjectFacade:
    """Only adapt the original SDK's CAS error to the Workspace client contract."""
    base_url='local'
    def __init__(self,app):self.app=app
    def get_project(self,project_id):return self.app.get_project(project_id)
    def save_project(self,project,*,from_view=None):
        from .store import Conflict
        from .client import ConflictError
        try:return self.app.save_project(project,from_view=from_view)
        except Conflict as error:raise ConflictError(409,'conflict',str(error))from error


class DirectoryIDE:
    def __init__(self,directory):
        self.project=Project(directory);self.app=self.workspace=self.server=self.watcher=None
        self.stop=threading.Event();self.sync_error=None;self._lock=None
    def open(self):
        if self.app is not None:raise ProjectError('Project IDE is already open')
        from .framework import Framework
        from .settings import Settings
        from .workspace import Workspace,META
        state=self.project.state();database=self.project.path('.chaos/project.sqlite3');metadata=self.project.path(META)
        self._lock=project_lock(self.project);self._lock.__enter__()
        try:
            if database.exists()!=metadata.exists():raise ProjectError('Project database/workspace metadata pair is incomplete; reconcile explicitly before opening')
            for path in(database,metadata,self.project.path('.chaos/settings.json')):
                if path.exists():_regular(path,128*1024*1024)
            initial=None;files=None
            if not metadata.exists():
                # Reject unfinished source before creating any persistent database.
                initial=Workspace(None,self.project.root,{'server':'local','projectId':'workspace','version':1,'entry':self.project.entry,'files':{}})
                files=initial._local()
                from .modules import ProgramBundle
                ProgramBundle(files[self.project.entry],files,self.project.entry)
            with working_directory(self.project.root):
                settings_path=self.project.path('.chaos/settings.json')
                settings=Settings(settings_path)
                explicit=_json(_regular(settings_path).decode('utf-8'))if settings_path.exists()else{}
                if 'workspace'not in explicit.get('runtime',{}):
                    # Persisted future settings edits must remain relocatable.
                    settings._data['runtime']['workspace']='.chaos/tasks'
                self.app=Framework(database,settings=settings)
            facade=LocalProjectFacade(self.app)
            if metadata.exists():self.workspace=Workspace.open(self.project.root,facade)
            else:
                self.workspace=initial;self.workspace.client=facade
                remote=self.app.get_project('workspace')
                remote.update(source=files[self.project.entry],files=files,entry=self.project.entry,name=(self.project.root.name or 'Project')[:120])
                saved=facade.save_project(remote,from_view='source')
                if self.workspace._local()!=files:self.workspace._conflict(saved,'Local files changed while initializing project')
                data={'server':'local','projectId':'workspace','version':saved['version'],'entry':saved['entry'],'files':saved['files']}
                _new_file(metadata,(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())
                self.workspace=Workspace.open(self.project.root,facade)
            self.sync()
            return self
        except BaseException:
            # A failed drain retains the app and lock; never expose an owned store.
            self.close()
            raise
    def sync(self):
        from .workspace import WorkspaceConflict
        current=Project(self.project.root)
        if current.entry!=self.project.entry:raise ProjectError('Project entry changed; stop and reopen to reconcile explicitly')
        remote=self.app.get_project(self.workspace.metadata['projectId'])
        if remote['entry']!=self.project.entry:raise WorkspaceConflict('IDE entry differs from chaos.project.json; reconcile the entry explicitly',remote)
        return self.workspace.sync()
    def start_sync(self,interval=.5):
        if not .1<=interval<=60:raise ProjectError('Sync interval must be between 0.1 and 60 seconds')
        def watch():
            from .language import LanguageError
            waiting=None
            while not self.stop.wait(interval):
                try:
                    result=self.sync()
                    if waiting is not None or result['status']!='unchanged':
                        print(json.dumps({'type':'project.synced',**result},ensure_ascii=False),flush=True)
                    waiting=None
                except LanguageError as error:
                    if error.code=='ownership_error':
                        self.sync_error=error;self.stop.set()
                        print(json.dumps({'type':'project.sync_stopped','error':str(error),'root':str(self.project.root)},ensure_ascii=False),file=sys.stderr,flush=True)
                        return
                    signature=(type(error).__name__,error.code,str(error))
                    if signature!=waiting:
                        print(json.dumps({'type':'project.sync_waiting','error':str(error),'code':error.code},ensure_ascii=False),file=sys.stderr,flush=True)
                    waiting=signature
                except Exception as error:
                    self.sync_error=error;self.stop.set()
                    print(json.dumps({'type':'project.sync_stopped','error':str(error),'root':str(self.project.root)},ensure_ascii=False),file=sys.stderr,flush=True)
                    return
        self.watcher=threading.Thread(target=watch,name='chaos-project-sync',daemon=False);self.watcher.start()
    def close(self):
        self.stop.set()
        if self.watcher is not None:
            self.watcher.join(15)
            if self.watcher.is_alive():raise TimeoutError('Project sync has not drained; IDE ownership is retained')
        if self.server is not None:
            self.server.server_close();self.server=None
        if self.app is not None:self.app.close(timeout=15);self.app=None
        if self._lock is not None:self._lock.__exit__(None,None,None);self._lock=None
    def __enter__(self):return self.open()
    def __exit__(self,*args):self.close()


def serve(project,*,host='127.0.0.1',port=0,no_browser=False,interval=.5):
    from .server import make_server
    with working_directory(project.root),DirectoryIDE(project.root)as ide:
        state=project.state()
        ide.server=make_server(host,port,framework=ide.app,state_dir=state/'control',update_home=state/'updates')
        ide.start_sync(interval)
        url='http://'+host+':'+str(ide.server.server_port)
        print(json.dumps({'type':'project.ready','url':url,'root':str(project.root)},ensure_ascii=False),flush=True)
        if not no_browser:webbrowser.open(url)
        installed=threading.current_thread()is threading.main_thread();previous=None
        def terminate(signum,frame):raise KeyboardInterrupt()
        if installed:previous=signal.signal(signal.SIGTERM,terminate)
        try:ide.server.serve_forever()
        except KeyboardInterrupt:pass
        finally:
            if installed:signal.signal(signal.SIGTERM,previous)
    return 0


def _delegate(project,args):
    from . import cli
    old_argv=sys.argv;old_config=os.environ.get('CHAOS_CONFIG')
    state=project.state()
    try:
        os.environ['CHAOS_CONFIG']=str(project.path('.chaos/settings.json'))
        file = args.file or project.entry
        source = getattr(args, 'source', None)
        if args.action == 'repl' and args.empty:
            sys.argv = ['chaos', 'repl']
        elif args.action == 'run' and source is not None:
            sys.argv = ['chaos', 'run', '-c', source]
        elif file == '-':
            sys.argv = ['chaos', args.action, '-']
        else:
            sys.argv=['chaos',args.action,str(project.path(file)),'--root',str(project.root)]
        if args.action=='run':
            sys.argv+=['--input-json',args.input_json,'--db',str(project.path('.chaos/cli.sqlite3'))if args.persistent else':memory:']
            if args.trace:sys.argv.append('--trace')
            if args.stream:sys.argv.append('--stream')
            if args.interactive:sys.argv.append('--interactive')
            for capability in args.capability:sys.argv+=['--capability',capability]
        elif args.action=='fmt':sys.argv+=['--line-width',str(args.line_width),'--indent-size',str(args.indent_size),'--language-version',str(args.language_version)]
        if args.action in ('run','repl') and args.config:
            sys.argv += ['--config', args.config]
        with working_directory(project.root):return cli.main()
    finally:
        sys.argv=old_argv
        if old_config is None:os.environ.pop('CHAOS_CONFIG',None)
        else:os.environ['CHAOS_CONFIG']=old_config


def main(argv=None,root=None,environment_locked=False):
    # Dispatch the selected runtime's deployment flow, including through older
    # stable launchers. Keep its options out of the ordinary run/IDE parser.
    incoming = list(sys.argv[1:] if argv is None else argv)
    if incoming and (incoming[0] == '-c' or incoming[0] == '-' or incoming[0].endswith('.chaos')):
        incoming = ['run', *incoming]
    if incoming and incoming[0] == 'docs':
        from .documentation import main as documentation_main
        return documentation_main(incoming[1:])
    if incoming and incoming[0] in ('ask', 'assist'):
        from . import assistant_cli, command_intelligence
        if incoming[0] == 'ask':
            parser = command_intelligence.ask_parser('chaos project ask')
            parser.add_argument('--directory', help='Local project directory')
            args = parser.parse_args(incoming[1:])
        else:
            args = assistant_cli.parse_args(incoming[1:], prog='chaos project assist', directory=True)
        try:
            project = select_project(args.directory, root)
            with working_directory(project.root):
                if incoming[0] == 'assist':
                    return assistant_cli.run(args)
                # Explicit CLI config can override this same project default.
                old_config = os.environ.get('CHAOS_CONFIG')
                try:
                    os.environ['CHAOS_CONFIG'] = str(project.path('.chaos/settings.json'))
                    return command_intelligence.ask(args)
                finally:
                    if old_config is None: os.environ.pop('CHAOS_CONFIG', None)
                    else: os.environ['CHAOS_CONFIG'] = old_config
        except (OSError, ValueError, RuntimeError) as error:
            print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr); return 1
    if incoming and incoming[0] in ('configure', 'setup', 'doctor'):
        from .project_deployment import main as deployment_main
        return deployment_main(root, incoming, environment_locked=environment_locked)
    parser=argparse.ArgumentParser(prog='chaos project',description=__doc__,epilog='Also: -c SOURCE, FILE.chaos, ask QUESTION [--directory DIR --role ROLE --stream], assist --help, setup/configure/doctor [--directory DIR], docs list/show. No arguments opens the IDE.')
    commands=parser.add_subparsers(dest='action',required=True)
    init=commands.add_parser('init');init.add_argument('directory')
    install=commands.add_parser('install',help='Copy the clean environment into another project');install.add_argument('directory')
    replace=commands.add_parser('replace',help='Validate and select a complete new project or portable ZIP');replace.add_argument('archive');replace.add_argument('--directory')
    for action in ('rollback','status'):
        commands.add_parser(action).add_argument('--directory')
    for action in('run','check','fmt','repl'):
        command=commands.add_parser(action);command.add_argument('file',nargs='?');command.add_argument('--directory')
        if action in ('run','repl'):
            command.add_argument('--config', help='Explicit settings file; default is this project settings')
        if action=='repl':
            command.add_argument('--empty',action='store_true',help='Start a fresh REPL without loading project entry')
        if action=='run':
            command.add_argument('-c',dest='source',help='Inline source (std/* imports only)')
            command.add_argument('--input-json',default='null');command.add_argument('--persistent',action='store_true')
            command.add_argument('--trace',action='store_true');command.add_argument('--stream',action='store_true');command.add_argument('--interactive',action='store_true');command.add_argument('--capability',action='append',default=[])
        elif action=='fmt':
            command.add_argument('--line-width',type=int,default=88);command.add_argument('--indent-size',type=int,default=2);command.add_argument('--language-version',type=int,choices=(1,2),default=2)
    ide=commands.add_parser('ide');ide.add_argument('--directory');ide.add_argument('--host',default='127.0.0.1');ide.add_argument('--port',type=int,default=0);ide.add_argument('--no-browser',action='store_true');ide.add_argument('--interval',type=float,default=.5)
    args=parser.parse_args(incoming if incoming else ['ide'])
    if args.action == 'repl' and args.empty and args.file:
        parser.error('--empty cannot preload a file')
    if args.action == 'run' and args.source is not None and args.file:
        parser.error('choose a file or -c, not both')
    from .language import LanguageError
    try:
        if args.action=='init':print(json.dumps(init_project(args.directory)));return 0
        if args.action=='install':
            from .project_environment import install
            from .project_launcher import environment
            runtime=environment(Path(root))/'runtime' if root else Path(__file__).resolve().parent.parent
            print(json.dumps(install(runtime,args.directory),ensure_ascii=False));return 0
        project=select_project(args.directory, root)
        if args.action in ('replace','rollback','status'):
            from . import project_environment as env
            locked=environment_locked and root is not None and project.root==Path(root).resolve()
            result=(env.replace(project,args.archive,locked=locked) if args.action=='replace' else
                    env.rollback(project,locked=locked) if args.action=='rollback' else env.status(project.root))
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if args.action=='ide':return serve(project,host=args.host,port=args.port,no_browser=args.no_browser,interval=args.interval)
        return _delegate(project,args)
    except (OSError,ValueError,RuntimeError,TimeoutError,KeyError,LanguageError)as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1
