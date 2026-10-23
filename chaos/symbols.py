"""Source-backed binding references and capture-checked bundle renames.

This module parses original syntax only. It never expands macros, evaluates source,
resolves descriptors, or infers identity from the spelling of a structural key.
Public offsets are UTF-16 units; internal edits use exact lexer token boundaries.
"""
import bisect
import hashlib
from dataclasses import dataclass, field

from .language import Parser, Node, TYPES, LanguageError, ResourceLimit, tokenize
from .modules import (STDLIB, MAX_DEPTH, MAX_BYTES, bundle_files, declarations, path_name,
                      resolve_path, normalize_language_versions)


def _error(message, code='symbol_error'):
    raise LanguageError(message, code=code)


class _Map:
    def __init__(self, text):
        self.text = text
        self.lines = [0] + [i + 1 for i, c in enumerate(text) if c == '\n']
        self.units = [0]
        for c in text: self.units.append(self.units[-1] + (2 if ord(c) > 0xffff else 1))

    def offset(self, line, column):
        return self.lines[(line or 1)-1] + (column or 1)-1

    def span(self, node):
        s = getattr(node, 'span', {})
        return (self.offset(s.get('line'), s.get('column')),
                self.offset(s.get('endLine'), s.get('endColumn')))

    def position(self, value):
        if type(value) is not int or value < 0 or value > self.units[-1]:
            _error('position 必须是源码内的 UTF-16 整数偏移')
        i = bisect.bisect_left(self.units, value)
        if i == len(self.units) or self.units[i] != value:
            _error('position 不可位于 UTF-16 代理对内部')
        return i

    def public(self, start, end):
        return {'from': self.units[start], 'to': self.units[end]}


@dataclass(eq=False)
class _Function:
    params: dict = field(default_factory=dict)
    limitations: set = field(default_factory=set)


@dataclass
class _Value:
    module: object = None
    function: object = None


@dataclass(eq=False)
class _Symbol:
    file: str
    start: int
    end: int
    name: str
    kind: str
    namespace: str
    value: _Value = field(default_factory=_Value)
    limitations: set = field(default_factory=set)
    parameter_of: object = None

    @property
    def identity(self): return (self.file, self.start, self.end, self.namespace)


@dataclass
class _Reference:
    file: str
    start: int
    end: int
    symbol: object
    kind: str
    name: str
    quoted: str = ''


