"""Immutable language code and explicit runtime closure construction.

No host eval, secondary evaluator, implicit locals or live AST sharing. Source
and IR pass the same grammar/control validator; construction uses original AST.
"""
from dataclasses import dataclass
import hashlib,json,re
from .language import Parser,Env,Function,LanguageError,ResourceLimit

FLAVORS=('fn','gen_fn','async_fn','async_gen_fn')
def error(message):raise LanguageError(message,code='code_error')
def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def sha(text):return hashlib.sha256(text.encode()).hexdigest()

def data(value,checkpoint=None):
    from .extensions import json_copy
    try:
        result=json_copy(value,max_chars=100000,max_nodes=20000)
        if checkpoint is not None:
            pending=[result]
            while pending:
                checkpoint();item=pending.pop()
                if type(item) is dict:pending.extend(item.values())
                elif type(item) is list:pending.extend(item)
        return result
    except (ValueError,TypeError,OverflowError) as fault:error(str(fault))

def function_node(tree):
    items=tree[1]
    if len(items)!=1:error('Code 需要恰好一个函数表达式或命名函数声明')
    node=items[0]
    while node[0]=='block' and len(node[1])==1:node=node[1][0]
    fn=node[2] if node[0]=='def' and len(node)==3 else node
    if fn[0] not in FLAVORS:error('Code 的执行根必须是普通、异步或生成器函数')
    return node,fn

def parsed(source,filename,checkpoint=None,legacy=False):
    tree=Parser(source,filename,legacy_items=legacy,checkpoint=checkpoint).program()
    if any(item[0] in ('import','export','data') for item in tree[1]):error('Code 不加载模块或声明模块数据')
    from .macros import Expander
    expanded=Expander({filename:tree},checkpoint=checkpoint).expand()[filename]
    function_node(expanded)
    return expanded

@dataclass(frozen=True,eq=False)
class Code:
    source: str
    ir_json: str
    info_json: str
    owner: object

def owned(value,evaluator):
    if type(value) is not Code:error('需要原生 Code 值')
    if value.owner is not evaluator:raise LanguageError('Code 不能跨执行上下文使用；请传递源码或 IR',code='ownership_error')
    return value

def describe(value):return {'$code':json.loads(value.info_json)['sourceHash']}
def make(evaluator,source):
    from .program import parse_program,format_program
    if type(source) is dict:
        original=data(source,evaluator.tick)
        source=format_program(original)
        version=original.get('languageVersion',1)
    elif type(source) is str:version=2
    else:error('code_from 需要 String 或 chaos.program/1 IR')
    if len(source)>100000:raise ResourceLimit('Code 源码超过 100 KB')
    # Canonical rendering removes executable metadata and revalidates grammar.
    filename='code/v'+str(version)+'/'+sha(source)[:24]+'.chaos'
    Parser(source,filename,legacy_items=version==1,checkpoint=evaluator.tick).program()
    ir=parse_program(source,filename,language_version=version)
    text=format_program(ir)
    ir=parse_program(text,filename,language_version=version)
    tree=parsed(text,filename,evaluator.tick,version==1);node,fn=function_node(tree)
    count=0
    def visit(value):
        nonlocal count
        evaluator.tick()
        if isinstance(value,dict):
            if 'kind' in value:count+=1
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(ir['root'])
    info={'schema':'chaos.code/1','sourceHash':sha(text),'semanticHash':ir['semanticHash'],
          'flavor':fn[0],'name':node[1] if node[0]=='def' else fn[4],
          'parameterCount':len(fn[1]),'nodeCount':count}
    return Code(text,canonical(ir),canonical(info),evaluator)

@dataclass(frozen=True)
class FunctionBuildRequest:
    code: Code
    bindings: tuple

def request(evaluator,code,bindings=None):
    owned(code,evaluator)
    if type(bindings) is not dict or len(bindings)>256:error('bindings 需要至多 256 项 Record')
    from .class_creation import validate_value
    # Snapshot associations before any language callback can suspend. Values
    # intentionally retain original identity and their existing authority.
    captured=dict(bindings)
    for name,value in captured.items():
        evaluator.tick()
        if type(name) is not str or len(name)>240 or re.fullmatch(r'[^\W\d]\w*',name,re.UNICODE) is None or name in Parser.reserved:
            error('bindings 名称必须是合法非保留标识符')
        validate_value(evaluator,value)
    return FunctionBuildRequest(code,tuple(captured.items()))

def construct(evaluator, request):
    from .execution_callables import callable_protocol
    return (yield from callable_protocol(evaluator, 'build', request))

def builtins(evaluator):
    def edit(code,operations):
        from .program import patch_program
        original=json.loads(owned(code,evaluator).ir_json)
        return make(evaluator,patch_program(original,data(operations,evaluator.tick)))
    return {'code_from':lambda source:make(evaluator,source),
            'code_source':lambda code:owned(code,evaluator).source,
            'code_ir':lambda code:json.loads(owned(code,evaluator).ir_json),
            'code_info':lambda code:json.loads(owned(code,evaluator).info_json),
            'code_edit':edit,
            'function_from':lambda code,bindings={}:request(evaluator,code,bindings)}
