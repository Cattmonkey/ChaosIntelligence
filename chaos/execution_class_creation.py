"""Authoritative class creation/declaration state on the shared execution cursor.

Public protocol calls retain their gateway and late binding; this module owns
algorithm phases rather than a generator containing the whole old algorithm.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class ClassFrameRequest:
    operation: str
    values: tuple


def class_protocol(evaluator,operation,*values):
    from .execution_frames import request_proxy,ensure_cursor
    proxy=request_proxy(ClassFrameRequest(operation,values))
    if getattr(evaluator,'_execution_cursor',None)is not None:return proxy
    return ensure_cursor(evaluator,proxy,observer=True)


from .execution_class_state import _ClassFrame


class ClassResolveBasesFrame(_ClassFrame):
    ROOTS=('bases','original','resolved','base','function','entries','linear')
    def __init__(self,bases):
        super().__init__();self.bases=bases;self.original=self.base=self.function=self.entries=self.linear=None;self.resolved=[];self.index=0;self.changed=False
    def bind(self):
        from .objects import ChaosClass
        from .language import LanguageError,TupleValue
        from . import class_creation as c
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner;c=d['c'];Class=d['ChaosClass'];Error=d['LanguageError']
        if error is not None:raise error
        if self.phase=='start':
            if type(self.bases)is not list or len(self.bases)>32:raise Error('类基类需要至多 32 项 List',code='argument_error')
            for index,base in enumerate(self.bases):c.preflight_base(e,base,self.bases[:index])
            self.original=d['TupleValue'](self.bases);self.phase='next'
        if self.phase=='function':self.function=value;self.phase='entries';return self.leaf(e._s_invoke(self.function,[self.original],synchronous=True))
        if self.phase=='entries':
            self.entries=value
            if type(value)is not d['TupleValue']:raise Error('__mro_entries__ 必须同步返回 Class 的 Tuple',code='argument_error')
            c.validate_value(e,value)
            if any(type(item)is not Class or item.building for item in value):raise Error('__mro_entries__ 只展开一层已完成的 Class',code='argument_error')
            self.resolved.extend(value);self.phase='after'
        while True:
            if self.phase=='after':
                if len(self.resolved)>32:raise Error('展开后的基类需要至多 32 项',code='argument_error')
                self.index+=1;self.phase='next'
            if self.index>=len(self.bases):
                self.linear=c.validate_bases(e,self.resolved);return self.done(e,(self.resolved,self.original if self.changed else None,self.linear))
            self.base=self.bases[self.index]
            if type(self.base)is Class:self.resolved.append(self.base);self.phase='after';continue
            self.changed=True;self.phase='function';return self.leaf(e._s_default_get_member(self.base,'__mro_entries__'))


class ClassFinalizeFrame(_ClassFrame):
    ROOTS=('klass','options')
    def __init__(self,klass,options):super().__init__();self.klass,self.options=klass,options
    def bind(self):
        from .agent_tools import scope
        return locals()
    def advance(self,cursor,value,error):
        from . import class_creation as c
        if error is not None:raise error
        if self.phase=='start':
            self.enter_scope(cursor.owner,self.klass.capabilities,factory=self.dependencies['scope']);self.phase='body';return self.leaf(c._finalize(cursor.owner,self.klass,self.options))
        return self.done(cursor.owner,value)


class ClassFinalizeBodyFrame(_ClassFrame):
    ROOTS=('klass','options','notifications','value','inherited','hook')
    def __init__(self,klass,options):
        super().__init__();self.klass,self.options=klass,options;self.notifications=[];self.index=0;self.value=self.inherited=self.hook=None
    def bind(self):
        from .objects import descriptor_protocols,ProtocolCall,member_value,declaration,bind_member
        from .language import LanguageError,check_type
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            self.klass.building=False;self.notifications=[(label,d['member_value'](member))for label,member in self.klass.members.items()];self.phase='next'
        if self.phase=='notify':self.index+=1;self.phase='next'
        if self.phase=='hook':self.hook=value;self.phase='invoke';return self.leaf(e._s_invoke(self.hook,[],self.options,synchronous=True))
        if self.phase=='invoke':d['check_type'](value,'Null');return self.done(e,self.klass)
        while self.index<len(self.notifications):
            self.label,self.value=self.notifications[self.index];e.tick()
            if '__set_name__'in d['descriptor_protocols'](self.value):
                self.phase='notify';return self.leaf(e._s_access_result(d['ProtocolCall'](self.value,'__set_name__',[self.klass,self.label],True)))
            self.index+=1
        self.inherited=d['declaration'](self.klass,'__init_subclass__',after=self.klass)
        if self.inherited is None:
            if self.options:raise d['LanguageError']('终端 __init_subclass__ 不接受类 options',code='argument_error')
            return self.done(e,self.klass)
        self.phase='hook';return self.leaf(e._s_access_result(d['bind_member'](d['member_value'](self.inherited),self.inherited,self.klass,self.klass,e)))


class ClassConstructFrame(_ClassFrame):
    ROOTS=('klass','args','kwargs','creator','function','supplied','value','initializer')
    def __init__(self,klass,args,kwargs,span):
        super().__init__();self.klass,self.args,self.kwargs,self.span=klass,args,kwargs,span;self.creator=self.function=self.supplied=self.value=self.initializer=None
    def bind(self):
        from .objects import ChaosInstance,ClassMethod,declaration,member_value,owned,bind_member
        from .language import LanguageError,Function,Native,check_type
        from .suspension import Task,Generator
        from . import class_creation as c
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner;c=d['c']
        if error is not None:raise error
        if self.phase=='start':
            if self.klass.building:raise d['LanguageError']('类尚未完成定义')
            self.creator=d['declaration'](self.klass,'__new__')
            if self.creator is None:self.value=c.allocate(e,self.klass);self.phase='initialize'
            else:self.phase='creator';return self.leaf(e._s_default_get_member(self.klass,'__new__'))
        if self.phase=='creator':
            self.function=value;self.supplied=list(self.args)if isinstance(d['member_value'](self.creator),d['ClassMethod'])else[self.klass]+list(self.args)
            self.phase='created';return self.leaf(e._s_invoke(self.function,self.supplied,self.kwargs,span=self.span,synchronous=True))
        if self.phase=='created':
            self.value=value
            if isinstance(value,(d['Task'],d['Generator'])):raise d['LanguageError']('__new__ 不能返回 Task 或 Generator',code='argument_error')
            c.validate_value(e,value);self.phase='initialize'
        if self.phase=='init_function':self.function=value;self.phase='initialized';return self.leaf(e._s_invoke(value,self.args,self.kwargs,span=self.span,synchronous=True))
        if self.phase=='initialized':d['check_type'](value,'Null');self.phase='finish'
        if self.phase=='initialize':
            self.phase='finish'
            if isinstance(self.value,d['ChaosInstance'])and any(base is self.klass for base in self.value.klass.mro):
                d['owned'](self.value.klass,e);self.initializer=d['declaration'](self.value.klass,'__init__')
                if self.initializer is None:
                    if self.creator is None and(self.args or self.kwargs):raise d['LanguageError'](self.klass.name+' 构造不接受参数',code='argument_error')
                else:
                    if not self.initializer.method or not(type(self.initializer.value)is d['Native']or type(self.initializer.value)is d['Function']and self.initializer.value.flavor=='fn'):raise d['LanguageError']('__init__ 必须是同步 fn 或原生方法')
                    self.phase='init_function';return self.leaf(e._s_default_get_member(self.value,'__init__'))
        return self.done(e,c.project_value(e,self.value,self.klass,self.value.klass if type(self.value)is d['ChaosInstance']else self.klass)if self.creator is not None else self.value)


class DecoratedDeclarationFrame(_ClassFrame):
    ROOTS=('node','env','method_closure','decorators','value','decorator')
    def __init__(self,node,env,method_closure=None):
        super().__init__();self.node,self.env,self.method_closure=node,env,method_closure;self.decorators=[];self.value=self.decorator=None;self.index=0
    def advance(self,cursor,value,error):
        from . import language as l
        e=cursor.owner
        if error is not None:raise error
        if self.phase=='expression':self.decorators.append(value);self.index+=1;self.phase='start'
        if self.phase=='start':
            if self.index<len(self.node[1]):self.phase='expression';return self.leaf(e._s_evaluate(self.node[1][self.index],self.env))
            self.declaration=self.node[2]
            if self.declaration[0]=='def':self.phase='function';return self.leaf(e._s_evaluate(self.declaration[2],self.env))
            if self.declaration[0]=='class':self.phase='declaration';return self.leaf(e._s_class(self.declaration,self.env,publish=False))
            self.phase='agent';return self.leaf(e._s_evaluate(self.declaration[2],self.env))
        if self.phase in('function','declaration','agent'):
            if self.phase=='function':
                if self.method_closure is None:value.env.values[self.declaration[1]]=value
                else:value.env=self.method_closure.snapshot()
            elif self.phase=='agent':value=l.Agent(self.declaration[1],value,e)
            self.value=value;self.index=len(self.decorators)-1;self.phase='decorate'
        if self.phase=='decorated':self.value=value;self.index-=1;self.phase='decorate'
        if self.index<0:return self.done(e,self.value)
        self.decorator=self.decorators[self.index];self.phase='decorated';return self.leaf(e._s_invoke(self.decorator,[self.value]))


class ClassDeclarationFrame(_ClassFrame):
    ROOTS=('node','env','declarations','bases','original_bases','linear','options','meta','namespace','seeded','klass','cell','local','closure','pending','member','previous','method','item','effective','value','contract')
    def __init__(self,node,env,publish=True):
        super().__init__();self.node,self.env,self.publish=node,env,publish;self.bases=[];self.options={};self.index=0;self.pending=None
        self.original_bases=self.linear=self.meta=self.namespace=self.seeded=self.klass=self.cell=self.local=self.closure=self.member=self.previous=self.method=self.item=self.effective=self.value=None;self.value_bound=False
    def bind(self):
        from .objects import ChaosClass,Member,Property,member_value,property_update,c3_merge,owned
        from .class_creation import class_options,validate_options,validate_name,shell,install,member_value as create_member,preflight_base,resolve_bases
        from .metaclasses import DEFAULT,select,prepare,namespace_write,build_stack,validate_result,is_metaclass,original_bases_write,original_bases_result
        from .agent_tools import scope as authority_scope
        from . import language as l
        return locals()
    def suppressed(self,e):
        if not self.value_bound:
            def unbound_value():
                if False:value=None
                return value
            return unbound_value()
        return self.done(e,self.env.bind(self.name,self.value)if self.publish else self.value)
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner;l=d['l']
        if error is not None:raise error
        if self.phase=='start':
            self.name,self.declarations=self.node[1],self.node[3][1]
            if self.name in self.env.values:raise l.LanguageError('不可重复绑定 '+self.name)
            if len(self.node[2])>32 or len(self.declarations)>10000:raise l.ResourceLimit('类基类或成员数量超过限制')
            self.declared=set()
            for member in self.declarations:
                label=member[2][1]if member[0]=='decorated'else member[1]
                if label in self.declared and member[0]!='decorated':raise l.LanguageError('类成员不能重名')
                self.declared.add(label)
            self.phase='bases'
        if self.phase=='base':d['preflight_base'](e,value,self.bases);self.bases.append(value);self.index+=1;self.phase='bases'
        if self.phase=='bases':
            if self.index<len(self.node[2]):self.phase='base';return self.leaf(e._s_evaluate(self.node[2][self.index],self.env))
            self.phase='resolved';return self.leaf(d['resolve_bases'](e,self.bases))
        if self.phase=='resolved':
            self.bases,self.original_bases,self.linear=value;self.arguments=d['class_options'](self.node);self.index=0;self.phase='options'
        if self.phase=='option':
            argument=self.argument
            if argument[0]=='keyword':
                if argument[1]in self.options:raise l.LanguageError('重复类选项 '+argument[1],code='argument_error')
                self.value=value;self.value_bound=True;self.options[argument[1]]=value
            else:
                if type(value)is not dict or any(type(key)is not str for key in value):raise l.LanguageError('类 ** 选项需要 String 键 Record',code='argument_error')
                if self.options.keys()&value.keys():raise l.LanguageError('重复展开类选项',code='argument_error')
                if len(self.options)+len(value)>10000:raise l.ResourceLimit('类选项超过 10000 项')
                self.options.update(value)
            self.index+=1;self.phase='options'
        if self.phase=='options':
            if self.index<len(self.arguments):
                self.argument=self.arguments[self.index]
                if self.argument[0]not in('keyword','kwspread'):raise l.LanguageError('类选项只支持具名参数和 ** 展开',code='argument_error')
                self.phase='option';return self.leaf(e._s_evaluate(self.argument[2]if self.argument[0]=='keyword'else self.argument[1],self.env))
            self.options=d['validate_options'](e,self.options);d['validate_name'](self.name);self.explicit=self.options.pop('metaclass',d['DEFAULT']);self.meta=d['select'](e,self.bases,self.explicit)
            self.enter_scope(e,getattr(self.meta,'capabilities',None),factory=d['authority_scope']);self.phase='prepared';return self.leaf(d['prepare'](e,self.meta,self.name,self.bases,self.options))
        if self.phase=='prepared':
            self.namespace,self.seeded=value;self.klass=d['shell'](e,self.name,self.bases,self.linear,metaclass=self.meta if d['is_metaclass'](e,self.meta)else e.native_type)
            self.cell=l.Cell(self.klass,'Any');self.cell._class_reference=True;self.local=l.Env(self.env.snapshot());self.local.values[self.name]=self.cell
            self.local._prepared_namespace=self.namespace;self.local._class_name=self.name;self.closure=self.env.snapshot();self.closure.values[self.name]=self.cell
            for label,seed in self.seeded.items():
                self.value=seed;self.value_bound=True
                from .objects import ClassMember
                stored=d['install'](e,self.klass,label,seed.value,seed.contract,seed.mutable,seed.method,seed.capabilities)if type(seed)is ClassMember else d['install'](e,self.klass,label,seed)
                self.local.values[label]=stored
            self.pending={'shell':self.klass,'used':False};self.stack=d['build_stack'](e);self.stack.append(self.pending);self.stack_entered=True;self.index=0;self.phase='members'
        if self.phase=='method':
            self.method=value;self.method.env=self.closure.snapshot();self.phase='method_ready'
        elif self.phase=='decorated_method':self.method=value;self.phase='method_ready'
        if self.phase=='method_ready':
            d['owned'](self.method,e)
            if self.previous is not None and self.label not in self.seeded and not(isinstance(self.method,d['Property'])and d['property_update'](self.method,d['member_value'](self.previous))):raise l.LanguageError('同名属性更新必须继承原 Property')
            binds=isinstance(self.method,l.Function)
            if binds and(not self.method.params or self.method.params[0][2]not in('positional','positional_only')):raise l.LanguageError('方法需要首个位置接收者参数')
            self.item=d['create_member'](e,self.method,contract='Function'if binds else'Any',method=binds);self.phase='write'
        if self.phase=='member_value':self.value=l.check_type(value,self.contract);self.value_bound=True;self.item=d['create_member'](e,self.value,contract=self.contract,mutable=self.member[0]=='var');self.phase='write'
        if self.phase=='write':self.phase='effective';return self.leaf(d['namespace_write'](e,self.namespace,self.label,self.item))
        if self.phase=='effective':
            from .objects import ClassMember
            self.effective=value
            stored=d['install'](e,self.klass,self.label,value.value,value.contract,value.mutable,value.method,value.capabilities)if type(value)is ClassMember else d['install'](e,self.klass,self.label,value)
            self.local.values[self.label]=stored;self.seeded.pop(self.label,None);self.index+=1;self.phase='members'
        if self.phase=='members':
            if self.index<len(self.declarations):
                self.member=self.declarations[self.index];self.original=self.member[2]if self.member[0]=='decorated'else self.member;self.label=self.original[1];self.previous=self.klass.members.get(self.label)
                if self.previous is not None and self.label in self.declared and self.label not in self.seeded and not(self.member[0]=='decorated'and isinstance(d['member_value'](self.previous),d['Property'])):raise l.LanguageError('类成员不能重名')
                if self.original[0]=='def':
                    if self.member[0]=='decorated':self.phase='decorated_method';return self.leaf(e._s_decorated(self.member,self.local,self.closure))
                    self.phase='method';return self.leaf(e._s_evaluate(self.original[2],self.local))
                self.contract=e.contract(self.member[2],self.local);self.phase='member_value';return self.leaf(e._s_evaluate(self.member[3],self.local))
            if self.original_bases is not None:self.phase='original';return self.leaf(d['original_bases_write'](e,self.namespace,self.original_bases,self.meta,self.klass))
            self.phase='invoke'
        if self.phase=='original':self.phase='invoke'
        if self.phase=='invoke':self.phase='created';return self.leaf(e._s_invoke(self.meta,[self.name,self.bases,self.namespace],self.options,synchronous=True))
        self.value=value;self.value_bound=True;d['validate_result'](e,value)
        if self.original_bases is not None:d['original_bases_result'](e,self.value)
        from .class_creation import project_value
        self.value=project_value(e,self.value,self.meta,self.namespace);self.cell.value=self.value
        self.retire(e);return self.done(e,self.env.bind(self.name,self.value)if self.publish else self.value)


class ClassSyntaxEvaluationFrame(_ClassFrame):
    def step(self,cursor,value,error):
        if getattr(self,'_flow_boundary',None) is not None:
            from .compiled_flow_access import step
            return step(self,cursor,value,error,super().step)
        return super().step(cursor,value,error)
    def evaluate_assignment(self,e):
        if getattr(self,'_flow_boundary',None) is not None:
            from .compiled_flow_access import evaluate
            return evaluate(self,self.node[2],regions=True)
        return self.leaf(e._s_evaluate(self.node[2],self.env,_compiled_regions=self.compiled_regions))
    KINDS={'name','assign','class','decorated'}
    ROOTS=('node','env','value')
    def __init__(self,node,env,compiled_regions=None):super().__init__();self.node,self.env,self.compiled_regions=node,env,compiled_regions;self.value=None
    def bind(self):
        from .suspension import Await,Yield,Generator,AsyncGenerator,GeneratorClose
        from .capture_frames import active as capture_active,reset as capture_reset,retain as capture_retain
        return locals()
    def advance(self,cursor,value,error):
        from . import language as l
        e=cursor.owner;k=self.node[0]
        if error is not None:raise error
        if self.phase=='start':
            self.dependencies['capture_active'](e)
            e.tick()
            if k=='name':
                from .numeric_values import captured_constructor
                capture=captured_constructor(getattr(self.node,'numeric_capture',None))
                if capture is not None:return self.done(e,e.numeric_constructors[capture])
                from .condition_values import captured_constructor as condition_capture
                if condition_capture(getattr(self.node,'condition_capture',None)):return self.done(e,e.condition_constructor)
                from .metaclasses import lookup_name
                self.phase='finish';return self.leaf(lookup_name(e,self.env,self.node[1]))
            if k=='class':self.phase='finish';return self.leaf(e._s_class(self.node,self.env))
            if k=='decorated':
                self.name=self.node[2][1]
                if self.name in self.env.values:raise l.LanguageError('不可重复绑定 '+self.name)
                self.phase='bind';return self.leaf(e._s_decorated(self.node,self.env))
            if k!='assign':raise l.LanguageError('Unknown class syntax operation',code='runtime_error')
            from .metaclasses import assign_name
            self.assign_name=assign_name;self.phase='assign';return self.evaluate_assignment(e)
        if self.phase=='assign':self.value=value;self.phase='finish';return self.leaf(self.assign_name(e,self.env,self.node[1],self.value))
        return self.done(e,self.env.bind(self.name,value)if self.phase=='bind'else value)


# Aggregate entry remains acyclic even for direct protocol-module imports.
from .execution_class_protocols import (TypeCheckFrame,MappingMemberFrame,
    NamespaceSnapshotFrame,NamespaceReadFrame,ClassLookupNameFrame,
    ClassAssignNameFrame,NamespaceWriteFrame,OriginalBasesWriteFrame,
    ClassPrepareFrame,TypeNewFrame,MetaclassNewValueFrame,TypeDefaultCallFrame,
    ClassCallFrame,MetaclassCreateFrame)

CLASS_FRAMES=(ClassResolveBasesFrame,ClassFinalizeFrame,ClassFinalizeBodyFrame,
    ClassConstructFrame,DecoratedDeclarationFrame,ClassDeclarationFrame,
    ClassSyntaxEvaluationFrame,TypeCheckFrame,MappingMemberFrame,
    NamespaceSnapshotFrame,NamespaceReadFrame,ClassLookupNameFrame,
    ClassAssignNameFrame,NamespaceWriteFrame,OriginalBasesWriteFrame,
    ClassPrepareFrame,TypeNewFrame,MetaclassNewValueFrame,TypeDefaultCallFrame,
    ClassCallFrame,MetaclassCreateFrame)

_CLASS_OPERATIONS={
    'resolve_bases':('ClassResolveBasesFrame',1),'finalize':('ClassFinalizeFrame',2),
    'finalize_body':('ClassFinalizeBodyFrame',2),'construct':('ClassConstructFrame',4),
    'decorated':('DecoratedDeclarationFrame',3),'declaration':('ClassDeclarationFrame',3),
    'syntax':('ClassSyntaxEvaluationFrame',3),'compiled_assign':('ClassSyntaxEvaluationFrame',1),'type_check':('TypeCheckFrame',1),
    'mapping_member':('MappingMemberFrame',2),'namespace_snapshot':('NamespaceSnapshotFrame',1),
    'namespace_read':('NamespaceReadFrame',2),'lookup_name':('ClassLookupNameFrame',2),
    'assign_name':('ClassAssignNameFrame',3),'namespace_write':('NamespaceWriteFrame',3),
    'original_bases_write':('OriginalBasesWriteFrame',3),'prepare':('ClassPrepareFrame',4),
    'lowlevel_new':('TypeNewFrame',1),'new_value':('MetaclassNewValueFrame',3),
    'default_call':('TypeDefaultCallFrame',1),'class_call':('ClassCallFrame',5),
    'metaclass_create':('MetaclassCreateFrame',1)}


def _request_values(request):
    from .language import LanguageError
    if type(request.operation)is not str or request.operation not in _CLASS_OPERATIONS or type(request.values)is not tuple or len(request.values)!=_CLASS_OPERATIONS[request.operation][1]:
        raise LanguageError('Malformed class execution request',code='ownership_error')
    return request.values


def class_frame(request):
    if type(request)is not ClassFrameRequest:return None
    values=_request_values(request)
    if request.operation=='compiled_assign':
        from .compiled_flow_access import assignment_frame
        return assignment_frame(*values)
    return globals()[_CLASS_OPERATIONS[request.operation][0]](*values)


def class_carrier_roots(evaluator,value):
    """Only extra supplementary edges; preserve existing Env charged roots."""
    from .language import Env,LanguageError
    from .capture_frames import Edge
    if type(value)is ClassFrameRequest:
        values=_request_values(value)
        return (Edge(evaluator,None,tuple(child for item in values for child in(item if type(item)in(tuple,list)else(item,)))),)
    if type(value)is Env:
        namespace=getattr(value,'_prepared_namespace',None)
        # lookup_name treats an explicit None exactly like an absent namespace.
        if namespace is None:return ()
        if hasattr(value,'_class_name')and type(value._class_name)is not str:
            raise LanguageError('Malformed prepared class name',code='ownership_error')
        from .objects import ChaosInstance
        if type(namespace)not in(dict,ChaosInstance):raise LanguageError('Malformed prepared class namespace',code='ownership_error')
        return (Edge(evaluator,None,(namespace,)),)
    return None
