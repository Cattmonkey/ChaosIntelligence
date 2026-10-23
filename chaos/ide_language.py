"""Read-only editor intelligence from the Chaos lexer and parser.

Analysis never evaluates a user expression. Ranges use JavaScript UTF-16 units;
internal parser positions remain Unicode code points. Unknown types stay Any.
"""
import bisect
import heapq
import json
from dataclasses import dataclass, field, replace

from .language import Parser, Node, LanguageError, ResourceLimit, Evaluator, Native, TYPES, tokenize
from .modules import STDLIB, path_name, resolve_path, bundle_files, declarations
from .program import plain, render
from .operators import NOT_IMPLEMENTED, ARITHMETIC, INPLACE
from .native_specs import SHAPES as NATIVE_SHAPES, spec_for, call_spec_for


class SourceMap:
    def __init__(self, source):
        self.source = source
        self.lines = [0] + [i + 1 for i, char in enumerate(source) if char == '\n']
        self.units = [0]
        for char in source: self.units.append(self.units[-1] + (2 if ord(char) > 0xffff else 1))

    def offset(self, line, column):
        line = max(1, min(line or 1, len(self.lines)))
        return min(len(self.source), self.lines[line - 1] + max(0, (column or 1) - 1))

    def span(self, node):
        span = getattr(node, 'span', {})
        return (self.offset(span.get('line'), span.get('column')),
                self.offset(span.get('endLine', span.get('line')), span.get('endColumn', span.get('column'))))

    def public(self, start, end):
        return {'from': self.units[max(0, min(start, len(self.source)))],
                'to': self.units[max(0, min(end, len(self.source)))]}


class RecoveryParser(Parser):
    """Use the actual grammar, recovering only statement lists and EOF closers.

    A failed statement is omitted, never accepted for execution. Complete nodes
    inside an unfinished function survive, including its real parameters.
    """
    def expect(self, value):
        if self.t.kind == 'eof' and value in (')', ']', '}'): return
        return super().expect(value)

    def identifier(self):
        if (self.i and self.tokens[self.i-1].value=='.' and self.t.kind!='id'
                and self.tokens[self.i-1].end <= getattr(self,'completion_cursor',-1) <= self.t.start):
            return '<editor-member>'
        return super().identifier()

    def comprehension_clause(self):
        previous = getattr(self, 'recovering_comprehension_clause', False)
        self.recovering_comprehension_clause = True
        try: return super().comprehension_clause()
        finally: self.recovering_comprehension_clause = previous

    def expr(self, minimum=0):
        if getattr(self, 'recovering_comprehension_clause', False) and self.t.kind == 'eof':
            token = self.t
            return Node(('editor_hole',), {'file':self.filename, 'line':token.line, 'column':token.column,
                                           'endLine':token.line, 'endColumn':token.column})
        return super().expr(minimum)

    def argument(self):
        token=self.t
        keyword = token.kind=='id' and self.tokens[self.i+1].value=='=' and self.tokens[self.i+2].kind=='eof'
        spread = token.value in ('*','**') and self.tokens[self.i+1].kind=='eof'
        if token.kind=='eof' or keyword or spread:
            if keyword:self.pop();self.pop()
            elif spread:self.pop()
            finish=self.t
            span={'file':self.filename,'line':finish.line,'column':finish.column,
                  'endLine':finish.line,'endColumn':finish.column}
            hole=Node(('editor_hole',),span)
            if keyword:return Node(('keyword',token.value,hole),dict(span,line=token.line,column=token.column))
            if spread:return Node(('kwspread' if token.value=='**' else 'spread',hole),dict(span,line=token.line,column=token.column))
            return hole
        return super().argument()

    def program(self, end='<eof>'):
        start = self.t
        statements = []
        while self.t.kind != 'eof' and not self.at(end):
            if self.accept(';'): continue
            before = self.i
            try: statements.append(self.statement())
            except LanguageError:
                # Synchronize without interpreting invalid text as declarations.
                nesting = 0
                while self.t.kind != 'eof':
                    if nesting == 0 and (self.at(';') or self.at(end)): break
                    token = self.pop().value
                    if token in ('(', '[', '{'): nesting += 1
                    elif token in (')', ']', '}'): nesting = max(0, nesting - 1)
                if self.i == before and not self.at(end) and self.t.kind != 'eof': self.pop()
            self.accept(';')
        finish = self.t
        return Node(('block', statements), {'file':self.filename,'line':start.line,'column':start.column,
                                            'endLine':finish.line,'endColumn':finish.column})


@dataclass(eq=False)
class Info:
    type: str = 'Any'
    kind: str = 'variable'
    detail: str = 'Any'
    members: dict = field(default_factory=dict)
    parameters: list = field(default_factory=list)
    returns: str = 'Any'
    start: int = -1
    end: int = -1
    mutable: bool = False
    awaited: str = 'Any'
    class_info: object = None
    mro: list = field(default_factory=list)
    own_members: dict = field(default_factory=dict)
    method: bool = False
    result_info: object = None
    awaited_info: object = None
    callback_async: bool = False
    parameter_contracts: dict = field(default_factory=dict)
    builtin: str = ''
    descriptor: str = ''
    wrapped: object = None
    getter: object = None
    setter: object = None
    previous: object = None
    uncertain_descriptor: bool = False
    deleter: object = None
    write_unknown: bool = False
    class_complete: bool = False
    naming_effects: bool = False
    exact_instance: bool = False
    elements: object = None
    closed_record: bool = False
    parameter_patterns: dict = field(default_factory=dict)
    # Declaration identity is distinct from the position used for scope hints.
    # Inferred result types must never acquire a definition merely by carrying
    # the metadata of a class or descriptor's return value.
    declaration_file: str = ''
    declaration_start: int = -1
    declaration_end: int = -1
    # A generator's completion annotation is unrelated to its yielded value.
    yielded_info: object = None
    code_function: object = None
    context_decorator: str = ''
    literal_string: object = None
    team_roles: object = None
    team_entry: object = None
    team_overrides: object = None
    # Inert nominal metadata; never evaluate metaclass/namespace hooks.
    metaclass_info: object = None
    # Same-source writes invalidate protocol certainty by actual ClassInfo identity.
    attribute_mutations: set = field(default_factory=set)


def signature(name, params, returns='Any', patterns=None):
    parts, keyword = [], False
    for index,param in enumerate(params):
        key, typ, mode, default = param
        if mode == 'keyword_only' and not keyword: parts.append('*'); keyword = True
        if mode == 'varargs': keyword = True
        prefix = '*' if mode == 'varargs' else '**' if mode == 'varkw' else ''
        pattern = (patterns or {}).get(key)
        parts.append(prefix + key + (' as ' + pattern if pattern is not None else '') + (': ' + typ if typ != 'Any' else '') + (' = …' if default else ''))
        if mode == 'positional_only' and (index+1 == len(params) or params[index+1][2] != 'positional_only'): parts.append('/')
    return name + '(' + ', '.join(parts) + ') -> ' + returns


def _semantic_role(info, *, position='reference'):
    """Choose an existing token role from inert binding metadata and position.

    A callable parameter remains a parameter at its declaration and references.
    Member names describe the member, even when its stored value came from a
    parameter; a pattern creates a fresh binding rather than borrowing its role.
    """
    if position in ('declaration','reference') and info.kind=='parameter':
        return 'parameter'
    if position=='declaration':
        return 'type' if info.kind=='class' else info.kind if info.kind in ('function','property') else 'variable'
    if position=='pattern':
        return 'type' if info.kind=='class' or info.type=='Class' else 'function' if info.type=='Function' else 'variable'
    if info.type=='Function':return 'function'
    if position=='member':return 'type' if info.type=='Class' else 'property'
    if info.kind=='class':return 'type'
    return 'variable'


def slice_info():
    return Info('Slice',detail='Slice · 只读切片键',members={name:Info('Any','property',name+': Int | Null · 只读边界') for name in ('start','stop','step')})


def numeric_value_info(typ):
    # Closed immutable member metadata only; never construct a numeric value.
    members={field.name:Info(field.type,'property',field.name+': '+field.type+' · 只读')
             for field in NATIVE_SHAPES[typ+'Fields']}
    return Info(typ,detail=typ+' · 不可变原生数值',members=members)


def error_value_info():
    # Nullable links remain conservative; analysis does not construct or chase errors.
    members={field.name:Info('Any' if field.nullable else field.type,'property',
                            field.name+': '+field.type+(' | Null' if field.nullable else '')+' · 只读')
             for field in NATIVE_SHAPES['ErrorFields']}
    return Info('Error',detail='Error · 本轮原生错误、因果链或保留层级的错误组',members=members)


def recovery_value_info(typ):
    members={field.name:Info('Any' if field.nullable else field.type,'property',field.name+': '+field.type+' · 只读') for field in NATIVE_SHAPES[typ+'Fields']}
    return Info(typ,detail=typ+' · 原生情况/动态恢复值',members=members)


def binding_value_info(typ, _seen=()):
    if typ in _seen:return Info(typ)
    members={}
    for field in NATIVE_SHAPES[typ+'Fields']:
        info=binding_value_info(field.type,_seen+(typ,)) if field.type in ('ContextVar','ContextToken','ContextScope','Context') else Info(field.type)
        members[field.name]=replace(info,kind='property',detail=field.name+': '+field.type+' · 只读')
    from .native_specs import member_specs_for
    for name,spec in member_specs_for(typ).items():members[name]=native_info(spec,_shallow_context=True)
    return Info(typ,detail=typ+' · 原生任务局部上下文',members=members)


def decimal_context_value_info():
    members={field.name:Info(field.type,'property',field.name+': '+field.type+' · 只读逻辑上下文字段')
             for field in NATIVE_SHAPES['DecimalContextFields']}
    return Info('DecimalContext',detail='DecimalContext · 本轮逻辑上下文；with 创建活动副本',members=members)


def shape_info(name):
    members = {}
    for item in NATIVE_SHAPES[name]:
        info = shape_info(item.shape) if item.shape else Info(item.type)
        detail = item.name + ': ' + item.type + (' | Null' if item.nullable else '')
        if item.optional: detail += ' · 可选字段（可能不存在）'
        members[item.name] = replace(info, type='Any' if item.nullable else item.type, kind='property', detail=detail)
    return Info('Record', members=members)


def control_prompt_info(typ):
    return replace(shape_info(typ+'Fields'),type=typ,detail=typ+' · 原生控制身份；查询与提示不执行控制转移')


def native_info(spec, _shallow_context=False, *, display_name=None):
    params = [(p.name,p.type,p.mode,p.optional) for p in spec.parameters]
    detail = signature(display_name or spec.id,params,spec.result_type)
    for p in spec.parameters:
        if p.optional and p.default_display is not None:
            # Signature text is derived from the same named parameter metadata.
            marker = p.name + (': '+p.type if p.type!='Any' else '') + ' = …'
            detail = detail.replace(marker,marker[:-1]+p.default_display)
    if spec.summary: detail += ' · ' + spec.summary
    info = Info('Function','function',detail,parameters=params,returns=spec.result_type,
                awaited=spec.awaited_type,builtin=spec.id)
    if spec.result_callable is not None:
        # Returned functions retain no fictitious global native identity.
        info.result_info=replace(native_info(spec.result_callable),builtin='')
    elif spec.inference_rule=='agent_definition_info':
        # Agent configuration has its own existing public schema. Project it
        # without inventing defaults or running the configured provider.
        from .agent_values import configuration_schema
        fields={key:Info({'string':'String','integer':'Int','array':'List'}.get(field.get('type'),'Any'),
                         'property',key+' · 可选配置字段')
                for key,field in configuration_schema()['properties'].items()}
        info.result_info=shape_info('AgentDefinitionInfo')
        info.result_info.members['config']=Info('Record','property','config · 原有公开配置，不填默认值',members=fields)
    elif spec.result_shape: info.result_info = replace(shape_info(spec.result_shape),type=spec.result_type)
    elif spec.result_type in ('Fraction','Complex','Decimal'): info.result_info = numeric_value_info(spec.result_type)
    elif spec.result_type in ('Condition','HandlerScope','RestartScope','Restart'):info.result_info=recovery_value_info(spec.result_type)
    elif spec.result_type in ('ContextVar','ContextToken','ContextScope','Context'):info.result_info=Info(spec.result_type) if _shallow_context else binding_value_info(spec.result_type)
    elif spec.result_type=='DecimalContext': info.result_info = decimal_context_value_info()
    elif spec.result_type=='Slice': info.result_info = slice_info()
    elif spec.result_type=='Error': info.result_info = error_value_info()
    elif spec.result_type=='TaskGroup': info.result_info = task_group_value_info()
    elif spec.result_type=='CancelShield': info.result_info = cancel_shield_value_info()
    elif spec.result_type=='Channel': info.result_info = Info('Channel',detail='Channel · 本轮共享通道')
    elif spec.result_type=='ChannelCase': info.result_info = Info('ChannelCase',detail='ChannelCase · 不可变可复用通信候选')
    if spec.awaited_shape: info.awaited_info = shape_info(spec.awaited_shape)
    return info


def cancel_shield_value_info():
    return Info('CancelShield',detail='CancelShield · 当前逻辑 Task 的单次词法取消屏蔽；无公开成员')


def task_group_value_info():
    return Info('TaskGroup',detail='TaskGroup · 原生异步任务作用域',
                members={'spawn':native_info(spec_for('TaskGroup.spawn'))})


def resource_stack_value_info(typ):
    methods = ('enter_context','callback','push','pop_all') + (
        ('enter_async_context','push_async_callback','push_async_exit','aclose')
        if typ == 'AsyncExitStack' else ('close',))
    return Info(typ,detail=typ+' · 本轮有界资源栈；后进先出释放',
                members={name:native_info(spec_for(typ+'.'+name),_shallow_context=True) for name in methods})


def task_group_metadata_info():
    return shape_info('TaskGroupInfo')


def channel_metadata_info():
    return shape_info('ChannelInfo')


def builtin_symbols():
    # Constructing the builtin table creates closures only; no callable is run.
    result = {}
    native_bindings = Evaluator().env.values
    for name, native in native_bindings.items():
        if native is NOT_IMPLEMENTED:
            result[name] = Info('NotImplemented','variable',name+': NotImplemented · 运算协议回退常量（不是函数）')
        elif type(native) is not Native:
            result[name] = Info()
        else:
            spec = spec_for(native.spec_id)
            if spec is None:
                raise ValueError('Missing native tooling declaration: ' + name)
            result[name] = native_info(spec)
    if 'Type' in native_bindings:
        meta=Info('Class','class','Type · 原生默认元类',builtin='Type',class_complete=True)
        meta.class_info=meta;meta.mro=[meta];meta.metaclass_info=meta
        for name in ('__prepare__','__new__','__init__','__call__','__instancecheck__','__subclasscheck__'):
            item=native_info(spec_for('Type.'+name));item.method=True
            meta.own_members[name]=item
            if name=='__prepare__':
                params=item.parameters[1:]
                meta.members[name]=replace(item,parameters=params,detail=signature(name,params,item.returns))
            else:meta.members[name]=item
        result['Type']=meta
    # Shared language declarations, not spelling-based guesses or user hooks.
    from .exception_classes import DEFINITIONS, FIELDS
    for name,parent,_code,_codes in DEFINITIONS:
        klass=Info('Class','class',name+' · 原生异常类',builtin=name,class_complete=True)
        klass.class_info=klass;klass.metaclass_info=result.get('Type')
        base=result.get(parent) if parent else None
        klass.mro=[klass]+(base.mro if base else [])
        klass.own_members['code']=Info('String','property','code: String · 只读异常字段')
        if base is None:
            klass.own_members={key:Info(typ,'property',key+': '+typ+' · 只读异常字段') for key,typ in FIELDS}
            klass.own_members['__new__']=native_info(spec_for('Exception.__new__'))
            initializer=native_info(spec_for('Exception.__init__'));initializer.method=True
            klass.own_members['__init__']=initializer
        for ancestor in reversed(klass.mro):klass.members.update(ancestor.own_members)
        result[name]=klass
    return result


