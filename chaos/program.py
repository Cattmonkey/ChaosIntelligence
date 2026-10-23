"""Canonical, validated program IR and structural edits, shared by every frontend."""
import copy
import hashlib
import json
import re
from .language import Node, Parser, LanguageError, ResourceLimit, serializable

SCHEMA = 'chaos.program/1'
# Declarative syntax shape also powers the no-code inspector.
SHAPES = {
    'decorated':[('decorators','nodes'),('declaration','node')],
    'block': [('statements','nodes')], 'literal':[('value','literal')], 'name':[('name','str')],
    'list_comp':[('element','node'),('clauses','nodes')], 'set_comp':[('element','node'),('clauses','nodes')],
    'record_comp':[('key','node'),('value','node'),('clauses','nodes')],
    'gen_comp':[('element','node'),('clauses','nodes')],
    'async_gen_comp':[('element','node'),('clauses','nodes')],
    'comp_for':[('name','str'),('iterable','node')],
    'comp_async_for':[('name','str'),('iterable','node')],
    'comp_if':[('condition','node')],
    'list':[('items','nodes')], 'set':[('items','nodes')], 'tuple':[('items','nodes')], 'record':[('fields','pairs')], 'index':[('object','node'),('index','node')],
    'assign_pattern':[('pattern','node'),('value','node')],
    'with_pattern':[('pattern','node'),('manager','node'),('body','node')],
    'async_with_pattern':[('pattern','node'),('manager','node'),('body','node')],
    'let_pattern':[('pattern','node'),('type','str'),('value','node')],
    'var_pattern':[('pattern','node'),('type','str'),('value','node')],
    'for_pattern':[('pattern','node'),('iterable','node'),('body','node')],
    'async_for_pattern':[('pattern','node'),('iterable','node'),('body','node')],
    'comp_for_pattern':[('pattern','node'),('iterable','node')],
    'comp_async_for_pattern':[('pattern','node'),('iterable','node')],
    'let':[('name','str'),('type','str'),('value','node')], 'var':[('name','str'),('type','str'),('value','node')],
    'class':[('name','str'),('bases','nodes'),('body','node'),('options','nodes')],
    'set_member':[('object','node'),('index','node'),('value','node')],
    'subscript':[('object','node'),('index','node')],
    'set_item':[('object','node'),('index','node'),('value','node')],
    'slice':[('start','optional'),('stop','optional'),('step','optional')],
    'aug_assign':[('target','node'),('operator','str'),('value','node')],
    'assign':[('name','str'),('value','node')], 'fn':[('parameters','params'),('returns','str'),('body','node'),('name','str')],
    'gen_fn':[('parameters','params'),('returns','str'),('body','node'),('name','str')],
    'async_fn':[('parameters','params'),('returns','str'),('body','node'),('name','str')],
    'async_gen_fn':[('parameters','params'),('returns','str'),('body','node'),('name','str')],
    'macro':[('name','str'),('parameters','strings'),('body','node')],
    'macrocall':[('name','str'),('arguments','nodes')], 'quote':[('body','node')], 'unquote':[('value','node')],
    'await':[('value','node')], 'yield':[('value','node')], 'yield_from':[('value','node')],
    'def':[('name','str'),('function','node')], 'agent':[('name','str'),('config','node')],
    'if':[('condition','node'),('then','node'),('else','node')],
    'binary':[('operator','str'),('left','node'),('right','node')], 'unary':[('operator','str'),('operand','node')],
    'call':[('callee','node'),('arguments','nodes')], 'rec':[('definitions','nodes')],
    'keyword':[('name','str'),('value','node')], 'spread':[('value','node')], 'kwspread':[('value','node')],
    'data':[('name','str'),('variants','variants')], 'import':[('path','str'),('name','str')], 'export':[('names','strings')],
    'do':[('body','node')], 'throw':[('value','node')], 'try':[('body','node'),('name','str'),('handler','node')],
    'finally':[('body','node'),('cleanup','node')],
    'loop_else':[('loop','node'),('otherwise','node')],
    'try_else':[('body','node'),('otherwise','node')],
    'try_cases':[('body','node'),('cases','arms')], 'rethrow':[],
    'try_group':[('body','node'),('cases','nodes')],
    'group_case':[('pattern','node'),('guard','optional'),('name','str'),('body','node')],
    'with':[('name','str'),('manager','node'),('body','node')],
    'async_with':[('name','str'),('manager','node'),('body','node')],
    'match':[('value','node'),('arms','arms')], 'while':[('condition','node'),('body','node')],
    'for':[('name','str'),('iterable','node'),('body','node')],
    'async_for':[('name','str'),('iterable','node'),('body','node')], 'break':[], 'continue':[], 'return':[('value','node')],
    'p_any':[], 'p_bind':[('name','str')], 'p_literal':[('value','literal')],
    'p_list':[('items','nodes'),('rest','optional')], 'p_tuple':[('items','nodes'),('rest','optional')], 'p_record':[('fields','pairs'),('rest','optional')],
    'p_tag':[('name','str'),('arguments','nodes')], 'p_class':[('name','str'),('fields','pairs'),('rest','optional')],
}


