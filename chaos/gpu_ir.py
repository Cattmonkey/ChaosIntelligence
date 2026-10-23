"""Pure, bounded structured Float32/Bool GPU programs.

Only numeric immutable IR crosses the worker boundary. Lexical names are
resolved before task creation, and this module needs no language runtime there.
"""
import math
import struct

MAX_NODES = 128
MAX_DEPTH = 64
MAX_SLOTS = 128
MAX_FOR_ITEMS = 256
MAX_STEPS = 4096
MAX_LOOP_ITERATIONS = 1024
FAULTS = {1: 'nonfinite', 2: 'step_limit', 3: 'loop_limit'}


def is_program(ir):
    return type(ir) in (tuple, list) and bool(ir) and ir[0] == 'program'


def f32(value):
    if type(value) not in (int, float): raise ValueError('GPU numeric values cannot be Bool or objects')
    try: value = struct.unpack('!f', struct.pack('!f', value))[0]
    except (OverflowError, struct.error): raise ValueError('Finite Float32 range exceeded') from None
    if not math.isfinite(value): raise ValueError('Finite Float32 required')
    return value


def bits(value): return struct.unpack('!I', struct.pack('!f', f32(value)))[0]


class ProgramFault(ValueError):
    def __init__(self, lane, code):
        self.lane, self.code = lane, code
        self.node, self.kind = code // 8 - 1, FAULTS[code % 8]
        super().__init__('GPU lane %d: %s at IR node %d' % (lane, self.kind, self.node))


def lower(evaluator, body, parameter):
    """Resolve AST bindings to slots without reading any captured environment."""
    count = 0; next_slot = 1
    def enter(node, depth):
        nonlocal count
        evaluator.tick(); count += 1
        if count > MAX_NODES or depth > MAX_DEPTH: raise ValueError('GPU program exceeds 128 nodes or 64 levels')
        # Node is a tuple subclass; no arbitrary iterable or callable is read.
        from .language import Node
        if type(node) not in (Node, tuple) or not node: raise ValueError('Invalid GPU AST')
    def slot():
        nonlocal next_slot
        value = next_slot; next_slot += 1
        if next_slot > MAX_SLOTS: raise ValueError('GPU program exceeds 128 slots')
        return value
    def constant(node, depth):
        evaluator.tick()
        from .language import Node
        if depth > MAX_DEPTH or type(node) not in (Node, tuple) or not node: raise ValueError('Invalid or overly deep GPU literal table item')
        if node[0] == 'literal' and len(node) == 2: return f32(node[1])
        if node[0] == 'unary' and len(node) == 3 and node[1] in ('+', '-'):
            value = constant(node[2], depth + 1)
            return f32(value if node[1] == '+' else -value)
        raise ValueError('GPU for requires finite numeric literal List/Tuple items; calls/range are unsupported')
    def visit(node, scope, depth=0):
        enter(node, depth); k = node[0]
        child = lambda item: visit(item, scope, depth + 1)
        if k == 'block' and len(node) == 2 and type(node[1]) is list:
            local = dict(scope); local[None] = set(); result = []
            for statement in node[1]: result.append(visit(statement, local, depth + 1))
            return ('block', tuple(result))
        if k == 'literal' and len(node) == 2:
            if type(node[1]) is bool: return ('bool', node[1])
            if node[1] is None: return ('null',)
            return ('literal', f32(node[1]))
        if k == 'name' and len(node) == 2 and type(node[1]) is str:
            if node[1] not in scope: raise ValueError('GPU programs cannot read free variables or captured bindings')
            return ('slot', scope[node[1]])
        if k in ('let', 'var') and len(node) == 4 and type(node[1]) is str:
            # Each block owns its own declaration set; a parent can be shadowed.
            if node[1] in scope.get(None, set()): raise ValueError('Duplicate GPU local binding')
            if node[2] not in ('Any', 'Number', 'Bool'): raise ValueError('GPU locals require stable Number/Bool/Any kinds')
            value = child(node[3]); target = slot()
            scope[node[1]] = target
            scope.setdefault(None, set()).add(node[1])
            return ('bind', target, k == 'var', node[2], value)
        if k == 'assign' and len(node) == 3 and type(node[1]) is str:
            if node[1] not in scope: raise ValueError('GPU assignment requires a local var binding')
            return ('store', scope[node[1]], child(node[2]))
        if k == 'aug_assign' and len(node) == 4 and node[1][0] == 'name' and node[2] in ('+', '-', '*'):
            name = node[1][1]
            if name not in scope: raise ValueError('GPU assignment requires a local var binding')
            # Preserve a single location and left-to-right read/RHS evaluation.
            left = visit(node[1], scope, depth + 1)
            return ('store', scope[name], ('binary', node[2], left, child(node[3])))
        if k == 'unary' and len(node) == 3 and node[1] in ('+', '-', '!'):
            return ('unary', node[1], child(node[2]))
        if k == 'binary' and len(node) == 4 and node[1] in ('+', '-', '*', '<', '<=', '>', '>=', '==', '!=', '&&', '||'):
            return ('binary', node[1], child(node[2]), child(node[3]))
        if k == 'if' and len(node) == 4: return ('if', child(node[1]), child(node[2]), child(node[3]))
        if k == 'while' and len(node) == 3: return ('while', child(node[1]), child(node[2]))
        if k == 'for' and len(node) == 4 and type(node[1]) is str:
            items = node[2]; enter(items, depth + 1)
            if items[0] not in ('list', 'tuple') or len(items) != 2 or type(items[1]) is not list or len(items[1]) > MAX_FOR_ITEMS:
                raise ValueError('GPU for requires at most 256 finite numeric literal List/Tuple items')
            values = tuple(constant(item, depth + 2) for item in items[1])
            target = slot(); local = dict(scope); local[None] = set(); local[node[1]] = target
            return ('for', target, values, visit(node[3], local, depth + 1))
        if k == 'loop_else' and len(node) == 3: return ('loop_else', child(node[1]), child(node[2]))
        if k == 'return' and len(node) == 2: return ('return', child(node[1]))
        if k in ('break', 'continue') and len(node) == 1: return (k,)
        raise ValueError('GPU program does not support calls, effects, objects, closures or changing local kinds')
    # A declaration set is local to each block, never inherited by nested ones.
    tree = visit(body, {parameter: 0, None: set()})
    return plan(('program', 2, tree)).tree


