"""A relocatable import root derived from the original wheel, never a second runtime."""
import argparse
import ast
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import tokenize
import unicodedata
import zipfile

SCHEMA = 'chaos.portable/1'
MAX_FILES, MAX_BYTES = 4096, 128 * 1024 * 1024
EXTRAS_FILES = ('run_chaos.py', 'requirements.txt', 'README.md')
LAUNCHER = b'''from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chaos.cli import main
if __name__ == '__main__':
    raise SystemExit(main())
'''
README = '''# Chaos portable package

Extract this complete directory before use. Keep chaos/ and *.dist-info together.
Python 3.9+ is required. The language core uses the standard library; full features
use the dependencies declared in manifest.json and requirements.txt. Python,
third-party dependencies, browsers, model credentials and GPU drivers are not bundled.

Install base dependencies in your application's environment:
    python -m pip install -r /absolute/path/to/this/requirements.txt

For a host application, add THIS extracted directory to sys.path, then use the
original `from chaos import Framework, Registry, Settings`. A package placed directly
in the host import root can be imported normally. Do not rename the chaos namespace.
CLI: python /absolute/path/to/this/run_chaos.py run /absolute/path/to/main.chaos
CLI/server/docs: the same launcher passes all arguments to the original Chaos CLI.
Use explicit --db, --config and --state-dir outside this package for persistent data.
For an in-memory embedded runtime, pass Settings(':memory:') explicitly.

Native, GPU and browser extras remain optional; their exact Requires-Dist markers
and Provides-Extra names are preserved in manifest.json and wheel METADATA.
Upgrade by extracting into a NEW versioned directory and restarting the host with
that import root. Extraction never merges with or replaces an existing destination.
Checksums detect changed bytes; use a trusted publisher to establish authenticity.
Direct ZIP import, renamed/nested namespaces and hot replacement are not supported.

Licensing: consult *.dist-info/licenses/LICENSE for this runtime's exact terms.
Where LicenseRef-Chaos-No-AI-Training-1.0 is supplied, the original Chaos code is
source-available and its use for AI/model training is prohibited by that license.
Third-party components retain their own licenses and are excluded from that
restriction. See *.dist-info/licenses/THIRD_PARTY_NOTICES.md and the upstream
license files under that directory, chaos/vendor/, and chaos/web/vendor/.
Keep all notices with redistributions. These terms do not license your own programs.
For historical packages, the license documents bundled with that version govern.
'''.encode('utf-8')


