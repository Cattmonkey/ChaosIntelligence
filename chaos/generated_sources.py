"""Owned runtime syntax provenance; no executable values or workspace mutation."""
from dataclasses import dataclass
import hashlib,re,json
from .language import LanguageError,ResourceLimit,Node
MAX_SOURCES=64
MAX_CHARACTERS=1000000
@dataclass(frozen=True)
class Source:
    file:str
    source:str
    language_version:int
    source_hash:str
    semantic_hash:str
    context_json:object=None
    def record(self):
        return {'source':self.source,'languageVersion':self.language_version,'sourceHash':self.source_hash,'semanticHash':self.semantic_hash}
class Registry:
    def __init__(self):self.values={};self.characters=0
    def register(self,value,fixed=()):
        if type(value)is not Source:raise LanguageError('需要原生生成源码描述',code='generated_source_error')
        if type(value.file)is not str or not value.file or len(value.file)>240 or type(value.source)is not str or len(value.source)>100000 or type(value.language_version)is not int or value.language_version not in (1,2) or type(value.source_hash)is not str or value.source_hash!=hashlib.sha256(value.source.encode()).hexdigest() or type(value.semantic_hash)is not str or re.fullmatch(r'[a-f0-9]{64}',value.semantic_hash)is None:
            raise LanguageError('生成源码描述无效',code='generated_source_error')
        if value.context_json is not None:
            if type(value.context_json) is not str or len(value.context_json)>80000:raise LanguageError('生成模块分析上下文无效',code='generated_source_error')
            try:c=json.loads(value.context_json)
            except (ValueError,TypeError):raise LanguageError('生成模块分析上下文无效',code='generated_source_error') from None
            if type(c) is not dict or set(c)!={'entry','virtualFiles','languageVersions'} or type(c['entry']) is not str or type(c['virtualFiles']) is not dict or type(c['languageVersions']) is not dict or not 1<=len(c['virtualFiles'])<=75 or c['entry'] not in c['virtualFiles'] or set(c['virtualFiles'])!=set(c['languageVersions']) or value.file not in c['virtualFiles'].values() or any(type(k) is not str or len(k)>240 or type(v) is not str or len(v)>240 for k,v in c['virtualFiles'].items()) or any(type(v) is not int or v not in (1,2) for v in c['languageVersions'].values()):raise LanguageError('生成模块分析上下文无效',code='generated_source_error')
        if value.file in fixed:raise LanguageError('生成源码不能覆盖项目文件',code='generated_source_error')
        old=self.values.get(value.file)
        if old is not None:
            if old!=value:raise LanguageError('生成源码标识冲突',code='generated_source_error')
            return old
        if len(self.values)>=MAX_SOURCES or self.characters+len(value.source)>MAX_CHARACTERS:raise ResourceLimit('生成源码登记超过64项或1000000字符')
        self.values[value.file]=value;self.characters+=len(value.source);return value

    def preflight(self,values,fixed=()):
        trial=Registry();trial.values=dict(self.values);trial.characters=self.characters
        for value in values:trial.register(value,fixed)
        return trial
    def register_many(self,values,fixed=()):
        trial=self.preflight(values,fixed)
        self.values,self.characters=trial.values,trial.characters
    def contexts(self):
        return {hashlib.sha256(value.context_json.encode()).hexdigest():json.loads(value.context_json) for value in self.values.values() if value.context_json is not None}

def register_batch(evaluator,values):
    fixed=getattr(getattr(evaluator,'bundle',None),'files',{})
    evaluator.generated_sources.preflight(values,fixed)
    handler=evaluator.source_handler;sink=getattr(handler,'__self__',None)
    if sink is not None and hasattr(sink,'preflight_sources'):sink.preflight_sources(values)
    evaluator.generated_sources.register_many(values,fixed)
    if sink is not None and hasattr(sink,'register_sources'):sink.register_sources(values)
    elif handler is not None:
        for value in values:handler(value)

def register(evaluator,value):
    fixed=getattr(getattr(evaluator,'bundle',None),'files',{})
    evaluator.generated_sources.register(value,fixed)
    if evaluator.source_handler is not None:evaluator.source_handler(value)

def tag(tree,value):
    pending=[tree]
    while pending:
        item=pending.pop()
        if isinstance(item,Node):
            if item.span.get('file')==value.file:item._generated_source=value
            pending.extend(item[1:])
        elif type(item)in (tuple,list):pending.extend(item)
