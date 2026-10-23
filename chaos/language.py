"""Chaos functional core: a parsed language, never Python eval/exec."""
import json
import inspect
import ast
import copy
import math
import re
import time
from dataclasses import dataclass, field
from .tuple_values import TupleValue
from .decimal_values import DecimalContext
from .condition_values import Condition
from .error_values import Error
from .hash_maps import HashMap
from .contracts import Contract
from .eql_specializers import EqlSpecializer
from .control_prompts import PromptTag,PromptScope,EscapeContinuation
from .native_code_cache import DEFAULT_NATIVE_CODE_CACHE, NativeCodeCache
from .continuations import Continuation
from .robot_values import Robot,RobotObservation,RobotCommand,RobotReceipt,VALUES as ROBOT_VALUES

from .numeric_values import Fraction, Complex, Decimal, is_real, is_numeric, encode as numeric_encode, decode as numeric_decode
from .set_values import SetValue, SetBuilder, set_equal, set_add, set_discard, set_is_disjoint
from .set_runtime import Request as SetRequest,resolve as set_resolve
from .comprehensions import comprehension_scopes
from .operators import PRECEDENCE, UNARY_PRECEDENCE, AWAIT_PRECEDENCE, AUGMENTED


class LanguageError(Exception):
    def __init__(self, message, line=None, column=None, file=None, code='runtime_error', value=None):
        self.message, self.line, self.column = message, line, column
        self.file, self.code, self.value = file, code, value
        super().__init__(message)

    def __str__(self):
        prefix = (self.file + ':' if self.file else '') + (f'{self.line}:{self.column} ' if self.line else '')
        return prefix + self.message

    def attach(self, span):
        if self.line is None:
            self.file, self.line, self.column = span.get('file'), span.get('line'), span.get('column')
        return self

    def record(self):
        return {'code': self.code, 'message': self.message, 'value': self.value,
                'location': {'file': self.file, 'line': self.line, 'column': self.column}}


class ResourceLimit(LanguageError):
    """Uncatchable control failure; catches must never defeat execution limits."""
    def __init__(self, message, *args, **kwargs):
        super().__init__(message, *args, code='resource_limit', **kwargs)


class ExecutionControl(Exception):
    """Host cancellation, propagated through every language error handler."""
    pass


class Raised(LanguageError):
    def __init__(self, value, *, message='程序抛出错误', code='user_throw', **location):
        super().__init__(message, code=code, value=value, **location)


@dataclass
class Token:
    value: str
    kind: str
    line: int
    column: int
    start: int = 0
    end: int = 0


def tokenize(source, *, trivia=False, tolerant=False, checkpoint=None):
    if len(source) > 100000:
        raise ResourceLimit("程序超过 100 KB 限制")
    pattern = re.compile(r'(?P<space>\s+)|(?P<comment>//[^\n]*|\#[^\n]*)|(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|(?P<id>[^\W\d]\w*)|(?P<op>\*\*=|<<=|>>=|[+*/%&|^-]=|\*\*|<<|>>|\.\.|\|>|=>|->|==|!=|<=|>=|&&|\|\||[+*/%<>=!?:,;.@(){}\[\]&|^~-])', re.UNICODE)
    tokens, offset, line, column = [], 0, 1, 1
    while offset < len(source):
        if checkpoint: checkpoint()
        match = pattern.match(source, offset)
        if not match:
            if not tolerant:
                raise LanguageError(f"无法识别字符 {source[offset]!r}", line, column)
            # Editor recovery is lexical only; strict parsing never uses it.
            end = offset + 1
            if source[offset] in ('"', "'"):
                end = source.find('\n', offset)
                if end < 0: end = len(source)
            raw, kind = source[offset:end], 'invalid'
        else:
            raw, kind = match.group(), match.lastgroup
        if trivia or kind not in ('space', 'comment'):
            tokens.append(Token(raw, kind, line, column, offset, offset + len(raw)))
        if '\n' in raw:
            line += raw.count('\n')
            column = len(raw.rsplit('\n', 1)[1]) + 1
        else:
            column += len(raw)
        offset += len(raw)
    tokens.append(Token('<eof>', 'eof', line, column, offset, offset))
    return tokens


class Node(tuple):
    """Tuple-compatible syntax node with a source span (excluded from semantics)."""
    def __new__(cls, values, span=None):
        obj = super().__new__(cls, values)
        obj.span = span or {}
        return obj


def located(method):
    def parse(self, *args, **kwargs):
        start = self.t
        try:
            result = method(self, *args, **kwargs)
        except LanguageError:
            raise
        except (ValueError, SyntaxError, OverflowError) as error:
            raise LanguageError(str(error), start.line, start.column, self.filename, 'syntax_error') from None
        end = self.tokens[max(0, self.i - 1)]
        return Node(result, {'file': self.filename, 'line': start.line, 'column': start.column,
                             'endLine': end.line, 'endColumn': end.column + len(end.value)})
    return parse