def plain(value):
    if isinstance(value,(tuple,list)):
        result=[plain(x) for x in value]
        if len(result)==3 and result[0]=='unary' and result[1]=='-' and isinstance(result[2],list) and len(result[2])==2 and result[2][0]=='literal' and type(result[2][1])in(int,float):return ['literal',-result[2][1]]
        if len(result)==5 and result[0] in ('fn','gen_fn','async_fn','async_gen_fn'):result[4]='<derived>'
        return result
    if isinstance(value,dict): return {k:plain(v) for k,v in value.items()}
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def normalize_numbers(program):
    result=copy.deepcopy(program)
    def visit(node):
        if isinstance(node,dict):
            value=node.get('value')
            if node.get('kind')in ('literal','p_literal')and (type(value)is float or (type(value)is int and abs(value)>9007199254740991)):
                node['value']={'$number':repr(value)}
            for child in node.values():visit(child)
        elif isinstance(node,list):
            for child in node:visit(child)
    visit(result.get('root'))
    return result


def parse_program(source, filename='main.chaos', *, language_version=2):
    if type(language_version) is not int or language_version not in (1,2):raise LanguageError('languageVersion 必须是 1 或 2')
    try: tree = Parser(source,filename,legacy_items=language_version==1).program()
    except RecursionError: raise ResourceLimit('源码语法嵌套过深') from None
    counts = {}
    def encode(node, scope='module', depth=0):
        if depth > 128: raise ResourceLimit('程序结构深度超过 128')
        kind = node[0]
        if kind not in SHAPES: raise LanguageError('未知语法结构 '+kind)
        identity = ('root' if depth==0 else (kind+':'+str(node[1]) if kind in ('let','var','def','agent','data','class','for','async_for','with','async_with') else digest(plain(node))))
        key = filename+'|'+scope+'|'+identity
        ordinal = counts.get(key,0); counts[key]=ordinal+1
        result = {'id':'n_'+digest([key,ordinal])[:20], 'kind':kind}
        span = getattr(node,'span',{})
        if span: result['span']=dict(span)
        child_scope = scope+'/'+identity if kind in ('class','def','agent','for','async_for','with','async_with','fn','gen_fn','async_fn','async_gen_fn','rec') else scope
        for (field,typ),value in zip(SHAPES[kind],node[1:]):
            if kind=='class' and field=='options' and not value:continue
            enc=lambda child: encode(child,child_scope,depth+1)
            if typ=='node': result[field]=enc(value)
            elif typ=='optional': result[field]=None if value is None else enc(value)
            elif typ=='nodes': result[field]=[enc(x) for x in value]
            elif typ=='pairs': result[field]=[{'key':k,'value':enc(v)} for k,v in value]
            elif typ=='params':
                result[field]=[]
                for parameter in value:
                    row={'name':parameter[0],'type':parameter[1]}
                    if len(parameter)>=4:
                        row.update(mode=parameter[2],default=None if parameter[3] is None else enc(parameter[3]))
                    if len(parameter)==5:row['pattern']=enc(parameter[4])
                    result[field].append(row)
            elif typ=='variants': result[field]=[{'name':k,'fields':list(v)} for k,v in value]
            elif typ=='arms': result[field]=[{'pattern':enc(p),'guard':None if g is None else enc(g),'body':enc(b)} for p,g,b in value]
            elif typ=='literal' and (type(value)is float or (type(value)is int and abs(value)>9007199254740991)):
                result[field]={'$number':repr(value)}
            else: result[field]=copy.deepcopy(value)
        return result
    result={'schema':SCHEMA,'languageVersion':language_version,'file':filename,'root':encode(tree)}
    # Validation here is structural; parsing already checked grammatical contexts.
    decode_program(result)
    result['semanticHash']=digest(plain(tree))
    return result


