"""Exact original native Bot algorithms; test-only differential oracle."""
from contextlib import contextmanager
from unittest.mock import patch
from chaos.bot_runtime import Decision,credential,active_decision,external_prefix,result,uuid

@contextmanager
def scope(evaluator,decision):
    runner=evaluator.suspension.current
    previous=getattr(runner,'bot_guard',None)
    runner.bot_guard=decision
    try:yield
    finally:runner.bot_guard=previous



def host(evaluator,operation,payload):
    from chaos.suspension import Effect
    from chaos.bot_values import data,fail
    if evaluator.guidance_handler is None or evaluator.effect_handler is None:
        fail('Native Bot needs the active run admission host','capability_error')
    evaluator.tick()
    result=yield Effect('guidance',{'operation':operation,'payload':data(payload)})
    evaluator.tick()
    return data(result)


def callback(evaluator,function,args):
    from chaos.contexts import _await,_operation
    from chaos.suspension import Task,Generator
    from chaos.iteration import HANDLES
    from chaos.multiple_values import primary
    from chaos.bot_values import fail,data
    created=len(evaluator.suspension.tasks)
    value=yield from evaluator._s_invoke(function,args,all_results=True)
    if isinstance(value,Task):
        value=yield from _await(evaluator,value,_operation(evaluator,value,evaluator.suspension.tasks[created:]),all_results=True)
    value=primary(value,evaluator)
    if isinstance(value,(Task,Generator)+HANDLES):fail('Bot callbacks require explicit stream consumption','bot_error')
    return data(value)


def close_owned(evaluator,token,original):
    from chaos.task_groups import create,GroupRun,ScopeOutcome
    from chaos.suspension import TERMINAL,AsyncGenerator,hard_error,error_priority
    from chaos.iteration import AsyncIterator
    scheduler=evaluator.suspension
    handles=tuple(scheduler.generators)+tuple(scheduler.iteration.handles)
    handles=tuple(handle for handle in handles if getattr(handle,'bot_guard',None) is token and handle.state not in TERMINAL)
    if not handles:return
    def cleanup():
        try:
            for handle in handles:
                manager=getattr(handle,'context_manager',None)
                if manager is not None:
                    if manager.state=='created':
                        from chaos.contexts import abandon
                        abandon(evaluator,manager)
                    continue
                if hard_error(original):
                    if handle in scheduler.generators:scheduler._abort_stream(handle)
                else:
                    yield from scheduler.iteration.close(handle,isinstance(handle,(AsyncGenerator,AsyncIterator)))
            return ScopeOutcome()
        except BaseException as error:return ScopeOutcome(error=error)
    if hard_error(original):
        # Hard shutdown retains the original native force-close policy and
        # cannot start new user callbacks or Tasks.
        for handle in handles:
            if handle in scheduler.generators:scheduler._abort_stream(handle)
        return
    group=create(evaluator);token.group=group
    try:
        outcome=yield GroupRun(group,cleanup())
        if outcome.error is not None:raise outcome.error
    except BaseException as error:
        if original is None or error_priority(error)>error_priority(original):raise


def phase(evaluator,token,function,args):
    from chaos.task_groups import create,GroupRun,ScopeOutcome
    group=create(evaluator);token.group=group
    def body():
        try:
            # Admit exactly once inside this real scope before callback entry.
            refreshed=yield from decision(evaluator,token.scope_id,token.revision,token.phase)
            if refreshed is None:
                from chaos.bot_values import fail
                fail('Guidance changed before Bot callback entry','bot_stale')
            token.decision_id,token.ticket=refreshed.decision_id,refreshed.ticket
            token.body_entered=True
            return ScopeOutcome(value=(yield from callback(evaluator,function,args)))
        except BaseException as error:return ScopeOutcome(error=error)
    try:
        with scope(evaluator,token):
            failure=None;outcome=None
            try:outcome=yield GroupRun(group,body())
            except BaseException as error:failure=error
            # Child work is quiescent before streams close. Cleanup itself uses
            # the original structured scope and current decision authority.
            yield from close_owned(evaluator,token,failure or outcome.error or outcome.signal)
            if failure is not None:raise failure
        if outcome.error is not None:raise outcome.error
        if outcome.signal is not None:raise outcome.signal
        return outcome.value
    finally:token.active=False


def decision(evaluator,scope_id,revision,phase='execution'):
    from chaos.bot_values import fail
    packet=yield from host(evaluator,'bot_decision',{'scopeId':scope_id,'revision':revision,'phase':phase})
    if (type(packet) is not dict or set(packet)!={'admitted','revision','decisionId','ticket'}
            or type(packet['admitted']) is not bool or type(packet['revision']) is not int or packet['revision']<0):
        fail('Invalid Bot decision receipt')
    if not packet['admitted']:
        if packet['decisionId'] is not None or packet['ticket'] is not None:fail('Rejected Bot decision has an identity')
        return None
    if packet['revision']!=revision:fail('Bot admission changed its revision')
    if any(type(packet[key]) is not str or not packet[key] for key in ('decisionId','ticket')):fail('Invalid Bot decision identity')
    return Decision(evaluator,evaluator.suspension.epoch,scope_id,revision,packet['decisionId'],packet['ticket'],phase=phase)


