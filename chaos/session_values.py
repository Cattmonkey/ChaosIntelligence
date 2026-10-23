"""Owned session handles and cold current-project conversation effects."""
import copy
import re
from dataclasses import dataclass
from .language import Agent, LanguageError
from .modes import identifier
from .agent_values import validate_definition, definition_hash, configuration_schema as agent_schema
from .session_protocol import data,context_limit


def configuration_schema():
    return {'type':'object','additionalProperties':False,'required':['agent','contextLimit'],'properties':{
        'agent':{'type':'object','additionalProperties':False,'required':['kind','name','config'],'properties':{
            'kind':{'const':'agent'},'name':{'type':'string','minLength':1,'maxLength':128},'config':agent_schema()}},
        'contextLimit':{'type':'integer','minimum':1000,'maximum':200000,'default':100000}}}


def agent_definition(value,evaluator):
    if not isinstance(value,Agent):raise LanguageError('Session 需要明确 Agent 值',code='session_error')
    if value.owner is not None and value.owner is not evaluator:raise LanguageError('Agent 不能跨上下文使用',code='ownership_error')
    definition=validate_definition({'kind':'agent','name':value.name,'config':value.config})
    if definition['config'].get('provider')!='model':raise LanguageError('AgentSession 仅支持显式 provider:model',code='session_error')
    return definition


def revision(value):
    if type(value) is not int or value<1:raise LanguageError('Session revision 需要正整数',code='session_error')
    return value


def message_identifier(value,label='message_id'):
    # Historical automatically generated IDs were unprefixed uuid.hex values.
    if type(value)is str and re.fullmatch(r'[0-9a-f]{32}',value):return value
    return identifier(value,label)


def metadata(value):
    result=data(value,250000)
    if type(result) is not dict:raise LanguageError('无效 Session 宿主元数据',code='session_error')
    identifier(result.get('key'),'key');revision(result.get('revision'))
    if result.get('status') not in ('idle','running','needs_reconciliation','closed'):raise LanguageError('无效 Session 状态',code='session_error')
    if type(result.get('turn')) is not int or result['turn']<0:raise LanguageError('无效 Session 轮次',code='session_error')
    definition=validate_definition(result.get('agent'))
    if definition['config'].get('provider')!='model' or result.get('definitionHash')!=definition_hash(definition):raise LanguageError('无效 Session Agent 身份',code='session_error')
    context_limit(result.get('contextLimit'))
    return result


@dataclass(eq=False)
class AgentSession:
    owner: object
    snapshot: dict
    busy: bool = False

    @property
    def key(self):return self.snapshot['key']


def owned(value,evaluator):
    if type(value) is not AgentSession:raise LanguageError('需要受控 AgentSession',code='session_error')
    if value.owner is not evaluator:raise LanguageError('AgentSession 不能跨执行上下文使用；请 session_load',code='ownership_error')
    return value


def describe(value):return {'$session':value.key,'revision':value.snapshot['revision'],'status':value.snapshot['status']}


@dataclass(frozen=True)
class SessionRequest:
    operation: str
    payload: dict
    handle: object = None


def turn_request(evaluator,handle,input,message_id=None):
    owned(handle,evaluator)
    if message_id is not None:message_identifier(message_id)
    from .computer_values import ObservedInput, observed_payload
    payload={'key':handle.key,'input':None if type(input) is ObservedInput else data(input),'messageId':message_id}
    if type(input) is ObservedInput:payload['_observed']=observed_payload(input,evaluator)
    return SessionRequest('turn',payload,handle)