class _Index:
    def __init__(self, source, *, file, files, entry, language_versions, checkpoint):
        self.checkpoint = checkpoint
        self.steps = 0
        file, entry = path_name(file), path_name(entry)
        supplied = dict(files or {}) if isinstance(files, dict) or files is None else None
        if supplied is None: _error('files 必须是源码记录')
        supplied[file] = source
        self.files, self.entry = bundle_files(supplied.get(entry, ''), supplied, entry)
        self.versions = normalize_language_versions(self.files, language_versions)
        self.maps, self.tokens, self.starts, self.trees = {}, {}, {}, {}
        self.exports, self.imports, self.envs, self.macros = {}, {}, {}, {}
        self.symbols, self.refs, self.module_limits = {}, [], {}
        self.pending_keywords = []
        self.loading = []
        self.current_file = file
        self.phase = 'runtime'
        self.opaque = []
        self.quote_envs = []
        self.parse(entry)
        for name in sorted(self.files): self.parse(name)
        self.analyze(entry)
        for name in sorted(self.files): self.analyze(name)
        self.propagate_escapes()
        for reference, shape, name, opaque in self.pending_keywords:
            reference.symbol = shape.params.get(name)
            if reference.symbol is not None:reference.symbol.limitations.update(opaque)

    def tick(self):
        self.steps += 1
        if self.steps > 200000: raise ResourceLimit('符号分析超过 200000 步')
        if self.checkpoint: self.checkpoint()

    def parse(self, name):
        self.tick()
        if name in self.loading: _error('循环导入: '+' -> '.join(self.loading+[name]), 'module_error')
        if name in self.trees: return
        if len(self.loading) >= MAX_DEPTH: raise ResourceLimit('模块依赖深度超过 32')
        text = STDLIB.get(name, self.files.get(name))
        if text is None: _error('模块不存在: '+name, 'module_error')
        self.loading.append(name)
        try:
            parser = Parser(text, name, ranges=True, legacy_items=self.versions.get(name,2)==1, checkpoint=self.checkpoint)
            tree = parser.program()
            self.maps[name] = _Map(text)
            self.tokens[name] = parser.tokens
            self.starts[name] = [t.start for t in parser.tokens]
            self.exports[name] = declarations(tree, include_macros=True)
            self.imports[name] = {}
            # Publish only after recursively checking dependencies, so cycles cannot hide.
            for node in tree[1]:
                if node[0] == 'import':
                    target = resolve_path(node[1], name)
                    self.parse(target)
                    self.imports[name][node[2]] = target
            self.trees[name] = tree
        finally: self.loading.pop()

    @property
    def mapping(self): return self.maps[self.current_file]

    def span(self, node): return self.mapping.span(node)

    def between(self, start, end):
        starts = self.starts[self.current_file]
        tokens = self.tokens[self.current_file]
        return tokens[bisect.bisect_left(starts,start):bisect.bisect_left(starts,end)]

    def token(self, name, node, after=None):
        if node[0]=='def' and not getattr(node,'span',None):
            start,_=self.span(node[2]); i=bisect.bisect_left(self.starts[self.current_file],start)
            t=self.tokens[self.current_file][i-1] if i else None
            return t if t and t.kind=='id' and t.value==name else None
        tokens=self.between(*self.span(node))
        if after is not None:
            i=next((i for i,t in enumerate(tokens) if t.value==after),len(tokens))
            tokens=tokens[i+1:]
        return next((t for t in tokens if t.kind=='id' and t.value==name),None)

    def declare(self, name, env, node, kind='local', *, token=None, after=None, value=None, own=None, namespace=None):
        self.tick()
        t=token or self.token(name,node,after)
        if t is None: _error('声明缺少可靠的源码标记: '+name, 'symbol_unsupported')
        key=(self.current_file,t.start,t.end,namespace or self.phase)
        symbol=self.symbols.get(key)
        if symbol is None:
            symbol=_Symbol(self.current_file,t.start,t.end,name,kind,namespace or self.phase,value or _Value())
            self.symbols[key]=symbol
            self.refs.append(_Reference(self.current_file,t.start,t.end,symbol,'declaration',name))
        elif value is not None: symbol.value=value
        if own is not None:
            if name in own: _error('重复绑定 '+name, 'symbol_conflict')
            own.add(name)
        symbol.limitations.update(self.opaque)
        env[name]=symbol
        return symbol

    def reference(self, token, symbol, kind='read', name=None):
        if token is None: _error('引用缺少可靠的源码标记', 'symbol_unsupported')
        self.tick()
        if symbol: symbol.limitations.update(self.opaque)
        raw=self.maps[self.current_file].text[token.start:token.end]
        quote=raw[0] if token.kind=='string' else ''
        self.refs.append(_Reference(self.current_file,token.start,token.end,symbol,kind,name or token.value,quote))

    def escape(self, value, reason):
        if value.module is not None: self.module_limits.setdefault(value.module,set()).add(reason)
        if value.function is not None: value.function.limitations.add(reason)

    def propagate_escapes(self):
        # A dynamic read can recover any exported value, including a namespace
        # re-export or a callable with a keyword contract declared elsewhere.
        # Compute this closure only after every module's exports are finalized.
        pending=[(module,reason) for module,reasons in self.module_limits.items() for reason in reasons]
        seen=set()
        while pending:
            self.tick()
            module,reason=pending.pop()
            if (module,reason) in seen:continue
            seen.add((module,reason))
            for name in self.exports[module]:
                self.tick()
                symbol=self.envs[module].get(name)
                if symbol is None:continue  # Macros are absent from runtime records.
                value=symbol.value
                if value.function is not None:value.function.limitations.add(reason)
                if value.module is not None:
                    self.module_limits.setdefault(value.module,set()).add(reason)
                    pending.append((value.module,reason))

    def qualified(self, name, tokens, env, kind):
        parts=name.split('.'); result=None; value=_Value()
        cursor=0
        for i,part in enumerate(parts):
            found=next(((j,t) for j,t in enumerate(tokens[cursor:],cursor) if t.kind=='id' and t.value==part),None)
            if found is None: _error('限定引用缺少可靠标记: '+name, 'symbol_unsupported')
            j,t=found;cursor=j+1
            if i==0: result=env.get(part)
            elif value.module is not None:
                namespace=self.macros[value.module] if kind=='macro' else self.envs[value.module]
                result=namespace.get(part) if part in self.exports[value.module] else None
            else: result=None
            self.reference(t,result,kind if i==0 else 'import_member',part)
            value=result.value if result else _Value()
        return value

    def annotation(self, name, start, end, env):
        if name=='Any': return
        tokens=self.between(start,end)
        if name in TYPES:
            t=next((t for t in tokens if t.kind=='id' and t.value==name),None)
            if t:self.reference(t,None,'builtin_type',name)
            return
        self.qualified(name,tokens,env,'type')

    def analyze(self, name):
        if name in self.envs: return
        for target in self.imports[name].values(): self.analyze(target)
        previous=self.current_file,self.phase
        self.current_file,self.phase=name,'runtime'
        env={}; macros={}
        self.envs[name]=env;self.macros[name]=macros
        try:
            # Macro and import namespaces are compile-time, independent of runtime shadowing.
            for node in self.trees[name][1]:
                if node[0]=='macro':
                    self.declare(node[1],macros,node,'macro',after='macro',namespace='macro')
                elif node[0]=='import':
                    self.declare(node[2],macros,node,'import',after='as',value=_Value(module=self.imports[name][node[2]]))
            result=self.block(self.trees[name],env,root=True)
            self.escape(result,'模块求值结果可向源码包外传递')
            for node in self.trees[name][1]:
                if node[0]=='export':
                    for label in node[1]:
                        symbol=macros.get(label) if label in macros and macros[label].kind=='macro' else env.get(label)
                        self.reference(self.token(label,node),symbol,'export',label)
        finally: self.current_file,self.phase=previous

    def block(self, node, env, root=False):
        local=env if root else dict(env);own=set();result=_Value()
        for child in node[1]: result=self.walk(child,local,own)
        return result

    def pattern(self, node, env, lookup, own, parameter=False, assignment=False):
        kind=node[0]
        if kind=='p_bind':
            if assignment:
                symbol=env.get(node[1]);self.reference(self.token(node[1],node),symbol,'write')
                return
            return self.declare(node[1],env,node,'pattern_parameter' if parameter else 'pattern',own=own)
        if kind in ('p_any','p_literal'): return
        if kind in ('p_list','p_tuple','p_record'):
            for item in node[1]: self.pattern(item[1] if kind=='p_record' else item,env,lookup,own,parameter,assignment)
            if node[2] is not None:self.pattern(node[2],env,lookup,own,parameter,assignment)
            return
        if kind=='p_class':
            self.qualified(node[1],self.between(*self.span(node)),lookup,'read')
            for _,item in node[2]:self.pattern(item,env,lookup,own,parameter,assignment)
            if node[3] is not None:self.pattern(node[3],env,lookup,own,parameter,assignment)
            return
        if kind=='p_tag':
            self.qualified(node[1],self.between(*self.span(node)),lookup,'read')
            for item in node[2]:self.pattern(item,env,lookup,own,parameter,assignment)
            return
        _error('未支持的模式: '+kind,'symbol_unsupported')

    def function(self,node,env,binding=None,group=None,defaults=None,recursive=True):
        shape=binding.value.function if binding and binding.value.function else _Function()
        value=_Value(function=shape)
        if binding:binding.value=value
        local=dict(env)
        if group:local.update(group)
        if binding and recursive:local[binding.name]=binding
        # Pattern constructors resolve against the captured Function.env, before
        # parameters are bound. Methods use the class lexical closure, without
        # an implicit recursive method-name binding.
        constructors=dict(local)
        body_start,_=self.span(node[3]);start,_=self.span(node)
        header=self.between(start,body_start);cursor=0;own=set()
        for param in node[1]:
            name,typ=param[:2]
            found=next(((j,t) for j,t in enumerate(header[cursor:],cursor) if t.kind=='id' and t.value==name),None)
            if found is None:_error('参数缺少可靠标记','symbol_unsupported')
            j,t=found;cursor=j+1
            symbol=self.declare(name,local,node,'parameter',token=t,own=own)
            mode=param[2] if len(param)>=4 else 'positional'
            # Only addressable parameters own caller keyword labels. Private
            # positional slots and rest variable names are lexical bindings.
            if mode in ('positional','keyword_only'):
                symbol.parameter_of=shape;shape.params[name]=symbol
            annotation_start=t.end
            if len(param)==5:
                self.pattern(param[4],local,constructors,own,True)
                _,annotation_start=self.span(param[4])
                while cursor<len(header) and header[cursor].start<annotation_start:cursor+=1
            end=next((x.start for x in header[cursor:] if x.value in ('=',',',')')),body_start)
            self.annotation(typ,annotation_start,end,defaults if defaults is not None else env)
            while cursor<len(header) and header[cursor].start<end:cursor+=1
            if len(param)>=4 and param[3] is not None:
                default_value=self.walk(param[3],defaults if defaults is not None else env)
                self.escape(default_value,'默认参数值可由动态调用访问')
                _,last=self.span(param[3])
                while cursor<len(header) and header[cursor].start<last:cursor+=1
        arrow=next((i for i,t in reversed(list(enumerate(header))) if t.value=='->'),None)
        if arrow is not None:self.annotation(node[2],header[arrow].end,body_start,defaults if defaults is not None else env)
        result=self.block(node[3],local)
        self.escape(result,'函数返回值可通过动态调用访问')
        return value

    def walk(self,node,env,own=None):
        self.tick()
        if not isinstance(node,(tuple,list)) or not node:return _Value()
        kind=node[0]
        if kind=='block':return self.block(node,env)
        if kind in ('literal','break','continue'):return _Value()
        if kind=='name':
            symbol=env.get(node[1]);self.reference(self.token(node[1],node),symbol)
            return symbol.value if symbol else _Value()
        if kind in ('let','var'):
            value=self.walk(node[3],env);t=self.token(node[1],node,kind)
            self.annotation(node[2],t.end,self.span(node[3])[0],env)
            if kind=='var':self.escape(value,'可变绑定不能证明模块或函数调用身份');value=_Value()
            return self.declare(node[1],env,node,kind,token=t,value=value,own=own).value
        if kind in ('let_pattern','var_pattern'):
            value=self.walk(node[3],env);self.escape(value,'解构值的模块或函数成员不能证明')
            self.annotation(node[2],self.span(node[1])[1],self.span(node[3])[0],env)
            self.pattern(node[1],env,dict(env),own if own is not None else set())
            return _Value()
        if kind=='assign_pattern':
            value=self.walk(node[2],env);self.escape(value,'解构赋值可使模块或函数身份动态变化')
            self.pattern(node[1],env,dict(env),set(),assignment=True)
            return _Value()
        if kind=='aug_assign' and node[1][0]=='name':
            target=node[1];symbol=env.get(target[1]);self.reference(self.token(target[1],target),symbol,'write')
            if symbol:self.escape(symbol.value,'增强赋值可动态调用运算协议')
            self.escape(self.walk(node[3],env),'增强赋值可动态传递模块或函数');return _Value()
        if kind=='assign':
            symbol=env.get(node[1]);self.reference(self.token(node[1],node),symbol,'write')
            value=self.walk(node[2],env);self.escape(value,'赋值可使模块或函数身份动态变化');return _Value()
        if kind=='def':
            outer=dict(env)
            binding=self.declare(node[1],env,node,'function',after='fn',value=_Value(function=_Function()),own=own)
            return self.function(node[2],outer,binding)
        if kind in ('fn','gen_fn','async_fn','async_gen_fn'):return self.function(node,env)
        if kind=='rec':
            outer=dict(env);group={}
            for child in node[1]:group[child[1]]=self.declare(child[1],env,child,'function',value=_Value(function=_Function()),own=own)
            for child in node[1]:self.function(child[2],outer,group[child[1]],group)
            return _Value()
        if kind=='decorated':
            for decorator in node[1]:self.escape(self.walk(decorator,env),'装饰器调用关系不可静态证明')
            value=self.walk(node[2],env,own)
            self.escape(value,'装饰器可替换函数或类型')
            # The declaration remains lexical, but its runtime callable identity is unknown.
            env[node[2][1]].value=_Value()
            return _Value()
        if kind=='agent':
            self.walk(node[2],env)
            return self.declare(node[1],env,node,'agent',after='agent',own=own).value
        if kind=='import':
            symbol=self.macros[self.current_file][node[2]]
            if own is not None:
                if node[2] in own:_error('重复绑定 '+node[2],'symbol_conflict')
                own.add(node[2])
            env[node[2]]=symbol;return symbol.value
        if kind=='export':return _Value()
        if kind=='macro':
            binding=self.macros[self.current_file][node[1]]
            local={};previous=self.phase;self.phase='macro'
            try:
                header=self.between(self.span(node)[0],self.span(node[3])[0]);opening=next(i for i,t in enumerate(header) if t.value=='(')
                tokens=[t for t in header[opening+1:] if t.kind=='id']
                for name,t in zip(node[2],tokens):self.declare(name,local,node,'macro_parameter',token=t)
                self.block(node[3],local)
            finally:self.phase=previous
            return _Value()
        if kind=='macrocall':
            self.qualified(node[1],self.between(*self.span(node)),self.macros[self.current_file],'macro')
            self.opaque.append('宏实参的语法可能被编译期代码检查或变换')
            try:
                for argument in node[2]:self.escape(self.walk(argument,env),'宏实参不能证明运行时模块或调用关系')
            finally:self.opaque.pop()
            return _Value()
        if kind=='quote':
            self.quote_envs.append((dict(env),self.phase,list(self.opaque)))
            previous=self.phase;self.phase='quote';self.opaque.append('quote 模板经过卫生化和编译期变换，不执行宏来猜测引用')
            try:self.block(node[1],{})
            finally:self.opaque.pop();self.phase=previous;self.quote_envs.pop()
            return _Value()
        if kind=='unquote':
            if not self.quote_envs:_error('unquote 缺少 quote','symbol_unsupported')
            outer,phase,opaque=self.quote_envs[-1];oldphase,oldopaque=self.phase,self.opaque
            self.phase,self.opaque=phase,list(opaque)
            try:return self.walk(node[1],outer)
            finally:self.phase,self.opaque=oldphase,oldopaque
        if kind=='data':
            self.declare(node[1],env,node,'type',after='data',own=own)
            tokens=self.between(*self.span(node));cursor=next(i for i,t in enumerate(tokens) if t.value=='{')+1
            for tag,fields in node[2]:
                j,t=next((j,t) for j,t in enumerate(tokens[cursor:],cursor) if t.kind=='id' and t.value==tag)
                self.declare(tag,env,node,'constructor',token=t,own=own);cursor=j+1
                for field_name in fields:
                    j,t=next((j,t) for j,t in enumerate(tokens[cursor:],cursor) if t.kind=='id' and t.value==field_name)
                    field_symbol=self.declare(field_name,{},node,'structural',token=t)
                    field_symbol.limitations.add('数据构造器字段是结构合同，不是词法变量');cursor=j+1
            return _Value()
        if kind=='class':
            for base in node[2]:self.escape(self.walk(base,env),'类基类不是静态函数调用')
            from .class_creation import class_options
            for option in class_options(node):self.escape(self.walk(option[2] if option[0]=='keyword' else option[1],env),'类创建选项可交给用户 hook')
            binding=self.declare(node[1],env,node,'class',after='class',own=own)
            closure=dict(env);defaults=dict(closure)
            for wrapper in node[3][1]:
                member=wrapper[2] if wrapper[0]=='decorated' else wrapper
                if wrapper[0]=='decorated':
                    for decorator in wrapper[1]:self.escape(self.walk(decorator,defaults),'类装饰器可替换成员')
                name=member[1]
                if member[0]=='def':
                    previous_defaults=dict(defaults)
                    symbol=self.declare(name,defaults,member,'structural',after='fn',value=_Value(function=_Function()))
                    symbol.limitations.add('类成员名称不是词法变量')
                    self.function(member[2],closure,symbol,defaults=previous_defaults,recursive=False)
                    symbol.value.function.limitations.add('方法可通过动态属性或描述符调用')
                elif member[0] in ('let','var'):
                    value=self.walk(member[3],defaults);self.escape(value,'类成员存储的模块或函数可动态访问')
                    t=self.token(name,member,member[0]);self.annotation(member[2],t.end,self.span(member[3])[0],defaults)
                    symbol=self.declare(name,defaults,member,'structural',token=t)
                    symbol.limitations.add('类成员名称不是词法变量')
                else:_error('未支持的类成员','symbol_unsupported')
            return binding.value
        if kind=='aug_assign' and node[1][0]=='unquote':
            self.walk(node[1],env);self.escape(self.walk(node[3],env),'宏插入的增强赋值目标不可证明');return _Value()
        if kind in ('index','subscript','set_member','set_item','aug_assign'):
            target=node[1] if kind=='aug_assign' else node
            obj=self.walk(target[1],env);key=target[2]
            if obj.module is not None:
                tokens=self.between(*self.span(key))
                if key[0]=='literal' and type(key[1]) is str and len(tokens)==1 and tokens[0].kind in ('id','string'):
                    symbol=self.envs[obj.module].get(key[1]) if key[1] in self.exports[obj.module] else None
                    self.reference(tokens[0],symbol,'import_member',key[1])
                    value=symbol.value if symbol else _Value()
                else:
                    self.walk(key,env);self.escape(obj,'模块存在动态字段访问，无法证明全部导出引用');value=_Value()
            else:
                # Dot labels are structural, whereas computed keys are expressions.
                self.walk(key,env);value=_Value()
            if kind in ('set_member','set_item','aug_assign'):
                if kind=='aug_assign':self.escape(value,'增强赋值可动态调用运算协议')
                self.escape(obj,'模块存在成员赋值');self.escape(self.walk(node[3],env),'成员赋值的模块或函数身份不可证明')
                return _Value()
            return value
        if kind=='call':
            callee=self.walk(node[1],env)
            if callee.module is not None:self.escape(callee,'模块被作为动态 callable 使用')
            for argument in node[2]:
                if argument[0]=='keyword':
                    if callee.function is not None:
                        self.reference(self.token(argument[1],argument),callee.function.params.get(argument[1]),'keyword',argument[1])
                        self.pending_keywords.append((self.refs[-1],callee.function,argument[1],tuple(self.opaque)))
                    value=self.walk(argument[2],env)
                elif argument[0] in ('spread','kwspread'):
                    value=self.walk(argument[1],env)
                    if argument[0]=='kwspread' and callee.function is not None:
                        callee.function.limitations.add('** 参数映射中的关键字关系不可证明')
                else:value=self.walk(argument,env)
                self.escape(value,'作为调用实参传递后模块或函数身份不可证明')
            return _Value()
        if kind in ('list','tuple','record','set'):
            for item in node[1]:self.escape(self.walk(item[1] if kind=='record' else item,env),'容器中的模块或函数可被动态访问')
            return _Value()
        if kind in ('list_comp','set_comp','record_comp','gen_comp','async_gen_comp'):
            local=dict(env)
            for clause in node[-1]:
                if clause[0]=='comp_if':self.walk(clause[1],local);continue
                self.escape(self.walk(clause[2],local),'推导式迭代值的模块或函数身份不可证明')
                local=dict(local)
                if clause[0].endswith('_pattern'):self.pattern(clause[1],local,dict(local),set())
                else:self.declare(clause[1],local,clause,'comprehension',after='for')
            for expression in node[1:-1]:self.escape(self.walk(expression,local),'推导式产生的模块或函数可动态访问')
            return _Value()
        if kind in ('for','async_for','for_pattern','async_for_pattern','with','async_with','with_pattern','async_with_pattern'):
            self.escape(self.walk(node[2],env),'迭代或上下文协议不能证明模块或函数身份')
            local=dict(env)
            if kind.endswith('_pattern'):self.pattern(node[1],local,dict(env),set())
            else:
                start=self.span(node[2])[1];end=self.span(node[3])[0]
                t=next((t for t in self.between(start,end) if t.kind=='id' and t.value==node[1]),None) if kind in ('with','async_with') else self.token(node[1],node,'for')
                self.declare(node[1],local,node,'context' if kind in ('with','async_with') else 'loop',token=t)
            self.block(node[3],local);return _Value()
        if kind=='rethrow':return _Value()
        if kind=='try_group':
            self.escape(self.walk(node[1],env),'分支返回模块或函数身份不确定')
            for case in node[2]:
                _,pattern,guard,name,body=case
                local=dict(env);self.pattern(pattern,local,dict(env),set())
                if guard is not None:self.walk(guard,local)
                header=self.between(self.span(guard if guard is not None else pattern)[1],self.span(body)[0])
                handler=dict(env);self.declare(name,handler,case,'catch_group',token=header[-1] if header else None)
                self.escape(self.block(body,handler),'分组处理返回模块或函数身份不确定')
            return _Value()
        if kind=='try_cases':
            self.escape(self.walk(node[1],env),'分支返回模块或函数身份不确定')
            for pattern,guard,body in node[2]:
                local=dict(env);self.pattern(pattern,local,dict(env),set())
                if guard is not None:self.walk(guard,local)
                self.escape(self.block(body,local),'分支返回模块或函数身份不确定')
            return _Value()
        if kind=='try':
            self.escape(self.walk(node[1],env),'分支返回模块或函数身份不确定')
            local=dict(env);self.declare(node[2],local,node,'catch',after='catch')
            self.escape(self.block(node[3],local),'分支返回模块或函数身份不确定');return _Value()
        if kind=='match':
            self.escape(self.walk(node[1],env),'模式匹配值的模块或函数身份不可证明')
            for pattern,guard,body in node[2]:
                local=dict(env);self.pattern(pattern,local,dict(env),set())
                if guard:self.walk(guard,local)
                self.escape(self.walk(body,local),'分支返回模块或函数身份不确定')
            return _Value()
        if kind=='if':
            self.walk(node[1],env)
            for child in node[2:]:self.escape(self.walk(child,env),'分支返回模块或函数身份不确定')
            return _Value()
        if kind in ('do','return','throw','await','yield','yield_from','spread','kwspread'):
            value=self.walk(node[1],env)
            if kind in ('return','throw','yield','yield_from','await'):
                self.escape(value,'延迟或返回值的模块或函数身份不可证明');return _Value()
            return value
        if kind in ('while','finally','loop_else','try_else'):
            for child in node[1:]:self.escape(self.walk(child,env),'控制流结果的模块或函数身份不可证明')
            return _Value()
        if kind=='binary':
            for child in node[2:]:self.escape(self.walk(child,env),'运算符可动态传递模块或函数')
            return _Value()
        if kind=='unary':self.escape(self.walk(node[2],env),'运算符可动态传递模块或函数');return _Value()
        if kind=='slice':
            for child in node[1:]:
                if child is not None:self.walk(child,env)
            return _Value()
        _error('未支持的源码节点 '+str(kind),'symbol_unsupported')

    def limitations(self,symbol):
        result=set(symbol.limitations)
        if symbol.parameter_of is not None:result.update(symbol.parameter_of.limitations)
        if symbol.name in self.exports[symbol.file] and (self.envs[symbol.file].get(symbol.name) is symbol or self.macros[symbol.file].get(symbol.name) is symbol):
            result.update(self.module_limits.get(symbol.file,set()))
        if symbol.file in STDLIB:result.add('标准模块源码只读')
        return sorted(result)

    def target(self,file,position):
        if file not in self.files:_error('查询文件不在源码包内')
        offset=self.maps[file].position(position)
        matches=[r for r in self.refs if r.file==file and r.start<=offset<r.end]
        if not matches:
            matches=[r for r in self.refs if r.file==file and r.start<offset==r.end]
        if not matches:return None
        return min(matches,key=lambda r:(r.end-r.start, r.kind!='declaration')).symbol

    def public_symbol(self,symbol):
        return dict(self.maps[symbol.file].public(symbol.start,symbol.end),file=symbol.file,
                    name=symbol.name,kind=symbol.kind,namespace=symbol.namespace)

    def matching(self,symbol):
        unique={}
        for ref in self.refs:
            if ref.symbol is symbol:
                key=(ref.file,ref.start,ref.end)
                if key not in unique or ref.kind=='declaration':unique[key]=ref
        return [unique[key] for key in sorted(unique)]


