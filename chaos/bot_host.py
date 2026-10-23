"""Run-local Bot invocation ownership and revision-linearized admission.

Scope ancestry comes exclusively from the scheduler envelope. Public snapshots
and guidance targets are identities, never executable authority or tickets.
"""
import copy
import uuid
from .language import LanguageError, ResourceLimit


class BotStale(LanguageError):
    def __init__(self, message='Bot decision was superseded before action admission', *, scope_id=None):
        super().__init__(message, code='bot_stale')
        self.scope_id=scope_id


def _box(context):
    if context.run_id not in context.engine.active:
        raise LanguageError('Bot run is no longer active',code='bot_error')
    box=context.engine.guidance.get(context.run_id)
    if box is None:raise LanguageError('Bot guidance mailbox is closed',code='bot_error')
    return box


def _scope(context,scope_id):
    box=_box(context);scope=box.get('botScopes',{}).get(scope_id) if type(scope_id)is str else None
    if scope is None:raise LanguageError('Bot scope is not owned by this run',code='ownership_error')
    return box,scope


def _parts(guard):
    fields={'scopeId','decisionId','revision','ticket'}
    def identity(row):
        if (type(row)is not dict or set(row)!=fields or type(row['revision'])is not int or row['revision']<0
                or any(type(row[k])is not str or not 1<=len(row[k])<=128 for k in ('scopeId','decisionId','ticket'))):
            raise LanguageError('Invalid internal Bot guard',code='ownership_error')
        return tuple(row[k] for k in ('scopeId','decisionId','revision','ticket'))
    if type(guard)is not dict or set(guard)not in (fields,fields|{'ancestors'}):
        raise LanguageError('Invalid internal Bot guard',code='ownership_error')
    ancestors=guard.get('ancestors',[])
    if type(ancestors)is not list or len(ancestors)>=16:
        raise LanguageError('Invalid internal Bot ancestry',code='ownership_error')
    parts=tuple(identity(row)for row in ancestors)+(identity({k:guard[k]for k in fields}),)
    if len({row[0]for row in parts})!=len(parts):
        raise LanguageError('Cyclic Bot ancestry',code='ownership_error')
    return parts


def _wire(parts):
    def row(part):return dict(zip(('scopeId','decisionId','revision','ticket'),part))
    result=row(parts[-1])
    if len(parts)>1:result['ancestors']=[row(part)for part in parts[:-1]]
    return result


def _chain(context,guard,*,fresh=False):
    parts=_parts(guard);result=[]
    for index,(scope_id,decision_id,revision,ticket) in enumerate(parts):
        box,scope=_scope(context,scope_id);decision=scope['decisions'].get(decision_id)
        if (scope['ancestry']!=parts[:index] or decision is None or decision['revision']!=revision
                or decision['ticket']!=ticket):
            raise LanguageError('Bot decision is not owned by this scope',code='ownership_error')
        if fresh and (not scope['active'] or scope['terminal'] or not decision['active']
                      or scope['mailbox']['revision']!=revision):
            raise BotStale(scope_id=scope_id)
        result.append((scope,decision))
    return box,result


def _parents(context,scope):
    if scope['ancestry']:_chain(context,_wire(scope['ancestry']),fresh=True)


def _metadata(scope):
    result={'scopeId':scope['id'],'botId':scope['botId'],'parentScopeId':scope['parentScopeId'],
            'rootScopeId':scope['rootScopeId'],'mailboxKind':scope['mailboxKind']}
    if 'processRoleId'in scope:result.update(roleKey=scope['roleKey'],roleId=scope['processRoleId'],processKey=scope['processKey'])
    return result


def _safe_ancestry(scope):
    return [{'scopeId':s,'decisionId':d,'revision':v}for s,d,v,t in scope['ancestry']]


def provenance(context,guard):
    if guard is None:return {}
    with context.engine.idle:
        unused,chain=_chain(context,guard)
        scope=chain[-1][0]
        return {**_metadata(scope),'mailbox':scope['mailboxKind']}


