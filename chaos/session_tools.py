"""Live language tools on persistent Sessions; only public contracts are stored."""
import json
import threading
import uuid
from dataclasses import dataclass
from .tool_declarations import native_tool
from .language import LanguageError,ResourceLimit,Agent,check_type
from .extensions import json_copy,ToolSpec,valid_name,callback_capabilities_allowed
from .program_values import canonical_hash
from .agent_tools import AgentTool,tool_info,bind as bind_agent,current_caps,access,scope,invoke_tool
from .session_values import owned as session_owned,turn_request,accept_turn


def contract_binding(definition_hash,tools):
    value={'kind':'language','definitionHash':definition_hash,'tools':sorted(tools,key=lambda item:item['name'])}
    return {**value,'bindingHash':canonical_hash(value)}


def validate_binding(value,agent,definition_hash):
    value=json_copy(value,max_chars=40000)
    if type(value)is not dict or set(value)!={'kind','definitionHash','tools','bindingHash'} or value['kind']!='language' or value['definitionHash']!=definition_hash:
        raise LanguageError('Invalid Session tool binding identity',code='session_error')
    tools=value['tools'];requested=set(agent['config'].get('tools',[]));names=set()
    if type(tools)is not list or len(tools)>128:raise LanguageError('Session supports up to 128 language tools',code='session_error')
    for tool in tools:
        if type(tool)is not dict or set(tool)!={'name','description','inputType','outputType','capabilities'}:
            raise LanguageError('Invalid Session tool contract',code='session_error')
        ToolSpec(tool['name'],lambda x:x,tool['description'],input_type=tool['inputType'],output_type=tool['outputType'])
        caps=tool['capabilities']
        if type(caps)is not list or len(caps)>128 or any(type(x)is not str for x in caps) or len(set(caps))!=len(caps):
            raise LanguageError('Invalid Session tool capabilities',code='session_error')
        for name in caps:valid_name(name)
        if caps!=sorted(caps) or not callback_capabilities_allowed(caps,requested) or tool['name']in names or tool['name']in requested:
            raise LanguageError('Session tool contract exceeds Agent authority or duplicates a tool',code='capability_error')
        names.add(tool['name'])
    if value!=contract_binding(definition_hash,tools):raise LanguageError('Noncanonical Session tool binding',code='session_error')
    # json_copy bounds string totals; also bound repeated metadata and JSON syntax.
    if len(json.dumps(value,ensure_ascii=False,separators=(',',':')))>40000:raise ResourceLimit('Session tool contracts exceed 40000 characters')
    return value


@dataclass(frozen=True,eq=False)
class BoundSession:
    owner: object
    epoch: int
    session: object
    tools: tuple
    binding_json: str
    capabilities: object

    @property
    def binding(self):return json.loads(self.binding_json)


def owned(value,evaluator):
    if type(value)is not BoundSession or value.owner is not evaluator or value.epoch!=evaluator.suspension.epoch:
        raise LanguageError('Session bindings cannot cross evaluators or epochs',code='ownership_error')
    session_owned(value.session,evaluator)
    return value


def binding_info(value):
    binding=value.binding;snapshot=value.session.snapshot
    return {'session':{key:snapshot[key] for key in ('key','revision','definitionHash')},
            'tools':binding['tools'],'bindingHash':binding['bindingHash']}


def bind(evaluator,value,tools):
    handle=session_owned(value,evaluator);definition=handle.snapshot['agent']
    bound=bind_agent(evaluator,Agent(definition['name'],definition['config'],evaluator),tools)
    binding=validate_binding(contract_binding(handle.snapshot['definitionHash'],[tool_info(t)for t in bound.tools]),definition,handle.snapshot['definitionHash'])
    return BoundSession(evaluator,evaluator.suspension.epoch,handle,tuple(sorted(bound.tools,key=lambda t:t.name)),
                        json.dumps(binding,ensure_ascii=False,sort_keys=True,separators=(',',':')),current_caps(evaluator))


def call(evaluator,value,input,message_id=None):
    owned(value,evaluator)
    request=turn_request(evaluator,value.session,input,message_id)
    with scope(evaluator,value.capabilities):
        task=evaluator.suspension.task(_run(evaluator,value,request.payload), capture_roots=(value,))
    task.agent_operation=True;task.context_structured=True
    return task


def _run(evaluator,binding,payload):
    with scope(evaluator,binding.capabilities):
        return (yield from _run_scoped(evaluator,binding,payload))


