"""Stable, standard-library-only bootstrap, copied verbatim to a project's chaos.py."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import stat
import struct
import inspect
import sysconfig
import subprocess
import sys
import tempfile
import time
import signal

ENV_SCHEMA = 'chaos.environment/1'


def _directory(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError('Expected a regular directory: ' + str(path))
    return path


def selection(root):
    state = _directory(Path(root) / '.chaos')
    pointer = state / 'environment.json'
    if not pointer.exists() and not pointer.is_symlink():
        return {'schema': ENV_SCHEMA, 'current': 'base', 'previous': None}
    if pointer.is_symlink() or not pointer.is_file() or pointer.stat().st_size > 4096:
        raise ValueError('Invalid project environment pointer')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate environment pointer key')
            result[key] = value
        return result
    value = json.loads(pointer.read_text(encoding='utf-8'), object_pairs_hook=pairs)
    if type(value) is not dict or set(value) != {'schema', 'current', 'previous'} or value['schema'] != ENV_SCHEMA:
        raise ValueError('Unsupported project environment pointer')
    for name in ('current', 'previous'):
        key = value[name]
        if key is None and name == 'previous':
            continue
        if type(key) is not str or not (key == 'base' or re.fullmatch(r'env-[a-f0-9]{32}', key)):
            raise ValueError('Invalid environment identifier')
    if value['current'] == value['previous']:
        raise ValueError('Current and previous environments must differ')
    return value


def environment(root, key=None):
    state = _directory(Path(root) / '.chaos')
    key = key if key is not None else selection(root)['current']
    if key == 'base':
        return state
    if not re.fullmatch(r'env-[a-f0-9]{32}', key):
        raise ValueError('Invalid environment identifier')
    return _directory(_directory(state / 'environments') / key)


def dependency_id(runtime):
    """Dependencies are reusable only on the same interpreter ABI/platform/requirements."""
    identity = [str(sys.version_info[:3]), sys.implementation.cache_tag,
                platform.system(), platform.machine(), str(struct.calcsize('P') * 8), sysconfig.get_platform(),
                (runtime / 'requirements.txt').read_text(encoding='utf-8')]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def dependencies_ready(env):
    deps = _directory(env / 'deps')
    marker = deps / '.chaos-dependencies.json'
    if not deps.is_dir() or marker.is_symlink() or not marker.is_file():
        return False
    try:
        return json.loads(marker.read_text(encoding='utf-8')) == {'identity': dependency_id(env / 'runtime')}
    except (ValueError, OSError):
        return False


@contextmanager
def environment_lock(root, exclusive=False):
    """All launcher processes pin an environment; setup/replacement require exclusive use."""
    state = _directory(Path(root) / '.chaos')
    state.mkdir(mode=0o700, exist_ok=True)
    lock = state / 'environment.lock'
    if lock.is_symlink():
        raise ValueError('Environment lock cannot be a symlink')
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Invalid environment lock')
        if info.st_size == 0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Project environment is in use; stop its IDE/programs before setup or replacement') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def setup(root, reporter=None, validator=None):
    """Caller holds the exclusive environment lock; failed installs preserve old deps."""
    env = environment(root)
    env.mkdir(parents=True, exist_ok=True)
    requirements = env / 'runtime' / 'requirements.txt'
    items = (requirements.read_text(encoding='utf-8') if requirements.is_file() else
             '\n'.join(importlib.metadata.requires('chaos-intelligence') or []) + '\n')
    target, backup = env / 'deps', env / 'deps.previous'
    _directory(target)
    if backup.exists() or backup.is_symlink():
        raise ValueError('Dependency directory needs attention; preserve or restore deps.previous first')
    with tempfile.TemporaryDirectory(prefix='deps-stage-', dir=env) as folder:
        stage = Path(folder)
        req = stage / 'requirements.txt';req.write_text(items, encoding='utf-8')
        payload = stage / 'payload'
        command = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                   '--target', str(payload), '-r', str(req)]
        if reporter is None:
            result = subprocess.run(command)
            code = result.returncode
        else:
            reporter('install', '准备与当前 Python/系统匹配的项目依赖')
            # If pip is missing, bootstrap it in a disposable project-local venv.
            available = subprocess.run([sys.executable, '-m', 'pip', '--version'],
                                       capture_output=True, timeout=30).returncode == 0
            if not available:
                reporter('bootstrap', '当前 Python 没有 pip，创建临时项目安装环境')
                import venv
                bootstrap = stage / 'installer'
                venv.EnvBuilder(with_pip=True).create(bootstrap)
                command[0] = str(bootstrap / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
            # Pip can print private index URLs. Retain no raw provider/index logs;
            # report actual elapsed work, while installation diagnostics stay bounded.
            with tempfile.TemporaryFile() as log:
                child = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=os.name == 'posix')
                started = time.monotonic()
                try:
                    while True:
                        try:
                            code = child.wait(timeout=2)
                            break
                        except subprocess.TimeoutExpired:
                            elapsed = int(time.monotonic() - started)
                            reporter('install', '依赖下载／安装仍在进行（%s 秒）' % elapsed)
                            if elapsed >= 600:
                                raise TimeoutError('Dependency installation exceeded 600 seconds')
                finally:
                    if child.poll() is None:
                        if os.name == 'posix':
                            try: os.killpg(child.pid, signal.SIGTERM)
                            except ProcessLookupError: pass
                        else: child.terminate()
                        try: child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            if os.name != 'posix': child.kill()
                        finally:
                            if os.name == 'posix':
                                try: os.killpg(child.pid, signal.SIGKILL)
                                except ProcessLookupError: pass
                            child.wait()
        if code:
            if reporter: reporter('failed', '依赖安装失败（退出码 %s）；原环境保留，请检查网络、包源和 Python 支持' % code)
            return code
        payload.mkdir(exist_ok=True)
        if validator is not None:
            validator(payload)
        if requirements.is_file():
            (payload / '.chaos-dependencies.json').write_text(
                json.dumps({'identity': dependency_id(env / 'runtime')}), encoding='utf-8')
        if target.exists():
            target.rename(backup)
        try:
            payload.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    if reporter: reporter('installed', '依赖与自检通过，已切换项目依赖目录')
    else: print('Dependencies ready. Run: python chaos.py')
    return 0


def _launch(root):
    env = environment(root)
    runtime, deps = _directory(env / 'runtime'), _directory(env / 'deps')
    bundled = (root / '.chaos' / 'bundled-runtime').exists() or selection(root)['current'] != 'base'
    if bundled and not (runtime / 'chaos' / 'project.py').is_file():
        raise ValueError('Selected runtime is missing; restore the complete environment, never merge ZIPs into it')
    # A root-level chaos.py must never shadow the selected package.
    sys.path[:] = [p for p in sys.path if Path(p or os.getcwd()).resolve() != root]
    # Untagged legacy deps remain usable only with the original legacy environment.
    legacy_deps = selection(root)['current'] == 'base' and not (deps / '.chaos-dependencies.json').exists()
    if deps.is_dir() and (dependencies_ready(env) or legacy_deps):
        sys.path.insert(0, str(deps))
    if runtime.is_dir():
        sys.path.insert(0, str(runtime))
    if sys.argv[1:] == ['setup'] and runtime.is_dir() and not (runtime / 'chaos' / 'project_deployment.py').is_file():
        return setup(root)
    import chaos
    if runtime.exists():
        try:
            Path(chaos.__file__).resolve().relative_to(runtime.resolve())
        except ValueError:
            raise ValueError('Selected runtime is incomplete; refusing an unrelated installed Chaos package') from None
    if sys.argv[1:] == ['--version']:
        from chaos.version import __version__
        print(__version__);return 0
    from chaos.project import main
    lock_mode = 'exclusive' if sys.argv[1:2] in (['setup'], ['configure'], ['replace'], ['rollback']) else 'shared'
    kwargs = {'environment_locked': lock_mode} if 'environment_locked' in inspect.signature(main).parameters else {}
    return main(sys.argv[1:], root=root, **kwargs)


def launch():
    root = Path(__file__).resolve().parent
    if sys.version_info < (3, 9):
        print('Chaos requires Python 3.9 or newer.', file=sys.stderr);return 1
    try:
        with environment_lock(root, exclusive=sys.argv[1:2] in (['setup'], ['configure'], ['replace'], ['rollback'])):
            return _launch(root)
    except (ImportError, importlib.metadata.PackageNotFoundError) as error:
        print('Environment dependency unavailable: ' + str(error), file=sys.stderr)
        print('Prepare this directory with: python chaos.py setup', file=sys.stderr);return 1
    except (OSError, ValueError) as error:
        print('Project environment: ' + str(error), file=sys.stderr);return 1


if __name__ == '__main__':
    raise SystemExit(launch())