class PackageError(ValueError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _path(name):
    if (not isinstance(name, str) or not name or len(name) > 500 or
            any(ord(c) < 32 or ord(c) == 127 for c in name) or any(c in name for c in '\\:<>"|?*')):
        raise PackageError('Invalid package path')
    value = PurePosixPath(name)
    if value.is_absolute() or str(value) != name or any(p in ('.', '..') for p in value.parts):
        raise PackageError('Package path must be canonical and relative')
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    if any(p.endswith((' ', '.')) or p.split('.')[0].casefold() in reserved for p in value.parts):
        raise PackageError('Package path is not portable')
    return name


def _names(names):
    if len(names) > MAX_FILES:
        raise PackageError('Package has too many files')
    seen = set()
    for name in names:
        folded = unicodedata.normalize('NFC', _path(name)).casefold()
        if folded in seen:
            raise PackageError('Duplicate or case-colliding package path')
        seen.add(folded)
    if any('/'.join(name.split('/')[:i]) in seen for name in seen for i in range(1, len(name.split('/')))):
        raise PackageError('Package file/directory path collision')


def _zip(path):
    path = Path(path)
    if path.is_symlink():
        raise PackageError('Package archive must not be a symlink')
    try:
        status = path.stat()
        if not stat.S_ISREG(status.st_mode):
            raise PackageError('Package archive must be a regular file')
        if status.st_size > MAX_BYTES + 1048576:
            raise PackageError('Archive exceeds byte limit')
        with zipfile.ZipFile(path) as archive:
            items = archive.infolist()
            _names([item.filename for item in items])
            if sum(item.file_size for item in items) > MAX_BYTES:
                raise PackageError('Package exceeds byte limit')
            for item in items:
                mode = item.external_attr >> 16
                if item.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or item.flag_bits & 1:
                    raise PackageError('Package must contain regular unencrypted files only')
            return {item.filename: archive.read(item) for item in items}
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise PackageError('Cannot read package archive') from error


def _wheel(files):
    if len(files) > MAX_FILES - len(EXTRAS_FILES) - 1:
        raise PackageError('Wheel has too many files for portable metadata')
    metadata_paths = [n for n in files if n.endswith('.dist-info/METADATA')]
    if len(metadata_paths) != 1:
        raise PackageError('Expected one wheel METADATA')
    prefix = metadata_paths[0].split('/')[0]
    metadata = BytesParser().parsebytes(files[metadata_paths[0]])
    if metadata.get_all('Name') != ['chaos-intelligence'] or len(metadata.get_all('Version', [])) != 1:
        raise PackageError('Expected chaos-intelligence wheel metadata')
    version = metadata['Version']
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version) or prefix != 'chaos_intelligence-' + version + '.dist-info':
        raise PackageError('Wheel version or metadata directory mismatch')
    if any(not n.startswith(('chaos/', prefix + '/')) for n in files):
        raise PackageError('Unexpected wheel payload outside chaos package')
    expression = metadata.get('License-Expression')
    for name in metadata.get_all('License-File', []):
        name = _path(name)
        # PEP 639 preserves complete relative paths. Older wheel builders used
        # flat dist-info entries, which remain readable for historical wheels.
        if (prefix + '/licenses/' + name not in files
                and (expression or prefix + '/' + name not in files)):
            raise PackageError('Wheel is missing declared license file: ' + name)
    if expression == 'LicenseRef-Chaos-No-AI-Training-1.0':
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            if prefix + '/licenses/' + name not in files:
                raise PackageError('Wheel is missing Chaos license document: ' + name)
    required = ('chaos/__init__.py', 'chaos/__main__.py', 'chaos/version.py', 'chaos/stdlib/swarm.chaos',
                'chaos/ui_catalog.json', 'chaos/docs/catalog.json', 'chaos/web/index.html', prefix + '/WHEEL', prefix + '/RECORD')
    if any(n not in files for n in required) or not any(n.startswith('chaos/examples/') for n in files) or not any(n.startswith('chaos/vendor/') for n in files):
        raise PackageError('Wheel is missing required runtime resources')
    try:
        tree = ast.parse(files['chaos/version.py'].decode('utf-8'))
        values = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
        if values != [version]:
            raise PackageError('Runtime version differs from wheel metadata')
        rows = list(csv.reader(io.StringIO(files[prefix + '/RECORD'].decode('utf-8'))))
        if any(len(row) != 3 for row in rows) or len(rows) != len(files) or {row[0] for row in rows} != set(files):
            raise PackageError('Wheel RECORD does not match payload')
        for name, digest, size in rows:
            if name == prefix + '/RECORD':
                if digest or size:
                    raise PackageError('RECORD self hash must be empty')
            elif digest != 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b'=').decode() or size != str(len(files[name])):
                raise PackageError('Wheel RECORD integrity mismatch: ' + name)
    except (UnicodeError, SyntaxError, ValueError, TypeError) as error:
        if isinstance(error, PackageError):
            raise
        raise PackageError('Invalid wheel version or RECORD') from error
    requirements = metadata.get_all('Requires-Dist', [])
    if any('\n' in r or '\r' in r or r.startswith('-') for r in requirements):
        raise PackageError('Invalid requirement metadata')
    base = []
    for requirement in requirements:
        marker = requirement.partition(';')[2]
        try:
            variables = {t.string for t in tokenize.generate_tokens(io.StringIO(marker).readline) if t.type == tokenize.NAME}
        except (tokenize.TokenError, IndentationError) as error:
            raise PackageError('Invalid requirement marker') from error
        if 'extra' not in variables:
            base.append(requirement)
    return {'version': version, 'requiresPython': metadata.get('Requires-Python'),
            'requirements': requirements, 'baseRequirements': base, 'extras': metadata.get_all('Provides-Extra', [])}


def _manifest(files):
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise PackageError('Duplicate manifest key')
                result[key] = value
            return result
        manifest = json.loads(files['manifest.json'].decode('utf-8'), object_pairs_hook=pairs)
        if type(manifest) is not dict or manifest.get('schema') != SCHEMA or type(manifest.get('files')) is not dict:
            raise PackageError('Invalid portable manifest')
        payload = {n: data for n, data in files.items() if n != 'manifest.json'}
        if set(payload) != set(manifest['files']):
            raise PackageError('Portable manifest file set mismatch')
        for name, data in payload.items():
            row = manifest['files'][name]
            if type(row) is not dict or type(row.get('size')) is not int or row != {'sha256': _sha(data), 'size': len(data)}:
                raise PackageError('Portable integrity mismatch: ' + name)
        if any(name not in payload for name in EXTRAS_FILES):
            raise PackageError('Missing portable entry point, documentation or dependencies')
        wheel_files = {n: data for n, data in payload.items() if n not in EXTRAS_FILES}
        info = _wheel(wheel_files)
        if any(manifest.get(k) != v for k, v in info.items()):
            raise PackageError('Portable metadata or launcher mismatch')
        if payload['requirements.txt'] != ('\n'.join(info['baseRequirements']) + '\n').encode('utf-8'):
            raise PackageError('Portable dependencies differ from wheel metadata')
        return manifest
    except (KeyError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise PackageError('Invalid portable manifest') from error


def build_portable(wheel, output):
    """Validate a wheel, then publish a new immutable ZIP without installing anything."""
    files = _zip(wheel)
    info = _wheel(files)
    files.update({'run_chaos.py': LAUNCHER, 'README.md': README,
                  'requirements.txt': ('\n'.join(info['baseRequirements']) + '\n').encode('utf-8')})
    manifest = {'schema': SCHEMA, **info, 'wheelSha256': _sha(Path(wheel).read_bytes()),
                'files': {n: {'sha256': _sha(data), 'size': len(data)} for n, data in sorted(files.items())}}
    files['manifest.json'] = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise PackageError('Refusing existing portable archive')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.chaos-package-', dir=output.parent)
    try:
        with os.fdopen(fd, 'wb') as stream, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0));item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(item, data)
        os.link(temporary, output)  # An existing file or symlink can never be replaced.
    except OSError as error:
        raise PackageError('Cannot publish new portable archive') from error
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'schema': SCHEMA, **info, 'archive': str(output), 'sha256': _sha(output.read_bytes()), 'files': len(files), 'wheelSha256': manifest['wheelSha256']}


