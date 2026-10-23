"""Complete typed host-event ABI shared by portable and native Flow dispatch.

An event packet selects an actual sealed instruction held by an invocation's
root domain. The selected handle is the semantic payload, not a diagnostic ID.
"""
from dataclasses import dataclass
import ctypes
from .native_values import RootDomain, RootRef, _bad

ABI_VERSION = 2
NATIVE_ABI = 'chaos.flow/rooted-host-events/2'
# Append-only numbering within this ABI. Control-only rows never cross as events.
EVENT_KINDS = (
    'enter', 'exit', 'eval', 'await', 'yield', 'delegate', 'acquire',
    'step', 'bind', 'loop_reset', 'unwind', 'condition', 'reset', 'tick',
    'null', 'return', 'protect', 'protect_return', 'protect_select',
    'protect_finish', 'protect_missing', 'protect_guard', 'resource',
    'resource_return', 'resource_missing', 'pattern', 'pattern_return',
    'pattern_missing', 'pattern_next',
    'operand', 'operand_return', 'operand_missing',
    'access', 'access_return', 'access_missing',
)
_KIND_CODES = {name: index + 1 for index, name in enumerate(EVENT_KINDS)}
CONTROL_KINDS = frozenset(('jump', 'branch', 'flag', 'done'))


def kind_code(kind):
    if type(kind) is not str or kind not in _KIND_CODES:
        _bad()
    return _KIND_CODES[kind]


@dataclass(frozen=True)
class EventPacket:
    abi: int
    domain: int
    kind: int
    site: int
    token: int
    next_pc: int


class EventTable:
    """Pins the original instruction rows and validates the selected real row."""
    def __init__(self, domain, instructions):
        if (type(domain) is not RootDomain or domain.closed
                or type(instructions) is not tuple
                or not 0 < len(instructions) <= 1024):
            _bad()
        self.domain, self.instructions = domain, instructions
        refs = []
        try:
            for row in instructions:
                if type(row) is not tuple or not row or type(row[0]) is not str:
                    _bad()
                if row[0] in CONTROL_KINDS:
                    refs.append(None)
                else:
                    kind_code(row[0])
                    refs.append(domain.pin(row))
        except BaseException:
            for ref in refs:
                if ref is not None:
                    domain.release(ref)
            raise
        self.refs = tuple(refs)
        self.handles = tuple(0 if ref is None else ref.token for ref in refs)
        self.kinds = tuple(0 if ref is None else kind_code(row[0])
                           for row, ref in zip(instructions, refs))
        self._native_handles = (ctypes.c_uint64 * len(self.handles))(*self.handles)
        self.validate(full=True)

    @property
    def native_handles(self):
        self.validate()
        if (not isinstance(self._native_handles, ctypes.Array)
                or self._native_handles._type_ is not ctypes.c_uint64
                or len(self._native_handles) != len(self.instructions)):
            _bad()
        return self._native_handles

    def validate(self, *, full=False):
        if (type(self.domain) is not RootDomain or self.domain.closed
                or type(self.instructions) is not tuple
                or type(self.refs) is not tuple or type(self.handles) is not tuple
                or type(self.kinds) is not tuple
                or not 0 < len(self.instructions) <= 1024
                or not len(self.instructions) == len(self.refs) == len(self.handles) == len(self.kinds)):
            _bad()
        self.domain.validate(self.domain.owner, self.domain.epoch)
        if not full:
            return
        for row, ref, token, kind in zip(self.instructions, self.refs, self.handles, self.kinds):
            if type(row) is not tuple or not row or type(row[0]) is not str:
                _bad()
            if row[0] in CONTROL_KINDS:
                if ref is not None or type(token) is not int or token != 0 or type(kind) is not int or kind != 0:
                    _bad()
            elif (type(ref) is not RootRef or type(token) is not int
                    or token != ref.token or type(kind) is not int
                    or kind != kind_code(row[0])
                    or self.domain.resolve(ref, self.domain.owner, self.domain.epoch) is not row):
                _bad()

    def packet(self, next_pc, event):
        """Encode the existing portable successor result without interpreting it."""
        self.validate()
        if type(next_pc) is not int or type(event) is not int:
            _bad()
        if event == 0:
            if not 0 <= next_pc < len(self.instructions) or self.instructions[next_pc][0] != 'done':
                _bad()
            return EventPacket(ABI_VERSION, self.domain.id, 0, -1, 0, next_pc)
        site = event - 1
        if not 0 <= site < len(self.instructions) or next_pc != site + 1 or not self.kinds[site]:
            _bad()
        return EventPacket(ABI_VERSION, self.domain.id, self.kinds[site], site,
                           self.handles[site], next_pc)

    def resolve(self, packet, owner, epoch):
        self.validate()
        self.domain.validate(owner, epoch)
        if (type(packet) is not EventPacket
                or any(type(value) is not int for value in (
                    packet.abi, packet.domain, packet.kind, packet.site,
                    packet.token, packet.next_pc))
                or packet.abi != ABI_VERSION or packet.domain != self.domain.id):
            _bad()
        if packet.kind == 0:
            if (packet.site != -1 or packet.token != 0
                    or not 0 <= packet.next_pc < len(self.instructions)
                    or self.instructions[packet.next_pc][0] != 'done'):
                _bad()
            return None
        site = packet.site
        if (not 0 <= site < len(self.instructions) or packet.next_pc != site + 1
                or not packet.next_pc < len(self.instructions)
                or packet.kind != self.kinds[site] or packet.token != self.handles[site]):
            _bad()
        # The resolved pin supplies the operation. Program indexing is only the
        # sealed identity check; it is never a payload fallback.
        actual = self.domain.resolve(RootRef(packet.domain, packet.token), owner, epoch)
        if actual is not self.instructions[site] or kind_code(actual[0]) != packet.kind:
            _bad()
        return actual

    def close(self):
        if not self.refs:
            return
        if self.domain.closed:
            self.refs = (); self.handles = (); self.kinds = (); self.instructions = ()
            self._native_handles = None
            return
        for ref in self.refs:
            if ref is not None:
                self.domain.release(ref)
        self.refs = (); self.handles = (); self.kinds = (); self.instructions = ()
        self._native_handles = None
