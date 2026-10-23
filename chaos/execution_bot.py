"""Authoritative native Bot lifecycle on the original execution cursor."""


class _BotFrame:
    ROOTS=()
    capabilities=None
    def roots(self):
        from .capture_frames import Edge
        from .bot_values import State,Bot
        from .conditions import intersection
        values=[];saved=[]
        saved_names=('authority_runner','previous_authority','previous_team','previous_guard')
        for name in self.ROOTS:
            value=getattr(self,name,None)
            if name in saved_names:
                saved.append(value);continue
            if name=='state' and type(value) is State:
                values.extend((value.goal,value.context,value.input,value.guidance,value.evidence,value.history,value.output))
            elif name in ('args','handles','parents','ancestry') and type(value) in (tuple,list):
                values.extend(value)
            else:values.append(value)
        bot=getattr(self,'bot',None)
        ceiling=intersection(self.capabilities,bot.capabilities if type(bot) is Bot else None)
        previous=getattr(self,'previous_authority',self.capabilities)
        return (Edge(self.owner,ceiling,tuple(values)),Edge(self.owner,previous,tuple(saved)))
    def close(self,cursor):
        bridge=getattr(self,'bridge',None)
        if bridge is not None:bridge.close(cursor)


class BotHostFrame(_BotFrame):
    ROOTS=('payload','result','error')
    def __init__(self,operation,payload):
        self.operation,self.payload=operation,payload
        self.phase='start';self.result=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,External
        from .suspension import Effect
        from .bot_values import data,fail
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            if e.guidance_handler is None or e.effect_handler is None:
                fail('Native Bot needs the active run admission host','capability_error')
            e.tick();self.phase='receipt'
            return External(Effect('guidance',{'operation':self.operation,'payload':data(self.payload)}))
        e.tick();self.result=data(value);return Done(self.result)


class BotCallbackFrame(_BotFrame):
    ROOTS=('function','args','value','pending_task','bridge','error')
    def __init__(self,function,args):
        self.function,self.args=function,args
        self.phase='start';self.created=0
        self.value=self.pending_task=self.bridge=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push,InvocationFrame,InvocationRequest,ProtocolBridge
        from .contexts import _await,_operation
        from .suspension import Task,Generator
        from .iteration import HANDLES
        from .multiple_values import primary
        from .bot_values import data,fail
        e=cursor.owner
        if self.phase=='await':
            self.error=error
            action=self.bridge.step(cursor,value,error)
            if type(action) is not Done:return action
            value=action.value;error=None;self.bridge=None;self.phase='finish'
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            self.created=len(e.suspension.tasks);self.phase='invoked'
            return Push(InvocationFrame(InvocationRequest(self.function,self.args,options={'all_results':True})))
        self.value=value
        if self.phase=='invoked' and isinstance(value,Task):
            self.pending_task=value
            protocol_owned=_operation(e,value,e.suspension.tasks[self.created:])
            self.bridge=ProtocolBridge(_await(e,value,protocol_owned,all_results=True));self.bridge.owner=e
            self.phase='await';return self.step(cursor,None,None)
        self.value=primary(self.value,e)
        if isinstance(self.value,(Task,Generator)+HANDLES):fail('Bot callbacks require explicit stream consumption','bot_error')
        self.value=data(self.value);return Done(self.value)


class BotDecisionFrame(_BotFrame):
    ROOTS=('packet','result','error')
    def __init__(self,scope_id,revision,phase='execution'):
        self.scope_id,self.revision,self.decision_phase=scope_id,revision,phase
        self.phase='start';self.packet=self.result=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push
        from .bot_runtime import Decision
        from .bot_values import fail
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            self.phase='receipt';return Push(BotHostFrame('bot_decision',{'scopeId':self.scope_id,'revision':self.revision,'phase':self.decision_phase}))
        self.packet=value
        if (type(value) is not dict or set(value)!={'admitted','revision','decisionId','ticket'}
                or type(value['admitted']) is not bool or type(value['revision']) is not int or value['revision']<0):
            fail('Invalid Bot decision receipt')
        if not value['admitted']:
            if value['decisionId'] is not None or value['ticket'] is not None:fail('Rejected Bot decision has an identity')
            return Done(None)
        if value['revision']!=self.revision:fail('Bot admission changed its revision')
        if any(type(value[key]) is not str or not value[key] for key in ('decisionId','ticket')):fail('Invalid Bot decision identity')
        self.result=Decision(e,e.suspension.epoch,self.scope_id,self.revision,value['decisionId'],value['ticket'],phase=self.decision_phase)
        return Done(self.result)