def _run_scoped(evaluator,binding,payload):
    from .suspension import Effect,hard_error,TaskCancelled,error_priority
    task=evaluator.suspension.current.task
    owned(binding,evaluator)
    if task.recording_scope is not None:
        if evaluator.mode_handler is not None:
            yield Effect('mode',{'operation':'recording.abort','payload':{'scopeId':task.recording_scope,'reason':'session_effect_forbidden'}})
        raise LanguageError('Recording scopes cannot call Sessions',code='mode_error')
    if evaluator.session_handler is None or evaluator.effect_handler is None:
        raise LanguageError('BoundSession requires a host Session driver',code='capability_error')
    handle=binding.session
    if handle.busy:raise LanguageError('AgentSession 正在执行另一调用',code='session_busy')
    handle.busy=True;identifier=None;attempted=False;hard=False;original=None
    payload={'operation':'start','payload':{**payload,'revision':handle.snapshot['revision'],'binding':binding.binding}}
    try:
        while True:
            attempted=True
            response=yield Effect('session',{'operation':'bound_transition','payload':payload})
            identifier=response.get('invocation')
            if response['status']=='done':return accept_turn(handle,response['value'])
            tool=next((tool for tool in binding.tools if tool.name==response['name']),None)
            if tool is None:raise LanguageError('Undeclared Session language tool',code='capability_error')
            result=yield from invoke_tool(evaluator,tool,response['input'])
            payload={'operation':'resume','invocation':identifier,'cursor':response['cursor'],'value':result}
    except BaseException as error:
        original=error;hard=hard_error(error) and not(isinstance(error,TaskCancelled) and error._cancel_event is not None)
        raise
    finally:
        try:
            if attempted and not hard:
                close={'operation':'close','invocation':identifier}if identifier is not None else {'operation':'close_owner'}
                try:yield Effect('session',{'operation':'bound_transition','payload':close})
                except BaseException as cleanup:
                    if original is None or error_priority(cleanup)>error_priority(original):raise
        finally:handle.busy=False


def builtins(evaluator):
    def info(value):return binding_info(owned(value,evaluator))
    return {'session_bind':lambda value,tools:bind(evaluator,value,tools),'session_binding_info':info}


class SessionInvocation:
    def __init__(self,context,payload,allowed):
        self.lock=threading.Lock();self.context=context;self.payload=json_copy(payload,max_chars=250000)
        self.authority=frozenset(allowed);self.task_key=context.effect_task_key;self.recording_scope=context.recording_scope
        self.cursor=0;self.claim=None;self.finished=False;self.host=None;self.child=None;self.result=None
        self.tools={};self.driver=self.actions()

    def actions(self):
        from .runtime import Context
        from .control import plugin_host
        import time
        context=self.context;payload=self.payload;sessions=context.store.sessions
        fields={'key','revision','input','messageId','binding'}
        if type(payload)is not dict or set(payload)-{'_observed'}!=fields:raise LanguageError('Invalid bound Session payload',code='session_error')
        message=context.computer_host().observed_message(context,payload['_observed'])if '_observed'in payload else None
        self.claim=sessions.claim(context.project['id'],payload['key'],payload['revision'],payload['input'],payload['messageId'],
            self.authority,context.run_id,context.project['version'],context.engine.settings.redact,
            user_message=message,tool_binding=payload['binding'])
        if 'receipt'in self.claim:self.finished=True;return self.claim
        row=self.claim['row'];config=row['agent']['config'];self.node_id=row['agent']['name']
        self.tools={item['name']:item for item in payload['binding']['tools']}
        settings=context.engine.settings
        resolved=settings.resolve(config.get('route',self.node_id),config.get('role','planner'if config.get('level')=='platform'else'executor'))
        if resolved['backend']!='native':raise LanguageError('AgentSession requires native backend',code='session_error')
        if 'maxSteps'in config:resolved['maxSteps']=config['maxSteps']
        resolved['timeout']=min(resolved['timeout'],max(.1,context.budget.deadline-time.monotonic()))
        self.child=Context(context.engine,context.run_id,context.project,context.cancel)
        self.child.budget=context.budget;self.child.team_scope=context.team_scope;self.child.program_call_id=context.program_call_id
        self.child.effect_task_key=self.task_key
        self.allowed=set(self.claim['allowed'])
        # Host declarations retain the Session's original launch ceiling. Native
        # permissions belong only to explicit live language callbacks: claim
        # checks them against fresh project authority, and the original caller
        # plus BoundSession scope still bounds their actual AST execution.
        for tool in self.tools.values():
            if not callback_capabilities_allowed(tool['capabilities'],self.allowed,allowed=self.authority,descriptors=context.engine.tools):raise LanguageError('Session language tool capability was revoked',code='capability_error')
        descriptors={name:context.engine.tools[name]for name in sorted(self.allowed)};descriptors.update(self.tools)
        specs=[native_tool(name,descriptor,mode='wrapped',image_output=name=='browser.control')
               for name,descriptor in sorted(descriptors.items())]
        self.host=plugin_host(settings,lambda kind,payload:self.context.event(kind,payload))
        factory=self.host.require('native')
        harness=factory(resolved,client_factory=lambda c:settings.client(resolved=c),report_failure=settings.report_failure,report_success=settings.report_success)
        if not hasattr(harness,'actions'):raise LanguageError('Native Session provider requires resumable actions',code='session_error')
        context.event('route.selected',{k:resolved[k]for k in ('provider','model','backend','configVersion')})
        result=yield from harness.actions(config.get('prompt',''),payload['input'],tools=specs,session_history=self.claim['history'],
            checkpoint=lambda:self.context.budget.tick(),event=lambda kind,data:self.context.event(kind,{'sessionKey':payload['key'],'nodeId':self.node_id,**settings.redact(data)}),
            journal=lambda messages:sessions.journal(self.claim,messages,settings.redact),context_limit=row['contextLimit'],user_message=message)
        return result

    def close_step(self):
        if self.child is None:return
        error=None
        for env in list(self.child.tool_environments.values()):
            try:env.close()
            except BaseException as caught:
                from .suspension import error_priority
                if error_priority(caught)>error_priority(error):error=caught
        self.child.tool_environments.clear()
        if error is not None:raise error

    def close_resources(self):
        error=None
        for action in (self.close_step,lambda:self.child.close_services('completed')if self.child else None,
                       lambda:self.host.close()if self.host else None):
            try:action()
            except BaseException as caught:
                from .suspension import error_priority
                if error_priority(caught)>error_priority(error):error=caught
        self.host=None;self.child=None
        if error is not None:raise error

    def complete(self,result):
        if self.finished:return result
        self.close_resources()
        with self.context.engine.idle:
            self.context.budget.tick()
            result=self.context.store.sessions.complete(self.claim,result,self.context.engine.settings.redact)
        self.finished=True
        return result

    def close(self):
        error=None
        try:self.driver.close()
        except BaseException as caught:error=caught
        try:self.close_resources()
        except BaseException as caught:
            from .suspension import error_priority
            if error_priority(caught)>error_priority(error):error=caught
        if self.claim is not None and not self.finished and 'receipt'not in self.claim:
            try:self.context.store.sessions.fail(self.claim,self.context.engine._run_error(error)if error else 'Bound Session turn did not complete')
            except BaseException as caught:
                from .suspension import error_priority
                if error_priority(caught)>error_priority(error):error=caught
            self.finished=True
        if error is not None:raise error


