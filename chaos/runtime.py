import concurrent.futures
import copy
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .graph import GraphError, validate, level, executable
from .language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, serializable
from .store import Conflict
from .tool_declarations import native_tool
from .native_code_cache import DEFAULT_NATIVE_CODE_CACHE, NativeCodeCache


from .extensions import (BUILTIN_TOOLS, DEFAULT_CAPABILITIES, Registry, AgentRequest,
                         ExtensionContext, Final, ToolCall, json_copy, TERMINAL_STATES, language_permissions)
from .language import check_type
from .ownership import RuntimeLease
from .store import version_number
from .modules import ProgramBundle, project_language_versions
from .debugger import DebugSession, options as debug_options

TOOLS = BUILTIN_TOOLS  # compatibility for existing embedding callers
SAFE_TOOLS = set(DEFAULT_CAPABILITIES)


class Cancelled(ExecutionControl):
    pass


class Budget:
    def __init__(self, cancel, steps=100000, seconds=60):
        self.cancel = cancel
        self.limit, self.steps = steps, 0
        self._deadline = time.monotonic() + seconds
        self.pause_clock = lambda: 0.0
        self.lock = threading.Lock()

    @property
    def deadline(self): return self._deadline + self.pause_clock()

    def tick(self):
        if self.cancel.is_set():
            raise Cancelled('执行已取消')
        with self.lock:
            self.steps += 1
            if self.steps > self.limit:
                raise ResourceLimit('运行总计算预算耗尽')
        if time.monotonic() > self.deadline:
            raise ResourceLimit('运行总时限已到')


class CombinedCancel:
    def __init__(self, parent, task): self.parent, self.task = parent, task
    def is_set(self): return self.parent.is_set() or self.task.is_set()
    def _cause(self):
        if isinstance(self.parent, CombinedCancel):
            cause = self.parent._cause()
        else:
            cause = ('global', self.parent) if self.parent.is_set() else None
        return cause or (('task', self.task) if self.task.is_set() else None)


class TaskBudget:
    def __init__(self, shared, cancel): self.shared, self.cancel = shared, cancel
    @property
    def deadline(self): return self.shared.deadline
    @property
    def lock(self): return self.shared.lock
    @property
    def steps(self): return self.shared.steps
    def tick(self):
        cause = self.cancel._cause()
        if cause is not None and cause[0] == 'global': raise Cancelled('执行已取消')
        if cause is not None:
            from .suspension import TaskCancelled
            raise TaskCancelled('任务已取消', _cancel_event=cause[1])
        self.shared.tick()


def as_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _team_scope(value):
    """Validate interpreter metadata; public tool/config data never enters here."""
    from .modes import identifier
    value = json_copy(value, max_chars=40000)
    fields = {'name', 'definitionHash', 'roleId', 'rolePath', 'capabilities'}
    if type(value) is not dict or not fields <= set(value) or set(value)-fields-{'implementationKind','invocationId'}:
        raise LanguageError('Invalid internal Team scope', code='team_error')
    if (type(value['name']) is not str or not 1 <= len(value['name']) <= 200 or
            type(value['definitionHash']) is not str or re.fullmatch(r'[0-9a-f]{64}', value['definitionHash']) is None):
        raise LanguageError('Invalid internal Team identity', code='team_error')
    if ('implementationKind'in value)!=('invocationId'in value):raise LanguageError('Incomplete Team invocation provenance',code='team_error')
    if 'implementationKind'in value:
        if value['implementationKind'] not in ('Function','Agent','BoundAgent','AgentSession','BoundSession','Team','BoundTeam','Program','Process','Plan','Goal','Skill','Computer','Robot','Bot'):raise LanguageError('Invalid Team implementation kind',code='team_error')
        identifier(value['invocationId'],'invocationId')
    identifier(value['roleId'], 'roleId')
    path, capabilities = value['rolePath'], value['capabilities']
    if type(path) is not list or not 1 <= len(path) <= 64:
        raise LanguageError('Invalid internal Team role path', code='team_error')
    for part in path: identifier(part, 'rolePath')
    if len(set(path)) != len(path) or path[-1] != value['roleId']:
        raise LanguageError('Invalid internal Team role path', code='team_error')
    if (type(capabilities) is not list or len(capabilities) > 128 or
            any(type(item) is not str or not 1 <= len(item) <= 128 for item in capabilities) or
            len(set(capabilities)) != len(capabilities)):
        raise LanguageError('Invalid internal Team capabilities', code='team_error')
    return value