def decode_program(program):
    if not isinstance(program,dict) or program.get('schema') != SCHEMA:
        raise LanguageError('需要 chaos.program/1 程序 IR')
    if set(program)-{'schema','languageVersion','sourceVersions','file','root','semanticHash','layout'}: raise LanguageError('未知程序 IR 字段')
    version=program.get('languageVersion',1)
    if type(version) is not int or version not in (1,2):raise LanguageError('languageVersion 必须是 1 或 2')
    versions=program.get('sourceVersions',{})
    if not isinstance(versions,dict) or any(not isinstance(key,str) or type(value) is not int or value not in (1,2) for key,value in versions.items()):raise LanguageError('sourceVersions 必须是文件名到 1 或 2 的记录')
    if not isinstance(program.get('file','main.chaos'),str): raise LanguageError('IR file 必须是字符串')
    ids, count = set(), [0]
    def decode(value,depth=0,allow_slice=False,allow_clause=False,quote_depth=0,argument_context=False,set_item_context=False,group_case_context=False):
        count[0]+=1
        if count[0]>10000 or depth>128: raise ResourceLimit('程序 IR 超过节点数/深度限制')
        if not isinstance(value,dict) or not isinstance(value.get('kind'),str) or value.get('kind') not in SHAPES: raise LanguageError('非法 IR 节点')
        kind=value['kind']; ident=value.get('id')
        if kind=='group_case' and not group_case_context:raise LanguageError('group_case 只能位于 try_group.cases 中')
        if kind in ('comp_for','comp_async_for','comp_for_pattern','comp_async_for_pattern','comp_if') and not allow_clause:
            raise LanguageError('推导子句只能位于 clauses 中')
        if kind=='slice' and not allow_slice:raise LanguageError('slice 仅可用于下标键')
        if kind=='spread' and not (argument_context or set_item_context):raise LanguageError('spread 仅可用于调用实参或集合直接元素')
        if kind in ('keyword','kwspread') and not argument_context:raise LanguageError('具名参数仅可用于调用实参')
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ident) or ident in ids:
            raise LanguageError('IR 节点 id 无效或重复')
        ids.add(ident)
        fields={name for name,_ in SHAPES[kind]}
        required=fields-({'options'} if kind=='class' else set())
        if set(value)-fields-{'id','kind','span'} or not required.issubset(value): raise LanguageError('IR 节点字段不完整 '+kind)
        span=value.get('span',{})
        if not isinstance(span,dict): raise LanguageError('非法源码位置')
        if set(span)-{'file','line','column','endLine','endColumn'}: raise LanguageError('非法源码位置字段')
        for key,item in span.items():
            if (key=='file' and not isinstance(item,str)) or (key!='file' and (type(item)is not int or item<1)):
                raise LanguageError('非法源码位置值')
        result=[kind]
        for field,typ in SHAPES[kind]:
            if kind=='class' and field=='options' and not value.get(field):
                if field in value and type(value[field]) is not list:raise LanguageError('类 options 必须是参数列表')
                continue
            item=value[field]
            # Only the immediate tuple key may hold Slice syntax; nested tuples
            # and ordinary tuple values must use first-class slice_key calls.
            key_context='key' if kind in ('subscript','set_item') and field=='index' else 'component' if kind=='tuple' and field=='items' and allow_slice=='key' else False
            dec=lambda child:decode(child,depth+1,key_context,kind in ('list_comp','set_comp','record_comp','gen_comp','async_gen_comp') and field=='clauses',quote_depth+(kind=='quote'),(kind=='call' and field=='arguments') or (kind=='class' and field=='options'),kind=='set' and field=='items',kind=='try_group' and field=='cases')
            if typ=='str':
                if not isinstance(item,str) or len(item)>100000: raise LanguageError('IR 字段需要字符串 '+field)
                item.encode('utf-8');result.append(item)
            elif typ=='literal':
                if isinstance(item,dict):
                    if set(item)!={'$number'} or not isinstance(item['$number'],str) or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?',item['$number']) or len(item['$number'])>4300:raise LanguageError('非法精确数字字面量')
                    raw=item['$number']
                    item=float(raw) if any(c in raw for c in '.eE') else int(raw)
                if item is not None and type(item) not in (bool,int,float,str): raise LanguageError('IR 字面量需要标量')
                serializable(item);result.append(item)
            elif typ=='node':result.append(dec(item))
            elif typ=='optional':result.append(None if item is None else dec(item))
            else:
                if not isinstance(item,list) or len(item)>10000: raise ResourceLimit('IR 列表过大或无效')
                if typ=='nodes':
                    if kind in ('list_comp','set_comp','record_comp','gen_comp','async_gen_comp') and field=='clauses':
                        if not item or len(item)>64 or any(not isinstance(v,dict) or v.get('kind') not in ('comp_for','comp_async_for','comp_for_pattern','comp_async_for_pattern','comp_if') for v in item) or item[0].get('kind') not in ('comp_for','comp_async_for','comp_for_pattern','comp_async_for_pattern'):
                            raise LanguageError('推导需要 1–64 个子句，且以 for 开始')
                    result.append([dec(v)for v in item])
                elif typ=='strings':
                    if any(not isinstance(v,str)for v in item):raise LanguageError('IR 需要字符串列表')
                    result.append(list(item))
                elif typ in ('pairs','params','variants','arms'):
                    rows=[]
                    expected={'pairs':{'key','value'},'params':{'name','type'},'variants':{'name','fields'},'arms':{'pattern','guard','body'}}[typ]
                    for row in item:
                        allowed=[expected,expected|{'pattern'},{'name','type','mode','default'},{'name','type','mode','default','pattern'}] if typ=='params' else [expected]
                        if not isinstance(row,dict)or set(row)not in allowed:raise LanguageError('IR 复合字段无效 '+field)
                        if typ=='pairs':
                            if not isinstance(row['key'],str):raise LanguageError('记录键必须是字符串')
                            rows.append((row['key'],dec(row['value'])))
                        elif typ=='params':
                            if not isinstance(row['name'],str)or not isinstance(row['type'],str):raise LanguageError('参数定义无效')
                            if 'mode' not in row and 'pattern' not in row:
                                rows.append((row['name'],row['type']))
                            else:
                                mode=row.get('mode','positional');default=row.get('default')
                                if mode not in ('positional','positional_only','keyword_only','varargs','varkw'):raise LanguageError('非法参数模式')
                                if mode in ('varargs','varkw') and default is not None:raise LanguageError('剩余参数不能有默认值')
                                if 'pattern' in row:
                                    from .patterns import pattern_names
                                    pattern=dec(row['pattern']);pattern_names(pattern)
                                    rows.append((row['name'],row['type'],mode,None if default is None else dec(default),pattern))
                                elif mode=='positional' and default is None:
                                    rows.append((row['name'],row['type']))
                                else:
                                    rows.append((row['name'],row['type'],mode,None if default is None else dec(default)))
                        elif typ=='variants':
                            if not isinstance(row['name'],str)or not isinstance(row['fields'],list)or any(not isinstance(v,str)for v in row['fields']):raise LanguageError('构造器定义无效')
                            rows.append((row['name'],list(row['fields'])))
                        else:rows.append((dec(row['pattern']),None if row['guard']is None else dec(row['guard']),dec(row['body'])))
                    result.append(rows)
        if kind=='set' and not result[1]:raise LanguageError('set 节点需要至少一项；空集合使用 {*[]}')
        if kind=='try_cases':
            from .language import validate_try_cases
            validate_try_cases(tuple(result))
        if kind=='try_group':
            from .language import validate_try_group
            validate_try_group(tuple(result))
        if kind in ('loop_else','try_else'):
            permitted=('while','for','async_for','for_pattern','async_for_pattern') if kind=='loop_else' else ('try','try_cases','try_group')
            if result[1][0] not in permitted or result[2][0]!='block':
                raise LanguageError('完成分支需要原生循环或 try/catch 及 block else 主体')
        if kind=='finally' and (result[1][0] not in ('block','try','try_cases','try_group','try_else') or result[2][0]!='block'):
            raise LanguageError('finally 需要 block、try/catch 或 try/catch/else 及 block 清理主体')
        if kind in ('p_tuple','p_class'):
            from .patterns import pattern_names
            pattern_names(tuple(result))
        if kind=='aug_assign':
            from .operators import AUGMENTED
            if result[1][0] not in (('name','index','subscript','unquote') if quote_depth else ('name','index','subscript')) or result[2] not in AUGMENTED.values():
                raise LanguageError('增强赋值需要名称、成员或下标目标及受支持的运算符')
        if kind in ('assign_pattern','with_pattern','async_with_pattern'):
            from .patterns import pattern_names
            if result[1][0] not in ('p_list','p_tuple','p_record','p_tag','p_class'):
                raise LanguageError('结构赋值或资源解构需要列表、元组、记录、构造器或类模式')
            pattern_names(result[1])
            if kind != 'assign_pattern' and result[3][0] != 'block':
                raise LanguageError('with 的主体必须是 block')
        if kind in ('let_pattern','var_pattern','for_pattern','async_for_pattern','comp_for_pattern','comp_async_for_pattern'):
            from .patterns import pattern_names
            if result[1][0] in ('p_bind','p_any'):raise LanguageError('简单名称目标必须使用原有绑定节点')
            pattern_names(result[1])
        if kind in ('fn','gen_fn','async_fn','async_gen_fn'):
            from .parameters import validate_parameters
            validate_parameters(result[1])
        if kind=='class' and len(result)==5:
            if any(option[0] not in ('keyword','kwspread') for option in result[4]):raise LanguageError('类 options 只接受具名参数和 ** 展开')
            labels=[option[1] for option in result[4] if option[0]=='keyword']
            if len(labels)!=len(set(labels)):raise LanguageError('类具名参数不能重复',code='argument_error')
        return Node(result,span)
    root=decode(program.get('root'))
    if root[0]!='block':raise LanguageError('程序根必须是 block')
    if len(json.dumps(program,ensure_ascii=False))>2000000:raise ResourceLimit('程序 IR 超过 2 MB')
    return root


