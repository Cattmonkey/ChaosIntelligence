"""First-class, evaluator-owned function compilation with exact checkpoints."""
from dataclasses import dataclass, replace
import hashlib
import json

from .language import Function, LanguageError, ResourceLimit, Node

MIN_I64, MAX_I64 = -(2 ** 63), 2 ** 63 - 1
MAX_NODES, MAX_PARAMETERS, MAX_KERNELS = 128, 8, 32


class Unsupported(Exception):
    pass


@dataclass(frozen=True)
class Expression:
    kind: str
    value: object
    children: tuple
    event: int
    operation_event: object = None


@dataclass(frozen=True)
class Analysis:
    expression: Expression
    prefix: tuple
    spans: tuple
    parameters: tuple
    public_parameters: tuple
    fingerprint: str
    node_count: int
    body_span: dict


def _span(value):
    from .extensions import json_copy
    span = getattr(value, 'span', {})
    try:
        result = json_copy(span, max_chars=4000, max_nodes=256)
    except (ValueError, TypeError, OverflowError, UnicodeError):
        raise Unsupported('source_metadata') from None
    if type(result) is not dict: raise Unsupported('source_metadata')
    return result


def analyze(function, checkpoint=None):
    if function.flavor != 'fn': raise Unsupported('suspending_function')
    if type(function.params) is not list or len(function.params) > MAX_PARAMETERS:
        raise Unsupported('parameter_limit')
    aliases = function.macro_bindings or {}
    if type(aliases) is not dict or len(aliases) > MAX_PARAMETERS or any(type(k) is not str or type(v) is not str for k,v in aliases.items()):
        raise Unsupported('parameter_aliases')
    parameters, signature = [], []
    for parameter in function.params:
        if not isinstance(parameter, tuple) or len(parameter) != 4:
            raise Unsupported('parameter_pattern')
        name, contract, mode, default = parameter
        if type(name) is not str or len(name) > 240 or mode not in ('positional', 'positional_only', 'keyword_only'):
            raise Unsupported('parameter_mode')
        internal = aliases.get(name, name)
        if len(internal) > 240 or internal in parameters: raise Unsupported('parameter_aliases')
        parameters.append(internal)
        signature.append([name, mode, contract if type(contract) is str else id(contract)])
    events, nodes = [], [0]
    metadata_chars = [0]
    def event(node, inherited):
        span = _span(node)
        metadata_chars[0] += len(json.dumps(span, ensure_ascii=True))
        if metadata_chars[0] > 64000: raise Unsupported('source_metadata')
        effective = span or inherited
        events.append(effective)
        return len(events) - 1, effective
    def count(node):
        if checkpoint: checkpoint()
        nodes[0] += 1
        if nodes[0] > MAX_NODES: raise Unsupported('node_limit')
        if type(node) not in (Node, tuple) or not node: raise Unsupported('body_shape')
    def expression(node, inherited, depth=0):
        if depth > 64: raise Unsupported('depth_limit')
        count(node)
        enter, span = event(node, inherited)
        kind = node[0]
        if kind == 'literal' and len(node) == 2 and type(node[1]) is int and MIN_I64 <= node[1] <= MAX_I64:
            return Expression(kind, node[1], (), enter)
        if kind == 'name' and len(node) == 2 and node[1] in parameters:
            return Expression(kind, parameters.index(node[1]), (), enter)
        if kind == 'unary' and len(node) == 3 and node[1] in ('+', '-'):
            child = expression(node[2], span, depth + 1)
            events.append(span)
            return Expression(kind, node[1], (child,), enter, len(events) - 1)
        if kind == 'binary' and len(node) == 4 and node[1] in ('+', '-', '*'):
            children = (expression(node[2], span, depth + 1), expression(node[3], span, depth + 1))
            events.append(span)
            return Expression(kind, node[1], children, enter, len(events) - 1)
        raise Unsupported('unsupported_expression')
    body = function.body
    count(body)
    if body[0] != 'block' or len(body) != 2 or type(body[1]) is not list or len(body[1]) != 1:
        raise Unsupported('single_expression_required')
    body_event, body_span = event(body, {})
    prefix = [body_event]
    value = body[1][0]
    if type(value) in (Node, tuple) and value and value[0] == 'return':
        count(value)
        if len(value) != 2: raise Unsupported('body_shape')
        index, inherited = event(value, body_span)
        prefix.append(index); value = value[1]
    else:
        inherited = body_span
    result = expression(value, inherited)
    def plain(item):
        return [item.kind, item.value, [plain(child) for child in item.children], item.event, item.operation_event]
    canonical = [function.flavor, signature, sorted(aliases.items()),
                 function.returns if type(function.returns) is str else id(function.returns),
                 plain(result), prefix, events]
    fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    return Analysis(result, tuple(prefix), tuple(events), tuple(parameters), tuple(parameter[0] for parameter in function.params), fingerprint, nodes[0], body_span)


