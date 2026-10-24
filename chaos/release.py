"""Build a complete, signed release from an isolated source snapshot.

The private signing key belongs to the maintainer and must live outside the
checkout.  This module produces uploadable files; it does not publish a feed.
"""
from __future__ import annotations

import argparse
import ast
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import site
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import quote, urlsplit
import zipfile


class ReleaseError(RuntimeError):
    pass


SOURCE_DIRECTORIES = ('chaos', 'web', 'vendor', 'docs', 'examples', 'tests', 'scripts')
SOURCE_FILES = ('setup.py', 'setup.cfg', 'pyproject.toml', 'MANIFEST.in', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')
EXCLUDED_DIRECTORIES = frozenset({
    'build', 'dist', 'node_modules', 'venv', '__pycache__', 'htmlcov',
    'keys', 'secrets', 'credentials', 'releases',
})
PRIVATE_VALUES_ENV = 'CHAOS_RELEASE_PRIVATE_VALUES_FILE'
CREDENTIAL_PATTERN = re.compile(rb'(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}')
STATE_SUFFIXES = ('.sqlite', '.sqlite3', '.db', '.log')
VERSION_PATTERN = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _excluded(name: str) -> bool:
    lower = name.lower()
    return (name.startswith('.') or lower in EXCLUDED_DIRECTORIES
            or lower.endswith(('.egg-info', '.dist-info', '.pyc', '.pyo', '.key', '.pem', '.p12', '.pfx'))
            or lower.endswith(STATE_SUFFIXES + tuple(ext + suffix for ext in STATE_SUFFIXES
                                                       for suffix in ('-wal', '-shm', '-journal')))
            or lower in {'id_rsa', 'id_ed25519', 'credentials.json', 'secrets.json'})



def _private_values(source: Path) -> tuple:
    """Load optional publisher-only values; never print or include this file."""
    filename = os.environ.get(PRIVATE_VALUES_ENV)
    if not filename:
        return ()
    path = Path(filename).expanduser()
    try:
        if _inside(path, source) or path.is_symlink() or not path.is_file():
            raise ValueError('external regular file required')
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('private value list too large')
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError('expected nonempty strings')
        return tuple(value.encode('utf-8') for value in values)
    except (OSError, ValueError, UnicodeError):
        raise ReleaseError(PRIVATE_VALUES_ENV + ' must point to an external JSON array of nonempty strings') from None


def _check_source_privacy(path: Path, source: Path, private_values: tuple) -> bytes:
    data = path.read_bytes()
    match = CREDENTIAL_PATTERN.search(data)
    offsets = [match.start()] if match else []
    offsets.extend(position for value in private_values for position in [data.find(value)] if position >= 0)
    if offsets:
        line = data.count(b'\n', 0, min(offsets)) + 1
        raise ReleaseError('private material in release source: ' + path.relative_to(source).as_posix()
                           + ':' + str(line))
    return data


def copy_source(source: Path, destination: Path) -> None:
    """Copy only release inputs; reject links instead of following them."""
    source, destination = Path(source).resolve(), Path(destination)
    if not (source / 'setup.py').is_file() or not (source / 'chaos/version.py').is_file():
        raise ReleaseError('source must be a Chaos checkout containing setup.py and chaos/version.py')
    private_values = _private_values(source)
    destination.mkdir(parents=True, exist_ok=False)

    def copy(path: Path, target: Path) -> None:
        if _excluded(path.name):
            return
        if path.is_symlink():
            raise ReleaseError('release source contains a symlink: ' + str(path.relative_to(source)))
        if path.is_dir():
            target.mkdir()
            for child in sorted(path.iterdir()):
                copy(child, target / child.name)
        elif path.is_file():
            target.write_bytes(_check_source_privacy(path, source, private_values))
            target.chmod(0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644)
        else:
            raise ReleaseError('release source contains a non-regular file: ' + str(path.relative_to(source)))

    for name in (*SOURCE_FILES, *SOURCE_DIRECTORIES):
        path = source / name
        if path.exists() or path.is_symlink():
            copy(path, destination / name)


def source_version(source: Path) -> str:
    try:
        tree = ast.parse((Path(source) / 'chaos/version.py').read_text(encoding='utf-8'))
        versions = [ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets)]
    except (OSError, SyntaxError, ValueError) as error:
        raise ReleaseError('cannot read the literal version from chaos/version.py') from error
    if len(versions) != 1 or not isinstance(versions[0], str) or not VERSION_PATTERN.fullmatch(versions[0]):
        raise ReleaseError('chaos/version.py must define one stable X.Y.Z version')
    if tuple(map(int, versions[0].split('.'))) < (0, 6, 0):
        raise ReleaseError('signed managed releases require version 0.6.0 or later')
    return versions[0]


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as error:
        raise ReleaseError('install cryptography before generating or signing a release') from error
    return serialization, Ed25519PrivateKey