def quote(value):return json.dumps(value,ensure_ascii=False,allow_nan=False)


def render(node,indent=0):
    k=node[0]; pad='  '*indent
    def expr(n):return render(n,indent)
    def item_key(n):
        if n[0]!='tuple':return expr(n)
        if not n[1]:return '()'
        return ', '.join(expr(item) for item in n[1])+(',' if len(n[1])==1 else '')
    def block(n):
        if n[0]!='block':raise LanguageError('需要语句块')
        if not n[1]:return '{}'
        return '{\n'+'\n'.join('  '*(indent+1)+render(x,indent+1)+';'for x in n[1])+'\n'+pad+'}'
    def function(n):
        if n[0] not in ('fn','gen_fn','async_fn','async_gen_fn'):raise LanguageError('函数声明需要 fn、fn*、async fn 或 async fn*')
        parts=[];keyword_mode=False
        for index,parameter in enumerate(n[1]):
            name,typ=parameter[:2]
            mode,default=parameter[2:4] if len(parameter)>=4 else ('positional',None)
            if mode=='keyword_only' and not keyword_mode:
                parts.append('*');keyword_mode=True
            prefix='*' if mode=='varargs' else '**' if mode=='varkw' else ''
            if mode=='varargs':keyword_mode=True
            parts.append(prefix+name+(' as '+expr(parameter[4]) if len(parameter)==5 else '')+(''if typ=='Any'else': '+typ)+(' = '+expr(default) if default is not None else ''))
            if mode=='positional_only' and (index+1==len(n[1]) or len(n[1][index+1])<4 or n[1][index+1][2]!='positional_only'):parts.append('/')
        return '('+', '.join(parts)+')'+(''if n[2]=='Any'else' -> '+n[2])+' '+block(n[3])
    if k=='decorated':
        if not 1<=len(node[1])<=64 or node[2][0] not in ('def','class','agent'):raise LanguageError('装饰器需要 1–64 个表达式及函数、类或智能体声明')
        return ('\n'+pad).join(['@'+expr(value) for value in node[1]]+[expr(node[2])])
    if k=='block':return '\n'.join(render(n,indent)+';'for n in node[1])
    if k in ('literal','p_literal'):return quote(node[1])
    if k in ('name','p_bind'):return node[1]
    if k=='set':
        if not node[1]:raise LanguageError('set 节点需要至少一项；空集合使用 {*[]}')
        return '{'+', '.join(expr(n) for n in node[1])+'}'
    if k in ('list','p_list'):
        items=[expr(n)for n in node[1]]
        if k=='p_list'and node[2]is not None:items.append('..'+expr(node[2]))
        return '['+', '.join(items)+']'
    if k in ('tuple','p_tuple'):
        items=[expr(n)for n in node[1]]
        if k=='p_tuple' and node[2] is not None:items.append('..'+expr(node[2]))
        return '('+', '.join(items)+(',' if len(items)==1 else '')+')'
    if k=='p_class':
        fields=[quote(key)+': '+expr(value) for key,value in node[2]]
        if node[3] is not None:fields.append('..'+expr(node[3]))
        return node[1]+'::{'+', '.join(fields)+'}'
    if k in ('record','p_record'):
        fields=[quote(key)+': '+expr(value)for key,value in node[1]]
        if k=='p_record'and node[2]is not None:fields.append('..'+expr(node[2]))
        return '{'+', '.join(fields)+'}'
    if k in ('list_comp','set_comp','record_comp','gen_comp','async_gen_comp'):
        clauses=node[-1]
        if not clauses or len(clauses)>64 or clauses[0][0] not in ('comp_for','comp_async_for','comp_for_pattern','comp_async_for_pattern'):
            raise LanguageError('推导需要 1–64 个子句，且以 for 开始')
        parts=[]
        for clause in clauses:
            if clause[0] in ('comp_for','comp_async_for','comp_for_pattern','comp_async_for_pattern'):
                parts.append(('async for ' if clause[0] in ('comp_async_for','comp_async_for_pattern') else 'for ')+(expr(clause[1]) if clause[0].endswith('_pattern') else clause[1])+' in '+expr(clause[2]))
            elif clause[0]=='comp_if':parts.append('if '+expr(clause[1]))
            else:raise LanguageError('非法推导子句')
        head=expr(node[1])+(': '+expr(node[2]) if k=='record_comp' else '')
        opening,closing={'list_comp':('[',']'),'set_comp':('{','}'),'record_comp':('{','}'),'gen_comp':('(',')'),'async_gen_comp':('async (',')')}[k]
        return opening+head+' '+' '.join(parts)+closing
    if k=='index':return '('+expr(node[1])+').['+expr(node[2])+']'
    if k=='subscript':return '('+expr(node[1])+')['+item_key(node[2])+']'
    if k=='slice':return ':'.join('' if value is None else expr(value) for value in node[1:])
    if k in ('let_pattern','var_pattern'):return k.removesuffix('_pattern')+' '+expr(node[1])+(''if node[2]=='Any'else': '+node[2])+' = '+expr(node[3])
    if k in ('let','var'):return k+' '+node[1]+(''if node[2]=='Any'else': '+node[2])+' = '+expr(node[3])
    if k=='aug_assign':return expr(node[1])+' '+node[2]+'= '+expr(node[3])
    if k=='assign':return node[1]+' = '+expr(node[2])
    if k=='assign_pattern':return expr(node[1])+' = '+expr(node[2])
    if k=='set_member':return '('+expr(node[1])+').['+expr(node[2])+'] = '+expr(node[3])
    if k=='set_item':return '('+expr(node[1])+')['+item_key(node[2])+'] = '+expr(node[3])
    if k=='class':
        from .class_creation import class_options
        header=[*node[2],*class_options(node)]
        return 'class '+node[1]+('('+', '.join(expr(item)for item in header)+')' if header else '')+' '+block(node[3])
    if k in ('fn','gen_fn','async_fn','async_gen_fn'):return {'fn':'fn','gen_fn':'fn*','async_fn':'async fn','async_gen_fn':'async fn*'}[k]+function(node)
    if k=='def':return {'fn':'fn','gen_fn':'fn*','async_fn':'async fn','async_gen_fn':'async fn*'}.get(node[2][0],'fn')+' '+node[1]+function(node[2])
    if k in ('await','yield','yield_from'):
        operand=expr(node[1])
        # A bare bracket after contextual await/yield is legacy item access.
        # Group only that operand so list values retain suspension semantics.
        if k in ('await','yield') and operand.startswith('['):operand='('+operand+')'
        return '('+('yield from' if k=='yield_from' else k)+' '+operand+')'
    if k=='macro':return 'macro '+node[1]+'('+', '.join(node[2])+') '+block(node[3])
    if k=='macrocall':return node[1]+'!('+', '.join(expr(a)for a in node[2])+')'
    if k=='quote':return 'quote '+block(node[1])
    if k=='unquote':return 'unquote('+expr(node[1])+')'
    if k=='agent':return 'agent '+node[1]+' '+expr(node[2])
    if k=='if':return '(if '+expr(node[1])+' '+block(node[2])+' else '+(expr(node[3])[1:-1]if node[3][0]=='if'else block(node[3]))+')'
    if k=='binary':return '('+expr(node[2])+' '+node[1]+' '+expr(node[3])+')'
    if k=='unary':return '('+node[1]+expr(node[2])+')'
    if k=='call':return '('+expr(node[1])+')('+', '.join(expr(a)for a in node[2])+')'
    if k=='keyword':return node[1]+' = '+expr(node[2])
    if k in ('spread','kwspread'):return ('*' if k=='spread' else '**')+expr(node[1])
    if k=='rec':return 'rec {\n'+'\n'.join('  '*(indent+1)+render(d,indent+1)+';'for d in node[1])+'\n'+pad+'}'
    if k=='data':return 'data '+node[1]+' { '+', '.join(tag+'('+', '.join(fields)+')'for tag,fields in node[2])+' }'
    if k=='import':return 'import '+quote(node[1])+' as '+node[2]
    if k=='export':return 'export { '+', '.join(node[1])+' }'
    if k=='do':return '(do '+block(node[1])+')'
    if k in ('throw','return'):return '('+k+' '+expr(node[1])+')'
    if k=='rethrow':return '(throw)'
    if k=='try_cases':return '(try '+block(node[1])+''.join(' catch '+expr(p)+(' if '+expr(g) if g is not None else '')+' '+block(b) for p,g,b in node[2])+')'
    if k=='try_group':return '(try '+block(node[1])+''.join(' catch* '+expr(case[1])+(' if '+expr(case[2]) if case[2] is not None else '')+' as '+case[3]+' '+block(case[4]) for case in node[2])+')'
    if k=='try':return '(try '+block(node[1])+' catch '+node[2]+' '+block(node[3])+')'
    if k in ('loop_else','try_else'):
        permitted=('while','for','async_for','for_pattern','async_for_pattern') if k=='loop_else' else ('try','try_cases','try_group')
        if node[1][0] not in permitted:raise LanguageError('完成分支的保护结构无效')
        return '('+expr(node[1])[1:-1]+' else '+block(node[2])+')'
    if k=='finally':
        protected=node[1]
        if protected[0]=='block':prefix='try '+block(protected)
        elif protected[0] in ('try','try_cases','try_group','try_else'):prefix=expr(protected)[1:-1]
        else:raise LanguageError('finally 的保护结构必须是 block、try/catch 或 try/catch/else')
        return '('+prefix+' finally '+block(node[2])+')'

    if k=='match':return '(match '+expr(node[1])+' {\n'+',\n'.join('  '*(indent+1)+render(p,indent+1)+(' if '+render(g,indent+1)if g is not None else'')+' => '+render(b,indent+1)for p,g,b in node[2])+'\n'+pad+'})'
    if k=='while':return '(while '+expr(node[1])+' '+block(node[2])+')'
    if k in ('for_pattern','async_for_pattern'):return '('+('async for ' if k=='async_for_pattern' else 'for ')+expr(node[1])+' in '+expr(node[2])+' '+block(node[3])+')'
    if k in ('for','async_for'):return '('+('async for ' if k=='async_for' else 'for ')+node[1]+' in '+expr(node[2])+' '+block(node[3])+')'
    if k in ('with','async_with','with_pattern','async_with_pattern'):
        if node[3][0]!='block':raise LanguageError('with 的主体必须是 block')
        return '('+('async with ' if k in ('async_with','async_with_pattern') else 'with ')+expr(node[2])+' as '+(expr(node[1]) if k.endswith('_pattern') else node[1])+' '+block(node[3])+')'
    if k in ('break','continue'):return k
    if k=='p_any':return '_'
    if k=='p_tag':return node[1]+'('+', '.join(expr(n)for n in node[2])+')'
    raise LanguageError('不能打印语法 '+k)


