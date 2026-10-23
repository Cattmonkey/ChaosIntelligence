"""Project-local environment preparation, observable checks and durable preferences.

This module imports only the standard library until dependencies have been checked.
Self-tests execute the selected runtime in a separate isolated interpreter, never the
user's entry point. The deployment receipt contains no model credentials.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import struct
import subprocess
import sys
import sysconfig
import tempfile
import time

from . import project_launcher as launcher

SCHEMA = 'chaos.deployment/1'
REPORT_SCHEMA = 'chaos.deployment-report/1'
DEPENDENCIES = ('openai', 'cryptography', 'packaging', 'markdown_it')


class Reporter:
    """ANSI only for interactive terminals; logs describe completed real stages."""
    def __init__(self, *, json_mode=False, no_color=False, stream=None):
        self.stream = stream if stream is not None else (sys.stderr if json_mode else sys.stdout)
        self.color = not (json_mode or no_color or 'NO_COLOR' in os.environ) and self.stream.isatty()
        self.started = time.monotonic()
        self.index = 0
        self.total = 0

    def stage(self, number, total, message):
        self.index, self.total = number, total
        self('stage', message)

    def __call__(self, kind, message):
        # External diagnostics must never inject terminal control sequences.
        clean = ''.join(c if c.isprintable() else ' ' for c in str(message))
        elapsed = time.monotonic() - self.started
        label = {'stage': '准备', 'passed': '通过', 'failed': '失败', 'warning': '注意',
                 'install': '安装', 'installed': '完成', 'bootstrap': '环境'}.get(kind, str(kind))
        prefix = '[%s/%s] %s · %.1fs' % (self.index, self.total, label, elapsed)
        if self.color:
            color = '31' if kind == 'failed' else '33' if kind == 'warning' else '32' if kind in ('passed', 'installed') else '36'
            prefix = '\x1b[' + color + 'm' + prefix + '\x1b[0m'
        print(prefix + '  ' + clean, file=self.stream, flush=True)


def _state_path(root):
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Project directory must be an existing nonsymlink directory')
    state = launcher._directory(root / '.chaos')
    path = state / 'deployment.json'
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('Deployment state must be a regular nonsymlink file')
    if path.exists() and (path.stat().st_nlink != 1 or path.stat().st_size > 100000):
        raise ValueError('Invalid deployment state file')
    return path


def read_state(root):
    path = _state_path(root)
    if not path.exists():
        return None
    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate deployment state key')
            value[key] = item
        return value
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    if type(value) is not dict or value.get('schema') != SCHEMA:
        raise ValueError('Unsupported deployment state schema')
    preferences = value.get('preferences')
    if type(preferences) is not dict or preferences.get('profile') not in ('core', 'standard') or type(preferences.get('aiAdvice')) is not bool:
        raise ValueError('Invalid deployment preferences')
    return value


def write_state(root, value):
    path = _state_path(root)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.deployment-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        _state_path(root)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def environment_report(root):
    env = launcher.environment(root)
    runtime = env / 'runtime'
    bundled = runtime.is_dir()
    if not bundled:
        runtime = Path(__file__).resolve().parents[1]
    info = {
        'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
        'python': platform.python_version(), 'implementation': platform.python_implementation(),
        'abi': sys.implementation.cache_tag, 'bits': struct.calcsize('P') * 8,
        'platform': sysconfig.get_platform(), 'executable': sys.executable,
        'runtime': str(runtime), 'dependencies': str(env / 'deps'), 'bundled': bundled,
        'pythonSupported': sys.version_info >= (3, 9),
        'freeBytes': shutil.disk_usage(str(root)).free,
        'optionalTools': {name: bool(shutil.which(name)) for name in ('git', 'node', 'adb', 'xcrun', 'nvidia-smi')},
    }
    identity = {key: info[key] for key in ('system', 'machine', 'python', 'implementation', 'abi', 'bits', 'platform')}
    requirements = runtime / 'requirements.txt'
    identity['requirements'] = hashlib.sha256(requirements.read_bytes()).hexdigest() if requirements.is_file() else None
    info['fingerprint'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return info


def _paths(info, deps=None):
    """Never let globally installed packages satisfy a bundled runtime check."""
    paths = [str(deps or info['dependencies']), info['runtime']]
    if not info['bundled'] and deps is None:
        # Installed SDK projects share their declared installed distributions.
        for distribution in ('chaos-intelligence', 'openai', 'cryptography', 'packaging', 'markdown-it-py'):
            try:
                path = str(importlib.metadata.distribution(distribution).locate_file(''))
                if path not in paths:
                    paths.append(path)
            except importlib.metadata.PackageNotFoundError:
                pass
    return paths


_SELF_TEST = r'''
import json, sys
spec=json.loads(sys.argv[1]);sys.path[:0]=spec['paths']
checks=[]
def check(name, action):
    try:
        value=action();checks.append({'name':name,'status':'passed','value':value})
    except Exception as exc:
        checks.append({'name':name,'status':'failed','error':type(exc).__name__})
def dependency(name):
    import importlib
    module=importlib.import_module(name)
    return {'version':str(getattr(module,'__version__','available'))}
if spec['profile']=='standard':
    for name in spec['dependencies']:check('dependency.'+name,lambda name=name:dependency(name))
def pure():
    from chaos import Framework
    value=Framework.evaluate('fn fact(n){if n <= 1 {1} else {n*fact(n-1)}};fact(6)')
    assert value==720
    return value
def modules():
    from chaos import Framework, Settings
    files={'main.chaos':'import "helper.chaos" as H;H.twice(21)', 'helper.chaos':'fn twice(x){x*2};export{twice}'}
    with Framework(':memory:',settings=Settings(':memory:')) as app:
        p=app.create_project('deployment-check',source=files['main.chaos'],files=files,entry='main.chaos',capabilities=[])
        value=app.start(p['id'],p['version'],None,mode='source').result(timeout=10)
        assert value==42
        return value
def asynchronous():
    from chaos import Framework
    value=Framework.evaluate('async fn twice(x){x*2};await gather([twice(20),twice(21)])')
    assert value==[40,42]
    return value
check('language.recursion',pure);check('language.modules',modules);check('runtime.async',asynchronous)
print(json.dumps({'ok':all(c['status']=='passed' for c in checks),'checks':checks}))
'''


def self_test(info, *, profile='standard', deps=None):
    if profile == 'standard' and deps is None and info['bundled']:
        # Importing successfully alone cannot prove dependencies were prepared
        # for this interpreter ABI and this release's requirement constraints.
        if not launcher.dependencies_ready(Path(info['dependencies']).parent):
            return {'ok': False, 'checks': [{'name': 'dependencies.identity', 'status': 'failed',
                    'error': 'Environment mismatch or unverified dependencies; run python chaos.py setup --yes'}]}
    spec = {'paths': _paths(info, deps), 'profile': profile, 'dependencies': DEPENDENCIES}
    if profile == 'core' and info['bundled']:
        spec['paths'] = [info['runtime']]
    # A temporary cwd also isolates caches/state of the fixed smoke programs.
    with tempfile.TemporaryDirectory(prefix='chaos-selftest-') as directory:
        result = subprocess.run([sys.executable, '-I', '-S', '-c', _SELF_TEST, json.dumps(spec)],
                                cwd=directory, capture_output=True, text=True, timeout=45)
    if result.returncode or len(result.stdout) > 100000:
        raise ValueError('Isolated self-test process failed; selected runtime or dependencies are incomplete')
    try:
        report = json.loads(result.stdout)
        if type(report) is not dict or type(report.get('ok')) is not bool or type(report.get('checks')) is not list:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Invalid isolated self-test result') from None
    return report


def gpu_report(info):
    """Optional torch probe has a strict deadline and never installs a GPU stack."""
    code = r'''
import json,sys,importlib.util
sys.path[:0]=json.loads(sys.argv[1])
if importlib.util.find_spec('torch') is None:
 print(json.dumps({'status':'not-installed','cuda':False,'mps':False}))
else:
 import torch
 cuda=bool(torch.cuda.is_available());mps=bool(hasattr(torch.backends,'mps') and torch.backends.mps.is_available())
 print(json.dumps({'status':'available' if cuda or mps else 'cpu-only','cuda':cuda,'mps':mps}))
'''
    try:
        paths = _paths(info)
        if info['bundled']:
            deps = launcher._directory(Path(info['dependencies']))
            marker = deps / '.chaos-dependencies.json'
            # Match _launch's legacy base environment exception exactly; a
            # stale marked environment must not advertise an unusable backend.
            legacy = deps.parent.name == '.chaos' and not (marker.exists() or marker.is_symlink())
            if not legacy and not launcher.dependencies_ready(deps.parent):
                paths = [path for path in paths if path != str(deps)]
        result = subprocess.run([sys.executable, '-I', '-S', '-c', code, json.dumps(paths)],
                                capture_output=True, text=True, timeout=15)
        value = json.loads(result.stdout) if result.returncode == 0 and len(result.stdout) < 10000 else None
        if type(value) is dict and value.get('status') in ('available', 'not-installed', 'cpu-only'):
            return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {'status': 'unavailable', 'cuda': False, 'mps': False}


def _checked(info, profile, reporter, deps=None):
    report = self_test(info, profile=profile, deps=deps)
    for check in report['checks']:
        reporter(check['status'], check['name'] + (': ' + check['error'] if check.get('error') else ''))
    if not report['ok']:
        failed = ', '.join(check['name'] for check in report['checks'] if check['status'] != 'passed')
        raise ValueError('Self-tests failed: ' + failed)
    return report


def _run(root, options, reporter):
    previous = read_state(root)
    preferences = dict(previous['preferences']) if previous else {'profile': 'standard', 'aiAdvice': False}
    profile = options.profile or preferences['profile']
    preferences['profile'] = profile
    if options.ai is not None:
        preferences['aiAdvice'] = options.ai
    doctor = options.command == 'doctor'
    if options.reinstall and (doctor or profile != 'standard' or options.skip_install):
        raise ValueError('--reinstall requires setup/configure with --profile standard and cannot be combined with --skip-install')
    reporter.stage(1, 3 if doctor else 5, '检测操作系统、Python、项目环境')
    info = environment_report(root)
    reporter('passed', '%s / %s · Python %s · %s 位' % (info['system'], info['machine'], info['python'], info['bits']))
    if not info['pythonSupported']:
        raise ValueError('Python 3.9 or newer is required; install a supported interpreter and rerun')
    changed = bool(previous and previous.get('environment', {}).get('fingerprint') != info['fingerprint'])
    if changed:
        reporter('warning', '环境已变化，将重新验证；已有模型与项目配置保留')
    result = {'schema': REPORT_SCHEMA, 'command': options.command, 'status': 'running',
              'environment': info, 'environmentChanged': changed, 'profile': profile}
    reporter.stage(2, 3 if doctor else 5, '验证依赖与语言运行时' if doctor else '配置项目依赖')
    if options.reinstall and not info['bundled']:
        raise ValueError('Reinstall requires a bundled project; manage installed SDK dependencies with your Python package manager')
    if not doctor and profile == 'standard' and not options.skip_install and info['bundled']:
        env = launcher.environment(root)
        if launcher.dependencies_ready(env) and not options.reinstall:
            reporter('passed', '当前依赖与环境指纹匹配，执行自检')
        else:
            if options.reinstall:
                reporter('install', '重新暂存安装标准层依赖；自检通过后替换，失败保留当前目录')
            verified = []
            def validate(payload):
                reporter('stage', '验证临时依赖，测试通过后切换')
                verified.append(_checked(info, profile, reporter, payload))
            code = launcher.setup(root, reporter=reporter, validator=validate)
            if code:
                raise ValueError('Dependency installation failed with exit code %s; original dependencies retained' % code)
            result['stagedSelfTest'] = verified[-1] if verified else None
    elif profile == 'core':
        reporter('passed', '核心层：仅运行语言核心，无第三方依赖安装')
    else:
        reporter('passed', '仅检查当前依赖，不安装')
    if not doctor:
        reporter.stage(3, 5, '运行递归、模块、异步和依赖自检')
    result['selfTest'] = _checked(info, profile, reporter)
    if doctor:
        reporter.stage(3, 3, '检查可选 GPU 后端（不安装）')
        result['gpu'] = gpu_report(info)
        reporter('warning' if result['gpu']['status'] == 'unavailable' else 'passed', 'GPU 检测：' + result['gpu']['status'])
        result['status'] = 'ready'
        return result
    reporter.stage(4, 5, '加载分层模型配置与可选智能建议')
    # setup may have made these modules available after launcher chose sys.path.
    # The isolated tests above have already checked this exact dependency folder.
    selected_deps = info['dependencies']
    if profile == 'standard' and Path(selected_deps).is_dir() and selected_deps not in sys.path:
        sys.path.insert(0, selected_deps)
    from . import deployment_settings
    interactive = not (options.yes or options.json) and sys.stdin.isatty()
    result['settings'] = deployment_settings.configure(
        root, interactive=interactive, models=options.models, ai=preferences['aiAdvice'], reporter=reporter,
        environment={key: info[key] for key in ('system', 'machine', 'python', 'bits', 'platform')})
    preferences['aiAdvice'] = result['settings'].get('preferences', {}).get('aiAdvice', preferences['aiAdvice'])
    reporter.stage(5, 5, '保存项目环境与默认偏好')
    receipt = {'schema': SCHEMA, 'checkedAt': datetime.now(timezone.utc).isoformat(), 'status': 'ready',
               'environment': info, 'preferences': preferences, 'selfTest': result['selfTest']}
    write_state(root, receipt)
    result['status'] = 'ready'
    result['preferences'] = preferences
    reporter('passed', '完成。启动 IDE：python chaos.py；运行程序：python chaos.py run；复查：python chaos.py doctor')
    return result


def main(root=None, argv=None, *, environment_locked=False):
    parser = argparse.ArgumentParser(prog='python chaos.py', description='检测并配置项目环境，执行真实语言自检')
    parser.add_argument('command', choices=('setup', 'configure', 'doctor'))
    parser.add_argument('--directory', help='项目目录；省略时使用启动器目录或从当前目录查找')
    parser.add_argument('--yes', action='store_true', help='不询问，保留既有模型配置与偏好')
    parser.add_argument('--json', action='store_true', help='stdout 输出一个 JSON 报告，进度写入 stderr')
    parser.add_argument('--no-color', action='store_true', help='关闭终端颜色')
    parser.add_argument('--profile', choices=('core', 'standard'), help='core 仅语言核心；standard 包括模型与文档依赖')
    parser.add_argument('--skip-install', action='store_true', help='只检查现有依赖，不安装')
    parser.add_argument('--reinstall', action='store_true', help='标准层重新暂存安装依赖，通过自检后替换，用于修复损坏文件')
    parser.add_argument('--models', action='store_true', help='配置共享提供方和分层模型；终端可交互')
    consent = parser.add_mutually_exclusive_group()
    consent.add_argument('--ai', action='store_true', default=None, help='同意使用已配置模型提供建议，并记住此选择')
    consent.add_argument('--no-ai', action='store_false', dest='ai', help='关闭并记住智能建议偏好')
    options = parser.parse_args(argv)
    if options.command == 'doctor' and (options.models or options.ai is not None):
        parser.error('doctor is read-only; use configure for model settings or AI advice')
    reporter = Reporter(json_mode=options.json, no_color=options.no_color)
    try:
        from .project import select_project
        bound = root is not None
        root = select_project(options.directory, root).root
        held_lock = environment_locked if bound else False
        if options.command != 'doctor' and held_lock and held_lock != 'exclusive':
            if held_lock is True and options.command == 'configure':
                raise ValueError('This older project bootstrap requires: python chaos.py setup --yes (or setup --models for questions)')
            if not (held_lock is True and options.command == 'setup'):
                raise ValueError('Setup/configure requires an exclusive environment lock; rerun with the target project chaos.py setup or configure')
        _state_path(root)
        lock = nullcontext() if held_lock else launcher.environment_lock(root, exclusive=options.command != 'doctor')
        with lock:
            report = _run(root, options, reporter)
        code = 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        # These errors are generated by this module or controlled local checks;
        # model/provider errors are sanitized in deployment_settings.
        report = {'schema': REPORT_SCHEMA, 'command': options.command, 'status': 'failed',
                  'error': str(error)}
        reporter('failed', str(error)); code = 1
        if not options.json:
            print(json.dumps(report, ensure_ascii=False, allow_nan=False), file=sys.stderr)
    except KeyboardInterrupt:
        report = {'schema': REPORT_SCHEMA, 'command': options.command, 'status': 'cancelled'}
        reporter('warning', '已取消，未写入成功部署记录'); code = 130
    if options.json:
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return code
