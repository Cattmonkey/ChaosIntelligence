"""Owned language closures as Agent tools, driven by the one AST scheduler."""
from dataclasses import dataclass
from .tool_declarations import native_tool
from contextlib import contextmanager
from .language import LanguageError, ResourceLimit, Agent, check_type
from .extensions import ToolSpec, json_copy, valid_name, language_permissions, callback_capabilities_allowed


def current_caps(evaluator):
    runner = evaluator.suspension.current
    return runner.capabilities if runner is not None else None


@contextmanager
def scope(evaluator, capabilities):
    runner = evaluator.suspension.current
    if runner is None:
        yield
        return
    previous = runner.capabilities
    runner.capabilities = capabilities if previous is None else previous if capabilities is None else previous & capabilities
    try: yield
    finally: runner.capabilities = previous


def access(value, evaluator):
    current = current_caps(evaluator)
    captured = getattr(value, 'capabilities', None)
    if current is not None and (captured is None or not captured.issubset(current)):
        raise LanguageError('Deferred resource has broader Agent tool capabilities', code='capability_error')


@dataclass(frozen=True, eq=False)
class AgentTool:
    owner: object
    name: str
    handler: object
    description: str
    input_type: str
    output_type: str
    capabilities: frozenset


@dataclass(frozen=True, eq=False)
class BoundAgent:
    owner: object
    agent: object
    tools: tuple
    @property
    def name(self): return self.agent.name


def owned(value, evaluator):
    if value.owner is not evaluator:
        raise LanguageError('Agent tool bindings cannot cross evaluators', code='ownership_error')
    return value


def tool_info(value):
    return {'name':value.name, 'description':value.description, 'inputType':value.input_type,
            'outputType':value.output_type, 'capabilities':sorted(value.capabilities)}


def binding_info(value):
    return {'agent':{'kind':'agent','name':value.agent.name,'config':json_copy(value.agent.config)},
            'tools':[tool_info(tool) for tool in value.tools]}


def create(evaluator, name, handler, description='', input_type='Any', output_type='Any', capabilities=[]):
    from .objects import require_callable
    require_callable(handler, evaluator)
    try:
        ToolSpec(name, lambda value: value, description, input_type=input_type, output_type=output_type)
        capabilities = json_copy(capabilities)
        if type(capabilities) is not list or len(capabilities)>128 or len(set(capabilities))!=len(capabilities):
            raise ValueError('Capabilities must be a unique list of tool names')
        for capability in capabilities: valid_name(capability)
    except (ValueError, TypeError) as error:
        raise LanguageError(str(error), code='argument_error') from None
    captured=current_caps(evaluator)
    if captured is not None and not set(capabilities).issubset(captured):
        raise LanguageError('AgentTool capabilities exceed enclosing authority',code='capability_error')
    return AgentTool(evaluator,name,handler,description,input_type,output_type,frozenset(capabilities))


def bind(evaluator, agent, tools):
    if type(agent) is not Agent or agent.owner not in (None,evaluator):
        raise LanguageError('agent_bind requires an owned Agent',code='argument_error')
    if type(tools) is not list or not 0<=len(tools)<=128:
        raise LanguageError('agent_bind requires up to 128 AgentTools',code='argument_error')
    names=set(); requested=agent.config.get('tools',[])
    if type(requested) is not list or any(type(x) is not str for x in requested):
        raise LanguageError('Agent tools must be a list of capabilities',code='argument_error')
    for tool in tools:
        if type(tool) is not AgentTool: raise LanguageError('agent_bind requires AgentTool descriptors',code='argument_error')
        owned(tool,evaluator)
        if tool.name in names or tool.name in requested: raise LanguageError('Duplicate Agent tool name '+tool.name,code='argument_error')
        names.add(tool.name)
        if not callback_capabilities_allowed(tool.capabilities,requested): raise LanguageError('AgentTool capabilities exceed Agent tools',code='capability_error')
        captured=current_caps(evaluator)
        if captured is not None and not tool.capabilities.issubset(captured): raise LanguageError('AgentTool capabilities exceed enclosing authority',code='capability_error')
    # Freeze the public definition independently of mutable declaration records.
    from .agent_values import validate_definition
    definition=validate_definition({'kind':'agent','name':agent.name,'config':agent.config})
    return BoundAgent(evaluator,Agent(agent.name,definition['config'],evaluator),tuple(tools))


def call(evaluator, binding, value):
    owned(binding,evaluator)
    try: value=json_copy(value)
    except ValueError as error: raise LanguageError(str(error),code='argument_error') from None
    task=evaluator.suspension.task(_run(evaluator,binding,value), capture_roots=(binding,value))
    task.agent_operation=True
    task.context_structured=True
    return task


def invoke_tool(evaluator,tool,argument):
    """One shared AST callback boundary for ephemeral and persistent Agents."""
    from .suspension import Task
    from .contexts import _await,_operation
    evaluator.tick()
    with scope(evaluator,tool.capabilities):
        argument=check_type(json_copy(argument),tool.input_type)
        created=len(evaluator.suspension.tasks)
        result=yield from evaluator._s_invoke(tool.handler,[argument])
        if isinstance(result,Task):
            result=yield from _await(evaluator,result,_operation(evaluator,result,evaluator.suspension.tasks[created:]))
        from .multiple_values import primary
        result=primary(result,evaluator)
        try:result=json_copy(result)
        except ValueError as error:raise LanguageError(str(error),code='agent_tool_error') from None
        return check_type(result,tool.output_type)


