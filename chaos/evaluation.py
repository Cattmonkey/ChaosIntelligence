"""Bounded deterministic Agent protocol fixtures, without live host services.

Only external responses are replaced. BoundAgent tools execute their original
language closures on the same evaluator and scheduler as ordinary execution.
"""
import copy
import json
import threading
import time
from types import SimpleNamespace

from .agent_values import validate_definition as validate_agent
from .extensions import BUILTIN_TOOLS, json_copy, valid_name, capability_scope, language_permissions
from .language import Evaluator, LanguageError, ResourceLimit, check_type
from .suspension import TaskCancelled, hard_error, error_priority

MAX_USES = 256
RESERVED = language_permissions() | frozenset(name for name, descriptor in BUILTIN_TOOLS.items() if capability_scope(descriptor) == 'platform')


def fixture_schema():
    from .agent_values import configuration_schema
    definition={'type':'object','additionalProperties':False,'required':['kind','name','config'],
        'properties':{'kind':{'const':'agent'},'name':{'type':'string','minLength':1,'maxLength':128},'config':configuration_schema()}}
    times={'type':'integer','minimum':1,'maximum':64}
    action={'oneOf':[
        {'type':'object','additionalProperties':False,'required':['tool','input','expect'],
         'properties':{'tool':{'type':'string','minLength':1,'maxLength':128},'input':{},'expect':{}}},
        {'type':'object','additionalProperties':False,'required':['final'],'properties':{'final':{}}}]}
    agent={'type':'object','additionalProperties':False,'required':['definition','input','actions'],
        'properties':{'definition':definition,'input':{},'actions':{'type':'array','minItems':1,'maxItems':12,'items':action},'times':times}}
    tool={'type':'object','additionalProperties':False,'required':['name','input','output'],
        'properties':{'name':{'type':'string','minLength':1,'maxLength':128},'input':{},'output':{},'times':times}}
    return {'type':'object','additionalProperties':False,'properties':{
        'agents':{'type':'array','maxItems':64,'items':agent},'tools':{'type':'array','maxItems':64,'items':tool}}}


