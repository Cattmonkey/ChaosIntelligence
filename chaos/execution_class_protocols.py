"""Inspectable metaclass, prepared namespace and class-local name algorithms."""
from .execution_class_state import _ClassFrame


class TypeCheckFrame(_ClassFrame):
    ROOTS=('request','klass','meta','member','function')
    def __init__(self,request):super().__init__();self.request=request;self.klass=self.meta=self.member=self.function=None
    def bind(self):
        from .objects import ChaosClass,owned,declaration,member_value,bind_member
        from .language import LanguageError,check_type
        from .class_creation import validate_value
        from .agent_tools import scope
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            self.klass=self.request.klass
            if type(self.klass)is not d['ChaosClass']or self.klass.building:raise d['LanguageError']('类型判断需要已完成的 Class',code='argument_error')
            d['validate_value'](e,self.klass);d['validate_value'](e,self.request.value)
            if self.request.operation=='subclass'and(type(self.request.value)is not d['ChaosClass']or self.request.value.building):raise d['LanguageError']('issubclass 的 child 需要已完成的 Class',code='argument_error')
            self.meta=d['owned'](self.klass.metaclass,e);self.name='__instancecheck__'if self.request.operation=='instance'else'__subclasscheck__';self.member=d['declaration'](self.meta,self.name)
            if self.member is None:raise d['LanguageError']('元类缺少 '+self.name,code='argument_error')
            self.enter_scope(e,self.klass.capabilities,self.meta.capabilities,factory=d['scope']);self.phase='function'
            return self.leaf(e._s_access_result(d['bind_member'](d['member_value'](self.member),self.member,self.klass,self.meta,e,instance_access=True)))
        if self.phase=='function':self.function=value;self.phase='result';return self.leaf(e._s_invoke(self.function,[self.request.value],synchronous=True))
        return self.done(e,d['check_type'](value,'Bool'))


class MappingMemberFrame(_ClassFrame):
    ROOTS=('namespace','name','access_request')
    def __init__(self,namespace,name):super().__init__();self.namespace,self.name=namespace,name;self.access_request=None
    def bind(self):
        from .objects import declaration,special_method
        from .language import LanguageError
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            if d['declaration'](self.namespace.klass,self.name)is None:raise d['LanguageError']('准备命名空间缺少 '+self.name,code='argument_error')
            self.access_request=d['special_method'](self.namespace,self.name,e);self.phase='access';return self.leaf(e._s_access_result(self.access_request))
        return self.done(e,value)


class NamespaceSnapshotFrame(_ClassFrame):
    ROOTS=('namespace','keys','function','getter','result_map','key','value')
    def __init__(self,namespace):
        super().__init__();self.namespace=namespace;self.keys=self.function=self.getter=self.key=self.value=None;self.result_map={};self.index=0
    def bind(self):
        from .objects import ChaosInstance,owned
        from .language import LanguageError,ResourceLimit
        from .class_creation import validate_value
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner;m=d['m']
        if error is not None:raise error
        if self.phase=='start':
            d['validate_value'](e,self.namespace)
            if type(self.namespace)is dict:
                if len(self.namespace)>10000 or any(type(k)is not str for k in self.namespace):raise d['LanguageError']('命名空间需要至多 10000 个 String 键',code='argument_error')
                return self.done(e,dict(self.namespace))
            if type(self.namespace)is not d['ChaosInstance']:raise d['LanguageError']('__prepare__ 需要 Record 或原生命名空间映射 Instance',code='argument_error')
            d['owned'](self.namespace.klass,e);self.phase='keys_function';return self.leaf(e._s_get_member(self.namespace,'keys'))
        if self.phase=='keys_function':self.function=value;self.phase='keys';return self.leaf(e._s_invoke(self.function,[],synchronous=True))
        if self.phase=='keys':
            self.keys=value
            if type(value)is not list or len(value)>10000 or any(type(k)is not str for k in value)or len(set(value))!=len(value):raise d['LanguageError']('命名空间 keys 必须是至多 10000 个唯一 String 的 List',code='argument_error')
            d['validate_value'](e,value);self.phase='getter';return self.leaf(m.mapping_member(e,self.namespace,'__getitem__'))
        if self.phase=='getter':self.getter=value;self.phase='next'
        if self.phase=='item':self.value=value;m.validate_result(e,value);self.result_map[self.key]=value;self.index+=1;self.phase='next'
        if self.index>=len(self.keys):d['validate_value'](e,self.result_map);return self.done(e,self.result_map)
        self.key=self.keys[self.index];e.tick();self.phase='item';return self.leaf(e._s_invoke(self.getter,[self.key],synchronous=True))