def _interval(expression, bound):
    if expression.kind == 'literal': interval = (expression.value, expression.value)
    elif expression.kind == 'name': interval = (-bound, bound)
    else:
        children = [_interval(child, bound) for child in expression.children]
        if expression.kind == 'unary':
            lower, upper = children[0]
            interval = (lower, upper) if expression.value == '+' else (-upper, -lower)
        else:
            (a, b), (c, d) = children
            if expression.value in ('<', '<=', '>', '>='): interval = (0, 1)
            elif expression.value == '+': interval = (a + c, b + d)
            elif expression.value == '-': interval = (a - d, b - c)
            else:
                products = (a*c, a*d, b*c, b*d)
                interval = (min(products), max(products))
    if interval[0] < MIN_I64 or interval[1] > MAX_I64: raise Unsupported('integer_range')
    return interval


def prove(analysis):
    bound = 2 ** 31 - 1
    while True:
        try:
            _interval(analysis.expression, bound)
            return bound
        except Unsupported:
            if bound <= 1: raise
            bound //= 2


@dataclass
class CompiledPlan:
    requested: str
    status: str
    reason: object = None
    analysis: object = None
    bound: object = None
    kernel: object = None
    native_calls: int = 0
    interpreter_calls: int = 0
    last_fallback: object = None
    regions: object = None
    flow: object = None

    def info(self, function):
        guards = []
        if self.bound is not None and not getattr(self.analysis, 'control_flow', False):
            guards = [{'name': name, 'minimum': -self.bound, 'maximum': self.bound} for name in self.analysis.public_parameters]
        return {'schema': 'chaos.compiled-function/1', 'status': self.status,
                'requestedTarget': self.requested, 'backend': 'llvm' if self.kernel else 'ast',
                'target': self.kernel.target if self.kernel else 'portable-ast',
                'compilerVersion': self.kernel.version if self.kernel else None,
                'parameterGuards': guards, 'nodeCount': self.analysis.node_count if self.analysis else 0,
                'nativeCalls': self.native_calls, 'interpreterCalls': self.interpreter_calls,
                'fallbackReason': self.reason, 'lastFallbackReason': self.last_fallback}

    def prepare(self, evaluator, function, scope):
        if self.kernel is None: return None, self.reason
        if evaluator.debug_hook: return None, 'debugger'
        trace_required = evaluator.expression_trace_required
        if (trace_required() if trace_required is not None else bool(evaluator.trace_handler)):
            return None, 'expression_trace'
        try:
            if self.regions is None:
                current = analyze(function)
            else:
                from .jit_regions import analyze_structured
                current = analyze_structured(function, control_flow=self.analysis.control_flow, resumable=self.analysis.resumable)
            if current.fingerprint != self.analysis.fingerprint:
                return None, 'definition_changed'
            if self.regions is not None and (len(current.regions) != len(self.analysis.regions) or
                    any(now.node is not original.node for now,original in zip(current.regions,self.analysis.regions))):
                return None, 'definition_changed'
        except (Unsupported, RecursionError):
            return None, 'definition_changed'
        arguments = [scope.get(name) for name in self.analysis.parameters]
        if not getattr(self.analysis, 'control_flow', False) and any(type(value) is not int or not -self.bound <= value <= self.bound for value in arguments):
            return None, 'argument_guard'
        return arguments, None

    def evaluate(self, evaluator, function, scope):
        if self.flow is not None:
            from .jit_flow import evaluate
            return (yield from evaluate(self, evaluator, function, scope))
        if self.regions is not None:
            from .jit_regions import evaluate_structured
            return (yield from evaluate_structured(self, evaluator, function, scope))
        from .suspension import CHECKPOINT
        body = function.body
        arguments, reason = self.prepare(evaluator, function, scope)
        if arguments is None:
            self.last_fallback = reason
            self.interpreter_calls += 1
            return (yield from evaluator._s_evaluate(body, scope, True, all_results=True))
        previous = evaluator.current_span
        evaluator.current_span = self.analysis.body_span or previous
        try:
            # Same entry safepoint as _s_evaluate(block), before its first tick.
            yield CHECKPOINT
            # A scheduler handoff can change public syntax or enable tracing.
            # Recheck before entering machine code, retaining the same captured
            # body object and the one block checkpoint already consumed.
            arguments, reason = self.prepare(evaluator, function, scope)
            if arguments is None:
                self.last_fallback = reason
                self.interpreter_calls += 1
                return (yield from evaluator._s_evaluate(body, scope, True, all_results=True, _entry_checkpoint_done=True))
            def checkpoint(index):
                span = self.analysis.spans[index] or previous
                evaluator.current_span = span
                try:
                    evaluator.tick()
                except LanguageError as error:
                    raise error.attach(span)
                except RecursionError:
                    raise ResourceLimit('语法求值深度超过限制').attach(span) from None
                except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError) as error:
                    raise LanguageError(str(error), code=type(error).__name__).attach(span) from None
            self.native_calls += 1
            self.last_fallback = None
            return self.kernel.invoke(arguments, checkpoint)
        except LanguageError as error:
            raise error.attach(getattr(body, 'span', {}))
        except RecursionError:
            raise ResourceLimit('语法求值深度超过限制').attach(getattr(body, 'span', {})) from None
        except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError) as error:
            raise LanguageError(str(error), code=type(error).__name__).attach(getattr(body, 'span', {})) from None
        finally:
            evaluator.current_span = previous