class Plan:
    pass


def plan(ir):
    """Revalidate every worker request, including scope, types and control depth."""
    if not is_program(ir) or len(ir) != 3 or type(ir[1]) is not int or ir[1] != 2: raise ValueError('Invalid GPU program schema')
    result = Plan(); result.ids = {}; result.types = {}; result.controls = {}; result.slots = {0: 'float'}; result.breaks = {}
    count = 0
    def make(fields, types, controls, number):
        value = tuple(list(fields)); result.ids[id(value)] = number
        result.types[id(value)] = frozenset(types); result.controls[id(value)] = frozenset(controls)
        return value
    def require(types, expected):
        if types != {expected}: raise ValueError('GPU expression requires exact ' + expected + ' kind')
    def visit(node, scope, depth=0, loops=0):
        nonlocal count
        number = count; count += 1
        if count > MAX_NODES or depth > MAX_DEPTH: raise ValueError('GPU program exceeds 128 nodes or 64 levels')
        if type(node) not in (tuple, list) or not node or type(node[0]) is not str: raise ValueError('Invalid GPU program node')
        k = node[0]; controls = set()
        def child(item, local=None, loop_depth=None):
            value = visit(item, scope if local is None else local, depth + 1, loops if loop_depth is None else loop_depth)
            controls.update(result.controls[id(value)]); return value
        def typ(value): return set(result.types[id(value)])
        def alloc(target, kind, mutable, local):
            if type(target) is not int or target <= 0 or target >= MAX_SLOTS or target in result.slots: raise ValueError('Invalid or duplicate GPU slot')
            result.slots[target] = kind; local[target] = (kind, mutable)
        if k == 'block' and len(node) == 2 and type(node[1]) in (tuple, list):
            local = dict(scope); values = []; normal = {'null'}; live = True; controls.clear()
            for item in node[1]:
                value = visit(item, local, depth + 1, loops); values.append(value)
                if live:
                    controls.update(result.controls[id(value)]); normal = typ(value); live = bool(normal)
            return make((k, tuple(values)), normal, controls, number)
        if k == 'literal' and len(node) == 2: return make((k, f32(node[1])), {'float'}, (), number)
        if k == 'bool' and len(node) == 2 and type(node[1]) is bool: return make((k, node[1]), {'bool'}, (), number)
        if k == 'null' and len(node) == 1: return make((k,), {'null'}, (), number)
        if k == 'slot' and len(node) == 2 and type(node[1]) is int and node[1] in scope:
            return make((k, node[1]), {scope[node[1]][0]}, (), number)
        if k == 'bind' and len(node) == 5 and type(node[2]) is bool and node[3] in ('Any', 'Number', 'Bool'):
            value = child(node[4]); types = typ(value)
            if len(types) != 1 or not types <= {'float', 'bool'}: raise ValueError('GPU locals need one stable Float32/Bool kind')
            kind = next(iter(types))
            if node[3] == 'Number' and kind != 'float' or node[3] == 'Bool' and kind != 'bool': raise ValueError('GPU local contract mismatch')
            alloc(node[1], kind, node[2], scope)
            return make((k, node[1], node[2], node[3], value), types, controls, number)
        if k == 'store' and len(node) == 3 and type(node[1]) is int and node[1] in scope:
            kind, mutable = scope[node[1]]
            if not mutable: raise ValueError('Cannot assign to immutable GPU let/parameter/for binding')
            value = child(node[2]); require(typ(value), kind)
            return make((k, node[1], value), {kind}, controls, number)
        if k == 'unary' and len(node) == 3 and node[1] in ('+', '-', '!'):
            value = child(node[2]); kind = 'bool' if node[1] == '!' else 'float'; require(typ(value), kind)
            return make((k, node[1], value), {kind}, controls, number)
        if k == 'binary' and len(node) == 4 and node[1] in ('+', '-', '*', '<', '<=', '>', '>=', '==', '!=', '&&', '||'):
            left = child(node[2]); right = child(node[3]); op = node[1]
            if op in ('&&', '||'): require(typ(left), 'bool'); require(typ(right), 'bool'); kind = 'bool'
            elif op in ('==', '!='):
                if typ(left) != typ(right) or len(typ(left)) != 1 or not typ(left) <= {'float', 'bool'}: raise ValueError('GPU equality requires matching numeric or Bool kinds')
                kind = 'bool'
            else:
                require(typ(left), 'float'); require(typ(right), 'float'); kind = 'float' if op in ('+', '-', '*') else 'bool'
            return make((k, op, left, right), {kind}, controls, number)
        if k == 'if' and len(node) == 4:
            cond = child(node[1]); require(typ(cond), 'bool'); yes = child(node[2]); no = child(node[3])
            return make((k, cond, yes, no), typ(yes) | typ(no), controls, number)
        if k == 'return' and len(node) == 2:
            value = child(node[1]); require(typ(value), 'float'); controls.add('return')
            return make((k, value), (), controls, number)
        if k in ('break', 'continue') and len(node) == 1 and loops:
            return make((k,), (), {k}, number)
        if k == 'while' and len(node) == 3:
            cond = child(node[1]); require(typ(cond), 'bool')
            if set(result.controls[id(cond)]) & {'break', 'continue'}: raise ValueError('GPU loop condition cannot contain break/continue targeting an enclosing loop')
            body = child(node[2], loop_depth=loops + 1)
            if body[0] != 'block': raise ValueError('GPU loop body requires a block')
            had_break = 'break' in result.controls[id(body)]; controls -= {'break', 'continue'}
            value = make((k, cond, body), {'null'}, controls, number); result.breaks[id(value)] = had_break; return value
        if k == 'for' and len(node) == 4 and type(node[2]) in (tuple, list) and len(node[2]) <= MAX_FOR_ITEMS:
            values = tuple(f32(item) for item in node[2]); local = dict(scope); alloc(node[1], 'float', False, local)
            body = child(node[3], local, loops + 1)
            if body[0] != 'block': raise ValueError('GPU loop body requires a block')
            had_break = 'break' in result.controls[id(body)]; controls -= {'break', 'continue'}
            value = make((k, node[1], values, body), {'null'}, controls, number); result.breaks[id(value)] = had_break; return value
        if k == 'loop_else' and len(node) == 3:
            loop = child(node[1]); other = child(node[2])
            if loop[0] not in ('while', 'for') or other[0] != 'block': raise ValueError('Invalid GPU loop else')
            normal = typ(other) | ({'null'} if result.breaks[id(loop)] else set())
            return make((k, loop, other), normal, controls, number)
        raise ValueError('Invalid GPU program node, binding, type or loop control')
    body = visit(ir[2], {0: ('float', False)})
    if body[0] != 'block' or not set(result.types[id(body)]) <= {'float'}: raise ValueError('GPU map normal exits and return values require Float32')
    result.tree = ('program', 2, body)
    return result