def extract_portable(archive, destination):
    """Claim a fresh directory only after whole-payload validation; manifest commits last."""
    files = _zip(archive);_manifest(files)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise PackageError('Refusing existing package destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.chaos-package-stage-', dir=destination.parent) as folder:
        stage = Path(folder)
        for name, data in files.items():
            path = stage / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(data);path.chmod(0o644)
        try:
            destination.mkdir()  # Exclusive claim: concurrent extractors cannot overlay a tree.
        except OSError as error:
            raise PackageError('Cannot claim a new package destination') from error
        try:
            for path in sorted(stage.iterdir()):
                if path.name != 'manifest.json':
                    shutil.move(str(path), str(destination / path.name))
            shutil.move(str(stage / 'manifest.json'), str(destination / 'manifest.json'))
        except BaseException:
            shutil.rmtree(destination)
            raise
    return verify_portable(destination)


def verify_portable(root):
    """Read and verify owned immutable payload; Python bytecode caches are ignored."""
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise PackageError('Expected an extracted regular package directory')
    files = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise PackageError('Portable package contains a symlink')
        if '__pycache__' in relative.parts:
            continue
        if path.is_file():
            if len(files) >= MAX_FILES or sum(map(len, files.values())) + path.stat().st_size > MAX_BYTES:
                raise PackageError('Portable package exceeds limits')
            files[relative.as_posix()] = path.read_bytes()
        elif not path.is_dir():
            raise PackageError('Portable package contains a non-regular entry')
    _names(list(files));manifest = _manifest(files)
    return {'schema': SCHEMA, 'version': manifest['version'], 'root': str(root), 'files': len(files),
            'requiresPython': manifest['requiresPython'], 'requirements': manifest['requirements'],
            'extras': manifest['extras'], 'verified': True}



def build_project(wheel, output):
    """Wrap the same verified portable runtime in an editable project directory."""
    from .project import project_files
    from .launchers import executable_mode
    files = _zip(wheel)
    info = _wheel(files)
    if any(name not in files for name in ('chaos/project.py', 'chaos/project_launcher.py', 'chaos/project_environment.py', 'chaos/launchers.py', 'chaos/docs/PROJECT_README.md')):
        raise PackageError('Wheel does not support directory projects')
    starter = project_files()
    if (starter['chaos.py'] != files['chaos/project_launcher.py']
            or Path(__file__).with_name('launchers.py').read_bytes() != files['chaos/launchers.py']):
        raise PackageError('Build projects with the matching runtime version')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise PackageError('Refusing existing project archive')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.chaos-project-', dir=output.parent) as folder:
        stage = Path(folder)
        portable = stage / 'runtime.zip'
        build_portable(wheel, portable)
        payload = {'.chaos/runtime/' + name:data for name,data in _zip(portable).items()}
        payload.update(starter)
        payload['.chaos/bundled-runtime'] = b'chaos.project/1\n'
        payload['README.md'] = files['chaos/docs/PROJECT_README.md']
        payload['.gitignore'] = b'.chaos/\n.chaos-workspace.json\n.chaos-sync.lock\n.chaos-before-pull.json\n.chaos-conflict.json\n.chaos-replaced-*/\n__pycache__/\n'
        _names(list(payload))
        archive = stage / 'project.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
            for name, data in sorted(payload.items()):
                item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.create_system = 3
                item.external_attr = (stat.S_IFREG | executable_mode(name)) << 16
                stream.writestr(item, data)
        os.link(archive, output)
    return {'schema':'chaos.project/1', 'version':info['version'], 'archive':str(output),
            'sha256':_sha(output.read_bytes()), 'files':len(payload)}

def main(argv=None):
    parser = argparse.ArgumentParser(prog='chaos package', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build');build.add_argument('wheel');build.add_argument('--output', required=True)
    project = commands.add_parser('project', help='Build a ready-to-edit project ZIP');project.add_argument('wheel');project.add_argument('--output', required=True)
    extract = commands.add_parser('extract');extract.add_argument('archive');extract.add_argument('--destination', required=True)
    verify = commands.add_parser('verify');verify.add_argument('root')
    args = parser.parse_args(argv)
    try:
        result = (build_project(args.wheel, args.output) if args.command == 'project' else
                  build_portable(args.wheel, args.output) if args.command == 'build' else
                  extract_portable(args.archive, args.destination) if args.command == 'extract' else verify_portable(args.root))
    except (PackageError, OSError) as error:
        parser.exit(1, 'Package failed: ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2));return 0