def _persist(context,operation,payload,scope=None):
    if getattr(context,'process_claim',None)is not None:
        if scope is not None:
            payload={**payload,**_metadata(scope),'ancestry':_safe_ancestry(scope)}
        return context.process_bot_operation(operation,copy.deepcopy(payload))
    return None


def _checkpoint(payload):
    from .bot_values import data
    snapshot,output=data(payload['snapshot']),data(payload['output'])
    fields={'goal','context','input','guidance','evidence','history','revision','iteration'}
    if (type(snapshot)is not dict or set(snapshot)!=fields or type(payload['done'])is not bool
            or payload['done']and not payload['terminal'] or type(snapshot['revision'])is not int
            or snapshot['revision']!=payload['revision'] or type(snapshot['iteration'])is not int
            or snapshot['iteration']<0 or any(type(snapshot[n])is not list for n in ('guidance','evidence','history'))):
        raise LanguageError('Invalid native Bot checkpoint',code='bot_error')
    return snapshot,output


def control(context,operation,payload):
    if type(payload)is not dict:raise LanguageError('Invalid Bot control payload',code='bot_error')
    with context.engine.idle:
        if operation!='bot_close':context.budget.tick()
        box=_box(context);registry=box.setdefault('botScopes',{})
        if operation=='bot_open':
            fields=set(payload);legacy=fields=={'scopeId'}
            if not legacy and fields not in ({'scopeId','botId','mailbox'},{'scopeId','botId','mailbox','role'}):
                raise LanguageError('Invalid Bot open payload',code='bot_error')
            scope_id=payload['scopeId'];bot_id=None if legacy else payload['botId'];mailbox='auto'if legacy else payload['mailbox']
            if (type(scope_id)is not str or not scope_id.startswith('bot-scope-')or len(scope_id)!=42
                    or any(ch not in '0123456789abcdef'for ch in scope_id[10:]) or scope_id in registry):
                raise LanguageError('Invalid trusted Bot scope identity',code='bot_error')
            if not legacy and (type(bot_id)is not str or not 1<=len(bot_id)<=128 or mailbox not in ('auto','scoped')):
                raise LanguageError('Invalid native Bot invocation',code='bot_error')
            parent_guard=None if legacy else context.bot_guard
            ancestry=_parts(parent_guard)if parent_guard is not None else ()
            parents=[]
            if parent_guard is not None:unused,parents=_chain(context,parent_guard,fresh=True)
            kind='scoped'if ancestry or mailbox=='scoped'else'run'
            role=None;claim=getattr(context,'process_claim',None)
            if 'role'in payload:
                if (type(payload['role'])is not str or claim is None or not ancestry or
                        not parents or parents[0][0]['mailboxKind']!='run'):
                    raise LanguageError('Bot role requires a nested invocation of the current Process root',code='process_error')
                role=context.engine.store.processes.role_box(claim,payload['role'])
            if getattr(context,'process_claim',None)is not None and not ancestry and kind!='run':
                raise LanguageError('Process requires one legacy root Bot checkpoint',code='process_error')
            if kind=='run'and box.get('botScope')is not None:
                raise LanguageError('Run guidance already has a Bot owner',code='bot_error')
            if len(registry)>=128 or sum(s['active']for s in registry.values())>=32 or len(ancestry)>=16:
                raise ResourceLimit('Bot invocation identity budget exhausted')
            caps=context.scoped_allowed(context.project.get('capabilities',[]))
            for parent,decision in parents:caps=set(caps)&parent['capabilities']
            scope={'id':scope_id,'botId':bot_id,'parentScopeId':ancestry[-1][0]if ancestry else None,
                   'rootScopeId':ancestry[0][0]if ancestry else scope_id,'ancestry':ancestry,
                   'mailboxKind':kind,'mailbox':box if kind=='run'else{'revision':0,'queue':[],'messages':{}},
                   'active':True,'terminal':False,'capabilities':set(caps),'pending':None,'decisions':{},'actions':0}
            if role is not None:
                scope.update(processRoleId=role['roleId'],roleKey=role['roleKey'],processKey=claim['key'])
                scope['mailbox']['revision']=role['revision']
                for item in role['messages']:
                    message_id=item['receipt']['messageId']
                    receipt={key:item['receipt'][key]for key in ('messageId','revision')}
                    receipt.update(runId=context.run_id,status='queued',target={'kind':'processRole','roleId':role['roleId']})
                    scope['mailbox']['messages'][message_id]={'hash':item['hash'],'receipt':receipt,'input':copy.deepcopy(item['input']),'durable':True}
                    scope['mailbox']['queue'].append(message_id)
            revision=scope['mailbox']['revision']
            _persist(context,'open',{'scopeId':scope_id,'revision':revision},scope)
            context.event('bot.scope.opened',{**_metadata(scope),'revision':revision})
            registry[scope_id]=scope
            if kind=='run':box['botScope']=scope
            return {'scopeId':scope_id,'revision':revision}
        expected={'bot_receive':{'scopeId'},'bot_adopt':{'scopeId','messageId','revision'},
                  'bot_decision':{'scopeId','revision','phase'},'bot_outcome':{'scopeId','decisionId'},
                  'bot_close':{'scopeId'},'bot_commit':{'scopeId','revision','terminal'}}
        allowed=(expected.get(operation),)
        if operation=='bot_commit':allowed+=(expected[operation]|{'snapshot','output','done'},)
        elif operation=='bot_close':allowed+=(expected[operation]|{'status'},)
        if operation not in expected or set(payload)not in allowed:
            raise LanguageError('Invalid Bot control operation or fields',code='bot_error')
        if operation=='bot_close'and 'status'in payload and payload['status']not in ('idle','running','achieved','exhausted','failed','needs_reconciliation'):
            raise LanguageError('Invalid native Bot retirement status',code='bot_error')
        if operation=='bot_close'and (type(payload['scopeId'])is not str or payload['scopeId']not in registry):
            if type(payload['scopeId'])is not str or not payload['scopeId']or any(s['active']for s in registry.values()):
                raise LanguageError('Bot scope is not owned by this run',code='ownership_error')
            return {'closed':True}
        box,scope=_scope(context,payload['scopeId']);mail=scope['mailbox']
        if operation=='bot_close'and not scope['active']:return {'closed':True}
        if operation not in ('bot_close','bot_outcome'):
            if not scope['active']:raise LanguageError('Bot invocation is closed',code='ownership_error')
            _parents(context,scope)
        if operation=='bot_receive':
            message_id=scope['pending']
            if message_id is None:
                if not mail['queue']:return {'received':False,'message':None,'closed':False,'revision':mail['revision']}
                message_id=mail['queue'][0];record=mail['messages'][message_id];receipt={**record['receipt'],'status':'received'}
                _persist(context,'receive',{'scopeId':scope['id'],'messageId':message_id,'revision':receipt['revision']},scope)
                context.event('guidance.received',receipt);mail['queue'].pop(0);record['receipt']=receipt;scope['pending']=message_id
            record=mail['messages'][message_id]
            return {'received':True,'message':{**record['receipt'],'input':copy.deepcopy(record['input'])},'closed':False,'revision':mail['revision']}
        if operation=='bot_adopt':
            message_id=payload['messageId'];record=mail['messages'].get(message_id)
            if scope['pending']!=message_id or record is None or type(payload['revision'])is not int or payload['revision']!=record['receipt']['revision']:
                raise LanguageError('Bot adoption must acknowledge its exact pending message',code='bot_error')
            _persist(context,'adopt',{'scopeId':scope['id'],'messageId':message_id,'revision':payload['revision']},scope)
            event_id=context.event('bot.context.applied',{**_metadata(scope),'messageId':message_id,'revision':payload['revision']})
            scope['pending']=None;record.pop('input')
            return {'applied':True,'revision':payload['revision'],'eventId':event_id}
        if operation=='bot_decision':
            revision=payload['revision'];phase=payload['phase']
            if type(revision)is not int or revision<0 or phase not in ('planning','execution'):
                raise LanguageError('Invalid Bot decision revision or phase',code='bot_error')
            if scope['terminal']or revision!=mail['revision']or mail['queue']or scope['pending']is not None:
                return {'admitted':False,'revision':mail['revision'],'decisionId':None,'ticket':None}
            if box.get('botDecisionCount',0)>=2000:raise ResourceLimit('Bot decision identity budget exhausted')
            decision_id='bot-decision-'+uuid.uuid4().hex;ticket=uuid.uuid4().hex
            decision={'id':decision_id,'revision':revision,'ticket':ticket,'phase':phase,'actions':[],'active':True}
            context.event('bot.decision.created',{**_metadata(scope),'decisionId':decision_id,'revision':revision,'phase':phase})
            scope['decisions'][decision_id]=decision;box['botDecisionCount']=box.get('botDecisionCount',0)+1
            return {'admitted':True,'revision':revision,'decisionId':decision_id,'ticket':ticket}
        if operation=='bot_commit':
            revision=payload['revision']
            if type(revision)is not int or revision<0 or type(payload['terminal'])is not bool:
                raise LanguageError('Invalid Bot result revision or terminal flag',code='bot_error')
            checkpoint=_checkpoint(payload)if 'snapshot'in payload else None
            if getattr(context,'process_claim',None)is not None and checkpoint is None:
                raise LanguageError('Process Bot commit requires its actual native checkpoint',code='bot_error')
            committed=not scope['terminal']and revision==mail['revision']and not mail['queue']and scope['pending']is None
            if committed:
                if any(action['status']!='completed'for decision in scope['decisions'].values()for action in decision['actions']):
                    raise LanguageError('Bot has an uncertain admitted action; reconcile before committing',code='bot_state_error')
                if checkpoint is not None:
                    snapshot,output=checkpoint
                    _persist(context,'commit',{'scopeId':scope['id'],'revision':revision,'terminal':payload['terminal'],
                        'done':payload['done'],'snapshot':snapshot,'output':output},scope)
                context.event('bot.result.committed'if payload['terminal']else'bot.context.committed',{**_metadata(scope),'revision':revision})
                scope['terminal']=payload['terminal']
            return {'committed':committed,'revision':mail['revision']}
        if operation=='bot_outcome':
            decision=scope['decisions'].get(payload['decisionId'])
            if decision is None:raise LanguageError('Unknown Bot decision',code='ownership_error')
            decision['active']=False
            return {'revision':decision['revision'],'actions':copy.deepcopy(decision['actions'])}
        pending=scope['pending'];failure=None;status=payload.get('status')
        success=scope['terminal']and(status is None or status in ('achieved','exhausted'))
        if any(s['active']and any(part[0]==scope['id']for part in s['ancestry'])for s in registry.values()):
            raise LanguageError('Bot invocation still has active descendants',code='bot_state_error')
        try:context.event('bot.scope.closed',{**_metadata(scope),'revision':mail['revision'],
                         'pendingRestored':pending is not None and scope['mailboxKind']=='run'})
        except BaseException as error:failure=error
        try:_persist(context,'close',{'scopeId':scope['id'],'revision':mail['revision'],'success':bool(success and failure is None)},scope)
        except BaseException as cleanup:
            from .suspension import error_priority
            if failure is None or error_priority(cleanup)>error_priority(failure):failure=cleanup
        finally:
            if pending is not None and scope['mailboxKind']=='run':mail['queue'].insert(0,pending);scope['pending']=None
            scope['active']=False
            if box.get('botScope')is scope:box.pop('botScope')
        if failure is not None:raise failure
        return {'closed':True}