class NamespaceReadFrame(_ClassFrame):
    ROOTS=('namespace','key','getter','value')
    def __init__(self,namespace,key):super().__init__();self.namespace,self.key=namespace,key;self.getter=self.value=None
    def bind(self):
        from .language import LanguageError
        from .objects import ClassMember
        from .class_creation import project_field,validate_value
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:
            if self.phase=='item'and isinstance(error,d['LanguageError'])and error.code in('KeyError','key_error'):return self.done(e,(False,None))
            raise error
        if self.phase=='start':
            if type(self.namespace)is dict:
                if self.key not in self.namespace:return self.done(e,(False,None))
                self.value=self.namespace[self.key];self.phase='finish'
            else:self.phase='getter';return self.leaf(d['m'].mapping_member(e,self.namespace,'__getitem__'))
        if self.phase=='getter':self.getter=value;self.phase='item';return self.leaf(e._s_invoke(self.getter,[self.key],synchronous=True))
        if self.phase=='item':self.value=value
        d['m'].validate_result(e,self.value)
        return self.done(e,(True,d['project_field'](e,self.value,'value')if type(self.value)is d['ClassMember']else self.value))


class ClassLookupNameFrame(_ClassFrame):
    ROOTS=('env','current','namespace','name','value')
    def __init__(self,env,name):super().__init__();self.env,self.name=env,name;self.current=env;self.namespace=self.value=None
    def bind(self):
        from .language import Cell,LanguageError
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='read':
            found,self.value=value
            if found:return self.done(e,self.value)
            self.current=self.current.parent;self.phase='start'
        while self.current is not None:
            self.namespace=getattr(self.current,'_prepared_namespace',None)
            if self.namespace is not None and self.name!=getattr(self.current,'_class_name',None):self.phase='read';return self.leaf(d['m'].namespace_read(e,self.namespace,self.name))
            if self.name in self.current.values:
                self.value=self.current.values[self.name];return self.done(e,self.value.value if isinstance(self.value,d['Cell'])else self.value)
            self.current=self.current.parent
        raise d['LanguageError']('未定义名称 '+self.name)


class ClassAssignNameFrame(_ClassFrame):
    ROOTS=('env','name','value','current','cell','item','effective')
    def __init__(self,env,name,value):super().__init__();self.env,self.name,self.value=env,name,value;self.current=env;self.cell=self.item=self.effective=None
    def bind(self):
        from .language import Cell,LanguageError,check_type
        from .objects import ClassMember
        from .class_creation import member_value
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            while self.current is not None and self.name not in self.current.values:self.current=self.current.parent
            if self.current is None or not hasattr(self.current,'_prepared_namespace'):return self.done(e,self.env.assign(self.name,self.value))
            self.cell=self.current.values[self.name]
            if not isinstance(self.cell,d['Cell'])or getattr(self.cell,'_class_reference',False):raise d['LanguageError']('不可赋值给 let/函数绑定 '+self.name,code='argument_error')
            self.value=d['check_type'](self.value,self.cell.contract);self.item=d['member_value'](e,self.value,mutable=True,contract=self.cell.contract)
            self.phase='effective';return self.leaf(d['m'].namespace_write(e,self.current._prepared_namespace,self.name,self.item))
        self.effective=value;self.cell.value=value.value if type(value)is d['ClassMember']else value;return self.done(e,self.cell.value)


