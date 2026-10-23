"""Invocation-owned pins for language values crossing the native Flow ABI.

These slots retain actual values, never addresses, copies or authority grants.
They are a bounded root lifetime contract, not a language allocator or collector.
"""
from dataclasses import dataclass
from itertools import count
from threading import RLock

_U32 = (1 << 32) - 1
_U64 = (1 << 64) - 1
_IDS = count(1)
_IDS_LOCK = RLock()


def _bad():
    from .language import LanguageError
    raise LanguageError('Invalid or retired native value root', code='ownership_error')


def _limit():
    from .language import ResourceLimit
    raise ResourceLimit('Native value root limit exceeded')


@dataclass(frozen=True)
class RootRef:
    domain: int
    token: int


class RootDomain:
    """A single invocation's strong roots, with domain and generation identity.

    A released slot is reused only with a new generation. Exhausted generations
    retire permanently. The process-wide domain sequence never wraps or resets.
    """
    def __init__(self, owner, epoch, *, max_slots=16384):
        if owner is None or type(epoch) is not int or epoch < 0:
            _bad()
        if type(max_slots) is not int or not 1 <= max_slots <= 65536:
            raise ValueError('max_slots must be an integer between 1 and 65536')
        with _IDS_LOCK:
            identity = next(_IDS)
        if identity > _U64:
            _limit()
        self._id, self._owner, self._epoch = identity, owner, epoch
        self._max_slots, self._closed = max_slots, False
        self._slots, self._free = [], []
        self._live = 0
        self._lock = RLock()

    @property
    def id(self): return self._id
    @property
    def owner(self): return self._owner
    @property
    def epoch(self): return self._epoch
    @property
    def closed(self): return self._closed
    @property
    def live_count(self): return self._live

    def validate(self, owner, epoch):
        if (self._closed or owner is not self._owner or type(epoch) is not int
                or epoch != self._epoch):
            _bad()
        scheduler = getattr(owner, 'suspension', None)
        if scheduler is not None and getattr(scheduler, 'epoch', epoch) != epoch:
            _bad()

    def _slot(self, ref):
        if (self._closed or type(ref) is not RootRef or type(ref.domain) is not int
                or ref.domain != self._id or type(ref.token) is not int
                or not 0 < ref.token <= _U64):
            _bad()
        index = (ref.token & _U32) - 1
        generation = ref.token >> 32
        if not 0 <= index < len(self._slots) or generation == 0:
            _bad()
        slot = self._slots[index]
        if not slot[2] or slot[0] != generation:
            _bad()
        return index, slot

    def pin(self, value):
        with self._lock:
            self.validate(self._owner, self._epoch)
            if self._free:
                index = self._free.pop()
                slot = self._slots[index]
                slot[1], slot[2] = value, True
            else:
                if len(self._slots) >= self._max_slots:
                    _limit()
                index = len(self._slots)
                slot = [1, value, True]
                self._slots.append(slot)
            self._live += 1
            return RootRef(self._id, (slot[0] << 32) | (index + 1))

    def resolve(self, ref, owner, epoch):
        with self._lock:
            self.validate(owner, epoch)
            return self._slot(ref)[1][1]

    def release(self, ref, owner=None, epoch=None):
        with self._lock:
            self.validate(self._owner if owner is None else owner,
                          self._epoch if epoch is None else epoch)
            index, slot = self._slot(ref)
            slot[1], slot[2] = None, False
            self._live -= 1
            if slot[0] < _U32:
                slot[0] += 1
                self._free.append(index)

    def roots(self):
        with self._lock:
            if self._closed:
                return ()
            self.validate(self._owner, self._epoch)
            return tuple(slot[1] for slot in self._slots if slot[2])

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for slot in self._slots:
                slot[1], slot[2] = None, False
                if slot[0] < _U32:
                    slot[0] += 1
            self._free.clear()
            self._live = 0
            self._owner = None
