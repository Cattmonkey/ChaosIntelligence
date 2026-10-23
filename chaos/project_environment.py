"""Local whole-environment installation and replacement, using the portable wire format."""
from contextlib import nullcontext
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from .portable import PackageError, _zip, _manifest, verify_portable
from .project_launcher import (ENV_SCHEMA, environment, selection, environment_lock,
                               dependency_id, dependencies_ready)


def _atomic(path, data):
    if path.is_symlink():
        raise PackageError('Refusing symlink destination: ' + str(path))
    fd, name = tempfile.mkstemp(prefix='.environment-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _select(root, current, previous):
    value = {'schema': ENV_SCHEMA, 'current': current, 'previous': previous}
    _atomic(root / '.chaos/environment.json', (json.dumps(value, indent=2) + '\n').encode())


def _read_runtime(source):
    source = Path(source).absolute()
    if source.is_dir():
        verify_portable(source)
        return {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*')
                if p.is_file() and '__pycache__' not in p.relative_to(source).parts}
    files = _zip(source)
    if 'manifest.json' in files:
        _manifest(files);return files
    prefix = '.chaos/runtime/'
    runtime = {name[len(prefix):]: data for name, data in files.items() if name.startswith(prefix)}
    if not runtime or 'chaos.py' not in files or 'chaos.project.json' not in files:
        raise PackageError('Expected a complete portable ZIP or project ZIP')
    _manifest(runtime)
    if files['chaos.py'] != runtime.get('chaos/project_launcher.py'):
        raise PackageError('Project launcher differs from its bundled runtime')
    return runtime


def _write_runtime(files, destination):
    destination.mkdir()
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _python_compatible(requirement):
    # Portable bootstrap must work before packaging or other third-party modules exist.
    # Unknown future specifier syntax fails closed, never silently ignores Requires-Python.
    if not requirement:
        raise PackageError('Runtime must declare Requires-Python')
    current = sys.version_info[:3]
    for clause in requirement.split(','):
        match = re.fullmatch(r'\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})(\.\*)?\s*', clause)
        if not match:
            raise PackageError('Unsupported Requires-Python; use a compatible Python/package: ' + requirement)
        op, number, wildcard = match.groups();parts = tuple(map(int, number.split('.')))
        if wildcard:
            if op not in ('==', '!='):
                raise PackageError('Invalid Requires-Python wildcard')
            ok = current[:len(parts)] == parts
            if op == '!=':ok = not ok
        else:
            target = parts + (0,) * (3 - len(parts))
            ok = {'>=': current >= target, '<=': current <= target, '>': current > target,
                  '<': current < target, '==': current == target, '!=': current != target}[op]
        if not ok:
            raise PackageError('This runtime requires Python ' + requirement + '; current is ' + sys.version.split()[0])


def _probe(runtime, manifest, *, legacy=False):
    _python_compatible(manifest['requiresPython'])
    required = ('project.py', 'project_launcher.py') if legacy else ('project.py', 'project_launcher.py', 'project_environment.py')
    if not all((runtime / 'chaos' / name).is_file() for name in required):
        raise PackageError('Runtime does not implement the replaceable project environment protocol')
    script = '''import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from chaos import Framework
from chaos.version import __version__
from chaos.project import Project
if sys.argv[3] != 'legacy':
    from chaos.project_launcher import ENV_SCHEMA
    assert ENV_SCHEMA == sys.argv[3]
assert __version__ == sys.argv[2]
assert Framework.evaluate('fn twice(f) { fn(x) { f(f(x)) } }; let inc = fn(x) { x + 1 }; twice(inc)(40)') == 42
print('chaos-environment-ok')
'''
    try:
        result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(runtime), manifest['version'], 'legacy' if legacy else ENV_SCHEMA],
                                cwd=runtime.parent, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise PackageError('Candidate runtime self-test timed out; active version is unchanged') from None
    if result.returncode or result.stdout.strip() != 'chaos-environment-ok':
        raise PackageError('Candidate runtime self-test failed; active version is unchanged')


def status(root):
    chosen = selection(root);env = environment(root)
    manifest = json.loads((env / 'runtime/manifest.json').read_text(encoding='utf-8'))
    return {'schema': ENV_SCHEMA, 'version': manifest['version'], 'root': str(root),
            'environment': chosen['current'], 'previous': chosen['previous'],
            'runtime': str(env / 'runtime'), 'dependenciesReady': dependencies_ready(env),
            'python': sys.version.split()[0]}


def _clean_previous(root, old):
    """Only a previously selected managed environment may be reclaimed, never user paths."""
    if not old or old == 'base' or old in (selection(root)['current'], selection(root)['previous']):
        return None
    try:
        folder = environment(root, old)
        if folder.exists():shutil.rmtree(folder)
    except (OSError, ValueError):
        return 'Previous environment is no longer selected, but could not be removed: ' + old
    return None


