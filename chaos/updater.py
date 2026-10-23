"""Signed, isolated application releases. User workspaces never live in this tree.

Trust is established locally by ``configure(publicKey=...)``. Network feeds cannot
change the pinned key. Activation only changes current.json; a supervisor confirms
health before old application releases are pruned.
"""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
import venv
import zipfile

from .version import __version__

DEFAULT_HOME = Path.home() / '.local' / 'share' / 'chaos-intelligence' / 'app'
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_VERSION = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$')


class UpdateError(RuntimeError):
    def __init__(self, message, code='update_failed'):
        super().__init__(message)
        self.code = code


def canonical_manifest(value):
    return json.dumps({k: v for k, v in value.items() if k != 'signature'},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _version(value):
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise UpdateError('Release version must be a semantic version', 'invalid_manifest')
    pre = match.group(4)
    if pre and any(part.isdigit() and len(part) > 1 and part[0] == '0' for part in pre.split('.')):
        raise UpdateError('Invalid semantic prerelease version', 'invalid_manifest')
    return tuple(map(int, match.group(1, 2, 3))), pre


def compare_versions(left, right):
    a, ap = _version(left)
    b, bp = _version(right)
    if a != b:
        return (a > b) - (a < b)
    if ap == bp:
        return 0
    if ap is None or bp is None:
        return 1 if ap is None else -1
    aa, bb = ap.split('.'), bp.split('.')
    for x, y in zip(aa, bb):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return (int(x) > int(y)) - (int(x) < int(y))
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return (x > y) - (x < y)
    return (len(aa) > len(bb)) - (len(aa) < len(bb))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            os.chmod(temporary, 0o600)
            json.dump(value, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if hasattr(os, 'O_DIRECTORY'):
            fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def public_key_bytes(value):
    """Accept a 32-byte Ed25519 key, hex/base64, or a local PEM/raw key file."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if isinstance(value, Path):
            raw = value.read_bytes()
        elif isinstance(value, bytes):
            raw = value
        elif isinstance(value, str):
            if '-----BEGIN PUBLIC KEY-----' in value:
                raw = value.encode('ascii')
            elif re.fullmatch(r'[0-9a-fA-F]{64}', value):
                raw = bytes.fromhex(value)
            elif len(value) <= 4096 and Path(value).is_file():
                raw = Path(value).read_bytes()
            else:
                raw = base64.b64decode(value, validate=True)
        else:
            raise ValueError('Invalid key')
        if b'-----BEGIN PUBLIC KEY-----' in raw:
            key = serialization.load_pem_public_key(raw)
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError('Wrong key type')
            return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if len(raw) != 32:
            # Text files may contain hexadecimal or base64 encoded raw keys.
            text = raw.decode('ascii').strip()
            raw = bytes.fromhex(text) if re.fullmatch(r'[0-9a-fA-F]{64}', text) else base64.b64decode(text, validate=True)
        if len(raw) != 32:
            raise ValueError('Wrong key size')
        Ed25519PublicKey.from_public_bytes(raw)
        return raw
    except ImportError as exc:
        raise UpdateError('The cryptography package is required for signed updates', 'dependency_missing') from exc
    except (ValueError, TypeError, OSError, UnicodeError) as exc:
        raise UpdateError('Expected an Ed25519 public key (PEM, hex or base64)', 'invalid_key') from exc


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


def validate_url(value, allow_local_http=False):
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port
        valid = parsed.scheme == 'https' or (allow_local_http and parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'})
        if not valid or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError('Unsafe URL')
    except (ValueError, TypeError, AttributeError) as exc:
        raise UpdateError('Update URLs require HTTPS without credentials or fragments', 'invalid_url') from exc
    return value


def verify_manifest(manifest, key, current_version=__version__, channel='stable', allow_local_http=False):
    """Verify authenticity before using any release field, then check compatibility."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        if not isinstance(manifest, dict):
            raise ValueError('Manifest object required')
        signature = manifest.get('signature')
        raw_key = public_key_bytes(key)
        if not isinstance(signature, dict) or signature.get('algorithm') != 'ed25519':
            raise ValueError('Ed25519 signature required')
        if signature.get('keyId') != hashlib.sha256(raw_key).hexdigest()[:16]:
            raise ValueError('Signing key mismatch')
        Ed25519PublicKey.from_public_bytes(raw_key).verify(base64.b64decode(signature['value'], validate=True), canonical_manifest(manifest))
    except ImportError as exc:
        raise UpdateError('The cryptography package is required for signed updates', 'dependency_missing') from exc
    except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
        raise UpdateError('Release manifest signature verification failed', 'invalid_signature') from exc
    if manifest.get('schemaVersion') != 1 or type(manifest.get('schemaVersion')) is not int:
        raise UpdateError('Unsupported release manifest schema', 'incompatible_release')
    if manifest.get('apiVersion') != 'v1' or type(manifest.get('storageVersion')) is not int or manifest.get('storageVersion') != 1 or type(manifest.get('installerVersion')) is not int or manifest.get('installerVersion') != 1:
        raise UpdateError('Release is incompatible with API v1, storage 1 or installer 1', 'incompatible_release')
    if manifest.get('channel') != channel:
        raise UpdateError('Release channel does not match configuration', 'incompatible_release')
    _version(manifest.get('version'))
    if compare_versions(manifest['version'], current_version) < 0:
        raise UpdateError('Feed attempted to downgrade this installation', 'downgrade_rejected')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, list):
        raise UpdateError('Release must contain a wheel artifact', 'invalid_manifest')
    wheels = [item for item in artifacts if isinstance(item, dict) and item.get('kind') == 'wheel']
    if len(wheels) != 1:
        raise UpdateError('Release must contain exactly one application wheel', 'invalid_manifest')
    artifact = wheels[0]
    name = artifact.get('filename')
    if not isinstance(name, str) or not re.fullmatch(r'chaos_intelligence-[A-Za-z0-9_.+!-]+-py3-none-any\.whl', name):
        raise UpdateError('Release requires a universal chaos_intelligence wheel', 'invalid_manifest')
    if type(artifact.get('size')) is not int or not 0 < artifact['size'] <= MAX_ARTIFACT_BYTES:
        raise UpdateError('Release artifact size is invalid', 'invalid_manifest')
    if not isinstance(artifact.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', artifact['sha256']):
        raise UpdateError('Release artifact SHA-256 is invalid', 'invalid_manifest')
    validate_url(artifact.get('url'), allow_local_http)
    return artifact


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_local_http):
        self.allow_local_http = allow_local_http
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.allow_local_http)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UpdateManager:
    def __init__(self, home=None, current_version=__version__, *, on_event=None,
                 can_activate=None, on_activate=None, on_error=None, installer=None, downloader=None,
                 allow_local_http=False, managed=None, activation_timeout=300, preserve_browser=None):
        self.home = Path(home or os.environ.get('CHAOS_MANAGED_HOME') or DEFAULT_HOME).expanduser().resolve()
        self.current_version = current_version
        _version(current_version)
        self.on_event, self.can_activate, self.on_activate = on_event, can_activate, on_activate
        self.on_error, self.activation_timeout = on_error, activation_timeout
        self.preserve_browser = preserve_browser
        self._installer = installer or self._install
        self._downloader = downloader
        self.allow_local_http = allow_local_http
        self._managed_override = managed
        self._lock = threading.RLock()
        self._busy = False
        self._lock_fd = None
        self._closed = threading.Event()
        self._thread = None
        self._auto_thread = None
        self._events = []
        self._runtime = {'state': 'idle', 'error': None, 'job': None, 'restartRequired': False}
        self._data = {'version': 1, 'config': {'feedURL': '', 'publicKey': '', 'channel': 'stable', 'autoCheck': True, 'intervalSeconds': 21600}, 'lastCheckedAt': None, 'latestManifest': None, 'preparedVersion': None}
        self._refresh()

    def _refresh(self):
        path = self.home / 'updater.json'
        if path.exists():
            try:
                saved = _json(path.read_bytes())
                if not isinstance(saved, dict) or not isinstance(saved.get('config'), dict):
                    raise ValueError('Invalid state')
                self._data.update({k: saved[k] for k in self._data if k in saved})
            except (ValueError, OSError, TypeError) as exc:
                raise UpdateError('Updater state is unreadable; repair updater.json locally', 'invalid_state') from exc

    def _pointer(self):
        try:
            pointer = _json((self.home / 'current.json').read_bytes())
            _version(pointer['version'])
            if pointer.get('previousVersion') is not None:
                _version(pointer['previousVersion'])
            return pointer
        except FileNotFoundError:
            return {}
        except (ValueError, TypeError, KeyError, UpdateError) as exc:
            raise UpdateError('Managed release pointer is invalid', 'invalid_state') from exc

    @property
    def managed(self):
        if self._managed_override is not None:
            return bool(self._managed_override)
        configured = os.environ.get('CHAOS_MANAGED_HOME')
        return bool(configured and Path(configured).expanduser().resolve() == self.home and
                    Path(sys.prefix).resolve() == (self.home / 'releases' / self.current_version / 'venv').resolve())

    def get_release_python(self, version):
        _version(version)
        return self.home / 'releases' / version / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def status(self):
        with self._lock:
            if not self._busy:
                self._refresh()
            config = dict(self._data['config'])
            key = config.pop('publicKey', '')
            config['publicKeyConfigured'] = bool(key)
            config['keyId'] = hashlib.sha256(public_key_bytes(key)).hexdigest()[:16] if key else None
            latest = self._data.get('latestManifest') or {}
            pointer = self._pointer()
            health = {}
            try:
                candidate = _json((self.home / 'health.json').read_bytes())
                if candidate.get('version') == self.current_version == pointer.get('version'):
                    health = {k: candidate[k] for k in ('version', 'ok', 'time', 'instanceId', 'rolledBackFrom', 'message') if k in candidate}
                    health['currentInstance'] = bool(os.environ.get('CHAOS_INSTANCE_ID') and candidate.get('instanceId') == os.environ.get('CHAOS_INSTANCE_ID'))
            except (OSError, ValueError, AttributeError):
                pass
            result = {**self._runtime, 'version': self._data['version'], 'config': config, 'health': health,
                      'configured': bool(config['feedURL'] and key), 'managed': self.managed,
                      'currentVersion': self.current_version, 'activeVersion': pointer.get('version'),
                      'previousVersion': pointer.get('previousVersion'), 'healthConfirmed': bool(pointer.get('healthConfirmed')), 'latestVersion': latest.get('version'),
                      'available': bool(latest.get('version') and compare_versions(latest['version'], self.current_version) > 0),
                      'availableVersion': latest.get('version'), 'changelog': str(latest.get('changelog', ''))[:16000],
                      'enrollmentCommand': 'chaos update adopt --feed <HTTPS_MANIFEST> --public-key <PUBLIC_KEY_FILE>',
                      'lastCheckedAt': self._data['lastCheckedAt'], 'preparedVersion': self._data['preparedVersion'],
                      'busy': self._busy, 'events': list(self._events), 'home': str(self.home)}
            if health.get('ok') and health.get('currentInstance'):
                if health.get('rolledBackFrom') and result['state'] in {'idle', 'active', 'checking', 'available', 'up_to_date'}:
                    result['state'] = 'rolled_back'
                elif result['state'] == 'idle':
                    result['state'] = 'active'
            return json.loads(json.dumps(result))

    def _save(self):
        atomic_json(self.home / 'updater.json', self._data)

    def _event(self, state, **payload):
        with self._lock:
            self._runtime['state'] = state
            event = {'id': len(self._events) + 1 if not self._events else self._events[-1]['id'] + 1,
                     'type': 'update.' + state, 'time': time.time(), **payload}
            self._events = (self._events + [event])[-100:]
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    @contextmanager
    def _file_lock(self):
        self.home.mkdir(parents=True, exist_ok=True)
        with (self.home / '.update.lock').open('a+b') as handle:
            try:
                if os.name == 'nt':
                    import msvcrt
                    if handle.tell() == 0:
                        handle.write(b'0'); handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpdateError('Another updater process is active', 'busy') from exc
            try:
                self._lock_fd = handle.fileno()
                yield
            finally:
                self._lock_fd = None
                if os.name == 'nt':
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def configure(self, options, version=None):
        if not isinstance(options, dict):
            raise ValueError('Update configuration must be an object')
        allowed = {'feedURL', 'publicKey', 'autoCheck', 'intervalSeconds', 'channel'}
        if set(options) - allowed:
            raise ValueError('Unknown update configuration field')
        with self._lock, self._file_lock():
            if self._busy:
                raise UpdateError('An update operation is active', 'busy')
            self._refresh()
            if version is not None and version != self._data['version']:
                raise UpdateError('Update configuration changed; reload before saving', 'conflict')
            config = dict(self._data['config'])
            if 'feedURL' in options:
                config['feedURL'] = validate_url(options['feedURL'], self.allow_local_http) if options['feedURL'] else ''
            if 'publicKey' in options:
                encoded = base64.b64encode(public_key_bytes(options['publicKey'])).decode('ascii')
                if config['publicKey'] and encoded != config['publicKey']:
                    raise UpdateError('Signing key rotation requires local administrator configuration', 'trust_locked')
                config['publicKey'] = encoded
            if 'channel' in options:
                if not isinstance(options['channel'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', options['channel']):
                    raise ValueError('Invalid update channel')
                config['channel'] = options['channel']
            if 'autoCheck' in options:
                if type(options['autoCheck']) is not bool:
                    raise ValueError('autoCheck must be boolean')
                config['autoCheck'] = options['autoCheck']
            if 'intervalSeconds' in options:
                if type(options['intervalSeconds']) is not int or not 60 <= options['intervalSeconds'] <= 604800:
                    raise ValueError('intervalSeconds must be 60–604800')
                config['intervalSeconds'] = options['intervalSeconds']
            self._data.update(config=config, version=self._data['version'] + 1,
                              latestManifest=None, preparedVersion=None)
            self._save()
            return self.status()

    def _run(self, action, reserved=False):
        with self._lock:
            if self._busy and not reserved:
                raise UpdateError('An update operation is active', 'busy')
            self._busy = True
            self._runtime.update(error=None, job={'id': uuid.uuid4().hex, 'action': action, 'startedAt': time.time()})
        try:
            with self._file_lock():
                self._refresh()
                getattr(self, '_' + action)()
        except Exception as exc:
            error = exc if isinstance(exc, UpdateError) else UpdateError('Update operation failed: ' + type(exc).__name__)
            with self._lock:
                self._runtime['error'] = {'code': error.code, 'message': str(error)}
            self._event('failed', error=self._runtime['error'])
            if self.on_error:
                try:
                    self.on_error(error)
                except Exception:
                    pass
            raise error from exc
        finally:
            with self._lock:
                self._busy = False
                self._runtime['job']['finishedAt'] = time.time()
        return self.status()

    def check(self):
        return self._run('check')

    def prepare(self):
        return self._run('prepare')

    def apply(self):
        return self._run('apply')

    def adopt(self):
        """One-time explicit installation into an unused managed root."""
        return self._run('adopt')

    def rollback(self):
        return self._run('rollback')

    def start(self, action='check'):
        if action not in {'check', 'prepare', 'apply', 'rollback'}:
            raise ValueError('Unknown update action')
        with self._lock:
            if self._busy:
                raise UpdateError('An update operation is active', 'busy')
            self._busy = True
            self._runtime.update(state='queued', error=None)
        def worker():
            try:
                self._run(action, reserved=True)
            except UpdateError:
                pass
        self._thread = threading.Thread(target=worker, name='chaos-update-' + action, daemon=True)
        self._thread.start()
        return self.status()

    def _download(self, url, limit, destination=None):
        validate_url(url, self.allow_local_http)
        if self._downloader:
            raw = self._downloader(url, limit)
            if not isinstance(raw, bytes) or len(raw) > limit:
                raise UpdateError('Downloaded content exceeds the allowed size', 'download_failed')
            if destination:
                Path(destination).write_bytes(raw)
                return len(raw)
            return raw
        opener = urllib.request.build_opener(_Redirects(self.allow_local_http))
        request = urllib.request.Request(url, headers={'User-Agent': 'Chaos-Updater/' + self.current_version, 'Accept-Encoding': 'identity'})
        try:
            started, length, chunks = time.monotonic(), 0, []
            with opener.open(request, timeout=20) as response:
                validate_url(response.geturl(), self.allow_local_http)
                advertised = response.headers.get('Content-Length')
                if advertised is not None and int(advertised) > limit:
                    raise UpdateError('Download is larger than the signed size', 'download_failed')
                handle = Path(destination).open('wb') if destination else None
                try:
                    while True:
                        chunk = response.read(min(65536, limit - length + 1))
                        if not chunk:
                            break
                        length += len(chunk)
                        if length > limit or time.monotonic() - started > 120:
                            raise UpdateError('Download exceeded its size or time limit', 'download_failed')
                        if handle:
                            handle.write(chunk)
                        else:
                            chunks.append(chunk)
                finally:
                    if handle:
                        handle.close()
            return length if destination else b''.join(chunks)
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError('Could not download the release; check the configured HTTPS feed and connection', 'download_failed') from exc

    def _check(self):
        self._event('checking')
        config = self._data['config']
        if not config['feedURL'] or not config['publicKey']:
            raise UpdateError('Configure a release feed and pin its public key locally first', 'not_configured')
        self._data['latestManifest'] = None
        try:
            manifest = _json(self._download(config['feedURL'], MAX_MANIFEST_BYTES))
        except (ValueError, UnicodeError) as exc:
            raise UpdateError('Release feed is not valid JSON', 'invalid_manifest') from exc
        verify_manifest(manifest, config['publicKey'], self.current_version, config['channel'], self.allow_local_http)
        with self._lock:
            self._data.update(latestManifest=manifest, lastCheckedAt=time.time())
            self._save()
        self._event('available' if compare_versions(manifest['version'], self.current_version) > 0 else 'up_to_date', version=manifest['version'])
        return manifest

    def _validate_wheel(self, path, version):
        try:
            with zipfile.ZipFile(path) as wheel:
                names = wheel.namelist()
                required = {'chaos/__init__.py', 'chaos/version.py', 'chaos/web/index.html', 'chaos/web/app.js', 'chaos/web/control.html', 'chaos/vendor/deepseek/manifest.json'}
                if len(names) != len(set(names)):
                    raise ValueError('Duplicate archive members')
                if not any(name.startswith('chaos/docs/') for name in names) or not any(name.startswith('chaos/examples/') for name in names):
                    raise ValueError('Release must include documentation and examples')
                if not required.issubset(names):
                    raise ValueError('Incomplete application payload')
                total = 0
                for item in wheel.infolist():
                    if item.filename.startswith('/') or '\\' in item.filename or '..' in Path(item.filename).parts or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                        raise ValueError('Unsafe archive member')
                    total += item.file_size
                if total > 1024 * 1024 * 1024 or len(names) > 100000:
                    raise ValueError('Oversized archive')
                metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
                if len(metadata) != 1:
                    raise ValueError('Missing package metadata')
                from email.parser import BytesParser
                info = BytesParser().parsebytes(wheel.read(metadata[0]))
                if info.get('Name', '').lower().replace('_', '-') != 'chaos-intelligence':
                    raise ValueError('Wrong distribution')
                # Packaging normalizes prereleases; use its installed dependency if necessary.
                candidate = info.get('Version')
                if candidate != version:
                    from packaging.version import Version
                    if Version(candidate) != Version(version):
                        raise ValueError('Wrong version')
        except Exception as exc:
            raise UpdateError('Signed wheel is incomplete or has invalid application metadata', 'invalid_artifact') from exc

    def _prepare(self, force=False):
        manifest = self._check()
        version = manifest['version']
        if not force and compare_versions(version, self.current_version) == 0:
            return None
        artifact = verify_manifest(manifest, self._data['config']['publicKey'], self.current_version,
                                   self._data['config']['channel'], self.allow_local_http)
        releases = self.home / 'releases'
        staging_root = self.home / 'staging'
        if releases.is_symlink() or staging_root.is_symlink():
            raise UpdateError('Managed release and staging directories cannot be symlinks', 'invalid_state')
        staging_root.mkdir(parents=True, exist_ok=True)
        for abandoned in staging_root.iterdir():
            if abandoned.name.startswith('release-') and abandoned.is_dir() and not abandoned.is_symlink():
                shutil.rmtree(abandoned)
        release = releases / version
        marker = release / '.release.json'
        pointer = self._pointer()
        if release.is_symlink():
            raise UpdateError('Release directory cannot be a symlink', 'invalid_state')
        if release.is_dir() and not marker.exists() and not marker.is_symlink() and version not in {pointer.get('version'), pointer.get('previousVersion')}:
            shutil.rmtree(release)
        if release.exists():
            try:
                saved = _json(marker.read_bytes())
                prior_artifact = verify_manifest(saved, self._data['config']['publicKey'], self.current_version,
                                                 self._data['config']['channel'], self.allow_local_http)
                immutable = ('filename', 'sha256', 'size')
                same = (saved['version'] == version and all(prior_artifact[k] == artifact[k] for k in immutable)
                        and self.get_release_python(version).is_file())
                if same and self._want_browser():
                    same = _json((release / '.features.json').read_bytes()).get('browser') is True
            except (OSError, ValueError, UpdateError):
                same = False
            if same:
                self._data['preparedVersion'] = version
                self._save()
                self._event('ready', version=version)
                return version
            raise UpdateError('Release directory already exists with different or incomplete content', 'release_exists')
        self._event('downloading', version=version)
        created = False
        try:
            with tempfile.TemporaryDirectory(prefix='release-', dir=staging_root) as temporary:
                wheel = Path(temporary) / artifact['filename']
                self._download(artifact['url'], artifact['size'], wheel)
                digest = hashlib.sha256()
                with wheel.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(65536), b''):
                        digest.update(chunk)
                if wheel.stat().st_size != artifact['size'] or digest.hexdigest() != artifact['sha256']:
                    raise UpdateError('Release artifact size or SHA-256 does not match its signed manifest', 'integrity_failed')
                self._validate_wheel(wheel, version)
                self._event('installing', version=version)
                release.mkdir(parents=True, exist_ok=False)
                created = True
                self._installer(wheel, release, version)
                if not self.get_release_python(version).is_file():
                    raise UpdateError('Installer did not produce a release interpreter', 'install_failed')
                atomic_json(release / '.features.json', {'browser': self._want_browser()})
                atomic_json(marker, manifest)
            self._data['preparedVersion'] = version
            self._save()
            self._event('ready', version=version)
            return version
        except Exception:
            if created:
                shutil.rmtree(release, ignore_errors=True)
            raise

    def _want_browser(self):
        if self.preserve_browser is not None:
            return bool(self.preserve_browser)
        import importlib.util
        return importlib.util.find_spec('playwright') is not None

    def _command(self, command, **kwargs):
        # A killed updater cannot leave an orphan installer racing the next update.
        # The child retains the advisory lock until it exits, including after SIGKILL.
        if os.name != 'nt' and self._lock_fd is not None:
            kwargs['pass_fds'] = (self._lock_fd,)
        return subprocess.run(command, **kwargs)

    def _install(self, wheel, release, version):
        venv.EnvBuilder(with_pip=False).create(release / 'venv')
        python = self.get_release_python(version)
        env = dict(os.environ)
        for name in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER', 'PIP_EXTRA_INDEX_URL', 'PIP_FIND_LINKS', 'PIP_TRUSTED_HOST'):
            env.pop(name, None)
        env['PIP_CONFIG_FILE'] = os.devnull
        browser = self._want_browser()
        requirement = str(wheel) + ('[browser]' if browser else '')
        try:
            self._command([str(python), '-I', '-m', 'ensurepip', '--upgrade', '--default-pip'],
                          cwd=release, env=env, capture_output=True, timeout=120, check=True)
            self._command([str(python), '-I', '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                            '--no-input', '--no-cache-dir', '--only-binary=:all:', requirement],
                           cwd=release, env=env, capture_output=True, timeout=600, check=True)
            if browser:
                # Keep the previous runtime's browser usable until rollback is no longer needed.
                env['PLAYWRIGHT_SKIP_BROWSER_GC'] = '1'
                self._command([str(python), '-I', '-m', 'playwright', 'install', 'chromium'],
                               cwd=release, env=env, capture_output=True, timeout=600, check=True)
            self._event('testing', version=version)
            self._self_test(python, version, env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise UpdateError('Candidate installation or isolated smoke test failed; active release was preserved', 'install_failed') from exc

    def _self_test(self, python, version, env):
        script = '''import sys,tempfile,pathlib,json,threading,urllib.request
import chaos,openai,cryptography
from chaos import Framework, Settings
from chaos.server import make_server,static_path
assert chaos.__version__ == sys.argv[1]
assert Framework.evaluate("1 + 2") == 3
for name in ("index.html","app.js","control.html","control.js"):
    assert static_path(name).is_file(), name
assert (pathlib.Path(chaos.__file__).parent / "vendor/deepseek/manifest.json").is_file()
if sys.argv[2] == 'browser':
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.set_content("<title>Chaos update smoke</title>")
            assert page.title() == "Chaos update smoke"
        finally:
            browser.close()
with tempfile.TemporaryDirectory() as state:
    with Framework(settings=Settings(":memory:")) as app:
        p=app.create_project("update-smoke",source="input + 1")
        assert app.start(p["id"],p["version"],41,mode="source").result() == 42
        server=make_server("127.0.0.1",0,framework=app,state_dir=state)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:%s/api/v1/health" % server.server_port,timeout=5) as response:
                health=json.load(response)
                assert health["version"] == sys.argv[1] and health["apiVersion"] == "v1"
        finally:
            server.shutdown();server.server_close();thread.join(5)
'''
        with tempfile.TemporaryDirectory(prefix='chaos-update-smoke-') as cwd:
            env = dict(env)
            env['CHAOS_MANAGED_HOME'] = str(Path(cwd) / 'managed')
            env.pop('CHAOS_SUPERVISED', None)
            env.pop('CHAOS_INSTANCE_ID', None)
            self._command([str(python), '-I', '-c', script, version, 'browser' if self._want_browser() else 'base'], cwd=cwd, env=env,
                           capture_output=True, timeout=90, check=True)

    def _ensure_idle(self):
        deadline = time.monotonic() + self.activation_timeout
        waiting = False
        while self.can_activate and not self.can_activate():
            if not waiting:
                self._event('waiting_idle')
                waiting = True
            if time.monotonic() >= deadline:
                raise UpdateError('Timed out waiting for active executions to finish; retry the update', 'not_idle')
            if self._closed.wait(min(.5, max(0, deadline - time.monotonic()))):
                raise UpdateError('Update activation was interrupted by shutdown', 'interrupted')

    def _activate(self, version, previous):
        self._ensure_idle()
        prior = self._pointer()
        atomic_json(self.home / 'current.json', {'version': version, 'previousVersion': previous, 'activatedAt': time.time(), 'healthConfirmed': False})
        self._runtime['restartRequired'] = True
        self._event('restart_required', version=version)
        try:
            if self.on_activate:
                self.on_activate(version)
        except Exception:
            if prior:
                atomic_json(self.home / 'current.json', prior)
            else:
                (self.home / 'current.json').unlink(missing_ok=True)
            self._runtime['restartRequired'] = False
            raise

    def _apply(self):
        if not self.managed:
            raise UpdateError('This installation needs one-time managed installation before one-click updates', 'unmanaged_installation')
        pointer = self._pointer()
        if pointer.get('version') and pointer['version'] != self.current_version:
            raise UpdateError('A release switch already awaits restart', 'restart_pending')
        version = self._prepare()
        if version is not None:
            pointer = self._pointer()
            self._activate(version, pointer.get('version', self.current_version))

    def _adopt(self):
        if self._pointer():
            raise UpdateError('Managed installation already exists; use its stable launcher', 'already_managed')
        version = self._prepare(force=True)
        self._activate(version, None)

    def _rollback(self):
        if not self.managed:
            raise UpdateError('Rollback requires a managed installation', 'unmanaged_installation')
        pointer = self._pointer()
        previous = pointer.get('previousVersion')
        if not previous or not self.get_release_python(previous).is_file():
            raise UpdateError('No previous application release is available', 'no_rollback')
        self._activate(previous, pointer['version'])

    def confirm_health(self, version):
        """Called only by the supervisor after the new server passes health checks."""
        with self._lock, self._file_lock():
            pointer = self._pointer()
            if pointer.get('version') != version:
                raise UpdateError('Health check is for a different active release', 'conflict')
            pointer['healthConfirmed'] = True
            atomic_json(self.home / 'current.json', pointer)
            keep = {pointer['version'], pointer.get('previousVersion')}
            releases = self.home / 'releases'
            if releases.exists():
                for entry in releases.iterdir():
                    if entry.name not in keep and _VERSION.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry)
            self._runtime['restartRequired'] = False
            self._event('active', version=version)
            return self.status()

    def _confirm_supervisor_health(self):
        if not self.managed or not os.environ.get('CHAOS_INSTANCE_ID') or self._busy:
            return
        pointer = self._pointer()
        if pointer.get('healthConfirmed') or pointer.get('version') != self.current_version:
            return
        try:
            health = _json((self.home / 'health.json').read_bytes())
        except FileNotFoundError:
            return
        if (health.get('ok') is True and health.get('version') == self.current_version
                and health.get('instanceId') == os.environ['CHAOS_INSTANCE_ID']):
            self.confirm_health(self.current_version)

    def start_auto_check(self):
        if self._auto_thread and self._auto_thread.is_alive():
            return
        def loop():
            while not self._closed.is_set():
                try:
                    with self._lock:
                        if not self._busy:
                            self._refresh()
                    self._confirm_supervisor_health()
                except (UpdateError, OSError, ValueError):
                    pass
                config = self._data['config']
                due = time.time() - (self._data.get('lastCheckedAt') or 0) >= config['intervalSeconds']
                if config['autoCheck'] and config['feedURL'] and config['publicKey'] and due and not self._busy:
                    try:
                        self.check()
                    except UpdateError:
                        # Failed checks are throttled, too; do not hammer an offline feed.
                        self._closed.wait(min(config['intervalSeconds'], 300))
                self._closed.wait(5)
        self._auto_thread = threading.Thread(target=loop, name='chaos-update-check', daemon=True)
        self._auto_thread.start()

    def close(self):
        self._closed.set()


def main(argv=None):
    """Local trust/bootstrap commands; live activation always goes through the server."""
    import argparse
    parser = argparse.ArgumentParser(prog='chaos update')
    parser.add_argument('action', nargs='?', default='status', choices=('status', 'configure', 'check', 'prepare', 'apply', 'rollback', 'adopt'))
    parser.add_argument('--home')
    parser.add_argument('--feed')
    parser.add_argument('--public-key')
    parser.add_argument('--channel')
    parser.add_argument('--interval', type=int)
    parser.add_argument('--no-auto-check', action='store_true')
    parser.add_argument('--server', default='http://127.0.0.1:8765')
    args = parser.parse_args(argv)
    try:
        if args.action in {'apply', 'rollback'}:
            server = validate_url(args.server.rstrip('/'), allow_local_http=True)
            request = urllib.request.Request(server + '/api/v1/updates/' + args.action, data=b'{}',
                                             headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
        else:
            manager = UpdateManager(args.home)
            options = {}
            for flag, field in (('feed', 'feedURL'), ('public_key', 'publicKey'), ('channel', 'channel'), ('interval', 'intervalSeconds')):
                if getattr(args, flag) is not None:
                    options[field] = getattr(args, flag)
            if args.no_auto_check:
                options['autoCheck'] = False
            if options:
                manager.configure(options)
            if args.action == 'adopt':
                result = manager.adopt()
                from .managed import write_launcher
                result['launcher'] = str(write_launcher(manager.home))
            elif args.action == 'configure':
                result = manager.status()
            else:
                result = getattr(manager, args.action)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        if isinstance(exc, UpdateError):
            error = {'code': exc.code, 'message': str(exc)}
        else:
            error = {'code': 'update_failed', 'message': 'Update command failed: ' + type(exc).__name__}
        print(json.dumps({'error': error}, ensure_ascii=False), file=sys.stderr)
        return 1
