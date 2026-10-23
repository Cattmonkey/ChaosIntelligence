"""Physical optimization of already proven total integer expression regions.

The original AST checkpoint tape is a separate ordered control stream. Values
use parameter positions, never source names; no call or runtime value is read.
"""
from dataclasses import dataclass
import hashlib
import json

MIN_I64, MAX_I64 = -(2 ** 63), 2 ** 63 - 1


class OptimizationError(ValueError):
    pass


@dataclass(frozen=True)
class Value:
    id: int
    kind: str
    value: object
    children: tuple = ()


@dataclass(frozen=True)
class Plan:
    values: tuple
    schedule: tuple
    result: int
    checkpoints: tuple
    original_order: tuple
    original_operations: int
    common_subexpressions: int
    constant_folds: int
    identities: int

    def info(self):
        operations = tuple(i for i in self.schedule if self.values[i].kind in ('unary', 'binary'))
        def digest(value):
            return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
        return {'schema': 'chaos.scalar-optimization/1', 'algorithm': 'integer-value-dag/1',
                'checkpointPolicy': 'original-order-before-computation',
                'checkpointCount': len(self.checkpoints), 'checkpointHash': digest(self.checkpoints),
                'originalOperations': self.original_operations, 'emittedOperations': len(operations),
                'commonSubexpressions': self.common_subexpressions, 'constantFolds': self.constant_folds,
                'identitySimplifications': self.identities,
                'scheduled': operations != self.original_order,
                'originalOrderHash': digest(self.original_order), 'scheduledOrderHash': digest(operations),
                'valueGraphHash': digest([[v.kind, v.value, v.children] for v in self.values]),
                'valueCount': len(self.schedule)}


def optimize(expression, prefix=()):
    """Build a bounded DAG; the caller must first prove the original region.

    Only total exact arithmetic is movable. No algebraic re-association, host
    evaluation, user protocol, float, effect or control syntax is accepted.
    """
    from .jit import Expression
    values, interned, computed, checkpoints, original = [], {}, {}, [], []
    counts = {'nodes': 0, 'operations': 0, 'cse': 0, 'fold': 0, 'identity': 0}

    def check(index):
        if type(index) is not int or not 0 <= index < 512:
            raise OptimizationError('invalid_checkpoint')
        checkpoints.append(index)
        if len(checkpoints) > 512: raise OptimizationError('checkpoint_limit')

    for index in prefix: check(index)

    def intern(kind, value, children=()):
        key = (kind, value, children)
        if key not in interned:
            interned[key] = len(values)
            values.append(Value(len(values), kind, value, children))
        return interned[key]

    def constant(value):
        if type(value) is not int or not MIN_I64 <= value <= MAX_I64:
            raise OptimizationError('integer_range')
        return intern('literal', value)

    def visit(item, depth=0):
        counts['nodes'] += 1
        if counts['nodes'] > 128 or depth > 64: raise OptimizationError('structure_limit')
        if type(item) is not Expression: raise OptimizationError('untrusted_expression')
        check(item.event)
        if item.kind == 'literal' and item.children == ():
            return constant(item.value)
        if item.kind == 'name' and item.children == () and type(item.value) is int and 0 <= item.value < 8:
            return intern('name', item.value)
        if not ((item.kind == 'unary' and item.value in ('+', '-') and len(item.children) == 1) or
                (item.kind == 'binary' and item.value in ('+', '-', '*', '<', '<=', '>', '>=') and len(item.children) == 2)):
            raise OptimizationError('unsupported_expression')
        children = tuple(visit(c, depth + 1) for c in item.children)
        check(item.operation_event)
        counts['operations'] += 1
        key = (item.kind, item.value, children)
        if key in computed:
            counts['cse'] += 1
            result = computed[key]
        elif all(values[i].kind == 'literal' for i in children):
            args = [values[i].value for i in children]
            if item.kind == 'unary': result = constant(args[0] if item.value == '+' else -args[0])
            else:
                a, b = args
                result = constant({'+': lambda: a+b, '-': lambda: a-b, '*': lambda: a*b,
                    '<': lambda: int(a<b), '<=': lambda: int(a<=b),
                    '>': lambda: int(a>b), '>=': lambda: int(a>=b)}[item.value]())
            counts['fold'] += 1
        else:
            result = None
            if item.kind == 'unary' and item.value == '+': result = children[0]
            elif item.kind == 'binary':
                a, b = children
                av = values[a].value if values[a].kind == 'literal' else None
                bv = values[b].value if values[b].kind == 'literal' else None
                if item.value == '+' and av == 0: result = b
                elif item.value in ('+', '-') and bv == 0: result = a
                elif item.value == '*' and (av == 0 or bv == 0): result = constant(0)
                elif item.value == '*' and av == 1: result = b
                elif item.value == '*' and bv == 1: result = a
            if result is not None: counts['identity'] += 1
            else: result = intern(item.kind, item.value, children)
        computed[key] = result
        original.append(result)
        return result

    result = visit(expression)
    reachable, todo = set(), [result]
    while todo:
        i = todo.pop()
        if i not in reachable:
            reachable.add(i); todo.extend(values[i].children)
    # Critical distance to the result prioritizes long independent chains.
    priority = {i: 0 for i in reachable}
    for i in sorted(reachable, reverse=True):
        cost = 3 if values[i].kind == 'binary' and values[i].value == '*' else 1
        for child in values[i].children: priority[child] = max(priority[child], priority[i] + cost)
    remaining, emitted, schedule = set(reachable), set(), []
    while remaining:
        ready = [i for i in remaining if all(c in emitted for c in values[i].children)]
        if not ready: raise OptimizationError('cyclic_graph')
        i = min(ready, key=lambda n: (-priority[n], n))
        remaining.remove(i); emitted.add(i); schedule.append(i)
    original_order = tuple(dict.fromkeys(i for i in original if i in reachable and values[i].kind in ('unary', 'binary')))
    return Plan(tuple(values), tuple(schedule), result, tuple(checkpoints), original_order,
                counts['operations'], counts['cse'], counts['fold'], counts['identity'])