class BotPhaseBodyFrame(_BotFrame):
    ROOTS=('token','function','args','refreshed','outcome')
    def __init__(self,token,function,args):
        self.token,self.function,self.args=token,function,args
        self.phase='start';self.refreshed=self.outcome=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push
        from .task_groups import ScopeOutcome
        from .bot_values import fail
        try:
            if error is not None:raise error
            if self.phase=='start':
                self.phase='admission';return Push(BotDecisionFrame(self.token.scope_id,self.token.revision,self.token.phase))
            if self.phase=='admission':
                self.refreshed=value
                if value is None:fail('Guidance changed before Bot callback entry','bot_stale')
                self.token.decision_id,self.token.ticket=value.decision_id,value.ticket
                self.token.body_entered=True;self.phase='callback'
                return Push(BotCallbackFrame(self.function,self.args))
            self.outcome=ScopeOutcome(value=value)
        except BaseException as failure:self.outcome=ScopeOutcome(error=failure)
        return Done(self.outcome)


def _abort_owned(e,token):
    from .suspension import TERMINAL
    for handle in tuple(e.suspension.generators)+tuple(e.suspension.iteration.handles):
        if getattr(handle,'bot_guard',None) is token and handle.state not in TERMINAL and handle in e.suspension.generators:
            e.suspension._abort_stream(handle)


class BotCleanupFrame(_BotFrame):
    ROOTS=('handles','original','handle','manager','bridge','outcome')
    def __init__(self,handles,original):
        self.handles,self.original=handles,original
        self.phase='next';self.index=0;self.handle=self.manager=self.bridge=self.outcome=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,ProtocolBridge
        from .task_groups import ScopeOutcome
        from .suspension import AsyncGenerator,hard_error
        from .iteration import AsyncIterator
        e=cursor.owner
        try:
            if self.phase=='close':
                action=self.bridge.step(cursor,value,error)
                if type(action) is not Done:return action
                self.bridge=None;self.index+=1;self.phase='next';error=None
            if error is not None:raise error
            while self.index<len(self.handles):
                self.handle=self.handles[self.index];self.manager=getattr(self.handle,'context_manager',None)
                if self.manager is not None:
                    if self.manager.state=='created':
                        from .contexts import abandon
                        abandon(e,self.manager)
                    self.index+=1;continue
                if hard_error(self.original):
                    if self.handle in e.suspension.generators:e.suspension._abort_stream(self.handle)
                    self.index+=1;continue
                self.bridge=ProtocolBridge(e.suspension.iteration.close(self.handle,isinstance(self.handle,(AsyncGenerator,AsyncIterator))))
                self.bridge.owner=e;self.phase='close';return self.step(cursor,None,None)
            self.outcome=ScopeOutcome()
        except BaseException as failure:self.outcome=ScopeOutcome(error=failure)
        return Done(self.outcome)


class BotCloseOwnedFrame(_BotFrame):
    ROOTS=('token','original','handles','group','outcome','error')
    def __init__(self,token,original):
        self.token,self.original=token,original
        self.phase='start';self.handles=();self.group=self.outcome=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,External,request_proxy
        from .bot_runtime import BotRequest
        from .task_groups import create,GroupRun
        from .suspension import TERMINAL,hard_error,error_priority
        e=cursor.owner
        if self.phase=='start':
            if error is not None:raise error
            self.handles=tuple(handle for handle in tuple(e.suspension.generators)+tuple(e.suspension.iteration.handles)
                               if getattr(handle,'bot_guard',None) is self.token and handle.state not in TERMINAL)
            if not self.handles:return Done(None)
            if hard_error(self.original):_abort_owned(e,self.token);return Done(None)
            self.group=create(e);self.token.group=self.group;self.phase='cleanup'
            return External(GroupRun(self.group,request_proxy(BotRequest('cleanup',(self.handles,self.original)))))
        try:
            if error is not None:raise error
            self.outcome=value
            if value.error is not None:raise value.error
        except BaseException as failure:
            self.error=failure
            if self.original is None or error_priority(failure)>error_priority(self.original):raise
        return Done(None)