def _key(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def equal(left,right,checkpoint=None):
    """The language's JSON equality, including distinct Bool/Int/Number types."""
    pending=[(left,right)]
    while pending:
        if checkpoint:checkpoint()
        first,second=pending.pop()
        if type(first) is not type(second):return False
        if type(first) is list:
            if len(first)!=len(second):return False
            pending.extend(zip(first,second))
        elif type(first) is dict:
            if first.keys()!=second.keys():return False
            pending.extend((first[key],second[key]) for key in first)
        elif first!=second:return False
    return True


def _error(message):
    raise LanguageError(message,code='fixture_error')


def validate_fixtures(value):
    try:value=json_copy(value)
    except (ValueError,TypeError,UnicodeError) as error:_error('Fixtures require bounded JSON: '+str(error))
    if type(value) is not dict or set(value)-{'agents','tools'}:_error('Fixtures require optional agents/tools arrays')
    for kind in ('agents','tools'):
        rows=value.get(kind,[])
        if type(rows) is not list or len(rows)>64:_error('Fixture '+kind+' requires at most 64 rows')
        seen=set();total=0
        for row in rows:
            required={'definition','input','actions'} if kind=='agents' else {'name','input','output'}
            if type(row) is not dict or not required<=set(row) or set(row)-required-{'times'}:_error('Invalid '+kind+' fixture fields')
            times=row.get('times',1)
            if type(times) is not int or not 1<=times<=64:_error('Fixture times must be 1–64')
            total+=times
            if kind=='agents':
                row['definition']=validate_agent(row['definition'])
                actions=row['actions']
                if type(actions) is not list or not 1<=len(actions)<=12:_error('Agent fixture requires 1–12 actions')
                if len(actions)>row['definition']['config'].get('maxSteps',12):_error('Fixture actions exceed the Agent maxSteps contract')
                for index,action in enumerate(actions):
                    expected={'final'} if index==len(actions)-1 else {'tool','input','expect'}
                    if type(action) is not dict or set(action)!=expected:_error('Agent fixture requires tool/input/expect steps followed by one final')
                    if 'tool' in action:
                        try:valid_name(action['tool'])
                        except ValueError as error:_error(str(error))
                key=_key([row['definition'],row['input']])
            else:
                try:valid_name(row['name'])
                except ValueError as error:_error(str(error))
                if row['name'] in RESERVED:_error('Reserved effects cannot be fixture tools')
                key=_key([row['name'],row['input']])
            if key in seen:_error('Duplicate fixture match; use times for identical repeated calls')
            seen.add(key)
        if total>MAX_USES:_error('Fixture '+kind+' total times exceeds '+str(MAX_USES))
    return value


class _Budget:
    def __init__(self,limit,timeout,cancelled,checkpoint):
        self.limit,self.deadline=limit,time.monotonic()+timeout
        self.cancelled,self.checkpoint=cancelled,checkpoint
        self.steps=0;self.lock=threading.RLock()
    def tick(self):
        if self.checkpoint:self.checkpoint()
        if self.cancelled and self.cancelled():raise ResourceLimit('Fixture evaluation cancelled')
        with self.lock:
            self.steps+=1
            if self.steps>self.limit:raise ResourceLimit('Fixture evaluation step budget exhausted')
        if time.monotonic()>self.deadline:raise ResourceLimit('Fixture evaluation deadline exceeded')


class _TaskBudget:
    def __init__(self,shared,cancel):self.shared,self.cancel=shared,cancel
    @property
    def deadline(self):return self.shared.deadline
    def tick(self):
        self.shared.tick()
        if self.cancel is not None and self.cancel.is_set():raise TaskCancelled('Fixture task cancelled',_cancel_event=self.cancel)


class FixtureHost:
    def __init__(self,fixtures,capabilities,descriptors,budget):
        self.definition=validate_fixtures(fixtures)
        self.allowed=frozenset(capabilities)-RESERVED
        self.budget=budget;self.lock=threading.RLock();self.failure=None
        self.services={'lock':threading.RLock(),'agentInvocations':{}}
        known={**copy.deepcopy(BUILTIN_TOOLS),**json_copy(descriptors or {})}
        for row in self.definition.get('tools',[]):known.setdefault(row['name'],{'description':'Deterministic fixture response'})
        self.engine=SimpleNamespace(tools=known,custom_providers={})
        self.rows={kind:[] for kind in ('agents','tools')}
        self.matches={kind:{} for kind in self.rows}
        for kind in self.rows:
            for index,row in enumerate(self.definition.get(kind,[])):
                record={'index':index,'expected':row.get('times',1),'used':0,'completed':0,'row':row}
                self.rows[kind].append(record)
                key=_key([row['definition'],row['input']]) if kind=='agents' else _key([row['name'],row['input']])
                self.matches[kind][key]=record
        self.direct_owner=object()

    def latch(self,error):
        with self.lock:
            if self.failure is None:self.failure=str(error)

    def fail(self,message):
        error=LanguageError(message,code='fixture_error');self.latch(error);raise error

    def claim(self,kind,key):
        self.budget.tick()
        with self.lock:
            record=self.matches[kind].get(key)
            if record is None:self.fail('No exact '+kind+' fixture matches this call')
            if record['used']>=record['expected']:self.fail('Unexpected extra '+kind+' fixture call')
            record['used']+=1
            return record

    def actions(self,binding,value):
        definition=validate_agent(binding['agent'])
        record=self.claim('agents',_key([definition,json_copy(value)]))
        completed=False
        try:
            for action in record['row']['actions']:
                self.budget.tick()
                if 'final' in action:
                    with self.lock:record['completed']+=1
                    completed=True
                    return json_copy(action['final'])
                actual=yield {'name':action['tool'],'input':json_copy(action['input'])}
                if _key(json_copy(actual))!=_key(action['expect']):self.fail('Actual Agent tool output differs from fixture expectation: '+action['tool'])
        finally:
            if not completed:self.latch('Agent fixture ended before its final action')

    def tool(self,name,value,allowed):
        if name in RESERVED or name not in allowed or name not in self.allowed:
            self.fail('Fixture tool capability denied: '+str(name))
        descriptor=self.engine.tools.get(name,{})
        try:check_type(value,descriptor.get('inputType','Any'))
        except LanguageError as error:self.fail('Fixture tool input contract failed: '+str(error))
        record=self.claim('tools',_key([name,json_copy(value)]))
        output=json_copy(record['row']['output'])
        try:check_type(output,descriptor.get('outputType','Any'))
        except LanguageError as error:self.fail('Fixture tool output contract failed: '+str(error))
        with self.lock:record['completed']+=1
        return output

    def context(self,allowed,cancel=None,recording=None,owner=None):
        return _FixtureContext(self,allowed,cancel,recording,owner)

    def effect(self,kind,payload,cancel):
        allowed=self.allowed
        if '_agent_caps' in payload:allowed=allowed&frozenset(payload['_agent_caps'])
        if '_team' in payload:allowed=allowed&frozenset(payload['_team']['capabilities'])
        context=self.context(allowed,cancel,payload.get('_mode',{}).get('recordingId'),id(cancel))
        try:
            if kind=='agent_transition':
                from .agent_tools import transition
                return transition(context,payload,allowed,'fixture')
            if kind=='tool':return context.tool(payload['name'],payload['value'],allowed,'fixture')
            if kind=='agent' and '_observed' not in payload:return context.agent(payload['name'],payload['config'],payload['value'])
            self.fail('Unsupported fixture effect: '+kind)
        except LanguageError as error:
            if error.code in ('fixture_error','capability_error'):self.latch(error)
            raise

    def close(self):
        failure=None
        for invocation in list(self.services['agentInvocations'].values()):
            try:invocation.close()
            except BaseException as error:
                if error_priority(error)>error_priority(failure):failure=error
        self.services['agentInvocations'].clear()
        if failure is not None:raise failure

    def evidence(self):
        with self.lock:
            return {'agents':[{k:record[k] for k in ('index','expected','used','completed')} for record in self.rows['agents']],
                    'tools':[{k:record[k] for k in ('index','expected','used','completed')} for record in self.rows['tools']],
                    'passed':self.failure is None and all(r['used']==r['completed']==r['expected'] for rows in self.rows.values() for r in rows)}

    def verify(self):
        evidence=self.evidence()
        if not evidence['passed']:
            error=LanguageError(self.failure or 'Unconsumed fixture calls remain',code='fixture_error')
            error.fixture_evidence=evidence
            raise error
        return evidence


class _FixtureContext:
    def __init__(self,host,allowed,cancel,recording,owner):
        self.host=host;self.engine=host.engine;self.services=host.services
        self.allowed=frozenset(allowed);self.effect_task_key=host.direct_owner if owner is None else owner
        self.recording_scope=recording;self.budget=_TaskBudget(host.budget,cancel)
        self.fixture_actions=host.actions
    def event(self,kind,payload):pass
    def tool(self,name,value,allowed,node_id):
        self.budget.tick()
        return self.host.tool(name,value,self.allowed&set(allowed))
    def agent(self,name,config,value):
        from .agent_tools import transition
        self.budget.tick()
        result=transition(self,{'operation':'start','binding':{'agent':{'kind':'agent','name':name,'config':config},'tools':[]},'value':value},self.allowed,'fixture')
        if result['status']!='done':self.host.fail('Ordinary Agent requested a language binding it does not own')
        return result['value']


class _FixtureEvaluator(Evaluator):
    def _observe_fixture_error(self,error):
        if error.code in ('capability_error','fixture_error'):
            self.fixture_host.latch(error)

    def _frame_invocation_error(self,error):
        # Typed child calls enter InvocationFrame directly, so a policy must
        # observe their failure before the enclosing language catch consumes it.
        if isinstance(error,LanguageError):self._observe_fixture_error(error)

    def _s_invoke(self,*args,**kwargs):
        # Tail-call trampolines may invoke a Native without a fresh expression
        # frame. Observe this boundary before an enclosing language catch.
        try:return (yield from super()._s_invoke(*args,**kwargs))
        except LanguageError as error:
            self._observe_fixture_error(error)
            raise

    def _s_evaluate(self,*args,**kwargs):
        try:return (yield from super()._s_evaluate(*args,**kwargs))
        except LanguageError as error:
            self._observe_fixture_error(error)
            raise


def evaluate(source_or_bundle,input=None,*,fixtures=None,capabilities=(),tool_descriptors=None,
             step_limit=30000,timeout=20,cancelled=None,checkpoint=None):
    """Return raw language output; callers choose strict JSON or IDE display."""
    host=None
    if fixtures is None:
        evaluator=Evaluator(step_limit=step_limit,timeout=timeout,cancelled=cancelled)
        if checkpoint:
            base=evaluator.tick
            def tick():checkpoint();base()
            evaluator.tick=tick
    else:
        budget=_Budget(step_limit,timeout,cancelled,checkpoint)
        host=FixtureHost(fixtures,capabilities,tool_descriptors,budget)
        def allowed():
            from .agent_tools import current_caps
            scope=current_caps(evaluator)
            return host.allowed if scope is None else host.allowed&scope
        def deny(*args,**kwargs):host.fail('Unsupported effect in fixture evaluation')
        evaluator=_FixtureEvaluator(step_limit=step_limit,timeout=timeout,cancelled=cancelled,
            tool_handler=lambda name,value:host.tool(name,value,allowed()),
            agent_handler=lambda name,config,value:host.context(allowed()).agent(name,config,value),
            workflow_handler=deny,mode_handler=deny,program_handler=deny,computer_handler=deny,session_handler=deny,process_handler=deny,
            effect_handler=host.effect)
        evaluator.fixture_host=host
        base=evaluator.tick
        def tick():budget.tick();base()
        evaluator.tick=tick
    value=None;failure=None
    try:value=evaluator.run(source_or_bundle,{'input':input})
    except BaseException as error:failure=error
    if host is not None:
        try:host.close()
        except BaseException as error:
            if error_priority(error)>error_priority(failure):failure=error
        if not hard_error(failure):
            try:evidence=host.verify()
            except LanguageError as error:
                if failure is None or isinstance(failure,LanguageError) and failure.code in ('fixture_error','capability_error'):failure=error
                else:failure.fixture_evidence=error.fixture_evidence
        else:evidence=host.evidence()
    else:evidence=None
    if failure is not None:raise failure
    return {'value':value,'steps':evaluator.steps,'fixtures':evidence}