def drain(evaluator,value,scope_id):
    from chaos.bot_values import data,fail
    from chaos.guidance_values import response
    state=value.state
    while True:
        packet=yield from host(evaluator,'bot_receive',{'scopeId':scope_id})
        if type(packet) is not dict or set(packet)!={'received','message','closed','revision'} or type(packet['revision']) is not int:fail('Invalid Bot guidance receipt')
        response({key:packet[key] for key in ('received','message','closed')})
        if packet['closed']:fail('Bot mailbox closed','bot_state_error')
        if not packet['received']:return
        message=packet['message']
        if message['revision']<=state.revision and any(item['messageId']==message['messageId'] for item in state.guidance):
            pass
        else:
            guidance=data(state.guidance+[message])
            # Commit the native copy before acknowledging host adoption. A
            # repeated pending receipt is recognized by identity, never reapplied.
            state.guidance=guidance;state.revision=message['revision']
        adopted=yield from host(evaluator,'bot_adopt',{'scopeId':scope_id,'messageId':message['messageId'],'revision':message['revision']})
        if type(adopted) is not dict or adopted.get('applied') is not True or adopted.get('revision')!=message['revision']:fail('Invalid Bot adoption receipt')


def save_outcome(evaluator,value,scope_id,token):
    from chaos.bot_values import data,fail
    if not token.decision_id:return {'revision':token.revision,'actions':[]}
    packet=yield from host(evaluator,'bot_outcome',{'scopeId':scope_id,'decisionId':token.decision_id})
    if type(packet) is not dict or set(packet)!={'revision','actions'} or type(packet['revision']) is not int or packet['revision']<0 or type(packet['actions']) is not list:fail('Invalid Bot outcome receipt')
    for row in packet['actions']:
        if (type(row) is not dict or not {'id','kind','status'}<=set(row) or set(row)-{'id','kind','status','errorCode'}
                or type(row['id']) is not str or not row['id'] or type(row['kind']) is not str or not row['kind']
                or row['status'] not in ('admitted','completed','uncertain','failed')
                or 'errorCode' in row and row['errorCode'] is not None and type(row['errorCode']) is not str):fail('Invalid Bot action outcome')
    row={'kind':'decision','decisionId':token.decision_id,'revision':token.revision,'phase':token.phase,'actions':packet['actions']}
    history=[item for item in value.state.history if item.get('decisionId')!=token.decision_id]
    value.state.history=data(history+[row])
    return packet

def commit(evaluator,value,scope_id,terminal,done,context,output):
    from chaos.bot_values import data,snapshot
    checkpoint=snapshot(value)
    checkpoint['context']=data(context)
    return (yield from host(evaluator,'bot_commit',{'scopeId':scope_id,'revision':value.state.revision,
        'terminal':terminal,'done':done,'snapshot':checkpoint,'output':data(output)}))