def _owned(evaluator, function):
    if type(function) is not Function:
        raise LanguageError('compile_function/compile_info 需要普通语言 Function', code='argument_error')
    if function.owner is not evaluator:
        raise LanguageError('编译函数不能跨执行上下文使用', code='ownership_error')
    return function


def compile_function(evaluator, function, target='auto'):
    _owned(evaluator, function)
    if type(target) is not str or target not in ('auto', 'native', 'interpreter', 'auto-control', 'native-control', 'auto-flow', 'native-flow'):
        raise LanguageError('编译目标必须为 auto、native、interpreter、auto-control、native-control、auto-flow 或 native-flow', code='argument_error')
    plan = CompiledPlan(target, 'interpreter', 'requested_interpreter' if target == 'interpreter' else None)
    result = replace(function, compiled_plan=plan)
    if target == 'interpreter': return result
    try:
        if target in ('auto-flow', 'native-flow'):
            from .jit_flow import compile_plan
            return compile_plan(evaluator, function, result, plan)
        if target in ('auto-control', 'native-control'):
            from .jit_regions import analyze_structured, compile_regions
            plan.analysis = analyze_structured(function, evaluator.tick, control_flow=True)
            return compile_regions(evaluator, function, result, plan)
        try:
            plan.analysis = analyze(function, evaluator.tick)
        except Unsupported:
            from .jit_regions import analyze_structured, compile_regions
            plan.analysis = analyze_structured(function, evaluator.tick)
            return compile_regions(evaluator, function, result, plan)
        plan.bound = prove(plan.analysis)
        if evaluator.native_kernel_count >= MAX_KERNELS:
            raise Unsupported('kernel_limit')
        from .native_jit import compile_kernel, BackendUnavailable
        evaluator.tick()
        try:
            kernel = compile_kernel(plan.analysis.expression, plan.analysis.prefix, code_cache=evaluator.native_code_cache)
        except (BackendUnavailable, OSError, RuntimeError) as error:
            plan.reason = str(error) if isinstance(error, BackendUnavailable) else 'backend_failure'
            if target in ('native', 'native-control', 'native-flow'):
                raise LanguageError('本机原生编译后端不可用: '+plan.reason, code='compiler_error') from None
            return result
        evaluator.native_kernel_count += 1
        plan.kernel, plan.status = kernel, 'native'
        evaluator.tick()
        return result
    except Unsupported as error:
        plan.reason, plan.status = str(error), 'unsupported'
        if target in ('native', 'native-control', 'native-flow'):
            raise LanguageError('函数不在原生编译子集内: '+plan.reason, code='compiler_error') from None
        return result


def compile_info(evaluator, function):
    _owned(evaluator, function)
    plan = function.compiled_plan or CompiledPlan('interpreter', 'interpreter', 'not_compiled')
    return plan.info(function)


def compile_optimization_info(evaluator, function):
    _owned(evaluator, function)
    plan = function.compiled_plan
    kernels = []
    if plan is not None:
        candidates = [item.kernel for item in plan.regions] if plan.regions is not None else [plan.kernel]
        for kernel in candidates:
            optimization = getattr(kernel, 'optimization', None)
            if optimization is not None: kernels.append(optimization.info())
    fields = ('originalOperations', 'emittedOperations', 'commonSubexpressions',
              'constantFolds', 'identitySimplifications', 'checkpointCount')
    return {'schema': 'chaos.compiled-optimization/1',
            'status': plan.status if plan is not None else 'not_compiled',
            'enabled': bool(kernels), 'algorithm': 'integer-value-dag/1',
            'kernelCount': len(kernels), **{name: sum(k[name] for k in kernels) for name in fields},
            'scheduled': any(k['scheduled'] for k in kernels), 'kernels': kernels}


def builtins(evaluator):
    return {'compile_function': lambda fn,target='auto': compile_function(evaluator, fn, target),
            'compile_info': lambda fn: compile_info(evaluator, fn),
            'compile_optimization_info': lambda fn: compile_optimization_info(evaluator, fn),
            'compile_flow_info': lambda fn: __import__('chaos.jit_flow', fromlist=['info']).info(evaluator, fn)}
