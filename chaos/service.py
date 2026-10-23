"""Owned per-user OS services. CLI and SDK share this stdlib implementation.

No shell commands, supplied environments, daemon threads or credential copies.
launchd/systemd own persistence; managed deployments retain managed.supervise.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


class ServiceError(RuntimeError):
    """A refused or incomplete lifecycle action; owned files are retained to retry."""


def _json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _absolute(value, name):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError(name + ' must be an absolute filesystem path')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(name + ' must be an absolute filesystem path without parent traversal')
    return str(path)


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    profile: str
    python: str
    cwd: str
    db: str
    state_dir: str
    config: str
    managed_home: str = None
    host: str = '127.0.0.1'
    port: int = 8765

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', self.name):
            raise ValueError('Service name must contain lowercase letters, digits and hyphens')
        if self.profile not in ('source', 'managed'):
            raise ValueError('Service profile must be source or managed')
        for name in ('python', 'cwd', 'db', 'state_dir', 'config'):
            object.__setattr__(self, name, _absolute(getattr(self, name), name))
        if self.managed_home is not None:
            object.__setattr__(self, 'managed_home', _absolute(self.managed_home, 'managed_home'))
        if (self.profile == 'managed') != (self.managed_home is not None):
            raise ValueError('Only a managed profile requires managed_home')
        if type(self.host) is not str:
            raise ValueError('Service host must be a numeric IP address string')
        try:
            ipaddress.ip_address(self.host)
        except (ValueError, TypeError):
            raise ValueError('Service host must be a numeric IP address') from None
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('Service port must be an integer between 1 and 65535')
        if self.db == self.config or self.db == self.state_dir or self.config == self.state_dir:
            raise ValueError('Database, state directory and settings paths must be distinct')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
            raise ValueError('Invalid service specification fields')
        return cls(**value)


class ServiceManager:
    """Manage one owned profile per name, using native user OS service ownership.

    Observer injection supports deterministic contract tests; injected observations
    are explicitly reported as unverified, and never supply arbitrary commands.
    """
    def __init__(self, root=None, *, platform=None, job_dir=None, runner=None,
                 health=None, process=None, clock=None, sleep=None):
        self.platform = platform or sys.platform
        if self.platform not in ('darwin', 'linux'):
            raise ServiceError('Native services support macOS launchd and Linux user systemd only')
        home = Path.home()
        default_root = home / ('.local/state/chaos-intelligence/services')
        self.root = Path(_absolute(str(root or default_root), 'service root'))
        default_jobs = home / ('Library/LaunchAgents' if self.platform == 'darwin' else '.config/systemd/user')
        self.job_dir = Path(_absolute(str(job_dir or default_jobs), 'job directory'))
        self.uid = os.getuid()
        self._run = runner or self._native_run
        self._health = health or self._native_health
        self._process = process or self._native_process
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._native = runner is None and health is None and process is None

    @staticmethod
    def _native_run(argv):
        environment = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(Path.home()),
                       'LANG': 'C', 'LC_ALL': 'C'}
        if sys.platform == 'linux':
            # Fixed OS user-bus location, never a caller-supplied environment.
            environment['XDG_RUNTIME_DIR'] = '/run/user/' + str(os.getuid())
        return subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False, env=environment)

    @staticmethod
    def _native_health(url):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=.5) as response:
                value = json.loads(response.read(65536))
            return value if type(value) is dict else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _native_process(pid):
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,lstart='],
                                capture_output=True, text=True, timeout=3, check=False,
                                env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        parts = result.stdout.strip().split(None, 2)
        if result.returncode or len(parts) != 3:
            return None
        try:
            return {'pid': int(parts[0]), 'ppid': int(parts[1]), 'birth': parts[2]}
        except ValueError:
            return None

    def _label(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', name):
            raise ValueError('Invalid service name')
        return 'ai.chaos-intelligence.' + str(self.uid) + '.' + name

    def _paths(self, name):
        label = self._label(name)
        folder = self.root / name
        return folder, folder / 'service.json', folder / 'launcher.py', self.job_dir / (label + ('.plist' if self.platform == 'darwin' else '.service'))

    def _guard(self, path, *, directory=False, missing=True, owned=True):
        """Reject links in every component, foreign ownership, and unsafe write roots."""
        path = Path(path)
        for item in (path, *path.parents):
            try:
                info = item.lstat()
            except FileNotFoundError:
                if missing:
                    continue
                raise ServiceError('Required service path is missing: ' + str(item)) from None
            if stat.S_ISLNK(info.st_mode):
                raise ServiceError('Service paths must not contain symbolic links: ' + str(item))
            if item == path and directory and not stat.S_ISDIR(info.st_mode):
                raise ServiceError('Service directory is not a directory: ' + str(item))
            if item == path and owned and info.st_uid != self.uid:
                raise ServiceError('Service path belongs to another user: ' + str(item))
            if item == path and owned and info.st_mode & 0o022:
                raise ServiceError('Owned service path must not be group/world writable: ' + str(item))
            if item == path and owned and not directory and not stat.S_ISREG(info.st_mode):
                raise ServiceError('Owned service artifact must be a regular file: ' + str(item))
        return path

    def _mkdir(self, path):
        self._guard(path, directory=True)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._guard(path, directory=True, missing=False)
        if path.stat().st_mode & 0o022:
            raise ServiceError('Service directory must not be group/world writable: ' + str(path))

    def _write(self, path, data):
        self._guard(path)
        self._guard(path.parent, directory=True, missing=False)
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
        try:
            with os.fdopen(fd, 'wb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            self._guard(path)
            self._guard(path.parent, directory=True, missing=False)
            # Exclusive publication cannot overwrite a raced foreign destination.
            os.link(temporary, path, follow_symlinks=False)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def _lock(self):
        self._mkdir(self.root)
        lock = self.root / '.lifecycle.lock'
        self._guard(lock)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(str(lock), flags, 0o600)
        try:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ServiceError('Another service lifecycle operation is active') from None
            yield
        finally:
            os.close(fd)

    def _prepare_logs(self, spec):
        folder = self.root / 'logs'
        self._mkdir(folder)
        for suffix in ('stdout.log', 'stderr.log'):
            path = folder / (spec.name + '.' + suffix)
            self._guard(path)
            if not path.exists():
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
                fd = os.open(str(path), flags, 0o600)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_mode & 0o022:
                        raise ServiceError('Unsafe service log endpoint')
                finally:
                    os.close(fd)
            self._guard(path, missing=False)

    def _prepare_updater(self, spec):
        if spec.profile != 'source':
            return
        namespace = self.root / 'updaters'
        folder = namespace / spec.name
        for directory in (namespace, folder):
            self._mkdir(directory)
            if stat.S_IMODE(directory.stat().st_mode) != 0o700:
                raise ServiceError('Source service updater directory must be private mode0700')
        state = folder / 'updater.json'
        self._guard(state)
        if state.exists() and stat.S_IMODE(state.stat().st_mode) != 0o600:
            raise ServiceError('Source service updater state must be private mode0600')

    @staticmethod
    def _systemd_word(value):
        # systemd percent specifiers and C-escaped quoted arguments are not shell syntax.
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

    def _plan(self, spec, nonce):
        if type(spec) is not ServiceSpec:
            raise TypeError('Expected ServiceSpec')
        folder, config, launcher, job = self._paths(spec.name)
        label = self._label(spec.name)
        command = [spec.python, '-I', str(launcher), '--service-spec', str(config)]
        out, err = str(self.root / 'logs' / (spec.name + '.stdout.log')), str(self.root / 'logs' / (spec.name + '.stderr.log'))
        if self.platform == 'darwin':
            payload = {'Label': label, 'ProgramArguments': command, 'WorkingDirectory': spec.cwd,
                       'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
                       'ThrottleInterval': 5, 'ExitTimeOut': 15, 'ProcessType': 'Background',
                       'StandardOutPath': out, 'StandardErrorPath': err,
                       'EnvironmentVariables': {'CHAOS_SERVICE_ID': nonce}}
            content = plistlib.dumps(payload, sort_keys=True).decode('utf-8')
        else:
            q = self._systemd_word
            content = ('[Unit]\nDescription=Chaos Intelligence ' + spec.name + '\n\n[Service]\nType=simple\n' +
                       'WorkingDirectory=' + q(spec.cwd) + '\nExecStart=' + ' '.join(q(x) for x in command) + '\n' +
                       'Environment=CHAOS_SERVICE_ID=' + nonce + '\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=15\n' +
                       'KillMode=control-group\nUMask=0077\nStandardOutput=append:' + q(out) + '\nStandardError=append:' + q(err) + '\n\n[Install]\nWantedBy=default.target\n')
        return {'spec': spec.to_dict(), 'label': label, 'platform': self.platform,
                'configPath': str(config), 'jobPath': str(job), 'command': command,
                'content': content, 'actualVerified': False}

    def plan(self, spec):
        """Pure generation only: does not read configuration or invoke an OS manager."""
        return self._plan(spec, '0' * 32)

    def _job(self, plan):
        label = plan['label']
        if self.platform == 'darwin':
            result = self._run(['/bin/launchctl', 'print', 'gui/' + str(self.uid) + '/' + label])
            if result.returncode:
                if result.returncode == 113 or 'Could not find service' in result.stderr:
                    return {'loaded': False, 'pid': None}
                raise ServiceError('Cannot inspect the launchd service domain (exit ' + str(result.returncode) + ')')
            text = result.stdout
            path = re.search(r'^\s*path = (.+)$', text, re.M)
            args = re.search(r'^\s*arguments = \{\s*\n(.*?)^\s*\}', text, re.M | re.S)
            found = []
            if args:
                for line in args.group(1).splitlines():
                    value = re.sub(r'^\s*\d+\s*=\s*', '', line).strip()
                    if value:
                        found.append(value[1:-1] if value.startswith('"') and value.endswith('"') else value)
            if not path or path.group(1).strip() != plan['jobPath'] or found != plan['command']:
                raise ServiceError('Loaded launchd label does not match the owned job path and arguments')
            match = re.search(r'^\s*pid = (\d+)$', text, re.M)
            return {'loaded': True, 'pid': int(match.group(1)) if match else None}
        result = self._run(['/usr/bin/systemctl', '--user', 'show', label + '.service',
                             '--property=LoadState,ActiveState,MainPID,FragmentPath,DropInPaths', '--no-pager'])
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if values.get('LoadState') == 'not-found' and result.returncode in (0, 4):
            return {'loaded': False, 'pid': None}
        if result.returncode or values.get('LoadState') != 'loaded':
            raise ServiceError('Cannot establish the loaded systemd unit identity')
        if values.get('FragmentPath') != plan['jobPath']:
            raise ServiceError('Loaded systemd unit does not match the owned unit file')
        if 'DropInPaths' not in values or values['DropInPaths'].strip():
            raise ServiceError('Loaded systemd unit has unowned or unobservable drop-ins')
        # Human systemctl ExecStart formatting is ambiguous for spaces/quotes.
        # Query the documented typed D-Bus value; unknown/missing tools refuse.
        def observe(arguments):
            try:
                observed = self._run(['/usr/bin/busctl', '--user', '--json=short', *arguments])
                if observed.returncode:
                    raise ValueError('D-Bus observation failed')
                parsed = json.loads(observed.stdout)
                if type(parsed) is not dict or set(parsed) != {'type', 'data'}:
                    raise ValueError('Unknown D-Bus observation shape')
                return parsed
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
                raise ServiceError('Cannot establish exact loaded systemd command identity') from None
        unit = observe(['call', 'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
                        'org.freedesktop.systemd1.Manager', 'GetUnit', 's', label + '.service'])
        paths = unit['data']
        if (unit['type'] != 'o' or type(paths) is not list or len(paths) != 1 or
                type(paths[0]) is not str or not re.fullmatch(r'/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+', paths[0])):
            raise ServiceError('Systemd returned an ambiguous unit object identity')
        executable = observe(['get-property', 'org.freedesktop.systemd1', paths[0],
                              'org.freedesktop.systemd1.Service', 'ExecStart'])
        rows = executable['data']
        if (executable['type'] != 'a(sasbttttuii)' or type(rows) is not list or len(rows) != 1 or
                type(rows[0]) is not list or len(rows[0]) != 10):
            raise ServiceError('Systemd returned an ambiguous loaded command')
        row = rows[0]
        if (row[0] != plan['command'][0] or type(row[1]) is not list or row[1] != plan['command'] or
                row[2] is not False or any(type(value) is not int for value in row[3:])):
            raise ServiceError('Loaded systemd executable and arguments do not match the owned command')
        raw_pid = values.get('MainPID', '')
        if not re.fullmatch(r'[0-9]+', raw_pid):
            raise ServiceError('Systemd returned an invalid process identity')
        pid = int(raw_pid)
        return {'loaded': True, 'active': values.get('ActiveState') in ('active', 'activating', 'deactivating', 'reloading'), 'pid': pid or None}

    def _load(self, name):
        folder, config, launcher, job = self._paths(name)
        for path in (folder, config, launcher, job):
            self._guard(path, directory=path == folder, missing=False)
        try:
            record = json.loads(config.read_text(encoding='utf-8'))
            if (type(record) is not dict or record.get('schema') != 'chaos.service.install/1' or
                    record.get('uid') != self.uid or record.get('platform') != self.platform or
                    not re.fullmatch(r'[0-9a-f]{32}', record.get('nonce', ''))):
                raise ValueError('Invalid ownership record')
            if config.read_bytes() != _json(record):
                raise ValueError('Changed canonical ownership record bytes')
            spec = ServiceSpec.from_dict(record['spec'])
            if spec.name != name or record['specSha256'] != _sha(_json(spec.to_dict())):
                raise ValueError('Changed service specification')
            plan = self._plan(spec, record['nonce'])
            if (record['jobSha256'] != _sha(plan['content'].encode('utf-8')) or
                    _sha(job.read_bytes()) != record['jobSha256'] or
                    _sha(launcher.read_bytes()) != record['launcherSha256']):
                raise ValueError('Changed owned service files')
        except (KeyError, ValueError, TypeError, OSError) as error:
            raise ServiceError('Service ownership verification failed: ' + str(error)) from None
        return spec, record, plan

    def _validate_inputs(self, spec):
        if type(spec) is not ServiceSpec:
            raise TypeError('Expected ServiceSpec')
        for field in ('python', 'cwd', 'db', 'state_dir', 'config'):
            self._guard(Path(getattr(spec, field)), directory=field in ('cwd', 'state_dir'), owned=False)
        if not Path(spec.python).is_file() or not os.access(spec.python, os.X_OK):
            raise ServiceError('Service Python interpreter is not executable')
        if not Path(spec.cwd).is_dir():
            raise ServiceError('Service working directory is missing')
        if spec.profile == 'source':
            if not (Path(spec.cwd) / 'chaos' / '__main__.py').is_file():
                raise ServiceError('Source profile requires a Chaos source workspace')
        else:
            home = self._guard(Path(spec.managed_home), directory=True, missing=False, owned=False)
            for path in (home / 'launcher.py', home / 'current.json'):
                self._guard(path, missing=False, owned=False)
            # Preserve the existing enrollment: never regenerate or replace stable launcher.
            from .managed import current, release_python
            version = current(home)['version']
            if not release_python(home, version).is_file():
                raise ServiceError('Managed current release is missing its interpreter')

    def install(self, spec):
        self._validate_inputs(spec)
        with self._lock():
            folder, config, launcher, job = self._paths(spec.name)
            nonce = uuid.uuid4().hex
            plan = self._plan(spec, nonce)
            if any(path.exists() for path in (folder, config, launcher, job)):
                raise ServiceError('Service files already exist; inspect or uninstall the owned profile first')
            if self._job(plan)['loaded']:
                raise ServiceError('Service label is already loaded')
            self._mkdir(self.job_dir)
            self._prepare_logs(spec)
            self._prepare_updater(spec)
            self._mkdir(folder)
            source = Path(__file__).with_name('managed.py').read_bytes()
            record = {'schema': 'chaos.service.install/1', 'uid': self.uid, 'platform': self.platform,
                      'nonce': nonce, 'spec': spec.to_dict(), 'specSha256': _sha(_json(spec.to_dict())),
                      'launcherSha256': _sha(source), 'jobSha256': _sha(plan['content'].encode('utf-8'))}
            created = []
            try:
                for path, content in ((launcher, source), (job, plan['content'].encode('utf-8')), (config, _json(record))):
                    self._write(path, content); created.append((path, _sha(content)))
            except BaseException:
                for path, digest in reversed(created):
                    self._guard(path)
                    if path.is_file() and _sha(path.read_bytes()) == digest:
                        path.unlink()
                try:
                    folder.rmdir()
                except OSError:
                    pass
                raise
            return {'installed': True, 'started': False, **plan}

    @staticmethod
    def _url(spec):
        host = '127.0.0.1' if spec.host == '0.0.0.0' else '::1' if spec.host == '::' else spec.host
        return 'http://' + ('[' + host + ']' if ':' in host else host) + ':' + str(spec.port) + '/api/v1/health'

    def _descends(self, pid, parent, birth):
        seen = set()
        while type(pid) is int and pid > 0 and pid not in seen and len(seen) < 32:
            seen.add(pid)
            observation = self._process(pid)
            if not observation or observation.get('pid') != pid or not observation.get('birth'):
                return False
            if pid == parent:
                return observation.get('birth') == birth
            pid = observation.get('ppid')
        return False

    def _status(self, name):
        spec, record, plan = self._load(name)
        job = self._job(plan)
        process = self._process(job['pid']) if job['pid'] else None
        if process and (process.get('pid') != job['pid'] or not process.get('birth')):
            raise ServiceError('Process observation does not match the OS job identity')
        health = self._health(self._url(spec))
        if spec.profile == 'managed':
            from .managed import current
            expected_version = current(spec.managed_home)['version']
        else:
            self._guard(Path(spec.cwd) / 'chaos' / 'version.py', missing=False, owned=False)
            version_text = (Path(spec.cwd) / 'chaos' / 'version.py').read_text(encoding='utf-8')
            match = re.search(r'''^__version__\s*=\s*['"]([^'"]+)['"]''', version_text, re.M)
            if not match:
                raise ServiceError('Source workspace has no version identity')
            expected_version = match.group(1)
        ready = bool(job['loaded'] and process and health and health.get('status') == 'ok' and
                     health.get('serviceId') == record['nonce'] and health.get('version') == expected_version and
                     type(health.get('pid')) is int and self._descends(health['pid'], job['pid'], process['birth']))
        return {'name': name, 'installed': True, 'loaded': job['loaded'], 'active': job.get('active', job['loaded']), 'running': bool(process),
                'ready': ready, 'pid': job['pid'], 'birth': process.get('birth') if process else None,
                'health': health, 'expectedVersion': expected_version, 'actualVerified': self._native and ready, 'platform': self.platform,
                'label': plan['label'], 'jobPath': plan['jobPath'], 'configPath': plan['configPath']}

    def status(self, name):
        folder, config, _, _ = self._paths(name)
        self._guard(folder, directory=True)
        if not config.exists():
            if folder.exists():
                raise ServiceError('Incomplete service installation; owned configuration is missing')
            return {'name': name, 'installed': False, 'loaded': False, 'running': False,
                    'ready': False, 'actualVerified': False, 'platform': self.platform}
        return self._status(name)

    def _command(self, argv):
        try:
            result = self._run(argv)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ServiceError('Native service manager did not complete: ' + type(error).__name__) from None
        if result.returncode:
            # Native stderr may contain paths; never reflect untrusted config or credentials.
            raise ServiceError('Native service manager rejected ' + argv[1] + ' (exit ' + str(result.returncode) + ')')

    def start(self, name, *, timeout=30):
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValueError('Readiness timeout must be positive and at most 300 seconds')
        with self._lock():
            spec, record, plan = self._load(name)
            state = self._status(name)
            if state['ready']:
                return state
            if state['loaded'] and (self.platform == 'darwin' or state['active']):
                raise ServiceError('Owned job is loaded but not ready; stop it before retrying')
            host = '127.0.0.1' if spec.host == '0.0.0.0' else '::1' if spec.host == '::' else spec.host
            try:
                connection = socket.create_connection((host, spec.port), timeout=.2)
            except OSError:
                connection = None
            if connection is not None:
                connection.close()
                raise ServiceError('Configured port is occupied; no service was started')
            self._prepare_logs(spec)
            self._prepare_updater(spec)
            # Revalidate all immutable ownership bytes immediately before OS start.
            self._load(name)
            if self.platform == 'darwin':
                argv = ['/bin/launchctl', 'bootstrap', 'gui/' + str(self.uid), plan['jobPath']]
            else:
                self._command(['/usr/bin/systemctl', '--user', 'daemon-reload'])
                self._command(['/usr/bin/systemctl', '--user', 'enable', plan['label'] + '.service'])
                argv = ['/usr/bin/systemctl', '--user', 'start', plan['label'] + '.service']
            self._command(argv)
            deadline = self._clock() + timeout
            while self._clock() < deadline:
                state = self._status(name)
                if state['ready']:
                    return state
                self._sleep(.1)
            # Do not bootout here: startup can race a foreign replacement. Retain exact
            # profile and inspectable failed state; explicit stop verifies identity anew.
            raise ServiceError('Owned service did not become ready before the deadline; inspect status and stop')

    def _stop(self, name, timeout):
        spec, record, plan = self._load(name)
        state = self._status(name)
        if not state['loaded']:
            if state['health'] and state['health'].get('serviceId') == record['nonce']:
                raise ServiceError('Owned server is present without its OS job; refusing an unowned PID signal')
            return state
        parent_pid, birth = state['pid'], state['birth']
        if parent_pid and not birth:
            raise ServiceError('Cannot establish the loaded job process identity')
        # Stop the exact loaded job, never a bare PID or force-kickstart.
        if self.platform == 'darwin':
            self._command(['/bin/launchctl', 'bootout', 'gui/' + str(self.uid), plan['jobPath']])
        else:
            self._command(['/usr/bin/systemctl', '--user', 'disable', '--now', plan['label'] + '.service'])
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            job = self._job(plan)
            observation = self._process(parent_pid) if parent_pid else None
            health = self._health(self._url(spec))
            old_alive = bool(observation and observation.get('birth') == birth)
            unloaded = not job['loaded'] if self.platform == 'darwin' else not job.get('active', False)
            if unloaded and not old_alive and not (health and health.get('serviceId') == record['nonce']):
                return self._status(name)
            self._sleep(.1)
        raise ServiceError('Service stop is incomplete; owned files were retained for inspection')

    def stop(self, name, *, timeout=20):
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValueError('Stop timeout must be positive and at most 300 seconds')
        with self._lock():
            return self._stop(name, timeout)

    def uninstall(self, name):
        with self._lock():
            self._stop(name, 20)
            spec, record, plan = self._load(name)
            folder, config, launcher, job = self._paths(name)
            # Revalidate all immutable bytes immediately before removal. User DB,
            # config, durable state and logs are never removed by uninstall.
            expected = ((job, record['jobSha256']), (launcher, record['launcherSha256']),
                        (config, _sha(_json(record))))
            for path, digest in expected:
                self._guard(path, missing=False)
                if not path.is_file() or _sha(path.read_bytes()) != digest:
                    raise ServiceError('Owned service file changed before uninstall: ' + str(path))
            for path, digest in expected:
                self._guard(path, missing=False)
                if not path.is_file() or _sha(path.read_bytes()) != digest:
                    raise ServiceError('Owned service file changed during uninstall: ' + str(path))
                path.unlink()
            if self.platform == 'linux':
                self._command(['/usr/bin/systemctl', '--user', 'daemon-reload'])
            try:
                folder.rmdir()
            except OSError:
                pass
            return {'name': name, 'installed': False, 'removed': True, 'dataPreserved': True,
                    'logsPreserved': True, 'actualVerified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Owned user service lifecycle for Chaos Intelligence')
    parser.add_argument('--root', help='Absolute service registry directory')
    parser.add_argument('--job-dir', help='Absolute OS user service configuration directory')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('plan', 'install'):
        command = sub.add_parser(action)
        command.add_argument('--name', required=True)
        command.add_argument('--profile', choices=('source', 'managed'), required=True)
        for flag in ('python', 'cwd', 'db', 'state-dir', 'config'):
            command.add_argument('--' + flag, required=True)
        command.add_argument('--managed-home')
        command.add_argument('--host', default='127.0.0.1')
        command.add_argument('--port', type=int, default=8765)
    for action in ('status', 'start', 'stop', 'uninstall'):
        command = sub.add_parser(action)
        command.add_argument('name')
    args = parser.parse_args(argv)
    try:
        manager = ServiceManager(args.root, job_dir=args.job_dir)
        if args.action in ('plan', 'install'):
            spec = ServiceSpec(**{key: getattr(args, key) for key in ServiceSpec.__dataclass_fields__})
            result = getattr(manager, args.action)(spec)
        else:
            result = getattr(manager, args.action)(args.name)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, ServiceError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