class Context:
    def __init__(self, engine, run_id, project, cancel, services=None):
        self.engine, self.store = engine, engine.store
        self.run_id, self.project, self.cancel = run_id, project, cancel
        self.budget = Budget(cancel,seconds=engine.settings.runtime()['timeout'])
        self.workflow_depth = 0
        self.trace_state=None
        self.tool_environments={}
        self.debugger=engine.debug_sessions.get(run_id)
        if self.debugger is not None:self.budget.pause_clock=self.debugger.paused_seconds
        self.effect_task_key=None
        self.recording_scope=None
        self.team_scope=None
        self.agent_capabilities=None
        self.bot_guard=None
        self.process_claim=engine.process_claims.get(run_id)
        self.program_depth=0
        self.program_call_id=None
        self.executable = project.get('execution')
        self.source_program_definition = None
        self.services=services if services is not None else {"lock":threading.RLock(),"mode":None,"browser":None,"computers":None,"recordingComputers":{}}

    def mode_host(self):
        with self.services['lock']:
            if self.executable is not None or self.source_program_definition is not None:
                from .mode_store import ModeHost
                from .program_values import canonical_hash
                identity = ({'kind': 'executable', 'imageHash': self.executable['imageHash']}
                            if self.executable is not None else
                            {'kind': 'source-program', 'definitionHash': canonical_hash(self.source_program_definition)})
                code_hash = canonical_hash(identity)
                hosts = self.services.setdefault('executableModes', {})
                if code_hash not in hosts:
                    host = ModeHost(self.store, self.project, self.run_id, self.engine.settings.redact)
                    # No user operation precedes binding this actual code identity.
                    host.source_hash = code_hash
                    hosts[code_hash] = host
                return hosts[code_hash]
            if self.services['mode'] is None:
                from .mode_store import ModeHost
                self.services['mode']=ModeHost(self.store,self.project,self.run_id,self.engine.settings.redact)
            return self.services['mode']

    def mode_operation(self, operation, payload):
        self.budget.tick()
        result=self.mode_host().handle(operation,json_copy(payload))
        self.budget.tick()
        return result

    def close_services(self, outcome):
        error=None
        for invocation in list(self.services.get('agentInvocations',{}).values())+list(self.services.get('sessionInvocations',{}).values()):
            try: invocation.close()
            except BaseException as caught:
                if error is None:error=caught
        self.services.get('agentInvocations',{}).clear()
        self.services.get('sessionInvocations',{}).clear()
        for host in list(self.services.get('executableModes', {}).values()):
            try: host.close(outcome)
            except BaseException as caught:
                if error is None: error = caught
        self.services.get('executableModes', {}).clear()
        for key in ('browser','computers','devices','robots','mode'):
            resource=self.services.get(key)
            if resource is not None:
                try:
                    if key in ('mode','computers','devices','robots'):resource.close(outcome)
                    else:resource.close()
                except BaseException as caught:
                    if error is None:error=caught
                self.services[key]=None
        if error is not None:raise error

    def scoped_allowed(self, allowed):
        allowed = set(allowed)
        if self.process_claim is not None:
            allowed.intersection_update(self.store.project(self.project['id'])['capabilities'])
            allowed.intersection_update(self.process_claim['pending']['effectiveCapabilities'])
        if self.agent_capabilities is not None: allowed.intersection_update(self.agent_capabilities)
        return allowed.intersection(self.team_scope['capabilities']) if self.team_scope is not None else allowed

    def event(self, kind, payload):
        if self.executable is not None:
            payload = {**payload, 'executionKind': 'executable', 'imageHash': self.executable['imageHash']}
        elif self.source_program_definition is not None:
            from .program_values import canonical_hash
            payload = {**payload, 'executionKind': 'source-program', 'definitionHash': canonical_hash(self.source_program_definition)}
        if self.program_call_id is not None:
            payload = {'callId':self.program_call_id, **payload}
        if self.team_scope is not None:
            payload = {**payload, 'team': {key: copy.deepcopy(self.team_scope[key]) for key in ('name', 'definitionHash', 'roleId', 'rolePath','implementationKind','invocationId')if key in self.team_scope}}
        return self.store.event(self.project['id'], kind, self.engine.settings.redact({'version': self.project['version'], **payload}), self.run_id)

    def process_bot_operation(self,operation,payload):
        if self.process_claim is None:return None
        return self.engine.process_bot_operation(self.process_claim,operation,payload)

    def trace(self, event, node_id):
        if self.executable is not None:
            event = {**event, 'executionKind': 'executable', 'imageHash': self.executable['imageHash']}
        lifecycle = event.get('event') in {'task.created', 'task.started', 'task.completed', 'task.cancelled', 'task.failed', 'group.created', 'group.entered', 'group.member', 'group.closed', 'shield.created', 'shield.entered', 'shield.deferred', 'shield.closed', 'channel.created', 'channel.closed', 'lifecycle.truncated'}
        if lifecycle:
            # All nested evaluators share one run-level lifecycle budget. Task
            # identities are interpreted with callId, not parent debugger frames.
            with self.services['lock']:
                count = self.services.get('taskEventCount', 0)
                self.services['taskEventCount'] = count + 1
            if count > 1000: return
            if count == 1000:
                event = {'event':'lifecycle.truncated', 'limit':1000}
            forwarded = {**event, 'runId':self.run_id, 'nodeId':node_id}
            if self.program_call_id is not None: forwarded['callId'] = self.program_call_id
            if self.engine.trace_handler: self.engine.trace_handler(forwarded)
            self.event('program.' + event['event'], {**event, 'nodeId':node_id})
            return
        if self.program_call_id is not None:
            # Child evaluators have their own frame identities. Keep their
            # execution evidence separate from the parent's debugger protocol.
            if self.trace_state is not None:
                with self.trace_state['lock']:
                    if self.trace_state['remaining'] <= 0:return
                    self.trace_state['remaining'] -= 1
                self.event('program.child.trace', {'event':event.get('event'),'nodeId':node_id})
            return
        if self.engine.trace_handler:self.engine.trace_handler({**event,'runId':self.run_id,'nodeId':node_id})
        if event.get('event','').startswith(('mode.', 'team.role.')):
            self.event(event['event'],{**event,'nodeId':node_id})
            return
        if self.trace_state is not None:
            with self.trace_state['lock']:
                remaining=self.trace_state['remaining']
                if remaining>=0:
                    self.trace_state['remaining']-=1
                    self.event('program.step'if remaining else 'program.trace_truncated',
                               {**event,'nodeId':node_id}if remaining else {'limit':200})

    def effect(self, kind, payload, task_cancel, allowed, node_id, allow_agents):
        from .bot_host import guarded_effect
        return guarded_effect(self, kind, payload, task_cancel, allowed, node_id, allow_agents)

    def _effect_unchecked(self, kind, payload, task_cancel, allowed, node_id, allow_agents):
        # Only copied values cross from the AST scheduler into effect workers.
        child = Context(self.engine, self.run_id, self.project, CombinedCancel(self.cancel, task_cancel), self.services)
        child.executable = self.executable
        child.source_program_definition = self.source_program_definition
        child.budget = TaskBudget(self.budget, child.cancel)
        child.workflow_depth = self.workflow_depth
        child.trace_state = self.trace_state
        # Browser/tool sessions are thread-affine and live for this one effect.
        # An agent may reuse its tools during the effect, then closes them here.
        child.tool_environments = {}
        child.effect_task_key = id(task_cancel)
        child.recording_scope = payload.get("_mode",{}).get("recordingId")
        child.team_scope = self.team_scope
        child.agent_capabilities = payload.get('_agent_caps',self.agent_capabilities)
        child.bot_guard = copy.deepcopy(payload.get('_bot_guard',self.bot_guard))
        child.program_depth = self.program_depth
        child.program_call_id = self.program_call_id
        child.debugger = self.debugger
        allowed = self.scoped_allowed(allowed)
        if '_team' in payload:
            child.team_scope = _team_scope(payload['_team'])
        allowed = child.scoped_allowed(allowed)
        failed = False
        try:
            if kind == 'guidance':return child.guidance_operation(payload['operation'],payload['payload'],allowed,child.team_scope)
            if kind == 'gpu':
                from .gpu_compute import run
                result=run({key:value for key,value in payload.items() if not key.startswith('_')},task_cancel)
                child.budget.tick()
                return result
            if kind == 'ui' and allow_agents:
                from .ui_generation import generate
                return generate(child, payload, allowed, node_id)
            if kind == 'agent_transition' and allow_agents:
                from .agent_tools import transition
                return transition(child,payload,allowed,node_id)
            if kind == 'robot':return child.robot_operation(payload['operation'],payload['payload'],allowed)
            if kind == 'device':return child.device_operation(payload['operation'],payload['payload'],allowed)
            if kind == 'computer': return child.computer_operation(payload['operation'],payload['payload'],allowed,allow_agents=allow_agents)
            if kind == 'agent' and '_observed' in payload:
                return child.computer_operation('agent',payload,allowed,allow_agents=allow_agents)
            if kind=='session' and payload['operation']=='bound_transition' and allow_agents:
                from .session_tools import transition
                return transition(child,payload['payload'],allowed)
            if kind == 'session': return child.session_operation(payload['operation'],payload['payload'],allowed,allow_agents=allow_agents)
            if kind == 'process': return child.process_operation(payload['operation'],payload['payload'],allowed,allow_agents=allow_agents)
            if kind == 'program': return child.program_operation(payload['operation'],payload['payload'],allowed,allow_agents=allow_agents)
            if kind == 'mode': return child.mode_operation(payload['operation'],payload['payload'])
            if kind == 'tool': return child.tool(payload['name'], payload['value'], allowed, node_id)
            if kind == 'agent' and allow_agents: return child.agent(payload['name'], payload['config'], payload['value'], allowed, node_id)
            if kind == 'workflow' and allow_agents: return child.workflow(payload['graph'], payload['value'], allowed)
            raise LanguageError('当前执行上下文不允许该异步效果')
        except BaseException:
            failed = True
            raise
        finally:
            cleanup_error = None
            for environment in list(child.tool_environments.values()):
                try: environment.close()
                except BaseException as error:
                    if cleanup_error is None: cleanup_error = error
            child.tool_environments.clear()
            if cleanup_error is not None and not failed: raise cleanup_error

    def evaluator(self, allowed=None, node_id='program', allow_agents=True, *, process_claim=None):
        allowed = self.scoped_allowed(self.project.get('capabilities', DEFAULT_CAPABILITIES) if allowed is None else allowed)
        def active_allowed():
            from .agent_tools import current_caps
            scope=current_caps(evaluator)
            return allowed if scope is None else set(allowed)&scope
        def active_context():
            from .execution_scope import current
            role=current(evaluator)
            from .bot_runtime import current_guard
            guard=current_guard(evaluator)
            if role is None and guard is None:return self
            child=copy.copy(self)
            if role is not None:child.team_scope=role
            if guard is not None:child.bot_guard=guard
            return child
        def debug_hook(node, env, frames):
            return self.debugger.checkpoint(node, env, frames, task_snapshot=evaluator.suspension.snapshot())
        def condition_debug_hook(metadata,node,env,frames):
            return self.debugger.pause_condition(metadata,node,env,frames,task_snapshot=evaluator.suspension.snapshot())
        evaluator = Evaluator(
            native_code_cache=self.engine.native_code_cache,
            tool_handler=lambda name, value: active_context().tool(name, value, active_allowed(), node_id),
            agent_handler=(lambda name, config, value: active_context().agent(name, config, value, active_allowed(), node_id)) if allow_agents else None,
            trace_handler=lambda event:active_context().trace({**event,'sourceKind':'module' if allow_agents else 'node'},node_id),
            workflow_handler=(lambda graph,value:active_context().workflow(graph,value,active_allowed())) if allow_agents else None,
            debug_hook=debug_hook if self.debugger is not None and allow_agents else None,
            source_handler=self.debugger.register_source if self.debugger is not None and allow_agents else None,
            condition_debug_hook=condition_debug_hook if self.debugger is not None and allow_agents else None,
            condition_pause=self.debugger.config['pauseOnCondition'] if self.debugger is not None and allow_agents else False,
            condition_debug_redact=self.debugger.redact if self.debugger is not None and allow_agents else None,
            mode_handler=self.mode_operation,
            program_handler=lambda operation,payload:active_context().program_operation(operation,payload,active_allowed(),allow_agents=allow_agents),
            program_origin=self.program_origin(),
            device_handler=lambda operation,payload:active_context().device_operation(operation,payload,active_allowed()),
            robot_handler=lambda operation,payload:active_context().robot_operation(operation,payload,active_allowed()),
            robot_cleanup_handler=lambda resource_id:self.robot_cleanup(resource_id),
            computer_handler=lambda operation,payload:active_context().computer_operation(operation,payload,active_allowed(),allow_agents=allow_agents),
            session_handler=lambda operation,payload:active_context().session_operation(operation,payload,active_allowed(),allow_agents=allow_agents),
            process_handler=lambda operation,payload:active_context().process_operation(operation,payload,active_allowed(),allow_agents=allow_agents),
            guidance_handler=self.guidance_operation,
            effect_handler=lambda kind,payload,task_cancel:self.effect(kind,payload,task_cancel,allowed,node_id,allow_agents),
            cancelled=self.cancel.is_set,
            timeout=max(.01, self.budget.deadline - time.monotonic()),
        )
        from .bot_host import _parts as inherited_bot_parts
        evaluator._inherited_bot_ancestry = inherited_bot_parts(self.bot_guard) if self.bot_guard is not None else ()
        evaluator.expression_trace_required = lambda: self.engine.trace_handler is not None or self.trace_state is not None
        base_tick = evaluator.tick
        def tick():
            self.budget.tick()
            base_tick()
        evaluator.tick = tick
        if process_claim is not None:
            evaluator.process_evolution_context={'claim':process_claim,'origin':self.program_origin(),'allowed':active_allowed}
        return evaluator

    def guidance_operation(self, operation, payload, capabilities=None, team_scope=None):
        """Copied run input and public progress; never evaluate submitted text."""
        if operation != "bot_close":self.budget.tick()
        child=copy.copy(self)
        child.agent_capabilities=capabilities
        child.team_scope=_team_scope(team_scope) if team_scope is not None else self.team_scope
        if operation in ('bot_open','bot_receive','bot_adopt','bot_decision','bot_outcome','bot_close','bot_commit'):
            from .bot_host import control
            return control(child,operation,payload)
        if operation=='receive':
            if payload!={}:raise LanguageError('Invalid guidance receive payload',code='guidance_error')
            return self.engine.receive_guidance(child)
        if operation!='progress' or type(payload)is not dict or set(payload)!={'kind','data'}:
            raise LanguageError('Invalid guidance operation',code='guidance_error')
        if payload['kind'] not in ('progress','plan','result'):
            raise LanguageError('Public progress kind must be progress, plan or result',code='guidance_error')
        try:data=json_copy(payload['data'],max_chars=8000,max_nodes=2048,max_depth=32)
        except ValueError as error:raise LanguageError(str(error),code='guidance_error') from error
        if len(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False))>8000:
            raise LanguageError('Public progress JSON exceeds 8000 characters',code='guidance_error')
        with self.engine.lock:
            child.budget.tick()
            if self.run_id not in self.engine.active:raise LanguageError('Guidance run is closed',code='guidance_error')
            box=self.engine.guidance[self.run_id]
            if box['progressCount']>=1000:raise ResourceLimit('Public progress event budget exhausted')
            from .bot_host import provenance
            identity=provenance(child,child.bot_guard) if child.bot_guard is not None else {}
            event_id=child.event('bot.progress',{'kind':payload['kind'],'data':data,**identity})
            box['progressCount']+=1
            return {'runId':self.run_id,'kind':payload['kind'],'eventId':event_id}

    def robot_cleanup(self,resource_id):
        with self.services['lock']:host=self.services.get('robots')
        if host is None:raise LanguageError('Robot resource is not owned by this run',code='ownership_error')
        return host.force_retire(resource_id)

    def robot_operation(self,operation,payload,allowed=None):
        if self.recording_scope is not None:
            try:self.mode_host().handle('recording.abort',{'scopeId':self.recording_scope,'reason':'robot_effect_not_recordable'})
            except BaseException as error:
                from .suspension import hard_error
                if hard_error(error):raise
            raise LanguageError('Physical Robot effects cannot be Skill-recorded',code='mode_error')
        from .robot_host import RobotHost
        with self.services['lock']:
            if self.services.get('robots') is None:self.services['robots']=RobotHost(self)
            host=self.services['robots']
        allowed=self.scoped_allowed(self.project.get('capabilities',DEFAULT_CAPABILITIES) if allowed is None else allowed)
        return host.handle(self,operation,json_copy(payload,max_chars=16000),allowed)

    def device_operation(self,operation,payload,allowed=None):
        if self.recording_scope is not None:
            try:self.mode_host().handle('recording.abort',{'scopeId':self.recording_scope,'reason':'device_effect_not_recordable'})
            except BaseException as error:
                from .suspension import hard_error
                if hard_error(error):raise
            raise LanguageError('Physical Device effects cannot be Skill-recorded',code='device_error')
        from .device_host import DeviceHost
        with self.services['lock']:
            if self.services.get('devices') is None:self.services['devices']=DeviceHost(self)
            host=self.services['devices']
        allowed=self.scoped_allowed(self.project.get('capabilities',DEFAULT_CAPABILITIES) if allowed is None else allowed)
        return host.handle(self,operation,json_copy(payload,max_chars=16000),allowed)

    def computer_host(self):
        from .computer_host import ComputerHost
        with self.services['lock']:
            if self.services.get('computers')is None:self.services['computers']=ComputerHost(self)
            return self.services['computers']

    def computer_operation(self, operation, payload, allowed=None, *, allow_agents=True):
        allowed=self.scoped_allowed(self.project.get('capabilities',DEFAULT_CAPABILITIES)if allowed is None else allowed)
        try:
            self.budget.tick()
            if not allow_agents:raise LanguageError('Function-node contexts cannot use Computers',code='computer_error')
            if operation=='agent':
                message=self.computer_host().observed_message(self,payload['_observed'])
                config=payload['config']
                if config.get('provider','local')!='model':raise LanguageError('ObservedInput requires the native model provider',code='computer_error')
                return self.agent(payload['name'],config,None,allowed,'computer',user_message=message)
            return self.computer_host().handle(self,operation,json_copy(payload,max_chars=150000),allowed)
        except BaseException:
            if self.recording_scope:
                try:self.mode_host().handle('recording.abort',{'scopeId':self.recording_scope,'reason':'computer_operation_failed'})
                except BaseException:pass
            raise

    def session_operation(self, operation, payload, allowed=None, *, allow_agents=True):
        self.budget.tick()
        if self.recording_scope is not None:
            try:self.mode_host().handle('recording.abort',{'scopeId':self.recording_scope,'reason':'session_effect_not_recordable'})
            except BaseException:pass
            raise LanguageError('Session effects cannot run inside a Skill recording',code='session_error')
        if not allow_agents and operation!='status':
            raise LanguageError('Function-node contexts cannot manage Sessions',code='session_error')
        allowed=self.scoped_allowed(self.project.get('capabilities',DEFAULT_CAPABILITIES) if allowed is None else allowed)
        if operation=='bound_transition':
            from .session_tools import transition
            return transition(self,payload,allowed)
        if operation!='turn':
            return self.engine.session_operation(self.project['id'],operation,payload,allowed=allowed,
                version=self.project['version'],checkpoint=self.budget.tick)
        fields={'key','revision','input','messageId'}
        if type(payload)is not dict or set(payload)-{'_observed'}!=fields:raise LanguageError('Invalid Session turn payload',code='session_error')
        sessions=self.store.sessions
        user_message=self.computer_host().observed_message(self,payload['_observed'])if '_observed'in payload else None
        claim=sessions.claim(self.project['id'],payload['key'],payload['revision'],payload['input'],payload['messageId'],
            allowed,self.run_id,self.project['version'],self.engine.settings.redact,**({'user_message':user_message}if user_message is not None else {}))
        if 'receipt'in claim:return claim
        child=None;failure=None
        try:
            settings=self.engine.settings;agent=claim['row']['agent'];config=agent['config'];node_id=agent['name']
            resolved=settings.resolve(config.get('route',node_id),config.get('role','planner' if config.get('level')=='platform' else 'executor'))
            if resolved['backend']!='native':raise LanguageError('AgentSession requires the native backend',code='session_error')
            if 'maxSteps'in config:resolved['maxSteps']=config['maxSteps']
            resolved['timeout']=min(resolved['timeout'],max(.1,self.budget.deadline-time.monotonic()))
            from .control import plugin_host
            factory=plugin_host(settings,lambda kind,payload:self.event(kind,payload)).require('native')
            harness=factory(resolved,client_factory=lambda c:settings.client(resolved=c),report_failure=settings.report_failure,report_success=settings.report_success)
            # Each conversation call owns and closes its effect resources before its independent commit.
            child=Context(self.engine,self.run_id,self.project,self.cancel)
            child.executable=self.executable;child.source_program_definition=self.source_program_definition
            child.budget=self.budget;child.team_scope=self.team_scope;child.program_call_id=self.program_call_id
            child.effect_task_key=self.effect_task_key;child.bot_guard=copy.deepcopy(self.bot_guard)
            allowed=set(claim['allowed'])
            tools=[native_tool(name,self.engine.tools[name],mode='wrapped',
                image_output=name=='browser.control',category='browser' if name=='browser.control' else 'tools')
                for name in sorted(allowed)]
            self.event('route.selected',{k:resolved[k] for k in ('provider','model','backend','configVersion')})
            result=harness.run_session(config.get('prompt',''),payload['input'],claim['history'],tools=tools,
                call_tool=lambda name,value:child.tool(name,value,allowed,node_id),checkpoint=self.budget.tick,
                event=lambda kind,value:self.event(kind,{'sessionKey':payload['key'],'nodeId':node_id,**settings.redact(value)}),
                journal=lambda messages:sessions.journal(claim,messages,settings.redact),context_limit=claim['row']['contextLimit'],**({'user_message':claim['userMessage']}if claim.get('userMessage')is not None else {}))
            self._close_session_resources(child);child=None
            # Cancellation accepted before this lock cannot race past the durable Session commit.
            with self.engine.idle:
                self.budget.tick()
                return sessions.complete(claim,result,settings.redact)
        except BaseException as error:
            failure=error
            try:sessions.fail(claim,self.engine._run_error(error))
            except BaseException:pass  # Original pending claim remains durable for restart recovery.
            raise
        finally:
            if child is not None:
                try:self._close_session_resources(child)
                except BaseException:
                    if failure is None:raise

    @staticmethod
    def _close_session_resources(child):
        error=None
        for env in list(child.tool_environments.values()):
            try:env.close()
            except BaseException as caught:
                if error is None:error=caught
        child.tool_environments.clear()
        try:child.close_services('completed')
        except BaseException as caught:
            if error is None:error=caught
        if error is not None:raise error

    def process_operation(self, operation, payload, allowed=None, *, allow_agents=True):
        self.budget.tick()
        if self.recording_scope is not None:
            try:self.mode_host().handle('recording.abort',{'scopeId':self.recording_scope,'reason':'process_effect_not_recordable'})
            except BaseException:pass
            raise LanguageError('Process effects cannot run inside a Skill recording',code='process_error')
        if not allow_agents and operation not in ('status','events','message'):
            raise LanguageError('Function-node contexts cannot schedule or manage Processes',code='process_error')
        allowed=self.scoped_allowed(self.project['capabilities'] if allowed is None else allowed)
        return self.engine.process_operation(self.project['id'],operation,payload,allowed=allowed,
                                             version=self.project['version'],checkpoint=self.budget.tick)

    def program_origin(self):
        if self.executable is not None or self.source_program_definition is not None:
            return None
        from .program_values import canonical_hash
        definition = {'kind':'program','entry':self.project['entry'],'files':self.project['files'],
                      'languageVersions':project_language_versions(self.project)}
        return {'projectId':self.project['id'],'version':self.project['version'],
                'definitionHash':canonical_hash(definition)}

    def program_operation(self, operation, payload, allowed=None, *, allow_agents=True):
        from .program_values import validate_definition, canonical_hash, validate_cases, cases_hash
        self.budget.tick()
        if operation == 'adopt' and self.program_origin() is None:
            raise LanguageError('Executable runs have no source Program adoption origin', code='program_error')
        if self.recording_scope is not None:
            try:self.mode_host().handle('recording.abort',{'scopeId':self.recording_scope,'reason':'program_effect_not_recordable'})
            except BaseException:pass
            raise LanguageError('Program effects cannot run inside a Skill recording',code='program_error')
        if operation in ('history','revision'):
            expected={'after','limit'}if operation=='history'else{'version'}
            if type(payload)is not dict or set(payload)!=expected:
                raise LanguageError('Invalid Program history operation payload',code='program_error')
            if operation=='history':result=self.store.history.history(self.project['id'],payload['after'],payload['limit'])
            else:
                revision=self.store.history.revision(self.project['id'],payload['version'])
                result={'definition':revision['definition'],'origin':revision['origin']}
            self.budget.tick()
            return result
        fields = {'run':{'definition','input'},'probe':{'definition','cases'},
                  'adopt':{'definition','cases','base','reason'}}
        if operation not in fields or type(payload) is not dict or set(payload) != fields[operation]:
            raise LanguageError('Invalid Program operation payload',code='program_error')
        payload = json_copy(payload,max_chars=1500000,max_nodes=50000)
        allowed = self.scoped_allowed(self.project['capabilities'] if allowed is None else allowed)
        if operation == 'adopt' and 'program.adopt' not in allowed:
            raise LanguageError('Current execution context does not grant program.adopt',code='program_error')
        if self.program_depth >= 4:raise ResourceLimit('Program nesting exceeds four calls')
        with self.services['lock']:
            count = self.services.get('programCalls',0) + 1
            if count > 64:raise ResourceLimit('Program invocation budget exceeds 64 calls')
            self.services['programCalls'] = count
        definition = validate_definition(payload['definition'],checkpoint=self.budget.tick)
        definition_hash = canonical_hash(definition)
        call_id = uuid.uuid4().hex
        evidence = {'callId':call_id,'parentCallId':self.program_call_id,'definitionHash':definition_hash,'operation':operation}
        self.event('program.execution.started',evidence)
        try:
            if operation == 'run':
                child = Context(self.engine,self.run_id,self.project,self.cancel,self.services)
                if self.executable is not None or self.source_program_definition is not None:
                    child.executable = None
                    child.source_program_definition = definition
                child.budget=self.budget;child.workflow_depth=self.workflow_depth
                child.program_depth=self.program_depth+1;child.program_call_id=call_id
                child.team_scope=self.team_scope;child.trace_state=self.trace_state;child.debugger=None
                child.effect_task_key=self.effect_task_key;child.bot_guard=copy.deepcopy(self.bot_guard)
                failure = None
                try:
                    bundle=ProgramBundle(definition['files'][definition['entry']],definition['files'],definition['entry'],
                                         language_versions=definition['languageVersions'],checkpoint=self.budget.tick)
                    result=json_copy(child.evaluator(allowed=allowed,allow_agents=allow_agents).run(bundle,{'input':payload['input']}))
                except BaseException as error:
                    failure=error;raise
                finally:
                    cleanup=None
                    for environment in list(child.tool_environments.values()):
                        try:environment.close()
                        except BaseException as error:
                            if cleanup is None:cleanup=error
                    child.tool_environments.clear()
                    if cleanup is not None and failure is None:raise cleanup
            else:
                cases=validate_cases(payload['cases'],definition)
                checks=[]
                for index,case in enumerate(cases):
                    entry=case.get('entry',definition['entry'])
                    from .evaluation import evaluate as evaluate_fixture, equal as fixture_equal
                    try:
                        bundle=ProgramBundle(definition['files'][entry],definition['files'],entry,
                                             language_versions=definition['languageVersions'],checkpoint=self.budget.tick)
                        evaluated=evaluate_fixture(bundle,case['input'],fixtures=case.get('fixtures'),capabilities=allowed,
                            tool_descriptors=self.engine.tools,cancelled=self.cancel.is_set,
                            timeout=max(.01,self.budget.deadline-time.monotonic()),checkpoint=self.budget.tick)
                        actual=json_copy(evaluated['value'])
                        check={'index':index,'entry':entry,'passed':fixture_equal(actual,case['expect'],self.budget.tick),'actual':actual}
                        if evaluated['fixtures'] is not None:check['fixtures']=evaluated['fixtures']
                    except (ResourceLimit,ExecutionControl):raise
                    except (LanguageError,ValueError) as error:
                        check={'index':index,'entry':entry,'passed':False,'error':self.engine._run_error(error)}
                        if hasattr(error,'fixture_evidence'):check['fixtures']=error.fixture_evidence
                    checks.append(check)
                result={'definitionHash':definition_hash,'casesHash':cases_hash(cases),
                        'passed':all(c['passed'] for c in checks),'checks':checks}
                self.event('program.probe.completed',{**evidence,'passed':result['passed'],'testCount':len(checks),'casesHash':result['casesHash']})
                if operation == 'adopt':
                    base=payload['base'];reason=payload['reason']
                    if (type(base) is not dict or set(base)!={'projectId','version','definitionHash'} or
                            base != self.program_origin()):
                        raise LanguageError('Program adoption base differs from this run snapshot',code='program_error')
                    if type(reason) is not str or len(reason)>1000:
                        raise LanguageError('Program adoption reason must be at most 1000 characters',code='program_error')
                    if not result['passed']:raise LanguageError('Program adoption requires all cases to pass',code='program_error')
                    # cancel()/close() take this same lock. After the transaction
                    # commits, its persisted receipt remains true even if a later
                    # cancellation discards the enclosing run's output.
                    with self.engine.lock:
                        self.budget.tick()
                        result=self.store.adopt_program(self.project['id'],base['version'],definition,base['definitionHash'],result,
                                                        self.engine.settings.redact(reason),self.run_id,self.budget.tick)
            self.event('program.execution.completed',evidence)
            return result
        except BaseException as error:
            try:self.event('program.execution.failed',{**evidence,'errorType':type(error).__name__})
            except BaseException:pass
            raise

    def workflow(self, graph, value, allowed):
        allowed = self.scoped_allowed(allowed)
        if self.workflow_depth>=8:raise ResourceLimit('嵌套工作流超过 8 层')
        graph=json_copy(graph,max_chars=750000)
        project={**self.project,'graph':graph,'capabilities':sorted(allowed),'authoring':'workflow'}
        self.engine.validate_graph_project(project,True)
        child=Context(self.engine,self.run_id,project,self.cancel,self.services)
        child.executable=self.executable;child.source_program_definition=self.source_program_definition
        child.budget=self.budget;child.workflow_depth=self.workflow_depth+1;child.trace_state=self.trace_state;child.tool_environments=self.tool_environments;child.effect_task_key=self.effect_task_key;child.recording_scope=self.recording_scope;child.team_scope=self.team_scope
        child.program_depth=self.program_depth;child.program_call_id=self.program_call_id;child.debugger=self.debugger;child.bot_guard=copy.deepcopy(self.bot_guard)
        return self.engine.execute_graph(child,value)

    def extension_context(self, node_id):
        return ExtensionContext(self.project['id'], self.run_id, node_id, self.budget.deadline,
                                self.budget.tick, self.cancel.is_set)

    def tool(self, name, value, allowed, node_id, *, _computer=None):
        from .bot_host import guarded_tool
        return guarded_tool(self, name, value, allowed, node_id, _computer)

    def _tool_unchecked(self, name, value, allowed, node_id, *, _computer=None):
        allowed = self.scoped_allowed(allowed)
        try:
            self.budget.tick()
            if not isinstance(name, str) or name not in self.engine.tools or name not in allowed:
                raise LanguageError(f'主体 {node_id} 没有工具能力 {name}')
            if name in language_permissions(self.engine.tools):
                raise LanguageError(name+' is a language/API permission, not a callable tool',code='program_error')
            value = json_copy(value)
            if name=='browser.control':
                from .execution import validate_browser_input
                validate_browser_input(value,internal=_computer is not None)
                if _computer is None and any(k.startswith('_')for k in value):raise LanguageError('Private browser fields are not callable tool inputs')
                if self.recording_scope:
                    binding=_computer['id']if _computer is not None else 'legacy'
                    with self.services['lock']:
                        bindings=self.services.setdefault('recordingComputers',{})
                        old=bindings.setdefault(self.recording_scope,binding)
                        if old!=binding:raise LanguageError('A Skill recording can bind only one Computer',code='computer_error')
            descriptor = self.engine.tools[name]
            receipt=self.mode_host().tool_begin(self.recording_scope,name,{k:v for k,v in value.items()if not k.startswith('_')}if name=='browser.control'else value,descriptor.get('version','1')) if self.recording_scope else None
            receipt_finished=False
            self.event('tool.started', {'nodeId': node_id, 'tool': name, 'effect': descriptor['effect']})
        except BaseException:
            if self.recording_scope:
                try:self.mode_host().handle('recording.abort',{'scopeId':self.recording_scope,'reason':'tool_admission_failed'})
                except BaseException:pass
            raise
        try:
            spec = self.engine.custom_tools.get(name)
            if spec:
                check_type(value, spec.input_type)
                result = spec.handler(value, self.extension_context(node_id))
                self.budget.tick()
                result = json_copy(result)
                check_type(result, spec.output_type)
            else:
                result = json_copy(self._builtin_tool(name, value, node_id, _computer=_computer))
            self.budget.tick()
            if receipt is not None:
                receipt_finished=True
                canonical=result.get('replayInput') if name=='browser.control' and not spec and isinstance(result,dict) else None
                self.mode_host().tool_finish(receipt,{k:v for k,v in result.items()if k not in ('_images','image','artifactPath')}if name=='browser.control'else result,replay_input=canonical)
            self.event('tool.completed', {'nodeId': node_id, 'tool': name})
            return result
        except BaseException as e:
            # Receipt/logging failures must never replace a hard control exception.
            if receipt is not None and not receipt_finished:
                try:self.mode_host().tool_finish(receipt,{'error':self.engine._run_error(e)},status='failed')
                except BaseException:pass
            try:self.event('tool.failed', {'nodeId': node_id, 'tool': name, 'error': self.engine._run_error(e)})
            except BaseException:pass
            raise

    def _builtin_tool(self, name, value, node_id='program', *, _computer=None):
        if name == 'text.normalize':
            result = re.sub(r'\s+', ' ', as_text(value)).strip()
        elif name == 'text.stats':
            text = as_text(value)
            result = {'characters': len(text), 'words': len(re.findall(r'\w+', text)), 'lines': len(text.splitlines()), 'sentences': len([s for s in re.split(r'[。！？.!?]+', text) if s.strip()])}
        elif name == 'data.select':
            result = value['record'][value['key']]
        elif name == 'math.evaluate':
            evaluator = Evaluator(timeout=max(.01, self.budget.deadline - time.monotonic()), cancelled=self.cancel.is_set, native_code_cache=self.engine.native_code_cache)
            base_tick = evaluator.tick
            def tick():
                self.budget.tick(); base_tick()
            evaluator.tick = tick
            result = serializable(evaluator.run(as_text(value)))
        elif name == 'memory.read':
            result = self.store.memory_read(self.project['id'], value)
        elif name == 'memory.compare_exchange':
            if type(value) is not dict or set(value) != {'key', 'expectedRevision', 'value'}:
                raise LanguageError('记忆版本提交需要 {key, expectedRevision, value}', code='argument_error')
            result = self.store.memory_compare_exchange(self.project['id'], value['key'], value['expectedRevision'], value['value'])
        elif name in ('memory.get', 'memory.put'):
            key = value if name == 'memory.get' else value['key']
            if not isinstance(key, str) or not 1 <= len(key) <= 128:
                raise LanguageError('记忆键必须为 1–128 字符')
            if name == 'memory.get':
                result = self.store.memory_get(self.project['id'], key)
            else:
                if len(json.dumps(value['value'])) > 100000:
                    raise LanguageError('记忆值过大')
                result = self.store.memory_put(self.project['id'], key, value['value'])
        elif name == 'browser.control':
            from .control import plugin_host
            from .execution import BrowserBroker
            from pathlib import Path
            def checkpoint():
                self.budget.tick()
                if _computer is not None and _computer.get('persistent'):
                    if 'browser.control' not in self.store.project(self.project['id'])['capabilities']:
                        raise LanguageError('Project browser.control grant was revoked',code='computer_error')
                    self.engine.computer_pool.validate(_computer)
                config=self.engine.settings.resolve('', 'executor')
                if not config['browserEnabled']:raise LanguageError('浏览器能力已关闭')
                host=plugin_host(self.engine.settings)
                try:host.require('browser')
                finally:host.close()
            checkpoint()
            config=self.engine.settings.resolve('', 'executor')
            with self.services['lock']:
                if _computer is None and self.services['browser'] is None:
                    self.services['browser']=BrowserBroker(Path(config['workspace'])/self.project['id'],timeout=min(15,config['timeout']))
                broker=_computer['broker']if _computer is not None else self.services['browser']
            captured=broker.call(value,checkpoint=checkpoint,**({'_computer':True}if _computer is not None else {}))
            # Images are bounded binary data, not text to rewrite with string
            # redaction; redact the public textual observation independently.
            images=captured.pop('_images',None)
            result=self.engine.settings.redact(captured)
            if images is not None:
                if _computer is not None:result['_images']=images
                else:result['image']='data:'+images['model']['mimeType']+';base64,'+images['model']['data']
        elif name in ('code.execute','image.generate'):
            from .control import ToolEnvironment
            config=self.engine.settings.resolve('', 'executor')
            config['workspace']=str(__import__('pathlib').Path(config['workspace']) / self.project['id'])
            key=(threading.get_ident(),node_id,self.effect_task_key)
            if key not in self.tool_environments:self.tool_environments[key]=ToolEnvironment(self.engine.settings,config,self.budget.tick,lambda k,p:self.event(k,p))
            result=self.tool_environments[key].call(name,value)
        elif name == 'graph.inspect':
            if self.executable is not None:
                from .runtime_view import snapshot as execution_view
                result = execution_view(self.store, self.project['id'], self.run_id)
            else:
                result = copy.deepcopy(self.project['graph'])
        else:
            raise LanguageError('拓扑修改只能通过平台事务执行')
        return result

    def agent(self, name, config, value, parent_allowed, node_id=None, *, user_message=None):
        from .bot_host import guarded_agent
        return guarded_agent(self, name, config, value, parent_allowed, node_id, user_message)

    def _agent_unchecked(self, name, config, value, parent_allowed, node_id=None, *, user_message=None):
        parent_allowed = self.scoped_allowed(parent_allowed)
        try:
            return json_copy(self._agent(name, json_copy(config), json_copy(value), parent_allowed, node_id,**({'user_message':user_message}if user_message is not None else {})))
        except Exception as e:
            self.event('agent.failed', {'nodeId': node_id or name, 'agent': name, 'error': self.engine._run_error(e)})
            raise

    def _agent(self, name, config, value, parent_allowed, node_id=None, *, user_message=None):
        self.budget.tick()
        node_id = node_id or name
        requested = config.get('tools', [])
        if not isinstance(requested, list) or any(not isinstance(t, str) or t not in self.engine.tools for t in requested):
            raise LanguageError('智能体包含未知工具能力')
        if set(requested).intersection(language_permissions(self.engine.tools)):
            raise LanguageError('Reserved language permissions cannot be requested as Agent tools; use the language effect',code='program_error')
        if not set(requested).issubset(parent_allowed):
            raise LanguageError('智能体请求了当前执行上下文未授予的能力')
        allowed = set(requested) & set(parent_allowed)
        provider = config.get('provider', 'local')
        metadata = {}
        try:
            from .agent_values import definition_hash
            metadata['agentDefinitionHash'] = definition_hash({'kind': 'agent', 'name': name, 'config': config})
        except Exception:
            # Legacy declarations and internal graph controller configuration can
            # exceed the public interchange contract. Optional evidence must not
            # alter their existing invocation behavior or disclose configuration.
            pass
        self.event('agent.observing', {'nodeId': node_id, 'agent': name, 'provider': provider, **metadata})
        if provider == 'local':
            mode = config.get('mode', 'analyze')
            evidence = self.tool('text.stats', value, allowed, node_id) if 'text.stats' in allowed else None
            text = as_text(value)
            if mode == 'review':
                result = {'provider': 'local', 'mode': 'deterministic-demo', 'review': {'nonEmpty': value is not None and value != '', 'status': '结构复核完成；内容正确性需另行验证'}, 'result': value}
            elif mode == 'summarize':
                result = {'provider': 'local', 'mode': 'deterministic-demo', 'summary': text[:600], 'evidence': value}
            else:
                result = {'provider': 'local', 'mode': 'deterministic-demo', 'task': text[:1200], 'statistics': evidence, 'plan': ['明确任务输入和验收条件', '通过授权工具收集结构信息', '汇总结果并交由质量复核'], 'note': '本地规则演示：提示词已保存，语义推理需切换模型服务。'}
            self.budget.tick()
        elif provider == 'model':
            result = self.model_agent(config, value, allowed, node_id,**({'user_message':user_message}if user_message is not None else {}))
        elif provider in self.engine.custom_providers:
            result = self.custom_agent(name, config, value, allowed, node_id)
        else:
            raise LanguageError(f'未知 provider {provider}')
        result = json_copy(result)
        self.budget.tick()
        self.event('agent.completed', {'nodeId': node_id, 'agent': name, 'provider': provider, **metadata})
        return result

    def custom_agent(self, name, config, value, allowed, node_id):
        driver=self.custom_actions(name,config,value,{t:copy.deepcopy(self.engine.tools[t]) for t in sorted(allowed)},node_id)
        try:
            action=next(driver)
            while True:
                try: result=self.tool(action['name'],action['input'],allowed,node_id)
                except BaseException as error:action=driver.throw(error)
                else:action=driver.send(result)
        except StopIteration as result:return result.value
        finally:driver.close()

    def custom_actions(self,name,config,value,descriptors,node_id,context=None):
        context=context or (lambda:self)
        maximum=config.get('maxSteps',4)
        if type(maximum) is not int or not 1<=maximum<=12:raise LanguageError('智能体 maxSteps 必须为 1–12')
        history=[];spec=self.engine.custom_providers[config['provider']]
        for step in range(1,maximum+1):
            host=context();host.budget.tick()
            host.event('agent.thinking',{'nodeId':node_id,'step':step,'maxSteps':maximum})
            request=AgentRequest(name,config.get('prompt',''),json_copy(value),step,maximum,json_copy(descriptors),json_copy(history))
            action=spec.handler(request,host.extension_context(node_id));host.budget.tick()
            if isinstance(action,Final):return json_copy(action.output)
            if not isinstance(action,ToolCall):raise LanguageError('Provider must return Final(output) or ToolCall(name, input)')
            if action.name not in descriptors:raise LanguageError('Provider requested an undeclared tool',code='capability_error')
            argument=json_copy(action.input)
            result=yield {'name':action.name,'input':argument}
            history.append({'tool':action.name,'input':argument,'output':json_copy(result)})
        raise LanguageError('智能体行动预算耗尽，尚未返回最终结果')

    def model_agent(self, config, value, allowed, node_id, *, user_message=None):
        from .harness import HarnessError
        from .control import plugin_host
        settings=self.engine.settings
        resolved=settings.resolve(config.get('route',node_id),config.get('role','planner'if config.get('level')=='platform'else 'executor'))
        if resolved['backend']=='codex':raise LanguageError('Codex 使用独立后台任务运行；Chaos 语言智能体请选择 native，以保持项目工具能力边界')
        if user_message is not None and resolved['backend']!='native':raise LanguageError('ObservedInput requires the native backend',code='computer_error')
        if 'maxSteps'in config:resolved['maxSteps']=config['maxSteps']
        resolved['timeout']=min(resolved['timeout'],max(.1,self.budget.deadline-time.monotonic()))
        tools=[native_tool(name,self.engine.tools[name],mode='wrapped',
            image_output=name=='browser.control',category='browser' if name=='browser.control' else 'code' if name=='code.execute' else 'tools')
            for name in sorted(allowed)]
        proposals=[]
        if config.get('_platformProposal'):
            tools.append({'name':'platform.propose','description':'Submit a proposed topology transaction for host validation. Does not execute the patch.',
                'parameters':{'type':'object','properties':{'reason':{'type':'string'},'operations':{'type':'array','items':{'type':'object'}}},'required':['reason','operations'],'additionalProperties':False}})
        def dispatch(name,arg):
            if name=='platform.propose' and config.get('_platformProposal'):
                proposals.append(json_copy(arg));return {'received':True}
            return self.tool(name,arg,allowed,node_id)
        host=plugin_host(settings,lambda kind,payload:self.event(kind,payload))
        try:
            factory=host.require(resolved['backend'])
            harness=factory(resolved,client_factory=lambda c:settings.client(resolved=c),report_failure=settings.report_failure,report_success=settings.report_success)if resolved['backend']=='native'else factory(resolved)
            self.event('route.selected',{k:resolved[k]for k in ('provider','model','backend','configVersion')})
            result=harness.run(config.get('prompt',''),value,tools=tools,**({'user_message':user_message}if user_message is not None else {}),
                call_tool=dispatch,checkpoint=self.budget.tick,
                event=lambda kind,payload:self.event(kind,{'nodeId':node_id,**settings.redact(payload)}))
            if config.get('_platformProposal'):
                if not proposals:raise LanguageError('平台智能体未提交结构化关系提案')
                return proposals[-1]
            return result
        except (HarnessError,ValueError)as error:raise LanguageError(settings.redact(str(error)))from None
        finally:host.close()