def _build(source, *, file, files, entry, language_versions, checkpoint):
    try:return _Index(source,file=file,files=files,entry=entry,language_versions=language_versions,checkpoint=checkpoint)
    except RecursionError:raise ResourceLimit('符号分析结构嵌套过深') from None


def references(source, *, file='main.chaos', files=None, entry='main.chaos', position,
               language_versions=None, include_declaration=True, checkpoint=None):
    if type(include_declaration) is not bool:_error('include_declaration 必须是 Bool')
    index=_build(source,file=file,files=files,entry=entry,language_versions=language_versions,checkpoint=checkpoint)
    target=index.target(path_name(file),position)
    if target is None:return {'target':None,'references':[],'complete':True}
    limits=index.limitations(target)
    result={'target':index.public_symbol(target),'references':[
        dict(index.maps[r.file].public(r.start,r.end),file=r.file,kind=r.kind)
        for r in index.matching(target) if include_declaration or r.kind!='declaration'], 'complete':not limits}
    if limits:result['limitations']=limits
    return result


def rename(source, *, file='main.chaos', files=None, entry='main.chaos', position,
           new_name, language_versions=None, checkpoint=None):
    if not isinstance(new_name,str):_error('new_name 必须是标识符字符串')
    tokens=tokenize(new_name)
    if len(tokens)!=2 or tokens[0].kind!='id' or tokens[0].value!=new_name or new_name in Parser.reserved:
        _error('new_name 必须是非保留标识符')
    index=_build(source,file=file,files=files,entry=entry,language_versions=language_versions,checkpoint=checkpoint)
    target=index.target(path_name(file),position)
    if target is None:_error('此位置没有可重命名的源码绑定')
    limits=index.limitations(target)
    if limits:_error('；'.join(limits),'symbol_unsupported')
    matching=index.matching(target)
    # Bound the complete projection before allocating even repeated replacement
    # strings. Input limits alone do not bound occurrences × new-name length.
    projected_chars={name:len(text) for name,text in index.files.items()}
    projected_bytes={name:len(text.encode('utf-8')) for name,text in index.files.items()}
    replacement_bytes=len(new_name.encode('utf-8'))
    for ref in matching:
        index.tick()
        if ref.file not in index.files:_error('不可编辑标准模块','symbol_unsupported')
        original=index.files[ref.file][ref.start:ref.end]
        quotes=2 if ref.quoted else 0
        projected_chars[ref.file]+=len(new_name)+quotes-(ref.end-ref.start)
        projected_bytes[ref.file]+=replacement_bytes+quotes-len(original.encode('utf-8'))
    if any(size>100000 for size in projected_chars.values()):
        raise ResourceLimit('重命名候选单文件超过 100 KB 限制')
    if sum(projected_bytes.values())>MAX_BYTES:
        raise ResourceLimit('重命名候选源码包超过 1 MB')
    internal={};replacements={}
    for ref in matching:
        index.tick()
        if ref.quoted not in replacements:
            replacements[ref.quoted]=ref.quoted+new_name+ref.quoted
        internal.setdefault(ref.file,[]).append((ref.start,ref.end,replacements[ref.quoted]))
    candidate=dict(index.files);edits={}
    for name,changes in sorted(internal.items()):
        previous=0;parts=[]
        for start,end,text in changes:
            index.tick()
            if start<previous:_error('符号编辑范围重叠','symbol_conflict')
            parts.extend((index.files[name][previous:start],text));previous=end
        parts.append(index.files[name][previous:])
        candidate[name]=''.join(parts)
        edits[name]=[dict(index.maps[name].public(a,b),text=t) for a,b,t in changes]
    try:
        after=_build(candidate[index.entry],file=index.entry,files=candidate,entry=index.entry,
                     language_versions=index.versions,checkpoint=checkpoint)
    except ResourceLimit:raise
    except LanguageError as error:
        _error('重命名候选不保持合法绑定: '+error.message,'symbol_conflict')
    translations={}
    for name,changes in internal.items():
        ends=[];deltas=[0]
        for a,b,text in changes:
            ends.append(b);deltas.append(deltas[-1]+len(text)-(b-a))
        translations[name]=(ends,deltas)
    def translate(name,point):
        ends,deltas=translations.get(name,([], [0]))
        return point+deltas[bisect.bisect_right(ends,point)]
    def identity(symbol):
        if symbol is None:return None
        return (symbol.file,translate(symbol.file,symbol.start),translate(symbol.file,symbol.end),symbol.namespace)
    new_refs={(r.file,r.start,r.end,r.kind):r for r in after.refs}
    if len(after.refs)!=len(index.refs):_error('重命名改变了源码绑定结构','symbol_conflict')
    for ref in index.refs:
        index.tick()
        key=(ref.file,translate(ref.file,ref.start),translate(ref.file,ref.end),ref.kind)
        actual=new_refs.get(key)
        if actual is None or (actual.symbol.identity if actual.symbol else None)!=identity(ref.symbol):
            _error('重命名会捕获、遮蔽或改变其他名称的绑定','symbol_conflict')
    return {'target':index.public_symbol(target),'newName':new_name,'edits':edits,
            'baseHashes':{name:hashlib.sha256(text.encode('utf-8')).hexdigest() for name,text in sorted(index.files.items())},
            'files':candidate,'entry':index.entry,'languageVersions':dict(index.versions)}
