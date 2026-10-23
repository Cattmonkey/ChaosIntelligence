"""Language-native closed module instances; original AST and logical scheduler."""
from dataclasses import dataclass
import hashlib,json,re
from .language import Env,Node,LanguageError,ResourceLimit,Parser

@dataclass(frozen=True)
class Request:
    program:object
    bindings:tuple

def request(evaluator,program,bindings=None):
    from .program_values import owned
    from .class_creation import validate_value
    owned(program,evaluator)
    if type(bindings) is not dict or len(bindings)>256:
        raise LanguageError('module_from bindings 需要至多256项 Record',code='module_error')
    associations=dict(bindings)
    for name,value in associations.items():
        evaluator.tick()
        if type(name) is not str or len(name)>240 or re.fullmatch(r'[^\W\d]\w*',name,re.UNICODE) is None or name in Parser.reserved:
            raise LanguageError('module_from bindings 名称无效',code='module_error')
        validate_value(evaluator,value)
    return Request(program,tuple(associations.items()))

class Scope:
    def __init__(self,evaluator,value):
        from .program_values import owned
        from .class_creation import validate_value
        from .modules import ProgramBundle
        owned(value.program,evaluator)
        for _,item in value.bindings:validate_value(evaluator,item)
        definition=value.program.definition
        self.evaluator=evaluator
        self.bundle=ProgramBundle(definition['files'][definition['entry']],definition['files'],definition['entry'],language_versions=definition['languageVersions'],checkpoint=evaluator.tick)
        self.definition=definition
        self.bindings=value.bindings
        self.cache={}
        self.loading=set()
        prefix='module/'+value.program.definition_hash+'/'
        self.virtual={name:prefix+hashlib.sha256(name.encode()).hexdigest()+'.chaos' for name in self.bundle.trees}
        context={'entry':self.bundle.entry,'virtualFiles':{name:self.virtual[name] for name in self.bundle.files},'languageVersions':dict(self.bundle.language_versions)}
        context_json=json.dumps(context,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        from .modules import STDLIB
        from .program import parse_program
        from .generated_sources import Source,register_batch
        sources={}
        for name,file in self.virtual.items():
            evaluator.tick();text=STDLIB[name] if name in STDLIB else self.bundle.files[name]
            version=self.bundle.language_versions.get(name,2)
            ir=parse_program(text,file,language_version=version)
            sources[name]=Source(file,text,version,hashlib.sha256(text.encode()).hexdigest(),ir['semanticHash'],context_json if name in self.bundle.files else None)
        # Validate the whole source batch before publishing any registry entry.
        register_batch(evaluator,tuple(sources.values()))
        self.sources=sources
        for name,tree in self.bundle.expanded_trees.items():
            pending=[tree]
            while pending:
                evaluator.tick();item=pending.pop()
                if isinstance(item,Node):
                    logical=item.span.get('file')
                    if logical in self.virtual:
                        item.span=dict(item.span,file=self.virtual[logical]);item._generated_source=sources[logical]
                    item._module_scope=self;item._module_file=name
                    pending.extend(item[1:])
                elif type(item) in (tuple,list):pending.extend(item)

    def load(self,name):
        from .execution_modules import module_cursor
        from .execution_frames import Done
        cursor = module_cursor(self.evaluator, 'load', self, name)
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

    def imported(self,node):
        from .execution_modules import module_cursor
        from .execution_frames import Done
        cursor = module_cursor(self.evaluator, 'imported', self, node)
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

def construct(evaluator,value):
    from .execution_modules import module_cursor
    from .execution_frames import Done
    cursor = module_cursor(evaluator, 'construct', value)
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

def builtins(evaluator):
    return {'module_from':lambda program,bindings={}:request(evaluator,program,bindings)}