def format_program(program):
    tree=decode_program(program); source=render(tree)+'\n'
    if len(source)>100000:raise ResourceLimit('格式化源码超过 100 KB')
    try: parsed=Parser(source,program.get('file','main.chaos')).program()
    except RecursionError:raise ResourceLimit('程序语法嵌套过深')from None
    if digest(plain(tree))!=digest(plain(parsed)):raise LanguageError('IR 结构不符合语言语法，拒绝有损打印')
    return source


def validate_program(program):
    source=format_program(program)
    return {'valid':True,'semanticHash':digest(plain(decode_program(program))),'source':source}


def children(node):
    result=[]
    for field,typ in SHAPES[node['kind']]:
        value=node.get(field,[]) if node['kind']=='class' and field=='options' else node[field]
        if typ=='node':result.append((field,value))
        elif typ=='optional'and value is not None:result.append((field,value))
        elif typ=='nodes':result.extend((field+'.'+str(i),n)for i,n in enumerate(value))
        elif typ=='pairs':result.extend((field+'.'+str(i)+'.value',row['value'])for i,row in enumerate(value))
        elif typ=='params':
            for i,row in enumerate(value):
                result.extend((field+'.'+str(i)+'.'+key,row[key])for key in ('default','pattern')if row.get(key) is not None)
        elif typ=='arms':
            for i,row in enumerate(value):
                result.extend((field+'.'+str(i)+'.'+k,v)for k,v in row.items()if v is not None)
    return result