class Parser:
    precedence = PRECEDENCE
    reserved = {'let', 'fn', 'agent', 'if', 'else', 'true', 'false', 'null', 'rec', 'data', 'match', 'try', 'catch', 'throw', 'do', 'import', 'as', 'export', 'var', 'while', 'for', 'in', 'break', 'continue', 'return'}

    def __init__(self, source, filename='main.chaos', *, ranges=False, legacy_items=False, checkpoint=None):
        self.ranges, self.legacy_items = ranges, legacy_items
        self.checkpoint = checkpoint
        self.filename, self.block_depth = filename, 0
        self.function_depth, self.loop_depth = 0, 0
        self.flavor, self.quote_depth = None, 0
        self.await_forbidden = False
        try:
            self.tokens, self.i = tokenize(source, checkpoint=checkpoint), 0
        except LanguageError as e:
            e.file = filename
            raise

    @property
    def t(self):
        return self.tokens[self.i]

    def pop(self):
        if self.checkpoint: self.checkpoint()
        t = self.t
        if t.kind != 'eof': self.i += 1
        return t

    def at(self, value):
        return self.t.value == value

    def accept(self, value):
        if self.at(value):
            self.pop()
            return True
        return False

    def expect(self, value):
        if not self.accept(value): self.fail(f"需要 {value!r}，实际为 {self.t.value!r}")

    def fail(self, message):
        raise LanguageError(message, self.t.line, self.t.column, self.filename, 'syntax_error')

    def identifier(self):
        if self.t.kind != 'id' or self.t.value in self.reserved: self.fail('需要非保留标识符')
        return self.pop().value

    def type_name(self):
        name = self.identifier()
        while self.accept('.'): name += '.' + self.identifier()
        return name

    def context_as_ahead(self, start):
        """Cheap contextual-keyword filter; bracketed legacy calls stay linear."""
        cached = getattr(self, '_context_tokens', None)
        if cached is None or cached[0] is not self.tokens:
            pairs, stack = {}, []
            for index, token in enumerate(self.tokens):
                if token.kind != 'op': continue
                if token.value in ('(', '[', '{'): stack.append((index, token.value))
                elif token.value in (')', ']', '}'):
                    if stack and stack[-1][1] == {')':'(', ']':'[', '}':'{'}[token.value]:
                        opening, _ = stack.pop(); pairs[opening] = index
                    else: stack.clear()
            cached = self._context_tokens = (self.tokens, pairs, {})
        _, pairs, answers = cached
        index, visited, result = start, [], False
        while index < len(self.tokens):
            if index in answers:
                result = answers[index]; break
            token = self.tokens[index]; value = token.value
            visited.append(index)
            if token.kind == 'eof': break
            if token.kind == 'id' and value == 'as':
                result = True; break
            if token.kind == 'id' and value in ('let', 'var', 'import', 'export', 'agent', 'rec', 'data'): break
            if token.kind == 'op' and value in (';', ',', '=', ')', ']', '}'): break
            if token.kind == 'op' and value in ('(', '[', '{'):
                if index not in pairs: break
                index = pairs[index]
            index += 1
        for index in visited: answers[index] = result
        return result

    def context_header(self):
        """Keep the parsed manager on success; restore every context on fallback."""
        asynchronous = self.at('async') and self.tokens[self.i + 1].value == 'with'
        if not self.at('with') and not asynchronous: return None
        start = self.i + (2 if asynchronous else 1)
        if not self.context_as_ahead(start): return None
        attributes = ('i', 'function_depth', 'loop_depth', 'block_depth', 'flavor', 'quote_depth', 'await_forbidden')
        previous = tuple(getattr(self, name) for name in attributes)
        committed = False
        try:
            self.i = start
            manager = self.expr()
            if not self.accept('as'): return None
            target = self.resource_target()
            if self.t.value not in ('{', ','): return None
            committed = True
            return asynchronous, manager, target
        except ResourceLimit:
            raise
        except LanguageError:
            return None
        finally:
            # A successful probe leaves the already parsed expression in place.
            if not committed:
                for name, value in zip(attributes, previous): setattr(self, name, value)

    @located
    def program(self, end='<eof>'):
        statements = []
        while not self.at(end):
            if self.t.kind == 'eof': self.fail(f'缺少 {end}')
            if self.accept(';'): continue
            statements.append(self.statement())
            self.accept(';')
        return ('block', statements)

    @located
    def block(self):
        self.expect('{')
        self.block_depth += 1
        try: result = self.program('}')
        finally: self.block_depth -= 1
        self.expect('}')
        return result

    def class_declaration_ahead(self):
        if not self.at('class') or self.tokens[self.i + 1].kind != 'id': return False
        start = self.i + 2
        if self.tokens[start].value == '{': return True
        if self.tokens[start].value != '(': return False
        depth = 0
        for index, token in enumerate(self.tokens[start:], start):
            if token.value == '(': depth += 1
            elif token.value == ')':
                depth -= 1
                if depth == 0:
                    return self.tokens[index + 1].value == '{'
        return False

    @located
    def statement(self):
        if self.at('@'):
            decorators = []
            while self.accept('@'):
                marker = self.tokens[self.i-1]
                if self.t.line != marker.line: self.fail('装饰器表达式必须从 @ 所在行开始')
                start, end, depth, line = self.i, self.i, 0, marker.line
                while self.tokens[end].kind != 'eof':
                    token = self.tokens[end]
                    if depth == 0 and token.line > line: break
                    if token.value in ('(', '[', '{'): depth += 1
                    elif token.value in (')', ']', '}'): depth -= 1
                    if depth < 0: self.fail('装饰器括号不匹配')
                    line = token.line + token.value.count('\n')
                    end += 1
                if end == start or depth: self.fail('装饰器表达式不完整')
                boundary = self.tokens[end]
                tokens, context = self.tokens, (self.function_depth, self.loop_depth, self.flavor, self.await_forbidden)
                self.tokens = tokens[:end] + [Token('<eof>', 'eof', boundary.line, boundary.column, boundary.start, boundary.start)]
                self.await_forbidden = self.await_forbidden or bool(self.function_depth and self.flavor not in ('async_fn', 'async_gen_fn'))
                self.function_depth = self.loop_depth = 0; self.flavor = None
                try:
                    decorators.append(self.expr())
                    if self.t.kind != 'eof': self.fail('装饰器需要独立逻辑行')
                finally:
                    self.tokens = tokens
                    self.function_depth, self.loop_depth, self.flavor, self.await_forbidden = context
                self.i = end
                if len(decorators) > 64: self.fail('每个声明最多 64 个装饰器')
            declaration = self.statement()
            if declaration[0] not in ('def', 'class', 'agent'): self.fail('装饰器仅支持函数、类和智能体声明')
            return ('decorated', decorators, declaration)
        if self.class_declaration_ahead():
            self.pop(); name = self.identifier(); bases, options, keywords = [], [], set()
            if name in TYPES: self.fail('不能重定义内置类型 '+name)
            if self.accept('('):
                while not self.at(')'):
                    argument = self.argument()
                    if argument[0] in ('keyword', 'kwspread'):
                        if argument[0] == 'keyword':
                            if argument[1] in keywords: self.fail('重复类选项 '+argument[1])
                            keywords.add(argument[1])
                        options.append(argument)
                    else:
                        if options: self.fail('类基类必须在具名选项之前')
                        if argument[0] == 'spread': self.fail('类基类不支持 * 展开')
                        bases.append(argument)
                    if not self.accept(','): break
                self.expect(')')
            old_context = (self.function_depth, self.loop_depth, self.flavor, self.await_forbidden)
            self.await_forbidden = self.await_forbidden or bool(self.function_depth and self.flavor not in ('async_fn','async_gen_fn'))
            self.function_depth = self.loop_depth = 0; self.flavor = None
            try: body = self.block()
            finally: self.function_depth, self.loop_depth, self.flavor, self.await_forbidden = old_context
            if any(node[0] not in ('let','var','def') and not (node[0] == 'decorated' and node[2][0] == 'def') for node in body[1]): self.fail('类体只允许 let、var 和方法声明')
            return ('class', name, bases, body, options) if options else ('class', name, bases, body)
        if self.at('let') or self.at('var'):
            binding = self.pop().value
            name = self.binding_target()
            typ = self.type_name() if self.accept(':') else 'Any'
            self.expect('=')
            return (binding if isinstance(name, str) else binding+'_pattern', name, typ, self.expr())
        if self.t.kind == 'id' and self.tokens[self.i + 1].value == '=':
            name = self.identifier(); self.expect('=')
            return ('assign', name, self.expr())
        if self.at('macro') and self.tokens[self.i + 1].kind == 'id':
            if self.block_depth: self.fail('macro 仅允许模块顶层')
            self.pop(); name = self.identifier(); self.expect('('); params = []
            while not self.at(')'):
                param = self.identifier()
                if param in params: self.fail('宏参数不能重名')
                params.append(param)
                if not self.accept(','): break
            self.expect(')')
            return ('macro', name, params, self.block())
        if self.at('async') and self.tokens[self.i + 1].value == 'fn' and (self.tokens[self.i + 2].kind == 'id' or self.tokens[self.i + 2].value == '*' and self.tokens[self.i + 3].kind == 'id'):
            self.pop(); self.pop(); flavor = 'async_gen_fn' if self.accept('*') else 'async_fn'
            name = self.identifier()
            return ('def', name, self.function(name, flavor))
        if self.at('fn') and self.tokens[self.i + 1].value == '*' and self.tokens[self.i + 2].kind == 'id':
            self.pop(); self.pop(); name = self.identifier()
            return ('def', name, self.function(name, 'gen_fn'))
        if self.at('fn') and self.tokens[self.i + 1].kind == 'id':
            self.pop(); name = self.identifier()
            return ('def', name, self.function(name))
        if self.accept('agent'):
            name = self.identifier()
            return ('agent', name, self.record())
        if self.accept('rec'):
            self.expect('{'); definitions = []
            while not self.at('}'):
                flavor = 'async_fn' if self.accept('async') else 'fn'
                self.expect('fn')
                if self.accept('*'):
                    flavor = 'async_gen_fn' if flavor == 'async_fn' else 'gen_fn'
                name = self.identifier()
                if name in [d[1] for d in definitions]: self.fail('rec 中函数不能重名')
                definitions.append(Node(('def', name, self.function(name, flavor))))
                self.accept(';')
            self.expect('}')
            if not definitions: self.fail('rec 至少需要一个函数')
            return ('rec', definitions)
        if self.accept('data'):
            if self.block_depth: self.fail('data 类型声明仅允许模块顶层')
            name = self.identifier();
            if name in TYPES: self.fail('不能重定义内置类型 '+name)
            self.expect('{'); variants = []
            while not self.at('}'):
                tag = self.identifier(); fields = []
                if self.accept('('):
                    while not self.at(')'):
                        field = self.identifier()
                        if field in fields: self.fail('构造器参数不能重名')
                        fields.append(field)
                        if not self.accept(','): break
                    self.expect(')')
                if tag == name or tag in [v[0] for v in variants]: self.fail('类型及构造器不能重名')
                variants.append((tag, fields))
                if not self.accept(','): break
            self.expect('}')
            if not variants: self.fail('data 至少需要一个构造器')
            return ('data', name, variants)
        if self.at('import') or self.at('export'):
            if self.block_depth: self.fail('import/export 仅允许模块顶层')
            if self.accept('import'):
                if self.t.kind != 'string': self.fail('import 需要模块路径字符串')
                path = self.string(self.pop().value); self.expect('as')
                return ('import', path, self.identifier())
            self.expect('export'); self.expect('{'); names = []
            while not self.at('}'):
                name = self.identifier()
                if name in names: self.fail('重复 export 名称')
                names.append(name)
                if not self.accept(','): break
            self.expect('}')
            return ('export', names)
        target = self.assignment_pattern()
        if target is not None:
            self.expect('=')
            return ('assign_pattern', target, self.expr())
        result = self.expr()
        if self.t.value in AUGMENTED:
            operator = AUGMENTED[self.pop().value]
            if result[0] not in ('name', 'index', 'subscript') and not (self.quote_depth and result[0] == 'unquote'):
                self.fail('复合赋值目标需要可变名称、对象字段或索引')
            return ('aug_assign', result, operator, self.expr())
        if self.accept('='):
            if result[0] not in ('index','subscript'): self.fail('成员赋值需要对象字段或索引')
            return ('set_item' if result[0]=='subscript' else 'set_member', result[1], result[2], self.expr())
        return result

    @located
    def function(self, name='<lambda>', flavor='fn'):
        self.expect('('); params, names = [], set()
        keyword_only, positional_default, bare_star, positional_only = False, False, False, False
        while not self.at(')'):
            if self.accept('/'):
                if positional_only or keyword_only or not params:
                    self.fail('/ 需要非空位置参数前缀，且只能在 * 之前出现一次')
                params = [(param[0], param[1], 'positional_only', param[3] if len(param) >= 4 else None, *param[4:]) for param in params]
                positional_only = True
                if not self.accept(','):
                    if not self.at(')'): self.fail('/ 后需要逗号或参数列表结束')
                    break
                continue
            if self.accept('**'):
                mode = 'varkw'
            elif self.accept('*'):
                if keyword_only: self.fail('只能声明一个 * 参数边界')
                keyword_only = True
                if self.accept(','):
                    bare_star = True
                    if self.at(')'): self.fail('裸 * 后需要关键字参数')
                    continue
                mode = 'varargs'
            else:
                mode = 'keyword_only' if keyword_only else 'positional'
            p = self.identifier()
            if p in names: self.fail('参数或模式变量不能重名 '+p)
            names.add(p)
            pattern = self.pattern(names) if self.accept('as') else None
            typ = self.type_name() if self.accept(':') else 'Any'
            default = None
            if self.accept('='):
                if mode in ('varargs', 'varkw'): self.fail('展开参数不能带默认值')
                old_function, old_loop, old_flavor, old_await = self.function_depth, self.loop_depth, self.flavor, self.await_forbidden
                self.await_forbidden = old_await or bool(old_function and old_flavor not in ('async_fn', 'async_gen_fn'))
                self.function_depth = self.loop_depth = 0; self.flavor = None
                try: default = self.expr()
                finally: self.function_depth, self.loop_depth, self.flavor, self.await_forbidden = old_function, old_loop, old_flavor, old_await
            if mode == 'positional':
                if default is None and positional_default: self.fail('必需位置参数不能位于默认参数之后')
                positional_default = positional_default or default is not None
            if mode == 'keyword_only': bare_star = False
            params.append((p, typ, mode, default, pattern) if pattern is not None else
                          (p, typ) if mode == 'positional' and default is None else (p, typ, mode, default))
            comma = self.accept(',')
            if mode == 'varkw':
                if not self.at(')'): self.fail('** 参数必须位于最后')
                break
            if not comma: break
        if bare_star: self.fail('裸 * 后需要具名关键字参数')
        from .parameters import validate_parameters
        validate_parameters(params, tick=self.checkpoint)
        self.expect(')')
        ret = self.type_name() if self.accept('->') else 'Any'
        old_loop, old_flavor, old_await = self.loop_depth, self.flavor, self.await_forbidden; self.loop_depth = 0; self.function_depth += 1; self.flavor = flavor; self.await_forbidden = False
        try: body = self.block()
        finally: self.function_depth -= 1; self.loop_depth = old_loop; self.flavor = old_flavor; self.await_forbidden = old_await
        return (flavor, params, ret, body, name)

    @located
    def argument(self):
        if self.accept('**'): return ('kwspread', self.expr())
        if self.accept('*'): return ('spread', self.expr())
        if self.t.kind == 'id' and self.tokens[self.i + 1].value == '=':
            name = self.pop().value; self.expect('=')
            return ('keyword', name, self.expr())
        return self.expr()

    @located
    def record(self):
        self.expect('{'); pairs = []
        if self.at('}'): self.pop(); return ('record', pairs)
        # Bare field labels keep their original literal meaning for records;
        # in a comprehension the same syntax denotes a dynamic key expression.
        tok = self.t
        label = tok.kind in ('id', 'string') and self.tokens[self.i + 1].value == ':'
        if label:
            self.pop()
            key = self.string(tok.value) if tok.kind == 'string' else tok.value
            key_node = Node(('literal' if tok.kind == 'string' else 'name', key),
                            {'file':self.filename,'line':tok.line,'column':tok.column,
                             'endLine':tok.line,'endColumn':tok.column+len(tok.value)})
        else:
            key_node = self.argument() if self.at('*') else self.expr()
            if not self.at(':'):
                if self.comprehension_start():
                    if key_node[0] == 'spread': self.fail('Set 推导头不能展开')
                    clauses = self.comprehension_clauses(); self.expect('}')
                    return self.checked_comprehension(('set_comp', key_node, clauses))
                items = [key_node]
                while self.accept(','):
                    if self.at('}'): break
                    items.append(self.argument() if self.at('*') else self.expr())
                self.expect('}'); return ('set', items)
            if key_node[0] == 'spread': self.fail('Record 字段不能展开')
        self.expect(':'); value = self.expr()
        if self.comprehension_start():
            if label and tok.value in ('true', 'false', 'null'):
                key_node = Node(('literal', {'true':True,'false':False,'null':None}[tok.value]), key_node.span)
            elif label and tok.kind == 'id' and key in self.reserved:
                self.fail('推导式动态字段需要表达式；固定字段名请使用字符串')
            clauses = self.comprehension_clauses(); self.expect('}')
            return self.checked_comprehension(('record_comp', key_node, value, clauses))
        if not label: self.fail('记录字段必须是标识符或字符串')
        pairs.append((key, value))
        if not self.accept(','):
            self.expect('}'); return ('record', pairs)
        while not self.at('}'):
            tok = self.pop()
            if tok.kind not in ('id', 'string'): self.fail('记录字段必须是标识符或字符串')
            key = self.string(tok.value) if tok.kind == 'string' else tok.value
            if key in [p[0] for p in pairs]: self.fail(f'重复字段 {key}')
            self.expect(':'); pairs.append((key, self.expr()))
            if not self.accept(','): break
        self.expect('}')
        return ('record', pairs)

    def comprehension_start(self):
        return self.at('for') or self.at('async') and self.tokens[self.i + 1].value == 'for'

    def checked_comprehension(self, node, context=None):
        if not self.quote_depth:
            from .comprehensions import validate_comprehension
            depth, _, flavor, forbidden = context or (self.function_depth, self.loop_depth, self.flavor, self.await_forbidden)
            validate_comprehension(node, not (forbidden or depth and flavor not in ('async_fn', 'async_gen_fn')), self.checkpoint)
        return node

    def comprehension_clauses(self):
        clauses = []
        while self.comprehension_start() or clauses and self.at('if'):
            if len(clauses) >= 64: self.fail('推导式最多允许 64 个子句')
            clauses.append(self.comprehension_clause())
        if not clauses: self.fail('推导式至少需要一个 for 子句')
        return clauses

    @located
    def comprehension_clause(self):
        if self.accept('if'): return ('comp_if', self.expr())
        asynchronous = bool(self.accept('async')); self.expect('for')
        name = self.binding_target(); self.expect('in')
        kind = 'comp_async_for' if asynchronous else 'comp_for'
        return (kind if isinstance(name, str) else kind+'_pattern', name, self.expr())

    def binding_target(self):
        # Keep all historical simple-name ASTs, including the identifier `_`.
        # A dotted name or constructor call belongs to the existing pattern
        # grammar; no expression, assignment target or default is introduced.
        if self.t.kind == 'id' and self.t.value not in self.reserved and self.tokens[self.i + 1].value not in ('.', '(') and not (self.tokens[self.i + 1].value == ':' and self.tokens[self.i + 2].value == ':'):
            return self.identifier()
        target = self.pattern()
        from .patterns import pattern_names
        pattern_names(target, tick=self.checkpoint)
        return target

    def resource_target(self):
        target = self.binding_target()
        if not isinstance(target, str) and target[0] not in ('p_list', 'p_tuple', 'p_record', 'p_tag', 'p_class'):
            self.fail('资源目标需要名字、列表、元组、记录或构造器模式')
        return target

    def assignment_pattern(self):
        """Recognize only a complete structural target followed by assignment.

        The speculative pattern grammar has no expression effects or parser
        context changes. Ordinary list/record/call expressions remain unchanged.
        """
        if self.t.value not in ('[', '{', '(') and not (self.t.kind == 'id' and self.tokens[self.i + 1].value in ('.', '(', ':')):
            return None
        start, committed = self.i, False
        try:
            target = self.pattern()
            if not self.at('=') or target[0] not in ('p_list', 'p_tuple', 'p_record', 'p_tag', 'p_class'): return None
            from .patterns import pattern_names
            pattern_names(target, tick=self.checkpoint)
            committed = True
            return target
        except ResourceLimit:
            raise
        except LanguageError:
            return None
        finally:
            if not committed: self.i = start

    def async_comprehension_start(self):
        if not self.at('async') or self.tokens[self.i + 1].value != '(': return False
        cached = getattr(self, '_async_comprehension_tokens', None)
        if cached is None or cached[0] is not self.tokens:
            cached = self._async_comprehension_tokens = (self.tokens, {})
        # Cache only the structural decision, never a parsed tree or failure.
        # Nested speculative heads otherwise repeat exponentially. Scope and
        # editor recovery inputs must not share decisions across contexts.
        key = (self.i, self.function_depth, self.loop_depth, self.block_depth,
               self.flavor, self.quote_depth, self.await_forbidden,
               self.ranges, self.legacy_items,
               getattr(self, 'recovering_comprehension_clause', False),
               getattr(self, 'completion_cursor', None))
        if key not in cached[1]: cached[1][key] = self._async_comprehension_start()
        return cached[1][key]

    def _async_comprehension_start(self):
        # Preserve contextual identifiers such as async(1). Only a previously
        # invalid call containing a top-level comprehension clause is claimed.
        if not self.at('async') or self.tokens[self.i + 1].value != '(': return False
        depth = 0
        for index in range(self.i + 1, len(self.tokens)):
            value = self.tokens[index].value
            if value in ('(', '[', '{'): depth += 1
            elif value in (')', ']', '}'):
                depth -= 1
                if depth == 0: return False
            elif depth == 1:
                if value == ',': return False
                if value == 'for' and index > self.i + 2:
                    # Probe a complete ordinary argument first. A for expression
                    # can occur inside a valid contextual async(...) call.
                    attributes = ('i','function_depth','loop_depth','block_depth','flavor','quote_depth','await_forbidden')
                    previous = tuple(getattr(self, name) for name in attributes)
                    try:
                        self.i += 2
                        self.argument()
                        return not (self.at(')') or self.at(','))
                    except ResourceLimit: raise
                    except LanguageError: return True
                    finally:
                        for name, saved in zip(attributes, previous): setattr(self, name, saved)
        return False

    def generator_comprehension(self, asynchronous=False):
        old = self.function_depth, self.loop_depth, self.flavor, self.await_forbidden
        if asynchronous:
            self.function_depth = self.loop_depth = 0
            self.flavor = None; self.await_forbidden = False
        try:
            element = self.expr()
            if not self.comprehension_start():
                if asynchronous: self.fail('async (...) 需要推导式 for 子句')
                return element
            # The outer source retains the enclosing await permission. Every
            # later component belongs to the generator's own async boundary.
            clauses = []
            while self.comprehension_start() or clauses and self.at('if'):
                if len(clauses) >= 64: self.fail('推导式最多允许 64 个子句')
                self.function_depth = self.loop_depth = 0; self.flavor = None
                self.await_forbidden = (old[3] or bool(old[0] and old[2] not in ('async_fn','async_gen_fn'))) if not clauses else not asynchronous
                clauses.append(self.comprehension_clause())
            if self.at(','): self.fail('生成器推导式不能包含尾随逗号')
            return self.checked_comprehension(('async_gen_comp' if asynchronous else 'gen_comp', element, clauses), old)
        finally:
            self.function_depth, self.loop_depth, self.flavor, self.await_forbidden = old

    def item_component(self):
        start_token = self.t
        start = None if self.at(':') else self.expr()
        if not self.accept(':'): return start
        if self.legacy_items: self.fail('旧版索引语法不支持切片；请使用新版源码')
        stop = None if self.t.value in (':', ']', ',') else self.expr()
        step = None
        if self.accept(':'): step = None if self.t.value in (']', ',') else self.expr()
        end_token = self.t
        return Node(('slice', start, stop, step), {'file':self.filename,'line':start_token.line,'column':start_token.column,'endLine':end_token.line,'endColumn':end_token.column+1})

    @staticmethod
    def string(raw):
        return json.loads(raw) if raw.startswith('"') else ast.literal_eval(raw)

    @located
    def pattern(self, names=None):
        names = set() if names is None else names
        tok = self.t
        if self.at('_') and self.tokens[self.i + 1].value != '.' and not (self.tokens[self.i + 1].value == ':' and self.tokens[self.i + 2].value == ':'):
            self.pop(); return ('p_any',)
        if self.accept('['):
            items, rest = [], None
            while not self.at(']'):
                if self.accept('..'):
                    rest = self.pattern(names)
                    if rest[0] not in ('p_bind', 'p_any'): self.fail('列表剩余项必须是名字或 _')
                    break
                items.append(self.pattern(names))
                if not self.accept(','): break
            self.expect(']'); return ('p_list', items, rest)
        if self.accept('('):
            items, rest, comma = [], None, False
            while not self.at(')'):
                if self.accept('..'):
                    rest = self.pattern(names)
                    if rest[0] not in ('p_bind', 'p_any'): self.fail('元组剩余项必须是名字或 _')
                    # A rest-only product still needs a comma to distinguish grouping.
                    comma = bool(self.accept(',')) or comma
                    break
                items.append(self.pattern(names))
                if not self.accept(','): break
                comma = True
            self.expect(')')
            if (items or rest is not None) and not comma: self.fail('元组模式需要逗号；单项写为 (name,)')
            return ('p_tuple', items, rest)
        if self.accept('{'):
            pairs, rest = [], None
            while not self.at('}'):
                if self.accept('..'):
                    rest = self.pattern(names)
                    if rest[0] not in ('p_bind', 'p_any'): self.fail('记录剩余项必须是名字或 _')
                    break
                key = self.pop()
                if key.kind not in ('id', 'string'): self.fail('需要记录模式字段')
                key = self.string(key.value) if key.kind == 'string' else key.value
                if key in [p[0] for p in pairs]: self.fail('重复模式字段')
                self.expect(':'); pairs.append((key, self.pattern(names)))
                if not self.accept(','): break
            self.expect('}'); return ('p_record', pairs, rest)
        negative = self.accept('-')
        if negative or tok.kind in ('number','string') or tok.value in ('true','false','null'):
            tok = self.pop()
            if negative and tok.kind != 'number': self.fail('负数模式需要数值')
            if tok.kind == 'number': value = float(tok.value) if any(c in tok.value for c in '.eE') else int(tok.value)
            elif tok.kind == 'string': value = self.string(tok.value)
            else: value = {'true':True,'false':False,'null':None}[tok.value]
            return ('p_literal', -value if negative else value)
        name = self.type_name()
        if self.at(':') and self.tokens[self.i + 1].value == ':':
            self.pop(); self.pop(); self.expect('{')
            pairs, rest = [], None
            while not self.at('}'):
                if self.accept('..'):
                    rest = self.pattern(names)
                    if rest[0] not in ('p_bind', 'p_any'): self.fail('类剩余字段需要名字或 _')
                    break
                key = self.pop()
                if key.kind not in ('id', 'string'): self.fail('需要类模式字段')
                key = self.string(key.value) if key.kind == 'string' else key.value
                if key in [pair[0] for pair in pairs]: self.fail('重复类模式字段')
                self.expect(':'); pairs.append((key, self.pattern(names)))
                if not self.accept(','): break
            self.expect('}'); return ('p_class', name, pairs, rest)
        if self.accept('('):
            children = []
            while not self.at(')'):
                children.append(self.pattern(names))
                if not self.accept(','): break
            self.expect(')'); return ('p_tag', name, children)
        if '.' in name: self.fail('带限定名的构造器模式需要 ()')
        if name in names: self.fail(f'模式变量重复 {name}')
        names.add(name)
        return ('p_bind', name)

    @located
    def catch_pattern(self):
        # A root underscore has always been a catch binding. Nested underscores
        # keep the ordinary structural wildcard meaning.
        if self.at('_') and self.tokens[self.i + 1].value != '.' and not (self.tokens[self.i + 1].value == ':' and self.tokens[self.i + 2].value == ':'):
            self.pop(); return ('p_bind', '_')
        return self.pattern()

    def completion_clause(self, kind, protected, start):
        """Locate the old child before extending its expression with an else."""
        if not self.at('else'): return protected
        end = self.tokens[self.i - 1]
        child = Node(protected, {'file': self.filename, 'line': start.line, 'column': start.column,
                                 'endLine': end.line, 'endColumn': end.column + len(end.value)})
        self.pop()
        return (kind, child, self.block())

    @located
    def expr(self, minimum=0):
        tok = self.t
        context = self.context_header()
        if context is not None:
            asynchronous, manager, target = context
            if asynchronous and (self.await_forbidden or self.function_depth and self.flavor not in ('async_fn', 'async_gen_fn')) and not self.quote_depth:
                self.fail('async with 仅允许顶层或 async fn 内')
            entries = []
            while True:
                entries.append((target, manager))
                if not self.accept(','): break
                manager = self.expr(); self.expect('as'); target = self.resource_target()
            body = self.block()
            kind = 'async_with' if asynchronous else 'with'
            end = getattr(body, 'span', {})
            for target, manager in reversed(entries[1:]):
                start = getattr(manager, 'span', {})
                span = {'file':self.filename, 'line':start.get('line', tok.line), 'column':start.get('column', tok.column),
                        'endLine':end['endLine'], 'endColumn':end['endColumn']}
                nested = Node((kind if isinstance(target, str) else kind+'_pattern', target, manager, body), span)
                body = Node(('block', [nested]), dict(span))
            first = entries[0][0]
            left = (kind if isinstance(first, str) else kind+'_pattern', first, entries[0][1], body)
        elif self.at('async') and self.tokens[self.i + 1].value == 'fn':
            self.pop(); self.pop(); left = self.function(flavor='async_gen_fn' if self.accept('*') else 'async_fn')
        elif self.async_comprehension_start():
            self.pop(); self.expect('('); left = self.generator_comprehension(True); self.expect(')')
        elif self.at('quote') and self.tokens[self.i + 1].value == '{':
            self.pop(); self.quote_depth += 1
            try: left = ('quote', self.block())
            finally: self.quote_depth -= 1
        elif self.at('unquote') and self.tokens[self.i + 1].value == '(' and self.quote_depth:
            self.pop(); self.expect('('); left = ('unquote', self.expr()); self.expect(')')
        elif self.at('yield') and (self.flavor in ('gen_fn', 'async_gen_fn') or self.quote_depth) and self.tokens[self.i + 1].value not in ({'=', '.', '[', ',', ')'} | set(AUGMENTED)):
            self.pop(); kind = 'yield_from' if self.accept('from') else 'yield'
            left = (kind, ('literal', None) if self.at(';') or self.at('}') else self.expr())
        elif self.at('await') and not (self.tokens[self.i + 1].value == '(' and self.t.end == self.tokens[self.i + 1].start) and self.tokens[self.i + 1].value not in (set(self.precedence) | set(AUGMENTED) | {'=', ';', '}', ',', ')', '.', '[', ']', ':', '<eof>'}):
            self.pop()
            if (self.await_forbidden or self.function_depth and self.flavor not in ('async_fn', 'async_gen_fn')) and not self.quote_depth: self.fail('await 仅允许顶层或 async fn 内')
            left = ('await', self.expr(AWAIT_PRECEDENCE))
        elif self.accept('!') or self.accept('-') or self.accept('+') or self.accept('~'):
            left = ('unary', tok.value, self.expr(UNARY_PRECEDENCE))
        elif self.accept('if'):
            condition = self.expr(); yes = self.block(); self.expect('else')
            left = ('if', condition, yes, self.expr() if self.at('if') else self.block())
        elif self.accept('while'):
            condition = self.expr(); self.loop_depth += 1
            try: body = self.block()
            finally: self.loop_depth -= 1
            left = self.completion_clause('loop_else', ('while', condition, body), tok)
        elif self.at('for') or self.at('async') and self.tokens[self.i + 1].value == 'for':
            asynchronous = bool(self.accept('async')); self.expect('for')
            if asynchronous and self.function_depth and self.flavor not in ('async_fn', 'async_gen_fn') and not self.quote_depth:
                self.fail('async for 仅允许顶层或 async fn 内')
            name = self.binding_target()
            if asynchronous and not isinstance(name, str) and self.await_forbidden and not self.quote_depth:
                self.fail('async for 仅允许顶层或 async fn 内')
            self.expect('in'); items = self.expr(); self.loop_depth += 1
            try: body = self.block()
            finally: self.loop_depth -= 1
            kind = 'async_for' if asynchronous else 'for'
            left = self.completion_clause('loop_else', (kind if isinstance(name, str) else kind+'_pattern', name, items, body), tok)
        elif self.at('break') or self.at('continue'):
            if not self.loop_depth and not self.quote_depth: self.fail('break/continue 必须位于当前函数的循环内')
            left = (self.pop().value,)
        elif self.accept('return'):
            if not self.function_depth and not self.quote_depth: self.fail('return 必须位于函数内')
            left = ('return', ('literal', None) if self.at(';') or self.at('}') else self.expr())
        elif self.accept('match'):
            subject = self.expr(); self.expect('{'); arms = []
            while not self.at('}'):
                pattern = self.pattern(); guard = self.expr() if self.accept('if') else None
                self.expect('=>'); arms.append((pattern, guard, self.expr()))
                if not self.accept(',') and not self.accept(';'): break
            self.expect('}')
            if not arms: self.fail('match 至少需要一个分支')
            left = ('match', subject, arms)
        elif self.accept('try'):
            body = self.block()
            cases, grouped = [], None
            while self.at('catch'):
                start = self.pop()
                star = bool(self.accept('*'))
                if grouped is not None and grouped != star: self.fail('不能混合 catch 和 catch*')
                grouped = star
                pattern = self.pattern() if star else self.catch_pattern()
                guard = self.expr() if self.accept('if') else None
                if star:
                    self.expect('as'); name = self.identifier(); handler = self.block()
                    end = self.tokens[self.i - 1]
                    cases.append(Node(('group_case', pattern, guard, name, handler),
                                      {'file': self.filename, 'line': start.line, 'column': start.column,
                                       'endLine': end.line, 'endColumn': end.column + len(end.value)}))
                else: cases.append((pattern, guard, self.block()))
            caught = bool(cases)
            if grouped:
                protected = ('try_group', body, cases)
                validate_try_group(protected)
            elif len(cases) == 1 and cases[0][0][0] == 'p_bind' and cases[0][1] is None:
                protected = ('try', body, cases[0][0][1], cases[0][2])
            elif caught:
                protected = ('try_cases', body, cases)
                validate_try_cases(protected)
            else:
                protected = body
            if self.at('else') and not caught: self.fail('try else 需要 catch 名字及处理块')
            if caught: protected = self.completion_clause('try_else', protected, tok)
            # Contextual syntax keeps existing identifiers named finally valid.
            if self.at('finally') and self.tokens[self.i + 1].value == '{':
                self.pop()
                if caught:
                    end = self.tokens[self.i - 2]
                    protected = Node(protected, {'file': self.filename, 'line': tok.line,
                                                'column': tok.column, 'endLine': end.line,
                                                'endColumn': end.column + len(end.value)})
                left = ('finally', protected, self.block())
            elif caught:
                left = protected
            else:
                self.fail('try 需要 catch 或 finally')
        elif self.accept('throw'):
            left = ('rethrow',) if self.t.value in (';', '}', ')', ']', ',', '<eof>') else ('throw', self.expr())
        elif self.accept('do'): left = ('do', self.block())
        elif self.accept('fn'): left = self.function(flavor='gen_fn' if self.accept('*') else 'fn')
        elif self.accept('('):
            if self.at(')'): left = ('tuple', [])
            else:
                left = self.generator_comprehension()
                if self.accept(','):
                    items = [left]
                    while not self.at(')'):
                        items.append(self.expr())
                        if not self.accept(','): break
                    left = ('tuple', items)
            self.expect(')')
        elif self.accept('['):
            items = []; clauses = None
            while not self.at(']'):
                items.append(self.expr())
                if len(items) == 1 and self.comprehension_start():
                    clauses = self.comprehension_clauses(); break
                if not self.accept(','): break
            self.expect(']'); left = self.checked_comprehension(('list_comp', items[0], clauses)) if clauses else ('list', items)
        elif self.at('{'): left = self.record()
        elif tok.kind == 'number':
            self.pop(); left = ('literal', float(tok.value) if any(c in tok.value for c in '.eE') else int(tok.value))
        elif tok.kind == 'string': self.pop(); left = ('literal', self.string(tok.value))
        elif tok.value in ('true', 'false', 'null'):
            self.pop(); left = ('literal', {'true':True,'false':False,'null':None}[tok.value])
        elif tok.kind == 'id': left = ('name', self.identifier())
        else: self.fail('需要表达式')
        while True:
            end = self.tokens[max(0, self.i - 1)]
            if self.ranges and not isinstance(left, Node):
                left = Node(left, {'file': self.filename, 'line': tok.line, 'column': tok.column,
                                  'endLine': end.line, 'endColumn': end.column + len(end.value)})
            if self.at('!') and self.tokens[self.i + 1].value == '(' and left[0] == 'name':
                self.pop(); self.pop(); args = []
                while not self.at(')'):
                    args.append(self.expr())
                    if not self.accept(','): break
                self.expect(')'); left = ('macrocall', left[1], args)
            elif self.at('!') and self.tokens[self.i + 1].value == '(':
                self.fail('宏目标需要名字或单级静态点字段')
            elif self.accept('('):
                args, keywords, named = [], set(), False
                while not self.at(')'):
                    arg = self.argument()
                    if arg[0] in ('keyword', 'kwspread'):
                        named = True
                        if arg[0] == 'keyword':
                            if arg[1] in keywords: self.fail('重复关键字参数 '+arg[1])
                            keywords.add(arg[1])
                    elif named:
                        self.fail('位置参数或 * 展开不能位于关键字参数之后')
                    args.append(arg)
                    if not self.accept(','): break
                self.expect(')'); left = ('call', left, args)
            elif self.accept('.'):
                if self.accept('['):
                    index = self.expr(); self.expect(']'); left = ('index', left, index)
                    continue
                field_token = self.t
                field_name = self.identifier()
                field_node = Node(('literal', field_name), {'file': self.filename, 'line': field_token.line,
                                  'column': field_token.column, 'endLine': field_token.line,
                                  'endColumn': field_token.column + len(field_token.value)})
                if self.at('!') and self.tokens[self.i + 1].value == '(' and left[0] == 'name':
                    self.pop(); self.pop(); args = []
                    while not self.at(')'):
                        args.append(self.expr())
                        if not self.accept(','): break
                    self.expect(')'); left = ('macrocall', left[1]+'.'+field_name, args)
                else:
                    left = ('index', left, field_node if self.ranges else ('literal', field_name))
            elif self.accept('['):
                index = self.item_component()
                if not self.legacy_items and self.accept(','):
                    items = [index]
                    while not self.at(']'):
                        items.append(self.item_component())
                        if not self.accept(','): break
                    index = ('tuple', items)
                self.expect(']'); left = ('index' if self.legacy_items else 'subscript', left, index)
            elif self.t.value in self.precedence and self.precedence[self.t.value] >= minimum:
                op = self.pop().value
                left = ('binary', op, left, self.expr(self.precedence[op] + (0 if op == '**' else 1)))
            else: return left