class NamespaceWriteFrame(_ClassFrame):
    ROOTS=('namespace','key','value','setter','getter','returned')
    def __init__(self,namespace,key,value):super().__init__();self.namespace,self.key,self.value=namespace,key,value;self.setter=self.getter=self.returned=None
    def bind(self):
        from .language import LanguageError,ResourceLimit,check_type
        from .class_creation import validate_value
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            d['validate_value'](e,self.value)
            if type(self.namespace)is dict:
                if self.key not in self.namespace and len(self.namespace)>=10000:raise d['ResourceLimit']('类成员超过 10000 项')
                self.namespace[self.key]=self.value;return self.done(e,self.value)
            self.phase='setter';return self.leaf(d['m'].mapping_member(e,self.namespace,'__setitem__'))
        if self.phase=='setter':self.setter=value;self.phase='written';return self.leaf(e._s_invoke(self.setter,[self.key,self.value],synchronous=True))
        if self.phase=='written':d['check_type'](value,'Null');self.phase='getter';return self.leaf(d['m'].mapping_member(e,self.namespace,'__getitem__'))
        if self.phase=='getter':self.getter=value;self.phase='read';return self.leaf(e._s_invoke(self.getter,[self.key],synchronous=True))
        self.returned=value;return self.done(e,d['m'].validate_result(e,value))


class OriginalBasesWriteFrame(_ClassFrame):
    ROOTS=('namespace','original','sources')
    def __init__(self,namespace,original,sources=()):super().__init__();self.namespace,self.original,self.sources=namespace,original,sources
    def bind(self):
        from .class_creation import project_value
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        if error is not None:raise error
        if self.phase=='start':
            self.original=self.dependencies['project_value'](cursor.owner,self.original,self.namespace,*self.sources);self.phase='write';return self.leaf(self.dependencies['m'].namespace_write(cursor.owner,self.namespace,'__orig_bases__',self.original))
        return self.done(cursor.owner,value)


class ClassPrepareFrame(_ClassFrame):
    ROOTS=('meta','name','bases','options','function','value','snapshot')
    def __init__(self,meta,name,bases,options):
        super().__init__();self.meta,self.name,self.bases,self.options=meta,name,bases,options;self.function=self.value=self.snapshot=None
    def bind(self):
        from .objects import ChaosInstance,ChaosClass
        from .language import LanguageError
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:
            if self.phase=='function'and isinstance(error,d['LanguageError'])and error.code=='attribute_error':self.function=None;self.phase='invoke'
            else:raise error
        elif self.phase=='function':self.function=value;self.phase='invoke'
        if self.phase=='start':
            if type(self.meta)in(d['ChaosClass'],d['ChaosInstance']):self.phase='function';return self.leaf(e._s_default_get_member(self.meta,'__prepare__'))
            self.phase='invoke'
        if self.phase=='invoke':
            if self.function is None:self.value={};self.phase='validate'
            else:self.phase='prepared';return self.leaf(e._s_invoke(self.function,[self.name,list(self.bases)],self.options,synchronous=True))
        if self.phase=='prepared':self.value=value;self.phase='validate'
        if self.phase=='validate':d['m'].validate_result(e,self.value);self.phase='snapshot';return self.leaf(d['m'].namespace_snapshot(e,self.value))
        self.snapshot=value;return self.done(e,(dict(self.snapshot)if type(self.value)is dict else self.value,self.snapshot))