class BotPhaseFrame(_BotFrame):
    ROOTS=('token','function','args','group','outcome','failure','authority_runner','previous_guard')
    def __init__(self,token,function,args):
        self.token,self.function,self.args=token,function,args
        self.phase='start';self.group=self.outcome=self.failure=None
        self.authority_runner=self.previous_guard=None;self.entered=False
    def restore_guard(self):
        if self.entered:
            self.entered=False;self.authority_runner.bot_guard=self.previous_guard
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push,External,request_proxy
        from .bot_runtime import BotRequest
        from .task_groups import create,GroupRun
        e=cursor.owner
        if self.phase=='start':
            if error is not None:raise error
            self.group=create(e);self.token.group=self.group
        try:
            if self.phase=='start':
                self.authority_runner=e.suspension.current;self.previous_guard=getattr(self.authority_runner,'bot_guard',None)
                self.authority_runner.bot_guard=self.token;self.entered=True;self.phase='body'
                return External(GroupRun(self.group,request_proxy(BotRequest('phase_body',(self.token,self.function,self.args)))))
            if self.phase=='body':
                self.failure,self.outcome=error,value;self.phase='close_owned'
                original=self.failure or self.outcome.error or self.outcome.signal
                return Push(BotCloseOwnedFrame(self.token,original))
            if error is not None:raise error
            if self.failure is not None:raise self.failure
            self.restore_guard()
            if self.outcome.error is not None:raise self.outcome.error
            if self.outcome.signal is not None:raise self.outcome.signal
            self.token.active=False;return Done(self.outcome.value)
        except BaseException:
            self.restore_guard();self.token.active=False;raise
    def close(self,cursor):
        if self.phase=='start':return
        try:_abort_owned(cursor.owner,self.token)
        finally:self.restore_guard();self.token.active=False


class BotDrainFrame(_BotFrame):
    ROOTS=('bot','packet','message','adopted','guidance','error')
    def __init__(self,bot,scope_id):
        self.bot,self.scope_id=bot,scope_id
        self.phase='start';self.packet=self.message=self.adopted=self.guidance=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push
        from .bot_values import data,fail
        from .guidance_values import response
        if error is not None:self.error=error;raise error
        if self.phase=='receive':
            self.packet=value
            if type(value) is not dict or set(value)!={'received','message','closed','revision'} or type(value['revision']) is not int:fail('Invalid Bot guidance receipt')
            response({key:value[key] for key in ('received','message','closed')})
            if value['closed']:fail('Bot mailbox closed','bot_state_error')
            if not value['received']:return Done(None)
            self.message=value['message'];state=self.bot.state
            if not (self.message['revision']<=state.revision and any(item['messageId']==self.message['messageId'] for item in state.guidance)):
                self.guidance=data(state.guidance+[self.message]);state.guidance=self.guidance;state.revision=self.message['revision']
            self.phase='adopt'
            return Push(BotHostFrame('bot_adopt',{'scopeId':self.scope_id,'messageId':self.message['messageId'],'revision':self.message['revision']}))
        if self.phase=='adopt':
            self.adopted=value
            if type(value) is not dict or value.get('applied') is not True or value.get('revision')!=self.message['revision']:fail('Invalid Bot adoption receipt')
        self.phase='receive';return Push(BotHostFrame('bot_receive',{'scopeId':self.scope_id}))


class BotOutcomeFrame(_BotFrame):
    ROOTS=('bot','token','packet','row','history','error')
    def __init__(self,bot,scope_id,token):
        self.bot,self.scope_id,self.token=bot,scope_id,token
        self.phase='start';self.packet=self.row=self.history=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push
        from .bot_values import data,fail
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            if not self.token.decision_id:return Done({'revision':self.token.revision,'actions':[]})
            self.phase='receipt';return Push(BotHostFrame('bot_outcome',{'scopeId':self.scope_id,'decisionId':self.token.decision_id}))
        self.packet=value
        if type(value) is not dict or set(value)!={'revision','actions'} or type(value['revision']) is not int or value['revision']<0 or type(value['actions']) is not list:fail('Invalid Bot outcome receipt')
        for row in value['actions']:
            if (type(row) is not dict or not {'id','kind','status'}<=set(row) or set(row)-{'id','kind','status','errorCode'}
                    or type(row['id']) is not str or not row['id'] or type(row['kind']) is not str or not row['kind']
                    or row['status'] not in ('admitted','completed','uncertain','failed')
                    or 'errorCode' in row and row['errorCode'] is not None and type(row['errorCode']) is not str):fail('Invalid Bot action outcome')
        self.row={'kind':'decision','decisionId':self.token.decision_id,'revision':self.token.revision,'phase':self.token.phase,'actions':value['actions']}
        self.history=[item for item in self.bot.state.history if item.get('decisionId')!=self.token.decision_id]
        self.bot.state.history=data(self.history+[self.row]);return Done(self.packet)