def run(evaluator,value,input):
    from chaos.bot_values import owned,data,snapshot,fail
    from chaos.execution_scope import scope as authority
    from chaos.suspension import hard_error,error_priority
    from chaos.control_prompts import NonlocalTransfer
    owned(value,evaluator);state=value.state
    if state.busy:fail('Bot already has an active invocation','bot_busy')
    if state.status=='needs_reconciliation':fail('Bot requires explicit reconciliation','bot_state_error')
    if evaluator.suspension.current.task.recording_scope is not None:fail('Bot work cannot be Skill-recorded','mode_error')
    if evaluator.guidance_handler is None or evaluator.effect_handler is None:
        fail('Native Bot needs the active run admission host','capability_error')
    parent=active_decision(evaluator)
    parents=parent.parents+(parent,) if parent is not None else ()
    ancestry=parent.ancestry+(credential(parent),) if parent is not None else getattr(evaluator,"_inherited_bot_ancestry",())
    inherited_count=parent.inherited_count if parent is not None else len(ancestry)
    state.busy=True;state.status='running';state.input=data(input)
    # Identity is owned before the opening yield, so receipt loss cannot orphan
    # a lease. It is never supplied by source input or a public snapshot.
    scope_id='bot-scope-'+uuid.uuid4().hex;original=None;entered=False;token=None
    try:
        with authority(evaluator,value.capabilities):
            opening={'scopeId':scope_id,'botId':state.id,'mailbox':value.mailbox}
            if value.role is not None:opening['role']=value.role
            opened=yield from host(evaluator,'bot_open',opening)
            if (type(opened) is not dict or set(opened)!={'scopeId','revision'} or opened['scopeId']!=scope_id
                    or type(opened['revision']) is not int or opened['revision']<0):fail('Invalid Bot scope receipt')
            state.revision=opened['revision'];rounds=0
            while value.max_iterations is None or rounds<value.max_iterations:
                evaluator.tick();yield from drain(evaluator,value,scope_id)
                token=Decision(evaluator,evaluator.suspension.epoch,scope_id,state.revision,'','',phase='planning',ancestry=ancestry,parents=parents,inherited_count=inherited_count)
                state.iteration+=1;rounds+=1
                try:proposal=yield from phase(evaluator,token,value.planner,[snapshot(value)])
                except BaseException as error:
                    if getattr(error,'code',None)=='bot_stale' and getattr(error,'scope_id',None)not in (None,scope_id):
                        raise
                    if hard_error(error) or isinstance(error,NonlocalTransfer):
                        entered=token.body_entered;original=error
                        if entered:state.status='needs_reconciliation'
                        raise
                    entered=token.body_entered
                    outcome=yield from save_outcome(evaluator,value,scope_id,token)
                    entered=external_prefix(outcome)
                    if external_prefix(outcome):
                        state.status='needs_reconciliation';original=error;entered=True
                        if hard_error(error) or isinstance(error,NonlocalTransfer):raise
                        return result(value)
                    if getattr(error,'code',None)=='bot_stale':
                        continue
                    raise
                entered=token.body_entered
                planning=yield from save_outcome(evaluator,value,scope_id,token)
                entered=external_prefix(planning)
                token=Decision(evaluator,evaluator.suspension.epoch,scope_id,state.revision,'','',ancestry=ancestry,parents=parents,inherited_count=inherited_count)
                try:
                    completed=yield from phase(evaluator,token,value.execute,[proposal,snapshot(value)])
                    entered=token.body_entered
                    state.evidence=data(state.evidence+[completed])
                    yield from save_outcome(evaluator,value,scope_id,token)
                    token=Decision(evaluator,evaluator.suspension.epoch,scope_id,state.revision,'','',ancestry=ancestry,parents=parents,inherited_count=inherited_count)
                    verified=yield from phase(evaluator,token,value.verify,[snapshot(value)])
                    yield from save_outcome(evaluator,value,scope_id,token)
                    if type(verified) is not dict or set(verified)!={'done','context','output'} or type(verified['done']) is not bool:fail('Bot verifier requires done/context/output')
                    context,output=data(verified['context']),data(verified['output'])
                    receipt=yield from commit(evaluator,value,scope_id,verified['done'] or value.max_iterations is not None and rounds>=value.max_iterations,verified['done'],context,output)
                    if (type(receipt) is not dict or set(receipt)!={'committed','revision'} or type(receipt['committed']) is not bool
                            or type(receipt['revision']) is not int):fail('Invalid Bot result commit')
                    if not receipt['committed']:fail('Guidance changed before Bot result commit','bot_stale')
                    if receipt['revision']!=state.revision:fail('Bot result commit changed revision')
                    state.context,state.output=context,output
                    if verified['done']:state.status='achieved';return result(value)
                    if value.max_iterations is not None and rounds>=value.max_iterations:
                        state.status='exhausted';return result(value)
                    entered=False
                except BaseException as error:
                    if getattr(error,'code',None)=='bot_stale' and getattr(error,'scope_id',None)not in (None,scope_id):
                        raise
                    entered=entered or token is not None and token.body_entered
                    if not entered and not external_prefix(planning) and getattr(error,'code',None)=='bot_stale':continue
                    state.status='needs_reconciliation';original=error
                    if hard_error(error) or isinstance(error,NonlocalTransfer):raise
                    if token is not None:
                        try:yield from save_outcome(evaluator,value,scope_id,token)
                        except BaseException as cleanup:
                            if error_priority(cleanup)>error_priority(error):raise
                    if hard_error(error) or isinstance(error,NonlocalTransfer):raise
                    return result(value)
            while True:
                # Superseded zero-action plans may consume the finite attempt
                # bound. Adopt remaining guidance and linearize exhaustion
                # without replaying any planner or executor callback.
                yield from drain(evaluator,value,scope_id)
                receipt=yield from commit(evaluator,value,scope_id,True,False,state.context,state.output)
                if (type(receipt) is not dict or set(receipt)!={'committed','revision'} or type(receipt['committed']) is not bool
                        or type(receipt['revision']) is not int):fail('Invalid Bot exhaustion commit')
                if receipt['committed']:
                    if receipt['revision']!=state.revision:fail('Bot exhaustion changed revision')
                    state.status='exhausted';return result(value)
    except BaseException as error:
        original=error
        state.status='needs_reconciliation' if entered else 'failed'
        raise
    finally:
        try:
            if scope_id is not None:
                try:
                    # Trusted lease retirement follows the completed scope
                    # barrier; it performs no language or external action.
                    from chaos.agent_tools import current_caps
                    from chaos.execution_scope import current as current_team
                    team=current_team(evaluator)
                    closed=data(evaluator.guidance_handler('bot_close',{'scopeId':scope_id,'status':state.status},current_caps(evaluator),data(team) if team is not None else None))
                    if closed!={'closed':True}:fail('Invalid Bot close receipt')
                except BaseException as cleanup:
                    if entered:state.status='needs_reconciliation'
                    if original is None or error_priority(cleanup)>error_priority(original):raise
        finally:state.busy=False


@contextmanager
def reference():
    with patch('chaos.bot_runtime.run',run):yield