def _decision(context,guard):
    box,chain=_chain(context,guard)
    return box,chain[-1][0],chain[-1][1]


def authority(context,guard):
    with context.engine.idle:
        unused,chain=_chain(context,guard)
        caps=set(chain[0][0]['capabilities'])
        for scope,decision in chain[1:]:caps.intersection_update(scope['capabilities'])
        return caps


def admit(context,guard,kind):
    with context.engine.idle:
        context.budget.tick()
        box,chain=_chain(context,guard,fresh=True)
        if box.get('botActionCount',0)>=4096:raise ResourceLimit('Bot action identity budget exhausted')
        scope,decision=chain[-1]
        row={'id':'bot-action-'+uuid.uuid4().hex,'kind':kind,'status':'admitted'}
        metadata={**_metadata(scope),'decisionId':decision['id'],'revision':decision['revision'],
                  'actionId':row['id'],'kind':kind,'phase':decision['phase'],'ancestry':_safe_ancestry(scope)}
        _persist(context,'admit',{'scopeId':scope['id'],'decisionId':decision['id'],'revision':decision['revision'],
                 'action':row,'phase':decision['phase']},scope)
        context.event('bot.action.admitted',metadata)
        for ancestor,owner in chain:owner['actions'].append(row);ancestor['actions']+=1
        box['botActionCount']=box.get('botActionCount',0)+1
        return row,metadata