def _run(evaluator,binding,value):
    from .suspension import Effect,Task
    from .contexts import _await, _operation
    if evaluator.effect_handler is None:
        raise LanguageError('BoundAgent requires a host Agent driver',code='capability_error')
    payload={'operation':'start','binding':binding_info(binding),'value':value}
    identifier=None
    attempted=False
    hard=False
    original=None
    try:
        while True:
            attempted=True
            response=yield Effect('agent_transition',payload)
            identifier=response.get('invocation')
            if response['status']=='done': return response['value']
            tool=next((item for item in binding.tools if item.name==response['name']),None)
            if tool is None: raise LanguageError('Undeclared language Agent tool',code='capability_error')
            result=yield from invoke_tool(evaluator,tool,response['input'])
            payload={'operation':'resume','invocation':identifier,'cursor':response['cursor'],'value':result}
    except BaseException as error:
        from .suspension import hard_error, TaskCancelled
        original=error
        hard=hard_error(error) and not (isinstance(error,TaskCancelled) and error._cancel_event is not None)
        raise
    finally:
        # Task-local cancellation owns this native driver too. Close it through
        # the worker barrier before the structured parent can continue. This is
        # host bookkeeping; global hard abort still uses the run-level barrier.
        if attempted and not hard:
            from .suspension import error_priority
            cleanup_payload=({'operation':'close','invocation':identifier} if identifier is not None else {'operation':'close_owner'})
            try: yield Effect('agent_transition',cleanup_payload)
            except BaseException as cleanup:
                if original is None or error_priority(cleanup)>error_priority(original):raise


def builtins(evaluator):
    def info(value):
        if type(value) is not AgentTool: raise LanguageError('Expected AgentTool',code='argument_error')
        return tool_info(owned(value,evaluator))
    def bindings(value):
        if type(value) is not BoundAgent: raise LanguageError('Expected BoundAgent',code='argument_error')
        return binding_info(owned(value,evaluator))
    return {'agent_tool':lambda name,handler,description='',input_type='Any',output_type='Any',capabilities=[]:create(evaluator,name,handler,description,input_type,output_type,capabilities),
            'agent_bind':lambda value,tools:bind(evaluator,value,tools), 'agent_tool_info':info,'agent_binding_info':bindings}


class HostInvocation:
    """Host-only model state. No closure, environment, or evaluator crosses here."""
    def __init__(self, context, binding, value, allowed, node_id):
        import threading
        self.lock=threading.Lock();self.context=context;self.cursor=0
        self.recording_scope=context.recording_scope
        self.authority=frozenset(allowed);self.task_key=context.effect_task_key
        self.binding=json_copy(binding);self.node_id=node_id
        self.name=binding['agent']['name'];self.config=binding['agent']['config']
        requested=self.config.get('tools',[])
        if type(requested) is not list or any(type(x) is not str or x not in context.engine.tools for x in requested):
            raise LanguageError('Agent contains unknown tool capabilities',code='capability_error')
        if not set(requested).issubset(allowed) or set(requested)&language_permissions(self.context.engine.tools):
            raise LanguageError('Agent capabilities exceed enclosing authority',code='capability_error')
        self.allowed=set(requested)
        self.tools={item['name']:item for item in binding['tools']}
        if len(self.tools)!=len(binding['tools']) or set(self.tools)&self.allowed:
            raise LanguageError('Duplicate Agent tool names',code='argument_error')
        for item in self.tools.values():
            if not callback_capabilities_allowed(item['capabilities'],self.allowed,allowed=allowed,descriptors=context.engine.tools): raise LanguageError('AgentTool capabilities exceed Agent',code='capability_error')
        self.host=None
        self.driver=self.actions(value)

    def actions(self,value):
        fixture_actions=getattr(self.context,'fixture_actions',None)
        if fixture_actions is not None:
            return (yield from fixture_actions(self.binding,value))
        provider=self.config.get('provider','local')
        context=self.context
        descriptors={name:context.engine.tools[name] for name in sorted(self.allowed)}
        descriptors.update({name:{**contract,'effect':'language'} for name,contract in self.tools.items()})
        context.event('agent.observing',{'nodeId':self.node_id,'agent':self.name,'provider':provider,'languageTools':list(self.tools)})
        if provider in context.engine.custom_providers:
            # The same custom action driver serves legacy and bound Agents.
            return (yield from context.custom_actions(self.name,self.config,value,descriptors,self.node_id,
                                                      context=lambda:self.context))
        if provider!='model':
            raise LanguageError('BoundAgent requires a structured custom or native model provider',code='provider_error')
        from .control import plugin_host
        from .harness import HarnessError
        import time
        settings=context.engine.settings
        resolved=settings.resolve(self.config.get('route',self.node_id),self.config.get('role','planner' if self.config.get('level')=='platform' else 'executor'))
        if resolved['backend']!='native':raise LanguageError('Language Agent tools require the native model backend',code='capability_error')
        if 'maxSteps' in self.config:resolved['maxSteps']=self.config['maxSteps']
        resolved['timeout']=min(resolved['timeout'],max(.1,context.budget.deadline-time.monotonic()))
        specs=[native_tool(name,descriptor,mode='wrapped',image_output=name=='browser.control')
               for name,descriptor in descriptors.items()]
        self.host=plugin_host(settings,lambda kind,payload:self.context.event(kind,payload))
        primary=None
        try:
            factory=self.host.require('native')
            harness=factory(resolved,client_factory=lambda c:settings.client(resolved=c),report_failure=settings.report_failure,report_success=settings.report_success)
            if not hasattr(harness,'actions'):raise LanguageError('Native provider does not support resumable tools',code='provider_error')
            context.event('route.selected',{k:resolved[k] for k in ('provider','model','backend','configVersion')})
            return (yield from harness.actions(self.config.get('prompt',''),value,specs,
                checkpoint=lambda:self.context.budget.tick(),event=lambda kind,payload:self.context.event(kind,{'nodeId':self.node_id,**settings.redact(payload)})))
        except (HarnessError,ValueError) as error:
            primary=error
            raise LanguageError(settings.redact(str(error)),code='provider_error') from None
        except BaseException as error:
            primary=error
            raise
        finally:
            host,self.host=self.host,None
            try:host.close()
            except BaseException as cleanup:
                from .suspension import error_priority
                if primary is None or isinstance(primary,GeneratorExit) or error_priority(cleanup)>error_priority(primary):raise

    def close(self):
        self.driver.close()
        if self.host is not None:self.host.close();self.host=None