def project_program(program):
    validate_program(program)
    nodes,edges=[],[]
    def walk(node,parent=None,slot=None,depth=0):
        nodes.append({'id':node['id'],'kind':node['kind'],'label':node.get('name',node.get('operator',node['kind'])),
                      'parentId':parent,'slot':slot,'depth':depth,'span':node.get('span',{})})
        if parent:edges.append({'source':parent,'target':node['id'],'kind':'syntax','slot':slot})
        for child_slot,child in children(node):walk(child,node['id'],child_slot,depth+1)
    walk(program['root'])
    return {'kind':'syntax-tree','nodes':nodes,'edges':edges,'semanticHash':digest(plain(decode_program(program)))}


def patch_program(program,operations):
    validate_program(program)
    if not isinstance(operations,list)or len(operations)>100:raise LanguageError('程序补丁最多 100 项')
    result=copy.deepcopy(program)
    for op in operations:
        if not isinstance(op,dict)or not isinstance(op.get('op'),str)or op.get('op')not in ('set','replace','append','remove','move'):raise LanguageError('未知程序补丁')
        index={}
        def scan(node,parent=None,slot=None):
            index[node['id']]=(node,parent,slot)
            for key,child in children(node):scan(child,node,key)
        scan(result['root'])
        if not isinstance(op.get('id'),str)or op.get('id')not in index:raise LanguageError('程序节点不存在')
        node,parent,slot=index[op['id']]
        if op['op']=='set':
            field=op.get('field')
            if not isinstance(field,str)or field not in dict(SHAPES[node['kind']]):raise LanguageError('不可修改该字段')
            node[field]=copy.deepcopy(op.get('value'))
            if node['kind']=='class' and field=='options' and node[field]==[]:del node[field]
        elif op['op']=='replace':
            replacement=copy.deepcopy(op.get('node'))
            if not isinstance(replacement,dict):raise LanguageError('替换需要节点')
            replacement['id']=node['id'];node.clear();node.update(replacement)
        elif op['op']=='append':
            field=op.get('field')
            if not isinstance(field,str)or dict(SHAPES[node['kind']]).get(field)!='nodes':raise LanguageError('只能向节点列表追加')
            if node['kind']=='class' and field=='options':node.setdefault(field,[])
            node[field].append(copy.deepcopy(op.get('node')))
        elif op['op']=='remove':
            if parent is None or slot is None:raise LanguageError('不能删除根节点')
            parts=slot.split('.')
            if len(parts)!=2 or dict(SHAPES[parent['kind']]).get(parts[0])!='nodes':raise LanguageError('只能删除节点列表成员')
            parent[parts[0]].pop(int(parts[1]))
            if parent['kind']=='class' and parts[0]=='options' and not parent['options']:del parent['options']
        else:
            field=op.get('field');old,new=op.get('from'),op.get('to')
            if not isinstance(field,str)or dict(SHAPES[node['kind']]).get(field)!='nodes'or type(old)is not int or type(new)is not int or not(0<=old<len(node.get(field,[]))and 0<=new<len(node.get(field,[]))):raise LanguageError('非法节点排序')
            node[field].insert(new,node[field].pop(old))
        decode_program(result)
    source=format_program(result)
    result['semanticHash']=digest(plain(decode_program(result)))
    # Positions belong to the newly formatted source; structural IDs remain untouched.
    fresh=parse_program(source,result.get('file','main.chaos'))
    def spans(old,new):
        if 'span'in new:old['span']=new['span']
        else:old.pop('span',None)
        for (_,a),(_,b)in zip(children(old),children(new)):spans(a,b)
    spans(result['root'],fresh['root'])
    return result