def finish(context,admission,error=None):
    row,metadata=admission
    with context.engine.idle:
        status='completed'if error is None else'uncertain';public={**metadata,'status':status}
        if error is not None:
            code=getattr(error,'code','effect_error');public['errorCode']=code if type(code)is str and len(code)<=128 else'effect_error'
        row['status']=status
        if error is not None:row['errorCode']=public['errorCode']
        unused,scope=_scope(context,metadata['scopeId'])
        _persist(context,'finish',{'scopeId':metadata['scopeId'],'decisionId':metadata['decisionId'],'revision':metadata['revision'],'action':row},scope)
        context.event('bot.action.completed',public)

def retirement(kind, payload):
    """Explicit resource retirement does not authorize new work or bypass caps."""
    operation = payload.get('operation')
    return ((kind == 'robot' and operation in ('stop', 'close')) or
            (kind in ('device', 'computer') and operation == 'close'))


def guarded_effect(context, kind, payload, task_cancel, allowed, node_id, allow_agents):
    guard = payload.get('_bot_guard', context.bot_guard)
    if guard is None or kind in ('guidance', 'tool', 'agent'):
        return context._effect_unchecked(kind, payload, task_cancel, allowed, node_id, allow_agents)
    if retirement(kind, payload):
        allowed = set(allowed) & authority(context, guard)
        retiring = copy.copy(context)
        retiring.bot_guard = None
        packet = dict(payload)
        packet.pop('_bot_guard', None)
        return retiring._effect_unchecked(kind, packet, task_cancel, allowed, node_id, allow_agents)
    context.budget.tick()
    if task_cancel.is_set():
        from .suspension import TaskCancelled
        raise TaskCancelled('Bot effect task was cancelled before admission', _cancel_event=task_cancel)
    allowed = set(allowed) & authority(context, guard)
    admission = admit(context, guard, kind)
    failure = None
    try:
        return context._effect_unchecked(kind, payload, task_cancel, allowed, node_id, allow_agents)
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            finish(context, admission, failure)
        except BaseException as cleanup:
            from .suspension import error_priority
            if failure is None or error_priority(cleanup) > error_priority(failure):
                raise


