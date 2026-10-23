"""Inert whole-bundle literal arithmetic plans; execution order stays elsewhere.

The evaluator must first enter each original AST child/frame and perform the
original operation tick. A site can then replace only that operation's physical
integer arithmetic. Plans never execute source, inspect environments or move
instructions/checkpoints. All returned values are immutable exact integers.
"""
from dataclasses import dataclass
from itertools import islice

from .language import Node

MAX_NODES = 16384
MAX_SITES = 4096
MAX_BITS = 256
_MISSING = object()


def _integer(value):
    return type(value) is int and value.bit_length() <= MAX_BITS


@dataclass(frozen=True, eq=False)
class ConstantSite:
    node: object
    operation: str
    operands: tuple
    result: int

    def match(self, operation, operands):
        """Guard actual operation and values without equality/protocol hooks."""
        if (type(self.operation) is not str or self.operation not in ('+', '-', '*', 'pos', 'neg') or
                type(self.operands) is not tuple or not _integer(self.result) or
                len(self.operands) != (1 if self.operation in ('pos', 'neg') else 2) or
                type(operation) is not str or operation != self.operation or
                type(operands) is not tuple or len(operands) != len(self.operands)):
            return False, None
        for actual, expected in zip(operands, self.operands):
            if not _integer(actual) or not _integer(expected) or actual != expected:
                return False, None
        return True, self.result


class LiteralPlan:
    def __init__(self, sites, scanned_nodes, truncated):
        # Each site retains its exact Node, preventing id reuse. No source text,
        # source metadata, live Function/Env or authority is captured.
        self._sites = dict(sites)
        self._scanned_nodes = scanned_nodes
        self._truncated = bool(truncated)

    def site(self, node):
        if type(node) is not Node:
            return None
        value = self._sites.get(id(node))
        return value if value is not None and value.node is node else None

    def info(self):
        return {'schema': 'chaos.literal-optimization/1',
                'algorithm': 'guarded-total-int/2',
                'candidateSites': len(self._sites),
                'scannedNodes': self._scanned_nodes,
                'truncated': self._truncated,
                'limits': {'maxNodes': MAX_NODES, 'maxSites': MAX_SITES, 'maxBits': MAX_BITS},
                'checkpointPolicy': 'original-node-and-operation-entry'}


def build_plan(trees, checkpoint=None):
    """Inspect exact AST/container shapes bottom-up, with bounded partial plans.

    ``trees`` normally is ProgramBundle.expanded_trees. An exact Node or exact
    list/tuple of trees is also accepted. Generic AST sequence containers are
    walked to reach function defaults/bodies; containers are never folded.
    Node attributes and dict-valued metadata are never inspected. The optional
    callback belongs to the caller's compilation budget, not runtime events.
    Invalid/unsupported structures and exhausted quotas fall back; only an
    explicit caller checkpoint failure propagates.
    """
    truncated = False
    if type(trees) is dict:
        roots = tuple(islice(trees.values(), MAX_NODES))
        truncated = len(trees) > MAX_NODES
    elif type(trees) is Node:
        roots = (trees,)
    elif type(trees) in (list, tuple):
        roots = tuple(trees[:MAX_NODES])
        truncated = len(trees) > MAX_NODES
    else:
        return LiteralPlan({}, 0, True)
    sites, values, retained, seen, active = {}, {}, {}, set(), set()
    shared = set()
    scanned, work = 0, 0
    pending = [(item, False) for item in reversed(roots)]
    while pending:
        item, leaving = pending.pop()
        kind = type(item)
        if kind not in (Node, list, tuple):
            continue
        identity = id(item)
        if leaving:
            active.discard(identity)
            seen.add(identity)
            if kind is not Node:
                continue
            if not item or type(item[0]) is not str:
                continue
            value = _MISSING
            operation, operands = None, ()
            if len(item) == 2 and item[0] == 'literal' and _integer(item[1]):
                value = item[1]
            elif (len(item) == 3 and item[0] == 'unary' and
                  type(item[1]) is str and item[1] in ('+', '-') and type(item[2]) is Node):
                child = values.get(id(item[2]), _MISSING)
                if child is not _MISSING:
                    operation = 'pos' if item[1] == '+' else 'neg'
                    operands = (child,)
                    value = child if operation == 'pos' else -child
            elif (len(item) == 4 and item[0] == 'binary' and
                  type(item[1]) is str and item[1] in ('+', '-', '*') and
                  type(item[2]) is Node and type(item[3]) is Node):
                left, right = values.get(id(item[2]), _MISSING), values.get(id(item[3]), _MISSING)
                if left is not _MISSING and right is not _MISSING:
                    operation, operands = item[1], (left, right)
                    value = left + right if operation == '+' else left - right if operation == '-' else left * right
            if value is not _MISSING and _integer(value):
                values[identity] = value
                if operation is not None:
                    if len(sites) < MAX_SITES:
                        sites[identity] = ConstantSite(item, operation, operands, value)
                    else:
                        truncated = True
            continue
        if identity in seen:
            shared.add(identity)
            continue
        if identity in active:
            shared.add(identity)
            truncated = True
            continue
        work += 1
        if work > MAX_NODES or kind is Node and scanned >= MAX_NODES:
            truncated = True
            break
        if checkpoint is not None:
            checkpoint()
        retained[identity] = item
        if kind is Node:
            scanned += 1
        active.add(identity)
        pending.append((item, True))
        # Names/literals are leaves even if a hostile SDK supplied an invalid
        # embedded value. Attribute metadata never enters this traversal.
        if kind is Node and (not item or type(item[0]) is not str or item[0] in ('name', 'literal')):
            continue
        start = 1 if kind is Node else 0
        # Charge every inspected sequence position, including scalar fields.
        # Otherwise many wide scalar containers multiply the structural bound.
        end = min(len(item), start + max(0, MAX_NODES - work))
        if end < len(item):
            truncated = True
        children = []
        for index in range(start, end):
            work += 1
            child = item[index]
            if type(child) in (Node, list, tuple):
                children.append(child)
        # Pending breadth is bounded independently of AST sharing/cycles.
        remaining = MAX_NODES - len(pending)
        if len(children) > remaining:
            children = children[:max(0, remaining)]
            truncated = True
        pending.extend((child, False) for child in reversed(children))
    from .lexical_optimizer import extend_plan
    lexical_truncated = extend_plan(roots, sites, values, shared, max(0, MAX_NODES-work),
                                    checkpoint, _integer, ConstantSite)
    return LiteralPlan(sites, scanned, truncated or lexical_truncated)