class Engine:
    def __init__(self, store, registry=None, trace_handler=None, settings=None, native_code_cache=DEFAULT_NATIVE_CODE_CACHE):
        if native_code_cache is not None and type(native_code_cache) is not NativeCodeCache:
            raise TypeError('native_code_cache must be NativeCodeCache or None')
        self.native_code_cache = native_code_cache
        from .settings import Settings
        self.settings=settings or Settings(':memory:')
        self.trace_handler=trace_handler
        self.store, self.lock, self.active = store, threading.RLock(), {}
        self.idle = threading.Condition(self.lock)
        self.closing = False
        self.operations = {}
        self.guidance = {}
        self.process_claims = {}
        self.debug_sessions = {}
        self.dispatch_paused = False
        self.dispatcher = None
        self.device_specs=(registry or Registry()).device_snapshot()
        self.robot_specs=(registry or Registry()).robot_snapshot()
        self.computer_specs=(registry or Registry()).computer_snapshot()
        self.device_lock=threading.RLock();self.device_leases={}
        self.custom_tools, self.custom_providers = (registry or Registry()).snapshot()
        self.tools = copy.deepcopy(BUILTIN_TOOLS)
        self.tools.update({name: spec.describe() for name, spec in self.custom_tools.items()})
        with store.lock:
            if store.runtime_owned:
                raise RuntimeError('Store already belongs to a runtime')
            self.lease = RuntimeLease(store.path)
            store.runtime_owned = True
            try:
                self.store.recover()
            except Exception:
                store.runtime_owned = False
                self.lease.close()
                raise
        from .computer_pool import ComputerPool
        self.computer_pool=ComputerPool(self)
        try:
            with self.lock:
                if self.store.processes.has_waiting():self._ensure_dispatcher()
        except BaseException:
            self.store.runtime_owned=False
            self.lease.close()
            raise

    def _ensure_dispatcher(self):
        # Engine.lock is held by callers. There is only one admission loop.
        if self.dispatcher is None and not self.closing:
            self.dispatcher=threading.Thread(target=self._dispatch_processes,name='chaos-process-dispatch',daemon=True)
            try:self.dispatcher.start()
            except BaseException:
                self.dispatcher=None
                raise

    def pause_dispatch(self, paused=True):
        if type(paused) is not bool:raise ValueError('paused must be Bool')
        with self.idle:
            self.dispatch_paused=paused
            self.idle.notify_all()

    def _dispatch_processes(self):
        while True:
            with self.idle:
                if self.closing:return
                if not self.dispatch_paused:
                    for project_id,key in self.store.processes.candidates():
                        if self.closing or self.dispatch_paused:break
                        if len(self.active)>=self.settings.runtime()['maxConcurrent']:break
                        if any(project_id==pid for _,pid in self.active.values()):continue
                        run_id=uuid.uuid4().hex[:16];cancel=threading.Event();claim=None
                        try:
                            claim=self.store.processes.claim(project_id,key,run_id)
                            if claim is None:continue
                            self.active[run_id]=(cancel,project_id)
                            self.process_claims[run_id]=claim
                            self._open_guidance(run_id)
                            thread=threading.Thread(target=self._run,args=(run_id,claim['project'],claim['input'],'source',cancel,False,claim),daemon=True)
                            thread.start()
                        except BaseException as error:
                            try:
                                if claim is not None:
                                    # No step ran. A transient diagnostic failure must not kill admission.
                                    if not self._fail_process_turn(claim,error):self._fail_process_turn(claim,error)
                                else:
                                    # Admission has no effects. Persist a visible pause instead of a retry loop.
                                    try:
                                        with self.store.write():
                                            row=self.store.processes._read(project_id,key)
                                            row['status']='paused';row['control']='paused';row['error']=self._run_error(error)
                                            self.store.processes._write(row,'process.admission.failed')
                                    except BaseException:pass  # Unwritable storage retains its previous durable state.
                            finally:
                                self.active.pop(run_id,None)
                                self.process_claims.pop(run_id,None)
                                self.guidance.pop(run_id,None)
                                self.idle.notify_all()
                self.idle.wait(.1)

    def _fail_process_turn(self, claim, error):
        # Never retry an exported function or any effect. Only its failed terminal receipt.
        try:
            message=self._run_error(error)
            self.store.transition_run(claim['pending']['runId'],'failed',
                {'version':claim['pending']['version'],'error':message,'phase':'process_terminal_persistence'},
                error=message,_process=claim)
            return True
        except BaseException:
            # If storage stays unavailable, the original pending lease survives for recover().
            return False

    def session_operation(self, project_id, operation, payload, *, allowed=None, version=None, checkpoint=None):
        fields={'open':{'key','agent','contextLimit'},'load':{'key'},'status':{'key'},'history':{'key','after','limit'},
            'fork':{'key','revision','newKey','agent'},'compact':{'key','revision','keepTurns'},'close':{'key','revision'},'reconcile':{'key','revision','evidence'}}
        if operation not in fields or type(payload)is not dict or set(payload)!=fields[operation]:raise LanguageError('Invalid Session operation payload',code='session_error')
        with self.idle:
            if self.closing:raise RuntimeError('Runtime is closing')
            if checkpoint is not None:checkpoint()
            project=self.store.project(project_id);allowed=set(project['capabilities'] if allowed is None else allowed)
            sessions=self.store.sessions;key=payload['key']
            if operation=='status':return sessions.get(project_id,key)
            if operation=='load':return sessions.get(project_id,key,allowed)
            if operation=='history':return sessions.history(project_id,key,allowed,payload['after'],payload['limit'])
            if operation in ('open','fork') and payload['agent'] is not None:
                definition=sessions.definition(payload['agent'])
                if not set(definition['config'].get('tools',[])).issubset(self.tools):raise LanguageError('Session Agent contains unknown tools',code='session_error')
            if operation=='open':return sessions.create(project_id,version,key,self.settings.redact(payload['agent']),payload['contextLimit'],allowed)
            if operation=='fork':return sessions.fork(project_id,key,payload['revision'],payload['newKey'],self.settings.redact(payload['agent']),allowed)
            body=dict(payload)
            if operation=='reconcile':body['evidence']=self.settings.redact(body['evidence'])
            return sessions.control(project_id,key,payload['revision'],operation,body,allowed)

    def start_session_turn(self,project_id,version,key,revision,value,message_id=None):
        # Validate the identity before starting an ordinary Engine run; claim/CAS still occurs in the worker.
        project=self.store.project(project_id)
        self.store.sessions.preflight(project_id,key,revision,value,message_id,project['capabilities'])
        version_number(revision)
        payload={'key':key,'revision':revision,'input':value,'messageId':message_id}
        return self.start(project_id,version,payload,mode='source',auto=False,_session=payload)

    def process_operation(self, project_id, operation, payload, *, allowed=None, version=None, checkpoint=None):
        fields={'start':{'key','definition','state'},'send':{'key','input','messageId'},'guide':{'key','input','messageId'},'status':{'key'},
                'events':{'key','after','limit'},'guidance':{'key'},'message':{'key','messageId'},
                'control':{'key','revision','action'},'configure':{'key','revision','definition'},
                'reconcile':{'key','revision','state','evidence','resume'}}
        expected = (fields.get(operation),)
        if operation == 'guide': expected += (fields[operation] | {'target'},)
        if operation not in fields or type(payload) is not dict or set(payload) not in expected:
            raise LanguageError('Invalid Process operation payload',code='process_error')
        if operation == 'guide' and 'target' in payload:
            from .guidance_targets import validate_target
            target = validate_target(payload['target'])
            if target['kind'] != 'processRole':
                raise Conflict('A Process accepts persistent role targets, not ephemeral scopes')
        payload=json_copy(payload,max_chars=100000)
        if operation in ('start','reconcile'):
            from .process_store import data as process_data
            process_data(payload['state'])
        if operation=='reconcile' and (type(payload['evidence']) is not str or len(payload['evidence'])>1000):
            raise ValueError('Reconciliation evidence must be a String of at most 1000 characters')
        with self.idle:
            if self.closing:raise RuntimeError('Runtime is closing')
            if checkpoint is not None:checkpoint()
            project=self.store.project(project_id)
            allowed=set(project['capabilities'] if allowed is None else allowed)
            processes=self.store.processes;key=payload['key']
            if operation=='status':return processes.get(project_id,key)
            if operation=='message':return self.settings.redact(processes.message(project_id,key,payload['messageId']))
            if operation=='guidance':return self._process_guidance_roles(project_id,key)
            if operation=='events':
                page=self.store.event_page(project_id,payload['after'],payload['limit'],process_key=key)
                return self.settings.redact(page)
            if operation=='start':
                self._ensure_dispatcher()
                result=processes.create(project_id,version,key,payload['definition'],self.settings.redact(payload['state']),allowed)
            elif operation=='send':result=processes.send(project_id,key,payload['input'],payload['messageId'],allowed,self.settings.redact)
            elif operation=='guide':
                active_claim=next((claim for claim in self.process_claims.values() if claim['project']['id']==project_id and claim['key']==key),None)
                run_id=active_claim['pending']['runId'] if active_claim is not None else None
                if run_id is not None and self.active[run_id][0].is_set():raise Conflict('Process is cancelling; guidance was not accepted')
                result=processes.guide(project_id,key,payload['input'],payload['messageId'],allowed,self.settings.redact,run_id=run_id,**({'target':payload['target']} if 'target' in payload else {}))
                if active_claim is not None:self._sync_process_guidance(active_claim)
            elif operation=='control':
                result=processes.control(project_id,key,payload['revision'],payload['action'],allowed)
                if payload['action']=='stop' and result['pending'] is not None:
                    run_id=result['pending']['runId']
                    if run_id in self.active:self.active[run_id][0].set()
            elif operation=='configure':result=processes.configure(project_id,key,payload['revision'],payload['definition'],allowed)
            else:result=processes.reconcile(project_id,key,payload['revision'],self.settings.redact(payload['state']),
                    self.settings.redact(payload['evidence']),payload['resume'],allowed)
            self._ensure_dispatcher();self.idle.notify_all()
            return result

    def _process_guidance_roles(self, project_id, key):
        """Persistent registry, joined only with this host's actual live bindings."""
        result = self.store.processes.role_registry(project_id, key)
        scopes = {}
        for run_id, claim in self.process_claims.items():
            if (claim['project']['id'] != project_id or claim['key'] != key or
                    run_id not in self.active or self.active[run_id][0].is_set()):
                continue
            for scope in self.guidance.get(run_id, {}).get('botScopes', {}).values():
                if scope['active'] and not scope['terminal'] and scope.get('processRoleId'):
                    scopes[scope['processRoleId']] = scope['id']
        for role in result['roles']:
            role['activeScopeId'] = scopes.get(role['roleId'])
        return result

    def process_bot_operation(self,claim,operation,payload):
        with self.idle:
            run_id=claim['pending']['runId']
            if self.process_claims.get(run_id) is not claim or run_id not in self.active:
                raise LanguageError('Process Bot has no active private turn lease',code='ownership_error')
            return self.store.processes.bot_operation(claim,operation,payload,self.settings.redact)

    def close(self, timeout=5):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.idle:
            self.closing = True
            self.idle.notify_all()
            for cancel, _ in self.active.values():
                cancel.set()
            for debug in self.debug_sessions.values():
                debug.wake()
            for cancel in self.operations.values():
                cancel.set()
            while self.active or self.operations:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError('Callbacks are still running; storage remains open, call close again after they finish')
                self.idle.wait(remaining)
        if self.dispatcher is not None and self.dispatcher is not threading.current_thread():
            remaining=None if deadline is None else max(0,deadline-time.monotonic())
            self.dispatcher.join(remaining)
            if self.dispatcher.is_alive():raise TimeoutError('Process admission has not stopped; storage remains open')
        self.computer_pool.close(deadline)
        self.store.runtime_owned = False
        self.lease.close()

    def validate_project(self, project, complete=True):
        bundle=ProgramBundle(project['source'],project.get('files'),project.get('entry','main.chaos'),language_versions=project_language_versions(project))
        if project.get('authoring')=='source':return bundle.summary()
        return self.validate_graph_project(project,complete)

    def adopt_program(self, project_id, version, definition, expected_hash, cases, reason=''):
        version_number(version)
        operation=uuid.uuid4().hex;cancel=threading.Event();ctx=None;failure=None
        with self.lock:
            if self.closing:raise RuntimeError('Runtime is closing')
            self.operations[operation]=cancel
        try:
            project=self.store.project(project_id)
            if project['version'] != version:raise Conflict('Program version changed; reload before adopting')
            ctx=Context(self,None,project,cancel)
            if expected_hash != ctx.program_origin()['definitionHash']:
                raise Conflict('Program source hash changed; reload before adopting')
            return ctx.program_operation('adopt',{'definition':definition,'cases':cases,
                'base':{'projectId':project_id,'version':version,'definitionHash':expected_hash},'reason':reason})
        except BaseException as error:
            failure=error;raise
        finally:
            try:
                if ctx is not None:
                    try:ctx.close_services('cancelled' if cancel.is_set() else 'failed' if failure else 'completed')
                    except BaseException:
                        if failure is None:raise
            finally:
                with self.idle:
                    self.operations.pop(operation,None);self.idle.notify_all()

    def validate_graph_project(self, project, complete=True):
        certificate = validate(project['graph'], complete)
        granted = set(project.get('capabilities', DEFAULT_CAPABILITIES))
        if not granted.issubset(set(self.tools) - {'graph.patch'}):
            raise LanguageError('项目引用了未注册或保留的工具能力')
        for node in project['graph']['nodes']:
            config = node.get('config', {})
            if not executable(node):
                continue
            requested = {config.get('tool', 'text.normalize')} if node['kind'] == 'tool' else set(config.get('tools', []))
            if not requested.issubset(granted):
                raise LanguageError(f"节点 {node['id']} 请求了项目未授予的工具能力")
            if node['kind'] in ('tool', 'agent') and requested & language_permissions(self.tools):
                raise LanguageError(f"节点 {node['id']} 把语言专属权限用作可调用工具；请在函数中调用对应原生效果", code='program_error')
            if node['kind'] == 'agent' and config.get('provider', 'local') not in {'local', 'model', *self.custom_providers}:
                raise LanguageError(f"节点 {node['id']} 引用了未注册的 provider")
        return certificate

    def optimize(self, project_id, version, cancel=None):
        version_number(version)
        operation = uuid.uuid4().hex
        cancel = cancel or threading.Event()
        with self.lock:
            if self.closing:
                raise RuntimeError('Runtime is closing')
            self.operations[operation] = cancel
        try:
            return self._optimize(project_id, version, cancel)
        finally:
            with self.idle:
                self.operations.pop(operation, None)
                self.idle.notify_all()

    def _optimize(self, project_id, version, cancel):
        project = self.store.project(project_id)
        if project['version'] != version:
            raise Conflict('版本冲突，请重新载入')
        if project.get('authoring')=='source':return {'project':project,'changed':False,'reason':'当前为函数程序，无顶层工作流关系'}
        graph = project['graph']
        controllers = [n for n in graph['nodes'] if level(n) == 3 and n.get('config', {}).get('policy') in ('ensure-review', 'model-plan')]
        if not controllers:
            return {'changed': False, 'project': project, 'reason': '没有启用自动复核策略的平台智能体'}
        controller = controllers[0]
        if 'graph.patch' not in controller.get('config', {}).get('tools', []):
            raise GraphError('平台智能体缺少 graph.patch 能力')
        if controller['config'].get('policy') == 'model-plan':
            if controller['config'].get('provider') != 'model':
                raise GraphError('模型拓扑策略需要 provider=model')
            ctx = Context(self, None, project, cancel)
            config = copy.deepcopy(controller['config'])
            config['_platformProposal']=True
            config['tools'] = ['graph.inspect'] if 'graph.inspect' in config.get('tools', []) else []
            config['prompt'] = config.get('prompt', '') + '\nSubmit a graph revision by calling the platform.propose tool. Allowed operations: add_node {node}, remove_node {id}, update_node {id,changes}, add_edge {edge}, remove_edge {id}. Each operation includes op. Preserve one input/output and complete DAG paths. Only modify descendants of your platform id. New nodes must have parentId equal to your id and level below platform. Existing config/identity cannot change. Do not execute patches yourself.'
            ctx.event('platform.proposing', {'nodeId': controller['id'], 'provider': 'model'})
            proposal = ctx.agent(controller['id'], config, {'platformId': controller['id'], 'graph': graph}, {'graph.inspect'}, controller['id'])
            if not isinstance(proposal, dict) or not isinstance(proposal.get('operations'), list) or not isinstance(proposal.get('reason'), str):
                raise GraphError('模型必须返回 {reason, operations} 拓扑提案')
            reason = proposal['reason'][:1000]
            if not proposal['operations']:
                return {'changed': False, 'project': project, 'reason': reason or '模型未建议修改'}
            updated = self.store.patch(project_id, version, proposal['operations'], controller['id'], reason)
            return {'changed': True, 'project': updated, 'reason': reason}
        sinks = [n for n in graph['nodes'] if n['kind'] == 'output']
        if len(sinks) != 1:
            raise GraphError('自动策略需要唯一输出')
        sink = sinks[0]
        incoming = [e for e in graph['edges'] if e['target'] == sink['id']]
        if len(incoming) != 1:
            raise GraphError('自动复核策略要求输出节点恰好一个输入')
        edge = incoming[0]
        predecessor = next(n for n in graph['nodes'] if n['id'] == edge['source'])
        if predecessor.get('config', {}).get('mode') == 'review':
            return {'changed': False, 'project': project, 'reason': '输出路径已包含复核节点'}
        reviewer_id = 'review_' + uuid.uuid4().hex[:6]
        reviewer = {'id': reviewer_id, 'kind': 'agent', 'label': '质量复核智能体', 'x': sink.get('x', 0), 'y': sink.get('y', 0), 'parentId': controller['id'], 'config': {'level': 'worker', 'provider': 'local', 'mode': 'review', 'prompt': '对交付结果进行结构复核。', 'tools': []}}
        operations = [
            {'op': 'remove_edge', 'id': edge['id']}, {'op': 'add_node', 'node': reviewer},
            {'op': 'add_edge', 'edge': {'id': reviewer_id + '_in', 'source': edge['source'], 'target': reviewer_id, 'sourcePort': 'value', 'targetPort': 'input'}},
            {'op': 'add_edge', 'edge': {'id': reviewer_id + '_out', 'source': reviewer_id, 'target': sink['id'], 'sourcePort': 'value', 'targetPort': edge.get('targetPort', 'input')}},
            {'op': 'update_node', 'id': sink['id'], 'changes': {'x': sink.get('x', 0) + 265}},
        ]
        reason = '平台策略 ensure-review 检测到输出缺少复核，自动插入质量复核节点'
        updated = self.store.patch(project_id, version, operations, controller['id'], reason)
        return {'changed': True, 'project': updated, 'reason': reason}

    def start_executable(self, project_id, version, image, value, trace=False):
        from .executable_runs import freeze_image
        execution = freeze_image(image)
        return self.start(project_id, version, value, mode='executable', auto=False,
                          trace=trace, _executable=execution)

    def start(self, project_id, version, value, mode='graph', auto=True, trace=False, debug=None, *, _session=None, _executable=None):
        version_number(version)
        if type(trace)is not bool:raise ValueError('trace must be a boolean')
        if type(auto) is not bool:
            raise ValueError('autoOptimize must be a boolean')
        value = json_copy(value, max_chars=100000)
        if _executable is not None:
            from .executable_runs import load_execution
            if mode != 'executable' or auto or debug is not None or _session is not None:
                raise ValueError('Executable runs cannot use source debugging, optimization or session admission')
            load_execution(_executable)
        elif mode not in ('graph', 'source'):
            raise ValueError('mode 必须为 graph 或 source')
        if len(json.dumps(value)) > 100000:
            raise ValueError('运行输入过大')
        run_id, cancel = uuid.uuid4().hex[:16], threading.Event()
        with self.lock:
            if self.closing:
                raise RuntimeError('Runtime is closing')
            if len(self.active) >= self.settings.runtime()['maxConcurrent'] or any(p == project_id for _, p in self.active.values()):
                raise Conflict('当前项目已有运行；请等待或取消')
            project = self.store.project(project_id)
            if version != project['version']:
                raise Conflict('运行版本已过期，请重新载入')
            config = debug_options(debug, project['files'])
            if config is not None and (mode != 'source' or auto):
                raise ValueError('调试需要 mode=source 且 autoOptimize=false，以固定执行版本')
            self.active[run_id] = (cancel, project_id)
            self._open_guidance(run_id)
        try:
            if _session is None and _executable is None:self.validate_project(project, True)
            if _session is None and _executable is None and project.get('authoring') == 'workflow':
                self.validate_project(project, True)
                if auto:
                    project = self.optimize(project_id, version, cancel)['project']
                self.validate_project(project, True)
            if _session is not None:mode='session'
            if _executable is not None:
                # The caller's image has been copied and validated; retain the
                # original authority source distinctly in the same run snapshot.
                project = {**project, 'execution': _executable}
            self.store.create_run(run_id, project, mode, value, _check_authority=_executable is not None)
            if config is not None:
                with self.lock:
                    while len(self.debug_sessions) >= 64:
                        ended = next((key for key in self.debug_sessions if key not in self.active), None)
                        if ended is None: break
                        self.debug_sessions.pop(ended)
                    self.debug_sessions[run_id] = DebugSession(run_id, project, config, cancel,
                        lambda kind,payload:self.store.event(project_id,kind,self.settings.redact({'version':project['version'],**payload}),run_id),self.settings.redact)
                self.store.event(project_id, 'debug.started', {'version':project['version'], **config}, run_id)
            thread = threading.Thread(target=self._run, args=(run_id, project, value, mode, cancel, trace, None, _session), daemon=True)
            thread.start()
            return self.store.run(run_id)
        except BaseException as error:
            try:
                if _session is not None or _executable is not None:
                    try:self.store.transition_run(run_id,'failed',{'error':self._run_error(error),'phase':'executable_admission' if _executable is not None else 'session_admission'},error=self._run_error(error))
                    except BaseException:pass
            finally:
                with self.lock:
                    self.active.pop(run_id, None)
                    self.guidance.pop(run_id, None)
                    self.debug_sessions.pop(run_id, None)
                    self.idle.notify_all()
            raise

    def cancel(self, run_id):
        with self.lock:
            self.store.run(run_id)
            if run_id in self.active:
                self.active[run_id][0].set()
                if run_id in self.debug_sessions:self.debug_sessions[run_id].wake()
                return True
            return False

    def _open_guidance(self,run_id):
        self.guidance[run_id]={'revision':0,'queue':[],'messages':{},'progressCount':0}
        if run_id in self.process_claims:self._sync_process_guidance(self.process_claims[run_id])

    def _sync_process_guidance(self,claim):
        run_id=claim['pending']['runId'];box=self.guidance[run_id]
        durable=self.store.processes.guidance_box(claim)
        box['revision']=durable['revision']
        for item in durable['messages']:
            message_id=item['receipt']['messageId']
            if message_id in box['messages']:continue
            if len(box['messages'])>=256:raise Conflict('Run guidance identity budget exhausted')
            receipt={key:item['receipt'][key] for key in ('messageId','revision','status')}
            receipt.update(runId=run_id,status='queued')
            box['messages'][message_id]={'hash':item['hash'],'receipt':receipt,'input':item['input'],'durable':True}
            box['queue'].append(message_id)

        # Only host-owned active scopes can receive the durable role projection.
        # Original receipts remain in SQLite; this is the current delivery view.
        for scope in box.get('botScopes', {}).values():
            role_id = scope.get('processRoleId')
            if not role_id or not scope['active'] or scope['terminal']:
                continue
            role = self.store.processes.role_box(claim, role_id=role_id)
            mail = scope['mailbox']; mail['revision'] = role['revision']
            for item in role['messages']:
                message_id = item['receipt']['messageId']
                if message_id in mail['messages']:
                    continue
                receipt = {key: item['receipt'][key] for key in ('messageId', 'revision')}
                receipt.update(runId=run_id, status='queued', target={'kind':'processRole','roleId':role_id})
                mail['messages'][message_id] = {'hash':item['hash'], 'receipt':receipt,
                                                'input':copy.deepcopy(item['input']), 'durable':True}
                mail['queue'].append(message_id)

    def guidance_scopes(self, run_id):
        """Read actual active native invocations, independent of event retention."""
        with self.idle:
            self.store.run(run_id)
            active = (not self.closing and run_id in self.active and
                      not self.active[run_id][0].is_set())
            rows = []
            if active:
                for scope in self.guidance[run_id].get('botScopes', {}).values():
                    if not scope['active']:
                        continue
                    role_id = scope.get('processRoleId')
                    addressable = (scope['mailboxKind'] == 'scoped' and not scope['terminal'] and
                                   (run_id not in self.process_claims or role_id is not None))
                    target = ({'kind':'processRole','roleId':role_id} if role_id else
                              {'kind':'botScope','scopeId':scope['id']}) if addressable else None
                    descriptor = {'scopeId':scope['id'], 'botId':scope['botId'],
                                  'parentScopeId':scope['parentScopeId'], 'rootScopeId':scope['rootScopeId'],
                                  'mailbox':scope['mailboxKind'], 'revision':scope['mailbox']['revision'],
                                  'terminal':scope['terminal'], 'addressable':bool(addressable), 'target':target}
                    if role_id:
                        descriptor.update(roleId=role_id, roleKey=scope['roleKey'], processKey=scope['processKey'])
                    rows.append(descriptor)
            return {'runId': run_id, 'active': bool(active), 'scopes': rows}

    def guide(self,run_id,value,message_id=None,*,target=None):
        from .guidance_targets import validate_target, private_mailbox, totals
        target = None if target is None else validate_target(target)
        value=json_copy(value,max_chars=40000,max_nodes=4096,max_depth=32)
        encoded=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        if len(encoded)>40000:raise ValueError('Guidance JSON exceeds 40000 characters')
        if message_id is None:message_id=uuid.uuid4().hex
        if type(message_id)is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',message_id):
            raise ValueError('messageId must be a 1–128 character identifier')
        import hashlib
        digest=hashlib.sha256(encoded.encode()).hexdigest()
        with self.idle:
            run=self.store.run(run_id)
            if self.closing or run_id not in self.active or self.active[run_id][0].is_set():
                raise Conflict('Run is closed or cancelling; guidance was not accepted')
            run_box=self.guidance[run_id]
            claim=self.process_claims.get(run_id)
            if claim is not None:
                if target is not None and target['kind'] != 'processRole':
                    raise Conflict('Process guidance requires a persistent role address')
                receipt=self.store.processes.guide(run['project_id'],claim['key'],value,message_id,run['snapshot']['capabilities'],self.settings.redact,run_id=run_id,**({'target':target} if target is not None else {}))
                self._sync_process_guidance(claim);self.idle.notify_all()
                if target is not None:return receipt
                return {key:(run_id if key=='runId' else receipt[key]) for key in ('runId','messageId','revision','status')}
            if target is None:
                box, scope = run_box, run_box.get('botScope')
            else:
                box, scope = private_mailbox(run_box, target)
            previous=box['messages'].get(message_id)
            if previous is not None:
                if previous['hash']!=digest:raise Conflict('messageId already has different input in this mailbox')
                return dict(previous['receipt'])
            if scope is not None and scope['terminal']:
                raise Conflict('Bot scope has committed a terminal result; new guidance was not accepted')
            identities, queued = totals(run_box)
            if queued>=64 or identities>=256:raise Conflict('Run guidance queue or identity budget exhausted')
            revision=box['revision']+1
            receipt={'runId':run_id,'messageId':message_id,'revision':revision,'status':'queued'}
            if target is not None:receipt['target']=target
            self.store.event(run['project_id'],'guidance.queued',{'version':run['version'],**receipt},run_id)
            box['revision']=revision
            box['messages'][message_id]={'hash':digest,'receipt':receipt,'input':value}
            box['queue'].append(message_id)
            self.idle.notify_all()
            return dict(receipt)

    def receive_guidance(self,ctx):
        with self.idle:
            ctx.budget.tick()
            box=self.guidance.get(ctx.run_id)
            if box is None:return {'received':False,'message':None,'closed':True}
            if box.get('botScope') is not None:
                raise LanguageError('Run guidance belongs to an active Bot',code='ownership_error')
            if not box['queue']:return {'received':False,'message':None,'closed':False}
            message_id=box['queue'][0];record=box['messages'][message_id]
            receipt={**record['receipt'],'status':'received'}
            if record.get('durable'):
                self.store.processes.receive_guide(ctx.process_claim,message_id,receipt['revision'],receipt)
            # A failed journal write leaves the message queued; cancellation was
            # checked under the same admission/terminal lock before this commit.
            if not record.get('durable'):ctx.event('guidance.received',receipt)
            box['queue'].pop(0);record['receipt']=receipt
            value=record.pop('input')
            return {'received':True,'message':{**receipt,'input':value},'closed':False}

    def debugger(self, run_id):
        with self.lock:
            run = self.store.run(run_id)
            session = self.debug_sessions.get(run_id)
        if session is None:
            raise ValueError('调试会话不可用：运行未启用调试、已被清理或服务已重启；历史运行状态为 '+run['status'])
        return session.snapshot()

    def debug_command(self, run_id, command, *, pause_id=None, breakpoints=None, restart_id=None, args=None, kwargs=None):
        with self.lock:
            self.store.run(run_id)
            session = self.debug_sessions.get(run_id)
        if session is None: raise ValueError('调试会话不可用；请重新启动调试')
        if command == 'cancel':
            self.cancel(run_id)
            return session.snapshot()
        return session.command(command, pause_id=pause_id, points=breakpoints,restart_id=restart_id,args=args,kwargs=kwargs)

    def _run_error(self, exception):
        # Host callbacks may raise exceptions whose __str__ itself fails. Never
        # let diagnostics prevent terminalization, or truncate a credential
        # before the redactor has had a chance to recognize the complete key.
        try:
            message = str(exception) or type(exception).__name__
        except BaseException:
            message = type(exception).__name__
        try:
            return self.settings.redact(message)[:1000]
        except BaseException:
            return type(exception).__name__

    def _prepare_process_evolution(self,ctx,claim,decision):
        from .process_values import evolution_owned, validate_binding, _EvolutionCommit, _canonical
        decision=evolution_owned(decision,decision.owner,finished=True)
        if decision.claim is not claim: raise LanguageError('Process evolution claim differs',code='ownership_error')
        payload=decision.payload
        current=self.store.project(ctx.project['id'])
        allowed=set(decision.capabilities)&set(claim['pending']['effectiveCapabilities'])&set(current['capabilities'])
        if not {'process.manage','program.adopt'} <= allowed:
            raise LanguageError('Process evolution authority was revoked',code='capability_error')
        definition=payload['definition']
        bundle=ProgramBundle(definition['files'][definition['entry']],definition['files'],definition['entry'],
                             language_versions=definition['languageVersions'],checkpoint=ctx.budget.tick)
        validate_binding(bundle,claim['definition'])
        report=ctx.program_operation('probe',{'definition':definition,'cases':payload['cases']},allowed)
        if not report['passed']: raise LanguageError('Process evolution requires all cases to pass',code='process_error')
        # Compilation, test execution and redaction happen before the terminal
        # transaction. Only validated immutable snapshots reach SQLite below.
        state=self.store.program_state(ctx.project['graph'],definition['files'][definition['entry']],definition['files'],
                                       definition['entry'],'source',definition['languageVersions'],checkpoint=ctx.budget.tick)
        base=payload['base']
        receipt={'previousVersion':base['version'],'version':base['version']+1,
                 'previousDefinitionHash':base['definitionHash'],'definitionHash':report['definitionHash'],
                 'casesHash':report['casesHash'],'testCount':len(report['checks']),
                 'runId':ctx.run_id,'reason':self.settings.redact(payload['reason'])}
        return _EvolutionCommit(decision,_canonical(state),_canonical(receipt),ctx.budget.deadline)

    def _run(self, run_id, project, value, mode, cancel, trace=False, process=None, session=None):
        ctx = None
        evolution = None
        evolution_commit = None
        status, output, error = 'completed', None, None
        provenance = {}
        try:
            provenance = ({'executionKind': 'executable', 'imageHash': project['execution']['imageHash']}
                          if mode == 'executable' else {})
            self.store.transition_run(run_id, 'running', {'version': project['version'], 'mode': mode, **provenance})
            ctx = Context(self, run_id, project, cancel)
            if trace:ctx.trace_state={'remaining':200,'lock':threading.Lock()}
            if mode == 'executable':
                from .executable_runs import load_execution
                bundle = load_execution(project['execution'])
            else:
                bundle=ProgramBundle(project['source'],project.get('files'),project.get('entry','main.chaos'),language_versions=project_language_versions(project))
            if session is not None:
                output=ctx.session_operation('turn',session)['receipt']
            elif process is not None:
                from .process_values import validate_step, validate_result, ProcessEvolution
                evaluator=ctx.evaluator(allowed=process['pending']['effectiveCapabilities'],process_claim=process)
                result=validate_result(evaluator.run_export(bundle,process['definition']['module'],process['definition']['export'],value),evaluator,finished=True)
                if type(result) is ProcessEvolution:
                    evolution=result;output=result.payload['step']
                else:output=result
                output=validate_step(self.settings.redact(output))
            else:
                output = serializable(ctx.evaluator().run(bundle, {'input': value}))
            ctx.budget.tick()
            output = json_copy(output)
            json.dumps(output, ensure_ascii=False, allow_nan=False)
        except Cancelled:
            status, output, error = 'cancelled', None, '执行已取消'
        except BaseException as exception:
            # This is the background-worker containment boundary, not a handler
            # for main-thread interrupts. SystemExit/CancelledError from a host
            # extension must not leave a persistent run looking alive forever.
            status, output, error = 'failed', None, self._run_error(exception)
        finally:
            computers=ctx.services.get('computers') if ctx is not None else None
            if ctx is not None:
                for env in list(ctx.tool_environments.values()):
                    try:
                        env.close()
                    except BaseException as exception:
                        # Try every disposer and preserve the original failure.
                        if status == 'completed':
                            status, output = 'failed', None
                            error = self._run_error(exception)
                try:ctx.close_services('cancelled' if cancel.is_set() else status)
                except BaseException as exception:
                    if status=='completed':status,output,error='failed',None,self._run_error(exception)
            if status=='completed' and evolution is not None:
                try:
                    ctx.budget.tick()
                    evolution_commit=self._prepare_process_evolution(ctx,process,evolution)
                except Cancelled:
                    status,output,error='cancelled',None,'执行已取消'
                except BaseException as exception:
                    status,output,error='failed',None,self._run_error(exception)
            # cancel() uses the same lock. A cancellation that wins this lock
            # discards output; a terminal commit that wins removes active before
            # cancel() can report acceptance. Callbacks/cleanup stay outside it.
            cancellation_drained=False
            while True:
                with self.idle:
                    needs_drain=cancel.is_set() and computers is not None and not cancellation_drained
                    if not needs_drain:
                        try:
                            if cancel.is_set():
                                status, output, error = 'cancelled', None, '执行已取消'
                            if run_id in self.debug_sessions:self.debug_sessions[run_id].finish(status,error)
                            try:
                                if status=='completed' and evolution_commit is not None:ctx.budget.tick()
                                self.store.transition_run(run_id, status,
                                    {'version': project['version'], 'output': output, 'error': error,
                                     'steps': ctx.budget.steps if ctx is not None else 0, **provenance}, output, error, _process=process,
                                    _evolution=evolution_commit if status=='completed' else None)
                            except BaseException as terminal_error:
                                if process is None:raise
                                self._fail_process_turn(process,terminal_error)
                        finally:
                            self.active.pop(run_id, None)
                            self.process_claims.pop(run_id, None)
                            self.guidance.pop(run_id, None)
                            self.idle.notify_all()

                        break
                # A cancellation accepted after ordinary resource release must
                # still retire this run's idle generation before terminal commit.
                # Never block unrelated Engine operations while closing drivers.
                try:computers.close('cancelled')
                except BaseException as cleanup_error:
                    error=self._run_error(cleanup_error)
                cancellation_drained=True

    def execute_node(self, ctx, node, value):
        ctx.budget.tick()
        kind, config, nid = node['kind'], node.get('config', {}), node['id']
        failed = False
        try:
            if kind in ('input', 'output'):return value
            if kind == 'tool':return ctx.tool(config.get('tool', 'text.normalize'), value, set(ctx.project.get('capabilities', DEFAULT_CAPABILITIES)), nid)
            if kind == 'function':
                bundle=ProgramBundle(config.get('source','input'),ctx.project.get('files'),ctx.project.get('entry','main.chaos'),language_versions=project_language_versions(ctx.project))
                return serializable(ctx.evaluator(set(config.get('tools', [])) & set(ctx.project.get('capabilities', DEFAULT_CAPABILITIES)), nid, allow_agents=False).run(bundle, {'input':value}))
            if kind == 'agent':return ctx.agent(nid, config, value, set(ctx.project.get('capabilities', DEFAULT_CAPABILITIES)), nid)
            raise GraphError('未知执行节点')
        except BaseException:
            failed = True
            raise
        finally:
            env=ctx.tool_environments.pop((threading.get_ident(),nid,ctx.effect_task_key),None)
            if env:
                try:
                    env.close()
                except BaseException:
                    if not failed:
                        raise

    def execute_graph(self, ctx, value):
        graph = ctx.project['graph']
        certificate = validate(graph, True)
        nodes = {n['id']: n for n in graph['nodes'] if executable(n)}
        deps = {n: [e for e in graph['edges'] if e['target'] == n] for n in nodes}
        pending, results, failed, running = set(nodes), {}, {}, {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='chaos-node') as pool:
            while pending or running:
                ctx.budget.tick()
                for nid in certificate['topologicalOrder']:
                    if nid not in pending:
                        continue
                    sources = [e['source'] for e in deps[nid]]
                    if any(s in failed for s in sources):
                        pending.remove(nid)
                        failed[nid] = '上游节点失败'
                        ctx.event('node.skipped', {'nodeId': nid, 'error': failed[nid]})
                    elif all(s in results for s in sources):
                        pending.remove(nid)
                        if nodes[nid]['kind'] == 'input':
                            payload = value
                        elif len(deps[nid]) == 1 and deps[nid][0].get('targetPort', 'input') == 'input':
                            payload = results[sources[0]]
                        else:
                            payload = {e.get('targetPort', 'input'): results[e['source']] for e in deps[nid]}
                        ctx.event('node.started', {'nodeId': nid})
                        future = pool.submit(self.execute_node, ctx, nodes[nid], copy.deepcopy(payload))
                        running[future] = (nid, time.monotonic())
                if not running:
                    if pending:
                        raise GraphError('调度器没有可执行节点')
                    break
                done, _ = concurrent.futures.wait(running, timeout=.1, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    nid, started = running.pop(future)
                    ctx.budget.tick()
                    try:
                        result = future.result()
                        if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > 250000:
                            raise LanguageError('节点结果超过 250 KB')
                        results[nid] = result
                        ctx.event('node.completed', {'nodeId': nid, 'output': result, 'durationMs': round((time.monotonic() - started) * 1000)})
                    except (ExecutionControl, ResourceLimit):
                        raise
                    except BaseException as e:
                        failed[nid] = self._run_error(e)
                        ctx.event('node.failed', {'nodeId': nid, 'error': failed[nid]})
            if failed:
                raise LanguageError('节点执行失败：' + '; '.join(f'{nid}: {message}' for nid, message in failed.items()))
            return results[certificate['exit'][0]]