def canonical(ir):
    tree = plan(ir).tree
    def freeze(value):
        if type(value) is tuple:
            if value and value[0] == 'literal': return ('literal', bits(value[1]))
            # for constants also preserve signed-zero bits.
            if value and value[0] == 'for': return ('for', value[1], tuple(bits(x) for x in value[2]), freeze(value[3]))
            return tuple(freeze(x) for x in value)
        return value
    return freeze(tree)


class _Signal(Exception):
    def __init__(self, kind, value=None): self.kind, self.value = kind, value


def cpu(ir, values, cancel):
    p = plan(ir); output = []
    for lane, item in enumerate(values):
        slots = {0: item}; steps = 0; completed = {}
        def fault(node, kind): raise ProgramFault(lane, (p.ids[id(node)] + 1) * 8 + kind)
        def evaluate(node):
            nonlocal steps
            cancel(); steps += 1
            if steps > MAX_STEPS: fault(node, 2)
            k = node[0]
            if k == 'literal': return node[1]
            if k == 'bool': return node[1]
            if k == 'null': return None
            if k == 'slot': return slots[node[1]]
            if k == 'block':
                value = None
                for child in node[1]: value = evaluate(child)
                return value
            if k in ('bind', 'store'):
                value = evaluate(node[4] if k == 'bind' else node[2]); slots[node[1]] = value; return value
            if k == 'unary':
                value = evaluate(node[2])
                if node[1] == '!': return not value
                try: return f32(value if node[1] == '+' else -value)
                except ValueError: fault(node, 1)
            if k == 'binary':
                op = node[1]; left = evaluate(node[2])
                if op == '&&' and not left or op == '||' and left: return left
                right = evaluate(node[3])
                if op in ('&&', '||'): return right
                if op == '<': return left < right
                if op == '<=': return left <= right
                if op == '>': return left > right
                if op == '>=': return left >= right
                if op == '==': return left == right
                if op == '!=': return left != right
                try: return f32(left + right if op == '+' else left - right if op == '-' else left * right)
                except ValueError: fault(node, 1)
            if k == 'if': return evaluate(node[2] if evaluate(node[1]) else node[3])
            if k == 'return': raise _Signal('return', evaluate(node[1]))
            if k in ('break', 'continue'): raise _Signal(k)
            if k in ('while', 'for'):
                iterations = 0; normal = True
                items = iter(node[2]) if k == 'for' else None
                while True:
                    if k == 'while':
                        if not evaluate(node[1]): break
                    else:
                        try: value = next(items)
                        except StopIteration: break
                    iterations += 1
                    if iterations > MAX_LOOP_ITERATIONS: fault(node, 3)
                    if k == 'for': slots[node[1]] = value
                    try: evaluate(node[2] if k == 'while' else node[3])
                    except _Signal as signal:
                        if signal.kind == 'return': raise
                        if signal.kind == 'break': normal = False; break
                completed[id(node)] = normal; return None
            if k == 'loop_else':
                evaluate(node[1]); return evaluate(node[2]) if completed[id(node[1])] else None
            raise AssertionError(k)
        try: value = evaluate(p.tree[2])
        except _Signal as signal:
            if signal.kind != 'return': raise
            value = signal.value
        output.append(value)
    return output