class Analysis:
    def __init__(self, source, file, files, entry, position, *, shared=None, language_versions=None):
        self.language_versions=language_versions or {}
        self.source, self.file = source, file
        self.map = SourceMap(source)
        self.lex = tokenize(source, trivia=True, tolerant=True)
        self.code = [t for t in self.lex if t.kind not in ('space','comment','eof')]
        self.code_starts = [t.start for t in self.code]
        self.files, self.entry = files, entry
        self.position = self.map.units[-1] if position is None else position
        if type(self.position) is not int or not 0 <= self.position <= self.map.units[-1]:
            raise LanguageError('position 必须是源码内的 UTF-16 offset')
        self.cursor = bisect.bisect_right(self.map.units, self.position) - 1
        self.diagnostics, self.symbols, self.references, self.scopes = [], [], [], []
        self.declaration_infos = []
        self.colors, self.complete = {}, True
        self.quote_environments = []
        self.macro_namespace = {}
        self.owned = {}
        self.shared = shared if shared is not None else {'cache':{}, 'loading':set()}
        self.base = builtin_symbols()
        self.base['input'] = Info('Any','variable','input: Any（运行输入）')
        self.tree = None
        self.member_steps = 0
        self.protocol_cache = {}
        self.resolving_members = set()
        self.node_infos = {}
        self.member_receivers = {}
        self.call_callees = {}
        self.comprehension_bindings = set()

    def diagnostic(self, message, start, end, severity='error', code='analysis_error'):
        item = dict(self.map.public(start,end),message=message,severity=severity,code=code,file=self.file)
        if item not in self.diagnostics and len(self.diagnostics) < 100: self.diagnostics.append(item)

    def error(self, error, node=None):
        if node is not None: start,end = self.map.span(node)
        else:
            start = self.map.offset(error.line,error.column)
            end = min(len(self.source),start+1)
        self.diagnostic(error.message,start,end,code=error.code)

    def between(self,start,end):
        return self.code[bisect.bisect_left(self.code_starts,start):bisect.bisect_left(self.code_starts,end)]

    def name_token(self, name, node, *, after=None):
        if node[0] == 'def' and not getattr(node, 'span', None):
            # rec contains real function nodes but its declaration wrappers
            # intentionally have no span. The function starts at its '(';
            # the immediately preceding parsed token is its declared name.
            start, _ = self.map.span(node[2])
            index = bisect.bisect_left(self.code_starts, start)
            token = self.code[index - 1] if index else None
            return token if token and token.kind == 'id' and token.value == name else None
        start,end = self.map.span(node)
        tokens = self.between(start,end)
        if after is not None:
            while tokens and tokens[0].value != after: tokens.pop(0)
            if tokens: tokens.pop(0)
        return next((t for t in tokens if t.kind == 'id' and t.value == name),None)

    def declaration_origin(self, info, token):
        return replace(info, declaration_file=self.file if token else '',
                       declaration_start=token.start if token else -1,
                       declaration_end=token.end if token else -1)

    @staticmethod
    def copy_origin(info, declaration):
        return replace(info, declaration_file=declaration.declaration_file,
                       declaration_start=declaration.declaration_start,
                       declaration_end=declaration.declaration_end)

    def declare(self, name, info, env, node, *, after=None, token=None):
        token = token or self.name_token(name,node,after=after)
        start,end = (token.start,token.end) if token else self.map.span(node)
        copy = self.declaration_origin(replace(info,members=dict(info.members),parameters=list(info.parameters),parameter_patterns=dict(info.parameter_patterns),start=start,end=end),token)
        if copy.descriptor=='property':
            copy.members={key:replace(value,wrapped=copy) if value.builtin in ('property.getter','property.setter','property.deleter') else value
                          for key,value in copy.members.items()}
        own=self.owned.get(id(env))
        if own is not None:
            if name in own:self.diagnostic('不可重复绑定 '+name,start,end,code='duplicate_binding')
            own.add(name)
        env[name] = copy
        if token:
            self.colors[token.start] = _semantic_role(copy,position='declaration')
        self.symbols.append(dict(self.map.public(start,end),name=name,type=copy.type,kind=copy.kind,detail=copy.detail))
        if token:self.declaration_infos.append((start,end,copy))
        return copy

    @staticmethod
    def mutable_shape(info):
        """Discard exact container proofs when a Cell can replace the value.

        Keep conservative member hints, but an initial literal is not evidence
        for the current shape after assignment, including through an alias.
        """
        if info.type in ('List','Tuple','Set'): return replace(info, elements=None)
        if info.type == 'Record':
            return replace(info, closed_record=False,
                           members={key:Analysis.mutable_shape(value) for key,value in info.members.items()})
        return info

    def color_record_keys(self, node):
        """AST pair boundaries distinguish static labels, including quoted keys, from values."""
        start, _ = self.map.span(node)
        for _, value in node[2] if node[0]=='p_class' else node[1]:
            left, right = self.map.span(value)
            header = self.between(start, left)
            if len(header) >= 2 and header[-1].value == ':' and header[-2].kind in ('id', 'string'):
                self.colors[header[-2].start] = 'property'
            start = right

    def module(self, spec):
        target = resolve_path(spec,self.file)
        if target in self.shared['cache']: return self.shared['cache'][target]
        if target in self.shared['loading']: raise LanguageError('循环导入: '+target,code='module_error')
        text = STDLIB.get(target,self.files.get(target))
        if text is None: raise LanguageError('模块不存在: '+target,code='module_error')
        if len(self.shared['loading']) >= 32: raise ResourceLimit('模块依赖深度超过 32')
        self.shared['loading'].add(target)
        try:
            tree = Parser(text,target,ranges=True,legacy_items=self.language_versions.get(target,2)==1).program()
            exports = declarations(tree, include_macros=True)
            analysis = Analysis(text,target,self.files,self.entry,None,shared=self.shared,language_versions=self.language_versions)
            env = dict(analysis.base)
            analysis.prepare_macros(tree)
            analysis.walk_block(tree,env,root=True)
            problem=next((d for d in analysis.diagnostics if d['code']=='module_error'),None)
            if problem:raise LanguageError(target+': '+problem['message'],code='module_error')
            result = {name:env[name] for name in exports if name in env}
            if target == 'std/context':
                # Only the sealed standard module confers this identity. It
                # follows immutable aliases/re-exports, never a spelling match.
                for name in ('contextmanager', 'asynccontextmanager'):
                    if name in result:
                        result[name] = replace(result[name], context_decorator=name,
                            detail=signature(name,[('factory','Function','positional',False)],'Function')+' · 生成器资源工厂，保留原参数')
            self.shared['cache'][target] = result
            return result
        finally: self.shared['loading'].discard(target)

    @staticmethod
    def macro_info(node):
        params=[(name,'Syntax','positional',False)for name in node[2]]
        return Info('Macro','function','macro '+signature(node[1],params,'Syntax'),parameters=params,returns='Syntax')

    def prepare_macros(self, tree):
        # Compile-time namespaces are separate from runtime lexical bindings.
        for node in tree[1]:
            if node[0]=='macro':self.macro_namespace[node[1]]=self.declaration_origin(self.macro_info(node),self.name_token(node[1],node,after='macro'))
            elif node[0]=='import':
                try:members=self.module(node[1])
                except (LanguageError,RecursionError):continue  # Ordinary import analysis reports the error.
                self.macro_namespace[node[2]]=self.declaration_origin(Info('Record','module',node[2]+' · '+node[1],members),self.name_token(node[2],node,after='as'))

    def function_info(self, node, name=None, env=None):
        params=[]
        patterns={param[0]:render(param[4]) for param in node[1] if len(param)==5}
        for param in node[1]:
            mode,default = param[2:4] if len(param)>=4 else ('positional',None)
            params.append((param[0],param[1],mode,default is not None))
        name = name or node[4]
        result_type={'async_fn':'Task','gen_fn':'Generator','async_gen_fn':'AsyncGenerator'}.get(node[0],node[2])
        prefix={'async_fn':'async ','gen_fn':'generator ','async_gen_fn':'async generator '}.get(node[0],'')
        result = self.type_info(node[2],env or {}) if node[0]=='fn' else Info(result_type) if node[0] in ('gen_fn','async_gen_fn') else None
        return Info('Function','function',prefix+signature(name,params,node[2],patterns),parameters=params,parameter_patterns=patterns,returns=result_type,awaited=node[2] if node[0]=='async_fn' else 'Any',result_info=result,awaited_info=self.type_info(node[2],env or {}) if node[0]=='async_fn' else None,callback_async=node[0]=='async_fn',parameter_contracts={key:self.type_info(typ,env or {}).class_info for key,typ,_,_ in params})

    def walk_function(self, node, env, name=None, recursive=None, receiver=None, defaults_env=None, binding=None):
        info = self.function_info(node,name,env)
        if binding is not None:info=self.copy_origin(info,binding)
        # Defaults belong to the outer environment, not to parameter scope.
        for param in node[1]:
            if len(param)>=4 and param[3] is not None:
                start,end=self.map.span(param[3]);self.scopes.append((start,end,dict(defaults_env if defaults_env is not None else env)))
                actual=self.walk(param[3],defaults_env if defaults_env is not None else env)
                self.check_value(actual,param[1],env,param[3])
        local = dict(env)
        if recursive: local.update({key:replace(value,start=-1,end=-1) for key,value in recursive.items()})
        if name: local[name] = info
        fnstart,fnend=self.map.span(node)
        body_start,_=self.map.span(node[3])
        header=self.between(fnstart,body_start)
        search=0
        for original,(key,typ,mode,default) in zip(node[1],info.parameters):
            # Parameter starts are recognized by header punctuation; defaults
            # can contain same-named references, so advance past their AST span.
            token=None
            while search < len(header):
                item=header[search];search+=1
                if item.kind=='id' and item.value==key: token=item;break
            parameter=self.type_info(typ,env)
            if mode in ('varargs','varkw'):
                parameter=Info('List' if mode=='varargs' else 'Record')
            if receiver is not None and key==info.parameters[0][0]:parameter=receiver
            self.declare(key,replace(parameter,kind='parameter',detail=key+': '+parameter.type),local,node,token=token)
            annotation_start=token.end if token else fnstart
            if len(original)==5:
                pattern=original[4]
                pattern_start,pattern_end=self.map.span(pattern)
                self.scopes.append((pattern_start,pattern_end,dict(env)))
                self.bind_pattern(pattern,local,parameter,lookup=env,parameter=True)
                if token:
                    for item in self.between(token.end,pattern_start):
                        if item.value=='as':self.colors[item.start]='keyword'
                annotation_start=pattern_end
                while search<len(header) and header[search].start<pattern_end:search+=1
            if token:
                end=next((t.start for t in header if t.start>=annotation_start and t.value in ('=',',',')')),body_start)
                self.color_annotation(typ,annotation_start,end)
                # A later parameter may share a preceding contract's name.
                # Advance to the actual separator before locating that binding.
                while search<len(header) and header[search].start<end:search+=1
            if len(original)>=4 and original[3] is not None:
                _,default_end=self.map.span(original[3])
                while search<len(header) and header[search].start<default_end:search+=1
        if node[2]!='Any':
            tail=[]
            for item in reversed(header):
                if item.kind=='id' or item.value=='.':tail.append(item)
                else:break
            for item in tail:
                if item.kind=='id':self.colors[item.start]='type'
        self.walk_block(node[3],local)
        if node[0] in ('gen_fn','async_gen_fn'):
            yielded = self.generator_yield_info(node[3])
            info.result_info.yielded_info = yielded
            # Definitions and class members were published before their bodies
            # were walked. Update the same inert result carrier used by aliases.
            target = binding
            while target is not None and target.descriptor in ('staticmethod','classmethod'):
                target = target.wrapped
            if target is not None and target.result_info is not None:
                target.result_info.yielded_info = yielded
        return info

    def generator_yield_info(self, body):
        values, pending, steps = [], [body], 0
        while pending:
            node = pending.pop(); steps += 1
            if steps > 20000: return Info()
            if isinstance(node, Node):
                if node[0] in ('fn','gen_fn','async_fn','async_gen_fn','class','quote','macro','gen_comp','async_gen_comp'):
                    continue
                if node[0] == 'yield':
                    values.append(self.node_infos.get(id(node[1]),Info()))
                elif node[0] == 'yield_from':
                    values.append(Info())
                else: pending.extend(node[1:])
            elif isinstance(node, (tuple,list)):
                pending.extend(node)
        return self.pattern_element(Info('List',elements=values)) if values else Info()

    @staticmethod
    def class_value(info):
        return info.class_info if info and info.type=='Class' else None

    def descriptor_info(self, kind, value=None, setter=None, previous=None, deleter=None):
        info=Info('Descriptor','property',kind+' 描述符',descriptor=kind,wrapped=value if kind!='property' else None,getter=value if kind=='property' else None,setter=setter,previous=previous,deleter=deleter)
        if kind=='property':
            for operation in ('getter','setter','deleter'):
                info.members[operation]=Info('Function','function',operation+'(function) -> Descriptor',parameters=[('function','Function','positional',False)],returns='Descriptor',builtin='property.'+operation,wrapped=info)
        if kind=='property':
            info.members.update(fget=value or Info('Null'),fset=setter or Info('Null'),fdel=deleter or Info('Null'))
        return info

    def decorate_info(self, decorator, value):
        spec = spec_for(decorator.builtin)
        if spec is not None and spec.inference_rule == 'compiled_function':
            return self.compiled_function_info(value)
        if decorator.context_decorator:
            target = self.callable_info(value)
            asynchronous = decorator.context_decorator == 'asynccontextmanager'
            expected = 'AsyncGenerator' if asynchronous else 'Generator'
            if target.type != 'Function' or target.returns != expected:
                return Info('Any',detail='Any · 生成器资源工厂的返回类型未知',uncertain_descriptor=True)
            manager = 'AsyncContextManager' if asynchronous else 'ContextManager'
            stream = target.result_info
            result = Info(manager,detail=manager+' · 单次资源作用域',yielded_info=stream.yielded_info if stream else None)
            return replace(target,returns=manager,result_info=result,awaited='Any',awaited_info=None,
                detail=signature(target.detail.split('(',1)[0].split()[-1],target.parameters,manager,target.parameter_patterns)+' · 生成器资源工厂')
        if decorator.builtin in ('staticmethod','classmethod','property'):
            return self.descriptor_info(decorator.builtin,value)
        if decorator.builtin in ('property.getter','property.setter','property.deleter') and decorator.wrapped:
            old=decorator.wrapped
            return self.descriptor_info('property',value if decorator.builtin.endswith('.getter') else old.getter,value if decorator.builtin.endswith('.setter') else old.setter,old,value if decorator.builtin.endswith('.deleter') else old.deleter)
        return Info('Any',detail='Any · 用户装饰器返回值未知',uncertain_descriptor=True)

    @staticmethod
    def return_info(info):
        if info.returns in ('ContextVar','ContextToken','ContextScope','Context'):return binding_value_info(info.returns)
        value=info.result_info or Info(info.returns,detail=info.returns,awaited=info.awaited,awaited_info=info.awaited_info)
        return replace(value,declaration_file='',declaration_start=-1,declaration_end=-1)

    @staticmethod
    def namespace_member(klass, name, member):
        if klass.naming_effects and member.mutable:
            return Info('Any',kind='property',mutable=True,uncertain_descriptor=True,write_unknown=True,detail=name+': Any · 命名钩子可改变可变成员')
        return member

    def raw_protocol(self, klass, name):
        """Nominal raw MRO lookup only. Never resolve an attribute or run a hook."""
        key=(id(klass),name)
        if klass.class_complete and key in self.protocol_cache:return self.protocol_cache[key]
        value=None
        for base in klass.mro[:256]:
            self.member_steps+=1
            if self.member_steps>20000:return None
            if name in base.attribute_mutations:
                value=Info('Any',uncertain_descriptor=True,write_unknown=True,detail=name+': Any · 协议已在源码中动态修改');break
            if name in base.own_members:
                value=self.namespace_member(base,name,base.own_members[name]);break
        if klass.class_complete:self.protocol_cache[key]=value
        return value

    def invalidate_attributes(self, value, key=None):
        if value.type!='Class' or value.class_info is None:return
        hooks={'__getattribute__','__getattr__','__setattr__','__delattr__','__dir__'}
        changed=hooks if key is None else hooks.intersection((key,))
        if changed:
            value.class_info.attribute_mutations.update(changed)
            self.protocol_cache.clear()

    def attribute_mutation_builtin(self, callee, nodes, infos):
        if callee.builtin not in ('setattr','default_setattr','delattr','default_delattr'):return
        values={};position=0;names=('value','name','item')
        for node,info in zip(nodes,infos):
            if node[0] in ('spread','kwspread'):
                target=values.get('value')
                if target is not None:self.invalidate_attributes(target)
                return
            if node[0]=='keyword':key=node[1]
            else:key=names[position] if position<len(names) else '';position+=1
            values[key]=info
        target=values.get('value')
        if target is not None:self.invalidate_attributes(target,values.get('name',Info()).literal_string)

    def attribute_hook(self, value, name):
        """Inert raw metadata only; ordinary access cannot certify stored values."""
        if value.type == 'Super': return None
        klass = (value.metaclass_info or (value.class_info.metaclass_info if value.class_info else None)) if value.type == 'Class' else value.class_info
        if klass is None:return None
        member=self.raw_protocol(klass,name)
        if self.member_steps>20000:
            return Info('Any',uncertain_descriptor=True,write_unknown=True,detail=name+': Any · 属性协议分析预算已耗尽')
        return member

    def attribute_info(self, value, name):
        if self.attribute_hook(value, '__getattribute__') is not None:
            return Info('Any',kind='property',mutable=True,write_unknown=True,
                        detail=name+': Any · __getattribute__ 可替换成员；不执行钩子或推断签名')
        member=value.members.get(name)
        if self.attribute_hook(value, '__getattr__') is not None:
            raw=self.raw_protocol(value.class_info,name) if value.class_info is not None else None
            descriptor=raw is not None and (raw.descriptor=='property' or '__get__' in self.protocols(raw))
            if member is None or member.uncertain_descriptor or descriptor:
                return Info('Any',kind='property',detail=name+': Any · 缺失或描述符失败可由 __getattr__ 替换')
        return member or Info()

    def attribute_members(self, value):
        # Only declared names are suggestions; never run __dir__ to invent names.
        return {name:self.attribute_info(value,name) for name in value.members}

    def protocols(self, value):
        if value.class_info is None or value.type=='Class':return {}
        return {name:info for name in ('__get__','__set__','__delete__','__set_name__')
                if (info:=self.raw_protocol(value.class_info,name)) is not None}

    def resolve_member(self, name, member, class_access=False):
        self.member_steps+=1
        key=(id(member),class_access)
        if key in self.resolving_members or len(self.resolving_members)>=32 or self.member_steps>20000:
            return Info('Any',detail=name+': Any · 访问协议递归或动态',uncertain_descriptor=True,write_unknown=True)
        self.resolving_members.add(key)
        try:
            if member.uncertain_descriptor and (member.type=='Descriptor' or member.class_info is not None):
                return Info('Any',kind='property',mutable=member.mutable,detail=name+': Any · 描述符行为未知',uncertain_descriptor=True,write_unknown=True)
            if member.descriptor=='staticmethod':return self.copy_origin(member.wrapped or Info(),member)
            if member.descriptor=='classmethod':
                target=self.callable_info(member.wrapped or Info())
                parameters=target.parameters[1:]
                patterns={key:target.parameter_patterns[key] for key,_,_,_ in parameters if key in target.parameter_patterns}
                return self.copy_origin(replace(target,parameters=parameters,parameter_patterns=patterns,detail=signature(name,parameters,target.returns,patterns)),member)
            if member.descriptor=='property' and not class_access:
                value=self.return_info(self.callable_info(member.getter)) if member.getter else Info()
                return self.copy_origin(replace(value,kind='property',mutable=member.setter is not None,setter=member.setter,deleter=member.deleter,detail=name+': '+value.type+(' · 可读写属性' if member.setter else ' · 只读属性')+(' · 可删除' if member.deleter else '')),member)
            protocols=self.protocols(member)
            if self.member_steps>20000 or any(slot.mutable or slot.uncertain_descriptor for key,slot in protocols.items() if key in ('__get__','__set__','__delete__')):
                return Info('Any',kind='property',mutable=True,uncertain_descriptor=True,write_unknown=True,detail=name+': Any · 访问协议可能改变')
            if any(key in protocols for key in ('__get__','__set__','__delete__')):
                data='__set__' in protocols or '__delete__' in protocols
                getter=protocols.get('__get__')
                if getter is not None:
                    call=self.callable_info(self.resolve_member('__get__',getter))
                    value=self.return_info(call)
                else:value=member
                # A user descriptor can compute any value. Its result metadata
                # is not evidence that the accessed name denotes that result's
                # declaration (or even the descriptor's storage slot).
                value=replace(value,declaration_file='',declaration_start=-1,declaration_end=-1)
                inferred=value.type
                if class_access:
                    # A class read can invoke __get__, but assignment still
                    # targets its own declaration slot; set_member uses raw info.
                    return replace(value,kind='property',detail=name+': '+value.type+' · 类访问协议')
                if not data or getter is None:value=Info('Any')
                setter=self.resolve_member('__set__',protocols['__set__']) if '__set__' in protocols else None
                deleter=self.resolve_member('__delete__',protocols['__delete__']) if '__delete__' in protocols else None
                return replace(value,kind='property',mutable=not data or setter is not None,setter=setter,deleter=deleter,
                               write_unknown=not data or (setter is not None and setter.type=='Any'),
                               detail=name+': '+value.type+(' · 数据描述符' if data else ' · 非数据描述符，__get__ → '+inferred+'；实例自有字段可覆盖'))
            if member.method and name!='__new__' and (not class_access or name in ('__init_subclass__','__prepare__','__class_getitem__')):
                parameters=member.parameters[1:]
                patterns={key:member.parameter_patterns[key] for key,_,_,_ in parameters if key in member.parameter_patterns}
                return replace(member,parameters=parameters,parameter_patterns=patterns,detail=signature(name,parameters,member.returns,patterns))
            return replace(member,declaration_file='',declaration_start=-1,declaration_end=-1) if member.uncertain_descriptor else member
        finally:self.resolving_members.discard(key)

    def bound_members(self, klass, after=None, class_access=False):
        if self.member_steps>20000:return {}
        members={};lineage=klass.mro[:256]
        if after is not None:
            if after not in lineage:return members
            lineage=lineage[lineage.index(after)+1:]
        for item in reversed(lineage):
            members.update({name:self.namespace_member(item,name,member) for name,member in item.own_members.items()})
        return {name:self.resolve_member(name,member,class_access) for name,member in members.items()}

    def instance_info(self, klass, exact=False):
        return Info(klass.detail.split(' · ')[0],detail=klass.detail.split(' · ')[0]+' 实例',members=self.bound_members(klass),class_info=klass,exact_instance=exact)

    def type_info(self, typ, env):
        if typ=='Type':return Info('Type',detail='Type · 原生 Type 的子类（元类），仍属于 Class 值')
        if typ=='UIArtifact':return Info('UIArtifact',detail='UIArtifact · 当前解释器所有的不可变页面/报告/幻灯片；通过 ui_info/ui_definition 查询纯数据')
        if typ=='ClassMember':return replace(shape_info('ClassMember'),type='ClassMember',detail='ClassMember · 只读成员元信息')
        if typ in ('Fraction','Complex','Decimal'):return numeric_value_info(typ)
        if typ in ('Condition','HandlerScope','RestartScope','Restart'):return recovery_value_info(typ)
        if typ in ('PromptTag','PromptScope','EscapeContinuation','Continuation'):return control_prompt_info(typ)
        if typ in ('ContextVar','ContextToken','ContextScope','Context'):return binding_value_info(typ)
        if typ=='DecimalContext':return decimal_context_value_info()
        if typ=='Slice':return slice_info()
        if typ=='Error':return error_value_info()
        if typ=='TaskGroup':return task_group_value_info()
        if typ=='CancelShield':return cancel_shield_value_info()
        if typ in ('ExitStack','AsyncExitStack'):return resource_stack_value_info(typ)
        if typ=='Channel':return Info('Channel',detail='Channel · 本轮共享通道')
        parts=typ.split('.');value=env.get(parts[0])
        for part in parts[1:]:value=value.members.get(part) if value else None
        if value is not None and value.type=='Contract':
            return Info('Any',detail=typ+' · 动态组合契约；不执行构造或推断字段')
        klass=self.class_value(value)
        return self.instance_info(klass) if klass else Info(typ,detail=typ)

    def callable_info(self, value):
        # Only the explicitly declared native value protocol applies here.
        # User classes with a coincidentally equal display name keep their own
        # __call__ inference; Type/exception member specs are not value calls.
        spec = call_spec_for(value.type) if value.class_info is None else None
        if spec is not None and spec.inference_rule == 'receiver_call':
            return native_info(spec, display_name=value.type)
        if value.descriptor=='staticmethod':return self.callable_info(value.wrapped) if value.wrapped else Info()
        if value.builtin=='Type':return native_info(spec_for('Type'))
        if value.type=='Class':
            klass=self.class_value(value)
            if klass is None:return Info('Function','function','Class → Instance（构造签名未知）',returns='Instance')
            allocator=self.raw_protocol(klass,'__new__')
            native_exception=allocator is not None and self.callable_info(allocator).builtin=='Exception.__new__'
            if allocator is not None and not native_exception:
                return Info('Function','function',klass.detail.split(' · ')[0]+' → Any（自定义 __new__，不推断实例身份）',returns='Any')
            instance=self.instance_info(klass,exact=True)
            initializer=instance.members.get('__init__')
            if native_exception and initializer is None:
                params=[('args','Any','varargs',False)]
                return Info('Function','function',signature(instance.type,params,instance.type),parameters=params,
                            returns=instance.type,result_info=instance)
            if self.member_steps>20000 or (initializer is not None and (initializer.type!='Function' or (not initializer.parameters and '(' not in initializer.detail))):
                return Info('Function','function',instance.type+' → '+instance.type+'（构造签名未知）',returns=instance.type,result_info=instance)
            return replace(initializer or Info('Function','function'),type='Function',parameters=list(initializer.parameters) if initializer else [],returns=instance.type,result_info=instance,detail=signature(instance.type,initializer.parameters if initializer else [],instance.type,initializer.parameter_patterns if initializer else None))
        if value.class_info is not None:
            target=value.members.get('__call__')
            return target if target is not None else value
        return value

    def operator_result(self, value, method, other=None):
        # Only exact constructor results can rule out a runtime subclass override.
        # Reflected dispatch, mutable slots and dynamic descriptors remain unknown.
        if value.type=='Class' or value.class_info is None or not value.exact_instance:return Info()
        if other is not None and (other.type in ('Any','Instance') or
                (other.class_info is not None and (not other.exact_instance or other.class_info is not value.class_info))):return Info()
        member=self.raw_protocol(value.class_info,method)
        if member is None or member.mutable or member.uncertain_descriptor or self.member_steps>20000:return Info()
        target=self.resolve_member(method,member)
        if target.type!='Function' or target.mutable or target.uncertain_descriptor or '(' not in target.detail:return Info()
        result=self.return_info(target)
        return Info() if result.type in ('Any','NotImplemented') else result

    def binary_result(self, op, left, right, right_node=None, augmented=False):
        if augmented and left.class_info is not None and left.type!='Class' and left.exact_instance:
            member=self.raw_protocol(left.class_info,INPLACE[op])
            if member is not None:
                if member.mutable or member.uncertain_descriptor or self.member_steps>20000:return Info()
                target=self.resolve_member(INPLACE[op],member)
                if target.type!='Function' or target.mutable or target.uncertain_descriptor or '(' not in target.detail:return Info()
                result=self.return_info(target)
                # A concrete return contract proves the first dispatch. Only an
                # explicit singleton contract proves that binary fallback runs.
                if result.type!='NotImplemented':return result
        if op in ('==','!=','<','>','<=','>=','&&','||'):return Info('Bool')
        if op=='|>':return Info(right.returns)
        if op in ('|','&','-','^') and left.type==right.type=='Set':
            elements=None
            if left.elements is not None and (op in ('&','-') or right.elements is not None):
                elements=left.elements if op in ('&','-') else left.elements+right.elements if len(left.elements)+len(right.elements)<=10000 else None
            return Info('Set',elements=elements)
        if op in ('&','|','^','<<','>>'):
            if left.type==right.type=='Int':return Info('Int')
        elif op=='+' and left.type==right.type and left.type in ('String','List','Tuple'):return Info(left.type)
        elif 'Decimal' in (left.type,right.type):
            # Inert result kinds only: never construct values or evaluate context.
            kinds=(left.type,right.type)
            if 'Complex' in kinds and op in ('+','-','*','/'):
                return numeric_value_info('Complex')
            if all(typ in ('Int','Decimal') for typ in kinds):
                if op in ('+','-','*','/','%'):return numeric_value_info('Decimal')
                if op=='//':return Info('Int')
                if op=='**' and right.type=='Int':return numeric_value_info('Decimal')
            return Info()
        elif left.type in ('Int','Number','Fraction','Complex') and right.type in ('Int','Number','Fraction','Complex') and ('Fraction' in (left.type,right.type) or 'Complex' in (left.type,right.type)):
            if op in ('+','-','*','/'):
                typ='Complex' if 'Complex' in (left.type,right.type) else 'Number' if 'Number' in (left.type,right.type) else 'Fraction'
                return numeric_value_info(typ) if typ in ('Fraction','Complex') else Info(typ)
            if op in ('%','//') and 'Complex' not in (left.type,right.type):
                typ='Number' if 'Number' in (left.type,right.type) else 'Int' if op=='//' else 'Fraction'
                return numeric_value_info(typ) if typ=='Fraction' else Info(typ)
            if op=='**' and right.type=='Int' and left.type in ('Fraction','Complex'):
                return numeric_value_info(left.type)
            # Invalid or runtime-sensitive combinations remain conservative.
            return Info()
        elif left.type in ('Int','Number') and right.type in ('Int','Number'):
            typ='Number' if op=='/' or 'Number' in (left.type,right.type) else 'Int'
            if op=='**' and typ=='Int':
                if right_node is not None and right_node[0]=='literal' and type(right_node[1]) is int:typ='Int' if right_node[1]>=0 else 'Number'
                elif right_node is not None and right_node[0]=='unary' and right_node[1]=='-' and right_node[2][0]=='literal' and type(right_node[2][1]) is int:typ='Number' if right_node[2][1]>0 else 'Int'
                else:return Info('Any',detail='Int | Number · 整数幂结果取决于指数是否为负')
            return Info(typ,detail=typ)
        return self.operator_result(left,ARITHMETIC.get(op,('',))[0],right)

    def augmented_write(self, target, result, env, node):
        if target[0]=='name':
            binding=env.get(target[1])
            if binding is not None:
                if not binding.mutable:self.error(LanguageError('不可赋值给只读绑定 '+target[1],code='immutable_binding'),target)
                self.check_value(result,binding.type,env,node)
            return
        if target[0] not in ('index','subscript'):return
        obj=self.node_infos.get(id(target[1]),Info());key=target[2]
        self.invalidate_attributes(obj,key[1] if key[0]=='literal' and type(key[1]) is str else None)
        if target[0]=='subscript':
            if obj.class_info is None or obj.type=='Class' or not obj.exact_instance:return
            item_set=self.raw_protocol(obj.class_info,'__setitem__')
            if self.member_steps>20000 or item_set is not None:return
        if key[0]!='literal' or not isinstance(key[1],str):return
        if self.attribute_hook(obj,'__setattr__') is not None:return
        field=self.raw_protocol(obj.class_info,key[1]) if obj.type=='Class' and obj.class_info else obj.members.get(key[1])
        if field is None:return
        if not field.mutable and not field.uncertain_descriptor:self.error(LanguageError('成员只读 '+key[1],code='immutable_binding'),target)
        if field.setter:
            parameters=field.setter.parameters[1:]
            if parameters:self.check_value(result,parameters[-1][1],env,node)
        elif not field.write_unknown:self.check_value(result,field.type,env,node)

    def operator_builtin(self, callee, nodes, infos):
        names={'abs':('x',),'sqrt':('x',),'pow':('base','exponent'),'floordiv':('left','right')}.get(callee.builtin)
        if names is None:return None
        if any(node[0] in ('spread','kwspread') for node in nodes):return Info()
        values={};position=0
        for node,info in zip(nodes,infos):
            if node[0]=='keyword':key=node[1]
            else:key=names[position] if position<len(names) else '';position+=1
            values[key]=info
        if any(name not in values for name in names):return Info()
        left=values[names[0]];right=values[names[1]] if len(names)>1 else None
        if callee.builtin=='sqrt':
            return numeric_value_info('Decimal') if left.type=='Decimal' else Info('Number') if left.type in ('Int','Number','Fraction') else Info('Real')
        if left.type in ('Fraction','Complex','Decimal') or right is not None and right.type in ('Fraction','Complex','Decimal'):
            if right is None:
                return Info('Real') if left.type=='Complex' else numeric_value_info(left.type)
            return self.binary_result('**' if callee.builtin=='pow' else '//',left,right)
        if left.type in ('Int','Number') and (right is None or right.type in ('Int','Number')):
            return Info(left.type if right is None else 'Number')
        return self.operator_result(left,{'abs':'__abs__','pow':'__pow__','floordiv':'__floordiv__'}[callee.builtin],right)

    def tuple_builtin(self, callee, nodes, infos):
        if callee.builtin!='tuple':return None
        if not nodes:return Info('Tuple',elements=[])
        if len(nodes)!=1 or nodes[0][0] in ('spread','kwspread'):return Info('Tuple')
        if nodes[0][0]=='keyword' and nodes[0][1]!='items':return Info('Tuple')
        value=infos[0];self.check_sync_source(value,nodes[0])
        return Info('Tuple',elements=value.elements if value.type in ('List','Tuple') else None)

    def set_builtin(self, callee, nodes, infos):
        if callee.builtin not in ('set_of','set_add','set_discard','set_is_disjoint'):return None
        if callee.builtin=='set_is_disjoint':return Info('Bool')
        if any(node[0] in ('spread','kwspread') for node in nodes):return Info('Set')
        names=('items',) if callee.builtin=='set_of' else ('value','item')
        values={};position=0
        for node,info in zip(nodes,infos):
            if node[0]=='keyword':name=node[1];expression=node[2]
            else:name=names[position] if position<len(names) else '';position+=1;expression=node
            values[name]=(info,expression)
        if callee.builtin=='set_of':
            if not nodes:return Info('Set',elements=[])
            if 'items' not in values:return Info('Set')
            value,expression=values['items'];self.check_sync_source(value,expression)
            return Info('Set',elements=value.elements if value.type in ('List','Tuple','Set') else [Info('String')] if value.type=='String' else None)
        value=values.get('value',(Info(),None))[0]
        elements=value.elements if value.type=='Set' else None
        if callee.builtin=='set_add':
            item=values.get('item',(Info(),None))[0]
            elements=None if elements is None or len(elements)>=10000 else elements+[item]
        return Info('Set',elements=elements)

    def iteration_builtin(self, callee, nodes, infos):
        if callee.builtin not in ('iter','aiter'):return None
        asynchronous=callee.builtin=='aiter'
        handle,stream=('AsyncIterator','AsyncGenerator') if asynchronous else ('Iterator','Generator')
        unknown=Info('Any',detail=handle+' | '+stream+' · 动态工厂结果未知')
        if len(nodes)!=1 or nodes[0][0] in ('spread','kwspread'):return unknown
        if nodes[0][0]=='keyword' and nodes[0][1]!='value':return unknown
        value=infos[0]
        if value.type in (handle,stream):return value
        if asynchronous and value.type=='Channel':return Info('AsyncIterator',detail='AsyncIterator · 独立通道消费者；关闭游标不会关闭共享通道')
        if not asynchronous and value.type in ('List','Tuple','Set','String','Context','HashMap'):return Info(handle,detail=handle+' · 同步游标')
        if value.type=='Class' or value.class_info is None or not value.exact_instance:return unknown
        factory=self.operator_result(value,'__aiter__' if asynchronous else '__iter__')
        if factory.type in (handle,stream):return factory
        if factory.class_info is not None and factory.type!='Class':return Info(handle,detail=handle+' · 用户协议适配器')
        return unknown

    def check_sync_source(self, info, node):
        # This is a protocol, not an annotation type. User classes and unknown
        # values remain conservative; known native asynchronous values do not
        # become synchronous sources merely because their step is awaitable.
        if info.class_info is None and info.type in TYPES and info.type not in ('Any','Instance','List','Tuple','Set','String','Generator','Iterator','Context','HashMap'):
            self.error(LanguageError('需要同步可迭代源（List、Tuple、Set、HashMap、String、Generator、Iterator、Context 或用户迭代协议），实际为 '+info.type,code='type_mismatch'),node)

    def compiled_function_info(self, value):
        # Compilation returns an ordinary Function with its actual language
        # parameter contract. It never invokes the target during analysis.
        target = self.callable_info(value)
        if target.type != 'Function':
            return Info('Function','function','Function · 编译目标的参数签名未知')
        return replace(target, builtin='', context_decorator='',
                       declaration_file='', declaration_start=-1, declaration_end=-1,
                       detail=target.detail+' · 编译选择保留原函数契约；实际后端见 compile_info')

    def code_builtin(self, callee, nodes, infos):
        spec = spec_for(callee.builtin)
        if spec is None or spec.inference_rule not in ('code_value','code_function'):return None
        values={};position=0
        for node,info in zip(nodes,infos):
            if node[0] in ('spread','kwspread'):return Info(spec.result_type)
            if node[0]=='keyword':key,expression=node[1],node[2]
            else:
                key=callee.parameters[position][0] if position<len(callee.parameters) else ''
                expression=node;position+=1
            values[key]=(info,expression)
        if spec.inference_rule=='code_function':
            value=values.get('code',(Info(),None))[0]
            return replace(value.code_function,detail=value.code_function.detail+' · 显式 Code 构造',declaration_file='',declaration_start=-1,declaration_end=-1) if value.code_function else Info('Function','function','Function · 动态 Code 签名未知')
        result=Info('Code')
        expression=values.get('source',(None,None))[1]
        if expression is not None and expression[0]=='literal' and type(expression[1]) is str and len(expression[1])<=100000:
            try:
                from .code_values import function_node
                _,fn=function_node(Parser(expression[1],'code/static.chaos').program())
                result.code_function=self.function_info(fn)
            except (LanguageError,ResourceLimit,RecursionError):pass
        return result

    def compilation_builtin(self, callee, nodes, infos):
        spec = spec_for(callee.builtin)
        if spec is None or spec.inference_rule != 'compiled_function':return None
        if any(node[0] in ('spread','kwspread') for node in nodes):
            return Info('Function','function','Function · 动态实参的编译目标签名未知')
        values, position = {}, 0
        for node, info in zip(nodes, infos):
            if node[0] == 'keyword':key = node[1]
            else:
                key = callee.parameters[position][0] if position < len(callee.parameters) else ''
                position += 1
            values[key] = info
        return self.compiled_function_info(values.get('fn',Info()))

    def multiple_value_builtin(self, callee, nodes, infos):
        spec = spec_for(callee.builtin)
        if spec is None or not (spec.inference_rule or '').startswith('multiple_'):return None
        rule=spec.inference_rule
        if rule=='multiple_capture':return Info('List')
        if rule=='multiple_nth':return Info()
        if any(node[0] in ('spread','kwspread') for node in nodes):return Info()
        if rule=='multiple_values':return infos[0] if infos else Info('Null')
        values={};position=0
        for node,info in zip(nodes,infos):
            if node[0]=='keyword':key=node[1]
            else:key=callee.parameters[position][0] if position<len(callee.parameters) else '';position+=1
            values[key]=info
        if rule=='multiple_values_list':
            source=values.get('items',Info())
            if source.type=='List' and source.elements is not None:
                return source.elements[0] if source.elements else Info('Null')
            return Info()
        key={'multiple_call':'function','multiple_bind':'consumer','multiple_prog1':'first'}[rule]
        # Completion state and generator terminal types are runtime evidence.
        # A handle producer does not certify a callable or terminal value here.
        if values.get(key,Info()).type in ('Task','Generator','AsyncGenerator'):return Info()
        target=self.callable_info(values.get(key,Info()))
        # Only explicitly async language callbacks are entered in this call.
        # An ordinary function returning a cold Task keeps that Task as a value.
        if rule!='multiple_call' and target.callback_async:return target.awaited_info or Info(target.awaited)
        return self.return_info(target) if target.type=='Function' else Info()

    def composition_builtin(self, callee, nodes, infos):
        source_name={'apply':'args','map':'xs','filter':'xs','fold':'items','any':'xs','all':'xs'}.get(callee.builtin)
        if source_name is None:return None
        # An expansion obscures positional binding; never guess its length or
        # execute an iterator to discover the callback or its result type.
        if any(node[0] in ('spread','kwspread') for node in nodes):return self.return_info(callee)
        values={};position=0
        for node,info in zip(nodes,infos):
            if node[0]=='keyword':key,expression=node[1],node[2]
            else:
                key=callee.parameters[position][0] if position<len(callee.parameters) else ''
                expression=node;position+=1
            values[key]=(info,expression)
        if source_name in values:self.check_sync_source(*values[source_name])
        if callee.builtin=='apply' and 'kwargs' in values:
            value,expression=values['kwargs']
            if expression[0] not in ('name','literal'):self.check_known(value.type,'Record | Null',expression)
        if callee.builtin=='apply' and 'fn' in values:
            target=self.callable_info(values['fn'][0])
            if target.type=='Function':return self.return_info(target)
        return self.return_info(callee)

    def context_result(self, value, asynchronous=False):
        """Use stable protocol annotations without evaluating a resource hook."""
        if value.type == 'CancelShield': return value
        if not asynchronous and value.type in ('DecimalContext','HandlerScope','RestartScope','ContextScope','ContextToken','PromptScope'): return value
        if value.type == ('AsyncContextManager' if asynchronous else 'ContextManager'):
            return value.yielded_info or Info()
        if value.type == ('AsyncExitStack' if asynchronous else 'ExitStack'): return value
        if asynchronous and value.type in ('Computer', 'TaskGroup', 'Robot'): return value
        result = self.operator_result(value, '__aenter__' if asynchronous else '__enter__')
        if not asynchronous: return result
        if result.type != 'Task': return Info()
        return result.awaited_info or Info(result.awaited)

    def generator_context_builtin(self, callee, nodes, infos):
        if callee.context_decorator:
            if len(nodes)==1 and nodes[0][0] not in ('spread','kwspread') and (nodes[0][0]!='keyword' or nodes[0][1]=='factory'):
                return self.decorate_info(callee,infos[0])
            return Info()
        if callee.builtin != 'generator_context': return None
        values, position = {}, 0
        for node,info in zip(nodes,infos):
            if node[0] in ('spread','kwspread'): return Info()
            if node[0]=='keyword': key,expression=node[1],node[2]
            else:
                key=('generator','asynchronous')[position] if position<2 else ''
                expression=node;position+=1
            values[key]=(info,expression)
        asynchronous = values.get('asynchronous')
        if asynchronous is None: flag=False
        elif asynchronous[1][0]=='literal' and type(asynchronous[1][1]) is bool: flag=asynchronous[1][1]
        else:return Info()
        stream=values.get('generator',(Info(),None))[0]
        manager='AsyncContextManager' if flag else 'ContextManager'
        expected='AsyncGenerator' if flag else 'Generator'
        return Info(manager,detail=manager+' · 单次资源作用域',yielded_info=stream.yielded_info if stream.type==expected else None)

    def resource_stack_builtin(self, callee, nodes, infos):
        # Native identity comes from the receiver's AST-derived metadata. Local
        # spellings, mutable aliases and dynamic mode expressions prove nothing.
        spec = spec_for(callee.builtin)
        if spec is None or spec.inference_rule not in (
                'exit_stack','stack_enter','stack_enter_async','stack_callback','stack_transfer'):
            return None
        rule = spec.inference_rule
        if rule == 'stack_transfer':return resource_stack_value_info(spec.result_type)
        unknown = Info('Task') if rule == 'stack_enter_async' else Info()
        if any(node[0] in ('spread','kwspread') for node in nodes):return unknown
        values, position = {}, 0
        for node,info in zip(nodes,infos):
            if node[0]=='keyword':
                key,expression=node[1],node[2]
                if any(name==key and mode=='positional_only' for name,_,mode,_ in callee.parameters):continue
            else:
                key=callee.parameters[position][0] if position<len(callee.parameters) else ''
                expression=node;position+=1
            values[key]=(info,expression)
        if rule == 'exit_stack':
            asynchronous = values.get('asynchronous')
            if asynchronous is None:flag=False
            elif asynchronous[1][0]=='literal' and type(asynchronous[1][1]) is bool:flag=asynchronous[1][1]
            else:return Info()
            return resource_stack_value_info('AsyncExitStack' if flag else 'ExitStack')
        if rule == 'stack_callback':
            value = values.get(callee.parameters[0][0],(Info(),None))[0]
            # Registration returns the original callable, including its own
            # parameters, result metadata and legitimate declaration identity.
            return value
        manager = values.get('manager',(Info(),None))[0]
        # TaskGroup and CancelShield lifetimes belong to their lexical owner and
        # cannot be dynamically entered by a resource stack.
        result = Info() if manager.type in ('TaskGroup', 'CancelShield') else self.context_result(manager,rule == 'stack_enter_async')
        if rule == 'stack_enter_async':
            return Info('Task',detail='Task · await 后为 '+result.type,awaited=result.type,awaited_info=result)
        return result

    def team_role_info(self, implementation=None, *, raw=False):
        if raw:
            parameters=[('input','Any','positional',False)];result=Info();patterns={};contracts={}
        else:
            target=self.callable_info(implementation or Info())
            if target.type!='Function' or (not target.parameters and '(' not in target.detail):
                parameters=[('args','Any','varargs',False),('kwargs','Any','varkw',False)];result=Info();patterns={};contracts={}
            else:
                parameters=list(target.parameters);patterns=dict(target.parameter_patterns);contracts=dict(target.parameter_contracts)
                result=self.return_info(target)
                if result.type=='Task':result=result.awaited_info or Info(result.awaited)
        return Info('Function','function',signature('Team role',parameters,'Task',patterns)+' · 冷 Task；await 后为 '+result.type+'；转发实现参数并等待一层 Task',
                    parameters=parameters,parameter_patterns=patterns,parameter_contracts=contracts,returns='Task',awaited=result.type,awaited_info=result)

    def team_builtin(self, callee, nodes, infos):
        spec=spec_for(callee.builtin)
        if spec is None or spec.inference_rule not in ('team_from','team_bind','team_role','team_bindings','team_binding_info'):return None
        rule=spec.inference_rule;values={};position=0
        if not any(node[0] in ('spread','kwspread') for node in nodes):
            for node,info in zip(nodes,infos):
                if node[0]=='keyword':key=node[1]
                else:key=callee.parameters[position][0] if position<len(callee.parameters) else '';position+=1
                values[key]=info
        value=values.get('value',Info())
        if rule=='team_from':
            definition=values.get('definition',Info());roles=definition.members.get('roles',Info());entry=definition.members.get('entry',Info()).literal_string
            known=None
            if definition.closed_record and roles.elements is not None:
                names=[role.members.get('id',Info()).literal_string if role.closed_record else None for role in roles.elements]
                if all(type(name)is str for name in names) and len(set(names))==len(names):known={name:self.team_role_info(raw=True) for name in names}
            return Info('Team',detail='Team · 原始团队定义',team_roles=known,team_entry=entry if known is not None and entry in known else None)
        if rule=='team_bind':
            bindings=values.get('bindings',Info());roles=dict(value.team_roles) if value.team_roles is not None else None
            overrides={name:self.team_role_info(item) for name,item in bindings.members.items()} if bindings.closed_record else None
            if roles is not None:
                roles={name:(overrides.get(name,role) if overrides is not None else self.team_role_info()) for name,role in roles.items()}
            entry=value.team_entry
            call=roles.get(entry) if roles is not None and entry in roles else None
            return replace(call or self.team_role_info(),team_roles=roles,team_entry=entry,team_overrides=overrides)
        if rule=='team_bindings':return Info('Record',detail='Record · 团队角色 callable',members=dict(value.team_roles if value.team_roles is not None else value.team_overrides or {}),closed_record=value.team_roles is not None)
        if rule=='team_binding_info':
            result=shape_info('TeamBindingInfo')
            if value.team_overrides is not None:
                result.members['bindings']=Info('Record',members={name:Info('Record',members={'kind':Info('String','property','kind: String · 实际实现种类')}) for name in value.team_overrides},closed_record=True)
            return result
        role=values.get('role',Info()).literal_string
        if value.team_roles is not None and role in value.team_roles:return value.team_roles[role]
        if value.team_overrides is not None and role in value.team_overrides:return value.team_overrides[role]
        return self.team_role_info(raw=value.type=='Team')

    def task_builtin(self, callee, nodes, infos):
        # spawn returns the exact Task, including statically known completion
        # metadata. Dynamic argument expansion does not prove a particular Task.
        spec = spec_for(callee.builtin)
        if spec is None or spec.inference_rule != 'same_task': return None
        if len(nodes) != 1 or nodes[0][0] in ('spread','kwspread'): return Info('Task')
        if nodes[0][0] == 'keyword' and nodes[0][1] != 'task': return Info('Task')
        value = infos[0]
        if value.type != 'Task': return Info('Task')
        return replace(value,kind='variable',detail='Task · spawn 保留任务完成类型')

    def walk_comprehension(self, node, env):
        # Binding order differs from textual order: projection uses all clauses,
        # each iterable uses only preceding bindings, and guards use their prefix.
        # Keep independent scope snapshots so later clauses cannot leak backwards.
        local = dict(env)
        start, end = self.map.span(node)
        self.scopes.append((start, end, dict(env)))
        if node[0] == 'async_gen_comp':
            token = next(iter(self.between(start, self.map.span(node[1])[0])), None)
            if token is not None and token.value == 'async': self.colors[token.start] = 'keyword'
        for clause in node[-1]:
            expression = clause[1] if clause[0] == 'comp_if' else clause[2]
            left, right = self.map.span(expression)
            self.scopes.append((left, right, dict(local)))
            info = self.walk(expression, local)
            if clause[0] == 'comp_if':
                self.check_known(info.type, 'Bool', expression)
                continue
            asynchronous = clause[0] in ('comp_async_for', 'comp_async_for_pattern')
            if asynchronous:
                for token in self.between(self.map.span(clause)[0], left):
                    if token.value == 'async': self.colors[token.start] = 'keyword'
                if info.class_info is None and info.type in TYPES and info.type not in ('Any', 'Instance', 'AsyncGenerator', 'AsyncIterator', 'Channel'):
                    self.error(LanguageError('需要异步可迭代源，实际为 '+info.type, code='type_mismatch'), expression)
            else:
                self.check_sync_source(info, expression)
            element = self.pattern_element(info,asynchronous).type if info.type=='Set' else 'String' if not asynchronous and info.type == 'String' else 'Any'
            local = dict(local)
            if clause[0].endswith('_pattern'):
                self.bind_pattern(clause[1], local, self.pattern_element(info,asynchronous), lookup=dict(local), forward=True)
            else:
                bound = self.declare(clause[1], Info(element, detail=clause[1]+': '+element+' · 推导绑定'), local, clause, after='for')
                self.comprehension_bindings.add(id(bound))
        for expression in node[1:-1]:
            left, right = self.map.span(expression)
            self.scopes.append((left, right, dict(local)))
            info = self.walk(expression, local)
            if node[0] == 'record_comp' and expression is node[1]: self.check_known(info.type, 'String', expression)
        if node[0]=='set_comp':return Info('Set',elements=[self.node_infos.get(id(node[1]),Info())])
        return Info({'list_comp':'List', 'record_comp':'Record', 'gen_comp':'Generator', 'async_gen_comp':'AsyncGenerator'}[node[0]])

    def pattern_element(self, source, asynchronous=False):
        if asynchronous: return Info()
        if source.type == 'String': return Info('String')
        def common(values, depth=0):
            if not values or depth>16: return Info()
            first = values[0]
            if all(value is first for value in values): return first
            if any(value.type != first.type for value in values): return Info()
            if first.type in ('List','Tuple') and all(value.elements is not None and len(value.elements)==len(first.elements or []) for value in values):
                return Info(first.type,elements=[common([value.elements[index] for value in values],depth+1) for index in range(len(first.elements))])
            if first.type == 'Record':
                keys=set(first.members).intersection(*(set(value.members) for value in values[1:]))
                closed=all(value.closed_record and set(value.members)==keys for value in values)
                return Info('Record',members={key:common([value.members[key] for value in values],depth+1) for key in keys},closed_record=closed)
            return Info(first.type,detail=first.type)
        return common(source.elements) if source.type in ('List','Tuple','Set') and source.elements is not None else Info()

    def class_pattern_members(self, klass):
        """Stored data contracts only: accessing a member would infer getter results.

        Own instance slots and subclass overrides may exist, so this is an open,
        conservative view, not an exact field inventory or a mismatch proof.
        """
        members={};seen=set()
        for base in klass.mro[:256]:
            for name,original in base.own_members.items():
                self.member_steps+=1
                if self.member_steps>20000:return members
                if name in seen:continue
                seen.add(name)
                member=self.namespace_member(base,name,original)
                if member.method or member.descriptor or member.uncertain_descriptor:continue
                if '__get__' in self.protocols(member):continue
                members[name]=self.mutable_shape(member)
        return members

    def bind_pattern(self, node, env, value, *, lookup=None, mutable=False, forward=False, parameter=False, assignment=False, matching=False):
        """Project inert value metadata through a real pattern, never a protocol.

        Constructors resolve in the pre-binding environment. New declarations
        share one source evaluation, while each leaf retains its own token span.
        Reassignment projects onto existing identities and contracts; it neither
        declares leaves nor claims a mutable cell keeps this particular value.
        """
        lookup = dict(env) if lookup is None else lookup
        def visit(pattern, info):
            kind = pattern[0]
            if kind == 'p_bind':
                name = pattern[1]
                if assignment:
                    token = self.name_token(name, pattern)
                    binding = env.get(name)
                    if token: self.colors[token.start] = _semantic_role(binding) if binding is not None else 'variable'
                    if binding is None:
                        self.error(LanguageError('未定义可变绑定 '+name,code='undefined_name'),pattern)
                    else:
                        if token: self.references.append((token.start,token.end,binding))
                        if not binding.mutable:self.error(LanguageError('不可赋值给只读绑定 '+name,code='immutable_binding'),pattern)
                        self.check_value(info,binding.type,env,pattern)
                    return
                detail = name+': '+('Any' if mutable else info.type)+' · '+('解构参数' if parameter else '可变解构绑定' if mutable else '解构绑定')
                if not mutable and info.type == 'Function': detail += ' · '+info.detail
                role = _semantic_role(info,position='pattern')
                leaf_kind = 'parameter' if parameter else 'class' if role=='type' else role
                leaf = Info('Any', detail=detail, mutable=True) if mutable else replace(info, kind=leaf_kind, detail=detail, mutable=False)
                bound = self.declare(name, leaf, env, pattern)
                if forward: self.comprehension_bindings.add(id(bound))
            elif kind in ('p_list','p_tuple'):
                container_type='Tuple' if kind=='p_tuple' else 'List'
                if not matching:self.check_known(info.type, container_type, pattern)
                items = info.elements if info.type == container_type else None
                if not matching and items is not None and (len(items) < len(pattern[1]) or pattern[2] is None and len(items) != len(pattern[1])):
                    self.error(LanguageError(container_type+' 模式长度不匹配',code='pattern_mismatch'),pattern)
                for index, child in enumerate(pattern[1]):
                    visit(child, items[index] if items is not None and index < len(items) else Info())
                if pattern[2] is not None:
                    visit(pattern[2], Info(container_type, elements=None if items is None else items[len(pattern[1]):]))
            elif kind == 'p_record':
                self.color_record_keys(pattern)
                if not matching:self.check_known(info.type, 'Record', pattern)
                members = info.members if info.type == 'Record' else {}
                keys = {key for key, _ in pattern[1]}
                if not matching and info.closed_record and (not keys <= members.keys() or pattern[2] is None and keys != members.keys()):
                    self.error(LanguageError('记录模式字段不匹配',code='pattern_mismatch'),pattern)
                for key, child in pattern[1]: visit(child, members.get(key, Info()))
                if pattern[2] is not None:
                    visit(pattern[2], Info('Record', members={key:item for key,item in members.items() if key not in keys},closed_record=info.closed_record))
            elif kind in ('p_tag','p_class'):
                start, finish = self.map.span(pattern)
                constructor = None
                parts = pattern[1].split('.')
                for index, part in enumerate(parts):
                    constructor = lookup.get(part) if index == 0 else constructor.members.get(part) if constructor else None
                    token = next((token for token in self.between(start, finish) if token.kind == 'id' and token.value == part), None)
                    if token is not None:
                        start = token.end
                        self.colors[token.start] = ('type' if kind=='p_class' else 'function') if index == len(parts)-1 else 'variable'
                        if constructor: self.references.append((token.start,token.end,constructor))
                        else: self.diagnostic('未定义'+('类 ' if kind=='p_class' else '构造器 ')+pattern[1],token.start,token.end,'warning','undefined_name')
                if kind=='p_tag':
                    for child in pattern[2]: visit(child, Info())
                else:
                    self.color_record_keys(pattern)
                    klass=self.class_value(constructor)
                    actual=info.class_info if info.type!='Class' else None
                    if klass is not None and actual is not None and any(base is klass for base in actual.mro):klass=actual
                    # A declared Base accepts subclasses with different storage
                    # contracts. Only an exact actual class certifies leaf types.
                    members=self.class_pattern_members(klass) if klass is not None and actual is not None and info.exact_instance else {}
                    for key,child in pattern[2]:visit(child,members.get(key,Info()))
                    if pattern[3] is not None:
                        keys={key for key,_ in pattern[2]}
                        visit(pattern[3],Info('Record',members={key:item for key,item in members.items() if key not in keys}))
        visit(node, value)

    def walk_context(self, node, env):
        manager = self.walk(node[2], env)
        local = dict(env)
        start, _ = self.map.span(node)
        manager_start, manager_end = self.map.span(node[2])
        _, finish = self.map.span(node)
        # Comma items lower into nested nodes. Only the first has real with/
        # async tokens, and an inner manager can itself contain a context.
        for token in self.between(start, manager_start):
            if token.value in ('with', 'async'): self.colors[token.start] = 'keyword'
        header = self.between(manager_end, finish)
        marker = next((i for i, token in enumerate(header) if token.value == 'as'), None)
        token = header[marker + 1] if marker is not None and marker + 1 < len(header) else None
        if token is not None and (token.kind != 'id' or token.value != node[1]): token = None
        entered = self.context_result(manager, node[0] in ('async_with','async_with_pattern'))
        if node[0].endswith('_pattern'):
            left,right=self.map.span(node[1]);self.scopes.append((left,right,dict(env)))
            self.bind_pattern(node[1],local,entered,lookup=dict(env))
            return self.walk_block(node[3],local)
        info = replace(entered, kind='variable', mutable=False,
                       detail=node[1] + ': ' + entered.type + ' · ' + ('async with' if node[0] == 'async_with' else 'with'))
        self.declare(node[1], info, local, node, token=token)
        return self.walk_block(node[3], local)

    def item_result(self, value, key, key_node):
        # The bracket operator has a separate namespace from attributes. Only a
        # stable exact class can prove an item hook or its absence; no hook runs.
        if value.class_info is not None and value.type!='Class':
            if not value.exact_instance:return Info()
            member=self.raw_protocol(value.class_info,'__getitem__')
            if self.member_steps>20000:return Info()
            if member is not None:return self.operator_result(value,'__getitem__')
        if value.type=='Class' and value.class_info is not None:
            member=self.raw_protocol(value.class_info,'__class_getitem__')
            if self.member_steps>20000:return Info()
            if member is not None:
                if member.mutable or member.uncertain_descriptor:return Info()
                return self.return_info(self.callable_info(value.members.get('__class_getitem__',Info())))
        if value.type=='Slice':return Info()
        if value.type=='String':return Info('String')
        if value.type in ('List','Tuple'):
            if value.type=='Tuple' and value.elements is not None:
                index=key_node[1] if key_node[0]=='literal' and type(key_node[1]) is int else None
                if key_node[0]=='unary' and key_node[1] in ('-','+') and key_node[2][0]=='literal' and type(key_node[2][1]) is int:
                    index=(-1 if key_node[1]=='-' else 1)*key_node[2][1]
                if index is not None and -len(value.elements)<=index<len(value.elements):return value.elements[index]
            return Info(value.type if key.type=='Slice' else 'Any')
        if key_node[0]=='literal' and type(key_node[1]) is str:
            info=self.attribute_info(value,key_node[1])
            # Runtime values cannot expose a compile-time macro through brackets.
            return Info() if info.type=='Macro' else info
        return Info()

    def indexing_builtin(self, callee, nodes, infos):
        if callee.builtin!='getitem':return None
        if any(node[0] in ('spread','kwspread') for node in nodes):return Info()
        values={};position=0;names=('value','key')
        for node,info in zip(nodes,infos):
            if node[0]=='keyword':key,expression=node[1],node[2]
            else:key,expression=names[position] if position<len(names) else '',node;position+=1
            values[key]=(info,expression)
        if not all(name in values for name in names):return Info()
        return self.item_result(values['value'][0],values['key'][0],values['key'][1])

    def class_protocol_builtin(self,callee,nodes,infos):
        spec=spec_for(callee.builtin)
        if spec is None or spec.inference_rule not in ('class_member','class_members','alloc_instance','new_class','native_type','class_of'):return None
        rule=spec.inference_rule
        fallback=callee.result_info or Info(callee.returns)
        if rule=='native_type':
            if len(nodes)==1 and nodes[0][0] not in ('keyword','spread','kwspread'):
                value=infos[0]
                if value.type=='Class' and value.class_info:return value.class_info.metaclass_info or Info('Class')
                if value.class_info:return value.class_info
                return Info('Class')
            return fallback
        if any(node[0] in ('spread','kwspread') for node in nodes):return fallback
        values={};position=0
        for node,info in zip(nodes,infos):
            if node[0]=='keyword':key=node[1]
            else:key=callee.parameters[position][0] if position<len(callee.parameters) else '';position+=1
            values[key]=info
        if rule=='class_of':
            value=values.get('value',Info())
            if value.type=='Class' and value.class_info:return value.class_info.metaclass_info or fallback
            if value.class_info:return value.class_info
            return fallback
        if rule=='class_member':
            members=dict(fallback.members)
            members['value']=replace(values.get('value',Info()),kind='property',mutable=False)
            return replace(fallback,members=members)
        klass=self.class_value(values.get('klass'))
        if klass is None:return fallback
        if rule=='alloc_instance':return self.instance_info(klass,exact=True)
        if rule=='class_members':
            members={}
            for name,item in klass.own_members.items():
                value=self.namespace_member(klass,name,item)
                fields=shape_info('ClassMember').members
                fields['value']=replace(value,mutable=False)
                members[name]=Info('ClassMember',kind='property',members=fields,detail=name+': ClassMember · 自有成员只读快照')
            return Info('Record',members=members,closed_record=True)
        return fallback

    def walk_class(self,node,env):
        from .objects import c3_merge
        bases=[];unresolved_bases=False
        for base in node[2]:
            value=self.walk(base,env);klass=self.class_value(value)
            if klass is not None:bases.append(klass)
            else:
                # Metadata lookup only: resolving a descriptor or calling a base
                # adapter would execute the user's program inside the editor.
                protocol=self.raw_protocol(value.class_info,'__mro_entries__') if value.class_info is not None and value.type!='Class' else None
                if value.type in ('Any','Class','Instance') or protocol is not None or value.class_info is not None:
                    # Even a known native instance may acquire a callable field
                    # dynamically. Neither its resolved MRO nor metaclass is known.
                    unresolved_bases=True
                else:self.error(LanguageError('基类必须是 Class 或原生基底适配实例',code='type_mismatch'),base)
        from .class_creation import class_options
        explicit=None;unknown_meta=unresolved_bases
        for option in class_options(node):
            value=self.walk(option[2] if option[0]=='keyword' else option[1],env)
            if option[0]=='keyword':
                if option[1]=='metaclass':explicit=value
                token=self.name_token(option[1],option)
                if token:self.colors[token.start]='parameter'
            else:unknown_meta=True
        default_meta=self.base.get('Type')
        chosen=self.class_value(explicit) if explicit else default_meta
        unknown_meta=unknown_meta or bool(explicit and chosen is None)
        for base in bases:
            candidate=base.metaclass_info
            if candidate is None:unknown_meta=True;continue
            if chosen is None:chosen=candidate
            elif chosen in candidate.mro:chosen=candidate
            elif candidate not in chosen.mro:unknown_meta=True
        info=self.declaration_origin(Info('Class','class',node[1]+' · class'),self.name_token(node[1],node,after='class'))
        info.class_info=info;info.metaclass_info=None if unknown_meta else chosen
        try:
            if len({id(base) for base in bases})!=len(bases):raise LanguageError('重复基类',code='mro_error')
            info.mro=[info]+c3_merge([base.mro for base in bases]+[bases])
        except LanguageError as error:self.error(error,node);info.mro=[info]
        local=dict(env);local[node[1]]=info;defaults=dict(local);methods=[];naming_candidates=[]
        for wrapper in node[3][1]:
            member=wrapper[2] if wrapper[0]=='decorated' else wrapper
            decorators=[self.walk(item,defaults) for item in wrapper[1]] if wrapper[0]=='decorated' else []
            if member[0]=='def':
                value=self.function_info(member[2],member[1],local)
                for decorator in reversed(decorators):value=self.decorate_info(decorator,value)
                value.method=value.type=='Function' and not value.descriptor
                method_defaults=dict(defaults);naming_candidates.append(value)
            elif member[0] in ('let','var'):
                value=self.walk(member[3],defaults);naming_candidates.append(value)
                typ=member[2] if member[2]!='Any' else ('Any' if member[0]=='var' else value.type)
                self.check_value(value,member[2],local,member[3])
                if member[0]=='var':
                    # Mutable storage cannot certify a builtin identity or descriptor
                    # signature: class writes may replace it before later analysis.
                    value=replace(self.type_info(typ,local),kind='property',mutable=True,detail=member[1]+': '+typ,uncertain_descriptor=typ in ('Any','Descriptor') or self.type_info(typ,local).class_info is not None)
                else:value=replace(value,type=typ,kind='property',uncertain_descriptor=value.uncertain_descriptor or (typ in ('Any','Descriptor') and not value.descriptor and value.class_info is None),detail=member[1]+': '+typ+(' · '+value.detail if value.type=='Function' else ''))
                token=self.name_token(member[1],member,after=member[0])
                if token:self.color_annotation(member[2],token.end,self.map.span(member[3])[0])
            else:continue
            previous=info.own_members.get(member[1])
            ancestor=value.previous
            while ancestor is not None and ancestor is not previous:ancestor=ancestor.previous
            uncertain_update=bool(wrapper[0]=='decorated' and previous and value.uncertain_descriptor and (previous.descriptor=='property' or previous.uncertain_descriptor))
            if previous and not uncertain_update and not (wrapper[0]=='decorated' and previous.descriptor=='property' and ancestor is previous):self.error(LanguageError('重复类成员 '+member[1],code='duplicate_binding'),member)
            value=self.declare(member[1],value,dict(defaults),member,after='fn' if member[0]=='def' else member[0])
            info.own_members[member[1]]=value;defaults[member[1]]=value
            if member[0]=='def':methods.append((member,method_defaults,value))
        info.naming_effects=any(value.type=='Any' or '__set_name__' in self.protocols(value) for value in naming_candidates) or any(self.raw_protocol(base,'__init_subclass__') is not None for base in bases)
        info.members=self.bound_members(info,class_access=True)
        info.class_complete=True
        for member,default_env,value in methods:
            is_meta=bool(default_meta and default_meta in info.mro)
            receiver=None if value.descriptor=='staticmethod' else info if value.descriptor=='classmethod' or member[1] in ('__new__','__init_subclass__','__prepare__','__class_getitem__') else Info('Class',detail='Class · 元类方法接收者',metaclass_info=info) if is_meta else self.instance_info(info)
            self.walk_function(member[2],local,member[1],receiver=receiver,defaults_env=default_env,binding=value)
        if unknown_meta or chosen is not default_meta:
            info=self.copy_origin(Info('Any','class',node[1]+' · Any（基底适配器、自定义元类或工厂；不执行基底或创建钩子）'),info)
        return self.declare(node[1],info,env,node,after='class')

    def color_annotation(self,typ,start,end):
        if typ=='Any':return
        for token in self.between(start,end):
            if token.kind=='id' and token.value in typ.split('.'):
                self.colors[token.start]='type'

    @staticmethod
    def nominal_mismatch(actual,target):
        if actual.type=='Class':
            meta=actual.class_info.metaclass_info if actual.class_info is not None else None
            return meta is not None and target not in meta.mro
        if actual.class_info is not None:return target not in actual.class_info.mro
        return actual.type in TYPES-{'Any','Instance','Class'}

    def check_value(self, actual, expected, env, node):
        if expected=='Type':
            klass=self.class_value(actual)
            default=self.base.get('Type')
            if klass is not None and default not in klass.mro:self.error(LanguageError('可确定的类型不匹配：需要 Type，实际为普通 Class',code='type_mismatch'),node)
            elif actual.type not in ('Any','Class','Type'):self.check_known(actual.type,expected,node)
            return
        if expected=='Descriptor' and (actual.descriptor or any(name in self.protocols(actual) for name in ('__get__','__set__','__delete__'))):return
        target=self.type_info(expected,env).class_info
        if target is not None:
            if self.nominal_mismatch(actual,target):self.error(LanguageError('可确定的类型不匹配：需要 '+expected+'，实际为 '+actual.type,code='type_mismatch'),node)
        else:self.check_known(actual.type,expected,node)

    def check_known(self, actual, expected, node):
        # Native metadata can describe a nullable parameter without adding a
        # union annotation grammar. Unknown user contracts remain conservative.
        options = expected.split(' | ')
        if actual not in TYPES or any(item not in TYPES for item in options): return
        numeric_kinds={'Int':{'Int'},'Number':{'Number'},'Fraction':{'Fraction'},'Complex':{'Complex'},'Decimal':{'Decimal'},
                       'Rational':{'Int','Fraction'},'Real':{'Int','Number','Fraction','Decimal'},'Numeric':{'Int','Number','Fraction','Complex','Decimal'}}
        numeric_contracts=dict(numeric_kinds,Number={'Int','Number'})
        accepted = any(actual == item or item == 'Any' or actual == 'Any' or
                       (actual in numeric_kinds and item in numeric_contracts and bool(numeric_kinds[actual]&numeric_contracts[item])) or
                       (item=='Type' and actual=='Class') or (item=='Function' and (actual=='Class' or call_spec_for(actual) is not None)) for item in options)
        if not accepted: self.error(LanguageError('可确定的类型不匹配：需要 '+expected+'，实际为 '+actual,code='type_mismatch'),node)

    def walk_block(self, node, env, root=False):
        local = env if root else dict(env)
        start,end = self.map.span(node)
        self.scopes.append((start,end,local))
        self.owned[id(local)] = set()
        result=Info('Null')
        for statement in node[1]: result=self.walk(statement,local)
        return result

    def walk(self, node, env):
        info = self._walk(node,env)
        self.node_infos[id(node)] = info
        # These offsets are syntax-derived postfix sites, including recovered
        # editor holes. They never certify an arbitrary textual name as native.
        if node[0] in ('index','subscript','call'):
            start,end = self.map.span(node)
            _,receiver_end = self.map.span(node[1])
            wanted = '(' if node[0]=='call' else '.'
            token = None
            cursor = bisect.bisect_left(self.code_starts,receiver_end)
            while cursor < len(self.code) and self.code[cursor].start < end:
                if self.code[cursor].value == wanted:
                    token = self.code[cursor]
                    break
                cursor += 1
            if token is not None:
                receiver = self.node_infos.get(id(node[1]),Info())
                if node[0]=='call': self.call_callees[token.start] = self.callable_info(receiver)
                elif node[0]=='index': self.member_receivers[token.start] = receiver
        return info

    def _walk(self, node, env):
        kind=node[0]
        if kind=='editor_hole':return Info()
        syntax_words={'async_gen_fn':('async','fn'),'async_for':('async','for'),'async_fn':('async','fn'),'gen_fn':('fn',),'macro':('macro',),'quote':('quote',),'unquote':('unquote',),'await':('await',),'yield':('yield',),'yield_from':('yield','from'),'class':('class',)}
        for word in syntax_words.get(kind,()):
            token=self.name_token(word,node)
            if token:self.colors[token.start]='keyword'
        if kind=='block':return self.walk_block(node,env)
        if kind in ('list_comp','set_comp','record_comp','gen_comp','async_gen_comp'):return self.walk_comprehension(node,env)
        if kind=='class':return self.walk_class(node,env)
        if kind=='decorated':
            decorators=[self.walk(item,env) for item in node[1]]
            declaration=node[2];temporary=dict(env)
            info=self.walk(declaration,temporary)
            for decorator in reversed(decorators):info=self.decorate_info(decorator,info)
            token=self.name_token(declaration[1],declaration,after={'def':'fn','class':'class','agent':'agent'}[declaration[0]])
            if token:
                position=self.map.public(token.start,token.end)
                self.symbols=[item for item in self.symbols if item['from']!=position['from'] or item['to']!=position['to']]
            return self.declare(declaration[1],info,env,declaration,token=token)

        if kind=='literal':
            typ={type(None):'Null',bool:'Bool',int:'Int',float:'Number',str:'String'}.get(type(node[1]),'Any')
            return Info(typ,detail=typ,literal_string=node[1] if type(node[1]) is str else None)
        if kind=='name':
            token=self.name_token(node[1],node)
            start,end=(token.start,token.end) if token else self.map.span(node)
            info=env.get(node[1])
            if info is None:
                self.diagnostic('未定义名称 '+node[1],start,end,'warning','undefined_name')
                return Info()
            if info.type=='Macro':self.diagnostic('语法宏只能使用 !(...) 调用，不能作为运行值',start,end,code='macro_phase')
            if token:self.colors[token.start]=_semantic_role(info)
            self.references.append((start,end,info));return info
        if kind in ('let_pattern','var_pattern'):
            before = dict(env)
            left, right = self.map.span(node[3]);self.scopes.append((left,right,before))
            value = self.walk(node[3], before)
            self.check_value(value, node[2], before, node[3])
            _, target_end = self.map.span(node[1]);self.color_annotation(node[2],target_end,left)
            target_start, _ = self.map.span(node[1]);self.scopes.append((target_start,target_end,before))
            self.bind_pattern(node[1],env,value,lookup=before,mutable=kind=='var_pattern')
            return value
        if kind in ('let','var'):
            value=self.walk(node[3],env);self.check_value(value,node[2],env,node[3])
            if kind=='var':value=self.mutable_shape(value)
            typ=node[2] if node[2]!='Any' else ('Any' if kind=='var' else value.type)
            token=self.name_token(node[1],node,after=kind)
            if token:self.color_annotation(node[2],token.end,self.map.span(node[3])[0])
            info=Info(typ,'variable',node[1]+': '+typ,mutable=True) if kind=='var' and value.class_info is None else replace(value,type=typ,mutable=kind=='var',detail=node[1]+': '+typ+((' · '+value.detail) if value.type in ('Function','Class') else ''))
            if node[2]!='Any' and info.class_info is None:
                annotated=self.type_info(node[2],env)
                if annotated.class_info is not None:info=replace(annotated,mutable=kind=='var',detail=node[1]+': '+node[2])
            if kind=='var':info=replace(info,exact_instance=False)
            if kind=='let' and info.kind=='parameter':
                # A fresh binding keeps value metadata, not the source's lexical role.
                role=_semantic_role(info,position='pattern')
                info=replace(info,kind='class' if role=='type' else role)
            return self.declare(node[1],info,env,node,after=kind)
        if kind=='def':
            info=self.function_info(node[2],node[1],env);outer=dict(env)
            binding=self.declare(node[1],info,env,node,after='fn')
            self.walk_function(node[2],outer,node[1],binding=binding);return binding
        if kind in ('fn','gen_fn','async_fn','async_gen_fn'):return self.walk_function(node,env)
        if kind=='macro':
            info=self.macro_info(node)
            self.declare(node[1],info,env,node,after='macro')
            local={name:Info('Function','function',name+'(value) -> '+typ,parameters=[('value','Any','positional',False)],returns=typ)
                   for name,typ in [('syntax_literal','Syntax'),('syntax_kind','String'),('syntax_value','Any')]}
            local.update({name:self.base[name] for name in ('condition','condition_info','decimal','decimal_text','decimal_quantize','fraction','complex','conjugate','numeric_encode','numeric_decode','tuple','set_of','set_add','set_discard','set_is_disjoint','return_values','values_list','multiple_value_list','nth_value','multiple_value_call','multiple_value_bind','multiple_value_prog1')})
            header=self.between(self.map.span(node)[0],self.map.span(node[3])[0])
            opening=next((index for index,item in enumerate(header) if item.value=='('),len(header))
            parameters=[item for item in header[opening+1:] if item.kind=='id']
            for name,token in zip(node[2],parameters):self.declare(name,Info('Syntax','parameter',name+': Syntax'),local,node,token=token)
            self.walk_block(node[3],local);return info
        if kind=='macrocall':
            parts=node[1].split('.')
            info=self.macro_namespace.get(parts[0]) if len(parts)<=2 else None
            token_start=self.map.span(node)[0]
            for index,part in enumerate(parts):
                if index:info=info.members.get(part) if info else None
                token=next((t for t in self.between(token_start,self.map.span(node)[1]) if t.kind=='id' and t.value==part),None)
                if token:
                    token_start=token.end
                    self.colors[token.start]='function' if index==len(parts)-1 else 'variable'
                    if info:self.references.append((token.start,token.end,info))
            if not info or info.type!='Macro':self.error(LanguageError('未定义语法宏 '+node[1],code='undefined_macro'),node)
            for argument in node[2]:self.walk(argument,env)
            return Info()
        if kind=='quote':
            self.quote_environments.append(dict(env))
            try:self.walk_block(node[1],{})
            finally:self.quote_environments.pop()
            return Info('Syntax')
        if kind=='unquote':
            phase=self.quote_environments[-1] if self.quote_environments else env
            start,end=self.map.span(node[1]);self.scopes.append((start,end,dict(phase)))
            self.walk(node[1],phase);return Info()
        if kind=='await':
            value=self.walk(node[1],env);return value.awaited_info or Info(value.awaited)
        if kind in ('yield','yield_from'):self.walk(node[1],env);return Info()
        if kind=='rec':
            group={d[1]:self.function_info(d[2],d[1],env) for d in node[1]}
            outer=dict(env)
            for definition in node[1]:group[definition[1]]=self.declare(definition[1],group[definition[1]],env,definition,after='fn')
            for definition in node[1]:self.walk_function(definition[2],outer,definition[1],group,binding=group[definition[1]])
            return Info('Null')
        if kind=='agent':
            self.walk(node[2],env)
            return self.declare(node[1],Info('Function','function',node[1]+'(input: Any) -> Any',parameters=[('input','Any','positional',False)]),env,node,after='agent')
        if kind=='data':
            self.declare(node[1],Info('DataType','class',node[1]+' · data'),env,node,after='data')
            for tag,fields in node[2]:
                params=[(name,'Any','positional',False) for name in fields]
                self.declare(tag,Info('Function','function',signature(tag,params,node[1]),parameters=params,returns=node[1]),env,node)
            return Info('Null')
        if kind=='import':
            try: members=self.module(node[1])
            except (LanguageError,RecursionError) as error:
                self.error(error if isinstance(error,LanguageError) else ResourceLimit('模块嵌套过深'),node);members={}
            # Runtime module records never contain compile-time macro exports.
            members={name:info for name,info in members.items() if info.type!='Macro'}
            return self.declare(node[2],Info('Record','module',node[2]+' · '+node[1],members),env,node,after='as')
        if kind=='export':return Info('Null')
        if kind=='assign_pattern':
            value=self.walk(node[2],env)
            self.bind_pattern(node[1],env,value,lookup=dict(env),assignment=True)
            return value
        if kind=='aug_assign':
            left=self.walk(node[1],env);right=self.walk(node[3],env)
            value=self.binary_result(node[2],left,right,node[3],augmented=True)
            self.augmented_write(node[1],value,env,node[3]);return value
        if kind=='assign':
            value=self.walk(node[2],env)
            if node[1] not in env:self.error(LanguageError('未定义名称 '+node[1],code='undefined_name'),node)
            else:
                token=self.name_token(node[1],node)
                if token:
                    self.colors[token.start]=_semantic_role(env[node[1]])
                    self.references.append((token.start,token.end,env[node[1]]))
                if not env[node[1]].mutable:self.error(LanguageError('不可赋值给只读绑定 '+node[1],code='immutable_binding'),node)
                self.check_known(value.type,env[node[1]].type,node[2])
            return value
        if kind=='set':
            elements=[];known=True
            for item in node[1]:
                if item[0]=='spread':
                    value=self.walk(item[1],env);self.check_sync_source(value,item[1])
                    if value.type in ('List','Tuple','Set') and value.elements is not None:
                        if known and len(elements)+len(value.elements)<=10000:elements.extend(value.elements)
                        else:known=False
                    elif value.type=='String':elements.append(Info('String'))
                    else:known=False
                else:elements.append(self.walk(item,env))
            return Info('Set',elements=elements if known else None)
        if kind in ('list','tuple'):
            return Info('Tuple' if kind=='tuple' else 'List',elements=[self.walk(value,env) for value in node[1]])
        if kind=='record':
            self.color_record_keys(node)
            # A field stores a value; copying its type/shape must not assert
            # that the field name was declared at that value's lexical binding.
            return Info('Record',members={key:replace(self.walk(value,env),declaration_file='',declaration_start=-1,declaration_end=-1) for key,value in node[1]},closed_record=True)
        if kind=='slice':
            for value in node[1:]:
                if value is not None:self.walk(value,env)
            return slice_info()
        if kind=='subscript':
            obj=self.walk(node[1],env);key=self.walk(node[2],env)
            return self.item_result(obj,key,node[2])
        if kind=='set_item':
            obj=self.walk(node[1],env);self.walk(node[2],env);value=self.walk(node[3],env)
            self.invalidate_attributes(obj,node[2][1] if node[2][0]=='literal' and type(node[2][1]) is str else None)
            if obj.class_info is not None and obj.type!='Class' and obj.exact_instance:
                member=self.raw_protocol(obj.class_info,'__setitem__')
                if self.member_steps>20000 or (member is not None and (member.mutable or member.uncertain_descriptor)):return Info()
                return Info('Null') if member is not None else value
            return value if obj.type in ('Class','Super','Descriptor') else Info()
        if kind=='index':
            obj=self.walk(node[1],env);index=self.walk(node[2],env)
            if node[2][0]=='literal' and isinstance(node[2][1],str):
                key=node[2][1];info=self.attribute_info(obj,key)
                static=self.macro_namespace.get(node[1][1]) if node[1][0]=='name' and obj.kind=='module' else None
                macro=static.members.get(key) if static else None
                if macro and macro.type=='Macro':info=macro
                if info.type=='Macro':self.error(LanguageError('语法宏只能使用 !(...) 调用，不能作为运行值',code='macro_phase'),node)
                token=self.name_token(key,node[2])
                if token:self.colors[token.start]=_semantic_role(info,position='member')
                start,end=self.map.span(node[2]);self.references.append((start,end,info));return info
            return Info('String' if obj.type=='String' else 'Any')
        if kind=='set_member':
            obj=self.walk(node[1],env);self.walk(node[2],env);value=self.walk(node[3],env)
            self.invalidate_attributes(obj,node[2][1] if node[2][0]=='literal' and type(node[2][1]) is str else None)
            if node[2][0]=='literal' and isinstance(node[2][1],str):
                token=self.name_token(node[2][1],node[2])
                if token:self.colors[token.start]='property'
                if self.attribute_hook(obj,'__setattr__') is not None:return value
                field=self.raw_protocol(obj.class_info,node[2][1]) if obj.type=='Class' and obj.class_info else obj.members.get(node[2][1])
                if field:
                    if token:self.references.append((token.start,token.end,field))
                    if not field.mutable and not field.uncertain_descriptor:self.error(LanguageError('成员只读 '+node[2][1],code='immutable_binding'),node)
                    if obj.type=='Class':
                        target=field.class_info if field.type!='Class' else None
                        if target is not None:
                            mismatch=self.nominal_mismatch(value,target)
                            if mismatch:self.error(LanguageError('可确定的类型不匹配：需要 '+field.type+'，实际为 '+value.type,code='type_mismatch'),node[3])
                        else:self.check_known(value.type,field.type,node[3])
                    elif field.setter:
                        parameters=field.setter.parameters[1:]
                        setter=replace(field.setter,parameters=parameters,parameter_patterns={key:field.setter.parameter_patterns[key] for key,_,_,_ in parameters if key in field.setter.parameter_patterns})
                        self.check_call(('call',node[1],[node[3]]),setter,env)
                    elif not field.write_unknown:self.check_known(value.type,field.type,node[3])
            return value
        if kind=='call':
            callee=self.callable_info(self.walk(node[1],env))
            argument_infos=[]
            for argument in node[2]:
                if argument[0]=='keyword':
                    token=self.name_token(argument[1],argument)
                    if token:self.colors[token.start]='property'
                    argument_infos.append(self.walk(argument[2],env))
                elif argument[0] in ('spread','kwspread'):
                    info=self.walk(argument[1],env);argument_infos.append(info)
                    if argument[0]=='spread':self.check_sync_source(info,argument[1])
                else:argument_infos.append(self.walk(argument,env))
            self.check_call(node,callee,env)
            self.attribute_mutation_builtin(callee,node[2],argument_infos)
            if callee.builtin in ('staticmethod','classmethod','property','property.getter','property.setter','property.deleter'):
                if any(argument[0] in ('spread','kwspread') for argument in node[2]):return Info('Descriptor',detail='Descriptor · 动态实参未知')
                names=('fget','fset','fdel') if callee.builtin=='property' else ('function',) if callee.builtin.startswith('property.') else ('value',)
                values={};position=0
                for argument,argument_info in zip(node[2],argument_infos):
                    if argument[0]=='keyword':key,expression=argument[1],argument[2]
                    else:
                        key=names[position] if position<len(names) else '';expression=argument;position+=1
                    values[key]=None if expression[0]=='literal' and expression[1] is None else argument_info
                if callee.builtin=='property':return self.descriptor_info('property',values.get('fget'),values.get('fset'),deleter=values.get('fdel'))
                if names[0] in values:return self.decorate_info(callee,values[names[0]] or Info())
            if node[1][0]=='name' and env.get(node[1][1]) is self.base.get(node[1][1]) and node[1][1]=='super' and len(node[2])==2:
                start=self.class_value(argument_infos[0]);receiver=argument_infos[1]
                if start and receiver.class_info:return Info('Super',members=self.bound_members(receiver.class_info,start,class_access=receiver.type=='Class'))
            if node[1][0]=='name' and env.get(node[1][1]) is self.base.get(node[1][1]) and node[1][1]=='getattr' and len(node[2])>=2 and node[2][1][0]=='literal':
                return self.attribute_info(argument_infos[0],node[2][1][1])
            team=self.team_builtin(callee,node[2],argument_infos)
            if team is not None:return team
            indexing=self.indexing_builtin(callee,node[2],argument_infos)
            if indexing is not None:return indexing
            class_protocol=self.class_protocol_builtin(callee,node[2],argument_infos)
            if class_protocol is not None:return class_protocol
            code=self.code_builtin(callee,node[2],argument_infos)
            if code is not None:return code
            compilation=self.compilation_builtin(callee,node[2],argument_infos)
            if compilation is not None:return compilation
            composition=self.composition_builtin(callee,node[2],argument_infos)
            if composition is not None:return composition
            multiple=self.multiple_value_builtin(callee,node[2],argument_infos)
            if multiple is not None:return multiple
            set_result=self.set_builtin(callee,node[2],argument_infos)
            if set_result is not None:return set_result
            tuple_result=self.tuple_builtin(callee,node[2],argument_infos)
            if tuple_result is not None:return tuple_result
            iteration=self.iteration_builtin(callee,node[2],argument_infos)
            if iteration is not None:return iteration
            task=self.task_builtin(callee,node[2],argument_infos)
            if task is not None:return task
            operation=self.operator_builtin(callee,node[2],argument_infos)
            if operation is not None:return operation
            stack=self.resource_stack_builtin(callee,node[2],argument_infos)
            if stack is not None:return stack
            context=self.generator_context_builtin(callee,node[2],argument_infos)
            if context is not None:return context
            if callee.result_info and callee.result_info.class_info:return self.instance_info(callee.result_info.class_info,exact=callee.result_info.exact_instance)
            if callee.returns in ('ContextVar','ContextToken','ContextScope','Context'):return binding_value_info(callee.returns)
            return callee.result_info or Info(callee.returns,awaited=callee.awaited,awaited_info=callee.awaited_info)
        if kind in ('with','async_with','with_pattern','async_with_pattern'):return self.walk_context(node,env)
        if kind in ('for_pattern','async_for_pattern'):
            iterable = self.walk(node[2],env);local = dict(env)
            source_start,source_end=self.map.span(node[2]);self.scopes.append((source_start,source_end,dict(env)))
            element = 'String' if kind=='for_pattern' and iterable.type=='String' else 'Any'
            self.bind_pattern(node[1],local,self.pattern_element(iterable,kind=='async_for_pattern'),lookup=dict(env))
            if kind=='async_for_pattern':
                token=self.name_token('async',node)
                if token:self.colors[token.start]='keyword'
            self.walk_block(node[3],local);return Info('Null')
        if kind in ('for','async_for'):
            iterable=self.walk(node[2],env);local=dict(env)
            element=self.pattern_element(iterable).type if kind=='for' and iterable.type=='Set' else 'String' if kind=='for' and iterable.type=='String' else 'Any'
            self.declare(node[1],Info(element,'variable',node[1]+': '+element+' · for'),local,node,after='for')
            self.walk_block(node[3],local);return Info('Null')
        if kind=='loop_else':
            self.walk(node[1],env)
            result=self.walk(node[2],env)
            # The loop may break. Its Null result cannot certify a callable or
            # container shape from the completion branch.
            return Info('Null' if result.type=='Null' else 'Any')
        if kind=='try_else':
            self.walk(node[1],env)
            handlers=[node[1][3]] if node[1][0]=='try' else [case[4] for case in node[1][2]] if node[1][0]=='try_group' else [case[2] for case in node[1][2]]
            results=[self.node_infos.get(id(handler),Info()) for handler in handlers]
            results.append(self.walk(node[2],env))
            # Successful protected values are discarded, and unknown branches
            # cannot certify callable identity or mutable container contents.
            return Info(results[0].type if all(item.type==results[0].type for item in results) else 'Any')
        if kind=='try_group':
            self.walk(node[1],env)
            leaf=Info('Record',members={'code':Info('String'),'message':Info('String'),'value':Info(),'location':Info('Record',members={'file':Info(),'line':Info(),'column':Info()},closed_record=True)},closed_record=True)
            for case in node[2]:
                _,pattern,guard,name,body=case
                local=dict(env)
                self.bind_pattern(pattern,local,leaf,lookup=dict(env),matching=True)
                start,_=self.map.span(pattern)
                # Pattern bindings select individual leaves, and never leak into
                # the handler that receives the whole matched subgroup.
                _,end=self.map.span(guard if guard is not None else pattern)
                self.scopes.append((start,end,local))
                if guard is not None:self.check_known(self.walk(guard,local).type,'Bool',guard)
                handler=dict(env)
                header=self.between(self.map.span(guard if guard is not None else pattern)[1],self.map.span(body)[0])
                self.declare(name,error_value_info(),handler,case,token=header[-1] if header else None)
                for token in header[:-1]:
                    if token.value=='as':self.colors[token.start]='keyword'
                self.walk(body,handler)
            # Several handlers can run. Do not infer a particular handler's
            # result as the expression's one statically known return value.
            return Info()
        if kind=='try_cases':
            results=[self.walk(node[1],env)]
            error=Info('Record',members={'code':Info('String'),'message':Info('String'),'value':Info(),'location':Info('Record',members={'file':Info(),'line':Info(),'column':Info()},closed_record=True)},closed_record=True)
            for pattern,guard,body in node[2]:
                local=dict(env)
                self.bind_pattern(pattern,local,error,lookup=dict(env),matching=True)
                start,_=self.map.span(pattern);_,end=self.map.span(body);self.scopes.append((start,end,local))
                if guard is not None:self.check_known(self.walk(guard,local).type,'Bool',guard)
                results.append(self.walk(body,local))
            return Info(results[0].type if all(item.type==results[0].type for item in results) else 'Any')
        if kind=='rethrow':return Info()
        if kind=='try':
            body=self.walk(node[1],env);local=dict(env)
            self.declare(node[2],Info('Record','variable',node[2]+': Record · error'),local,node,after='catch')
            result=self.walk(node[3],local);return Info(body.type if body.type==result.type else 'Any')
        if kind=='finally':
            body=self.walk(node[1],env);self.walk(node[2],env);return body
        if kind=='match':
            value=self.walk(node[1],env)
            for pattern,guard,body in node[2]:
                local=dict(env);self.bind_pattern(pattern,local,value,lookup=dict(env),matching=True)
                start,_=self.map.span(pattern);_,end=self.map.span(body);self.scopes.append((start,end,local))
                if guard:self.walk(guard,local)
                self.walk(body,local)
            return Info()
        if kind=='if':
            condition=self.walk(node[1],env);self.check_known(condition.type,'Bool',node[1])
            yes=self.walk(node[2],env);no=self.walk(node[3],env)
            return Info(yes.type if yes.type==no.type else 'Any')
        if kind=='binary':
            left=self.walk(node[2],env);right=self.walk(node[3],env)
            return self.binary_result(node[1],left,right,node[3])
        if kind=='unary':
            value=self.walk(node[2],env)
            if node[1]=='!':return Info('Bool')
            if node[1]=='~':return Info('Int') if value.type=='Int' else self.operator_result(value,'__invert__')
            if value.type in ('Fraction','Complex','Decimal'):return numeric_value_info(value.type)
            return Info(value.type) if value.type in ('Int','Number') else self.operator_result(value,'__pos__' if node[1]=='+' else '__neg__')
        if kind in ('do','return','throw'):return self.walk(node[1],env)
        if kind=='while':self.walk(node[1],env);self.walk(node[2],env)
        return Info('Null')

    def check_call(self,node,callee,env):
        # Only exact syntax is checked. Dynamic unpacking is intentionally unknown.
        if callee.type!='Function' or (not callee.parameters and '(' not in callee.detail) or any(arg[0] in ('spread','kwspread','editor_hole') or arg[0]=='keyword' and arg[2][0]=='editor_hole' for arg in node[2]):return
        positional=[arg for arg in node[2] if arg[0]!='keyword']
        keywords={arg[1]:arg[2] for arg in node[2] if arg[0]=='keyword'}
        index=0;remaining=dict(keywords)
        for name,typ,mode,default in callee.parameters:
            if mode=='varargs':index=len(positional);continue
            if mode=='varkw':remaining={};continue
            value=None
            if mode in ('positional','positional_only') and index<len(positional):
                value=positional[index];index+=1
                if mode!='positional_only' and name in remaining:self.error(LanguageError('参数重复赋值 '+name,code='argument_error'),node);return
            elif mode!='positional_only' and name in remaining:value=remaining.pop(name)
            elif not default:self.error(LanguageError('缺少参数 '+name,code='argument_error'),node);return
            actual=None
            if value is not None and value[0]=='name':actual=env.get(value[1])
            elif value is not None and value[0]=='literal':
                actual=Info({type(None):'Null',bool:'Bool',int:'Int',float:'Number',str:'String'}.get(type(value[1]),'Any'))
            if actual is not None:
                target=callee.parameter_contracts.get(name)
                if target is not None:
                    mismatch=self.nominal_mismatch(actual,target)
                    if mismatch:self.error(LanguageError('可确定的类型不匹配：需要 '+typ+'，实际为 '+actual.type,code='type_mismatch'),value)
                else:self.check_known(actual.type,typ,value)
        if index<len(positional):self.error(LanguageError('收到过多位置参数',code='argument_error'),node)
        if remaining:self.error(LanguageError('未知关键字 '+next(iter(remaining)),code='argument_error'),node)

    def call_context(self,env):
        tokens=[t for t in self.code if t.start<self.cursor]
        openings=[]
        for i,token in enumerate(tokens):
            if token.value in ('(','[','{'):openings.append(i)
            elif token.value in (')',']','}') and openings:openings.pop()
        for opening in reversed(openings):
            if tokens[opening].value!='(' or opening==0:continue
            last=opening-1;parts=[]
            macro_call=tokens[last].value=='!'
            if macro_call:last-=1
            while last>=0 and tokens[last].kind=='id':
                parts.insert(0,tokens[last].value);last-=1
                if last>=0 and tokens[last].value=='.':last-=1
                else:break
            if last>=0 and tokens[last].value in ('fn','class','agent','macro'):continue
            if last>=1 and tokens[last].value=='*' and tokens[last-1].value=='fn':continue
            if not macro_call and parts==['await'] and tokens[opening-1].end<tokens[opening].start:continue
            namespace=self.macro_namespace if macro_call else env
            info=namespace.get(parts[0]) if parts else None
            for part in parts[1:]:info=(info.members.get(part) if macro_call else self.attribute_info(info,part)) if info else None
            if not macro_call:
                info = self.call_callees.get(tokens[opening].start) or (self.callable_info(info) if info else None)
            if info and info.type==('Macro' if macro_call else 'Function'):
                used=set();depth=0;positions=0;named=False
                inner=tokens[opening+1:]
                for i,token in enumerate(inner):
                    if depth==0 and token.kind=='id' and i+1<len(inner) and inner[i+1].value=='=':used.add(token.value);named=True
                    if depth==0 and token.value==',' and not named:positions+=1
                    if token.value in ('(','[','{'):depth+=1
                    elif token.value in (')',']','}'):depth=max(0,depth-1)
                positional=[p[0] for p in info.parameters if p[2] in ('positional','positional_only')]
                used.update(positional[:positions])
                return info,used,tokens[opening].start
        return None

    def signature_help(self, env):
        """Describe the actual bound callable, without evaluating argument text.

        Delimiters come from the real lexer. Dynamic positional expansion does
        not reveal its length, so subsequent positional mapping stays unknown.
        """
        context = self.call_context(env)
        if not context:
            return None
        info, _, opening = context
        parameters = []
        for name, typ, mode, default in info.parameters:
            prefix = '*' if mode == 'varargs' else '**' if mode == 'varkw' else ''
            pattern = info.parameter_patterns.get(name)
            parameters.append({'name': name, 'type': typ, 'mode': mode,
                               'optional': bool(default),
                               'label': prefix + name + (' as ' + pattern if pattern is not None else '') + ': ' + typ + (' = …' if default else '')})
        arguments, depth = [[]], 0
        for token in self.code:
            if token.start <= opening or token.start >= self.cursor:
                continue
            if token.kind == 'op' and token.value == ',' and depth == 0:
                arguments.append([])
                continue
            arguments[-1].append(token)
            if token.kind == 'op' and token.value in ('(', '[', '{'):
                depth += 1
            elif token.kind == 'op' and token.value in (')', ']', '}'):
                depth = max(0, depth - 1)
        def named(tokens):
            return tokens[0].value if len(tokens) > 1 and tokens[0].kind == 'id' and tokens[1].value == '=' else None
        def rest(mode):
            return next((i for i, p in enumerate(parameters) if p['mode'] == mode), None)
        current, active = arguments[-1], None
        key = named(current)
        if key is not None:
            active = next((i for i, p in enumerate(parameters) if p['name'] == key and p['mode'] not in ('positional_only', 'varargs', 'varkw')), rest('varkw'))
        elif current and current[0].value in ('*', '**'):
            active = rest('varargs' if current[0].value == '*' else 'varkw')
        else:
            previous = arguments[:-1]
            # Once names or unknown spreads have appeared, an empty or bare
            # argument cannot be assigned a certain positional parameter.
            uncertain = any(named(part) is not None or (part and part[0].value in ('*', '**')) for part in previous)
            if not uncertain:
                positional = [i for i, p in enumerate(parameters) if p['mode'] in ('positional', 'positional_only')]
                number = len(previous)
                active = positional[number] if number < len(positional) else rest('varargs')
        return dict(self.map.public(opening, self.cursor), label=info.detail,
                    parameters=parameters, activeParameter=active,
                    activeArgument=len(arguments) - 1)

    def pattern(self,node,env):
        if node[0]=='p_bind':self.declare(node[1],Info('Any','variable',node[1]+': Any · pattern'),env,node)
        elif node[0] in ('p_list','p_tuple','p_tag'):
            for child in node[1] if node[0] in ('p_list','p_tuple') else node[2]:self.pattern(child,env)
            if node[0] in ('p_list','p_tuple') and node[2]:self.pattern(node[2],env)
        elif node[0]=='p_record':
            self.color_record_keys(node)
            for _,child in node[1]:self.pattern(child,env)
            if node[2]:self.pattern(node[2],env)

    def environment(self):
        scopes=[(end-start,index,env) for index,(start,end,env) in enumerate(self.scopes)
                if start<=self.cursor<end or (self.cursor==end and (index==0 or self.source[max(0,end-1):end]!='}'))]
        env=min(scopes,key=lambda item:(item[0],-item[1]))[2] if scopes else self.base
        return {name:info for name,info in env.items() if info.start<0 or info.start<=self.cursor or id(info) in self.comprehension_bindings}

    def suggestions(self,env):
        before=[t for t in self.code if t.start<self.cursor]
        current=next((t for t in self.lex if t.start<=self.cursor<t.end or t.end==self.cursor and t.kind in ('id','string','invalid')),None)
        start,end,prefix=self.cursor,self.cursor,''
        if current and current.kind=='comment':return []
        if current and current.kind in ('string','invalid'):
            # Module path strings are a grammar context, not a filename scan.
            prior=[t for t in self.code if t.end<=current.start]
            if prior and prior[-1].value=='import':
                start=current.start+1;end=current.end-(1 if current.value[-1:]==current.value[:1] and len(current.value)>1 else 0)
                prefix=self.source[start:self.cursor]
                paths=[]
                import posixpath
                for name in sorted(set(self.files)|set(STDLIB)):
                    label=name if name in STDLIB else posixpath.relpath(name,posixpath.dirname(self.file) or '.')
                    if name!=self.file and label.startswith(prefix):paths.append(dict(self.map.public(start,end),label=label,type='module',detail='封闭程序包模块',apply=label))
                return paths[:100]
            return []
        if current and current.kind=='id':start,end=current.start,current.end;prefix=self.source[start:self.cursor]
        previous=[t for t in self.code if t.end<=start]
        members=None
        if previous and previous[-1].value=='.':
            # Resolve a qualified path from actual name/index syntax only.
            last=len(previous)-2;parts=[]
            while last>=0 and previous[last].kind=='id':
                parts.insert(0,previous[last].value);last-=1
                if last>=0 and previous[last].value=='.':last-=1
                else:break
            info=env.get(parts[0]) if parts else None
            for part in parts[1:]:info=self.attribute_info(info,part) if info else None
            info = self.member_receivers.get(previous[-1].start) or info
            members=self.attribute_members(info) if info else {}
            # Static imports survive lexical shadowing, but runtime aliases cannot
            # acquire macro members and qualified macro names have only one dot.
            static=self.macro_namespace.get(parts[0]) if len(parts)==1 else None
            if static:members.update({name:info for name,info in static.members.items() if info.type=='Macro'})
        items=members if members is not None else {**env,**{name:info for name,info in self.macro_namespace.items() if info.type=='Macro'}}
        result=[]
        # Retain visible source declarations before the bounded builtin catalog.
        # Comprehension scope priority and qualified member ordering stay intact.
        for name,info in sorted(items.items(),key=lambda item:(
                id(item[1]) not in self.comprehension_bindings,
                members is None and not (item[1].declaration_file and item[1].declaration_start>=0),
                item[0])):
            if name.startswith(prefix):
                # Member syntax uses the same identifier grammar as the parser.
                # Reserved or arbitrary record keys retain the preceding dot.
                member_key = members is not None and (name in Parser.reserved or not name.isidentifier())
                insertion = '['+json.dumps(name,ensure_ascii=False)+']' if member_key else name+'!' if info.type=='Macro' and self.source[end:end+1]!='!' else name
                result.append(dict(self.map.public(start,end),label=name,type='module' if info.kind=='module' else 'class' if info.kind=='class' else 'function' if info.type in ('Function','Macro') else 'property' if members is not None else 'variable',detail=info.detail,apply=insertion))
        if members is None:
            context=self.call_context(env)
            if context and context[0].type=='Function':
                for name,typ,mode,default in context[0].parameters:
                    if name not in context[1] and mode not in ('varargs','varkw','positional_only') and name.startswith(prefix):
                        result.insert(0,dict(self.map.public(start,end),label=name+'=',type='property',detail=name+': '+typ+' · '+context[0].detail,apply=name+'='))
            for name in sorted(Parser.reserved | {'async','await','yield','macro','quote','unquote','with'}):
                if name.startswith(prefix):result.append(dict(self.map.public(start,end),label=name,type='keyword',detail='Chaos 语法关键字',apply=name))
        return result[:150]

    def reference_hints(self):
        """Select the same smallest AST reference as hover for each token.

        A sweep keeps many references linearithmic; every public range uses the
        original SourceMap UTF-16 boundaries. This reads Info values only.
        """
        ordered = sorted(enumerate(self.references), key=lambda item:item[1][0])
        active, cursor, result = [], 0, []
        for token in self.lex:
            if token.kind in ('space','eof'): continue
            while cursor < len(ordered) and ordered[cursor][1][0] <= token.start:
                index, (start,end,info) = ordered[cursor]
                heapq.heappush(active, (end-start,index,end,info.detail))
                cursor += 1
            while active and active[0][2] < token.end: heapq.heappop(active)
            if active:
                result.append(dict(self.map.public(token.start,token.end),detail=active[0][3]))
        return result

    def outline(self):
        """Original declaration syntax, never a claim about runtime scope/types.

        This walks the already parsed/recovered tree. Quoted templates and
        anonymous function bodies are intentionally opaque; parameter bindings
        are not declarations in the file outline. Limits report truncation.
        """
        output = {'file': self.file, 'complete': self.complete,
                  'truncated': False, 'items': []}
        origins = {(start, end): info for start, end, info in self.declaration_infos}
        count = 0
        steps = 0
        flavors = {'fn': 'function', 'async_fn': 'asyncFunction',
                   'gen_fn': 'generator', 'async_gen_fn': 'asyncGenerator'}
        methods = {'fn': 'method', 'async_fn': 'asyncMethod',
                   'gen_fn': 'generatorMethod', 'async_gen_fn': 'asyncGeneratorMethod'}

        def add(name, kind, node, items, *, token=None, after=None, envelope=None, span=None):
            nonlocal count
            if count >= 512:
                output['truncated'] = True
                return None
            token = token or self.name_token(name, node, after=after)
            if token is None:
                output['truncated'] = True
                return None
            start, end = span or self.map.span(envelope or node)
            if node[0] == 'def' and not getattr(node, 'span', None):
                # rec wrappers omit ranges, but real fn AST and its grammatical
                # immediately preceding name/prefix tokens retain exact origin.
                start, end = self.map.span(node[2])
                index = bisect.bisect_left(self.code_starts, token.start) - 1
                if index >= 0 and self.code[index].value == '*': index -= 1
                if index >= 0 and self.code[index].value == 'fn':
                    start = self.code[index].start
                    if index and self.code[index-1].value == 'async': start = self.code[index-1].start
                else: start = token.start
            if not 0 <= start <= token.start < token.end <= end <= len(self.source):
                output['truncated'] = True
                return None
            info = origins.get((token.start, token.end))
            detail = info.detail if info else name
            if len(detail) > 1000:
                detail = detail[:1000]; output['truncated'] = True
            item = {'name': name, 'kind': kind, 'detail': detail,
                    'range': self.map.public(start, end),
                    'selectionRange': self.map.public(token.start, token.end), 'children': []}
            count += 1; items.append(item)
            return item

        def patterns(node, declaration, items, depth):
            nonlocal steps
            if not isinstance(node, (tuple, list)): return
            steps += 1
            if depth > 32 or steps > 20000 or count >= 512:
                output['truncated'] = True
                return
            if isinstance(node, Node) and node[0] == 'p_bind':
                add(node[1], 'variable', node, items, envelope=declaration)
                return
            for child in node: patterns(child, declaration, items, depth+1)

        def function_children(node, items, depth, include_body):
            # Parameter metadata contains the same source expressions for named
            # and anonymous functions; only anonymous bodies are opaque.
            for parameter in node[1]:
                if len(parameter) >= 4: visit(parameter[3], items, depth+1)
            if include_body: visit(node[3], items, depth+1)

        def visit(node, items, depth=0, member=False, envelope=None):
            nonlocal steps
            if not isinstance(node, (tuple, list)): return
            steps += 1
            if depth > 32 or steps > 20000 or count >= 512:
                output['truncated'] = True
                return
            # Grammar metadata tuples (e.g. record labels) are not AST nodes.
            kind = node[0] if isinstance(node, Node) and node else None
            if kind == 'quote': return
            if kind in flavors:
                function_children(node, items, depth, False)
                return
            if kind == 'decorated':
                index = len(items)
                visit(node[2], items, depth, member, node)
                if len(items) > index:
                    prefixes = []
                    for expression in node[1]: visit(expression, prefixes, depth+1)
                    items[index]['children'][:0] = prefixes
                return
            if kind == 'def':
                item = add(node[1], (methods if member else flavors)[node[2][0]],
                           node, items, after='fn', envelope=envelope)
                if item is not None: function_children(node[2], item['children'], depth, True)
                return
            if kind == 'class':
                item = add(node[1], 'class', node, items, after='class', envelope=envelope)
                if item is not None:
                    for base in node[2]: visit(base, item['children'], depth+1)
                    if len(node) > 4:
                        for option in node[4]: visit(option, item['children'], depth+1)
                    for child in node[3][1]: visit(child, item['children'], depth+1, True)
                return
            if kind == 'macro':
                item = add(node[1], 'macro', node, items, after='macro')
                if item is not None: visit(node[3], item['children'], depth+1)
                return
            if kind == 'agent':
                item = add(node[1], 'agent', node, items, after='agent', envelope=envelope)
                if item is not None: visit(node[2], item['children'], depth+1)
                return
            if kind == 'data':
                item = add(node[1], 'data', node, items, after='data')
                if item is None: return
                tokens = self.between(*self.map.span(node))
                cursor = next((i+1 for i, t in enumerate(tokens) if t.value == '{'), len(tokens))
                for tag, fields in node[2]:
                    # Consume exactly the grammar-produced variant/field order.
                    # A field named like a later variant cannot steal its range.
                    if cursor >= len(tokens) or tokens[cursor].value != tag:
                        output['truncated'] = True; break
                    token = tokens[cursor]; cursor += 1; end = token.end
                    if cursor < len(tokens) and tokens[cursor].value == '(':
                        cursor += 1
                        for field in fields:
                            if cursor >= len(tokens) or tokens[cursor].value != field:
                                output['truncated'] = True; return
                            cursor += 1
                            if cursor < len(tokens) and tokens[cursor].value == ',': cursor += 1
                        if cursor >= len(tokens) or tokens[cursor].value != ')':
                            output['truncated'] = True; return
                        end = tokens[cursor].end; cursor += 1
                    add(tag, 'constructor', node, item['children'], token=token, span=(token.start, end))
                    if cursor < len(tokens) and tokens[cursor].value == ',': cursor += 1
                return
            if kind == 'import':
                add(node[2], 'module', node, items, after='as')
                return
            if kind in ('let', 'var'):
                item = add(node[1], 'property' if member else 'variable', node, items, after=kind)
                if item is not None: visit(node[3], item['children'], depth+1)
                return
            if kind in ('let_pattern', 'var_pattern'):
                patterns(node[1], node, items, depth+1)
                # A destructuring declaration has several names and no single
                # owner. RHS declarations stay in their enclosing container.
                visit(node[3], items, depth+1)
                return
            # Structural containment in parsed blocks/branches, never inferred
            # ownership from flat symbol order or runtime object metadata.
            for child in node: visit(child, items, depth+1)

        if self.tree is not None: visit(self.tree, output['items'])
        return output

    def definition(self):
        """Return only a source-backed declaration for the identifier at cursor.

        Every origin is attached while walking a parsed binding. Imported
        offsets belong to their own SourceMap, never this document's map.
        Standard modules have no editor document and intentionally return null.
        """
        token=next((item for item in self.code if item.kind=='id' and item.start<=self.cursor<=item.end),None)
        if token is None:return None
        candidates=[(end-start,index,info) for index,(start,end,info) in enumerate(self.references+self.declaration_infos)
                    if start<=token.start and token.end<=end]
        if not candidates:return None
        info=min(candidates,key=lambda item:(item[0],-item[1]))[2]
        text=self.files.get(info.declaration_file)
        start,end=info.declaration_start,info.declaration_end
        if text is None or not 0<=start<end<=len(text):return None
        mapping=self.map if info.declaration_file==self.file else SourceMap(text)
        return dict(mapping.public(start,end),file=info.declaration_file)

    def run(self):
        try:self.tree=Parser(self.source,self.file,ranges=True,legacy_items=self.language_versions.get(self.file,2)==1).program()
        except (LanguageError,RecursionError) as error:
            self.complete=False
            self.error(error if isinstance(error,LanguageError) else ResourceLimit('语法嵌套过深'))
            repair=list(self.source)
            for token in self.lex:
                if token.kind=='invalid':
                    for i in range(token.start,token.end):
                        if repair[i]!='\n':repair[i]=' '
            try:
                parser=RecoveryParser(''.join(repair),self.file,ranges=True,legacy_items=self.language_versions.get(self.file,2)==1)
                parser.completion_cursor=self.cursor
                self.tree=parser.program()
            except (LanguageError,RecursionError):self.tree=None
        env=dict(self.base)
        if self.complete:
            try:declarations(self.tree)
            except LanguageError as error:self.error(error)
        if self.tree is not None:
            self.prepare_macros(self.tree)
            try:self.walk_block(self.tree,env,root=True)
            except RecursionError:self.diagnostic('分析结构嵌套过深',0,min(1,len(self.source)),code='resource_limit')
        # Named function AST spans start at the parameter list, so color their
        # soft async prefix from its grammatical token pair as well.
        for index,token in enumerate(self.code[:-1]):
            if token.value=='async' and self.code[index+1].value in ('fn','for'):
                self.colors[token.start]='keyword'
        tokens=[]
        for token in self.lex:
            if token.kind in ('space','eof'):continue
            kind=self.colors.get(token.start)
            if kind is None:
                kind={'id':'keyword' if token.value in Parser.reserved else 'variable','number':'number','string':'string','comment':'comment','invalid':'string' if token.value[:1] in ('"',"'") else 'operator','op':'punctuation' if token.value in '(){}[],:;.' else 'operator'}[token.kind]
            tokens.append(dict(self.map.public(token.start,token.end),type=kind))
        hover=None
        candidates=[(end-start,start,end,info) for start,end,info in self.references if start<=self.cursor<=end]
        if candidates:
            _,start,end,info=min(candidates,key=lambda x:x[0]);hover=dict(self.map.public(start,end),contents=info.detail)
        else:
            for symbol in self.symbols:
                if symbol['from']<=self.position<=symbol['to']:hover={'from':symbol['from'],'to':symbol['to'],'contents':symbol['detail']};break
        visible=self.environment()
        if hover is None:
            context=self.call_context(visible)
            if context:hover=dict(self.map.public(context[2],self.cursor),contents=context[0].detail)
        return {'tokens':tokens,'diagnostics':self.diagnostics,'symbols':self.symbols,
                'referenceHints':self.reference_hints(),
                'completions':self.suggestions(visible),'hover':hover,
                'signatureHelp':self.signature_help(visible),'definition':self.definition(),'complete':self.complete,
                'outline':self.outline()}


def analyze(source, file='main.chaos', files=None, entry='main.chaos', position=None, *, language_versions=None):
    if language_versions is not None and (not isinstance(language_versions,dict) or any(not isinstance(key,str) or type(value) is not int or value not in (1,2) for key,value in language_versions.items())):raise LanguageError('languageVersions 必须是文件名到 1 或 2 的记录')
    if not isinstance(source,str):raise LanguageError('source 必须是字符串')
    file=path_name(file);entry=path_name(entry)
    if files is not None and not isinstance(files,dict):raise LanguageError('files 必须是文件名到源码的记录')
    supplied=dict(files or {})
    supplied[file]=source
    supplied,entry=bundle_files(supplied.get(entry,''),supplied,entry)
    return Analysis(source,file,supplied,entry,position,language_versions=language_versions).run()


def format_source(source, file='main.chaos', *, language_version=2, line_width=88, indent_size=2):
    """Shared, comment-preserving source formatter used by IDE, SDK and CLI."""
    from .source_format import format_source as format_text
    return format_text(source, file, language_version=language_version,
                       line_width=line_width, indent_size=indent_size)
