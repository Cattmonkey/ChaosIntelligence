"""Shared class-frame lifecycle and exact explicit root schema (no algorithms)."""
class _ClassFrame:
    capabilities=None
    ROOTS=()
    def __init__(self):self.phase='start';self.result=None;self.dependencies=None
    def bind(self):return {}
    def roots(self):
        from .capture_frames import Edge
        from .conditions import intersection
        from .language import LanguageError
        runner=getattr(self,'authority_runner',None)
        if runner is not None:
            from .suspension import Runner
            from .execution_frames import ExecutionCursor
            if type(runner)is not Runner or type(runner.iterator)is not ExecutionCursor or runner.iterator.owner is not self.owner or self.authority_epoch!=self.owner.suspension.epoch:
                raise LanguageError('Class restoration runner cannot cross owner or epoch',code='ownership_error')
        if hasattr(self,'index')and type(self.index)is not int:raise LanguageError('Malformed class loop position',code='ownership_error')
        if hasattr(self,'declared')and(type(self.declared)is not set or any(type(item)is not str for item in self.declared)):raise LanguageError('Malformed class declaration names',code='ownership_error')
        if type(self.phase)is not str:raise LanguageError('Malformed class phase',code='ownership_error')
        for name in ('publish','value_bound','changed','authority_entered','stack_entered'):
            if hasattr(self,name)and type(getattr(self,name))is not bool:raise LanguageError('Malformed class lifecycle flag',code='ownership_error')
        if hasattr(self,'authority_epoch')and type(self.authority_epoch)is not int:raise LanguageError('Malformed class authority epoch',code='ownership_error')
        active=getattr(self,'active_caps',None)
        if active is not None and(type(active)is not frozenset or any(type(item)is not str for item in active)):raise LanguageError('Malformed active class authority',code='ownership_error')
        request=getattr(self,'request',None)
        if request is not None:
            from .class_creation import ClassRequest
            from .metaclasses import TypeNewRequest,TypeCheckRequest
            if type(request)in(ClassRequest,TypeNewRequest)and type(request.name)is not str:raise LanguageError('Malformed pending class name',code='ownership_error')
            if type(request)is TypeCheckRequest and type(request.operation)is not str:raise LanguageError('Malformed pending type-check operation',code='ownership_error')
        previous=getattr(self,'previous_authority',None)
        if previous is not None and(type(previous)is not frozenset or any(type(item)is not str for item in previous)):raise LanguageError('Malformed class restoration authority',code='ownership_error')
        pending=getattr(self,'pending',None)
        if pending is not None and(type(pending)is not dict or len(pending)!=2 or any(type(key)is not str for key in pending)or 'shell'not in pending or 'used'not in pending or type(pending['used'])is not bool):raise LanguageError('Malformed class build entry',code='ownership_error')
        result=[]
        for name in self.ROOTS+('result','previous_authority'):
            value=getattr(self,name,None)
            # Exact host tuples are operation vectors, not language Tuples.
            if name in ('args','supplied','sources','result','linear')and type(value)is tuple:values=tuple(value)
            elif name in ('notifications','validated'):
                if type(value)is not list:raise LanguageError('Malformed class row vector',code='ownership_error')
                size=2 if name=='notifications'else 6
                if any(type(row)is not tuple or len(row)!=size or type(row[0])is not str for row in value):raise LanguageError('Malformed class execution row',code='ownership_error')
                if name=='validated':
                    for row in value:
                        if type(row[3])is not bool or type(row[4])is not bool or row[5]is not None and(type(row[5])is not frozenset or any(type(item)is not str for item in row[5])):raise LanguageError('Malformed class row metadata',code='ownership_error')
                values=tuple(child for row in value for child in row)
            else:values=(value,)
            saved=('previous_authority','node','env','method_closure','request','args','kwargs','local','closure','bases','options','original_bases','original')
            caps=self.capabilities if name in saved else intersection(self.capabilities,getattr(self,'active_caps',None))
            result.append(Edge(self.owner,caps,values))
        return tuple(result)
    def enter_scope(self,e,*ceilings,factory=None):
        # Use the original bound contextmanager, including host extension hooks.
        if factory is None:
            from .agent_tools import scope as factory
        self.authority_runner=e.suspension.current;self.authority_epoch=e.suspension.epoch
        self.previous_authority=getattr(self.authority_runner,'capabilities',None)
        self.scope_managers=[];self.authority_entered=True
        for ceiling in ceilings:
            manager=factory(e,ceiling);manager.__enter__();self.scope_managers.append(manager)
        self.active_caps=getattr(self.authority_runner,'capabilities',None)
    def retire(self,e,error=None):
        failure=error
        if getattr(self,'stack_entered',False):
            self.stack_entered=False
            try:
                from .language import LanguageError
                if type(self.stack)is not list or not self.stack or self.stack[-1]is not self.pending:raise LanguageError('类创建上下文失配',code='runtime_error')
                self.stack.pop()
            except BaseException as cause:failure=cause
        if getattr(self,'authority_entered',False):
            self.authority_entered=False
            for manager in reversed(self.scope_managers):
                try:
                    if manager.__exit__(type(failure)if failure is not None else None,failure,getattr(failure,'__traceback__',None)):failure=None
                except BaseException as cause:failure=cause
        if failure is not None and failure is not error:raise failure
        return error is not None and failure is None
    def done(self,e,value):
        from .execution_frames import Done
        self.result=value;self.retire(e);self.phase='done';return Done(value)
    def suppressed(self,e):return self.done(e,None)
    def step(self,cursor,value,error):
        try:
            if self.dependencies is None:
                if error is not None:raise error
                self.dependencies=self.bind();self.dependencies.pop('self',None)
            return self.advance(cursor,value,error)
        except BaseException as failure:
            if self.retire(cursor.owner,failure):return self.suppressed(cursor.owner)
            raise
    def close(self,cursor):
        if self.retire(cursor.owner,GeneratorExit()):self.suppressed(cursor.owner)
    def leaf(self,iterator):
        from .execution_frames import Push,ProtocolBridge
        bridge=ProtocolBridge(iterator);bridge.supplementary=True;return Push(bridge)


