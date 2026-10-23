"""Bounded, pure AST macros. No runtime environment, providers, or I/O are exposed.

The original parse trees remain the source/IR authority. This module produces a
separate executable tree and uses lexical alpha-renaming, never source rewriting.
"""
from dataclasses import dataclass
import math
import time

from .language import (Node, LanguageError, ResourceLimit, TYPES, Env, MISSING,
                       Evaluator, check_type)
from .multiple_values import Completion,components,primary,index as result_index
from .tuple_values import TupleValue
from .numeric_values import Fraction,Complex,Decimal,validate as numeric_validate,equal as numeric_equal
from .condition_values import Condition
from .set_values import (SetValue, SetBuilder, set_equal, set_binary,
                         set_add, set_discard, set_is_disjoint)


MAX_STEPS = 50000
MAX_NODES = 50000
MAX_CALLS = 1000
MAX_DEPTH = 64
MAX_SECONDS = 2


@dataclass(frozen=True)
class Syntax:
    tree: object


@dataclass
class _Function:
    params: list
    returns: str
    body: object
    env: Env
    name: str


@dataclass(frozen=True)
class _Builtin:
    name: str


class _Return(Exception):
    def __init__(self, value): self.value = value


def _children(node, visit):
    """Map expressions, leaving names, parameter labels and record keys intact."""
    kind = node[0]
    if kind in ('block', 'list', 'tuple', 'set', 'rec'):
        return (kind, [visit(x) for x in node[1]])
    if kind == 'record': return (kind, [(key, visit(x)) for key, x in node[1]])
    if kind in ('list_comp', 'set_comp', 'gen_comp', 'async_gen_comp'):
        return (kind, visit(node[1]), [visit(clause) for clause in node[2]])
    if kind == 'record_comp':
        return (kind, visit(node[1]), visit(node[2]), [visit(clause) for clause in node[3]])
    if kind in ('comp_for', 'comp_async_for', 'comp_for_pattern', 'comp_async_for_pattern'): return (kind, node[1], visit(node[2]))
    if kind == 'comp_if': return (kind, visit(node[1]))
    if kind == 'decorated': return (kind, [visit(x) for x in node[1]], visit(node[2]))
    if kind == 'class':
        from .class_creation import class_options
        result=(kind,node[1],[visit(x) for x in node[2]],visit(node[3]))
        options=class_options(node)
        return result+([visit(x) for x in options],) if options else result
    if kind in ('set_member', 'set_item'): return (kind, visit(node[1]), visit(node[2]), visit(node[3]))
    if kind == 'aug_assign': return (kind, visit(node[1]), node[2], visit(node[3]))
    if kind == 'slice': return (kind, *(None if child is None else visit(child) for child in node[1:]))
    if kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
        params = [param if len(param) == 2 else (*param[:3], None if param[3] is None else visit(param[3]), *param[4:])
                  for param in node[1]]
        return (kind, params, node[2], visit(node[3]), node[4])
    if kind in ('let', 'var', 'let_pattern', 'var_pattern'): return (*node[:3], visit(node[3]))
    if kind in ('def', 'agent', 'assign', 'assign_pattern', 'keyword'): return (*node[:2], visit(node[2]))
    if kind in ('call',): return (kind, visit(node[1]), [visit(x) for x in node[2]])
    if kind in ('macrocall',): return (kind, node[1], [visit(x) for x in node[2]])
    if kind in ('do', 'throw', 'return', 'await', 'yield', 'yield_from', 'spread', 'kwspread', 'quote', 'unquote'):
        return (kind, visit(node[1]))
    if kind in ('for', 'async_for', 'for_pattern', 'async_for_pattern', 'with', 'async_with', 'with_pattern', 'async_with_pattern'): return (*node[:2], visit(node[2]), visit(node[3]))
    if kind == 'try': return (kind, visit(node[1]), node[2], visit(node[3]))
    if kind == 'try_group': return (kind, visit(node[1]), [visit(case) for case in node[2]])
    if kind == 'group_case': return (kind, node[1], None if node[2] is None else visit(node[2]), node[3], visit(node[4]))
    if kind in ('match', 'try_cases'):
        return (kind, visit(node[1]), [(pattern, None if guard is None else visit(guard), visit(body))
                                      for pattern, guard, body in node[2]])
    if kind == 'unary': return (*node[:2], visit(node[2]))
    if kind == 'binary': return (*node[:2], visit(node[2]), visit(node[3]))
    if kind in ('index', 'subscript', 'while', 'finally', 'loop_else', 'try_else'): return (kind, visit(node[1]), visit(node[2]))
    if kind == 'if': return (kind, visit(node[1]), visit(node[2]), visit(node[3]))
    return tuple(node)


def _has_phase_nodes(tree):
    pending = [tree]
    while pending:
        node = pending.pop()
        if node[0] in ('macro', 'macrocall', 'quote', 'unquote'): return True
        _children(node, lambda child: pending.append(child))
    return False


def _check(value, contract):
    if contract == 'Function' and isinstance(value, (_Function, _Builtin)): return value
    return check_type(value, contract)