def _workflow_payload(graph):
    from .graph import validate
    validate(graph)
    graph=copy.deepcopy(graph)
    for node in graph['nodes']:
        node.pop('x',None);node.pop('y',None)
    return json.dumps(graph,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def workflow_source(graph, *, previous_source=None, files=None, entry='main.chaos'):
    """Generate readable source; layout-only edits retain the saved text.

    Compare ordered JSON text so numeric types, signed zero, field order and
    every property except node coordinates remain part of the definition.
    Callers may supply only their authoritative previous source for retention.
    """
    payload=_workflow_payload(graph)
    if previous_source is not None:
        previous=extract_workflow(previous_source)
        if previous is not None and _workflow_payload(previous)==payload:
            return previous_source
    # Late import: the shared formatter imports this module's AST plain helper.
    from .source_format import _format_generated_source
    source='workflow('+payload+', input)\n'
    max_bytes=None
    if files is not None:
        from .modules import bundle_files, MAX_BYTES
        compact_files,canonical_entry=bundle_files(source,files,entry)
        max_bytes=MAX_BYTES-sum(len(text.encode('utf-8')) for name,text in compact_files.items() if name!=canonical_entry)
    return _format_generated_source(source,entry,max_bytes=max_bytes)


def extract_workflow(source):
    tree=Parser(source).program()
    if len(tree[1])!=1:return None
    call=tree[1][0]
    if call[0]!='call'or call[1]!=('name','workflow')or len(call[2])!=2 or call[2][1]!=('name','input'):return None
    def literal(n):
        if n[0]=='literal':return n[1]
        if n[0]=='unary'and n[1]=='-'and n[2][0]=='literal'and type(n[2][1])in(int,float):return -n[2][1]
        if n[0]=='list':return [literal(x)for x in n[1]]
        if n[0]=='record':return {k:literal(v)for k,v in n[1]}
        raise LanguageError('工作流图必须是字面量')
    try:graph=literal(call[2][0])
    except LanguageError:return None
    from .graph import validate
    validate(graph)
    return graph


def templates():
    """Syntactically valid building blocks; enclosing-context rules still apply."""
    snippets={
        'decorated':'@(fn(value) { value })\nfn compute(value) { value }',
        'list_comp':'[item for item in []]','set_comp':'{item for item in []}',
        'record_comp':'{item: item for item in []}',
        'gen_comp':'(item for item in [])',
        'async_gen_comp':'async (item for item in [])',
        'literal':'0','name':'input','set':'{0}','list':'[]','tuple':'()','record':'{}','index':'input.[0]','subscript':'input[0]',
        'assign_pattern':'[value] = [0]',
        'with_pattern':'with input as [value] { value }',
        'async_with_pattern':'async with input as [value] { value }',
        'let_pattern':'let [value] = [0]',
        'var_pattern':'var [value] = [0]',
        'for_pattern':'for [value] in [] {value}',
        'async_for_pattern':'async for [value] in input {value}',
        'let':'let value = 0','var':'var count: Int = 0','assign':'count = 0','aug_assign':'count += 1',
        'class':'class Example { var value: Int = 0; fn read(self) { self.value } }',
        'set_member':'input.value = 0','set_item':'input[0] = 0',
        'fn':'fn(value) { value }','def':'fn compute(value) { value }',
        'macro':'macro identity(value) { quote { unquote(value) } }',
        'macrocall':'identity!(input)',
        'async_gen_fn':'async fn*(value) { yield value }','async_for':'async for item in (async fn*(){yield null})() { item }',
        'gen_fn':'fn*(value) { yield value }','async_fn':'async fn(value) { value }','await':'await gather([])',
        'agent':'agent assistant {prompt:"描述任务",provider:"local",tools:[],maxSteps:4}',
        'if':'if true { 1 } else { 0 }','binary':'1 + 1','unary':'!true','call':'length([])',
        'rec':'rec { fn even(n) { if n == 0 { true } else { odd(n - 1) } } fn odd(n) { if n == 0 { false } else { even(n - 1) } } }',
        'data':'data Option { None, Some(value) }','import':'import "std/list" as list','export':'export {}',
        'do':'do { null }','throw':'throw {message:"错误"}','try':'try { null } catch error { error.message }',
        'finally':'try { null } finally { null }',
        'loop_else':'for item in [] { item } else { null }',
        'try_else':'try { null } catch error { error.message } else { null }',
        'try_cases':'try { null } catch {code: code, .._} if code == "user_throw" { null } catch error { error.message }',
        'try_group':'try { null } catch* _ as failures { failures.message }',
        'rethrow':'throw',
        'with':'with input as resource { resource }','async_with':'async with input as resource { resource }',
        'match':'match input { _ => null }','while':'while false { null }','for':'for item in [] { item }',
    }
    result={kind:parse_program(source)['root']['statements'][0]for kind,source in snippets.items()}
    result['group_case']=result['try_group']['cases'][0]
    for kind,source in {'keyword':'f(value=0)','spread':'f(*[])','kwspread':'f(**{})'}.items():
        result[kind]=parse_program(source)['root']['statements'][0]['arguments'][0]
    for kind,source in {'yield':'fn* f(){yield null}','yield_from':'fn* f(){yield from []}'}.items():
        result[kind]=parse_program(source)['root']['statements'][0]['function']['body']['statements'][0]
    clauses=parse_program('[item async for item in input if true]')['root']['statements'][0]['clauses']
    result['comp_for']=parse_program('[item for item in []]')['root']['statements'][0]['clauses'][0]
    result['comp_async_for'],result['comp_if']=clauses
    result['comp_for_pattern']=parse_program('[value for [value] in []]')['root']['statements'][0]['clauses'][0]
    result['comp_async_for_pattern']=parse_program('[value async for [value] in input]')['root']['statements'][0]['clauses'][0]
    result['slice']=parse_program('input[:]')['root']['statements'][0]['index']
    macro_template=parse_program('macro identity(value) { quote { unquote(value) } }')['root']['statements'][0]
    result['quote']=macro_template['body']['statements'][0]
    result['unquote']=result['quote']['body']['statements'][0]
    result['block']=parse_program('null')['root']
    result['return']=parse_program('fn f(){return null}')['root']['statements'][0]['function']['body']['statements'][0]
    for kind in ('break','continue'):
        result[kind]=parse_program('while true{'+kind+'}')['root']['statements'][0]['body']['statements'][0]
    patterns={'p_any':'_','p_bind':'value','p_literal':'0','p_list':'[]','p_tuple':'()','p_record':'{}','p_tag':'Some(value)','p_class':'Example::{field:value,..rest}'}
    for kind,pattern in patterns.items():
        result[kind]=parse_program('match input {'+pattern+'=>null}')['root']['statements'][0]['arms'][0]['pattern']
    return result
