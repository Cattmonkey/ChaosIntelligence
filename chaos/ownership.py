"""One active runtime owner per SQLite file; prevents false restart recovery."""
from pathlib import Path
import os


class RuntimeLease:
    def __init__(self, path):
        self.file = None
        if str(path) == ':memory:':
            return
        path = Path(path).resolve()
        self.file = open(str(path) + '.runtime.lock', 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0); self.file.write(b'0'); self.file.flush(); self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close(); self.file = None
            raise RuntimeError('Database already has an active Chaos runtime; reuse its Framework or connect through Client') from None

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def __del__(self):
        self.close()