def guarded_tool(context, name, value, allowed, node_id, computer=None):
    guard = context.bot_guard
    if guard is None:
        return context._tool_unchecked(name, value, allowed, node_id, _computer=computer)
    context.budget.tick()
    allowed = context.scoped_allowed(allowed) & authority(context, guard)
    if type(name) is not str or name not in context.engine.tools or name not in allowed:
        raise LanguageError('Bot tool is not granted to this invocation', code='capability_error')
    admission = admit(context, guard, 'tool:' + name)
    failure = None
    try:
        return context._tool_unchecked(name, value, allowed, node_id, _computer=computer)
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            finish(context, admission, failure)
        except BaseException as cleanup:
            from .suspension import error_priority
            if failure is None or error_priority(cleanup) > error_priority(failure):
                raise


def guarded_agent(context, name, config, value, allowed, node_id=None, user_message=None):
    guard = context.bot_guard
    kwargs = {'user_message':user_message} if user_message is not None else {}
    if guard is None:
        return context._agent_unchecked(name, config, value, allowed, node_id, **kwargs)
    allowed = context.scoped_allowed(allowed) & authority(context, guard)
    admission = admit(context, guard, 'agent')
    failure = None
    try:
        return context._agent_unchecked(name, config, value, allowed, node_id, **kwargs)
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            finish(context, admission, failure)
        except BaseException as cleanup:
            from .suspension import error_priority
            if failure is None or error_priority(cleanup) > error_priority(failure):
                raise