@dataclass
class Cell:
    value: object
    contract: object


def catch_pattern_names(pattern, *, tick=None):
    """The catch root retains its historical underscore binding spelling."""
    from .patterns import pattern_names
    if isinstance(pattern, tuple) and pattern == ('p_bind', '_'):
        if tick: tick()
        return ('_',)
    if isinstance(pattern, tuple) and pattern and pattern[0] == 'p_any':
        raise LanguageError('catch 根 _ 是绑定名，不能使用根通配 AST', code='syntax_error').attach(getattr(pattern, 'span', {}))
    return pattern_names(pattern, tick=tick)


def validate_try_cases(node, *, tick=None):
    """Closed additive shape shared by source, IR, hygiene and execution."""
    def block(value):
        return isinstance(value, tuple) and len(value) == 2 and value[0] == 'block' and isinstance(value[1], list)
    def fail(message):
        raise LanguageError(message, code='syntax_error').attach(getattr(node, 'span', {}))
    if not isinstance(node, tuple) or len(node) != 3 or node[0] != 'try_cases' or not block(node[1]):
        fail('try_cases 需要 protected 块及 catch 分支')
    cases = node[2]
    if not isinstance(cases, list) or not cases: fail('try_cases 至少需要一个 catch 分支')
    for case in cases:
        if tick: tick()
        if not isinstance(case, (tuple, list)) or len(case) != 3: fail('catch 分支需要模式、守卫及处理块')
        pattern, guard, handler = case
        catch_pattern_names(pattern, tick=tick)
        if guard is not None and (not isinstance(guard, tuple) or not guard or not isinstance(guard[0], str)):
            fail('catch 守卫需要表达式')
        if not block(handler): fail('catch 处理体必须为块')
    if len(cases) == 1 and cases[0][0][0] == 'p_bind' and cases[0][1] is None:
        fail('单个无守卫名字 catch 必须使用历史 try AST')
    return node


def validate_try_group(node, *, tick=None):
    """Validate group handlers and their lexical control boundaries once."""
    from .patterns import pattern_names
    from .macros import _children
    def fail(message, at=node):
        raise LanguageError(message, code='syntax_error').attach(getattr(at, 'span', {}))
    def block(value):
        return isinstance(value, tuple) and len(value) == 2 and value[0] == 'block' and isinstance(value[1], list)
    if not isinstance(node, tuple) or len(node) != 3 or node[0] != 'try_group' or not block(node[1]):
        fail('try_group 需要 protected 块及 catch* 分支')
    if not isinstance(node[2], list) or not node[2]: fail('try_group 至少需要一个 catch* 分支')
    for case in node[2]:
        if tick: tick()
        if not isinstance(case, tuple) or len(case) != 5 or case[0] != 'group_case': fail('无效 catch* 分支')
        _, pattern, guard, name, body = case
        pattern_names(pattern, tick=tick)
        if type(name) is not str or not name.isidentifier() or name in Parser.reserved: fail('catch* as 需要绑定名', case)
        if guard is not None and (not isinstance(guard, tuple) or not guard): fail('catch* 守卫需要表达式', case)
        if not block(body): fail('catch* 处理体需要块', case)
        pending = [(body, 0)] + ([(guard, 0)] if guard is not None else [])
        while pending:
            item, loops = pending.pop()
            if tick: tick()
            kind = item[0]
            if kind == 'return' or kind in ('break', 'continue') and not loops:
                fail('catch* 守卫或处理器不能用 return/break/continue 逃逸', item)
            if kind in ('quote', 'macro'): continue
            if kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
                # Defaults run here; the nested body has its own return target.
                pending.extend((p[3], loops) for p in item[1] if len(p) >= 4 and p[3] is not None)
            elif kind in ('for', 'async_for', 'for_pattern', 'async_for_pattern'):
                pending.extend([(item[2], loops), (item[3], loops+1)])
            elif kind == 'while': pending.extend([(item[1], loops), (item[2], loops+1)])
            else: _children(item, lambda child: pending.append((child, loops)))
    return node


class ReturnSignal(Exception):
    def __init__(self, value): self.value = value


class BreakSignal(Exception): pass
class ContinueSignal(Exception): pass


@dataclass
class _Completion:
    """Invocation-local control outcome, never stored in a language Env/value."""
    state: str = 'pending'


class Env:
    def __init__(self, parent=None):
        self.parent, self.values = parent, {}

    def snapshot(self):
        result = Env()
        if self.parent:
            result.values.update(self.parent.snapshot().values)
        result.values.update(self.values)
        return result

    def bind(self, name, value):
        if name in self.values:
            raise LanguageError(f'不可重复绑定 {name}')
        self.values[name] = value
        return value

    def resolve_cell(self, name):
        """Resolve the actual location before an effectful read/modify/write."""
        if name in self.values:
            cell = self.values[name]
            if not isinstance(cell, Cell) or getattr(cell, '_class_reference', False): raise LanguageError(f'不可赋值给 let/函数绑定 {name}')
            return cell
        if self.parent: return self.parent.resolve_cell(name)
        raise LanguageError(f'未定义可变绑定 {name}')

    def assign(self, name, value):
        cell = self.resolve_cell(name)
        value = check_type(value, cell.contract)
        cell.value = value
        return value

    def get(self, name):
        if name in self.values:
            value = self.values[name]
            return value.value if isinstance(value, Cell) else value
        if self.parent:
            return self.parent.get(name)
        raise LanguageError(f'未定义名称 {name}')


@dataclass
class Function:
    params: list
    returns: str
    body: tuple
    env: Env
    name: str
    owner: object = None
    flavor: str = 'fn'
    macro_bindings: object = None

    capabilities: object = None
    compiled_plan: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.owner is not None:
            from .agent_tools import current_caps
            current=current_caps(self.owner)
            self.capabilities=current if self.capabilities is None else self.capabilities if current is None else self.capabilities & current


@dataclass
class TailCall:
    function: object
    args: list
    contracts: tuple = ()
    kwargs: object = None
    span: object = None
    continuations: tuple = ()
    handled_error: object = field(default_factory=lambda: MISSING, repr=False)
    error_context: object = field(default_factory=lambda: MISSING, repr=False)
    module_scope: object = field(default_factory=lambda: MISSING, repr=False)
    primary_only: bool = False
    ordinary_call: bool = False


@dataclass
class Agent:
    name: str
    config: dict
    owner: object = None


@dataclass
class Native:
    name: str
    call: object
    owner: object = None
    constructor: object = None
    spec_id: object = None

    capabilities: object = None
    generic: object = None
    captures: tuple = ()

    def __post_init__(self):
        if self.owner is not None:
            from .agent_tools import current_caps
            current=current_caps(self.owner)
            self.capabilities=current if self.capabilities is None else self.capabilities if current is None else self.capabilities & current


@dataclass(frozen=True)
class DataType:
    name: str
    variants: tuple

    def accepts(self, value):
        return (isinstance(value, dict) and set(value) == {'$type', '$tag', '$values'}
                and value['$type'] == self.name and isinstance(value['$tag'], str)
                and isinstance(value['$values'], list)
                and (value['$tag'], len(value['$values'])) in self.variants)


MISSING = object()


from .dynamic_contexts import ContextVar,ContextToken,ContextScope,Context,VALUES as CONTEXT_VALUES

from .bot_values import Bot