class TypeNewFrame(_ClassFrame):
    ROOTS=('request','linear','options','selected','members','validated','klass','previous_members','pending','value')
    def __init__(self,request):
        super().__init__();self.request=request;self.linear=self.options=self.selected=self.members=self.klass=self.pending=self.value=None;self.validated=[];self.previous_members={}
    def bind(self):
        from .class_creation import validate_bases,validate_options,validate_name,shell,install,finalize,member_value,project_value
        from .objects import ClassMember,ChaosClass
        from .language import LanguageError,Cell
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner;m=d['m'];r=self.request
        if error is not None:raise error
        if self.phase=='start':
            if not m.is_metaclass(e,r.metaclass):raise d['LanguageError']('Type.__new__ mcls 需要原生 Type 子类',code='argument_error')
            self.linear=d['validate_bases'](e,r.bases);self.options=d['validate_options'](e,r.options)
            if 'metaclass'in self.options:raise d['LanguageError']('Type.__new__ options 不接受 metaclass',code='argument_error')
            d['validate_name'](r.name);self.selected=m.select(e,r.bases,r.metaclass)
            if self.selected is not r.metaclass:self.phase='redirect';return self.leaf(m.new_value(e,self.selected,[r.name,r.bases,r.namespace],self.options))
            self.phase='snapshot';return self.leaf(m.namespace_snapshot(e,r.namespace))
        if self.phase in('redirect','finalized'):return self.done(e,value)
        self.members=value
        for label,item in self.members.items():
            e.tick();label.encode('utf-8')
            if type(item)is d['ClassMember']:
                member=d['member_value'](e,item.value,mutable=item.mutable,method=item.method,contract=item.contract);self.validated.append((label,member.value,member.contract,member.mutable,member.method,item.capabilities))
            else:self.validated.append((label,item,'Any',False,False,None))
        for pending in reversed(m.build_stack(e)):
            shell=pending['shell']
            if not pending['used']and shell.name==r.name and shell.bases==tuple(r.bases)and shell.metaclass is self.selected:
                self.klass=shell;self.pending=pending;pending['used']=True;self.previous_members=self.klass.members;self.klass.members={};break
        if self.klass is None:self.klass=d['shell'](e,r.name,r.bases,self.linear,metaclass=self.selected)
        captured=getattr(r.namespace,'capabilities',None)
        if captured is not None:self.klass.capabilities=captured if self.klass.capabilities is None else self.klass.capabilities&captured
        for args in self.validated:
            d['install'](e,self.klass,*args);previous=self.previous_members.get(args[0]);current=self.klass.members[args[0]]
            if current.mutable and previous is not None and previous.mutable and type(previous.value)is d['Cell']:
                previous.value.value=current.value.value;previous.value.contract=current.value.contract;current.value=previous.value
        self.phase='finalized';return self.leaf(d['finalize'](e,self.klass,self.options))


class MetaclassNewValueFrame(_ClassFrame):
    ROOTS=('meta','args','options','member','function','supplied')
    def __init__(self,meta,args,options):super().__init__();self.meta,self.args,self.options=meta,args,options;self.member=self.function=self.supplied=None
    def bind(self):
        from .objects import ClassMethod,declaration,member_value,bind_member
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            self.member=d['declaration'](self.meta,'__new__')
            if d['member_value'](self.member)is d['member_value'](e.native_type.members['__new__']):
                self.phase='function';return self.leaf(e._s_access_result(d['bind_member'](d['member_value'](self.member),self.member,None,self.meta,e)))
            self.phase='function';return self.leaf(e._s_default_get_member(self.meta,'__new__'))
        if self.phase=='function':
            self.function=value;self.supplied=list(self.args)if self.member is not None and isinstance(d['member_value'](self.member),d['ClassMethod'])else[self.meta]+list(self.args)
            self.phase='result';return self.leaf(e._s_invoke(self.function,self.supplied,self.options,synchronous=True))
        return self.done(e,d['m'].validate_result(e,value))


class TypeDefaultCallFrame(_ClassFrame):
    ROOTS=('request','klass','args','options','value','initializer','function')
    def __init__(self,request):super().__init__();self.request=request;self.klass=self.args=self.options=self.value=self.initializer=self.function=None
    def bind(self):
        from .objects import ChaosClass,ChaosInstance,owned,declaration,bind_member,member_value
        from .class_creation import construct,project_value
        from .language import LanguageError,check_type
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner;m=d['m']
        if error is not None:raise error
        if self.phase=='start':
            self.klass,self.args,self.options=self.request.klass,self.request.args,self.request.options;d['owned'](self.klass,e)
            if not m.is_metaclass(e,self.klass):self.phase='constructed';return self.leaf(d['construct'](e,self.klass,self.args,self.options,e.current_span))
            if self.klass is e.native_type and len(self.args)==1 and not self.options:
                self.value=self.args[0];d['owned'](self.value,e)
                if type(self.value)is d['ChaosClass']:result=self.value.metaclass
                elif type(self.value)is d['ChaosInstance']:result=self.value.klass
                else:raise d['LanguageError']('Type(value) 目前查询 Class 或 Instance；内建标量类型身份尚未实现',code='argument_error')
                return self.done(e,d['project_value'](e,result,self.value))
            self.phase='created';return self.leaf(m.new_value(e,self.klass,self.args,self.options))
        if self.phase=='constructed':return self.done(e,value)
        if self.phase=='created':
            self.value=value
            if type(value)is d['ChaosClass']and m.subclass(value.metaclass,self.klass):
                self.initializer=d['declaration'](value.metaclass,'__init__')
                if self.initializer is not None:
                    self.phase='function';return self.leaf(e._s_access_result(d['bind_member'](d['member_value'](self.initializer),self.initializer,value,value.metaclass,e,instance_access=True)))
            self.phase='finish'
        if self.phase=='function':self.function=value;self.phase='initialized';return self.leaf(e._s_invoke(value,self.args,self.options,synchronous=True))
        if self.phase=='initialized':d['check_type'](value,'Null')
        return self.done(e,d['project_value'](e,self.value,self.klass,self.value.metaclass if type(self.value)is d['ChaosClass']else self.klass))