def resolve(evaluator,request):
    payload=data(request.payload,250000)
    def call():
        from .suspension import Effect
        task=evaluator.suspension.current.task
        if task.recording_scope is not None:
            if evaluator.mode_handler is not None:
                yield Effect('mode',{'operation':'recording.abort','payload':{'scopeId':task.recording_scope,'reason':'session_effect_forbidden'}})
            raise LanguageError('录制 scope 不支持 Session 效果',code='mode_error')
        if '_observed' in payload and evaluator.computer_handler is None:raise LanguageError('Current environment does not allow visual Session input',code='capability_error')
        if evaluator.session_handler is None:raise LanguageError('当前环境不允许 Session 效果',code='capability_error')
        handle=owned(request.handle,evaluator) if request.handle is not None else None
        if handle is not None:
            if handle.busy:raise LanguageError('AgentSession 正在执行另一调用',code='session_busy')
            handle.busy=True
            payload['revision']=handle.snapshot['revision']
        try:
            evaluator.tick()
            result=yield Effect('session',{'operation':request.operation,'payload':payload})
            if request.operation in ('open','load','fork'):
                snapshot=metadata(result)
                expected=payload.get('newKey') if request.operation=='fork' else payload['key']
                if snapshot['key']!=expected:raise LanguageError('Session 宿主返回另一键',code='session_error')
                if request.operation in ('open','fork') and payload.get('agent') is not None and snapshot['definitionHash']!=definition_hash(payload['agent']):
                    raise LanguageError('Session 宿主返回不同 Agent 定义',code='session_error')
                result=AgentSession(evaluator,snapshot)
            elif handle is not None:
                result=accept_turn(handle,result)
            else:result=data(result,250000)
            evaluator.tick()
            return result
        finally:
            if handle is not None:handle.busy=False
    return evaluator.suspension.task(call())


def accept_turn(handle,result):
    if type(result) is not dict or set(result)!={'receipt','session'}:raise LanguageError('无效 Session 宿主回执',code='session_error')
    snapshot=metadata(result['session']);receipt=data(result['receipt'],250000)
    if (type(receipt) is not dict or set(receipt)!={'key','messageId','status','output','revision','replayed'}
        or receipt['key']!=handle.key or receipt['status']!='completed' or type(receipt['output']) is not str
        or type(receipt['replayed']) is not bool):raise LanguageError('无效 Session 完成回执',code='session_error')
    message_identifier(receipt['messageId'],'messageId');revision(receipt['revision'])
    if (snapshot['key']!=handle.key or snapshot['definitionHash']!=handle.snapshot['definitionHash']
        or snapshot['contextLimit']!=handle.snapshot['contextLimit'] or snapshot['revision']<receipt['revision']):raise LanguageError('Session 身份发生变化',code='session_error')
    if snapshot['revision']>=handle.snapshot['revision']:handle.snapshot=snapshot
    result=receipt
    return result


def builtins(evaluator):
    def session_open(key,agent,context_limit=100000):
        from .session_protocol import context_limit as check_limit
        return SessionRequest('open',{'key':identifier(key,'key'),'agent':agent_definition(agent,evaluator),'contextLimit':check_limit(context_limit)})
    def session_load(key):return SessionRequest('load',{'key':identifier(key,'key')})
    def session_info(value):return copy.deepcopy(owned(value,evaluator).snapshot)
    def session_status(key):return SessionRequest('status',{'key':identifier(key,'key')})
    def session_history(key,after=0,limit=100):
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:raise LanguageError('无效 Session 分页',code='session_error')
        return SessionRequest('history',{'key':identifier(key,'key'),'after':after,'limit':limit})
    def session_fork(key,revision,new_key,agent=None):
        return SessionRequest('fork',{'key':identifier(key,'key'),'revision':_revision(revision),'newKey':identifier(new_key,'new_key'),'agent':None if agent is None else agent_definition(agent,evaluator)})
    def session_compact(key,revision,keep_turns):
        if type(keep_turns) is not int or keep_turns<0 or keep_turns>1000000:raise LanguageError('keep_turns 需要有界非负整数',code='session_error')
        return SessionRequest('compact',{'key':identifier(key,'key'),'revision':_revision(revision),'keepTurns':keep_turns})
    def session_close(key,revision):return SessionRequest('close',{'key':identifier(key,'key'),'revision':_revision(revision)})
    def session_reconcile(key,revision,evidence):
        if type(evidence) is not str or len(evidence)>1000:raise LanguageError('Session evidence 最多1000字符',code='session_error')
        return SessionRequest('reconcile',{'key':identifier(key,'key'),'revision':_revision(revision),'evidence':evidence})
    _revision=revision
    return {'session_open':session_open,'session_load':session_load,'session_info':session_info,'session_status':session_status,
            'session_history':session_history,'session_fork':session_fork,'session_compact':session_compact,'session_close':session_close,'session_reconcile':session_reconcile}