def transition(context,payload,allowed):
    if context.recording_scope is not None:raise LanguageError('Session effects cannot be recorded',code='session_error')
    operation=payload['operation']
    with context.services['lock']:
        registry=context.services.setdefault('sessionInvocations',{})
        if operation=='close_owner':
            matches=[(key,item)for key,item in registry.items()if item.task_key==context.effect_task_key and item.authority==frozenset(allowed)and item.recording_scope==context.recording_scope]
            if not matches:return {'status':'closed'}
            if len(matches)!=1:raise LanguageError('Ambiguous Session owner',code='ownership_error')
            identifier,invocation=matches[0];operation='close'
        elif operation=='start':
            if len(registry)>=128:raise ResourceLimit('Too many active bound Session invocations')
            invocation=SessionInvocation(context,payload['payload'],allowed);identifier=uuid.uuid4().hex;registry[identifier]=invocation
        else:
            identifier=payload['invocation'];invocation=registry.get(identifier)
            if invocation is None:
                if operation=='close':return {'status':'closed'}
                raise LanguageError('Session invocation is closed',code='ownership_error')
    if not invocation.lock.acquire(False):raise LanguageError('Session invocation is busy',code='session_busy')
    original=None
    try:
        if invocation.task_key!=context.effect_task_key or invocation.authority!=frozenset(allowed)or invocation.recording_scope!=context.recording_scope:
            raise LanguageError('Session invocation changed owner or authority',code='ownership_error')
        invocation.context=context
        if invocation.child is not None:
            invocation.child.cancel=context.cancel;invocation.child.budget=context.budget
        if operation=='close':invocation.close();return {'status':'closed'}
        context.budget.tick()
        if operation=='start':action=next(invocation.driver)
        else:
            if payload.get('cursor')!=invocation.cursor:raise LanguageError('Stale Session transition',code='ownership_error')
            action=invocation.driver.send(json_copy(payload['value']))
        while True:
            name=action['name'];argument=json_copy(action['input'])
            if name in invocation.tools:
                check_type(argument,invocation.tools[name]['inputType']);invocation.cursor+=1
                context.event('session.language_tool',{'key':invocation.payload['key'],'tool':name,'cursor':invocation.cursor})
                return {'status':'tool','invocation':identifier,'cursor':invocation.cursor,'name':name,'input':argument}
            try:result=invocation.child.tool(name,argument,invocation.allowed,invocation.node_id)
            except BaseException as error:action=invocation.driver.throw(error)
            else:action=invocation.driver.send(result)
    except StopIteration as result:
        try:return {'status':'done','value':invocation.complete(result.value)}
        except BaseException as error:
            original=error;raise
    except BaseException as error:
        original=error
        raise
    finally:
        try:
            try:invocation.close_step()
            except BaseException as cleanup:
                from .suspension import error_priority
                if error_priority(cleanup)>error_priority(original):original=cleanup
            if original is not None or invocation.finished or operation=='close':
                with context.services['lock']:registry.pop(identifier,None)
                try:invocation.close()
                except BaseException as cleanup:
                    from .suspension import error_priority
                    if error_priority(cleanup)>error_priority(original):original=cleanup
        finally:invocation.lock.release()
        if original is not None:raise original