class ClassCallFrame(_ClassFrame):
    ROOTS=('klass','args','options','meta','member','function')
    def __init__(self,klass,args,options,span=None,synchronous=False):super().__init__();self.klass,self.args,self.options,self.span,self.synchronous=klass,args,options,span,synchronous;self.meta=self.member=self.function=None
    def bind(self):
        from .objects import declaration,bind_member,member_value,owned
        from .language import LanguageError
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            if self.klass.building:raise d['LanguageError']('类尚未完成定义',code='argument_error')
            self.meta=d['owned'](self.klass.metaclass,e);self.member=d['declaration'](self.meta,'__call__')
            if self.member is None:raise d['LanguageError']('元类缺少 __call__',code='argument_error')
            self.phase='function';return self.leaf(e._s_access_result(d['bind_member'](d['member_value'](self.member),self.member,self.klass,self.meta,e,instance_access=True)))
        if self.phase=='function':self.function=value;self.phase='invoked';return self.leaf(e._s_invoke(value,self.args,self.options,span=self.span,synchronous=self.synchronous))
        return self.done(e,value)


class MetaclassCreateFrame(_ClassFrame):
    ROOTS=('request','bases','original','linear','options','meta','namespace','value','member_value')
    def __init__(self,request):super().__init__();self.request=request;self.bases=self.original=self.linear=self.options=self.meta=self.namespace=self.value=self.member_value=None
    def bind(self):
        from .class_creation import resolve_bases,validate_options,validate_name,validate_value,project_value
        from .agent_tools import scope
        from .language import LanguageError,ResourceLimit
        from . import metaclasses as m
        return locals()
    def advance(self,cursor,value,error):
        d=self.dependencies;e=cursor.owner;m=d['m'];r=self.request
        if error is not None:raise error
        if self.phase=='start':d['validate_name'](r.name);self.phase='bases';return self.leaf(d['resolve_bases'](e,r.bases))
        if self.phase=='bases':
            self.bases,self.original,self.linear=value;self.options=d['validate_options'](e,r.options);self.explicit=self.options.pop('metaclass',m.DEFAULT);self.meta=m.select(e,self.bases,self.explicit)
            if type(r.members)is not dict or len(r.members)>10000 or any(type(k)is not str for k in r.members):raise d['LanguageError']('类 members 需要至多 10000 个 String 键 Record',code='argument_error')
            d['validate_value'](e,r.members);self.enter_scope(e,getattr(self.meta,'capabilities',None),factory=d['scope']);self.phase='prepared';return self.leaf(m.prepare(e,self.meta,r.name,self.bases,self.options))
        if self.phase=='prepared':self.namespace,_=value;self.member_iterator=iter(r.members.items());self.index=0;self.phase='members'
        if self.phase=='written':self.index+=1;self.phase='members'
        if self.phase=='members':
            try:self.label,self.member_value=next(self.member_iterator)
            except StopIteration:
                if self.original is not None:self.phase='original';return self.leaf(m.original_bases_write(e,self.namespace,self.original,self.meta))
                self.phase='invoke'
            else:self.phase='written';return self.leaf(m.namespace_write(e,self.namespace,self.label,self.member_value))
        if self.phase=='original':self.phase='invoke'
        if self.phase=='invoke':self.phase='created';return self.leaf(e._s_invoke(self.meta,[r.name,list(self.bases),self.namespace],self.options,synchronous=True))
        self.value=value;m.validate_result(e,value)
        if self.original is not None:m.original_bases_result(e,value)
        return self.done(e,d['project_value'](e,self.value,self.meta,self.namespace))
