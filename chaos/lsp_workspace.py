"""Read-only file confinement and transactional UTF-16 document overlays."""
import bisect
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .language import LanguageError, Parser, tokenize
from .modules import STDLIB, path_name, resolve_path


MAX_FILES = 64
MAX_SOURCE_CHARS = 100000
MAX_BUNDLE_BYTES = 1000000


def integer(value):
    return type(value) is int and -(2 ** 31) <= value < 2 ** 31


def source_text(value):
    if not isinstance(value, str):
        raise ValueError('Document text must be a string')
    if len(value) > MAX_SOURCE_CHARS:
        raise ValueError('Source file exceeds 100000 characters')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('Document contains invalid Unicode') from None
    return value


class TextMap:
    """Translate LSP line/UTF-16 positions and analyzer global UTF-16 spans."""
    def __init__(self, text):
        self.text = text
        self.units = [0]
        for char in text:
            self.units.append(self.units[-1] + (2 if ord(char) > 0xffff else 1))
        self.starts, self.ends = [0], []
        for match in re.finditer(r'\r\n|\r|\n', text):
            self.ends.append(match.start())
            self.starts.append(match.end())
        self.ends.append(len(text))

    def offset(self, position):
        if not isinstance(position, dict):
            raise ValueError('Position must be an object')
        line, character = position.get('line'), position.get('character')
        if not integer(line) or not integer(character) or line < 0 or character < 0:
            raise ValueError('Position requires nonnegative integer line and character')
        if line >= len(self.starts):
            raise ValueError('Position line is outside the document')
        start, end = self.starts[line], self.ends[line]
        unit = min(self.units[start] + character, self.units[end])
        offset = bisect.bisect_left(self.units, unit, start, end + 1)
        if self.units[offset] != unit:
            raise ValueError('Position splits a UTF-16 surrogate pair')
        return offset

    def position(self, unit):
        unit = max(0, min(unit, self.units[-1]))
        offset = bisect.bisect_left(self.units, unit)
        line = bisect.bisect_right(self.starts, offset) - 1
        return {'line': line, 'character': self.units[min(offset, self.ends[line])] - self.units[self.starts[line]]}

    def span(self, start, end):
        return {'start': self.position(start), 'end': self.position(end)}

    def pieces(self, start, end):
        """Split semantic spans into nonempty single-line UTF-16 tokens."""
        first = self.position(start)['line']
        last = self.position(end)['line']
        for line in range(first, last + 1):
            base = self.units[self.starts[line]]
            left, right = max(start, base), min(end, self.units[self.ends[line]])
            if right > left:
                yield line, left - base, right - left


def changed_text(text, changes):
    if not isinstance(changes, list) or len(changes) > 1024:
        raise ValueError('contentChanges must be a bounded array')
    # Nothing is committed until every successive edit has been checked.
    for change in changes:
        if not isinstance(change, dict) or 'text' not in change:
            raise ValueError('Each content change must contain text')
        replacement = source_text(change['text'])
        if 'range' not in change:
            if 'rangeLength' in change:
                raise ValueError('rangeLength requires range')
            text = replacement
        else:
            mapping = TextMap(text)
            span = change['range']
            if not isinstance(span, dict):
                raise ValueError('Change range must be an object')
            start, end = mapping.offset(span.get('start')), mapping.offset(span.get('end'))
            if start > end:
                raise ValueError('Change range is reversed')
            if 'rangeLength' in change:
                length = change['rangeLength']
                if not integer(length) or length < 0 or length != mapping.units[end] - mapping.units[start]:
                    raise ValueError('rangeLength does not match the UTF-16 range')
            text = source_text(text[:start] + replacement + text[end:])
    return text


def uri_path(uri):
    if not isinstance(uri, str) or len(uri) > 8192 or re.search(r'%(?![0-9a-fA-F]{2})', uri):
        raise ValueError('Invalid file URI')
    parsed = urlsplit(uri)
    if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost') or parsed.query or parsed.fragment:
        raise ValueError('Only local file URIs are supported')
    try:
        path = unquote(parsed.path, encoding='utf-8', errors='strict')
    except UnicodeError:
        raise ValueError('Invalid file URI encoding') from None
    if not path.startswith('/') or '\x00' in path or '\\' in path:
        raise ValueError('Invalid absolute file path')
    return Path(os.path.abspath(path))


def root_path(value):
    path = Path(value).absolute()
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Workspace root must be an existing nonsymlink directory')
    return path.resolve()