class BotCommitFrame(_BotFrame):
    ROOTS=('bot','context','output','checkpoint','receipt','error')
    def __init__(self,bot,scope_id,terminal,done,context,output):
        self.bot,self.scope_id,self.terminal,self.done=bot,scope_id,terminal,done
        self.context,self.output=context,output
        self.phase='start';self.checkpoint=self.receipt=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Done,Push
        from .bot_values import data,snapshot
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            self.checkpoint=snapshot(self.bot);self.checkpoint['context']=data(self.context);self.phase='receipt'
            return Push(BotHostFrame('bot_commit',{'scopeId':self.scope_id,'revision':self.bot.state.revision,
                'terminal':self.terminal,'done':self.done,'snapshot':self.checkpoint,'output':data(self.output)}))
        self.receipt=value;return Done(self.receipt)


class BotRunFrame(_BotFrame):
    ROOTS=('bot','state','input','token','parents','ancestry','proposal','planning','completed','verified','context','output','receipt','original','error','result','opening','opened','authority_runner','previous_authority','previous_team')
    EXECUTION={'start_execute','execute','execution_outcome','verify','verification_outcome','commit'}
    def __init__(self,bot,input):
        self.bot,self.input=bot,input;self.state=bot.state;self.phase='start'
        self.scope_id=None;self.rounds=0;self.entered=False;self.lease_owned=False;self.authority_entered=False;self.retiring=False
        self.parents=self.ancestry=();self.inherited_count=0
        for name in ('token','proposal','planning','completed','verified','context','output','receipt','original','error','result','opening','opened','authority_runner','previous_authority','previous_team'):setattr(self,name,None)
    def restore_authority(self):
        if self.authority_entered:
            self.authority_entered=False
            if self.authority_runner is not None:
                self.authority_runner.capabilities=self.previous_authority;self.authority_runner.team_scope=self.previous_team
    def retire(self,e,failure=None,value=None):
        from .execution_frames import Done
        from .bot_values import data,fail
        from .suspension import error_priority
        from .agent_tools import current_caps
        from .execution_scope import current as current_team
        self.retiring=True;self.restore_authority()
        try:
            if self.lease_owned and self.scope_id is not None:
                try:
                    team=current_team(e)
                    closed=data(e.guidance_handler('bot_close',{'scopeId':self.scope_id,'status':self.state.status},current_caps(e),data(team) if team is not None else None))
                    if closed!={'closed':True}:fail('Invalid Bot close receipt')
                except BaseException as cleanup:
                    if self.entered:self.state.status='needs_reconciliation'
                    if self.original is None or error_priority(cleanup)>error_priority(self.original):raise
        finally:
            if self.lease_owned:self.state.busy=False;self.lease_owned=False
        if failure is not None:raise failure
        self.result=value;return Done(self.result)
    def finish(self,e):
        from .bot_runtime import result
        return self.retire(e,value=result(self.bot))
    def make_token(self,e,planning=False):
        from .bot_runtime import Decision
        self.token=Decision(e,e.suspension.epoch,self.scope_id,self.state.revision,'','',phase='planning' if planning else 'execution',ancestry=self.ancestry,parents=self.parents,inherited_count=self.inherited_count)
    def plan_failure(self,e,error):
        from .bot_runtime import external_prefix
        from .control_prompts import NonlocalTransfer
        from .suspension import hard_error
        from .execution_frames import Push
        if getattr(error,'code',None)=='bot_stale' and getattr(error,'scope_id',None) not in (None,self.scope_id):raise error
        self.entered=self.token.body_entered
        if hard_error(error) or isinstance(error,NonlocalTransfer):
            self.original=error
            if self.entered:self.state.status='needs_reconciliation'
            raise error
        self.error=error;self.phase='planning_error_outcome'
        return Push(BotOutcomeFrame(self.bot,self.scope_id,self.token))
    def execution_failure(self,e,error):
        from .execution_frames import Push
        from .bot_runtime import external_prefix
        from .suspension import hard_error
        from .control_prompts import NonlocalTransfer
        if getattr(error,'code',None)=='bot_stale' and getattr(error,'scope_id',None) not in (None,self.scope_id):raise error
        self.entered=self.entered or self.token is not None and self.token.body_entered
        if not self.entered and not external_prefix(self.planning) and getattr(error,'code',None)=='bot_stale':
            self.phase='round';return self.advance(e,None,None)
        self.state.status='needs_reconciliation';self.original=self.error=error
        if hard_error(error) or isinstance(error,NonlocalTransfer):raise error
        if self.token is not None:
            self.phase='execution_error_outcome';return Push(BotOutcomeFrame(self.bot,self.scope_id,self.token))
        return self.finish(e)
    def step(self,cursor,value,error):
        from .suspension import error_priority
        e=cursor.owner
        try:
            if self.phase=='execution_error_outcome':
                if error is not None and error_priority(error)>error_priority(self.error):raise error
                return self.finish(e)
            if self.phase=='plan':
                try:return self.advance(e,value,error)
                except BaseException as failure:return self.plan_failure(e,failure)
            if self.phase in self.EXECUTION:
                try:return self.advance(e,value,error)
                except BaseException as failure:
                    if self.retiring:raise
                    return self.execution_failure(e,failure)
            return self.advance(e,value,error)
        except BaseException as failure:
            if not self.lease_owned:raise
            self.original=failure;self.state.status='needs_reconciliation' if self.entered else 'failed'
            return self.retire(e,failure=failure)
    def advance(self,e,value,error):
        from .execution_frames import Push
        from .bot_values import owned,data,snapshot,fail
        from .bot_runtime import active_decision,credential,external_prefix,uuid
        if error is not None:raise error
        if self.phase=='start':
            owned(self.bot,e)
            if self.state.busy:fail('Bot already has an active invocation','bot_busy')
            if self.state.status=='needs_reconciliation':fail('Bot requires explicit reconciliation','bot_state_error')
            if e.suspension.current.task.recording_scope is not None:fail('Bot work cannot be Skill-recorded','mode_error')
            if e.guidance_handler is None or e.effect_handler is None:fail('Native Bot needs the active run admission host','capability_error')
            parent=active_decision(e)
            self.parents=parent.parents+(parent,) if parent is not None else ()
            self.ancestry=parent.ancestry+(credential(parent),) if parent is not None else getattr(e,'_inherited_bot_ancestry',())
            self.inherited_count=parent.inherited_count if parent is not None else len(self.ancestry)
            self.state.busy=True;self.state.status='running';self.state.input=data(self.input)
            self.scope_id='bot-scope-'+uuid.uuid4().hex;self.lease_owned=True
            self.authority_runner=e.suspension.current
            if self.authority_runner is not None:
                self.previous_authority=self.authority_runner.capabilities;self.previous_team=self.authority_runner.team_scope
                caps=self.bot.capabilities;previous=self.previous_authority
                self.authority_runner.capabilities=caps if previous is None else previous if caps is None else previous & caps
            self.authority_entered=True
            self.opening={'scopeId':self.scope_id,'botId':self.state.id,'mailbox':self.bot.mailbox}
            if self.bot.role is not None:self.opening['role']=self.bot.role
            self.phase='open';return Push(BotHostFrame('bot_open',self.opening))
        if self.phase=='open':
            self.opened=value
            if (type(value) is not dict or set(value)!={'scopeId','revision'} or value['scopeId']!=self.scope_id
                    or type(value['revision']) is not int or value['revision']<0):fail('Invalid Bot scope receipt')
            self.state.revision=value['revision'];self.rounds=0;self.phase='round'
        if self.phase=='round':
            if self.bot.max_iterations is not None and self.rounds>=self.bot.max_iterations:
                self.phase='exhaustion_drain';return Push(BotDrainFrame(self.bot,self.scope_id))
            e.tick();self.phase='drain';return Push(BotDrainFrame(self.bot,self.scope_id))
        if self.phase=='drain':
            self.make_token(e,True);self.state.iteration+=1;self.rounds+=1;self.phase='plan'
            return Push(BotPhaseFrame(self.token,self.bot.planner,[snapshot(self.bot)]))
        if self.phase=='plan':
            self.proposal=value;self.entered=self.token.body_entered;self.phase='planning_outcome'
            return Push(BotOutcomeFrame(self.bot,self.scope_id,self.token))
        if self.phase=='planning_error_outcome':
            self.entered=external_prefix(value)
            if self.entered:
                self.state.status='needs_reconciliation';self.original=self.error;return self.finish(e)
            if getattr(self.error,'code',None)=='bot_stale':self.phase='round';return self.advance(e,None,None)
            raise self.error
        if self.phase=='planning_outcome':
            self.planning=value;self.entered=external_prefix(value);self.make_token(e)
            self.phase='start_execute'
            try:return self.advance(e,None,None)
            except BaseException as failure:return self.execution_failure(e,failure)
        if self.phase=='start_execute':
            self.phase='execute';return Push(BotPhaseFrame(self.token,self.bot.execute,[self.proposal,snapshot(self.bot)]))
        if self.phase=='execute':
            self.completed=value;self.entered=self.token.body_entered;self.state.evidence=data(self.state.evidence+[self.completed]);self.phase='execution_outcome'
            return Push(BotOutcomeFrame(self.bot,self.scope_id,self.token))
        if self.phase=='execution_outcome':
            self.make_token(e);self.phase='verify';return Push(BotPhaseFrame(self.token,self.bot.verify,[snapshot(self.bot)]))
        if self.phase=='verify':
            self.verified=value;self.phase='verification_outcome';return Push(BotOutcomeFrame(self.bot,self.scope_id,self.token))
        if self.phase=='verification_outcome':
            if type(self.verified) is not dict or set(self.verified)!={'done','context','output'} or type(self.verified['done']) is not bool:fail('Bot verifier requires done/context/output')
            self.context,self.output=data(self.verified['context']),data(self.verified['output']);self.phase='commit'
            terminal=self.verified['done'] or self.bot.max_iterations is not None and self.rounds>=self.bot.max_iterations
            return Push(BotCommitFrame(self.bot,self.scope_id,terminal,self.verified['done'],self.context,self.output))
        if self.phase=='commit':
            self.receipt=value
            if type(value) is not dict or set(value)!={'committed','revision'} or type(value['committed']) is not bool or type(value['revision']) is not int:fail('Invalid Bot result commit')
            if not value['committed']:fail('Guidance changed before Bot result commit','bot_stale')
            if value['revision']!=self.state.revision:fail('Bot result commit changed revision')
            self.state.context,self.state.output=self.context,self.output
            if self.verified['done']:self.state.status='achieved';return self.finish(e)
            if self.bot.max_iterations is not None and self.rounds>=self.bot.max_iterations:self.state.status='exhausted';return self.finish(e)
            self.entered=False;self.phase='round';return self.advance(e,None,None)
        if self.phase=='exhaustion_drain':
            self.phase='exhaustion_commit';return Push(BotCommitFrame(self.bot,self.scope_id,True,False,self.state.context,self.state.output))
        if self.phase=='exhaustion_commit':
            self.receipt=value
            if type(value) is not dict or set(value)!={'committed','revision'} or type(value['committed']) is not bool or type(value['revision']) is not int:fail('Invalid Bot exhaustion commit')
            if value['committed']:
                if value['revision']!=self.state.revision:fail('Bot exhaustion changed revision')
                self.state.status='exhausted';return self.finish(e)
            self.phase='exhaustion_drain';return self.advance(e,None,None)
        raise RuntimeError('Invalid Bot run frame phase')
    def close(self,cursor):
        if self.lease_owned:
            if self.phase=='plan' and self.token is not None:
                self.entered=self.token.body_entered
            elif self.phase in self.EXECUTION and self.token is not None:
                self.entered=self.entered or self.token.body_entered
            failure=GeneratorExit();self.original=failure
            self.state.status='needs_reconciliation' if self.entered else 'failed'
            try:self.retire(cursor.owner)
            finally:self.restore_authority()


BOT_FRAMES=(BotRunFrame,BotHostFrame,BotCallbackFrame,BotPhaseFrame,BotPhaseBodyFrame,
            BotCloseOwnedFrame,BotCleanupFrame,BotDecisionFrame,BotDrainFrame,BotOutcomeFrame,BotCommitFrame)


def bot_frame(request):
    from .bot_runtime import BotRequest
    if type(request) is not BotRequest:return None
    classes={'run':BotRunFrame,'host':BotHostFrame,'callback':BotCallbackFrame,
             'phase':BotPhaseFrame,'phase_body':BotPhaseBodyFrame,'close_owned':BotCloseOwnedFrame,
             'cleanup':BotCleanupFrame,'decision':BotDecisionFrame,'drain':BotDrainFrame,
             'outcome':BotOutcomeFrame,'commit':BotCommitFrame}
    return classes[request.operation](*request.values)