TYPES = {'Bot','Robot','RobotObservation','RobotCommand','RobotReceipt','PromptTag','PromptScope','EscapeContinuation','Continuation','Device','DeviceAction','DeviceReceipt','EqlSpecializer','Contract','HashMap','Error','Code','ContextVar','ContextToken','ContextScope','Context','Condition','HandlerScope','RestartScope','Restart','Decimal','DecimalContext','Fraction', 'Complex', 'Numeric', 'Real', 'Rational', 'Type', 'UIArtifact', 'AgentTool', 'Any', 'Number', 'Int', 'String', 'Bool', 'List', 'Tuple', 'Set', 'Record', 'Function', 'Null', 'Task', 'TaskGroup', 'CancelShield', 'ContextManager', 'AsyncContextManager', 'ExitStack', 'AsyncExitStack', 'Channel', 'ChannelCase', 'Generator', 'AsyncGenerator', 'Iterator', 'AsyncIterator', 'Class', 'ClassMember', 'Instance', 'Descriptor', 'NotImplemented', 'Slice', 'Plan', 'Goal', 'Skill', 'Team', 'Program', 'Process', 'ProcessEvolution', 'AgentSession', 'Computer', 'Observation', 'ComputerAction', 'ObservedInput'}


def check_type(value, typ, *, _probe=False):
    if type(typ) is Contract:
        from .contracts import accepts, require
        return accepts(typ.owner, typ, value) if _probe else require(typ.owner, typ, value)
    from .code_values import Code
    from .agent_tools import AgentTool, BoundAgent
    from .session_tools import BoundSession
    from .device_values import Device, DeviceAction, DeviceReceipt
    from .computer_values import Computer, Observation, ComputerAction, ObservedInput
    from .session_values import AgentSession
    from .process_values import Process, ProcessEvolution
    from .program_values import Program
    from .team_values import Team
    from .modes import ModeValue, Plan, Goal, Skill
    from .operators import NOT_IMPLEMENTED
    from .indexing import Slice
    from .objects import ChaosClass, ChaosInstance, ClassMember, UIArtifact, DESCRIPTORS, callable_object, is_descriptor
    if isinstance(typ, ChaosClass):
        actual = value.metaclass if type(value) is ChaosClass else value.klass if type(value) is ChaosInstance else None
        valid = actual is not None and value.owner is typ.owner and any(base is typ for base in actual.mro)
        if _probe: return valid
        if not valid: raise LanguageError('类型契约要求 '+typ.name)
        return value
    from .contexts import ContextManager, AsyncContextManager
    from .resource_stacks import ExitStack, AsyncExitStack
    from .suspension import Task, Generator, AsyncGenerator
    from .iteration import Iterator, AsyncIterator
    from .task_groups import TaskGroup
    from .cancellation import CancelShield
    from .channels import Channel
    from .channel_select import ChannelCase
    from .conditions import HandlerScope,RestartScope,Restart
    if isinstance(typ, DataType):
        valid = typ.accepts(value)
        if _probe: return valid
        if not valid: raise LanguageError(f'类型契约要求 {typ.name}')
        return value
    if typ not in TYPES:
        raise LanguageError(f'未知类型 {typ}')
    valid = {
        'PromptTag':type(value) is PromptTag,'PromptScope':type(value) is PromptScope,'EscapeContinuation':type(value) is EscapeContinuation,'Continuation':type(value) is Continuation,
        'ContextVar':type(value) is ContextVar,'ContextToken':type(value) is ContextToken,'ContextScope':type(value) is ContextScope,'Context':type(value) is Context,
        'EqlSpecializer':type(value) is EqlSpecializer, 'Contract':type(value) is Contract, 'HashMap':type(value) is HashMap, 'Error':type(value) is Error, 'Code':type(value) is Code, 'Any': True, 'Bot':type(value) is Bot, 'Number': type(value) in (int, float), 'Int': type(value) is int,
        'Condition':type(value) is Condition, 'HandlerScope':type(value) is HandlerScope, 'RestartScope':type(value) is RestartScope, 'Restart':type(value) is Restart,
        'Decimal':type(value) is Decimal, 'DecimalContext':type(value) is DecimalContext,
        'Fraction': type(value) is Fraction, 'Complex': type(value) is Complex,
        'Numeric': is_numeric(value), 'Real': is_real(value), 'Rational': type(value) in (int,Fraction),
        'String': isinstance(value, str), 'Bool': type(value) is bool,
        'List': isinstance(value, list), 'Tuple': type(value) is TupleValue, 'Set': type(value) is SetValue, 'Record': isinstance(value, dict),
        'UIArtifact': type(value) is UIArtifact,
        'AgentTool': type(value) is AgentTool,
        'Function': isinstance(value, (Bot, Device, BoundSession, BoundAgent, Function, Native, Agent, ModeValue, Team, Program, Process, AgentSession, Computer)) or callable_object(value),
        'Type': type(value) is ChaosClass and getattr(value.owner, 'native_type', None) in value.mro,
        'Class': isinstance(value, ChaosClass), 'ClassMember': type(value) is ClassMember, 'Instance': isinstance(value, ChaosInstance), 'Descriptor': is_descriptor(value), 'Null': value is None,
        'ExitStack': type(value) is ExitStack, 'AsyncExitStack': type(value) is AsyncExitStack,
        'ContextManager': type(value) is ContextManager, 'AsyncContextManager': type(value) is AsyncContextManager,
        'Task': isinstance(value, Task), 'TaskGroup': type(value) is TaskGroup, 'CancelShield': type(value) is CancelShield, 'Channel': type(value) is Channel, 'ChannelCase': type(value) is ChannelCase, 'Generator': isinstance(value, Generator) and not isinstance(value, AsyncGenerator),
        'AsyncGenerator': isinstance(value, AsyncGenerator), 'Iterator': isinstance(value, Iterator),
        'AsyncIterator': isinstance(value, AsyncIterator), 'NotImplemented': value is NOT_IMPLEMENTED, 'Slice': type(value) is Slice, 'Plan': type(value) is Plan, 'Goal': type(value) is Goal, 'Skill': type(value) is Skill, 'Team': type(value) is Team, 'Program': type(value) is Program, 'Process': type(value) is Process, 'ProcessEvolution': type(value) is ProcessEvolution, 'AgentSession':type(value) is AgentSession, 'Robot':type(value) is Robot,'RobotObservation':type(value) is RobotObservation,'RobotCommand':type(value) is RobotCommand,'RobotReceipt':type(value) is RobotReceipt,'Device':type(value) is Device,'DeviceAction':type(value) is DeviceAction,'DeviceReceipt':type(value) is DeviceReceipt,'Computer':type(value) is Computer, 'Observation':type(value) is Observation, 'ComputerAction':type(value) is ComputerAction, 'ObservedInput':type(value) is ObservedInput,
    }[typ]
    if _probe: return valid
    if not valid:
        raise LanguageError(f'类型契约要求 {typ}，实际为 {type(value).__name__}')
    return value


def serializable(value):
    from .code_values import Code,describe as describe_code
    from .agent_tools import AgentTool, BoundAgent, tool_info, binding_info
    from .session_tools import BoundSession, binding_info as session_binding_info
    from .team_bindings import BoundTeam,binding_info as team_binding_info
    from .resource_stacks import STACKS, describe as describe_stack
    from .contexts import MANAGERS, describe as describe_context
    from .device_values import VALUES as DEVICE_VALUES, describe as describe_device
    from .computer_values import VALUES as COMPUTER_VALUES, describe as describe_computer
    from .session_values import AgentSession, describe as describe_session
    from .process_values import Process, ProcessEvolution, describe as describe_process, describe_evolution
    from .program_values import Program, describe as describe_program
    from .team_values import Team, describe as describe_team
    from .modes import ModeValue, describe as describe_mode
    from .indexing import Slice, describe as describe_slice
    from .operators import NOT_IMPLEMENTED
    from .objects import OBJECT_VALUES, describe
    from .task_groups import describe as describe_group
    from .cancellation import CancelShield, describe as describe_shield
    from .channels import describe as describe_channel
    from .suspension import Task, Generator, AsyncGenerator
    from .iteration import Iterator, AsyncIterator
    from .task_groups import TaskGroup
    from .channels import Channel
    from .channel_select import ChannelCase, describe as describe_case
    count, chars = [0], [0]
    def visit(v, depth=0):
        count[0] += 1
        if count[0] > 20000 or depth > 100:
            raise ResourceLimit('值展开超过节点数或深度资源限制')
        from .conditions import VALUES as RECOVERY_VALUES,describe as recovery_describe
        if type(v) is Bot:
            from .bot_values import describe as bot_describe
            return visit(bot_describe(v),depth+1)
        if type(v) in (PromptTag,PromptScope,EscapeContinuation,Continuation):
            from .control_prompts import describe as control_describe
            return visit(control_describe(v),depth+1)
        if type(v) is EqlSpecializer:
            from .eql_specializers import describe as eql_describe
            return visit(eql_describe(v),depth+1)
        if type(v) is Contract:
            from .contracts import describe as describe_contract
            return visit(describe_contract(v), depth+1)
        if type(v) is HashMap:
            from .hash_maps import describe as describe_hash_map
            return visit(describe_hash_map(v), depth+1)
        if type(v) is Error:
            from .error_values import describe as describe_error
            return visit(describe_error(v),depth+1)
        if type(v) is Code:return visit(describe_code(v),depth+1)
        if type(v) is Condition:
            from .condition_values import describe as condition_describe
            return visit(condition_describe(v),depth+1)
        if type(v) in CONTEXT_VALUES:
            from .dynamic_contexts import describe as binding_describe
            return visit(binding_describe(v),depth+1)
        if type(v) in RECOVERY_VALUES:return visit(recovery_describe(v),depth+1)
        if type(v) is DecimalContext:
            from .decimal_values import info
            return visit({'$decimalContext':info(v)},depth+1)
        if type(v) in (Fraction, Complex, Decimal): return visit(numeric_encode(v), depth+1)
        if type(v) is AgentTool: return visit({'$agentTool':tool_info(v)}, depth+1)
        if type(v) is BoundAgent: return visit({'$boundAgent':binding_info(v)}, depth+1)
        if type(v) is BoundSession: return visit({'$boundSession':session_binding_info(v)}, depth+1)
        if type(v)is BoundTeam:return visit({'$boundTeam':team_binding_info(v)},depth+1)
        if type(v) in STACKS: return visit(describe_stack(v), depth + 1)
        if type(v) in MANAGERS: return visit(describe_context(v), depth + 1)
        if type(v) in ROBOT_VALUES:
            from .robot_values import describe as robot_describe
            return visit(robot_describe(v),depth+1)
        if type(v) in DEVICE_VALUES:return visit(describe_device(v),depth+1)
        if type(v) in COMPUTER_VALUES: return visit(describe_computer(v),depth+1)
        if type(v) is AgentSession: return visit(describe_session(v), depth + 1)
        if type(v) is Process: return visit(describe_process(v), depth + 1)
        if type(v) is ProcessEvolution: return visit(describe_evolution(v), depth + 1)
        if type(v) is Program: return visit(describe_program(v), depth + 1)
        if type(v) is Team: return visit(describe_team(v), depth + 1)
        if isinstance(v, ModeValue): return visit(describe_mode(v), depth + 1)
        if v is NOT_IMPLEMENTED: return visit({'$notImplemented': True}, depth + 1)
        if type(v) is Slice: return visit(describe_slice(v), depth + 1)
        if isinstance(v, OBJECT_VALUES):
            return visit(describe(v), depth + 1)
        if type(v) is TaskGroup: return visit(describe_group(v), depth + 1)
        if type(v) is CancelShield: return visit(describe_shield(v), depth + 1)
        if type(v) is Channel: return visit(describe_channel(v), depth + 1)
        if type(v) is ChannelCase: return visit(describe_case(v), depth + 1)
        if isinstance(v, DataType):
            return {'$datatype': v.name}
        if isinstance(v, (Task, Generator, Iterator, AsyncIterator)):
            return {'$handle': type(v).__name__, 'state': v.state}
        if isinstance(v, (Function, Native, Agent)):
            return {'$callable': v.name}
        if type(v) is float and not math.isfinite(v):
            raise LanguageError('不支持非有限数值')
        if type(v) is int and v.bit_length() > 14000:
            raise ResourceLimit('整数超过 14000 bit 资源限制')
        if isinstance(v, str):
            v.encode("utf-8")
            chars[0] += len(v)
            if chars[0] > 250000:
                raise ResourceLimit('字符串总量超过资源限制')
        if type(v) is SetValue: return visit({'$set': list(v)}, depth + 1)
        if type(v) is TupleValue: return visit({'$tuple': list(v)}, depth + 1)
        if isinstance(v, list):
            if len(v) > 10000:
                raise ResourceLimit('列表超过 10000 项限制')
            return [visit(x, depth + 1) for x in v]
        if isinstance(v, dict):
            if len(v) > 10000: raise ResourceLimit('记录超过 10000 字段限制')
            if not all(isinstance(k, str) for k in v): raise LanguageError('记录字段类型无效')
            return {visit(k, depth + 1): visit(x, depth + 1) for k, x in v.items()}
        if v is None or type(v) in (bool, int, float, str):
            return v
        raise LanguageError('不支持的运行时值类型')
    return visit(value)


