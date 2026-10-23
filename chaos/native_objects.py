"""Explicit host-private authenticated native objects, never wire executable input.

Only trusted native builders call _get_or_emit. Cache data is authenticated before
LLVM sees it. The filesystem secret is host-local trust, not a self-signed claim
that arbitrary machine code is safe. This optional store currently requires POSIX.
"""
from contextlib import contextmanager
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time

_SCHEMA = 'chaos.native-object-cache/1'
_ENTRY = re.compile(r'[0-9a-f]{64}\.entry\Z')
_TEMPORARY = re.compile(r'\.[0-9a-f]{64}\.[0-9a-f]{24}\.tmp\Z')
_METADATA_KEYS = frozenset(('schema', 'keyHash', 'objectHash', 'objectSize', 'authTag'))


class _Rejected(Exception):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _private(st, directory=False):
    wanted = 0o700 if directory else 0o600
    if (st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != wanted or
            not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)) or
            not directory and st.st_nlink != 1):
        raise OSError(errno.EPERM, 'Native object cache requires private owned files')


class NativeObjectCache:
    """Bounded disk store. Counters are per instance; disk inventory is current.

    clear() preserves the authentication key and never affects a linked kernel.
    NativeCodeCache.clear() independently clears only its in-memory references.
    """
    def __init__(self, directory, *, max_entries=32, max_object_bytes=4000000,
                 max_total_bytes=16000000, lock_timeout=10.0):
        for value, maximum in ((max_entries, 64), (max_object_bytes, 16000000),
                               (max_total_bytes, 64000000)):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError('Native object cache limits must be bounded nonnegative integers')
        if type(lock_timeout) not in (int, float) or not 0 < lock_timeout <= 60:
            raise ValueError('Native object lock timeout must be within60 seconds')
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            raise OSError(errno.ENOTSUP, 'Native object persistence requires POSIX private storage')
        if not isinstance(directory, (str, os.PathLike)):
            raise TypeError('Native object cache directory must be a host filesystem path')
        self._limits = (max_entries, max_object_bytes, max_total_bytes)
        self._timeout = float(lock_timeout)
        self._thread_lock = threading.RLock(); self._depth = 0
        self._root_fd = self._lock_fd = None
        self._hits = self._misses = self._writes = self._evictions = 0
        self._invalidations = self._oversize = self._generation = 0
        # Resolve existing parents (including system /var aliases), then open
        # every physical component with directory FDs and O_NOFOLLOW.
        requested = Path(directory).expanduser().absolute()
        parent = requested.parent.resolve(strict=True)
        name = requested.name
        if name in ('', '.', '..') or requested == requested.parent:
            raise ValueError('Native object cache requires its own private directory')
        self._directory = str(parent / name)
        parent_fd = self._open_physical(parent)
        try:
            try: os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError: pass
            self._root_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            _private(os.fstat(self._root_fd), True)
        except BaseException:
            self.close(); raise
        finally: os.close(parent_fd)
        try:
            # Separate atomic creation from opening an existing lock. Concurrent
            # O_CREAT|O_NOFOLLOW lookup can report ENOENT on macOS; O_EXCL
            # chooses one creator without weakening the no-follow boundary.
            try:
                self._lock_fd = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._root_fd)
            except FileExistsError:
                self._lock_fd = os.open('lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=self._root_fd)
            _private(os.fstat(self._lock_fd))
            with self._locked():
                try: self._secret = self._read(self._root_fd, 'secret', 32)
                except _Rejected:
                    raise OSError(errno.EPERM, 'Invalid native object cache authentication key') from None
                except FileNotFoundError:
                    key = secrets.token_bytes(32)
                    self._write(self._root_fd, 'secret', key)
                    os.fsync(self._root_fd); self._secret = key
                if len(self._secret) != 32:
                    raise OSError(errno.EPERM, 'Invalid native object cache authentication key')
        except BaseException:
            self.close(); raise

    @staticmethod
    def _open_physical(path):
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in path.parts[1:]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = child
            return fd
        except BaseException:
            os.close(fd); raise

    @property
    def directory(self): return self._directory
    @property
    def max_entries(self): return self._limits[0]
    @property
    def max_object_bytes(self): return self._limits[1]
    @property
    def max_total_bytes(self): return self._limits[2]

    @contextmanager
    def _locked(self):
        import fcntl
        with self._thread_lock:
            if self._root_fd is None or self._lock_fd is None:
                raise OSError(errno.EBADF, 'Native object cache is closed')
            _private(os.fstat(self._root_fd), True); _private(os.fstat(self._lock_fd))
            outer = not self._depth
            if outer:
                until = time.monotonic() + self._timeout
                while True:
                    try: fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                    except BlockingIOError:
                        if time.monotonic() >= until: raise OSError(errno.ETIMEDOUT, 'Native object cache lock timed out')
                        time.sleep(min(0.01, max(0, until - time.monotonic())))
            self._depth += 1
            try:
                if outer:
                    self._cleanup_temporaries()
                    self._trim()
                yield
            finally:
                self._depth -= 1
                if not self._depth: fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    @staticmethod
    def _read(parent, name, maximum):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd); _private(before)
            if before.st_size > maximum: raise _Rejected('oversized native object cache file')
            chunks = []; remaining = maximum + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk: break
                chunks.append(chunk); remaining -= len(chunk)
            data = b''.join(chunks); after = os.fstat(fd)
            if (len(data) > maximum or len(data) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise _Rejected('native object cache file changed during read')
            return data
        finally: os.close(fd)

    @staticmethod
    def _write(parent, name, data):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            _private(os.fstat(fd)); offset = 0
            while offset < len(data): offset += os.write(fd, data[offset:])
            os.fsync(fd)
        finally: os.close(fd)

    def _open_entry(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._root_fd)
        try: _private(os.fstat(fd), True)
        except BaseException: os.close(fd); raise
        return fd

    def _cleanup_temporaries(self):
        # A previous process may die between mkdir and atomic publication.
        # Only exact cache-owned names/content are reclaimed, under the lock.
        with os.scandir(self._root_fd) as entries:
            for _, entry in zip(range(256), entries):
                if _TEMPORARY.fullmatch(entry.name): self._remove(entry.name)

    def _entries(self):
        result = []; count = 0
        with os.scandir(self._root_fd) as entries:
            for entry in entries:
                count += 1
                if count > 256: raise OSError(errno.EFBIG, 'Native object cache directory exceeds scan limit')
                if not _ENTRY.fullmatch(entry.name): continue
                fd = self._open_entry(entry.name)
                try:
                    try: st = os.stat('object.o', dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        self._invalidations += 1; self._remove(entry.name)
                        continue
                    _private(st)
                    result.append((entry.name, st.st_size, st.st_mtime_ns))
                finally: os.close(fd)
        return result

    def _trim(self):
        entries = sorted(self._entries(), key=lambda row: (row[2], row[0]))
        kept = []
        for row in entries:
            if row[1] > self.max_object_bytes:
                self._remove(row[0]); self._evictions += 1
            else: kept.append(row)
        total = sum(row[1] for row in kept)
        while kept and (len(kept) > self.max_entries or total > self.max_total_bytes):
            name, size, _ = kept.pop(0)
            self._remove(name); total -= size; self._evictions += 1

    def _remove(self, name):
        try: fd = self._open_entry(name)
        except FileNotFoundError: return
        except OSError:
            st = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode): os.unlink(name, dir_fd=self._root_fd); return
            raise
        try:
            # Never recursively remove arbitrary user content, even in our dir.
            with os.scandir(fd) as entries:
                names = [entry.name for _, entry in zip(range(4), entries)]
            if set(names) - {'object.o', 'metadata.json'}:
                raise OSError(errno.EPERM, 'Unexpected content in native object cache entry')
            for child in ('object.o', 'metadata.json'):
                try: os.unlink(child, dir_fd=fd)
                except FileNotFoundError: pass
        finally: os.close(fd)
        os.rmdir(name, dir_fd=self._root_fd)

    def _authenticated(self, name, key_hash):
        fd = self._open_entry(name)
        try:
            metadata_bytes = self._read(fd, 'metadata.json', 4096)
            try: metadata = json.loads(metadata_bytes)
            except (ValueError, UnicodeError): raise _Rejected('invalid native object metadata') from None
            if type(metadata) is not dict or set(metadata) != _METADATA_KEYS:
                raise _Rejected('invalid native object metadata keys')
            if (metadata['schema'] != _SCHEMA or metadata['keyHash'] != key_hash or
                type(metadata['objectSize']) is not int or not 0 < metadata['objectSize'] <= self.max_object_bytes or
                any(type(metadata[key]) is not str or re.fullmatch(r'[0-9a-f]{64}', metadata[key]) is None
                    for key in ('keyHash', 'objectHash', 'authTag'))):
                raise _Rejected('native object metadata mismatch')
            data = self._read(fd, 'object.o', self.max_object_bytes)
            unsigned = {key: value for key, value in metadata.items() if key != 'authTag'}
            tag = hmac.new(self._secret, b'chaos-native-object/1\0' + _canonical(unsigned) + b'\0' + data, hashlib.sha256).hexdigest()
            if (len(data) != metadata['objectSize'] or hashlib.sha256(data).hexdigest() != metadata['objectHash'] or
                    not hmac.compare_digest(tag, metadata['authTag'])):
                raise _Rejected('native object authentication failed')
            return data
        finally: os.close(fd)

    def _get_or_emit(self, key, emit):
        if type(key) is not tuple or any(type(item) is not str for item in key):
            raise TypeError('Native object keys require an internal String tuple')
        key_hash = hashlib.sha256(_canonical(key)).hexdigest(); name = key_hash + '.entry'
        with self._locked():
            try:
                data = self._authenticated(name, key_hash)
                self._hits += 1; return data
            except FileNotFoundError:
                try: os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
                except FileNotFoundError: pass
                else:
                    self._invalidations += 1; self._remove(name)
            except (_Rejected, OSError):
                self._invalidations += 1; self._remove(name)
            self._misses += 1; generation = self._generation
            data = emit()
            if type(data) is not bytes or not data: raise TypeError('Trusted native emitter must return object bytes')
            if generation != self._generation: return data
            if (not self.max_entries or len(data) > self.max_object_bytes or len(data) > self.max_total_bytes):
                self._oversize += 1; return data
            entries = sorted(self._entries(), key=lambda row: (row[2], row[0]))
            total = sum(row[1] for row in entries)
            while entries and (len(entries) >= self.max_entries or total + len(data) > self.max_total_bytes):
                old, size, _ = entries.pop(0); self._remove(old); total -= size; self._evictions += 1
            unsigned = {'schema': _SCHEMA, 'keyHash': key_hash,
                'objectHash': hashlib.sha256(data).hexdigest(), 'objectSize': len(data)}
            metadata = dict(unsigned, authTag=hmac.new(self._secret,
                b'chaos-native-object/1\0' + _canonical(unsigned) + b'\0' + data, hashlib.sha256).hexdigest())
            temporary = '.' + key_hash + '.' + secrets.token_hex(12) + '.tmp'
            os.mkdir(temporary, 0o700, dir_fd=self._root_fd)
            fd = self._open_entry(temporary)
            try:
                self._write(fd, 'object.o', data); self._write(fd, 'metadata.json', _canonical(metadata)); os.fsync(fd)
                os.rename(temporary, name, src_dir_fd=self._root_fd, dst_dir_fd=self._root_fd)
                os.fsync(self._root_fd); self._writes += 1
            except BaseException:
                self._remove(temporary); raise
            finally: os.close(fd)
            return data

    def info(self):
        with self._locked():
            entries = self._entries()
            return {'schema': _SCHEMA, 'entries': len(entries), 'objectBytes': sum(row[1] for row in entries),
                'hits': self._hits, 'misses': self._misses, 'writes': self._writes, 'evictions': self._evictions,
                'invalidations': self._invalidations, 'oversize': self._oversize,
                'limits': {'maxEntries': self.max_entries, 'maxObjectBytes': self.max_object_bytes,
                           'maxTotalBytes': self.max_total_bytes}}

    def clear(self):
        with self._locked():
            for name, _, _ in self._entries(): self._remove(name)
            self._generation += 1; os.fsync(self._root_fd)

    def close(self):
        lock = getattr(self, '_thread_lock', None)
        if lock is None: return
        with lock:
            if self._depth: raise RuntimeError('Cannot close native object cache during an active operation')
            for field in ('_lock_fd', '_root_fd'):
                fd = getattr(self, field, None)
                if fd is not None:
                    setattr(self, field, None); os.close(fd)

    def __del__(self):
        try: self.close()
        except Exception: pass