def _private_key(path: Path, source: Path):
    path = Path(path).expanduser()
    if _inside(path, source):
        raise ReleaseError('the maintainer private key must be outside the source checkout')
    if path.is_symlink():
        raise ReleaseError('the private key must be a regular file, not a symlink')
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ReleaseError('the private key must be a regular file')
        if os.name == 'posix' and stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ReleaseError('the private key must have mode 0600 (chmod 600 PRIVATE_KEY)')
        serialization, key_type = _crypto()
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, key_type):
            raise ReleaseError('the private key must be an unencrypted Ed25519 PEM key')
        return key
    except (OSError, ValueError, TypeError) as error:
        raise ReleaseError('cannot load the Ed25519 signing key') from error


def public_key_id(public_key) -> str:
    serialization, _ = _crypto()
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def generate_key(private_path: Path, public_path: Path, source: Path) -> dict:
    """Create a new key pair explicitly, without replacing an existing key."""
    private_path, public_path = Path(private_path).expanduser(), Path(public_path).expanduser()
    if _inside(private_path, source):
        raise ReleaseError('the maintainer private key must be outside the source checkout')
    if private_path.resolve() == public_path.resolve():
        raise ReleaseError('private and public key paths must differ')
    if private_path.exists() or public_path.exists() or private_path.is_symlink() or public_path.is_symlink():
        raise ReleaseError('refusing to replace an existing signing key')
    serialization, key_type = _crypto()
    key = key_type.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
    public_data = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    try:
        fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(private_data)
        try:
            fd = os.open(public_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(public_data)
        except OSError:
            private_path.unlink()
            raise
    except OSError as error:
        raise ReleaseError('cannot create signing key files') from error
    return {'privateKey': str(private_path.resolve()), 'publicKey': str(public_path.resolve()),
            'keyId': public_key_id(key.public_key())}


def canonical_manifest(manifest: dict) -> bytes:
    return json.dumps({k: v for k, v in manifest.items() if k != 'signature'},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def sign_manifest(manifest: dict, key) -> dict:
    result = dict(manifest)
    result['signature'] = {'algorithm': 'ed25519', 'keyId': public_key_id(key.public_key()),
                           'value': base64.b64encode(key.sign(canonical_manifest(result))).decode('ascii')}
    return result


def _build_environment(home: Path) -> dict:
    # Build/test subprocesses do not inherit provider keys or the user's settings.
    environment = {name: os.environ[name] for name in
                   ('PATH', 'SYSTEMROOT', 'WINDIR', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL')
                   if name in os.environ}
    # Changing HOME must not hide dependencies installed for this interpreter
    # with pip --user. Preserve only its explicit site directory, not an inherited
    # PYTHONPATH (which could import the working checkout instead of the snapshot).
    if site.ENABLE_USER_SITE:
        user_sites = site.getusersitepackages()
        if isinstance(user_sites, str):
            user_sites = [user_sites]
        user_sites = [str(Path(path).resolve()) for path in user_sites if Path(path).is_dir()]
        if user_sites:
            environment['PYTHONPATH'] = os.pathsep.join(user_sites)
    home.mkdir()
    environment.update(HOME=str(home), USERPROFILE=str(home),
                       PYTHONDONTWRITEBYTECODE='1', PIP_CONFIG_FILE=os.devnull,
                       PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
                       SOURCE_DATE_EPOCH='315532800')
    return environment


def _run(command: list, source: Path, environment: dict) -> None:
    try:
        result = subprocess.run(command, cwd=source, env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError('release check/build could not finish: ' + ' '.join(command)) from error
    if result.returncode:
        raise ReleaseError('release check/build failed: ' + ' '.join(command) + '\n' + result.stdout[-12000:])


def validate_wheel(wheel: Path, source: Path, version: str) -> None:
    """Require byte-for-byte inclusion of all runtime source and bundled assets."""
    expected = {}
    if not (source / 'vendor/deepseek').is_dir():
        raise ReleaseError('release source is missing required directory: vendor/deepseek')
    for directory in ('chaos', 'web', 'vendor', 'docs', 'examples'):
        root = source / directory
        if not root.is_dir():
            raise ReleaseError('release source is missing required directory: ' + directory)
        for path in root.rglob('*'):
            if path.is_file() and not any(_excluded(part) for part in path.relative_to(root).parts):
                target = str(path.relative_to(source)).replace(os.sep, '/')
                if directory != 'chaos':
                    target = 'chaos/' + target
                expected[target] = path.read_bytes()
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ReleaseError('wheel contains duplicate paths')
            packaged = {name for name in names if name.startswith('chaos/') and not name.endswith('/')}
            if packaged != set(expected):
                missing, extra = sorted(set(expected) - packaged), sorted(packaged - set(expected))
                raise ReleaseError('wheel content mismatch; missing=' + repr(missing[:12]) + '; unexpected=' + repr(extra[:12]))
            for name, data in expected.items():
                if archive.read(name) != data:
                    raise ReleaseError('wheel content differs from clean source: ' + name)
            metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
            if len(metadata) != 1 or ('\nVersion: ' + version + '\n') not in archive.read(metadata[0]).decode('utf-8'):
                raise ReleaseError('wheel metadata does not match source version')
            # New releases carry the publisher's terms beside separately licensed
            # dependencies. Preserve paths: old builders flattened LICENSE files
            # with identical basenames and could silently discard upstream terms.
            prefix = metadata[0].rsplit('/', 1)[0] + '/licenses/'
            for filename in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
                path = source / filename
                if not path.is_file():
                    continue  # Historical release fixtures predate project terms.
                target = prefix + filename
                if target not in names:
                    raise ReleaseError('wheel is missing license document: ' + target
                                       + '; build with setuptools>=77')
                if archive.read(target) != path.read_bytes():
                    raise ReleaseError('wheel license differs from clean source: ' + filename)
    except (OSError, zipfile.BadZipFile) as error:
        raise ReleaseError('invalid wheel archive') from error


def _source_archive(source: Path, destination: Path, version: str) -> None:
    with destination.open('wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream,
                                                       mtime=315532800) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for path in sorted(source.rglob('*')):
                relative = path.relative_to(source)
                if any(_excluded(part) for part in relative.parts):
                    continue
                info = archive.gettarinfo(str(path), arcname='chaos-intelligence-' + version + '/' + str(relative))
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 315532800
                if path.is_file():
                    with path.open('rb') as file_stream:
                        archive.addfile(info, file_stream)
                else:
                    archive.addfile(info)


@contextmanager
def _publish_lock(output: Path):
    lock = output / '.release.lock'
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ReleaseError('another publication is active; inspect ' + str(lock) + ' before removing a stale lock') from error
    try:
        yield
    finally:
        lock.rmdir()


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_release(source: Path, output: Path, base_url: str, private_key: Path,
                  *, skip_tests: bool = False, include_source: bool = False,
                  include_portable: bool = False) -> dict:
    source, output = Path(source).resolve(), Path(output).resolve()
    try:
        parsed = urlsplit(base_url)
        parsed.port
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(character.isspace() for character in base_url)):
            raise ValueError('invalid base URL')
    except (ValueError, TypeError, AttributeError) as error:
        raise ReleaseError('base URL must be HTTPS without credentials, query, or fragment') from error
    if output == source or any(_inside(output, source / folder) for folder in SOURCE_DIRECTORIES):
        raise ReleaseError('output must be outside release source directories (for example dist/releases)')
    if _inside(Path(private_key).expanduser(), output):
        raise ReleaseError('the private key must be outside the release output directory')
    key = _private_key(Path(private_key), source)
    with tempfile.TemporaryDirectory(prefix='chaos-release-') as temporary:
        temporary = Path(temporary)
        snapshot = temporary / 'source'
        copy_source(source, snapshot)
        version = source_version(snapshot)
        environment = _build_environment(temporary / 'home')
        if not skip_tests:
            if not any((snapshot / 'tests').glob('test_*.py')):
                raise ReleaseError('release compatibility tests are missing')
            _run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], snapshot, environment)
        # Archive before the build backend creates build/ or egg-info directories.
        artifacts = temporary / 'artifacts'
        artifacts.mkdir()
        if include_source:
            _source_archive(snapshot, artifacts / ('chaos-intelligence-' + version + '-source.tar.gz'), version)
        wheel_directory = temporary / 'wheels'
        wheel_directory.mkdir()
        _run([sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps', '--no-build-isolation',
              '--wheel-dir', str(wheel_directory)], snapshot, environment)
        wheels = list(wheel_directory.glob('*.whl'))
        if len(wheels) != 1:
            raise ReleaseError('build must produce exactly one wheel')
        wheel = wheels[0]
        validate_wheel(wheel, snapshot, version)
        shutil.copyfile(wheel, artifacts / wheel.name)
        if include_portable:
            from .portable import build_portable
            build_portable(wheel, artifacts / ('chaos-intelligence-' + version + '-portable.zip'))
        wheel_data = wheel.read_bytes()
        manifest = sign_manifest({
            'schemaVersion': 1, 'version': version, 'channel': 'stable',
            'publishedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'apiVersion': 'v1', 'storageVersion': 1, 'installerVersion': 1,
            'artifacts': [{'kind': 'wheel', 'filename': wheel.name,
                           'url': base_url.rstrip('/') + '/' + quote(wheel.name),
                           'sha256': hashlib.sha256(wheel_data).hexdigest(), 'size': len(wheel_data)}],
        }, key)
        immutable = {path.name: path.read_bytes() for path in artifacts.iterdir()}
        checksums = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                            for name, data in sorted(immutable.items())).encode('ascii')
        for name, data in list(immutable.items()):
            immutable[name + '.sha256'] = (hashlib.sha256(data).hexdigest() + '  ' + name + '\n').encode('ascii')
        # Refuse version reuse before replacing any existing release files.
        output.mkdir(parents=True, exist_ok=True)
        with _publish_lock(output):
            existing_feed = output / 'feed.json'
            if existing_feed.is_file():
                try:
                    previous = json.loads(existing_feed.read_text(encoding='utf-8'))
                    if not isinstance(previous, dict) or not isinstance(previous.get('signature'), dict):
                        raise ValueError('invalid manifest')
                    previous_version = previous['version']
                    if not isinstance(previous_version, str) or not VERSION_PATTERN.fullmatch(previous_version):
                        raise ValueError('invalid version')
                    if tuple(map(int, previous_version.split('.'))) > tuple(map(int, version.split('.'))):
                        raise ReleaseError('refusing to move an existing feed to an older version')
                    if previous_version == version and (previous.get('artifacts') != manifest['artifacts']
                                                        or previous.get('signature', {}).get('keyId') != manifest['signature']['keyId']):
                        raise ReleaseError('version already published with different metadata; bump chaos/version.py')
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise ReleaseError('existing feed is invalid; inspect it before publishing') from error
            for name, data in immutable.items():
                existing = output / name
                if existing.is_symlink() or (existing.exists() and (not existing.is_file() or existing.read_bytes() != data)):
                    raise ReleaseError('immutable release already exists with different content: ' + name + '; bump chaos/version.py')
            manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
            for name, data in immutable.items():
                if not (output / name).exists():
                    _atomic_write(output / name, data)
            _atomic_write(output / 'SHA256SUMS', checksums)
            # The feed is always last, so clients never observe a not-yet-written wheel.
            _atomic_write(output / 'feed.json', manifest_data)
        return {'version': version, 'output': str(output), 'manifest': str(output / 'feed.json'),
                'wheel': str(output / wheel.name), 'keyId': manifest['signature']['keyId'],
                'testsRun': not skip_tests, 'published': False,
                **({'portable': str(output / ('chaos-intelligence-' + version + '-portable.zip'))} if include_portable else {})}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    keygen = commands.add_parser('keygen', help='create a maintainer signing key outside the checkout')
    keygen.add_argument('--private-key', type=Path, required=True)
    keygen.add_argument('--public-key', type=Path, required=True)
    keygen.add_argument('--source', type=Path, default=Path.cwd())
    build = commands.add_parser('build', help='test, build and sign a complete uploadable release')
    build.add_argument('--source', type=Path, default=Path.cwd())
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--base-url', required=True, help='HTTPS directory where release files will be uploaded')
    build.add_argument('--private-key', type=Path, required=True)
    build.add_argument('--include-source', action='store_true')
    build.add_argument('--include-portable', action='store_true', help='export the validated wheel as a relocatable import root')
    build.add_argument('--skip-tests', action='store_true', help='explicitly skip compatibility tests; use only after an independent passing run')
    args = parser.parse_args(argv)
    try:
        if args.command == 'keygen':
            result = generate_key(args.private_key, args.public_key, args.source)
        else:
            result = build_release(args.source, args.output, args.base_url, args.private_key,
                                   skip_tests=args.skip_tests, include_source=args.include_source, include_portable=args.include_portable)
    except ReleaseError as error:
        parser.exit(1, 'Release failed: ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