class Evaluator:
    def __init__(self, tool_handler=None, agent_handler=None, step_limit=30000, timeout=20, cancelled=None, workflow_handler=None, trace_handler=None, effect_handler=None, debug_hook=None, mode_handler=None, program_handler=None, program_origin=None, process_handler=None, session_handler=None, computer_handler=None, source_handler=None, device_handler=None, condition_debug_hook=None, condition_pause=False, condition_debug_redact=None, robot_handler=None, robot_cleanup_handler=None, guidance_handler=None, optimize_literals=True, native_code_cache=DEFAULT_NATIVE_CODE_CACHE):
        if native_code_cache is not None and type(native_code_cache) is not NativeCodeCache:
            raise TypeError('native_code_cache must be NativeCodeCache or None')
        self.native_code_cache = native_code_cache
        if type(optimize_literals) is not bool: raise ValueError('optimize_literals requires Bool')
        self.optimize_literals = optimize_literals
        self._literal_plan = None
        self._literal_applied_operations = self._literal_guard_misses = 0
        self.tool_handler, self.agent_handler = tool_handler, agent_handler
        self.workflow_handler, self.trace_handler = workflow_handler, trace_handler
        self.effect_handler, self.debug_hook = effect_handler, debug_hook
        self.condition_debug_hook, self.condition_pause = condition_debug_hook, condition_pause
        self.condition_debug_depth = 0
        self.condition_debug_redact = condition_debug_redact or (lambda value:value)
        from .generated_sources import Registry
        self.generated_sources,self.source_handler=Registry(),source_handler
        self.mode_handler = mode_handler
        self.process_handler = process_handler
        self.session_handler = session_handler
        self.device_handler = device_handler
        self.robot_handler = robot_handler
        self.robot_cleanup_handler = robot_cleanup_handler
        self.guidance_handler = guidance_handler
        self.computer_handler = computer_handler
        self.program_handler, self.program_origin = program_handler, copy.deepcopy(program_origin)
        self.frames, self.frame_sequence = [], 0
        from .suspension import Scheduler
        self.suspension = Scheduler(self)
        self.current_span = {}
        self.handled_error = None
        self.error_context = None
        self.step_limit, self.steps = step_limit, 0
        self.deadline, self.cancelled = time.monotonic() + timeout, cancelled or (lambda: False)
        self.depth = 0
        self.object_class_count = self.object_instance_count = 0
        self.native_kernel_count = 0
        self.expression_trace_required = None
        from .metaclasses import bootstrap
        bootstrap(self)
        from .exception_classes import bootstrap as exception_bootstrap
        exception_bootstrap(self)
        self.env = self.builtins()

    def numeric_checkpoint(self):
        """Stable logical numeric carrier; forwards the actual replaceable budget."""
        self.tick()

    def optimization_info(self):
        """Aggregate portable specialization evidence since the last run began."""
        from .portable_optimizer import LiteralPlan
        plan = self._literal_plan
        return {'schema': 'chaos.literal-runtime/1', 'enabled': self.optimize_literals,
                'candidateSites': plan.info()['candidateSites'] if type(plan) is LiteralPlan else 0,
                'appliedOperations': self._literal_applied_operations,
                'guardMisses': self._literal_guard_misses}

    def tick(self):
        current = self.suspension.current
        if current and current.task and current.task.cancel_event.is_set():
            raise self.suspension.cancelled_error(current.task)
        self.steps += 1
        if self.cancelled():
            raise ResourceLimit('执行已取消')
        if self.steps > self.step_limit:
            raise ResourceLimit(f'超过计算步数限制 {self.step_limit}')
        if time.monotonic() > self.deadline:
            raise ResourceLimit('执行超时')
        self.suspension.check_interrupt()

    @staticmethod
    def lookup_qualified(name, env):
        parts = name.split('.')
        value = env.get(parts[0])
        for part in parts[1:]:
            if not isinstance(value, dict) or part not in value:
                raise LanguageError(f'限定名称不存在 {name}')
            value = value[part]
        return value

    def contract(self, name, env):
        if name in TYPES: return name
        value = self.lookup_qualified(name, env)
        from .objects import ChaosClass, owned
        owned(value, self)
        if type(value) is Contract:
            from .contracts import validate_closed as validate_contract
            return validate_contract(self, value)
        if not isinstance(value, (DataType, ChaosClass)): raise LanguageError(f'未知类型 {name}')
        return value

    def run(self, source, bindings=None, *, files=None, entry='main.chaos'):
        return self._run(source, bindings, files=files, entry=entry)

    def run_export(self, bundle, module, export, value):
        from .process_values import validate_binding, data
        validate_binding(bundle, {'kind':'process','name':'turn','module':module,'export':export,
                                  'intervalSeconds':None,'maxTurns':1})
        value = data(value, max_chars=100000)
        return self._run(bundle, {'input':value}, export_target=(module,export,value))

    def _s_export(self, target, value):
        from .execution_modules import module_cursor
        from .execution_frames import Done
        cursor = module_cursor(self, 'export-run', target, value)
        value = error = None
        closing = False
        closing_error = None
        while True:
            if closing:
                action = cursor.close_request(closing_error)
                if type(action) is Done:
                    if not cursor.close_completed:
                        raise cursor.close_signal
                    return action.value
                pending = action.request
            else:
                try:
                    pending = cursor.throw(error) if error is not None else cursor.send(value)
                except StopIteration as stop:
                    return stop.value
            try:
                value = yield pending
                error = None
                closing = False
            except GeneratorExit as failure:
                value = error = None
                closing_error = failure
                closing = True
            except BaseException as failure:
                value = None
                error = failure
                closing = False

    def _run(self, source, bindings=None, *, files=None, entry='main.chaos', export_target=None):
        from .modules import ProgramBundle
        self.suspension.begin()
        failure = None
        try:
            self._literal_plan = None
            self._literal_applied_operations = self._literal_guard_misses = 0
            self.bundle = source if isinstance(source, ProgramBundle) else ProgramBundle(source, files, entry)
            if self.optimize_literals:
                self._literal_plan = getattr(self.bundle, '_literal_plan', None)
            self.module_cache, self.module_results = {}, {}
            self.module_bindings = {}
            for key, value in (bindings or {}).items():
                self.module_bindings[key] = self.copy_value(value)
            if export_target is None:
                self.load_module(self.bundle.entry)
                result = self.module_results[self.bundle.entry]
            else:
                from .process_values import validate_result
                module,name,value = export_target
                exports = self.load_module(module)
                result = validate_result(self.suspension.drive(self._s_export(exports[name],value)),self)
            serializable(result); self.tick()
            return result
        except LanguageError as error:
            failure = error
            raise
        except RecursionError:
            failure = ResourceLimit('嵌套深度过大；请使用尾递归或 fold')
            raise failure from None
        except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError) as e:
            failure = LanguageError(str(e), code=type(e).__name__)
            raise failure from None
        except BaseException as error:
            failure = error
            raise
        finally:
            self.suspension.finish(failure)

    def load_module(self, name):
        from .execution_modules import legacy_load
        return legacy_load(self, name)

    def pattern_constructor(self, name, env):
        constructor = self.lookup_qualified(name, env)
        if not isinstance(constructor, Native) or constructor.constructor is None:
            raise LanguageError('模式需要 data 构造器 '+name)
        return constructor.constructor

    def pattern_class_fields(self, name, value, keys, include_rest, env):
        from .objects import pattern_fields
        return pattern_fields(value, self.lookup_qualified(name, env), self, keys, include_rest)

    def pattern_match(self, pattern, value, env, bound):
        from .patterns import match_pattern
        result = match_pattern(pattern, value, resolve_tag=lambda name:self.pattern_constructor(name, env),
                               equal=self.equal, tick=self.tick,
                               project_class=lambda name,item,keys,rest:self.pattern_class_fields(name,item,keys,rest,env))
        if result is None: return False
        bound.update(result); return True

    def bind_pattern(self, pattern, value, env, *, mutable=False, lookup_env=None):
        """Match and preflight every leaf, then commit the entire binding group."""
        bindings = {}
        if not self.pattern_match(pattern, value, env if lookup_env is None else lookup_env, bindings):
            raise LanguageError('解构绑定模式不匹配', code='match_error').attach(getattr(pattern, 'span', {}))
        for name in bindings:
            if name in env.values: raise LanguageError('不可重复绑定 '+name).attach(getattr(pattern, 'span', {}))
        values = {name:Cell(item, 'Any') for name,item in bindings.items()} if mutable else bindings
        env.values.update(values)
        return value

    def assign_pattern(self, pattern, value, env):
        """Preflight the whole structural update before touching existing Cells."""
        bindings = {}
        if not self.pattern_match(pattern, value, env, bindings):
            raise LanguageError('解构赋值模式不匹配', code='match_error').attach(getattr(pattern, 'span', {}))
        updates = []
        for name, item in bindings.items():
            self.tick()
            cell = env.resolve_cell(name)
            updates.append((cell, check_type(item, cell.contract)))
        self.tick()
        # No callbacks, budget checks, debugger stops or suspension are allowed
        # inside this commit. Effects performed by the RHS are not rolled back.
        for cell, item in updates: cell.value = item
        return value

    def bind_scope_target(self, node, value, scope, lookup_env):
        """Bind an acquired value within its already-protected lexical scope."""
        if node[0] in ('with_pattern', 'async_with_pattern'):
            return self.bind_pattern(node[1], value, scope, lookup_env=lookup_env)
        return scope.bind(node[1], value)

    def _s_evaluate(self,node,env,tail=False,*,all_results=False,_completion=None,_entry_checkpoint_done=False,_compiled_regions=None,_normalized=False):
        from .execution_frames import gateway,EvaluationRequest
        return gateway(self,EvaluationRequest(node,env,tail,{'all_results':all_results,'_completion':_completion,'_entry_checkpoint_done':_entry_checkpoint_done,'_compiled_regions':_compiled_regions}),normalized=_normalized)

    def debug_checkpoint(self, node, env):
        source=getattr(node,"_generated_source",None)
        if source is not None and self.debug_hook:
            from .generated_sources import register
            register(self,source)
        if self.debug_hook:
            paused = self.debug_hook(node, env, tuple(self.frames)) or 0
            if isinstance(paused, (int, float)) and paused > 0: self.deadline += paused

    def evaluate(self, node, env, tail=False):
        return self.suspension.drive(self._s_evaluate(node, env, tail))

    def invoke(self, fn, args, kwargs=None, **options):
        return self.suspension.drive(self._s_invoke(fn, args, kwargs, **options))

    def resolve(self, value):
        return self.suspension.drive(self._s_resolve(value))

    @staticmethod
    def copy_value(value):
        serializable(value)
        def clone(v):
            if type(v) is SetValue: return v
            if type(v) is TupleValue: return TupleValue([clone(x) for x in v])
            if isinstance(v, list):
                return [clone(x) for x in v]
            if isinstance(v, dict):
                return {k: clone(x) for k, x in v.items()}
            return v
        return clone(value)

    def host_value(self, value):
        from .code_values import Code,owned as code_owned,describe as describe_code
        from .resource_stacks import STACKS, owned as stack_owned, describe as describe_stack
        from .contexts import MANAGERS, context_owned, describe as describe_context
        from .device_values import VALUES as DEVICE_VALUES, owned as device_owned, describe as describe_device
        from .computer_values import VALUES as COMPUTER_VALUES, owned as computer_owned, describe as describe_computer
        from .session_values import AgentSession, owned as session_owned, describe as describe_session
        from .process_values import Process, ProcessEvolution, owned as process_owned, describe as describe_process, evolution_owned, describe_evolution
        from .program_values import Program, owned as program_owned, describe as describe_program
        from .team_values import Team, owned as team_owned, describe as describe_team
        from .modes import ModeValue, owned as mode_owned, describe as describe_mode
        # Preserve legacy callable handling, but new live object graphs may never
        # cross the host-effect boundary, including nested synchronous payloads.
        from .objects import OBJECT_VALUES, describe, owned
        self.copy_value(value)
        # One ownership walk covers nested map entries before opaque export.
        from .indexing import validate_owned
        validate_owned(self, value)
        from .operators import NOT_IMPLEMENTED
        from .iteration import HANDLES
        from .task_groups import TaskGroup, owned as group_owned, describe as describe_group
        from .cancellation import CancelShield, owned as shield_owned, describe as describe_shield
        from .channels import Channel, owned as channel_owned, describe as describe_channel
        from .channel_select import ChannelCase, owned as case_owned, describe as describe_case
        from .indexing import Slice, describe as describe_slice
        def visit(item):
            from .conditions import VALUES as RECOVERY_VALUES,owned as recovery_owned,describe as recovery_describe
            if type(item) in (PromptTag,PromptScope,EscapeContinuation,Continuation):
                from .control_prompts import describe as control_describe,owned as control_owned
                return control_describe(control_owned(item,self))
            if type(item) is EqlSpecializer:
                from .eql_specializers import describe as eql_describe
                return eql_describe(item)
            if type(item) is Contract:
                from .contracts import owned as contract_owned, describe as describe_contract
                return describe_contract(contract_owned(item,self))
            if type(item) is HashMap:
                from .hash_maps import describe as describe_hash_map
                return describe_hash_map(item)
            if type(item) is Error:
                from .error_values import owned as error_owned, describe as describe_error
                return visit(describe_error(error_owned(item,self)))
            if type(item) is Code:return describe_code(code_owned(item,self))
            if type(item) is Condition:
                from .condition_values import describe as condition_describe
                return visit(condition_describe(item))
            if type(item) in CONTEXT_VALUES:
                from .dynamic_contexts import owned as binding_owned,describe as binding_describe
                return binding_describe(binding_owned(item,self))
            if type(item) in RECOVERY_VALUES:return recovery_describe(recovery_owned(item,self))
            if type(item) is DecimalContext:
                from .decimal_values import owned as decimal_owned,info
                return {'$decimalContext':info(decimal_owned(item,self))}
            if type(item) in (Fraction,Complex,Decimal): return numeric_encode(item)
            if type(item) is SetValue: return {'$set': [serializable(visit(child)) for child in item]}
            if type(item) is TupleValue:
                from .indexing import validate_owned
                validate_owned(self, item)
                return {'$tuple': [serializable(visit(child)) for child in item]}
            if type(item) in STACKS: return describe_stack(stack_owned(item, self))
            if type(item) in MANAGERS: return describe_context(context_owned(item, self))
            if type(item) is TaskGroup: return describe_group(group_owned(item, self))
            if type(item) is CancelShield: return describe_shield(shield_owned(item, self))
            if type(item) is Channel: return describe_channel(channel_owned(item, self))
            if type(item) is ChannelCase: return describe_case(case_owned(item, self))
            if type(item) in ROBOT_VALUES:
                from .robot_values import owned as robot_owned,describe as robot_describe
                return robot_describe(robot_owned(item,self))
            if type(item) in DEVICE_VALUES:return describe_device(device_owned(item,self))
            if type(item) in COMPUTER_VALUES: return describe_computer(computer_owned(item,self))
            if type(item) is AgentSession: return describe_session(session_owned(item,self))
            if type(item) is Process: return describe_process(process_owned(item, self))
            if type(item) is ProcessEvolution: return describe_evolution(evolution_owned(item,self))
            if type(item) is Program: return describe_program(program_owned(item, self))
            if type(item) is Team: return describe_team(team_owned(item, self))
            if type(item) is Bot:
                from .bot_values import owned as bot_owned,describe as bot_describe
                return bot_describe(bot_owned(item,self))
            if isinstance(item, ModeValue): return describe_mode(mode_owned(item, self))
            if item is NOT_IMPLEMENTED: return {'$notImplemented': True}
            if type(item) is Slice: return describe_slice(item)
            if isinstance(item, HANDLES):
                self.suspension.validate(item, type(item))
                return serializable(item)
            if isinstance(item, OBJECT_VALUES): return describe(owned(item, self))
            if isinstance(item, list): return [visit(child) for child in item]
            if isinstance(item, dict): return {key:visit(child) for key,child in item.items()}
            return item
        return visit(value)

    def equal(self, left, right):
        from .objects import OBJECT_VALUES, owned
        todo, seen = [(left, right)], set()
        while todo:
            self.tick()
            a, b = todo.pop()
            owned(a, self); owned(b, self)
            if type(a) is not type(b):
                return False
            if type(a) is EqlSpecializer:
                from .eql_specializers import validate as eql_validate
                eql_validate(self,a);eql_validate(self,b)
                if a is not b:return False
            elif type(a) is HashMap:
                if a is not b: return False
            elif type(a) is SetValue:
                if not set_equal(a, b, self.tick): return False
            elif isinstance(a, (list, dict, TupleValue)):
                pair = (id(a), id(b))
                if pair in seen:
                    continue
                seen.add(pair)
                if len(a) != len(b):
                    return False
                if isinstance(a, (list, TupleValue)):
                    todo.extend(zip(a, b))
                else:
                    if a.keys() != b.keys():
                        return False
                    todo.extend((a[k], b[k]) for k in a)
            elif isinstance(a, (Function, Native, Agent) + OBJECT_VALUES):
                if a is not b:
                    return False
            elif type(a) is Bot:
                from .bot_values import same_identity as bot_identity,owned as bot_owned
                bot_owned(a,self);bot_owned(b,self)
                if not bot_identity(a,b):return False
            elif type(a) in ROBOT_VALUES:
                from .robot_values import same_identity as robot_identity
                if not robot_identity(a,b):return False
            elif type(a) in (PromptTag,PromptScope,EscapeContinuation,Continuation):
                from .control_prompts import same_identity
                if a is not b and not same_identity(a,b):return False
            elif type(a) is Error:
                if a.exception is not b.exception:return False
            elif type(a) is Condition:
                from .condition_values import validate as condition_validate
                condition_validate(a,self.tick);condition_validate(b,self.tick)
                if a.kind!=b.kind or a.message!=b.message:return False
                todo.append((a.data,b.data))
            elif type(a) in (Fraction, Complex, Decimal):
                from .numeric_values import equal as numeric_equal
                if not numeric_equal(a,b): return False
            elif a != b:
                return False
        return True

    def _s_resolve(self, value, *, all_results=False):
        if not isinstance(value, TailCall):
            from .multiple_values import primary,prepare
            value=prepare(self,value)
            return value if all_results else primary(value,self)
        return (yield from self._s_invoke(value.function, value.args, value.kwargs, span=value.span, contracts=value.contracts, continuations=value.continuations, handled_error=value.handled_error, error_context=value.error_context, all_results=all_results, primary_only=value.primary_only))

    def _s_protected_value(self, node, env):
        """Enter the shared typed resolver for a protected dynamic boundary."""
        from .execution_frames import gateway,ProtectedValueRequest
        return gateway(self,ProtectedValueRequest(node,env))

    def _s_execute(self, node, env, tail=False, *, all_results=False, _completion=None, _compiled_regions=None, _flow_boundary=None):
        from .execution_modules import ModuleSyntaxFrame
        cursor = None
        if node[0] in ModuleSyntaxFrame.KINDS:
            from .execution_modules import module_cursor
            from .execution_frames import Done
            cursor = module_cursor(self, 'syntax', node, env)
        elif node[0] in ('await','yield','yield_from','throw','rethrow'):
            from .execution_suspension import SuspensionSyntaxFrame,suspension_cursor
            if node[0] in SuspensionSyntaxFrame.KINDS:
                from .execution_frames import Done
                cursor = suspension_cursor(self,node,env)
        if cursor is not None:
            value = error = None
            closing = False
            closing_error = None
            while True:
                if closing:
                    action = cursor.close_request(closing_error)
                    if type(action) is Done:
                        if not cursor.close_completed:
                            raise cursor.close_signal
                        return action.value
                    pending = action.request
                else:
                    try:
                        pending = cursor.throw(error) if error is not None else cursor.send(value)
                    except StopIteration as stop:
                        return stop.value
                try:
                    value = yield pending
                    error = None
                    closing = False
                except GeneratorExit as failure:
                    value = error = None
                    closing_error = failure
                    closing = True
                except BaseException as failure:
                    value = None
                    error = failure
                    closing = False
        from .execution_callables import CallableSyntaxFrame, callable_protocol
        if node[0] in CallableSyntaxFrame.KINDS:
            return (yield from callable_protocol(self, 'syntax', node, env))
        if node[0] in ('name', 'assign', 'class', 'decorated'):
            from .execution_class_creation import class_protocol
            return (yield from class_protocol(self, 'syntax', node, env, _compiled_regions))
        if node[0] == 'set':
            from .execution_hash_collections import hash_collection_protocol
            return (yield from hash_collection_protocol(self,'literal',node,env))
        from .execution_patterns import PatternSyntaxFrame, pattern_cursor
        if node[0] in PatternSyntaxFrame.KINDS:
            # This actual public native generator owns the protocol frontier.
            # A close that reaches another yield remains reachable on this same
            # iterator for second close, send, or throw; no outer yield-from
            # delegate is allowed to destroy that frontier on RuntimeError.
            from .execution_frames import Done
            cursor = pattern_cursor(self,node,env,tail,all_results,_compiled_regions,_flow_boundary)
            value = error = None
            closing = False
            closing_error = None
            while True:
                if closing:
                    action = cursor.close_request(closing_error)
                    if type(action) is Done:
                        if not cursor.close_completed:
                            raise cursor.close_signal
                        return action.value
                    pending = action.request
                else:
                    try:
                        pending = cursor.throw(error) if error is not None else cursor.send(value)
                    except StopIteration as stop:
                        return stop.value
                try:
                    value = yield pending
                    error = None
                    closing = False
                except GeneratorExit as failure:
                    value = error = None
                    closing_error = failure
                    closing = True
                except BaseException as failure:
                    value = None
                    error = failure
                    closing = False
        from .suspension import Await, Yield, Generator, AsyncGenerator, GeneratorClose
        from .capture_frames import active as capture_active, reset as capture_reset, retain as capture_retain
        capture=capture_active(self)
        self.tick()
        k = node[0]
        if k == 'loop_else':
            allowed = ('while', 'for', 'async_for', 'for_pattern', 'async_for_pattern')
            if len(node) != 3 or not isinstance(node[1], tuple) or not node[1] or node[1][0] not in allowed or not isinstance(node[2], tuple) or not node[2] or node[2][0] != 'block':
                raise LanguageError('正常完成分支需要对应控制结构和 else 块', code='syntax_error')
            # Never catch signals around the whole child: only its historical
            # body/catch boundaries determine a normal, broken or handled exit.
            # Re-enter _s_evaluate so the old child's trace, span, debugger and
            # scheduler checkpoints are retained. Nested calls get no collector.
            outcome = _Completion()
            value = yield from self._s_evaluate(node[1], env, tail, all_results=all_results, _completion=outcome, _compiled_regions=_compiled_regions)
            if outcome.state == 'normal':
                return (yield from self._s_evaluate(node[2], env, tail, all_results=all_results, _compiled_regions=_compiled_regions))
            if outcome.state not in ('broken', 'handled'):
                raise LanguageError('无效内部完成状态')
            return value
        if k in ('list_comp', 'record_comp', 'set_comp', 'gen_comp', 'async_gen_comp'):
            from .comprehensions import evaluate
            return (yield from evaluate(self, node, env))
        if k == 'literal':
            serializable(node[1])
            return node[1]
        if k in ('list', 'tuple'):
            if len(node[1]) > 10000:
                raise ResourceLimit('列表超过 10000 项限制')
            result = []
            for x in node[1]:
                result.append((yield from self._s_evaluate(x, env)))
            return TupleValue(result) if k == 'tuple' else result
        if k == 'record':
            if len(node[1]) > 10000:
                raise ResourceLimit('记录超过 10000 字段限制')
            result = {}
            for (key, v) in node[1]:
                result[key] = (yield from self._s_evaluate(v, env))
            return result
        if k == 'block':
            (scope, result) = (Env(env), None)
            capture_reset(capture,scope)
            for (i, statement) in enumerate(node[1]):
                result = (yield from self._s_evaluate(statement, scope, tail and i == len(node[1]) - 1, all_results=all_results and i == len(node[1])-1, _compiled_regions=_compiled_regions))
                capture_reset(capture,scope)
            return result
        if k == 'let':
            return env.bind(node[1], check_type((yield from self._s_evaluate(node[3], env, _compiled_regions=_compiled_regions)), self.contract(node[2], env)))
        if k == 'var':
            typ = self.contract(node[2], env)
            value = check_type((yield from self._s_evaluate(node[3], env, _compiled_regions=_compiled_regions)), typ)
            env.bind(node[1], Cell(value, typ))
            return value
        if k == 'return':
            raise ReturnSignal((yield from self._s_evaluate(node[1], env, True, all_results=True, _compiled_regions=_compiled_regions)))
        if k == 'break':
            raise BreakSignal()
        if k == 'continue':
            raise ContinueSignal()
        if k == 'while':
            while check_type((yield from self._s_evaluate(node[1], env, _compiled_regions=_compiled_regions)), 'Bool'):
                self.tick()
                try:
                    yield from self._s_evaluate(node[2], env, _compiled_regions=_compiled_regions)
                except BreakSignal:
                    if _completion is not None: _completion.state = 'broken'
                    break
                except ContinueSignal:
                    continue
                finally:
                    capture_reset(capture,env)
            else:
                if _completion is not None: _completion.state = 'normal'
            return None
        if k in ('for', 'async_for', 'for_pattern', 'async_for_pattern'):
            items = yield from self._s_evaluate(node[2], env)
            asynchronous = k in ('async_for', 'async_for_pattern')
            handle = yield from self.suspension.iteration.acquire(items, asynchronous)
            capture_reset(capture,env,items,handle)
            while True:
                capture_reset(capture,env,items,handle)
                if asynchronous:
                    operation = self.suspension.anext(handle)
                    try: item = yield Await(operation)
                    except BaseException:
                        if operation.state not in ('completed', 'failed', 'cancelled', 'closed'): self.suspension.cancel(operation)
                        raise
                else: item = yield from self.suspension.iteration.step(handle)
                if item['done']:
                    if _completion is not None: _completion.state = 'normal'
                    break
                self.tick()
                scope = Env(env)
                if k.endswith('_pattern'): self.bind_pattern(node[1], item['value'], scope, lookup_env=env)
                else: scope.bind(node[1], item['value'])
                try:
                    yield from self._s_evaluate(node[3], scope, _compiled_regions=_compiled_regions)
                except ContinueSignal: continue
                except BreakSignal:
                    if _completion is not None: _completion.state = 'broken'
                    break
            return None
        if k == 'data':
            names = [node[1]] + [tag for (tag, _) in node[2]]
            if any((name in env.values for name in names)):
                raise LanguageError('data 名称与当前作用域冲突')
            datatype = DataType(getattr(node, 'span', {}).get('file', 'main.chaos') + '::' + node[1], tuple(((tag, len(fields)) for (tag, fields) in node[2])))
            values = [(node[1], datatype)]

            def constructor(tag, fields):
                params = [(field, 'Any', 'positional', MISSING) for field in fields]

                def construct(*args, **kwargs):
                    bound = self.bind_arguments(tag, params, args, kwargs)
                    return {'$type': datatype.name, '$tag': tag, '$values': [bound[field] for field in fields]}
                return Native(tag, construct, self, (datatype, tag, len(fields)), captures=(datatype,))
            for (tag, fields) in node[2]:
                values.append((tag, constructor(tag, fields)))
            for (name, value) in values:
                env.bind(name, value)
            return None
        if k == 'do':
            return (yield from self._s_evaluate(node[1], env, tail, all_results=all_results))
        if k == 'if':
            cond = check_type((yield from self._s_evaluate(node[1], env, _compiled_regions=_compiled_regions)), 'Bool')
            return (yield from self._s_evaluate(node[2] if cond else node[3], env, tail, all_results=all_results, _compiled_regions=_compiled_regions))
        if k == 'unary':
            value = (yield from self._s_evaluate(node[2], env, _compiled_regions=_compiled_regions))
            if node[1] == '!': return not check_type(value, 'Bool')
            from .operators import operation
            return (yield from operation(self, {'-':'neg', '+':'pos', '~':'invert'}[node[1]], (value,)))
        if k == 'binary':
            op = node[1]
            left = (yield from self._s_evaluate(node[2], env, _compiled_regions=_compiled_regions))
            if op in ('&&', '||'):
                truth = check_type(left, 'Bool')
                if op == '&&' and (not truth) or (op == '||' and truth):
                    return truth
                result = (yield from self._s_evaluate(node[3], env, tail, all_results=True, _compiled_regions=_compiled_regions))
                if isinstance(result, TailCall):
                    result.contracts += ('Bool',)
                    result.primary_only = True
                    return result
                from .multiple_values import primary
                return check_type(primary(result,self), 'Bool')
            right = (yield from self._s_evaluate(node[3], env, _compiled_regions=_compiled_regions))
            if op == '|>':
                return TailCall(right, [left], span=dict(self.current_span), handled_error=self.handled_error,error_context=self.error_context,module_scope=getattr(node,'_module_scope',None),primary_only=not all_results) if tail else (yield from self._s_invoke(right, [left],all_results=all_results))
            from .operators import operation
            return (yield from operation(self, op, (left, right)))
        if k == 'call':
            fn = (yield from self._s_evaluate(node[1], env))
            (args, kwargs) = ([], {})
            for arg in node[2]:
                kind = arg[0]
                if kind == 'keyword':
                    value = (yield from self._s_evaluate(arg[2], env))
                    if arg[1] in kwargs:
                        raise LanguageError('重复关键字参数 ' + arg[1], code='argument_error')
                    kwargs[arg[1]] = value
                elif kind == 'kwspread':
                    values = (yield from self._s_evaluate(arg[1], env))
                    if not isinstance(values, dict):
                        raise LanguageError('** 展开需要 Record', code='argument_error')
                    if not all((isinstance(key, str) for key in values)):
                        raise LanguageError('关键字名称必须为 String', code='argument_error')
                    if kwargs.keys() & values.keys():
                        raise LanguageError('重复展开关键字参数', code='argument_error')
                    if len(args) + len(kwargs) + len(values) > 10000:
                        raise ResourceLimit('调用参数超过 10000 项限制')
                    kwargs.update(values)
                elif kind == 'spread':
                    values = (yield from self._s_evaluate(arg[1], env))
                    from .iteration import IterationRequest
                    values = yield from self.suspension.iteration.resolve(
                        IterationRequest('expand', values, 10000 - len(args) - len(kwargs)))
                    args.extend(values)
                else:
                    args.append((yield from self._s_evaluate(arg, env)))
                self.argument_budget(args, kwargs)
            return TailCall(fn, args, kwargs=kwargs, span=dict(self.current_span), handled_error=self.handled_error,error_context=self.error_context,module_scope=getattr(node,'_module_scope',None),primary_only=not all_results) if tail else (yield from self._s_invoke(fn, args, kwargs,all_results=all_results))
        raise LanguageError(f'未知语法节点 {k}')

    def _s_access_result(self,value,protocol_depth=0):
        from .execution_access import access_protocol
        return access_protocol(self,'access',value,protocol_depth)


    def _s_item_key(self,node,env):
        from .execution_access import access_protocol
        return access_protocol(self,'item_key',node,env)


    def _s_index_value(self,value,index):
        from .execution_access import access_protocol
        return access_protocol(self,'legacy_index',value,index)


    def _s_get_member(self, target, name):
        from .attribute_protocols import read
        return (yield from read(self,target,name))

    def _s_default_get_member(self, target, name):
        from .attribute_protocols import read
        return (yield from read(self,target,name,default=True))

    def _s_scoped_get_member(self,target,name):
        from .execution_access import access_protocol
        return access_protocol(self,'scoped_get',target,name)


    def _s_set_member(self, target, name, item):
        if type(target) is Bot:
            from .bot_values import owned as bot_owned
            bot_owned(target,self)
            raise LanguageError('Bot has no writable members',code='attribute_error')
        from .attribute_protocols import write
        return (yield from write(self,target,name,item))

    def _s_default_set_member(self, target, name, item):
        from .attribute_protocols import write
        return (yield from write(self,target,name,item,default=True))

    def _s_scoped_set_member(self,target,name,item):
        from .execution_access import access_protocol
        return access_protocol(self,'scoped_set',target,name,item)


    def _s_delete_member(self, target, name):
        if type(target) is Bot:
            from .bot_values import owned as bot_owned
            bot_owned(target,self)
            raise LanguageError('Bot has no writable members',code='attribute_error')
        from .attribute_protocols import write
        return (yield from write(self,target,name,delete=True))

    def _s_default_delete_member(self, target, name):
        from .attribute_protocols import write
        return (yield from write(self,target,name,delete=True,default=True))

    def _s_scoped_delete_member(self,target,name):
        from .execution_access import access_protocol
        return access_protocol(self,'scoped_delete',target,name)


    def _s_decorated(self, node, env, method_closure=None):
        from .execution_class_creation import class_protocol
        return (yield from class_protocol(self, 'decorated', node, env, method_closure))

    def _s_class(self, node, env, publish=True):
        from .execution_class_creation import class_protocol
        return (yield from class_protocol(self, 'declaration', node, env, publish))

    def object_builtins(self):
        from .objects import ChaosClass, ChaosInstance, UIArtifact, SuperProxy, AccessRequest, StaticMethod, ClassMethod, owned, require_callable, make_property
        def require_class(value):
            owned(value, self)
            if not isinstance(value, ChaosClass): raise LanguageError('需要 Class', code='argument_error')
            return value
        def nominal_instance(value, klass):
            require_class(klass); owned(value, self)
            actual = value.metaclass if type(value) is ChaosClass else value.klass if type(value) is ChaosInstance else None
            return actual is not None and any(base is klass for base in actual.mro)
        def nominal_subclass(child, base):
            require_class(child); require_class(base)
            return any(item is base for item in child.mro)
        def instance_of(value, klass):
            from .metaclasses import TypeCheckRequest
            return TypeCheckRequest('instance', value, klass)
        def subclass_of(child, base):
            from .metaclasses import TypeCheckRequest
            return TypeCheckRequest('subclass', child, base)
        def class_of(value):
            owned(value, self)
            from .class_creation import project_value
            if type(value) is ChaosClass: result = value.metaclass
            elif type(value) is ChaosInstance: result = value.klass
            else: raise LanguageError('class_of 需要 Class 或 Instance', code='argument_error')
            return project_value(self, result, value)
        def super_value(klass, instance):
            require_class(klass)
            meta_access = type(instance) is ChaosClass and nominal_instance(instance, klass)
            valid = meta_access or (nominal_subclass(instance, klass) if isinstance(instance, ChaosClass) else nominal_instance(instance, klass))
            if not valid: raise LanguageError('super 的接收者不属于该类', code='argument_error')
            return SuperProxy(klass, instance, self, metaclass_access=meta_access)
        def get_attribute(value, name): return AccessRequest('get', value, name)
        def set_attribute(value, name, item): return AccessRequest('set', value, name, item)
        def has_attribute(value, name): return AccessRequest('has', value, name)
        def delete_attribute(value, name): return AccessRequest('delete', value, name)
        def static_method(value): return StaticMethod(require_callable(value, self), self)
        def class_method(value): return ClassMethod(require_callable(value, self), self)
        def property_value(fget=None, fset=None, fdel=None): return make_property(fget, fset, self, fdel=fdel)
        from .class_creation import ClassRequest, member_value as create_member, snapshot_members, allocate
        def new_class(name, bases=[], members={}, options={}): return ClassRequest(name, bases, members, options)
        def class_member(value, *, mutable=False, method=False, contract='Any'):
            return create_member(self, value, mutable=mutable, method=method, contract=contract)
        def class_members(klass): return snapshot_members(self, klass)
        def alloc_instance(klass): return allocate(self, klass)
        def variables(value):
            from .objects import member_value
            from .class_creation import project_value
            owned(value, self)
            if type(value) is UIArtifact: return value.definition
            if isinstance(value, ChaosClass):
                result = {name:owned(member_value(member), self) for name,member in value.members.items()}
            elif isinstance(value, ChaosInstance):
                result = {name:owned(cell.value, self) for name,cell in value.fields.items()}
            else: raise LanguageError('vars 需要 Class 或 Instance', code='argument_error')
            serializable(result)
            return project_value(self, result, value, value.klass if isinstance(value, ChaosInstance) else value)
        def directory(value):return AccessRequest('dir',value,'')
        def default_directory(value):
            if type(value) is Bot:
                from .bot_values import owned as bot_owned
                bot_owned(value,self);return []
            if type(value) in ROBOT_VALUES:
                from .robot_values import owned as robot_owned,info as robot_info
                return sorted(robot_info(robot_owned(value,self)))
            owned(value, self)
            if type(value) is PromptTag:return ["id","name"]
            if type(value) is PromptScope:return ["active","id","state","tag"]
            if type(value) is EscapeContinuation:return ["active","available","id","scopeId","state"]
            if type(value) is Continuation:return ["available","frames","id","invocations","state"]
            from .conditions import VALUES as RECOVERY_VALUES,describe as recovery_describe
            if type(value) in CONTEXT_VALUES:
                from .dynamic_contexts import describe
                extra=['get','set','reset'] if type(value) is ContextVar else ['get','has','copy','run','keys','values','items'] if type(value) is Context else ['var','old_value'] if type(value) is ContextToken else []
                return sorted([key for key in describe(value) if key!='$handle']+extra)
            if type(value) is Error:
                from .native_specs import SHAPES
                return [field.name for field in SHAPES['ErrorFields']]
            if type(value) is Condition:return ['data','kind','message']
            if type(value) in RECOVERY_VALUES:return sorted(key for key in recovery_describe(value) if key!='$handle')
            if type(value) is DecimalContext:
                from .decimal_values import owned as decimal_owned,info
                return sorted(info(decimal_owned(value,self)))
            if type(value) in (Fraction,Complex,Decimal): return ['sign','coefficient','exponent'] if type(value) is Decimal else ['numerator','denominator'] if type(value) is Fraction else ['real','imag']
            if type(value) is UIArtifact: return sorted(value.definition)
            if isinstance(value, ChaosClass): chain, names = value.mro, set()
            elif isinstance(value, ChaosInstance): chain, names = value.klass.mro, set(value.fields)
            elif isinstance(value, SuperProxy):
                chain = value.receiver.mro if isinstance(value.receiver, ChaosClass) else value.receiver.klass.mro
                position = next(i for i,item in enumerate(chain) if item is value.start_class)
                chain, names = chain[position+1:], set()
            else: raise LanguageError('dir 需要对象', code='argument_error')
            for klass in chain:
                self.tick(); names.update(klass.members)
                if len(names) > 10000: raise ResourceLimit('对象成员超过 10000 项')
            return sorted(names)
        self._attribute_default_directory=default_directory
        return {'default_getattr':lambda value,name:AccessRequest('default_get',value,name),
                'default_setattr':lambda value,name,item:AccessRequest('default_set',value,name,item),
                'default_delattr':lambda value,name:AccessRequest('default_delete',value,name),
                'default_dir':lambda value:AccessRequest('default_dir',value,''),
                'isinstance':instance_of,'issubclass':subclass_of,'class_of':class_of,
                'new_class':new_class,'class_member':class_member,'class_members':class_members,'alloc_instance':alloc_instance,
                'super':super_value,'getattr':get_attribute,'setattr':set_attribute,'delattr':delete_attribute,'hasattr':has_attribute,'dir':directory,'vars':variables,
                'staticmethod':static_method,'classmethod':class_method,'property':property_value}

    @staticmethod
    def argument_budget(args, kwargs):
        if len(args) + len(kwargs) > 10000: raise ResourceLimit('调用参数超过 10000 项限制')

    @staticmethod
    def bind_arguments(name, params, args, kwargs):
        """Bind everything before evaluating a function body or constructor."""
        bound, remaining, index = {}, dict(kwargs), 0
        for param in params:
            key, typ, mode, default = param[:4]
            if mode == 'varargs':
                value = list(args[index:]); index = len(args)
            elif mode == 'varkw':
                value = remaining; remaining = {}
            elif mode in ('positional', 'positional_only') and index < len(args):
                if mode == 'positional' and key in remaining: raise LanguageError('参数重复赋值 '+key, code='argument_error')
                value = args[index]; index += 1
            elif mode != 'positional_only' and key in remaining:
                value = remaining.pop(key)
            elif default is not MISSING:
                value = default
            else:
                raise LanguageError(name+' 缺少参数 '+key, code='argument_error')
            bound[key] = check_type(value, typ)
        if index != len(args): raise LanguageError(name+' 收到过多位置参数', code='argument_error')
        if remaining: raise LanguageError(name+' 收到未知关键字 '+next(iter(remaining)), code='argument_error')
        return bound

    def _s_invoke(self, fn, args, kwargs=None, *, span=None, contracts=(), continuations=(), force=False, synchronous=False, bound_parameters=None, handled_error=MISSING, error_context=MISSING, _recovery_callback=False, _context_callback=False, all_results=False, primary_only=False, _normalized=False):
        from .execution_frames import gateway,InvocationRequest
        options={'span':span,'contracts':contracts,'continuations':continuations,'force':force,'synchronous':synchronous,'bound_parameters':bound_parameters,'handled_error':handled_error,'error_context':error_context,'_recovery_callback':_recovery_callback,'_context_callback':_context_callback,'all_results':all_results,'primary_only':primary_only}
        return gateway(self,InvocationRequest(fn,args,kwargs,options),normalized=_normalized)

    def _frame_invocation_enter(self, state):
        from .capture_frames import enter as capture_enter, leave as capture_leave, reset as capture_reset, retain as capture_retain, Edge as CaptureEdge
        state.capture = capture_enter(self, 'invoke', state.fn, state.args, state.kwargs, state.bound_parameters)
        state.capture_result = MISSING
        from .suspension import Effect
        from .agent_tools import BoundAgent, call as bound_agent_call
        from .team_bindings import BoundTeam, RoleRequest, call as bound_team_call
        from .objects import ChaosClass, ChaosInstance, BoundMethod, StaticMethod, AccessRequest, special_method, declaration, owned
        state.kwargs = {} if state.kwargs is None else state.kwargs
        state.previous_span = self.current_span
        state.previous_error, state.previous_context = (self.handled_error, self.error_context)
        if state.error_context is not MISSING:
            self.error_context = state.error_context
        if state.handled_error is not MISSING:
            self.handled_error = state.handled_error
        state.authority_runner = self.suspension.current
        state.previous_authority = state.authority_runner.capabilities if state.authority_runner is not None else None
        state.previous_module = getattr(state.authority_runner, 'module_scope', None) if state.authority_runner is not None else None
        state.call_span = dict(state.previous_span if state.span is None else state.span)
        state.required = {typ: state.call_span for typ in state.contracts}
        state.pending = []
        state.counted_depth = False
        state.debug_frame = None
        if state.continuations:
            for target, location in reversed(state.continuations):
                state.pending.append((target, location, state.required, state.authority_runner.capabilities if state.authority_runner is not None else None, self.handled_error, self.error_context, getattr(state.authority_runner, 'module_scope', None), state.primary_only))
                state.required = {}
                state.primary_only = False

    def _frame_invocation_dispatch(self, state):
        from .execution_invocation import dispatch_protocol
        return dispatch_protocol(self, state)


    def _frame_invocation_reduce(self, state):
        from .execution_frames import DispatchAgain
        if isinstance(state.value, TailCall):
            state.primary_only = state.primary_only or state.value.primary_only
            if state.value.ordinary_call:
                state.force = False
                state._context_callback = False
                state._recovery_callback = False
            if state.authority_runner is not None and state.value.module_scope is not MISSING:
                state.authority_runner.module_scope = state.value.module_scope
            if state.value.handled_error is not MISSING:
                self.handled_error = state.value.handled_error
            if state.value.error_context is not MISSING:
                self.error_context = state.value.error_context
            for typ in state.value.contracts:
                state.required.setdefault(typ, state.value.span or state.call_span)
            if state.value.continuations:
                for target, location in reversed(state.value.continuations):
                    state.pending.append((target, location, state.required, state.authority_runner.capabilities if state.authority_runner is not None else None, self.handled_error, self.error_context, getattr(state.authority_runner, 'module_scope', None), state.primary_only))
                    state.required = {}
                    state.primary_only = False
            state.fn, state.args, state.kwargs = (state.value.function, state.value.args, state.value.kwargs or {})
            state.call_span = dict(state.value.span or state.call_span)
            return DispatchAgain()
        from .multiple_values import primary, prepare, Completion
        is_completion = type(state.value) is Completion
        from .robot_values import validate_embedded as validate_robot_result
        validate_robot_result(self, state.value)
        from .bot_values import validate_embedded as validate_bot_result
        validate_bot_result(self,state.value)
        state.value = prepare(self, state.value)
        from .agent_tools import current_caps
        if not is_completion and current_caps(self) is not None:
            from .class_creation import _project_return
            state.value = _project_return(self, state.value, current_caps(self))
        for typ, location in state.required.items():
            try:
                check_type(primary(state.value, self), typ)
            except LanguageError as error:
                raise error.attach(location)
        if state.primary_only:
            state.value = primary(state.value, self)
        if state.pending:
            state.fn, state.call_span, state.required, state.restore_authority, self.handled_error, self.error_context, state.restore_module, state.primary_only = state.pending.pop()
            if state.authority_runner is not None:
                state.authority_runner.capabilities = state.restore_authority
                state.authority_runner.module_scope = state.restore_module
            state.args, state.kwargs = ([primary(state.value, self)], {})
            return DispatchAgain()
        state.capture_result = state.value if state.all_results else primary(state.value, self)
        return state.capture_result

    def _frame_invocation_leave(self, state):
        from .capture_frames import leave as capture_leave
        if state.debug_frame is not None and state.debug_frame in self.frames:
            self.frames.remove(state.debug_frame)
        if state.counted_depth:
            self.depth -= 1
        self.current_span = state.previous_span
        self.handled_error, self.error_context = (state.previous_error, state.previous_context)
        if state.authority_runner is not None:
            state.authority_runner.capabilities = state.previous_authority
            state.authority_runner.module_scope = state.previous_module
        capture_leave(state.capture, state.capture_result)

    def _frame_invocation_error(self, error):
        """Observe a retired invocation failure before its caller handles it.

        Ordinary execution adds no policy. Specialized hosts can retain audit
        failure without changing whether language-level catch is permitted.
        """

    def builtins(self):
        env = Env()

        def sequence(value):
            check_type(value, 'List')
            if len(value) > 10000:
                raise ResourceLimit('列表超过 10000 项限制')
            return value

        def range_fn(start, stop=None, step=1):
            if stop is None:
                start, stop = 0, start
            check_type(start, 'Int'); check_type(stop, 'Int'); check_type(step, 'Int')
            if step == 0: raise LanguageError('range step 不能为 0')
            values = range(start, stop, step)
            count = max(0, (stop-start+(step-1 if step>0 else step+1))//step)
            if count > 10000: raise ResourceLimit('range 超过 10000 项限制')
            return list(values)

        def fold(fn, initial, items):
            return IterationRequest('fold', items, (fn, initial))

        def effect(name, value):
            check_type(name, 'String')
            from .agent_tools import current_caps
            capabilities=current_caps(self)
            if capabilities is not None and name not in capabilities:
                raise LanguageError('Agent tool capability denied: '+name,code='capability_error')
            if not self.tool_handler:
                raise LanguageError('当前纯函数环境不允许工具效果')
            if self.suspension.current and self.suspension.current.task:
                return self.suspension.effect('tool', {'name':name, 'value':serializable(self.host_value(value))})
            try: result = self.tool_handler(name, self.host_value(value))
            except (LanguageError, ExecutionControl): raise
            except Exception as error: raise LanguageError(str(error), code='tool_error') from None
            return self.copy_value(result)

        def join(sep, xs):
            check_type(sep, 'String'); sequence(xs)
            if not all(isinstance(x, str) for x in xs):
                raise LanguageError('join 需要字符串列表')
            if sum(len(x) for x in xs) + len(sep) * max(0, len(xs) - 1) > 250000:
                raise ResourceLimit('join 输出超过资源限制')
            return sep.join(xs)

        def extrema(xs, fn):
            from functools import cmp_to_key
            from .numeric_values import compare
            values = sequence(xs)
            for x in values: self.tick(); check_type(x,'Real')
            return fn(values,key=cmp_to_key(lambda a,b:compare(a,b,self.tick)))

        def sort_values(xs):
            xs = sequence(xs)
            for value in xs: self.tick()
            if xs and not (all(is_real(v) for v in xs) or all(type(v) is str for v in xs)):
                raise LanguageError('sort 需要数值或字符串列表')
            if xs and not all(type(v) is str for v in xs):
                from functools import cmp_to_key
                from .numeric_values import compare
                return sorted(xs,key=cmp_to_key(lambda a,b:compare(a,b,self.tick)))
            return sorted(xs)

        from .operators import OperationRequest, NOT_IMPLEMENTED
        def power(base, exponent): return OperationRequest('pow', (base, exponent))
        def floor_divide(left, right): return OperationRequest('floordiv', (left, right))
        def boolean(value): return OperationRequest('bool', (value,))

        def json_stringify(value):
            serializable(value)
            from .indexing import validate_owned
            validate_owned(self, value)
            def encode(item):
                if type(item) in (PromptTag,PromptScope,EscapeContinuation,Continuation):
                    from .control_prompts import describe,owned as control_owned
                    return describe(control_owned(item,self))
                if type(item) is EqlSpecializer:
                    from .eql_specializers import describe as eql_describe
                    return eql_describe(item)
                if type(item) is Contract:
                    from .contracts import owned as contract_owned, describe as describe_contract
                    return describe_contract(contract_owned(item,self))
                if type(item) is HashMap:
                    from .hash_maps import describe as describe_hash_map
                    return describe_hash_map(item)
                from .conditions import VALUES as RECOVERY_VALUES,owned as recovery_owned,describe as recovery_describe
                if type(item) is Condition:
                    from .condition_values import describe as condition_describe
                    return encode(condition_describe(item))
                if type(item) in CONTEXT_VALUES:
                    from .dynamic_contexts import owned as binding_owned,describe as binding_describe
                    return binding_describe(binding_owned(item,self))
                if type(item) in RECOVERY_VALUES:return recovery_describe(recovery_owned(item,self))
                if type(item) is DecimalContext:
                    from .decimal_values import info,owned as decimal_owned
                    return {'$decimalContext':info(decimal_owned(item,self))}
                if type(item) in (Fraction,Complex,Decimal): return numeric_encode(item)
                from .objects import UIArtifact
                from .session_tools import BoundSession,owned as session_binding_owned,binding_info as session_binding_info
                if type(item) is UIArtifact:
                    from .ui_artifacts import owned as ui_owned, describe as ui_describe
                    return ui_describe(ui_owned(item, self))
                if type(item)is BoundSession:return {'$boundSession':session_binding_info(session_binding_owned(item,self))}
                from .team_bindings import BoundTeam,owned as bound_team_owned,binding_info as team_binding_info
                if type(item)is BoundTeam:return {'$boundTeam':team_binding_info(bound_team_owned(item,self))}
                if type(item) is Bot:
                    from .bot_values import owned as bot_owned,describe as bot_describe
                    return bot_describe(bot_owned(item,self))
                from .channel_select import ChannelCase, owned as case_owned, describe as describe_case
                if type(item) is ChannelCase: return describe_case(case_owned(item, self))
                from .device_values import VALUES as DEVICE_VALUES, owned as device_owned, describe as describe_device
                from .computer_values import VALUES as COMPUTER_VALUES, owned as computer_owned, describe as describe_computer
                if type(item) in ROBOT_VALUES:
                    from .robot_values import owned as robot_owned,describe as robot_describe
                    return robot_describe(robot_owned(item,self))
                if type(item) in DEVICE_VALUES:return describe_device(device_owned(item,self))
                if type(item) in COMPUTER_VALUES:return describe_computer(computer_owned(item,self))
                from .session_values import AgentSession, describe as describe_session
                if type(item) is AgentSession: return describe_session(item)
                from .process_values import Process, ProcessEvolution, describe as describe_process, evolution_owned, describe_evolution
                if type(item) is Process: return describe_process(item)
                if type(item) is ProcessEvolution: return describe_evolution(evolution_owned(item,self))
                from .program_values import Program, describe as describe_program
                if type(item) is Program: return describe_program(item)
                from .team_values import Team, describe as describe_team
                if type(item) is Team: return describe_team(item)
                from .modes import ModeValue, describe as describe_mode
                if isinstance(item, ModeValue): return describe_mode(item)
                from .indexing import Slice, describe as describe_slice
                if type(item) is Slice: return describe_slice(item)
                if item is NOT_IMPLEMENTED: return {'$notImplemented': True}
                if type(item) is SetValue: return serializable(item)
                if type(item) is TupleValue:
                    # Ownership is checked before projecting any live children.
                    return serializable(self.host_value(item))
                if isinstance(item,list): return [encode(child) for child in item]
                if isinstance(item,dict): return {key:encode(child) for key,child in item.items()}
                return item
            return json.dumps(encode(value),ensure_ascii=False,allow_nan=False)

        def copy_json(value):
            # Unlike display serialization, interchange never converts a live
            # handle to an inert marker. Charge traversal to the caller budget.
            from .extensions import json_copy
            try:
                copied = json_copy(value, max_depth=32, checkpoint=self.tick)
                if len(json.dumps(copied, ensure_ascii=False, allow_nan=False)) > 250000:
                    raise ResourceLimit('JSON copy exceeds serialized size budget')
                return copied
            except (ValueError, TypeError, OverflowError, UnicodeError) as error:
                raise LanguageError('json_copy requires finite bounded JSON: ' + str(error), code='argument_error') from None

        def parse_json(text):
            check_type(text, 'String')
            value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(LanguageError('JSON 不能包含非有限数')))
            return self.copy_value(value)

        def slice_value(value, start, stop=None):
            if not isinstance(value, (str, list, TupleValue)): raise LanguageError('slice 需要 String、List 或 Tuple')
            check_type(start, 'Int')
            if stop is not None: check_type(stop, 'Int')
            return value[start:stop]

        def replace(text, old, new):
            for v in (text,old,new): check_type(v, 'String')
            count = text.count(old) if old else len(text)+1
            if len(text) + count * (len(new)-len(old)) > 250000: raise ResourceLimit('replace 输出超过资源限制')
            return text.replace(old, new)

        def flatten(xs):
            result = []
            for sub in sequence(xs):
                self.tick(); sequence(sub)
                if len(result)+len(sub) > 10000: raise ResourceLimit('flatten 超过 10000 项')
                result.extend(sub)
            return result

        def contains(container, item): return OperationRequest('contains', (container, item))

        def remove(obj,key):
            check_type(obj,'Record');check_type(key,'String')
            return {k:v for k,v in obj.items()if k!=key}

        def workflow(graph, value):
            if self.workflow_handler is None: raise LanguageError('当前上下文未授予工作流效果')
            if self.suspension.current and self.suspension.current.task:
                return self.suspension.effect('workflow', {'graph':serializable(self.host_value(graph)), 'value':serializable(self.host_value(value))})
            return self.copy_value(self.workflow_handler(self.host_value(graph),self.host_value(value)))

        def type_of(value):
            if type(value) in ROBOT_VALUES:
                from .robot_values import owned as robot_owned
                return type(robot_owned(value,self)).__name__
            if type(value) in (PromptTag,PromptScope,EscapeContinuation,Continuation):
                from .control_prompts import owned as control_owned
                return type(control_owned(value,self)).__name__
            if type(value) is EqlSpecializer:
                from .eql_specializers import validate as eql_validate
                eql_validate(self,value);return 'EqlSpecializer'
            if type(value) is Contract:
                from .contracts import owned as contract_owned
                contract_owned(value,self); return 'Contract'
            if type(value) is HashMap:
                from .hash_maps import owned as hash_map_owned
                hash_map_owned(value,self);return 'HashMap'
            from .code_values import Code,owned as code_owned
            if type(value) is Code:code_owned(value,self);return "Code"
            from .conditions import VALUES as RECOVERY_VALUES,owned as recovery_owned
            if type(value) is Condition:
                from .condition_values import validate as condition_validate
                condition_validate(value,self.tick);return 'Condition'
            if type(value) in CONTEXT_VALUES:
                from .dynamic_contexts import owned as binding_owned
                return type(binding_owned(value,self)).__name__
            if type(value) in RECOVERY_VALUES:return type(recovery_owned(value,self)).__name__
            from .team_bindings import BoundTeam,owned as bound_team_owned
            if type(value)is BoundTeam:bound_team_owned(value,self);return 'Function'
            if type(value) is Bot:
                from .bot_values import owned as bot_owned
                bot_owned(value,self);return 'Bot'
            from .session_tools import BoundSession,owned as session_binding_owned
            if type(value)is BoundSession:session_binding_owned(value,self);return 'Function'
            from .agent_tools import AgentTool, BoundAgent, owned as binding_owned
            if type(value) in (AgentTool,BoundAgent):
                binding_owned(value,self)
                return 'Function' if type(value) is BoundAgent else 'AgentTool'
            from .resource_stacks import STACKS, owned as stack_owned
            if type(value) in STACKS: return type(stack_owned(value, self)).__name__
            from .contexts import MANAGERS, context_owned
            if type(value) in MANAGERS: return type(context_owned(value, self)).__name__
            from .channel_select import ChannelCase, owned as case_owned
            if type(value) is ChannelCase: return type(case_owned(value, self)).__name__
            from .channels import Channel, owned as channel_owned
            if type(value) is Channel: return type(channel_owned(value, self)).__name__
            from .cancellation import CancelShield, owned as shield_owned
            if type(value) is CancelShield: return type(shield_owned(value, self)).__name__
            from .task_groups import TaskGroup, owned as group_owned
            if type(value) is Error:
                from .error_values import owned as error_owned
                error_owned(value, self); return 'Error'
            if type(value) is TaskGroup: return type(group_owned(value, self)).__name__
            from .device_values import VALUES as DEVICE_VALUES, owned as device_owned, describe as describe_device
            from .computer_values import VALUES as COMPUTER_VALUES, owned as computer_owned
            if type(value) in DEVICE_VALUES:return type(device_owned(value,self)).__name__
            if type(value) in COMPUTER_VALUES:return type(computer_owned(value,self)).__name__
            from .session_values import AgentSession, owned as session_owned
            if type(value) is AgentSession: return type(session_owned(value,self)).__name__
            from .process_values import Process, ProcessEvolution, owned as process_owned, evolution_owned
            if type(value) is Process: return type(process_owned(value,self)).__name__
            if type(value) is ProcessEvolution: return type(evolution_owned(value,self)).__name__
            from .program_values import Program, owned as program_owned
            if type(value) is Program: return type(program_owned(value,self)).__name__
            from .team_values import Team, owned as team_owned
            if type(value) is Team: return type(team_owned(value,self)).__name__
            from .modes import ModeValue, owned as mode_owned
            if isinstance(value, ModeValue): return type(mode_owned(value,self)).__name__
            from .indexing import Slice
            if type(value) is Slice: return 'Slice'
            if value is NOT_IMPLEMENTED: return 'NotImplemented'
            from .objects import ChaosClass, ChaosInstance, ClassMember, UIArtifact, BoundMethod, SuperProxy, DESCRIPTORS, owned
            owned(value, self)
            if isinstance(value, DESCRIPTORS): return 'Descriptor'
            if isinstance(value, ChaosClass): return 'Class'
            if type(value) is ClassMember: return 'ClassMember'
            if type(value) is UIArtifact: return 'UIArtifact'
            if isinstance(value, ChaosInstance): return 'Instance'
            if isinstance(value, BoundMethod): return 'Function'
            if isinstance(value, SuperProxy): return 'Super'
            from .suspension import Task, Generator, AsyncGenerator
            from .iteration import HANDLES
            if isinstance(value, (Task, Generator)+HANDLES): return type(value).__name__
            if value is None: return 'Null'
            if isinstance(value, DataType): return 'Type'
            if isinstance(value, (Function,Native,Agent)): return 'Function'
            if type(value) is DecimalContext:
                from .decimal_values import owned as decimal_owned
                decimal_owned(value,self)
                return 'DecimalContext'
            return {Decimal:'Decimal',Fraction:'Fraction',Complex:'Complex',bool:'Bool',int:'Int',float:'Number',str:'String',list:'List',dict:'Record',TupleValue:'Tuple',SetValue:'Set'}[type(value)]

        def apply(fn, args, kwargs=None):
            kwargs = {} if kwargs is None else check_type(kwargs, 'Record')
            self.argument_budget([], kwargs)
            return IterationRequest('apply', args, (fn, dict(kwargs), dict(self.current_span)))

        def partial(fn, /, *bound, **keywords):
            def call(*args, **kwargs):
                merged = dict(keywords); merged.update(kwargs)
                self.argument_budget(bound + args, merged)
                return TailCall(fn, list(bound + args), kwargs=merged, span=dict(self.current_span),module_scope=getattr(self.suspension.current,'module_scope',None))
            return Native('partial', call,self, captures=(fn,) + bound + tuple(keywords.values()))

        def compose(f, g):
            def call(*args, **kwargs):
                location = dict(self.current_span)
                return TailCall(g, list(args), kwargs=kwargs, span=location, continuations=((f, location),),module_scope=getattr(self.suspension.current,'module_scope',None))
            return Native('compose', call,self, captures=(f, g))

        from .iteration import IterationRequest
        from .indexing import ItemRequest, IndexRequest
        from .task_groups import create as create_group, info as group_info
        from .cancellation import create as create_shield, info as shield_info
        from .contexts import generator_context
        from .resource_stacks import create as create_exit_stack
        from .channel_select import send_case, receive_case, case_info, select as channel_select
        from .channels import create as create_channel, send as channel_send, receive as channel_receive, close as channel_close, info as channel_info
        from .numeric_values import make_fraction, conjugate, to_number, rounding
        from . import decimal_values as dv
        def str_value(x):
            value=x
            from .indexing import validate_owned
            validate_owned(self, value)
            # Retain legacy generic formatting, but newly live decimal handles
            # must not bypass their owner through nested text serialization.
            from .decimal_values import owned as decimal_owned
            pending=[value];seen=set()
            while pending:
                item=pending.pop()
                if id(item) in seen:continue
                seen.add(id(item));self.tick()
                from .conditions import VALUES as RECOVERY_VALUES,owned as recovery_owned
                if type(item) in CONTEXT_VALUES:
                    from .dynamic_contexts import owned as binding_owned
                    binding_owned(item,self)
                if type(item) in RECOVERY_VALUES:recovery_owned(item,self)
                elif type(item) is Condition:
                    from .condition_values import validate as condition_validate
                    condition_validate(item,self.tick)
                elif type(item) is DecimalContext:decimal_owned(item,self)
                elif type(item) is HashMap:
                    from .hash_maps import children as hash_map_children
                    pending.extend(hash_map_children(item))
                elif type(item) in (list,dict,TupleValue,SetValue):
                    pending.extend(item.values() if type(item) is dict else item)
                if len(seen)>20000:raise ResourceLimit('str 值超过资源限制')
            return value if isinstance(value,str) else json.dumps(serializable(value),ensure_ascii=False)

        def number_value(s):
            if type(s) is str:
                result=float(s)
                if not math.isfinite(result): raise LanguageError('不支持非有限数值')
                return result
            return to_number(s)
        def add_numeric(a,b):
            from .operators import primitive
            check_type(a,'Numeric');check_type(b,'Numeric')
            return primitive('+',a,b,self.numeric_checkpoint)
        functions = {
            'decimal':lambda value=0:dv.make(value,self.numeric_checkpoint),
            'decimal_text':dv.text,
            'decimal_context':lambda precision=28,rounding='half_even',emin=-999,emax=999,clamp=False,traps=list(dv.DEFAULT_TRAPS):dv.create(self,precision,rounding,emin,emax,clamp,traps),
            'get_decimal_context':lambda:dv.current(self),
            'decimal_context_info':lambda context=None:dv.context_info(self,context),
            'decimal_clear_flags':lambda context=None:dv.clear(self,context),
            'decimal_quantize':lambda value,quantum:dv.quantize(value,quantum,self.numeric_checkpoint),
            'fraction':lambda numerator=0,denominator=1:make_fraction(numerator,denominator,self.tick),
            'complex':lambda real=0,imag=0:Complex(real,imag),
            'conjugate':lambda value:conjugate(value,self.numeric_checkpoint),
            'numeric_encode':numeric_encode,'numeric_decode':lambda value:numeric_decode(value,self.numeric_checkpoint),
            'tuple': lambda items=[]: IterationRequest('tuple', items),
            'set_of': lambda items=[]: IterationRequest('set', items),
            'set_add': lambda value,item: SetRequest('add',value,item),
            'set_discard': lambda value,item: SetRequest('discard',value,item),
            'set_is_disjoint': lambda left,right: SetRequest('disjoint',left,right),
            'map': lambda fn, xs: IterationRequest('map', xs, fn),
            'filter': lambda fn, xs: IterationRequest('filter', xs, fn),
            'fold': fold, 'range': range_fn, 'length': lambda x: OperationRequest('length', (x,)),
            'head': lambda xs: sequence(xs)[0], 'tail': lambda xs: sequence(xs)[1:],
            'append': lambda xs, x: sequence(xs) + [x],
            'get': lambda obj, key, default=None: check_type(obj, 'Record').get(check_type(key, 'String'), default),
            'set': lambda obj, key, value: dict(check_type(obj, 'Record'), **{check_type(key, 'String'): value}),
            'keys': lambda obj: list(check_type(obj, 'Record').keys()),
            'upper': lambda s: check_type(s, 'String').upper(), 'lower': lambda s: check_type(s, 'String').lower(),
            'trim': lambda s: check_type(s, 'String').strip(),
            'split': lambda s, sep: check_type(s, 'String').split(check_type(sep, 'String')),
            'join': join,
            'str': str_value,
            'number': number_value, 'abs': lambda x: OperationRequest('abs', (x,)),
            'min': lambda xs: extrema(xs, min), 'max': lambda xs: extrema(xs, max),
            'sum': lambda xs: fold(Native('+', add_numeric), 0, sequence(xs)),
            'compose': compose, 'partial': partial, 'apply': apply,
            'reverse': lambda xs: list(reversed(sequence(xs))), 'sort': sort_values,
            'zip': lambda xs, ys: [list(v) for v in zip(sequence(xs),sequence(ys))], 'flatten': flatten,
            'any': lambda f,xs: IterationRequest('any', xs, f),
            'all': lambda f,xs: IterationRequest('all', xs, f),
            'take': lambda n,xs: sequence(xs)[:max(0,check_type(n,'Int'))],
            'drop': lambda n,xs: sequence(xs)[max(0,check_type(n,'Int')):],
            'slice': slice_value, 'contains': contains, 'replace': replace,
            'values': lambda obj: list(check_type(obj,'Record').values()),
            'has': lambda obj,key: check_type(key,'String') in check_type(obj,'Record'),
            'merge': lambda a,b: {**check_type(a,'Record'),**check_type(b,'Record')},
            'remove': remove,
            'floor': lambda x: rounding(x,'floor'), 'ceil': lambda x: rounding(x,'ceil'),
            'sqrt': lambda x: dv.sqrt(x,self.numeric_checkpoint) if type(x) is Decimal else math.sqrt(to_number(check_type(x,'Real'))), 'pow': power, 'floordiv': floor_divide, 'bool': boolean,
            'round': lambda x: rounding(x,'round'), 'parse_json': parse_json, 'json_stringify': json_stringify, 'json_copy': copy_json,
            'type_of': type_of,
            'tool': effect, 'workflow': workflow,
            'getitem': lambda value,key: ItemRequest('get', value, key),
            'setitem': lambda value,key,item: ItemRequest('set', value, key, item),
            'delitem': lambda value,key: ItemRequest('delete', value, key),
            'index': lambda value: IndexRequest('index', (value,)),
            'slice_key': lambda start=None,stop=None,step=None: IndexRequest('slice', (start,stop,step)),
            'slice_indices': lambda spec,length: IndexRequest('indices', (spec,length)),
            'exit_stack': lambda asynchronous=False,max_entries=1024: create_exit_stack(self, asynchronous, max_entries),
            'generator_context': lambda generator,asynchronous=False: generator_context(self, generator, asynchronous),
            'iter': lambda value: IterationRequest('iter', value), 'aiter': lambda value: IterationRequest('aiter', value),
            'next': lambda handle: IterationRequest('next', handle),
            'close': lambda handle: IterationRequest('close', handle),
            'collect': lambda handle: IterationRequest('collect', handle),
            'send': lambda handle,value: IterationRequest('send', handle, value),
            'throw_into': lambda handle,value: IterationRequest('throw', handle, value),
            'anext': self.suspension.anext, 'asend': self.suspension.asend, 'athrow': self.suspension.athrow,
            'aclose': self.suspension.aclose, 'acollect': self.suspension.acollect,
            'spawn': self.suspension.spawn, 'gather': self.suspension.gather, 'cancel': self.suspension.cancel,
            'task_info': self.suspension.info,
            'channel': lambda capacity=0: create_channel(self, capacity),
            'channel_send': lambda channel, value: channel_send(self, channel, value),
            'channel_receive': lambda channel: channel_receive(self, channel),
            'channel_close': lambda channel: channel_close(self, channel),
            'channel_info': lambda channel: channel_info(self, channel),
            'send_case': lambda channel, value: send_case(self, channel, value),
            'receive_case': lambda channel: receive_case(self, channel),
            'case_info': lambda case: case_info(self, case),
            'channel_select': lambda cases, timeout=None: channel_select(self, cases, timeout),
            'cancel_shield': lambda: create_shield(self),
            'cancel_shield_info': lambda shield: shield_info(self, shield),
            'task_group': lambda *, errors='first': create_group(self, errors=errors),
            'task_group_info': lambda group: group_info(self, group),
        }
        from .error_values import create as create_error, current as current_error, group as error_group, UNSET
        functions.update({'error': lambda value, *, cause=UNSET: create_error(self, value, cause=cause),
                          'current_error': lambda: current_error(self),
                          'error_group': lambda message, errors, *, cause=UNSET: error_group(self, message, errors, cause=cause)})
        from .dynamic_contexts import builtins as context_builtins
        functions.update(context_builtins(self))
        from .conditions import builtins as recovery_builtins
        functions.update(recovery_builtins(self))
        from .hash_maps import builtins as hash_map_builtins
        functions.update(hash_map_builtins(self))
        from .contracts import builtins as contract_builtins
        functions.update(contract_builtins(self))
        from .generic_functions import builtins as generic_builtins
        functions.update(generic_builtins(self))
        from .module_values import builtins as module_builtins
        functions.update(module_builtins(self))
        from .code_values import builtins as code_builtins
        functions.update(code_builtins(self))
        from .jit import builtins as jit_builtins
        functions.update(jit_builtins(self))
        from .ui_artifacts import builtins as ui_builtins
        functions.update(ui_builtins(self))
        from .gpu_compute import builtins as gpu_builtins
        functions.update(gpu_builtins(self))
        from .modes import builtins as mode_builtins
        functions.update(mode_builtins(self))
        from .agent_tools import builtins as agent_tool_builtins
        functions.update(agent_tool_builtins(self))
        from .agent_values import builtins as agent_value_builtins
        functions.update(agent_value_builtins(self))
        from .team_values import builtins as team_value_builtins
        functions.update(team_value_builtins(self))
        from .team_bindings import builtins as team_binding_builtins
        functions.update(team_binding_builtins(self))
        from .program_values import builtins as program_value_builtins
        functions.update(program_value_builtins(self))
        from .device_values import builtins as device_value_builtins
        functions.update(device_value_builtins(self))
        from .robot_values import builtins as robot_value_builtins
        functions.update(robot_value_builtins(self))
        from .computer_values import builtins as computer_value_builtins
        functions.update(computer_value_builtins(self))
        from .session_tools import builtins as session_tool_builtins
        from .session_values import builtins as session_value_builtins
        functions.update(session_value_builtins(self),**session_tool_builtins(self))
        from .process_values import builtins as process_value_builtins
        functions.update(process_value_builtins(self))
        from .guidance_values import builtins as guidance_value_builtins
        functions.update(guidance_value_builtins(self))
        from .bot_values import builtins as bot_value_builtins
        functions.update(bot_value_builtins(self))
        from .task_waits import sleep as native_sleep, wait as native_wait
        functions.update({
            'sleep': lambda seconds: native_sleep(self, seconds),
            'task_wait': lambda tasks, until='first', timeout=None: native_wait(self, tasks, until, timeout),
        })
        functions.update(self.object_builtins())
        from .native_specs import spec_for
        from .multiple_values import builtins as value_builtins
        functions.update(value_builtins(self))
        from .control_prompts import builtins as control_builtins
        functions.update(control_builtins(self))
        for name, call in functions.items():
            env.bind(name, Native(name, call, self, spec_id=name if spec_for(name) else None))
        self.numeric_constructors = {name:env.get(name) for name in ('fraction','complex','decimal')}
        self.condition_constructor=env.get('condition')
        env.bind('Type', self.native_type)
        for name, klass in self.exception_classes.items(): env.bind(name, klass)
        env.bind('NotImplemented', NOT_IMPLEMENTED)
        return env