class Expander:
    """One budget and fresh-name namespace for the whole closed source bundle."""
    def __init__(self, trees, *, imports=None, macro_exports=None, step_limit=MAX_STEPS, node_limit=MAX_NODES,
                 call_limit=MAX_CALLS, depth_limit=MAX_DEPTH, timeout=MAX_SECONDS, checkpoint=None, external_macros=None):
        self.trees = trees
        self.checkpoint = checkpoint
        self.steps = self.nodes = self.calls = self.serial = 0
        self.step_limit, self.node_limit = step_limit, node_limit
        self.call_limit, self.depth_limit = call_limit, depth_limit
        self.deadline = time.monotonic() + timeout
        self.used = set()
        self.current = None
        self.current_module = None
        self.active = []
        # Only validated raw declaration maps are injected by the v3 compiler.
        # Local collection in expand() retains external module namespaces.
        self.macros = {name: dict(definitions) for name, definitions in (external_macros or {}).items()}
        from .decimal_values import DecimalContext
        self.decimal_context=DecimalContext()
        # Linking is supplied by ProgramBundle; no source or filesystem reads
        # occur during expansion. A qualified call sees only explicit exports.
        self.imports = imports or {}
        self.macro_exports = macro_exports or {}
        # Names in unquoted caller syntax must never collide with fresh binders.
        pending = list(trees.values())
        while pending:
            if self.checkpoint: self.checkpoint()
            value = pending.pop()
            if isinstance(value, str): self.used.add(value)
            elif isinstance(value, (tuple, list)): pending.extend(value)

    def tick(self, depth=0):
        if self.checkpoint: self.checkpoint()
        self.steps += 1
        if self.steps > self.step_limit: self.limit('宏编译超过计算步数限制')
        if depth > self.depth_limit: self.limit('宏编译或展开嵌套超过深度限制')
        if time.monotonic() > self.deadline: self.limit('宏编译超过时间限制')

    def details(self, node=None):
        if not self.current: return None
        definition, call = self.current
        return {'macro': definition[1], 'definition': dict(getattr(definition, 'span', {})),
                'origin': dict(getattr(node, 'span', {})),
                'expansion': [name for name, _ in self.active],
                'definitionModule': self.current_module}

    def fail(self, message, node=None):
        location = self.current[1] if self.current else node
        raise LanguageError(message, code='macro_error', value=self.details(node)).attach(
            getattr(location, 'span', {}))

    def limit(self, message):
        error = ResourceLimit(message)
        error.value = self.details()
        if self.current: error.attach(getattr(self.current[1], 'span', {}))
        raise error

    def fresh(self, name):
        while True:
            self.serial += 1
            candidate = f'__chaos_macro_{self.serial}_{name}'
            if candidate not in self.used:
                self.used.add(candidate)
                return candidate

    def node(self, original, values, *, introduced=False):
        self.nodes += 1
        if self.nodes > self.node_limit: self.limit('宏展开超过语法节点数量限制')
        span = dict(getattr(original, 'span', {}))
        if introduced and self.current:
            span = dict(getattr(self.current[1], 'span', {}))
            span['macroOrigin'] = self.details(original)
        result = Node(values, span)
        capture=getattr(original,'numeric_capture',None)
        if capture is not None: result.numeric_capture=capture
        condition_capture=getattr(original,'condition_capture',None)
        if condition_capture is not None:result.condition_capture=condition_capture
        aliases = getattr(original, 'macro_bindings', None)
        if aliases: result.macro_bindings = dict(aliases)
        module = self.current_module if introduced else getattr(original, 'macro_module', None)
        if module is not None: result.macro_module = module
        return result

    def clone(self, value, depth=0, module=None):
        self.tick(depth)
        if isinstance(value, tuple):
            children = tuple(self.clone(x, depth + 1, module) for x in value)
            if not isinstance(value, Node): return children
            result = self.node(value, children)
            if module is not None and not hasattr(result, 'macro_module'): result.macro_module = module
            return result
        if isinstance(value, list): return [self.clone(x, depth + 1, module) for x in value]
        return value

    def expand(self):
        # Collect every module first so parse/iteration order cannot change
        # definition-site helper resolution, including forward declarations.
        for filename, tree in self.trees.items():
            definitions = {}
            for node in tree[1]:
                if node[0] == 'macro':
                    if node[1] in definitions: self.fail('宏重复声明 '+node[1], node)
                    definitions[node[1]] = node
            self.macros[filename] = definitions
        results = {}
        for filename, tree in self.trees.items():
            if not _has_phase_nodes(tree):
                results[filename] = tree
                continue
            results[filename] = self.node(tree, ('block', [
                self.walk(node, module=filename) for node in tree[1] if node[0] != 'macro']))
            self.validate_scope(results[filename])
        return results

    def resolve_macro(self, name, module, node):
        parts = name.split('.')
        if len(parts) == 1:
            definition = self.macros.get(module, {}).get(name)
            if definition is None: self.fail('当前模块未声明宏 '+name, node)
            return module, definition
        if len(parts) != 2 or not all(parts):
            self.fail('宏限定名只允许一级 import 别名 Alias.name', node)
        alias, exported = parts
        target = self.imports.get(module, {}).get(alias)
        if target is None: self.fail('宏命名空间不是当前模块的顶层 import 别名 '+alias, node)
        if exported not in self.macro_exports.get(target, ()):
            self.fail('模块未导出宏 '+name, node)
        definition = self.macros.get(target, {}).get(exported)
        if definition is None: self.fail('模块未声明宏 '+name, node)
        return target, definition

    def validate_scope(self, tree):
        # Quote syntax is parsed before its insertion context is known. Check
        # the executable AST before ANY module begins evaluating effects.
        pending = [(tree, None, 0, True)]
        while pending:
            node, function, loops, await_allowed = pending.pop()
            self.tick()
            kind = node[0]
            message = None
            if kind == 'return' and function is None: message = '宏展开 return 必须位于函数内'
            if kind in ('break', 'continue') and not loops: message = '宏展开循环控制必须位于当前函数循环内'
            if kind in ('yield', 'yield_from') and function not in ('gen_fn', 'async_gen_fn'): message = '宏展开 yield 必须位于生成器内'
            if kind in ('await', 'async_for', 'async_for_pattern', 'async_with', 'async_with_pattern') and not await_allowed: message = '宏展开 await/async with 仅允许顶层或 async fn 内'
            if kind in ('spread', 'kwspread', 'keyword'): message = '宏展开参数或展开只能用于调用参数或集合直接元素'
            if kind == 'slice': message = '宏展开切片只能用于 item 索引位置'
            if kind in ('loop_else', 'try_else'):
                allowed = ('while', 'for', 'async_for', 'for_pattern', 'async_for_pattern') if kind == 'loop_else' else ('try', 'try_cases', 'try_group')
                if len(node) != 3 or node[1][0] not in allowed or node[2][0] != 'block':
                    message = '宏展开完成分支必须包含对应控制结构和 else 块'
            if kind == 'try_cases':
                from .language import validate_try_cases
                validate_try_cases(node, tick=self.tick)
            if kind == 'try_group':
                from .language import validate_try_group
                validate_try_group(node, tick=self.tick)
            if kind == 'group_case':message = '分组处理分支只能位于 try_group.cases 内'
            if kind == 'aug_assign' and node[1][0] not in ('name', 'index', 'subscript'):
                message = '宏展开增强赋值目标必须是名字、成员或索引'
            if kind in ('assign_pattern', 'with_pattern', 'async_with_pattern'):
                if node[1][0] not in ('p_list', 'p_tuple', 'p_record', 'p_tag', 'p_class'):
                    message = '宏展开结构目标必须是列表、元组、记录、构造器或类字段模式'
                else:
                    from .patterns import pattern_names
                    pattern_names(node[1], tick=self.tick)
            if message:
                span = getattr(node, 'span', {})
                raise LanguageError(message, code='macro_error', value=span.get('macroOrigin')).attach(span)
            if kind == 'try_group':
                pending.append((node[1], function, loops, await_allowed))
                for case in node[2]:
                    if case[2] is not None:pending.append((case[2], function, loops, await_allowed))
                    pending.append((case[4], function, loops, await_allowed))
            elif kind == 'set':
                if not node[1]: self.fail('宏展开集合需要至少一个元素或展开；空集合使用 {*[]}', node)
                for item in node[1]:
                    pending.append((item[1] if item[0] == 'spread' else item, function, loops, await_allowed))
            elif kind == 'call':
                pending.append((node[1], function, loops, await_allowed))
                for argument in node[2]:
                    child = argument[1] if argument[0] in ('spread', 'kwspread') else argument[2] if argument[0] == 'keyword' else argument
                    pending.append((child, function, loops, await_allowed))
            elif kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
                from .parameters import validate_parameters
                validate_parameters(node[1], tick=self.tick)
                for param in node[1]:
                    if len(param) >= 4 and param[3] is not None:
                        pending.append((param[3], None, 0, await_allowed))
                pending.append((node[3], kind, 0, kind in ('async_fn', 'async_gen_fn')))
            elif kind in ('list_comp', 'set_comp', 'record_comp', 'gen_comp', 'async_gen_comp'):
                from .language import comprehension_scopes
                _, expressions = comprehension_scopes(node, await_allowed)
                pending.extend((value, None, 0, allowed) for value, allowed in expressions)
            elif kind in ('comp_for', 'comp_async_for', 'comp_for_pattern', 'comp_async_for_pattern', 'comp_if'):
                self.fail('推导子句只能出现在推导式内部', node)
            elif kind == 'decorated':
                if node[2][0] not in ('def', 'class', 'agent'):
                    self.fail('宏展开装饰器只能用于函数、类或智能体声明', node)
                pending.extend((value, None, 0, await_allowed) for value in node[1])
                pending.append((node[2], function, loops, await_allowed))
            elif kind == 'class':
                # Bases execute in the enclosing scope. Member defaults cannot
                # escape to its return/loop/yield target, but keep its async
                # permission; method bodies establish their own function scope.
                pending.extend((base, function, loops, await_allowed) for base in node[2])
                from .class_creation import class_options
                labels=set()
                for option in class_options(node):
                    if option[0] not in ('keyword','kwspread'):self.fail('宏展开类选项需要具名参数或 ** 展开',option)
                    if option[0]=='keyword':
                        if option[1] in labels:self.fail('宏展开类选项重复',option)
                        labels.add(option[1])
                    pending.append((option[2] if option[0]=='keyword' else option[1],function,loops,await_allowed))
                for member in node[3][1]:
                    if member[0] == 'decorated':
                        if member[2][0] != 'def': self.fail('宏展开类体装饰器只能用于方法', member)
                        value = member
                    else: value = member[2] if member[0] == 'def' else member[3]
                    pending.append((value, None, 0, await_allowed))
            elif kind in ('subscript', 'set_item'):
                pending.append((node[1], function, loops, await_allowed))
                key = node[2]
                keys = key[1] if key[0] == 'tuple' else [key]
                for component in keys:
                    if component[0] == 'slice':
                        pending.extend((bound, function, loops, await_allowed) for bound in component[1:] if bound is not None)
                    else:
                        pending.append((component, function, loops, await_allowed))
                if kind == 'set_item': pending.append((node[3], function, loops, await_allowed))
            elif kind in ('for', 'async_for', 'for_pattern', 'async_for_pattern'):
                pending.extend([(node[2], function, loops, await_allowed),
                                (node[3], function, loops + 1, await_allowed)])
            elif kind == 'while':
                pending.extend([(node[1], function, loops, await_allowed),
                                (node[2], function, loops + 1, await_allowed)])
            else:
                _children(node, lambda child: pending.append((child, function, loops, await_allowed)))

    def walk(self, value, depth=0, module=None):
        self.tick(depth)
        if isinstance(value, tuple):
            module = getattr(value, 'macro_module', module)
            if value and value[0] == 'macrocall':
                name, args = value[1:]
                definition_module, definition = self.resolve_macro(name, module, value)
                previous, previous_module = self.current, self.current_module
                self.current = (definition, value)
                self.current_module = definition_module
                self.active.append((name, value))
                try:
                    if len(args) != len(definition[2]): self.fail('宏参数数量不符 '+name, value)
                    self.calls += 1
                    if self.calls > self.call_limit: self.limit('宏调用次数超过限制')
                    self.tick(len(self.active))
                    env = Env()
                    for helper in ('return_values','values_list','multiple_value_list','nth_value','multiple_value_call','multiple_value_bind','multiple_value_prog1','condition','condition_info','syntax_literal', 'syntax_kind', 'syntax_value', 'tuple', 'set_of', 'set_add', 'set_discard', 'set_is_disjoint', 'decimal','decimal_text','decimal_quantize','fraction', 'complex', 'conjugate', 'numeric_encode', 'numeric_decode'):
                        env.bind(helper, _Builtin(helper))
                    params = Env(env)
                    for param, argument in zip(definition[2], args):
                        syntax = self.clone(argument, module=module)
                        if not isinstance(syntax, Node): syntax = self.node(argument, syntax)
                        if not hasattr(syntax, 'macro_module'): syntax.macro_module = module
                        params.bind(param, Syntax(syntax))
                    result = self.pure(definition[3], params)
                    if not isinstance(result, Syntax): self.fail('宏必须返回 Syntax 语法对象', definition[3])
                    return self.walk(result.tree, depth + 1, definition_module)
                except ResourceLimit as error:
                    error.value = error.value or self.details()
                    raise error.attach(getattr(value, 'span', {}))
                except LanguageError as error:
                    if error.code == 'macro_error': raise
                    self.fail(error.message, definition[3])
                except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError) as error:
                    self.fail(str(error), definition[3])
                finally:
                    self.active.pop()
                    self.current, self.current_module = previous, previous_module
            if value and value[0] in ('macro', 'quote', 'unquote'):
                self.fail('编译期语法只能出现在宏定义及其 quote/unquote 内', value)
            children = _children(value, lambda child: self.walk(child, depth + 1, module))
            return self.node(value, children) if isinstance(value, Node) else children
        if isinstance(value, list): return [self.walk(x, depth + 1, module) for x in value]
        return value

    def value_budget(self, value, depth=0):
        self.tick(depth)
        if type(value) is Completion:
            for child in components(value,self):self.value_budget(child,depth+1)
            return value
        if isinstance(value, (Syntax, _Function, _Builtin)): return value
        if value is None or type(value) is bool: return value
        if type(value) is int:
            if value.bit_length() > 14000: self.limit('宏整数超过 14000 bit 限制')
        elif type(value) is float:
            if not math.isfinite(value): self.fail('宏不支持非有限数值')
        elif type(value) is Condition:
            from .condition_values import validate
            validate(value,self.tick)
        elif type(value) in (Fraction,Complex,Decimal): numeric_validate(value,self.tick)
        elif isinstance(value, str):
            if len(value) > 250000: self.limit('宏字符串超过长度限制')
        elif isinstance(value, (list, dict, TupleValue, SetValue)):
            if type(value) is SetValue and value.owner is not None:self.fail('宏需要纯 Set，不能捕获运行时协议集合')
            if len(value) > 10000: self.limit('宏容器超过 10000 项限制')
            for item in (value.values() if isinstance(value, dict) else value):
                self.value_budget(item, depth + 1)
        else: self.fail('不支持的编译期值')
        return value

    def literal(self, value, origin, depth=0):
        self.tick(depth)
        if type(value) is Condition:
            from .condition_values import capture_constructor,validate
            validate(value,self.tick)
            target=self.node(origin,('name','condition'),introduced=True)
            target.condition_capture=capture_constructor()
            return self.node(origin,('call',target,[self.literal(x,origin,depth+1) for x in (value.kind,value.message,value.data)]),introduced=True)
        if type(value) in (Fraction,Complex,Decimal):
            from .numeric_values import capture_constructor
            name='decimal' if type(value) is Decimal else 'fraction' if type(value) is Fraction else 'complex'
            target=self.node(origin,('name',name),introduced=True)
            target.numeric_capture=capture_constructor(name)
            from .decimal_values import text as decimal_text
            fields=(decimal_text(value),) if type(value) is Decimal else (value.numerator,value.denominator) if type(value) is Fraction else (value.real,value.imag)
            return self.node(origin,('call',target,[self.literal(x,origin,depth+1) for x in fields]),introduced=True)
        if type(value) is SetValue:
            if value.owner is not None:self.fail('syntax_literal 不能捕获运行时协议 Set',origin)
            items = [self.literal(x, origin, depth + 1) for x in value]
            if not items:
                empty = self.node(origin, ('list', []), introduced=True)
                items = [self.node(origin, ('spread', empty), introduced=True)]
            return self.node(origin, ('set', items), introduced=True)
        if type(value) is TupleValue:
            return self.node(origin, ('tuple', [self.literal(x, origin, depth + 1) for x in value]), introduced=True)
        if isinstance(value, list):
            return self.node(origin, ('list', [self.literal(x, origin, depth + 1) for x in value]), introduced=True)
        if isinstance(value, dict):
            if not all(isinstance(k, str) for k in value): self.fail('Syntax Record 字段必须是字符串', origin)
            return self.node(origin, ('record', [(k, self.literal(v, origin, depth + 1)) for k, v in value.items()]), introduced=True)
        if value is None or type(value) in (str, int, float, bool):
            self.value_budget(value)
            return self.node(origin, ('literal', value), introduced=True)
        self.fail('syntax_literal 只接收纯数据，不能插入函数或 Syntax', origin)

    def equal(self, left, right):
        pending = [(left, right)]
        while pending:
            self.tick()
            a, b = pending.pop()
            if isinstance(a, (Syntax, _Function, _Builtin)) or isinstance(b, (Syntax, _Function, _Builtin)):
                self.fail('宏语法或函数对象不能直接比较')
            if type(a) is not type(b): return False
            if type(a) is SetValue:
                if not set_equal(a, b, tick=self.tick): return False
            elif isinstance(a, (list, TupleValue)):
                if len(a) != len(b): return False
                pending.extend(zip(a, b))
            elif isinstance(a, dict):
                if a.keys() != b.keys(): return False
                pending.extend((a[key], b[key]) for key in a)
            elif type(a) is Condition:
                if a.kind!=b.kind or a.message!=b.message:return False
                pending.append((a.data,b.data))
            elif type(a) in (Fraction,Complex,Decimal):
                if not numeric_equal(a,b): return False
            elif a != b: return False
        return True

    def result_helper(self,name,args,kwargs,node,depth):
        modes={'values_list': [('items','Any','positional',MISSING)],
               'multiple_value_list':[('producer','Any','positional',MISSING)],
               'nth_value':[('index','Any','positional',MISSING),('producer','Any','positional',MISSING)],
               'multiple_value_call':[('function','Any','positional',MISSING),('producers','Any','varargs',MISSING)],
               'multiple_value_bind':[('count','Any','positional',MISSING),('producer','Any','positional',MISSING),('consumer','Any','positional',MISSING)],
               'multiple_value_prog1':[('first','Any','positional',MISSING),('afters','Any','varargs',MISSING)]}
        if name=='return_values':
            if kwargs:self.fail('return_values accepts positional values',node)
            return self.value_budget(Completion(self,tuple(args)))
        bound=Evaluator.bind_arguments(name,modes[name],args,kwargs)
        if name=='values_list':return self.value_budget(Completion(self,tuple(check_type(bound['items'],'List'))))
        producers=bound.get('producers',[bound.get('producer',bound.get('first'))])
        afters=bound.get('afters',[])
        for function in producers+afters+[bound[key] for key in ('function','consumer') if key in bound]:
            if type(function) not in (_Function,_Builtin):self.fail('Multiple-value callback must be a pure Function',node)
        if name in ('nth_value','multiple_value_bind'):
            count=result_index(bound['index' if name=='nth_value' else 'count'],'index' if name=='nth_value' else 'count')
            if name=='multiple_value_bind' and count>10000:self.limit('Macro result bindings exceed 10000')
        if name=='multiple_value_call':
            values=[]
            for function in producers:
                values.extend(components(self.pure_invoke(function,[],{},node,depth+1),self))
                if len(values)>10000:self.limit('Macro multiple-value arguments exceed 10000')
            return self.pure_invoke(bound['function'],values,{},node,depth+1)
        result=self.pure_invoke(producers[0],[],{},node,depth+1)
        values=components(result,self)
        if name=='multiple_value_list':return self.value_budget(list(values))
        if name=='nth_value':return values[count] if count<len(values) else None
        if name=='multiple_value_bind':
            supplied=list(values[:count]);supplied.extend([None]*(count-len(supplied)))
            return self.pure_invoke(bound['consumer'],supplied,{},node,depth+1)
        for function in afters:self.pure_invoke(function,[],{},node,depth+1)
        return result

    def helper(self, name, args, kwargs, node, depth=0):
        if name in ('return_values','values_list','multiple_value_list','nth_value','multiple_value_call','multiple_value_bind','multiple_value_prog1'):
            return self.result_helper(name,args,kwargs,node,depth)
        if name in ('condition','condition_info'):
            from .condition_values import make,info
            params=[('kind','String','positional',MISSING),('message','String','positional',''),('data','Any','positional',None)] if name=='condition' else [('value','Condition','positional',MISSING)]
            values=Evaluator.bind_arguments(name,params,args,kwargs)
            result=make(values['kind'],values['message'],values['data'],self.tick) if name=='condition' else info(values['value'],self.tick)
            return self.value_budget(result)
        if name in ('decimal','decimal_text','decimal_quantize'):
            from .decimal_values import make,text,quantize
            params=[('value','Any','positional',0)] if name=='decimal' else [('value','Decimal','positional',MISSING)]
            if name=='decimal_quantize':params.append(('quantum','Decimal','positional',MISSING))
            values=Evaluator.bind_arguments(name,params,args,kwargs)
            result=make(values['value'],self.tick) if name=='decimal' else text(values['value']) if name=='decimal_text' else quantize(values['value'],values['quantum'],self.tick)
            return self.value_budget(result)
        if name in ('fraction','complex','conjugate','numeric_encode','numeric_decode'):
            from .numeric_values import make_fraction,conjugate,encode,decode
            if name in ('fraction','complex'):
                names=('numerator','denominator') if name=='fraction' else ('real','imag')
                defaults=(0,1) if name=='fraction' else (0,0)
                params=[(key,'Int' if name=='fraction' else 'Real','positional',default) for key,default in zip(names,defaults)]
                values=Evaluator.bind_arguments(name,params,args,kwargs)
                result=make_fraction(*(values[x] for x in names),tick=self.tick) if name=='fraction' else Complex(*(values[x] for x in names))
            else:
                params=[('value','Record' if name=='numeric_decode' else 'Numeric','positional',MISSING)]
                value=Evaluator.bind_arguments(name,params,args,kwargs)['value']
                result=conjugate(value,self.tick) if name=='conjugate' else encode(value) if name=='numeric_encode' else decode(value,self.tick)
            return self.value_budget(result)
        if name == 'set_of':
            if len(args) > 1 or set(kwargs) - {'items'} or args and kwargs:
                self.fail('set_of 接受一个 items 参数', node)
            value = args[0] if args else kwargs.get('items', [])
            if type(value) not in (list, str, TupleValue, SetValue):
                self.fail('宏 set_of 只接受纯 List、String、Tuple 或 Set', node)
            result = SetBuilder(tick=self.tick)
            for item in value: result.add(item)
            return result.finish()
        if name in ('set_add', 'set_discard', 'set_is_disjoint'):
            names = ('left', 'right') if name == 'set_is_disjoint' else ('value', 'item')
            params = [(key, 'Any', 'positional', MISSING) for key in names]
            values = Evaluator.bind_arguments(name, params, args, kwargs)
            operation = {'set_add': set_add, 'set_discard': set_discard,
                         'set_is_disjoint': set_is_disjoint}[name]
            return operation(*(values[key] for key in names), tick=self.tick)
        if name == 'tuple':
            if len(args) > 1 or set(kwargs) - {'items'} or args and kwargs:
                self.fail('tuple 接受一个 items 参数', node)
            value = args[0] if args else kwargs.get('items', [])
            if type(value) not in (list, str, TupleValue, SetValue):
                self.fail('宏 tuple 只接受纯 List、String、Tuple 或 Set', node)
            if len(value) > 10000: self.limit('宏元组超过 10000 项限制')
            return self.value_budget(TupleValue(tuple(value)))
        if kwargs or len(args) != 1: self.fail(name+' 需要一个位置参数', node)
        value = args[0]
        if name == 'syntax_literal': return Syntax(self.literal(value, node))
        if not isinstance(value, Syntax): self.fail(name+' 需要 Syntax 参数', node)
        if name == 'syntax_kind': return value.tree[0]
        if value.tree[0] != 'literal': self.fail('syntax_value 只接受字面量语法', node)
        return value.tree[1]

    def pure(self,node,env,depth=0,*,all_results=False):
        result=self._pure(node,env,depth)
        if type(result) is Completion:self.value_budget(result,depth)
        return result if all_results else primary(result,self)

    def _pure(self, node, env, depth=0):
        """Small closed evaluator: it cannot obtain a runtime Native or Agent."""
        self.tick(depth)
        kind = node[0]
        if kind == 'literal': return self.value_budget(node[1])
        if kind == 'name':
            try: return env.get(node[1])
            except LanguageError: self.fail('编译期名称不可用 '+node[1], node)
        if kind == 'block':
            scope, value = Env(env), None
            for i,statement in enumerate(node[1]): value = self.pure(statement, scope, depth + 1,all_results=i==len(node[1])-1)
            return value
        if kind == 'let':
            if node[2] not in TYPES: self.fail('宏编译期只支持内置类型契约', node)
            value = _check(self.pure(node[3], env, depth + 1), node[2])
            return env.bind(node[1], value)
        if kind == 'let_pattern':
            if node[2] not in TYPES: self.fail('宏编译期只支持内置类型契约', node)
            value = _check(self.pure(node[3], env, depth + 1), node[2])
            self.pure_bind_pattern(node[1], value, env, node)
            return value
        if kind == 'do': return self.pure(node[1], env, depth + 1,all_results=True)
        if kind == 'return': raise _Return(self.pure(node[1], env, depth + 1,all_results=True))
        if kind == 'if':
            condition = check_type(self.pure(node[1], env, depth + 1), 'Bool')
            return self.pure(node[2] if condition else node[3], env, depth + 1,all_results=True)
        if kind == 'quote': return Syntax(self.quote(node[1], env, {}, depth + 1))
        if kind == 'set':
            result = SetBuilder(tick=self.tick)
            for item in node[1]:
                if item[0] == 'spread':
                    values = self.pure(item[1], env, depth + 1)
                    if type(values) not in (list, str, TupleValue, SetValue):
                        self.fail('宏集合展开只接受纯 List、String、Tuple 或 Set', item)
                    for value in values: result.add(value)
                else:
                    if result.attempts >= result.limit: self.limit('宏集合输入超过 10000 项限制')
                    result.add(self.pure(item, env, depth + 1))
            return result.finish()
        if kind == 'list': return self.value_budget([self.pure(x, env, depth + 1) for x in node[1]])
        if kind == 'tuple': return self.value_budget(TupleValue(tuple(self.pure(x, env, depth + 1) for x in node[1])))
        if kind == 'record': return self.value_budget({k: self.pure(v, env, depth + 1) for k, v in node[1]})
        if kind in ('list_comp', 'set_comp', 'record_comp'):
            return self.pure_comprehension(node, env, depth + 1)
        if kind == 'index':
            value, key = self.pure(node[1], env, depth + 1), self.pure(node[2], env, depth + 1)
            if type(value) is Condition:
                from .condition_values import member
                return self.value_budget(member(value,key,self.tick))
            if type(value) in (Fraction,Complex,Decimal):
                from .numeric_values import member
                return self.value_budget(member(value,key))
            if not isinstance(value, (str, list, dict, TupleValue)): self.fail('宏索引需要容器', node)
            if isinstance(value, dict): check_type(key, 'String')
            elif type(key) is not int or key < 0: self.fail('宏索引需要非负整数', node)
            return value[key]
        if kind == 'subscript':
            # This helper only reads built-in values; it never performs the
            # runtime Instance/descriptor dispatch used by ordinary code.
            from .indexing import native_get, slice_key
            value = self.pure(node[1], env, depth + 1)
            def component(key):
                if key[0] == 'slice':
                    return slice_key(*(None if bound is None else self.pure(bound, env, depth + 1) for bound in key[1:]))
                return self.pure(key, env, depth + 1)
            key_node = node[2]
            key = (TupleValue(tuple(component(child) for child in key_node[1]))
                   if key_node[0] == 'tuple' else component(key_node))
            return native_get(value, key)
        if kind == 'unary':
            value = self.pure(node[2], env, depth + 1)
            op = node[1]
            if op == '!': return not check_type(value, 'Bool')
            if op in ('+','-'):
                from .numeric_values import unary
                return self.value_budget(unary('pos' if op=='+' else 'neg',check_type(value,'Numeric'),self.tick))
            if op == '~': return self.value_budget(~check_type(value, 'Int'))
            self.fail('宏编译期不支持一元运算符 '+op, node)
        if kind == 'binary':
            op, left = node[1], self.pure(node[2], env, depth + 1)
            if op in ('&&', '||'):
                check_type(left, 'Bool')
                if (op == '&&' and not left) or (op == '||' and left): return left
                return check_type(self.pure(node[3], env, depth + 1), 'Bool')
            right = self.pure(node[3], env, depth + 1)
            if op=='|>':return self.pure_invoke(right,[left],{},node,depth+1)
            if op in ('==', '!='):
                # Syntax identity/equality is deliberately not an implicit
                # source comparison API; use syntax_kind/value explicitly.
                same = self.equal(left, right)
                return same if op == '==' else not same
            if type(left) is SetValue or type(right) is SetValue:
                return self.value_budget(set_binary(op, left, right, tick=self.tick))
            if op == '+' and type(left) is type(right) and isinstance(left, (str, list, TupleValue)):
                bound = 250000 if isinstance(left, str) else 10000
                if len(left) + len(right) > bound: self.limit('宏容器连接超过限制')
                result = TupleValue(tuple(left) + tuple(right)) if type(left) is TupleValue else left + right
                return self.value_budget(result)
            from .operators import primitive,NOT_IMPLEMENTED
            value=primitive(op,left,right,self.tick)
            if value is NOT_IMPLEMENTED: self.fail('宏操作数不支持运算 '+op,node)
            return self.value_budget(value)
        if kind == 'fn':
            from .parameters import validate_parameters
            validate_parameters(node[1], tick=self.tick)
            params = []
            for param in node[1]:
                name, typ = param[:2]
                if typ not in TYPES: self.fail('宏编译期只支持内置类型契约', node)
                mode, default = param[2:4] if len(param) >= 4 else ('positional', None)
                default = MISSING if default is None else _check(self.pure(default, env, depth + 1), typ)
                params.append((name, typ, mode, default, param[4]) if len(param) == 5 else
                              (name, typ, mode, default))
            if node[2] not in TYPES: self.fail('宏编译期只支持内置返回契约', node)
            return _Function(params, node[2], node[3], env.snapshot(), node[4])
        if kind == 'def':
            value = self.pure(node[2], env, depth + 1)
            value.env.bind(node[1], value)
            return env.bind(node[1], value)
        if kind == 'call':
            target = self.pure(node[1], env, depth + 1)
            args, kwargs = [], {}
            for argument in node[2]:
                mode = argument[0]
                if mode in ('keyword', 'kwspread'):
                    values = ({argument[1]: self.pure(argument[2], env, depth + 1)} if mode == 'keyword'
                              else self.pure(argument[1], env, depth + 1))
                    if not isinstance(values, dict) or not all(isinstance(k, str) for k in values):
                        self.fail('宏 ** 参数需要 Record', argument)
                    if kwargs.keys() & values.keys(): self.fail('宏函数参数重复', argument)
                    kwargs.update(values)
                elif mode == 'spread':
                    values = self.pure(argument[1], env, depth + 1)
                    if type(values) not in (list, TupleValue, SetValue): self.fail('宏 * 参数需要 List、Tuple 或 Set', argument)
                    args.extend(values)
                else: args.append(self.pure(argument, env, depth + 1))
                if len(args) + len(kwargs) > 10000: self.limit('宏函数参数超过限制')
            return self.pure_invoke(target,args,kwargs,node,depth+1)
        self.fail('宏编译期不支持 '+kind+'；运行期效果必须放在 quote 中', node)


    def pure_invoke(self,target,args,kwargs,node,depth):
        self.tick(depth)
        if isinstance(target, _Builtin): return self.helper(target.name, args, kwargs, node,depth)
        if not isinstance(target, _Function): self.fail('编译期只能调用纯宏函数或语法构造器', node)
        scope = Env(target.env)
        from .patterns import bind_parameter_patterns
        untyped = [(p[0], 'Any', p[2], p[3]) for p in target.params]
        bound = Evaluator.bind_arguments(target.name, untyped, args, kwargs)
        for param in target.params: bound[param[0]] = _check(bound[param[0]], param[1])
        def resolve_tag(name):
            self.fail('宏编译期不支持 data 构造器模式 '+name, node)
        try:
            bound = bind_parameter_patterns(target.params, bound, resolve_tag=resolve_tag,
                                            project_class=lambda *args: self.fail('宏编译期不支持运行时类字段模式', node),
                                            equal=self.equal, tick=self.tick)
        except LanguageError as error:
            if error.code != 'match_error': raise
            self.fail('宏编译期参数解构模式不匹配', node)
        for name, value in bound.items(): scope.bind(name, value)
        try: result = self.pure(target.body, scope, depth + 1,all_results=True)
        except _Return as signal: result = signal.value
        _check(primary(result,self),target.returns)
        return result

    def pure_bind_pattern(self, pattern, value, env, node):
        # Closed compile-time values never dispatch runtime protocols or effects.
        from .patterns import match_pattern
        def resolve_tag(name):
            self.fail('宏编译期不支持 data 构造器模式 '+name, node)
        bound = match_pattern(pattern, value, resolve_tag=resolve_tag,
                              project_class=lambda *args: self.fail('宏编译期不支持运行时类字段模式', node),
                              equal=self.equal, tick=self.tick)
        if bound is None: self.fail('宏编译期解构模式不匹配', node)
        for name in bound:
            if name in env.values: self.fail('宏编译期重复绑定 '+name, node)
        env.values.update(bound)

    def pure_comprehension(self, node, env, depth):
        """Compile-time comprehensions only consume closed sequence data."""
        clauses = node[-1]
        result = ({} if node[0] == 'record_comp' else
                  SetBuilder(tick=self.tick) if node[0] == 'set_comp' else [])
        accepted = 0
        def visit(index, scope):
            nonlocal accepted
            self.tick(depth + index)
            if index == len(clauses):
                accepted += 1
                if accepted > 10000: self.limit('宏推导结果超过 10000 项限制')
                if node[0] == 'record_comp':
                    key = check_type(self.pure(node[1], scope, depth + 1), 'String')
                    result[key] = self.pure(node[2], scope, depth + 1)
                elif node[0] == 'set_comp': result.add(self.pure(node[1], scope, depth + 1))
                else: result.append(self.pure(node[1], scope, depth + 1))
                return
            clause = clauses[index]
            if clause[0] == 'comp_if':
                if check_type(self.pure(clause[1], scope, depth + 1), 'Bool'):
                    visit(index + 1, scope)
                return
            if clause[0] not in ('comp_for', 'comp_for_pattern'): self.fail('宏编译期推导不支持异步迭代', clause)
            items = self.pure(clause[2], scope, depth + 1)
            if type(items) not in (list, str, TupleValue, SetValue): self.fail('宏编译期推导来源需要 List、Tuple、Set 或 String', clause)
            if len(items) > 10000: self.limit('宏推导来源超过 10000 项限制')
            for item in items:
                self.tick(depth + index)
                local = Env(scope)
                if clause[0] == 'comp_for_pattern': self.pure_bind_pattern(clause[1], item, local, clause)
                else: local.bind(clause[1], item)
                visit(index + 1, local)
        visit(0, env)
        return self.value_budget(result.finish() if node[0] == 'set_comp' else result)

    def contract(self, name, node, scope=None):
        if name in TYPES: return name
        parts = name.split('.')
        if scope is not None and parts[0] in scope:
            return '.'.join([scope[parts[0]]] + parts[1:])
        self.fail('quote 类型契约必须是内置类型或模板内显式绑定: '+name, node)

    def quote(self, node, env, scope, depth=0):
        """Instantiate one closed template with fresh lexical binding identities."""
        self.tick(depth)
        kind = node[0]
        def visit(value, bindings=scope): return self.quote(value, env, bindings, depth + 1)
        def made(values): return self.node(node, values, introduced=True)
        if kind == 'unquote':
            value = self.pure(node[1], env, depth + 1)
            if not isinstance(value, Syntax): self.fail('unquote 需要 Syntax；值请用 syntax_literal', node)
            return self.clone(value.tree, depth + 1)
        if kind == 'name':
            if node[1] not in scope:
                self.fail('quote 自由名称必须通过 unquote 显式传入: '+node[1], node)
            return made(('name', scope[node[1]]))
        if kind == 'block':
            local, statements, declared = dict(scope), [], set()
            for statement in node[1]:
                declaration = statement[2] if statement[0] == 'decorated' else statement
                tag = declaration[0]
                if statement[0] == 'decorated' and tag not in ('def', 'class', 'agent'):
                    self.fail('quote 装饰器只能用于函数、类或智能体声明', statement)
                if tag in ('let', 'var', 'def', 'agent', 'class'):
                    name = declaration[1]
                    if name in declared: self.fail('quote 重复绑定 '+name, statement)
                    declared.add(name)
                    # Decorators see the old scope, not the new declaration.
                    decorators = ([self.quote(value, env, local, depth + 1) for value in statement[1]]
                                  if statement[0] == 'decorated' else None)
                    fresh = self.fresh(name)
                    if tag in ('let', 'var'):
                        item = self.node(declaration, (tag, fresh, self.contract(declaration[2], declaration, local),
                                                    self.quote(declaration[3], env, local, depth + 1)), introduced=True)
                    elif tag == 'class':
                        item = self.class_template(declaration, env, local, fresh, depth + 1)
                    elif tag == 'agent':
                        item = self.node(declaration, (tag, fresh, self.quote(declaration[2], env, local, depth + 1)), introduced=True)
                    else:
                        recursive = dict(local); recursive[name] = fresh
                        item = self.node(declaration, ('def', fresh, self.function_template(declaration[2], env, local, recursive, depth + 1)), introduced=True)
                    if decorators is not None:
                        item = self.node(statement, ('decorated', decorators, item), introduced=True)
                    local[name] = fresh
                    statements.append(item)
                elif tag in ('let_pattern', 'var_pattern'):
                    from .patterns import pattern_names
                    names = pattern_names(declaration[1])
                    for name in names:
                        if name in declared: self.fail('quote 重复绑定 '+name, statement)
                    declared.update(names)
                    # The RHS, contract and every constructor see the same old
                    # scope; no freshly introduced leaf can capture a sibling.
                    contract = self.contract(declaration[2], declaration, local)
                    value = self.quote(declaration[3], env, local, depth + 1)
                    outer = dict(local)
                    pattern = self.pattern(declaration[1], local, depth + 1, outer)
                    statements.append(self.node(declaration, (tag, pattern, contract, value), introduced=True))
                elif tag == 'rec':
                    recursive = dict(local)
                    for definition in statement[1]:
                        name = definition[1]
                        if name in declared: self.fail('quote 重复绑定 '+name, definition)
                        declared.add(name); recursive[name] = self.fresh(name)
                    definitions = [self.node(definition, ('def', recursive[definition[1]],
                                   self.function_template(definition[2], env, local, recursive, depth + 1)), introduced=True)
                                   for definition in statement[1]]
                    statements.append(self.node(statement, ('rec', definitions), introduced=True))
                    local = recursive
                else: statements.append(self.quote(statement, env, local, depth + 1))
            return made(('block', statements))
        if kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
            return self.function_template(node, env, scope, scope, depth + 1)
        if kind in ('set_member', 'set_item'):
            return made((kind, visit(node[1]), visit(node[2]), visit(node[3])))
        if kind == 'aug_assign':
            # A bare name follows the same closed-template binding rule as
            # assignment; unquote explicitly retains a caller's target identity.
            return made((kind, visit(node[1]), node[2], visit(node[3])))
        if kind == 'slice':
            return made((kind, *(None if child is None else visit(child) for child in node[1:])))
        if kind == 'assign':
            if node[1] not in scope: self.fail('quote 赋值目标必须是模板内绑定', node)
            return made(('assign', scope[node[1]], visit(node[2])))
        if kind == 'assign_pattern':
            # Writes retain existing identities; declaring fresh leaves here
            # would silently shadow the actual state being updated.
            target = self.pattern(node[1], dict(scope), depth + 1, scope, declare=False)
            return made((kind, target, visit(node[2])))
        if kind in ('list_comp', 'set_comp', 'record_comp', 'gen_comp', 'async_gen_comp'):
            # Execution order matters: each source precedes its own binding,
            # whereas the head sees every binding although it is written first.
            local, clauses = dict(scope), []
            for clause in node[-1]:
                if clause[0] == 'comp_if':
                    values = ('comp_if', visit(clause[1], local))
                else:
                    source = visit(clause[2], local)
                    if clause[0] in ('comp_for_pattern', 'comp_async_for_pattern'):
                        target = self.pattern(clause[1], local, depth + 1, dict(local))
                    else:
                        target = self.fresh(clause[1]); local[clause[1]] = target
                    values = (clause[0], target, source)
                clauses.append(self.node(clause, values, introduced=True))
            if kind == 'record_comp':
                return made((kind, visit(node[1], local), visit(node[2], local), clauses))
            return made((kind, visit(node[1], local), clauses))
        if kind in ('for_pattern', 'async_for_pattern', 'with_pattern', 'async_with_pattern'):
            local = dict(scope)
            pattern = self.pattern(node[1], local, depth + 1, scope)
            return made((kind, pattern, visit(node[2]), visit(node[3], local)))
        if kind in ('for', 'async_for', 'with', 'async_with'):
            local = dict(scope); local[node[1]] = self.fresh(node[1])
            return made((kind, local[node[1]], visit(node[2]), visit(node[3], local)))
        if kind == 'try':
            local = dict(scope); local[node[2]] = self.fresh(node[2])
            return made(('try', visit(node[1]), local[node[2]], visit(node[3], local)))
        if kind == 'try_group':
            cases = []
            for case in node[2]:
                guard_scope = dict(scope)
                pattern = self.pattern(case[1], guard_scope, depth + 1, scope)
                handler_scope = dict(scope)
                handler_scope[case[3]] = self.fresh(case[3])
                values = ('group_case', pattern, None if case[2] is None else visit(case[2], guard_scope),
                          handler_scope[case[3]], visit(case[4], handler_scope))
                cases.append(self.node(case, values, introduced=True))
            return made((kind, visit(node[1]), cases))
        if kind in ('match', 'try_cases'):
            arms = []
            for pattern, guard, body in node[2]:
                local = dict(scope)
                pattern = self.pattern(pattern, local, depth + 1)
                arms.append((pattern, None if guard is None else visit(guard, local), visit(body, local)))
            return made((kind, visit(node[1]), arms))
        if kind in ('macro', 'quote', 'import', 'export', 'data'):
            self.fail('quote 不能生成 '+kind+' 声明或编译期语法', node)
        # Tuple metadata (record keys, keyword labels) is kept literally;
        # only known expression positions are visited.
        if kind == 'literal': return made(node)
        if kind in ('break', 'continue', 'rethrow'): return made(node)
        if kind in ('list', 'tuple', 'set'): return made((kind, [visit(x) for x in node[1]]))
        if kind == 'record': return made(('record', [(k, visit(v)) for k, v in node[1]]))
        if kind == 'call': return made(('call', visit(node[1]), [visit(x) for x in node[2]]))
        if kind == 'macrocall': return made(('macrocall', node[1], [visit(x) for x in node[2]]))
        if kind == 'keyword': return made(('keyword', node[1], visit(node[2])))
        if kind in ('spread', 'kwspread', 'do', 'throw', 'return', 'await', 'yield', 'yield_from'):
            return made((kind, visit(node[1])))
        if kind == 'unary': return made(('unary', node[1], visit(node[2])))
        if kind == 'binary': return made(('binary', node[1], visit(node[2]), visit(node[3])))
        if kind in ('index', 'subscript', 'while', 'finally', 'loop_else', 'try_else'): return made((kind, visit(node[1]), visit(node[2])))
        if kind == 'if': return made(('if', visit(node[1]), visit(node[2]), visit(node[3])))
        self.fail('quote 暂不支持语法 '+kind, node)

    def class_template(self, node, env, outer, fresh, depth):
        """Public member labels are not lexical bindings of the method body."""
        self.tick(depth)
        bases = [self.quote(base, env, outer, depth + 1) for base in node[2]]
        from .class_creation import class_options
        options=[self.quote(option,env,outer,depth+1) for option in class_options(node)]
        lexical = dict(outer); lexical[node[1]] = fresh
        declared, members, defaults = set(), [], dict(lexical)
        for member in node[3][1]:
            self.tick(depth)
            declaration = member[2] if member[0] == 'decorated' else member
            kind, label = declaration[:2]
            if kind not in ('let', 'var', 'def') or member[0] == 'decorated' and kind != 'def':
                self.fail('quote 类体只允许 let、var 和方法声明', member)
            # Whether a repeated decorated label is a legitimate immutable
            # Property update is a runtime identity/lineage check, never a
            # spelling heuristic in the compiler.
            if label in declared and member[0] != 'decorated': self.fail('quote 类成员重复 '+label, member)
            declared.add(label)
            decorators = ([self.quote(value, env, defaults, depth + 1) for value in member[1]]
                          if member[0] == 'decorated' else None)
            if kind in ('let', 'var'):
                value = (kind, label, self.contract(declaration[2], declaration, defaults),
                         self.quote(declaration[3], env, defaults, depth + 1))
            else:
                value = ('def', label, self.function_template(declaration[2], env, defaults, lexical, depth + 1))
            item = self.node(declaration, value, introduced=True)
            if decorators is not None: item = self.node(member, ('decorated', decorators, item), introduced=True)
            members.append(item)
            # Defaults can see earlier public members; methods instead retain
            # the lexical outer environment plus their own class binding.
            defaults[label] = label
        body = self.node(node[3], ('block', members), introduced=True)
        values=('class',fresh,bases,body)
        return self.node(node,values+(options,) if options else values,introduced=True)

    def function_template(self, node, env, outer, scope, depth):
        self.tick(depth)
        local, constructors, aliases, params = dict(scope), dict(scope), {}, []
        for param in node[1]:
            name, typ = param[:2]
            typ = self.contract(typ, node, outer)
            aliases[name] = self.fresh(name)
            local[name] = aliases[name]
            if len(param) >= 4:
                default = None if param[3] is None else self.quote(param[3], env, outer, depth + 1)
                if len(param) == 5:
                    pattern = self.pattern(param[4], local, depth + 1, constructors)
                    params.append((name, typ, param[2], default, pattern))
                else: params.append((name, typ, param[2], default))
            else: params.append((name, typ))
        result = self.node(node, (node[0], params, self.contract(node[2], node, outer),
                                 self.quote(node[3], env, local, depth + 1), node[4]), introduced=True)
        # Slot labels remain stable, including private positional-only slots;
        # only lexical identities change. Binding decides which labels accept
        # keywords before aliases are installed in the function frame.
        result.macro_bindings = aliases
        return result

    def pattern(self, node, scope, depth, constructors=None, *, declare=True):
        self.tick(depth)
        kind = node[0]
        constructors = dict(scope) if constructors is None else constructors
        if kind == 'p_bind':
            if declare:
                scope[node[1]] = self.fresh(node[1])
            elif node[1] not in scope:
                self.fail('quote 解构赋值目标必须是模板内绑定: '+node[1], node)
            values = ('p_bind', scope[node[1]])
        elif kind in ('p_literal', 'p_any'): values = node
        elif kind in ('p_list', 'p_tuple'):
            values = (kind, [self.pattern(x, scope, depth + 1, constructors, declare=declare) for x in node[1]],
                      None if node[2] is None else self.pattern(node[2], scope, depth + 1, constructors, declare=declare))
        elif kind == 'p_record':
            values = ('p_record', [(key, self.pattern(x, scope, depth + 1, constructors, declare=declare)) for key, x in node[1]],
                      None if node[2] is None else self.pattern(node[2], scope, depth + 1, constructors, declare=declare))
        elif kind == 'p_class':
            parts = node[1].split('.')
            if parts[0] not in constructors:
                self.fail('quote 类字段模式必须通过模板内绑定显式传入: '+node[1], node)
            name = '.'.join([constructors[parts[0]]] + parts[1:])
            values = ('p_class', name,
                      [(key, self.pattern(x, scope, depth + 1, constructors, declare=declare)) for key, x in node[2]],
                      None if node[3] is None else self.pattern(node[3], scope, depth + 1, constructors, declare=declare))
        elif kind == 'p_tag':
            parts = node[1].split('.')
            if parts[0] not in constructors:
                self.fail('quote 构造器必须通过模板内绑定显式传入: '+node[1], node)
            name = '.'.join([constructors[parts[0]]] + parts[1:])
            values = ('p_tag', name, [self.pattern(x, scope, depth + 1, constructors, declare=declare) for x in node[2]])
        else: self.fail('quote 未知模式 '+kind, node)
        return self.node(node, values, introduced=True)


def expand_trees(trees, **limits):
    """Expand linked AST macros with one shared compile-time resource budget."""
    try: return Expander(trees, **limits).expand()
    except RecursionError: raise ResourceLimit('宏编译语法嵌套超过宿主限制') from None