def _bootstrap_protocol(data):
    try:
        declarations = [ast.literal_eval(n.value) for n in ast.parse(data).body
                        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ENV_SCHEMA' for t in n.targets)]
        return declarations == [ENV_SCHEMA]
    except (SyntaxError, ValueError, TypeError):
        return False


def _replace(project, files, bootstrap=None):
    root = project.root;before = selection(root);manifest = _manifest(files)
    if bootstrap is None:
        # An old copied launcher cannot understand environment.json. Fail before staging.
        launcher = root / 'chaos.py'
        from .project import _regular
        data = _regular(launcher)
        if not _bootstrap_protocol(data):
            raise PackageError('Upgrade the project entry point with the new package: python NEW/chaos.py install PROJECT')
    from .updater import compare_versions
    current = json.loads((environment(root) / 'runtime/manifest.json').read_text(encoding='utf-8'))
    if compare_versions(manifest['version'], current['version']) < 0:
        raise PackageError('Refusing an older package; use rollback for the previous environment')
    parent = root / '.chaos/environments'
    if parent.is_symlink():raise PackageError('Environment directory cannot be a symlink')
    parent.mkdir(exist_ok=True)
    key = 'env-' + uuid.uuid4().hex;candidate = parent / key;candidate.mkdir()
    previous_launcher = None
    committed = False
    try:
        runtime = candidate / 'runtime';_write_runtime(files, runtime)
        _probe(runtime, manifest)
        old_env = environment(root)
        if dependencies_ready(old_env) and dependency_id(old_env / 'runtime') == dependency_id(runtime):
            # Never follow a user-created symlink into files outside the dependency environment.
            if any(p.is_symlink() for p in (old_env / 'deps').rglob('*')):
                raise PackageError('Dependencies contain symlinks; run setup in the new environment instead')
            shutil.copytree(old_env / 'deps', candidate / 'deps')
        if bootstrap is not None:
            previous_launcher = (root / 'chaos.py').read_bytes()
            _atomic(root / 'chaos.py', bootstrap)
        _select(root, key, before['current']);committed = True
    finally:
        if not committed:
            if previous_launcher is not None:_atomic(root / 'chaos.py', previous_launcher)
            shutil.rmtree(candidate)
    result = status(root)
    warning = _clean_previous(root, before['previous'])
    if warning:result['warning'] = warning
    result['action'] = 'replaced'
    if not result['dependenciesReady']:result['next'] = 'python chaos.py setup'
    return result


def replace(project, source, *, locked=False):
    from .project import project_lock
    with (nullcontext() if locked else environment_lock(project.root, exclusive=True)), project_lock(project):
        return _replace(project, _read_runtime(source))


def rollback(project, *, locked=False):
    from .project import project_lock
    with (nullcontext() if locked else environment_lock(project.root, exclusive=True)), project_lock(project):
        before = selection(project.root)
        if before['previous'] is None:raise PackageError('No previous environment is available')
        runtime = environment(project.root, before['previous']) / 'runtime'
        manifest = _manifest(_read_runtime(runtime));_probe(runtime, manifest, legacy=True)
        _select(project.root, before['previous'], before['current'])
        return {'action': 'rolled_back', **status(project.root)}


def install(source_runtime, destination):
    """Install into a host project, or migrate an unchanged legacy project bootstrap."""
    from .project import Project, init_project, project_files, project_lock, _regular
    files = _read_runtime(source_runtime);manifest = _manifest(files)
    root = Path(destination).expanduser().absolute()
    if root.is_symlink():raise PackageError('Project directory cannot be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'chaos.project.json').exists():
        project = Project(root)
        with environment_lock(root, exclusive=True), project_lock(project):
            launcher = _regular(root / 'chaos.py')
            if _bootstrap_protocol(launcher):
                # The version-independent bootstrap already implements the protocol.
                # Keep it verbatim, including user customization; only swap its environment.
                return _replace(project, files)
            old = environment(root) / 'runtime/chaos/project_launcher.py'
            if not old.is_file() or launcher != _regular(old):
                raise PackageError('Project chaos.py was customized; preserve it and migrate the bootstrap explicitly')
            return _replace(project, files, bootstrap=files['chaos/project_launcher.py'])
    for name in (*project_files(), '.chaos'):
        if (root / name).exists() or (root / name).is_symlink():
            raise PackageError('Refusing existing project path: ' + str(root / name))
    # Stage outside the target project; failed self-test leaves host files untouched.
    with tempfile.TemporaryDirectory(prefix='.chaos-install-', dir=root.parent) as folder:
        stage = Path(folder);_write_runtime(files, stage / 'runtime');_probe(stage / 'runtime', manifest)
        state = root / '.chaos';state.mkdir(mode=0o700)
        created = []
        try:
            shutil.move(str(stage / 'runtime'), str(state / 'runtime'))
            (state / 'bundled-runtime').write_text('chaos.project/1\n', encoding='utf-8')
            result = init_project(root);created = result['created']
        except BaseException:
            for name in created:(root / name).unlink()
            shutil.rmtree(state)
            raise
    return {'action': 'installed', **status(root)}