def tensor(torch, ir, values, cancel):
    """Masked lane interpreter: unselected expressions never execute."""
    p = plan(ir); size = values.numel(); device = values.device
    slots = {i: torch.zeros(size, dtype=torch.float32 if kind == 'float' else torch.bool, device=device) for i, kind in p.slots.items()}
    slots[0] = values; steps = torch.zeros(size, dtype=torch.int32, device=device)
    flow = torch.zeros(size, dtype=torch.int32, device=device); faults = torch.zeros_like(flow); output = torch.zeros_like(values); completed = {}
    def alive(lanes): return lanes[flow[lanes] == 0]
    def fail(node, lanes, kind):
        if lanes.numel(): faults[lanes] = (p.ids[id(node)] + 1) * 8 + kind; flow[lanes] = 4
    def evaluate(node, lanes):
        cancel(); lanes = alive(lanes); steps[lanes] += 1
        fail(node, lanes[steps[lanes] > MAX_STEPS], 2); lanes = alive(lanes)
        kinds = p.types[id(node)]; kind = next(iter(kinds)) if len(kinds) == 1 else None
        value = torch.zeros(size, dtype=torch.float32 if kind == 'float' else torch.bool, device=device) if kind in ('float', 'bool') else None
        if not lanes.numel(): return value
        k = node[0]
        if k in ('literal', 'bool'): value[lanes] = node[1]; return value
        if k == 'null': return None
        if k == 'slot': value[lanes] = slots[node[1]][lanes]; return value
        if k == 'block':
            for child in node[1]: value = evaluate(child, lanes)
            return value
        if k in ('bind', 'store'):
            value = evaluate(node[4] if k == 'bind' else node[2], lanes); active = alive(lanes); slots[node[1]][active] = value[active]; return value
        if k == 'unary':
            left = evaluate(node[2], lanes); active = alive(lanes)
            result = ~left[active] if node[1] == '!' else left[active] if node[1] == '+' else -left[active]
            value[active] = result
            if kind == 'float': fail(node, active[~torch.isfinite(result)], 1)
            return value
        if k == 'binary':
            op = node[1]; left = evaluate(node[2], lanes); active = alive(lanes)
            if op in ('&&', '||'):
                value[active] = left[active]; selected = active[left[active] if op == '&&' else ~left[active]]
                right = evaluate(node[3], selected); selected = alive(selected); value[selected] = right[selected]; return value
            right = evaluate(node[3], active); active = alive(active); a, b = left[active], right[active]
            if op == '+': result = a + b
            elif op == '-': result = a - b
            elif op == '*': result = a * b
            elif op == '<': result = a < b
            elif op == '<=': result = a <= b
            elif op == '>': result = a > b
            elif op == '>=': result = a >= b
            elif op == '==': result = a == b
            else: result = a != b
            value[active] = result
            if kind == 'float': fail(node, active[~torch.isfinite(result)], 1)
            return value
        if k == 'if':
            cond = evaluate(node[1], lanes); active = alive(lanes)
            yes, no = active[cond[active]], active[~cond[active]]
            a = evaluate(node[2], yes); b = evaluate(node[3], no)
            if value is not None:
                for selected, branch in ((alive(yes), a), (alive(no), b)):
                    if selected.numel(): value[selected] = branch[selected]
            return value
        if k == 'return':
            returned = evaluate(node[1], lanes); active = alive(lanes); output[active] = returned[active]; flow[active] = 1; return None
        if k in ('break', 'continue'): flow[lanes] = 2 if k == 'break' else 3; return None
        if k in ('while', 'for'):
            active = lanes; done = torch.zeros(size, dtype=torch.bool, device=device); iterations = torch.zeros_like(flow); position = 0
            while active.numel():
                cancel()
                if k == 'while':
                    cond = evaluate(node[1], active); active = alive(active); done[active[~cond[active]]] = True; active = active[cond[active]]
                else:
                    if position == len(node[2]): done[active] = True; break
                    slots[node[1]][active] = node[2][position]; position += 1
                iterations[active] += 1; fail(node, active[iterations[active] > MAX_LOOP_ITERATIONS], 3); active = alive(active)
                evaluate(node[2] if k == 'while' else node[3], active)
                stay = (flow[active] == 0) | (flow[active] == 3)
                broken = active[flow[active] == 2]; flow[broken] = 0
                continued = active[flow[active] == 3]; flow[continued] = 0
                active = active[stay]
            completed[id(node)] = done; return None
        if k == 'loop_else':
            evaluate(node[1], lanes); selected = alive(lanes); selected = selected[completed[id(node[1])][selected]]
            branch = evaluate(node[2], selected)
            if value is not None and selected.numel(): value[selected] = branch[selected]
            return value
        raise AssertionError(k)
    lanes = torch.arange(size, device=device, dtype=torch.int64); last = evaluate(p.tree[2], lanes); active = alive(lanes)
    if active.numel(): output[active] = last[active]
    cancel(); recorded = faults.detach().cpu().tolist()
    for lane, code in enumerate(recorded):
        if code: raise ProgramFault(lane, code)
    return output


