"""Guarded native scalar regions inside the language's original control flow.

The invocation context is explicit and private. It never replaces scopes,
CHECKPOINT yields, binding, branch selection or ReturnSignal handling.
"""
from dataclasses import dataclass, field
import hashlib
import json

from .jit import (Analysis, Expression, Unsupported, MIN_I64, MAX_I64,
                  MAX_NODES, MAX_PARAMETERS, MAX_KERNELS, _span, prove)
from .language import Node, LanguageError, ResourceLimit


@dataclass(frozen=True)
class Region:
    node_id: int
    analysis: Analysis
    result_type: str
    native: bool = True
    node: object = field(default=None,repr=False,compare=False)


@dataclass(frozen=True)
class StructuredAnalysis:
    regions: tuple
    parameters: tuple
    public_parameters: tuple
    fingerprint: str
    node_count: int
    body_span: dict
    control_flow: bool = False
    resumable: bool = False


@dataclass(frozen=True)
class CompiledRegion:
    region: Region
    bound: int
    kernel: object


def analyze_structured(function, checkpoint=None, *, control_flow=False, resumable=False):
    """Analyze only bounded immutable local/control syntax, without executing it."""
    if function.flavor != 'fn' and not (resumable and control_flow and function.flavor in ('gen_fn','async_fn','async_gen_fn')):
        raise Unsupported('suspending_function')
    parameter_limit = 10000 if resumable else MAX_PARAMETERS
    if type(function.params) is not list or len(function.params) > parameter_limit:
        raise Unsupported('parameter_limit')
    aliases = function.macro_bindings or {}
    if type(aliases) is not dict or len(aliases) > parameter_limit or any(type(k) is not str or type(v) is not str for k,v in aliases.items()):
        raise Unsupported('parameter_aliases')
    parameters, public, signature = [], [], []
    for parameter in function.params:
        if type(parameter) is not tuple or len(parameter) not in ((4,5) if resumable else (4,)): raise Unsupported('parameter_pattern')
        name, contract, mode, default = parameter[:4]
        if type(name) is not str or len(name) > 240 or mode not in (('positional', 'positional_only', 'keyword_only','varargs','varkw') if resumable else ('positional', 'positional_only', 'keyword_only')):
            raise Unsupported('parameter_mode')
        internal = aliases.get(name, name)
        if len(internal) > 240 or internal in parameters: raise Unsupported('parameter_aliases')
        parameters.append(internal); public.append(name)
        row=[name, mode, contract if type(contract) is str else id(contract)]
        if resumable:
            # Evaluated defaults are live values, not syntax. Retain identity
            # without traversing containers, invoking hooks or serializing them.
            row.extend([id(default),parameter[4] if len(parameter)==5 else None])
        signature.append(row)
    nodes, metadata, regions = [0], [0], []

    def enter(node, inherited, depth):
        if depth > 64: raise Unsupported('depth_limit')
        if checkpoint: checkpoint()
        nodes[0] += 1
        if nodes[0] > MAX_NODES: raise Unsupported('node_limit')
        if type(node) not in (Node, tuple) or not node: raise Unsupported('body_shape')
        own = _span(node)
        metadata[0] += len(json.dumps(own, ensure_ascii=True))
        if metadata[0] > 64000: raise Unsupported('source_metadata')
        return own or inherited

    def scalar(root, names, inherited, depth):
        # Bool bindings stay ordinary language values. They are deliberately
        # interpreted, never passed to a machine Int operand slot.
        if len(root) == 2 and ((root[0] == 'name' and type(root[1]) is str and names.get(root[1]) == 'Bool') or
                               (root[0] == 'literal' and (type(root[1]) is bool or root[1] is None))):
            span = enter(root,inherited,depth)
            typ = 'Bool' if root[0] == 'name' or type(root[1]) is bool else 'Null'
            regions.append(Region(id(root),None,typ,False,root))
            return typ,[root[0],root[1],span]
        spans, inputs = [], []
        def expression(node, parent, level):
            span = enter(node, parent, level)
            spans.append(span); event = len(spans)-1
            kind = node[0]
            if kind == 'literal' and len(node) == 2 and type(node[1]) is int and MIN_I64 <= node[1] <= MAX_I64:
                return Expression(kind, node[1], (), event), 'Int', [kind, node[1], span]
            if kind == 'name' and len(node) == 2 and type(node[1]) is str and (names.get(node[1]) == 'Int' or control_flow and names.get(node[1]) in ('Dynamic', 'Unknown')):
                if node[1] not in inputs: inputs.append(node[1])
                if len(inputs) > MAX_PARAMETERS: raise Unsupported('region_parameter_limit')
                return Expression(kind, inputs.index(node[1]), (), event), 'Int', [kind, node[1], span]
            if kind == 'unary' and len(node) == 3 and node[1] in ('+', '-'):
                child, typ, plain = expression(node[2], span, level+1)
                if typ != 'Int': raise Unsupported('integer_operand')
                spans.append(span)
                return Expression(kind, node[1], (child,), event, len(spans)-1), 'Int', [kind,node[1],plain,span]
            if kind == 'binary' and len(node) == 4 and node[1] in ('+', '-', '*', '<', '<=', '>', '>='):
                left, lt, lp = expression(node[2], span, level+1)
                right, rt, rp = expression(node[3], span, level+1)
                if lt != 'Int' or rt != 'Int': raise Unsupported('integer_operand')
                spans.append(span)
                typ = 'Bool' if node[1] in ('<', '<=', '>', '>=') else 'Int'
                return Expression(kind,node[1],(left,right),event,len(spans)-1),typ,[kind,node[1],lp,rp,span]
            raise Unsupported('unsupported_expression')
        result, typ, canonical = expression(root, inherited, depth)
        analysis = Analysis(result, (), tuple(spans), tuple(inputs), (), '', 0, inherited)
        regions.append(Region(id(root), analysis, typ,True,root))
        return typ, canonical

    def opaque(value, inherited, depth=0):
        """Fingerprint an interpreted subtree without inspecting runtime values.

        In particular iterator factories, cursors and protocols are never
        acquired, called or classified as pure during compilation.
        """
        if depth > 64: raise Unsupported('depth_limit')
        if type(value) in (Node, tuple):
            span = enter(value, inherited, depth)
            return ['opaque', [opaque(v, span, depth+1) for v in value], span]
        if type(value) is list:
            if len(value) > MAX_NODES: raise Unsupported('node_limit')
            return [opaque(v, inherited, depth+1) for v in value]
        if value is None or type(value) in (str, int, bool, float):
            try: encoded = json.dumps(value, allow_nan=False)
            except (ValueError, OverflowError): raise Unsupported('body_shape') from None
            metadata[0] += len(encoded)
            if metadata[0] > 64000: raise Unsupported('source_metadata')
            return value
        raise Unsupported('body_shape')

    def pattern(node, names, mutable=False):
        from .patterns import pattern_names
        try: leaves = pattern_names(node)
        except LanguageError: raise Unsupported('binding_pattern') from None
        for name in leaves: names[name] = 'Dynamic' if mutable else 'Unknown'

    def original_control(node, names, inherited, depth=0):
        # Scalar roots count themselves; control nodes count here exactly once.
        if type(node) not in (Node, tuple) or not node: raise Unsupported('body_shape')
        kind = node[0]
        if control_flow:
            # These operators retain the original strict Bool/short-circuit
            # and equality protocols. Only selected scalar children compile.
            if kind == 'binary' and len(node) == 4 and node[1] in ('&&','||','==','!='):
                span = enter(node,inherited,depth)
                _, left = control(node[2],names,span,depth+1)
                _, right = control(node[3],names,span,depth+1)
                return 'Bool',[kind,node[1],left,right,span]
            if kind == 'unary' and len(node) == 3 and node[1] == '!':
                span = enter(node,inherited,depth)
                _, child = control(node[2],names,span,depth+1)
                return 'Bool',[kind,'!',child,span]
            if kind in ('list','tuple','record') or kind == 'literal' and len(node) == 2 and type(node[1]) in (str,float):
                return 'Unknown',opaque(node,inherited,depth)
            if kind in ('var','let_pattern','var_pattern') and len(node) == 4:
                span = enter(node,inherited,depth)
                typ, child = control(node[3],names,span,depth+1)
                if kind == 'var':
                    if type(node[1]) is not str or type(node[2]) is not str: raise Unsupported('body_shape')
                    names[node[1]]='Dynamic'; target=node[1]
                else:
                    target=opaque(node[1],span,depth+1);pattern(node[1],names,kind=='var_pattern')
                return typ,[kind,target,node[2],child,span]
            if kind in ('assign','assign_pattern') and len(node) == 3:
                span = enter(node,inherited,depth)
                if kind == 'assign':
                    if type(node[1]) is not str or node[1] not in names: raise Unsupported('free_assignment')
                    target=node[1]
                else:
                    from .patterns import pattern_names
                    if any(name not in names for name in pattern_names(node[1])): raise Unsupported('free_assignment')
                    target=opaque(node[1],span,depth+1)
                typ, child = control(node[2],names,span,depth+1)
                return typ,[kind,target,child,span]
            if kind == 'aug_assign' and len(node) == 4:
                span = enter(node,inherited,depth)
                if type(node[1]) not in (Node,tuple) or len(node[1])!=2 or node[1][0]!='name' or node[1][1] not in names:
                    raise Unsupported('assignment_target')
                target=opaque(node[1],span,depth+1)
                _, child=control(node[3],names,span,depth+1)
                return 'Unknown',[kind,target,node[2],child,span]
            if kind == 'while' and len(node) == 3:
                span = enter(node,inherited,depth)
                _, condition = control(node[1],names,span,depth+1)
                _, body = control(node[2],dict(names),span,depth+1)
                return 'Null',[kind,condition,body,span]
            if (kind in ('for','for_pattern') or resumable and function.flavor in ('async_fn','async_gen_fn') and kind in ('async_for','async_for_pattern')) and len(node) == 4:
                span = enter(node,inherited,depth)
                source=opaque(node[2],span,depth+1);local=dict(names)
                if kind in ('for','async_for'):
                    if type(node[1]) is not str: raise Unsupported('body_shape')
                    local[node[1]]='Unknown';target=node[1]
                else:
                    target=opaque(node[1],span,depth+1);pattern(node[1],local)
                _, body=control(node[3],local,span,depth+1)
                return 'Null',[kind,target,source,body,span]
            if kind == 'loop_else' and len(node) == 3:
                span = enter(node,inherited,depth)
                if node[1][0] not in (('while','for','for_pattern','async_for','async_for_pattern') if resumable and function.flavor in ('async_fn','async_gen_fn') else ('while','for','for_pattern')): raise Unsupported('loop_shape')
                _, loop=control(node[1],names,span,depth+1)
                typ, otherwise=control(node[2],dict(names),span,depth+1)
                return typ,[kind,loop,otherwise,span]
            if resumable and kind=='match'and len(node)==3 and type(node[2])in(list,tuple):
                span=enter(node,inherited,depth)
                _,subject=control(node[1],dict(names),span,depth+1);cases=[]
                for case in node[2]:
                    if not isinstance(case,(tuple,list))or len(case)!=3:raise Unsupported('body_shape')
                    local=dict(names);target=opaque(case[0],span,depth+1);pattern(case[0],local)
                    guard=None if case[1]is None else control(case[1],local,span,depth+1)[1]
                    body=control(case[2],local,span,depth+1)[1];cases.append([target,guard,body])
                return 'Unknown',[kind,subject,cases,span]
            if resumable and kind in ('with','async_with','with_pattern','async_with_pattern') and len(node)==4:
                span=enter(node,inherited,depth)
                _,manager=control(node[2],dict(names),span,depth+1)
                local=dict(names)
                if kind in ('with_pattern','async_with_pattern'):
                    target=opaque(node[1],span,depth+1);pattern(node[1],local)
                else:
                    if type(node[1])is not str:raise Unsupported('body_shape')
                    local[node[1]]='Unknown';target=node[1]
                typ,body=control(node[3],local,span,depth+1)
                return typ,[kind,target,manager,body,span]
            if resumable and kind in ('try','try_cases','try_group','finally','try_else'):
                span=enter(node,inherited,depth)
                if kind in ('finally','try_else') and len(node)==3:
                    _,body=control(node[1],dict(names),span,depth+1)
                    typ,after=control(node[2],dict(names),span,depth+1)
                    return typ,[kind,body,after,span]
                if kind=='try'and len(node)==4 and type(node[2])is str:
                    _,body=control(node[1],dict(names),span,depth+1)
                    local=dict(names);local[node[2]]='Unknown'
                    typ,handler=control(node[3],local,span,depth+1)
                    return typ,[kind,body,node[2],handler,span]
                if kind in ('try_cases','try_group')and len(node)==3 and type(node[2])is list:
                    from .language import catch_pattern_names
                    from .patterns import pattern_names
                    catch_names = pattern_names if kind == 'try_group' else catch_pattern_names
                    _,body=control(node[1],dict(names),span,depth+1)
                    rows=[]
                    for case in node[2]:
                        if not isinstance(case,(tuple,list))or len(case)!=(5 if kind=='try_group'else 3):raise Unsupported('body_shape')
                        target=case[1]if kind=='try_group'else case[0]
                        guard=case[2]if kind=='try_group'else case[1]
                        handler=case[4]if kind=='try_group'else case[2]
                        local=dict(names)
                        try:
                            for name in catch_names(target):local[name]='Unknown'
                        except LanguageError:raise Unsupported('body_shape')from None
                        if kind=='try_group':local[case[3]]='Unknown'
                        pattern_plain=opaque(target,span,depth+2)
                        gp=None if guard is None else control(guard,local,span,depth+2)[1]
                        hp=control(handler,local,span,depth+2)[1]
                        rows.append([pattern_plain,gp,case[3]if kind=='try_group'else None,hp])
                    return 'Unknown',[kind,body,rows,span]
                raise Unsupported('body_shape')
            if kind in ('break','continue') and len(node) == 1:
                span=enter(node,inherited,depth)
                return 'Null',[kind,span]
        if kind not in ('block','let','return','if'):
            return scalar(node, names, inherited, depth)
        span = enter(node, inherited, depth)
        if kind == 'block' and len(node) == 2 and type(node[1]) is list:
            local = dict(names); parts = []; typ = 'Null'
            for item in node[1]:
                typ, plain = control(item, local, span, depth+1); parts.append(plain)
            return typ, [kind,parts,span]
        if kind == 'let' and len(node) == 4 and type(node[1]) is str and len(node[1]) <= 240 and type(node[2]) is str and len(node[2]) <= 240:
            typ, plain = control(node[3], names, span, depth+1)
            names[node[1]] = typ
            return typ, [kind,node[1],node[2],plain,span]
        if kind == 'return' and len(node) == 2:
            typ, plain = control(node[1], names, span, depth+1)
            return typ, [kind,plain,span]
        if kind == 'if' and len(node) == 4:
            condition, cp = control(node[1], dict(names), span, depth+1)
            left, lp = control(node[2], dict(names), span, depth+1)
            right, rp = control(node[3], dict(names), span, depth+1)
            return left if left == right else 'Unknown', [kind,cp,lp,rp,span]
        raise Unsupported('body_shape')

    def control(node,names,inherited,depth=0):
        if not resumable:return original_control(node,names,inherited,depth)
        # Scalar discovery is speculative, never a prerequisite for a host
        # semantic event. Failed attempts cannot consume the structural budget
        # or publish partial regions. Real compiler checkpoints remain charged.
        snapshot=(nodes[0],metadata[0],len(regions),dict(names))
        try:return original_control(node,names,inherited,depth)
        except Unsupported as error:
            if str(error) in ('depth_limit','node_limit','source_metadata','body_shape'):
                raise
            nodes[0],metadata[0]=snapshot[:2];del regions[snapshot[2]:]
            names.clear();names.update(snapshot[3])
            return 'Unknown',opaque(node,inherited,depth)

    if resumable:
        for row,parameter in zip(signature,function.params):
            if len(parameter)==5:
                # Bound the complete syntax graph before pattern_names walks
                # it, including rechecks that intentionally have no tick hook.
                row[-1]=opaque(parameter[4],{})
                from .patterns import pattern_names
                try:leaves=pattern_names(parameter[4],tick=checkpoint)
                except LanguageError as error:
                    if error.code != 'syntax_error': raise
                    raise Unsupported('parameter_pattern') from None
                for leaf in leaves:
                    internal_leaf=aliases.get(leaf,leaf)
                    if internal_leaf in parameters:raise Unsupported('parameter_aliases')
                    parameters.append(internal_leaf)
        encoded=json.dumps([signature,sorted(aliases.items())],ensure_ascii=True,separators=(',',':'))
        metadata[0]+=len(encoded)
        if metadata[0]>64000:raise Unsupported('source_metadata')

    body = function.body
    if type(body) not in (Node, tuple) or not body or body[0] != 'block': raise Unsupported('body_shape')
    _, canonical = control(body, {name:'Unknown' if control_flow else 'Int' for name in parameters}, {})
    if not regions and not resumable: raise Unsupported('no_scalar_regions')
    payload = [function.flavor,signature,sorted(aliases.items()),
               function.returns if type(function.returns) is str else id(function.returns),canonical]
    if control_flow: payload.append('resumable-host-regions/1' if resumable else 'guarded-control-regions/1')
    fingerprint = hashlib.sha256(json.dumps(payload,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
    return StructuredAnalysis(tuple(regions),tuple(parameters),tuple(public),fingerprint,nodes[0],canonical[-1],control_flow,resumable)


def compile_regions(evaluator, function, result, plan, *, optional=False):
    """Allocate proven regional kernels, charging every successful allocation."""
    from .native_jit import compile_kernel, BackendUnavailable
    if optional:
        compiled=[];bounds=[]
        for region in plan.analysis.regions:
            bound=None;kernel=None
            if region.native:
                try:bound=prove(region.analysis)
                except Unsupported:pass
                if bound is not None and evaluator.native_kernel_count<MAX_KERNELS:
                    try:
                        evaluator.tick();kernel=compile_kernel(region.analysis.expression,(),code_cache=evaluator.native_code_cache)
                        evaluator.native_kernel_count+=1;evaluator.tick()
                    except (BackendUnavailable,OSError,RuntimeError):
                        kernel=None
            if kernel is not None:bounds.append(bound)
            compiled.append(CompiledRegion(region,bound,kernel))
        plan.regions=tuple(compiled);plan.bound=min(bounds) if bounds else None
        return result
    proofs = [(region,prove(region.analysis) if region.native else None) for region in plan.analysis.regions]
    native_proofs = [(region,bound) for region,bound in proofs if region.native]
    if not native_proofs: raise Unsupported('no_scalar_regions')
    plan.bound = min(bound for region,bound in native_proofs)
    if evaluator.native_kernel_count + len(native_proofs) > MAX_KERNELS: raise Unsupported('kernel_limit')
    compiled = []
    try:
        for region, bound in proofs:
            if not region.native:
                compiled.append(CompiledRegion(region,bound,None)); continue
            evaluator.tick()
            kernel = compile_kernel(region.analysis.expression, (), code_cache=evaluator.native_code_cache)
            evaluator.native_kernel_count += 1
            compiled.append(CompiledRegion(region,bound,kernel))
            evaluator.tick()
    except (BackendUnavailable,OSError,RuntimeError) as error:
        plan.reason = str(error) if isinstance(error,BackendUnavailable) else 'backend_failure'
        if plan.requested in ('native','native-control'):
            raise LanguageError('本机原生编译后端不可用: '+plan.reason,code='compiler_error') from None
        return result
    plan.regions = tuple(compiled)
    plan.kernel, plan.status = next(item.kernel for item in compiled if item.kernel is not None), 'native'
    return result


class RegionInvocation:
    """Per-function-call counters and AST guards, never evaluator-global state."""
    def __init__(self, plan, evaluator, function, scope):
        self.plan,self.evaluator,self.function,self.scope = plan,evaluator,function,scope
        self.regions = {item.region.node_id:item for item in plan.regions}
        self.native_counted = self.interpreter_counted = False
        self.disabled_reason = None
        plan.last_fallback = None

    def fallback(self, reason):
        if not self.interpreter_counted:
            self.plan.interpreter_calls += 1; self.interpreter_counted = True
        self.plan.last_fallback = reason

    def try_region(self, node, env, inherited):
        # Recheck even at original control nodes: a CHECKPOINT may install a
        # replacement AST whose new expression identities are absent here.
        if self.disabled_reason is not None:
            self.fallback(self.disabled_reason); return False,None
        _, reason = self.plan.prepare(self.evaluator,self.function,self.scope)
        if reason is not None:
            if reason in ('definition_changed','debugger','expression_trace'):self.disabled_reason=reason
            self.fallback(reason); return False,None
        item = self.regions.get(id(node))
        if item is None: return False,None
        if item.region.node is not node:
            self.disabled_reason='definition_changed'
            self.fallback(self.disabled_reason); return False,None
        if item.kernel is None:
            self.fallback('argument_guard'); return False,None
        arguments = [env.get(name) for name in item.region.analysis.parameters]
        if any(type(value) is not int or not -item.bound <= value <= item.bound for value in arguments):
            self.fallback('argument_guard'); return False,None
        e = self.evaluator
        def checkpoint(index):
            span = item.region.analysis.spans[index] or inherited
            e.current_span = span
            try: e.tick()
            except LanguageError as error: raise error.attach(span)
            except RecursionError:
                raise ResourceLimit('语法求值深度超过限制').attach(span) from None
            except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError,OverflowError) as error:
                raise LanguageError(str(error),code=type(error).__name__).attach(span) from None
        if not self.native_counted:
            self.plan.native_calls += 1; self.native_counted = True
        value = item.kernel.invoke(arguments,checkpoint)
        return True,bool(value) if item.region.result_type == 'Bool' else value


def evaluate_structured(plan,evaluator,function,scope):
    body = function.body
    arguments, reason = plan.prepare(evaluator,function,scope)
    if arguments is None:
        plan.last_fallback = reason; plan.interpreter_calls += 1
        return (yield from evaluator._s_evaluate(body,scope,True,all_results=True))
    invocation = RegionInvocation(plan,evaluator,function,scope)
    return (yield from evaluator._s_evaluate(body,scope,True,all_results=True,_compiled_regions=invocation))