def transition(context,payload,allowed,node_id):
    import uuid
    operation=payload['operation']
    with context.services['lock']:
        registry=context.services.setdefault('agentInvocations',{})
        if operation=='close_owner':
            # The start worker may complete before cancellation discards its
            # response. Its Task still owns the invocation even without an id.
            # Authority comes exclusively from scheduler/host provenance.
            matches=[(key,item) for key,item in registry.items()
                     if item.task_key==context.effect_task_key and
                     item.recording_scope==context.recording_scope and
                     item.authority==frozenset(allowed)]
            if not matches:return {'status':'closed'}
            if len(matches)!=1:raise LanguageError('Ambiguous Agent invocation owner',code='ownership_error')
            identifier,invocation=matches[0]
            operation='close'
        elif operation=='start':
            if len(registry)>=128:raise ResourceLimit('Too many active bound Agent invocations')
            invocation=HostInvocation(context,payload['binding'],json_copy(payload['value']),allowed,node_id)
            identifier=uuid.uuid4().hex;registry[identifier]=invocation
        else:
            identifier=payload['invocation'];invocation=registry.get(identifier)
            if invocation is None:
                if operation=='close':return {'status':'closed'}
                raise LanguageError('Bound Agent invocation is closed',code='ownership_error')
    if not invocation.lock.acquire(False):raise LanguageError('Bound Agent invocation is busy',code='ownership_error')
    try:
        if invocation.task_key!=context.effect_task_key or invocation.recording_scope!=context.recording_scope or invocation.authority!=frozenset(allowed):
            raise LanguageError('Bound Agent invocation changed authority or owner',code='capability_error')
        invocation.context=context
        if operation=='close':
            invocation.close()
            with context.services['lock']:registry.pop(identifier,None)
            return {'status':'closed'}
        context.budget.tick()
        if operation=='start':action=next(invocation.driver)
        else:
            if payload.get('cursor')!=invocation.cursor:raise LanguageError('Stale Agent transition',code='ownership_error')
            action=invocation.driver.send(json_copy(payload['value']))
        while True:
            if type(action) is not dict or type(action.get('name')) is not str:raise LanguageError('Invalid Agent action',code='provider_error')
            name=action['name'];argument=json_copy(action['input'])
            if name in invocation.tools:
                contract=invocation.tools[name];check_type(argument,contract['inputType'])
                invocation.cursor+=1
                context.event('agent.language_tool',{'nodeId':node_id,'agent':invocation.name,'tool':name,'cursor':invocation.cursor,'contract':contract})
                return {'status':'tool','invocation':identifier,'cursor':invocation.cursor,'name':name,'input':argument}
            try:result=context.tool(name,argument,invocation.allowed,node_id)
            except BaseException as error:action=invocation.driver.throw(error)
            else:action=invocation.driver.send(result)
    except StopIteration as result:
        with context.services['lock']:registry.pop(identifier,None)
        invocation.close()
        output=json_copy(result.value)
        context.event('agent.completed',{'nodeId':node_id,'agent':invocation.name,'languageTools':list(invocation.tools)})
        return {'status':'done','value':output}
    except BaseException as original:
        with context.services['lock']:registry.pop(identifier,None)
        try:invocation.close()
        except BaseException as cleanup:
            from .suspension import error_priority
            if error_priority(cleanup)>error_priority(original):raise
        raise
    finally:invocation.lock.release()