def source(ir):
    """Compile the typed program to direct Metal lexical control flow."""
    p = plan(ir); lines = []; serial = 0; indent = 1; loop_names = {}; loop_stack = []
    def line(text): lines.append('    ' * indent + text)
    def fresh(prefix='t'):
        nonlocal serial
        name = prefix + str(serial); serial += 1; return name
    def kind(node):
        kinds = p.types[id(node)]; return next(iter(kinds)) if len(kinds) == 1 else None
    def guard(node, fault=2):
        code = (p.ids[id(node)] + 1) * 8 + fault
        line('if (++fuel > %du) { faults[index] = %d; return; }' % (MAX_STEPS, code))
    def finite(node, name):
        line('if ((as_type<uint>(float(%s)) & 0x7f800000u) == 0x7f800000u) { faults[index] = %d; return; }' % (name, (p.ids[id(node)] + 1) * 8 + 1))
    def declare(typ, expression=None):
        name = fresh(); line('volatile %s %s%s;' % ('float' if typ == 'float' else 'bool', name, '' if expression is None else ' = ' + expression)); return name
    def emit(node):
        nonlocal indent
        guard(node); k = node[0]; typ = kind(node)
        if k == 'literal': return declare('float', 'as_type<float>(0x%08xu)' % bits(node[1]))
        if k == 'bool': return declare('bool', 'true' if node[1] else 'false')
        if k == 'null': return None
        if k == 'slot': return declare(typ, 's%d' % node[1])
        if k == 'block':
            value = None
            for child in node[1]: value = emit(child)
            return value
        if k in ('bind', 'store'):
            value = emit(node[4] if k == 'bind' else node[2]); line('s%d = %s;' % (node[1], value)); return value
        if k == 'unary':
            value = emit(node[2]); name = declare(typ, node[1] + value)
            if typ == 'float': finite(node, name)
            return name
        if k == 'binary':
            op = node[1]; left = emit(node[2])
            if op in ('&&', '||'):
                name = declare('bool', left); line('if (%s%s) {' % ('' if op == '&&' else '!', left)); indent += 1
                right = emit(node[3]); line(name + ' = ' + right + ';'); indent -= 1; line('}'); return name
            right = emit(node[3]); name = declare(typ, left + ' ' + op + ' ' + right)
            if typ == 'float': finite(node, name)
            return name
        if k == 'if':
            cond = emit(node[1]); name = declare(typ) if typ in ('float', 'bool') else None
            line('if (' + cond + ') {'); indent += 1; yes = emit(node[2])
            if name is not None and yes is not None: line(name + ' = ' + yes + ';')
            indent -= 1; line('} else {'); indent += 1; no = emit(node[3])
            if name is not None and no is not None: line(name + ' = ' + no + ';')
            indent -= 1; line('}'); return name
        if k == 'return':
            value = emit(node[1]); line('output[index] = ' + value + '; return;'); return None
        if k in ('break', 'continue'):
            if k == 'break': line(loop_stack[-1] + ' = false;')
            line(k + ';'); return None
        if k in ('while', 'for'):
            normal, counter = fresh('normal'), fresh('iterations'); loop_names[id(node)] = normal
            line('bool %s = true; uint %s = 0u;' % (normal, counter)); loop_stack.append(normal)
            if k == 'for':
                constants = ', '.join('as_type<float>(0x%08xu)' % bits(x) for x in node[2]) or '0.0f'
                items = fresh('items'); line('const float %s[%d] = {%s};' % (items, max(1, len(node[2])), constants))
                position = fresh('position'); line('for (uint %s=0u; %s<%du; ++%s) {' % (position, position, len(node[2]), position)); indent += 1
                line('s%d = %s[%s];' % (node[1], items, position))
            else:
                line('while (true) {'); indent += 1; cond = emit(node[1]); line('if (!' + cond + ') break;')
            line('if (++%s > %du) { faults[index] = %d; return; }' % (counter, MAX_LOOP_ITERATIONS, (p.ids[id(node)] + 1) * 8 + 3))
            emit(node[2] if k == 'while' else node[3]); indent -= 1; line('}'); loop_stack.pop(); return None
        if k == 'loop_else':
            emit(node[1]); name = declare(typ) if typ in ('float', 'bool') else None
            line('if (' + loop_names[id(node[1])] + ') {'); indent += 1; value = emit(node[2])
            if name is not None and value is not None: line(name + ' = ' + value + ';')
            indent -= 1; line('}'); return name
        raise AssertionError(k)
    line('if (index >= uint(count)) return;'); line('uint fuel = 0u; faults[index] = 0;')
    for slot, typ in sorted(p.slots.items()):
        line('%s s%d = %s;' % ('float' if typ == 'float' else 'bool', slot, 'input[index]' if slot == 0 else '0.0f' if typ == 'float' else 'false'))
    last = emit(p.tree[2])
    if last is not None: line('output[index] = ' + last + ';')
    return ('#include <metal_stdlib>\nusing namespace metal;\n#pragma clang fp contract(off)\n#pragma clang fp reassociate(off)\n'
            'kernel void chaos_map(device const float* input [[buffer(0)]], device float* output [[buffer(1)]], constant int& count [[buffer(2)]], device int* faults [[buffer(3)]], uint index [[thread_position_in_grid]]) {\n' + '\n'.join(lines) + '\n}\n')