def module_name(name):
    if path_name(name) != name or not name.endswith('.chaos') or name.startswith('std/'):
        raise ValueError('Document must be a canonical .chaos module')
    if any(part in ('.git', '.chaos') or part.startswith('.chaos-replaced-') for part in Path(name).parts):
        raise ValueError('Reserved workspace path')
    return name


def confined(root, path):
    try:
        name = path.relative_to(root).as_posix()
    except ValueError:
        raise ValueError('Document is outside the initialized workspace roots') from None
    module_name(name)
    current = root
    for part in Path(name).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Workspace symlinks are not allowed')
    return name


def read_source(root, name):
    """Open each component relative to an anchored directory, without symlinks."""
    module_name(name)
    descriptors = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(root.anchor, directory_flags)
        descriptors.append(fd)
        for part in root.parts[1:]:
            fd = os.open(part, directory_flags, dir_fd=fd)
            descriptors.append(fd)
        parts = Path(name).parts
        for part in parts[:-1]:
            fd = os.open(part, directory_flags, dir_fd=fd)
            descriptors.append(fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(file_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * MAX_SOURCE_CHARS:
            raise ValueError('Source is not a regular bounded file')
        chunks, count = [], 0
        while True:
            block = os.read(file_fd, min(65536, 4 * MAX_SOURCE_CHARS + 1 - count))
            if not block:
                break
            chunks.append(block)
            count += len(block)
            if count > 4 * MAX_SOURCE_CHARS:
                raise ValueError('Source file exceeds size limit')
        try:
            return source_text(b''.join(chunks).decode('utf-8'))
        except UnicodeError:
            raise ValueError('Source file is not UTF-8') from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@dataclass(frozen=True)
class Document:
    uri: str
    root: Path
    name: str
    text: str
    version: int


@dataclass(frozen=True)
class Snapshot:
    uri: str
    key: str
    root: Path
    name: str
    version: object
    generation: int
    overlays: dict
    entry: object
    language_version: int
    document_uris: dict
    document_versions: dict

    def load(self):
        files, attempted = {}, set()
        entry = self.entry or self.name
        pending = [self.name, entry]
        while pending:
            name = pending.pop()
            if name in attempted:
                continue
            attempted.add(name)
            if len(attempted) > MAX_FILES:
                raise ValueError('Workspace dependency graph exceeds 64 modules')
            confined(self.root, self.root / name)
            try:
                text = self.overlays[name] if name in self.overlays else read_source(self.root, name)
            except FileNotFoundError:
                if name in (self.name, entry):
                    raise ValueError('Document or configured entry does not exist') from None
                # The shared analyzer reports missing imports at their source.
                continue
            files[name] = text
            self._limits(files)
            tokens = tokenize(text, tolerant=True)
            for index, token in enumerate(tokens[:-1]):
                if token.value != 'import' or tokens[index + 1].kind != 'string':
                    continue
                try:
                    target = resolve_path(Parser.string(tokens[index + 1].value), name)
                except (LanguageError, ValueError, SyntaxError):
                    continue
                if target not in STDLIB:
                    pending.append(target)
        return files, entry

    def load_package(self, checkpoint=None):
        """Read every bounded project module, including reverse importers.

        Enumeration and descent stay anchored to directory descriptors. Internal
        state and the sealed std tree are not project modules; symlinks refuse
        the operation, so no incomplete package is reported as a full rename.
        """
        check=checkpoint or (lambda:None)
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        descriptors=[];names=[];visited=[0]
        try:
            fd=os.open(self.root.anchor,flags);descriptors.append(fd)
            for part in self.root.parts[1:]:
                fd=os.open(part,flags,dir_fd=fd);descriptors.append(fd)
            def walk(directory,prefix='',depth=0):
                check()
                if depth>32:raise ValueError('Workspace directory depth exceeds32')
                entries=[]
                with os.scandir(directory)as iterator:
                    for item in iterator:
                        check();visited[0]+=1
                        if visited[0]>8192:raise ValueError('Workspace directory enumeration exceeds8192 entries')
                        if item.name in ('.git','.chaos') or item.name.startswith('.chaos-replaced-') or (not prefix and item.name=='std'):continue
                        entries.append(item.name)
                for name in sorted(entries):
                    check();relative=prefix+name;info=os.stat(name,dir_fd=directory,follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):raise ValueError('Workspace symlinks are not allowed for whole-package refactoring')
                    if stat.S_ISDIR(info.st_mode):
                        child=os.open(name,flags,dir_fd=directory)
                        try:walk(child,relative+'/',depth+1)
                        finally:os.close(child)
                    elif name.endswith('.chaos'):
                        if not stat.S_ISREG(info.st_mode):raise ValueError('Project modules must be regular files')
                        module_name(relative);names.append(relative)
                        if len(names)>MAX_FILES:raise ValueError('Workspace package exceeds64 modules')
            walk(fd)
        finally:
            for descriptor in reversed(descriptors):os.close(descriptor)
        files={}
        for name in sorted(set(names)|set(self.overlays)):
            check();confined(self.root,self.root/name)
            files[name]=self.overlays[name]if name in self.overlays else read_source(self.root,name)
            self._limits(files)
        entry=self.entry or self.name
        if self.name not in files or entry not in files:raise ValueError('Document or configured entry does not exist')
        return files,entry

    @staticmethod
    def _limits(files):
        if len(files) > MAX_FILES or sum(len(text.encode('utf-8')) for text in files.values()) > MAX_BUNDLE_BYTES:
            raise ValueError('Workspace exceeds 64 modules or 1000000 UTF-8 bytes')


class Workspace:
    """State is owned by the server lock; workers only receive snapshots."""
    def __init__(self, roots, entry=None, language_version=2):
        self.roots = tuple(sorted(set(roots), key=lambda p: len(str(p)), reverse=True))
        self.entry = module_name(entry) if entry is not None else None
        self.language_version = language_version
        self.documents = {}
        self.generation = 0

    def locate(self, uri):
        path = uri_path(uri)
        # Normalize aliases in ancestor paths such as macOS /var -> /private/var.
        # Components inside a configured root still go through confined checks.
        for root in self.roots:
            try:
                name = confined(root, path)
            except ValueError:
                if root not in path.parents:
                    for ancestor in path.parents:
                        try:
                            resolved = ancestor.resolve()
                        except (RuntimeError, OSError):
                            continue
                        if resolved == root:
                            name = confined(ancestor, path)
                            return (root / name).as_uri(), root, name
                    continue
                raise
            return (root / name).as_uri(), root, name
        raise ValueError('Document is outside the initialized workspace roots')

    def open(self, item):
        if not isinstance(item, dict) or not integer(item.get('version')) or not isinstance(item.get('languageId'), str):
            raise ValueError('didOpen requires a version and languageId')
        uri, root, name = self.locate(item.get('uri'))
        if uri in self.documents:
            raise ValueError('Document is already open')
        if len(self.documents) >= MAX_FILES:
            raise ValueError('At most 64 documents may be open')
        text = source_text(item.get('text'))
        if sum(len(d.text.encode('utf-8')) for d in self.documents.values()) + len(text.encode('utf-8')) > MAX_BUNDLE_BYTES:
            raise ValueError('Open documents exceed 1000000 UTF-8 bytes')
        self.documents[uri] = Document(item['uri'], root, name, text, item['version'])
        self.generation += 1
        return uri

    def change(self, item, changes):
        if not isinstance(item, dict) or not integer(item.get('version')):
            raise ValueError('didChange requires an integer version')
        uri, _, _ = self.locate(item.get('uri'))
        document = self.documents.get(uri)
        if document is None:
            raise ValueError('Document is not open')
        if item['version'] <= document.version:
            raise ValueError('Document version must increase')
        text = changed_text(document.text, changes)
        size = sum(len(d.text.encode('utf-8')) for key, d in self.documents.items() if key != uri) + len(text.encode('utf-8'))
        if size > MAX_BUNDLE_BYTES:
            raise ValueError('Open documents exceed 1000000 UTF-8 bytes')
        self.documents[uri] = Document(document.uri, document.root, document.name, text, item['version'])
        self.generation += 1
        return uri

    def close(self, item):
        if not isinstance(item, dict):
            raise ValueError('textDocument must be an object')
        uri, _, _ = self.locate(item.get('uri'))
        if uri not in self.documents:
            raise ValueError('Document is not open')
        document = self.documents.pop(uri)
        self.generation += 1
        return document.uri

    def snapshot(self, uri):
        key, root, name = self.locate(uri)
        document = self.documents.get(key)
        overlays = {d.name: d.text for d in self.documents.values() if d.root == root}
        document_uris = {d.name: d.uri for d in self.documents.values() if d.root == root}
        return Snapshot(document.uri if document else uri, key, root, name,
                        document.version if document else None, self.generation,
                        overlays, self.entry, self.language_version, document_uris, {d.name:d.version for d in self.documents.values()if d.root==root})
