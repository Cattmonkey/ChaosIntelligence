"""Immutable, execution-free metadata for native values and functions.

These descriptions are tooling contracts, not a second evaluator. Runtime
implementations still enforce arguments, ownership and lifecycle rules.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    type: str = 'Any'
    mode: str = 'positional'
    optional: bool = False
    default_display: object = None


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    nullable: bool = False
    shape: object = None
    optional: bool = False


@dataclass(frozen=True)
class NativeSpec:
    id: str
    parameters: tuple
    result_type: str
    awaited_type: str = 'Any'
    result_shape: object = None
    awaited_shape: object = None
    summary: str = ''
    inference_rule: object = None
    receiver_type: object = None
    compatibility_export: bool = True
    # A returned callable is metadata, not an entry in the global namespace.
    result_callable: object = None


P, F = ParameterSpec, FieldSpec
SHAPES = MappingProxyType({
    'BotInfo': (F('id','String'),F('status','String'),F('busy','Bool'),F('revision','Int'),F('iteration','Int'),F('maxIterations','Int',True),F('needsReconciliation','Bool')),
    'BotContext': (F('goal','Any'),F('context','Any'),F('input','Any'),F('guidance','List'),F('evidence','List'),F('history','List'),F('revision','Int'),F('iteration','Int')),
    'BotResult': (F('done','Bool'),F('status','String'),F('state','Record',shape='BotContext'),F('output','Any')),
    'GuidanceTarget': (F('kind','String'),F('scopeId','String',optional=True),F('roleId','String',optional=True)),
    'GuidanceMessage': (F('runId','String'),F('messageId','String'),F('revision','Int'),F('input','Any'),F('status','String'),F('target','Record',True,shape='GuidanceTarget')),
    'GuidanceResult': (F('received','Bool'),F('message','Record',True,shape='GuidanceMessage'),F('closed','Bool')),
    'ProgressReceipt': (F('runId','String'),F('kind','String'),F('eventId','Int')),
    'ProgramDefinitionInfo': (F('kind','String'),F('entry','String'),F('files','Record'),F('languageVersions','Record')),
    'ProgramMetadataInfo': (F('definitionHash','String'),F('origin','Any')),
    'ProgramIRInfo': (F('schema','String'),F('languageVersion','Int'),F('file','String'),F('root','Record'),F('semanticHash','String')),
    'ProgramHistoryInfo': (F('items','List'),F('nextCursor','Any'),F('currentVersion','Int'),F('firstAvailableVersion','Int')),
    'RobotInfo': (F('id','String'),F('name','String'),F('generation','Int'),F('profile','String'),F('status','String'),F('actuators','Record'),F('maxObservationAgeMs','Int'),F('latestObservationId','String',True)),
    'RobotObservationInfo': (F('id','String'),F('robotId','String'),F('generation','Int'),F('sequence','Int'),F('receivedAtNs','Int'),F('readings','Record')),
    'RobotCommandInfo': (F('id','String'),F('robotId','String'),F('generation','Int'),F('observationId','String'),F('channels','Record'),F('ttlMs','Int'),F('used','Bool')),
    'RobotReceiptInfo': (F('schema','String'),F('id','String'),F('robotId','String'),F('name','String'),F('generation','Int'),F('operation','String'),F('status','String'),F('targetVerified','Bool'),F('commandId','String',True),F('observationId','String',True)),
    'DeviceInfo': (F('id','String'),F('name','String'),F('generation','Int'),F('status','String'),F('profile','String')),
    'DeviceActionInfo': (F('deviceId','String'),F('generation','Int'),F('spec','Record'),F('used','Bool')),
    'DeviceReceiptInfo': (F('schema','String'),F('id','String'),F('deviceId','String'),F('name','String'),F('generation','Int'),F('action','String'),F('status','String'),F('targetVerified','Bool')),
    'ContractInfo': (F('kind','String'),F('name','String',optional=True),F('element','Record',optional=True),F('elements','List',optional=True),F('required','Record',optional=True),F('optional','Record',optional=True),F('allowExtra','Bool',optional=True),F('alternatives','List',optional=True),F('key','Record',optional=True),F('value','Record',optional=True),F('root','String',optional=True),F('definitions','Record',optional=True)),
    'HashMapInfo': (F('size','Int'),F('ordered','Bool'),F('persistent','Bool'),F('maxEntries','Int'),F('keyPolicy','String')),
    'EqlSpecializerInfo': (F('kind','String'),F('comparison','String'),F('valueType','String')),
    'GenericFunctionInfo': (F('name','String'),F('arity','Int'),F('methodCount','Int'),F('qualifiers','List'),F('precedence','String'),F('nextArguments','String')),
    'ErrorFields': (F('code','String'),F('message','String'),F('value','Any'),F('location','Record'),F('exceptions','Tuple'),F('cause','Error',True),F('context','Error',True),F('suppress_context','Bool'),F('instance','Instance')),
    'ContextVarFields': (F('id','String'),F('name','String'),F('has_default','Bool'),),
    'ContextTokenFields': (F('id','String'),F('used','Bool'),F('has_old_value','Bool'),F('var','ContextVar'),F('old_value','Any'),),
    'ContextScopeFields': (F('id','String'),F('state','String'),F('bindingCount','Int'),),
    'ContextFields': (F('id','String'),F('active','Bool'),F('bindingCount','Int'),),
    'DecimalFields': (F('sign','Int'),F('coefficient','Int'),F('exponent','Int')),
    'ConditionFields': (F('kind','String'),F('message','String'),F('data','Any')),
    'RestartFields': (F('id','String'),F('name','String',True),F('report','String'),F('mode','String'),F('active','Bool')),
    'HandlerScopeFields': (F('id','String'),F('state','String'),F('bindingCount','Int')),
    'RestartScopeFields': (F('id','String'),F('state','String'),F('bindingCount','Int'),F('mode','String')),
    'PromptTagFields': (F('id','String'),F('name','String')),
    'PromptScopeFields': (F('id','String'),F('tag','PromptTag',shape='PromptTagFields'),F('state','String'),F('active','Bool')),
    'ContinuationFields': (F('id','String'),F('state','String'),F('available','Bool'),F('frames','Int'),F('invocations','Int')),
    'EscapeContinuationFields': (F('id','String'),F('scopeId','String'),F('state','String'),F('active','Bool'),F('available','Bool')),
    'DecimalContextFields': (F('precision','Int'),F('rounding','String'),F('emin','Int'),F('emax','Int'),F('clamp','Bool'),F('traps','List'),F('flags','List')),
    'FractionFields': (F('numerator','Int'),F('denominator','Int')),
    'ComplexFields': (F('real','Real'),F('imag','Real')),
    'UIArtifactInfo': (F('kind','String'),F('title','String'),F('hash','String'),F('libraryHash','String'),F('nodeCount','Int'),F('actionNames','List')),
    'UIArtifactDefinition': (F('schema','String'),F('kind','String'),F('title','String'),F('source','String'),F('root','Record'),F('libraryHash','String'),F('hash','String')),
    'UIValidation': (F('passed','Bool'),F('checks','List'),F('artifactHash','String')),
    'ClassMember': (F('value','Any'),F('mutable','Bool'),F('method','Bool'),F('contract','Any')),
    'GpuResult': (F('values','List'),F('device','String'),F('backend','String'),F('dtype','String'),
                  F('fallbackReason','String',True)),
    'GpuKernelInfo': (F('requestedCompiler','String'),F('compiler','String',True),F('status','String'),
                      F('irHash','String'),F('sourceHash','String',True),F('sourceCacheHit','Bool',True),
                      F('dispatches','Int',True),F('isolated','Bool',True),F('fallbackReason','String',True)),
    'GpuDevices': (F('backend','String'),F('version','String',True),F('cpu','Bool'),F('mps','Bool'),
                   F('cuda','Bool'),F('devices','List'),F('defaultDevice','String')),
    'CompiledFunctionInfo': (F('schema','String'),F('status','String'),F('requestedTarget','String'),
        F('backend','String'),F('target','String'),F('compilerVersion','String',True),
        F('parameterGuards','List'),F('nodeCount','Int'),F('nativeCalls','Int'),F('interpreterCalls','Int'),
        F('fallbackReason','String',True),F('lastFallbackReason','String',True)),
    'CompilationReport': (F('schema','String'), F('compiler','Record'), F('target','String'),
        F('artifactHash','String'), F('entry','String'), F('languageVersions','Record'),
        F('modules','List'), F('summary','Record'), F('passes','List'), F('limitations','List')),
    'CodeInfo': (F('schema','String'),F('sourceHash','String'),F('semanticHash','String'),F('flavor','String'),F('name','String'),F('parameterCount','Int'),F('nodeCount','Int')),
    'CompiledFlowInfo': (F('schema','String'),F('abi','String'),F('status','String'),
        F('instructionCount','Int'),F('branchCount','Int'),F('checkpointCount','Int'),
        F('resumeCalls','Int'),F('nativeResumes','Int'),F('portableResumes','Int'),
        F('lastPC','Int'),F('definitionPolicy','String')),
    'CompiledOptimizationInfo': (F('schema','String'),F('status','String'),F('enabled','Bool'),
        F('algorithm','String'),F('kernelCount','Int'),F('originalOperations','Int'),
        F('emittedOperations','Int'),F('commonSubexpressions','Int'),F('constantFolds','Int'),
        F('identitySimplifications','Int'),F('checkpointCount','Int'),F('scheduled','Bool'),F('kernels','List')),

    'SymbolReferences': (F('target','Record',True),F('references','List'),F('complete','Bool'),F('limitations','List',optional=True)),
    'ProgramOriginInfo': (F('projectId','String'),F('version','Int'),F('definitionHash','String')),
    'ProgramAdoptionReceipt': (F('previousVersion','Int'),F('version','Int'),F('previousDefinitionHash','String'),
                               F('definitionHash','String'),F('casesHash','String'),F('testCount','Int'),
                               F('runId','String',True),F('reason','String')),
    'ProcessDefinitionInfo': (F('kind','String'),F('name','String'),F('module','String'),F('export','String'),
                              F('intervalSeconds','Number',True),F('maxTurns','Int',True),F('roles','List',optional=True)),
    'ProcessStepInfo': (F('state','Any'),F('output','Any'),F('done','Bool')),
    'ProcessEvolutionInfo': (F('base','Record',shape='ProgramOriginInfo'),F('definitionHash','String'),
                             F('casesHash','String'),F('testCount','Int'),F('state','Any'),F('output','Any'),
                             F('done','Bool'),F('reason','String')),
    'ProcessRecord': (F('key','String'),F('projectId','String'),F('revision','Int'),
                       F('definition','Record',shape='ProcessDefinitionInfo'),F('definitionHash','String'),
                       F('status','String'),F('turn','Int'),F('state','Any'),F('output','Any'),F('pending','Record',True,shape='ProcessPending'),
                       F('nextWakeAt','Number',True),F('lastRunId','String',True),F('lastVersion','Int',True),
                       F('error','String',True),F('launchCapabilities','List'),F('control','String'),F('started','Bool'),
                       F('created','Number'),F('updated','Number'),F('reconciliation','Any'),
                       F('lastEvolution','Record',shape='ProgramAdoptionReceipt',optional=True),F('botRoles','Record',optional=True)),
    'ProcessMessageReceipt': (F('key','String'),F('messageId','String'),F('status','String')),
    'ProcessMessageRun': (F('id','String'),F('version','Int'),F('status','String'),F('output','Any'),
                          F('error','String',True),F('created','Number'),F('updated','Number')),
    'ProcessMessageReconciliation': (F('eventId','Int'),F('runId','String'),F('evidence','String',True),F('at','Number')),
    'ProcessMessageOutcome': (F('key','String'),F('messageId','String'),F('mailboxStatus','String'),F('linkStatus','String'),
                              F('run','Record',True,shape='ProcessMessageRun'),
                              F('reconciliation','Record',True,shape='ProcessMessageReconciliation')),
    'ProcessEventPage': (F('items','List'),F('nextCursor','Int',True),F('lastEventId','Int'),
                         F('historyStartEventId','Int',True),F('legacyHistoryIncluded','Bool')),
    'ProcessGuidanceReceipt': (F('key','String'),F('runId','String',True),F('messageId','String'),F('revision','Int'),F('status','String'),F('target','Record',shape='GuidanceTarget',optional=True)),
    'ProcessBotScope': (F('id','String'),F('terminal','Bool'),F('committed','Bool')),
    'ProcessBotCheckpoint': (F('state','Record',shape='BotContext'),F('output','Any'),F('done','Bool')),
    'ProcessBotWork': (F('phase','String'),F('blocked','Bool'),F('scope','Record',True,shape='ProcessBotScope'),F('actions','List'),F('checkpoint','Record',True,shape='ProcessBotCheckpoint'),F('checkpointRevision','Int'),F('scopes','Int'),F('guidanceRevision','Int')),
    'ProcessPending': (F('runId','String'),F('messageId','String',True),F('turn','Int'),F('version','Int'),F('definitionHash','String'),F('kind','String'),F('processDefinitionHash','String'),F('effectiveCapabilities','List'),F('botWork','Record',shape='ProcessBotWork',optional=True)),
    'AgentToolInfo': (F('name','String'), F('description','String'), F('inputType','String'),
                      F('outputType','String'), F('capabilities','List')),
    'AgentDefinitionInfo': (F('kind','String'), F('name','String'), F('config','Record')),
    'AgentBindingInfo': (F('agent','Record',shape='AgentDefinitionInfo'), F('tools','List')),
    'TeamDefinitionInfo': (F('kind','String'),F('name','String'),F('entry','String'),F('roles','List')),
    'TeamBindingInfo': (F('team','Record',shape='TeamDefinitionInfo'),F('definitionHash','String'),F('bindings','Record')),
    'SessionBindingIdentity': (F('key','String'),F('revision','Int'),F('definitionHash','String')),
    'SessionBindingInfo': (F('session','Record',shape='SessionBindingIdentity'),F('tools','List'),F('bindingHash','String')),
    'SessionToolBinding': (F('kind','String'),F('definitionHash','String'),F('tools','List'),F('bindingHash','String'),F('runId','String'),F('sourceVersion','Int')),
    'SessionInfo': (F('key','String'),F('revision','Int'),F('status','String'),F('turn','Int'),F('definitionHash','String'),F('agent','Record',shape='AgentDefinitionInfo'),F('contextLimit','Int'),F('lastToolBinding','Record',shape='SessionToolBinding',optional=True)),
    'SessionReceipt': (F('key','String'),F('messageId','String'),F('status','String'),F('output','String'),F('revision','Int'),F('replayed','Bool')),
    'ComputerInfo': (F('id','String'), F('backend','String'), F('status','String'), F('binding','String',optional=True), F('profile','String',optional=True), F('actions','List',optional=True),
                     F('sessionKey','String',optional=True), F('stableSessionId','String',optional=True),
                     F('generation','Int',optional=True), F('requiresReconcile','Bool',optional=True), F('continued','Bool',optional=True)),
    'ObservationInfo': (F('backend','String',optional=True), F('binding','String',optional=True), F('profile','String',optional=True), F('actions','List',optional=True), F('id','String'), F('runId','String'), F('sourceVersion','Int'), F('computerId','String'),
                        F('sessionId','String'), F('controlEpoch','Int'), F('snapshotId','Int'), F('url','String'),
                        F('title','String'), F('text','String'), F('targets','List'), F('viewport','Record'),
                        F('cursor','Record'), F('image','Any'), F('truncated','Record'), F('receipt','Any'),
                        F('sessionKey','String',optional=True), F('stableSessionId','String',optional=True),
                        F('generation','Int',optional=True), F('requiresReconcile','Bool',optional=True), F('continued','Bool',optional=True)),
    'ComputerActionInfo': (F('spec','Record'), F('observationId','String'), F('computerId','String'), F('used','Bool')),
    'TaskTiming': (F('seconds','Number',True), F('until','String',True),
                   F('timeoutSeconds','Number',True), F('remainingSeconds','Number',True)),
    'TaskInfo': (F('id','String'), F('state','String'), F('phase','String'), F('kind','String'),
                 F('parentTaskId','String',True), F('groupId','String',True), F('channelId','String',True),
                 F('memberTaskIds','List'), F('memberCount','Int'), F('membersTruncated','Bool'),
                 F('cancellationRequested','Bool'), F('cancellationDeferred','Bool'), F('shieldDepth','Int'),
                 F('observed','Bool'), F('location','Record'),
                 F('timing','Record',True,'TaskTiming'), F('selection','Record',True,'TaskSelection')),
    'TaskSelection': (F('caseCount','Int'), F('cases','List'), F('casesTruncated','Bool'), F('index','Int',True)),
    'ChannelCaseInfo': (F('id','String'), F('op','String'), F('channelId','String'), F('location','Record')),
    'SelectResult': (F('index','Int',True), F('op','String',True), F('done','Bool'), F('value','Any'), F('timedOut','Bool')),
    'CancelShieldInfo': (F('id','String'), F('state','String'), F('taskId','String',True),
                         F('location','Record'), F('cancellationRequested','Bool'),
                         F('cancellationDeferred','Bool'), F('depth','Int')),
    'TaskGroupInfo': (F('id','String'), F('state','String'), F('ownerTaskId','String',True),
                      F('bodyTaskId','String',True), F('parentGroupId','String',True),
                      F('memberTaskIds','List'), F('memberCount','Int'), F('membersTruncated','Bool'), F('location','Record')),
    'ChannelInfo': (F('id','String'), F('state','String'), F('capacity','Int'), F('buffered','Int'),
                    F('waitingSenders','Int'), F('waitingReceivers','Int'), F('sendTaskIds','List'),
                    F('receiveTaskIds','List'), F('sendersTruncated','Bool'), F('receiversTruncated','Bool'), F('location','Record')),
    'WaitResult': (F('done','List'), F('pending','List'), F('timedOut','Bool')),
    'IterationStep': (F('done','Bool'), F('value','Any')),
})

_STACK_SPECS = tuple(
    spec
    for stack in ('ExitStack', 'AsyncExitStack')
    for spec in (
        NativeSpec(stack+'.enter_context',(P('manager'),),'Any',summary='同步进入资源，捕获退出并按后进先出释放；保留进入值类型',inference_rule='stack_enter',receiver_type=stack),
        NativeSpec(stack+'.callback',(P('function','Function',mode='positional_only'),P('args',mode='varargs'),P('kwargs',mode='varkw')),'Function',summary='登记同步清理回调；返回原回调；清理返回值不抑制异常',inference_rule='stack_callback',receiver_type=stack),
        NativeSpec(stack+'.push',(P('exit_function'),),'Any',summary='登记退出回调或捕获类资源退出方法；不进入资源，返回原值；Bool 返回值决定异常抑制',inference_rule='stack_callback',receiver_type=stack),
        NativeSpec(stack+'.pop_all',(),stack,summary='转移所有退出项到同类型新栈；原栈变空，不执行清理',inference_rule='stack_transfer',receiver_type=stack),
    )
) + (
    NativeSpec('ExitStack.close',(),'Null',summary='按后进先出同步释放所有资源与回调',receiver_type='ExitStack'),
    NativeSpec('AsyncExitStack.enter_async_context',(P('manager'),),'Task',summary='冷 Task；await 后进入异步资源、登记退出并保留进入值类型',inference_rule='stack_enter_async',receiver_type='AsyncExitStack'),
    NativeSpec('AsyncExitStack.push_async_callback',(P('function','Function',mode='positional_only'),P('args',mode='varargs'),P('kwargs',mode='varkw')),'Function',summary='登记异步清理回调；返回原回调；退出时 await，返回值不抑制异常',inference_rule='stack_callback',receiver_type='AsyncExitStack'),
    NativeSpec('AsyncExitStack.push_async_exit',(P('exit_function'),),'Any',summary='登记异步退出回调或捕获类资源异步退出方法；不进入资源，返回原值；退出时 await Bool 决定异常抑制',inference_rule='stack_callback',receiver_type='AsyncExitStack'),
    NativeSpec('AsyncExitStack.aclose',(),'Task',awaited_type='Null',summary='冷 Task；await 后按后进先出释放同步与异步资源',receiver_type='AsyncExitStack'),
)

_NUMERIC_SPECS = (
    NativeSpec('decimal',(P('value','String | Int | Decimal | Fraction | Number',optional=True,default_display='0'),),'Decimal',summary='不可变有限十进制；String/Int/Decimal 精确；Fraction 显式按当前精度舍入；Number 显式保留二进制精确值并提交 FloatOperation 信号'),
    NativeSpec('decimal_text',(P('value','Decimal'),),'String',summary='精确十进制文本；保留尾零 quantum 与负零，不转换为浮点'),
    NativeSpec('decimal_context',(P('precision','Int',optional=True,default_display='28'),P('rounding','String',optional=True,default_display='"half_even"'),P('emin','Int',optional=True,default_display='-999'),P('emax','Int',optional=True,default_display='999'),P('clamp','Bool',optional=True,default_display='false'),P('traps','List',optional=True,default_display='["InvalidOperation", "DivisionByZero", "Overflow"]')),'DecimalContext',summary='本轮逻辑上下文配置；with 创建清空 flags 的活动副本，退出恢复；八种舍入和九种信号，字段只读'),
    NativeSpec('get_decimal_context',(),'DecimalContext',summary='读取当前语言逻辑上下文身份；不启动任务，不读取宿主全局状态'),
    NativeSpec('decimal_context_info',(P('context','DecimalContext | Null',optional=True,default_display='null'),),'Record',result_shape='DecimalContextFields',summary='只读复制上下文七字段；默认当前逻辑上下文，traps/flags 为排序信号名列表'),
    NativeSpec('decimal_clear_flags',(P('context','DecimalContext | Null',optional=True,default_display='null'),),'Null',summary='仅清除当前活动逻辑上下文 flags；拒绝其他配置、兄弟任务或过期句柄'),
    NativeSpec('decimal_quantize',(P('value','Decimal'),P('quantum','Decimal')),'Decimal',summary='按当前上下文量化为指定指数；提交 Rounded/Inexact 等信号，不静默转换浮点'),
    NativeSpec('fraction',(P('numerator','Int',optional=True,default_display='0'),P('denominator','Int',optional=True,default_display='1')),'Fraction',summary='有界精确分数；规范化为互素整数和正分母；只读 numerator/denominator，不接受 Bool'),
    NativeSpec('complex',(P('real','Real',optional=True,default_display='0'),P('imag','Real',optional=True,default_display='0')),'Complex',summary='不可变原生复数；精确部件保留精确值；无 Decimal 域时 float 提升两部件，Decimal 域只允许 Decimal/Int；只读 real/imag'),
    NativeSpec('conjugate',(P('value','Numeric'),),'Numeric',summary='原生数值共轭；保留实数，Complex 仅反转虚部；不调用用户钩子'),
    NativeSpec('numeric_encode',(P('value','Numeric'),),'Record',summary='单个原生数值的无损显式 $numeric JSON 记录；整数与分数部件用规范十进制字符串'),
    NativeSpec('numeric_decode',(P('value','Record'),),'Numeric',summary='严格校验单个 $numeric 记录并显式恢复原生数值；不遍历普通容器，不自动恢复 JSON 标记'),
    NativeSpec('number',(P('s','String | Real'),),'Number',summary='显式有限浮点转换；String 或 Real，Fraction 可能近似；不接受 Complex 或 Bool'),
    NativeSpec('floor',(P('x','Real'),),'Int',summary='实数向下取整；Fraction 使用精确整数算法'),
    NativeSpec('ceil',(P('x','Real'),),'Int',summary='实数向上取整；Fraction 使用精确整数算法'),
    NativeSpec('round',(P('x','Real'),),'Int',summary='实数取最近整数；Fraction 精确处理中点并取偶数'),
    NativeSpec('sqrt',(P('x','Real'),),'Real',summary='非负实数平方根；Decimal 使用当前精度和固定 half_even 返回 Decimal，其余显式有限浮点近似；不返回 Complex'),
    NativeSpec('sum',(P('xs','List'),),'Numeric',summary='Numeric 列表累计；保持精确值，混合浮点按共享提升规则；空列表返回 Int 0'),
    NativeSpec('min',(P('xs','List'),),'Real',summary='非空 Real 列表最小值；精确混合比较，不接受 Complex'),
    NativeSpec('max',(P('xs','List'),),'Real',summary='非空 Real 列表最大值；精确混合比较，不接受 Complex'),
    NativeSpec('sort',(P('xs','List'),),'List',summary='Real 列表或 String 列表排序；精确比较，保持原值类型，不接受 Complex'),
)

_RECOVERY_SPECS = (
    NativeSpec('condition',(P('kind','String'),P('message','String',optional=True,default_display='""'),P('data','Any',optional=True,default_display='null')),'Condition',summary='不可变纯情况数据；复制有界容器/精确数值，不携带活句柄或权限'),
    NativeSpec('condition_info',(P('value','Condition'),),'Record',result_shape='ConditionFields',summary='只读复制 kind/message/data，不执行处理器'),
    NativeSpec('handler_bind',(P('bindings','List'),),'HandlerScope',summary='同步with安装动态处理器组；predicate严格Bool，handler正常返回拒绝到外层组'),
    NativeSpec('restart_bind',(P('bindings','List'),),'RestartScope',summary='同步with安装inline恢复出口；调用后返回原invocation位置'),
    NativeSpec('restart_case',(P('body','Function'),P('bindings','List')),'Any',summary='原AST动态恢复范围；invoke_restart真实展开内层清理后调用case函数；直接async回调透明执行'),
    NativeSpec('signal_condition',(P('value','Condition'),),'Null',summary='在情况现场调用动态处理器；未处理返回Null，返回冷句柄的handler报协议错误'),
    NativeSpec('error_condition',(P('value','Condition'),),'Any',summary='同一情况处理流程；默认未恢复抛condition_error；显式pauseOnCondition可在原现场选择inline返回或case展开；不转换旧throw或硬取消'),
    NativeSpec('invoke_debugger',(P('value','Condition'),),'Any',summary='当前原AST情况现场交互暂停；精确活动Restart恢复，inline返回值/case展开；无会话或拒绝抛condition_error'),
    NativeSpec('compute_restarts',(P('condition','Condition | Null',optional=True,default_display='null'),),'List',summary='查询当前逻辑动态恢复出口；显式执行可选test回调，严格Bool'),
    NativeSpec('find_restart',(P('name','String'),P('condition','Condition | Null',optional=True,default_display='null')),'Any',summary='按由内到外/声明顺序返回真实Restart或Null；执行可选test'),
    NativeSpec('invoke_restart',(P('restart','Restart | String'),P('args','List',optional=True,default_display='[]'),P('kwargs','Record',optional=True,default_display='{}')),'Any',summary='调用当前逻辑活动出口；inline返回，case非局部转移；始终交集权限，不隐式await冷返回值'),
    NativeSpec('restart_info',(P('value','Restart'),),'Record',result_shape='RestartFields',summary='只读出口身份/名字/report/mode/active，不执行test或恢复函数'),
)

_CONTEXT_SPECS = (
    NativeSpec('context_var',(P('name','String'),P('default','Any',optional=True,default_display='<missing>'),),'ContextVar',summary='创建身份独立的变量；缺失与显式null不同，name仅诊断'),
    NativeSpec('context_get',(P('variable','ContextVar'),P('default','Any',optional=True,default_display='<missing>'),),'Any',summary='绑定优先于显式fallback和声明default；全部缺失抛lookup错误'),
    NativeSpec('context_set',(P('variable','ContextVar'),P('value'),),'ContextToken',summary='保存原生语言值，返回单次原Context恢复Token'),
    NativeSpec('context_reset',(P('variable','ContextVar'),P('token','ContextToken'),),'Null',summary='检查变量、原Context、owner/epoch及未消费Token，再恢复原绑定'),
    NativeSpec('context_bind',(P('bindings','List'),),'ContextScope',summary='事务验证绑定，同步with按选定变量恢复；退出栈须LIFO'),
    NativeSpec('context',(),'Context',summary='创建空绑定Context，不保存权限或活动恢复栈'),
    NativeSpec('copy_context',(),'Context',summary='当前绑定有界O(n)浅复制，保留值身份，任务创建使用同一语义'),
    NativeSpec('context_copy',(P('context','Context'),),'Context',summary='指定Context绑定浅复制，声明缺省值不计入条目'),
    NativeSpec('context_get_in',(P('context','Context'),P('variable','ContextVar'),P('default','Any',optional=True,default_display='<missing>'),),'Any',summary='只读取Context显式条目；不使用变量声明default'),
    NativeSpec('context_has',(P('context','Context'),P('variable','ContextVar'),),'Bool',summary='仅检查显式变量条目，不执行回调或读取payload'),
    NativeSpec('context_items',(P('context','Context'),),'List',summary='显式变量和值的Tuple列表，读取保留所有权及权限上限'),
    NativeSpec('context_info',(P('value'),),'Record',summary='只读句柄元数据，不导出default、绑定或Token旧值'),
    NativeSpec('context_run',(P('context','Context'),P('function','Function'),P('args','List',optional=True,default_display='[]'),P('kwargs','Record',optional=True,default_display='{}'),),'Any',summary='指定Context执行原AST直接async回调；普通返回Task/Generator保持冷句柄，finally恢复调用者'),
    NativeSpec('ContextVar.get',(P('default','Any',optional=True,default_display='<missing>'),),'Any',summary='当前绑定、显式fallback、声明default按顺序读取',receiver_type='ContextVar',compatibility_export=False),
    NativeSpec('ContextVar.set',(P('item'),),'ContextToken',summary='返回可读取旧值且只能reset一次的原Context Token',receiver_type='ContextVar',compatibility_export=False),
    NativeSpec('ContextVar.reset',(P('token','ContextToken'),),'Null',summary='原变量/Context/epoch Token恢复；活动with管理的Token不能手动reset',receiver_type='ContextVar',compatibility_export=False),
    NativeSpec('Context.get',(P('variable','ContextVar'),P('default','Any',optional=True,default_display='<missing>'),),'Any',summary='读取显式绑定；缺失时只使用显式fallback',receiver_type='Context',compatibility_export=False),
    NativeSpec('Context.copy',(),'Context',summary='有界浅复制关联表；返回Context具有相同原生成员',receiver_type='Context',compatibility_export=False),
    NativeSpec('Context.run',(P('function','Function'),P('args','List',optional=True,default_display='[]'),P('kwargs','Record',optional=True,default_display='{}'),),'Any',summary='原函数args/kwargs调用，直接async透明，生成器及冷句柄不隐式消费',receiver_type='Context',compatibility_export=False),
    NativeSpec('Context.has',(P('variable','ContextVar'),),'Bool',summary='查询显式绑定成员',receiver_type='Context',compatibility_export=False),
    NativeSpec('Context.keys',(),'List',summary='原ContextVar身份列表；不读取payload',receiver_type='Context',compatibility_export=False),
    NativeSpec('Context.values',(),'List',summary='读取原生值并保留权限上限',receiver_type='Context',compatibility_export=False),
    NativeSpec('Context.items',(),'List',summary='变量和值Tuple条目列表',receiver_type='Context',compatibility_export=False),
)

_GENERIC_SPECS = (
    NativeSpec('generic_eql',(P('value'),),'EqlSpecializer',summary='构造按实际值特化的原生选择器；同型标量按纯值比较，其余值严格按身份；不调用相等或哈希协议'),
    NativeSpec('generic_eql_info',(P('specializer','EqlSpecializer'),),'Record',result_shape='EqlSpecializerInfo',summary='验证所有权和隐藏值当前生命周期，返回kind/comparison/valueType诊断；不暴露隐藏值或地址'),
    NativeSpec('generic_function',(P('name','String'),P('arity','Int',optional=True,default_display='1')),'Function',inference_rule='generic_callable',result_callable=NativeSpec('Generic Function',(P('args',mode='varargs'),P('kwargs',mode='varkw')),'Any',summary='按真实类型分派；返回值可能为冷 Task/Generator，不隐式等待'),summary='创建不可变空方法表；按前 arity 个位置参数的实际值／类型选择器分派，arity 为 1..32；调用结果保守为 Any'),
    NativeSpec('generic_method',(P('function','Function'),P('specializers','List'),P('implementation','Function'),P('qualifier','String',optional=True,default_display='"primary"'),P('with_next','Bool',mode='keyword_only',optional=True,default_display='false')),'Function',inference_rule='generic_callable',result_callable=NativeSpec('Generic Function',(P('args',mode='varargs'),P('kwargs',mode='varkw')),'Any',summary='按真实类型分派；返回值可能为冷 Task/Generator，不隐式等待'),summary='持久新增或替换方法；仅 primary 可显式 with_next=true，接收(next,原实参)，next严格零参且只调用后续primary；旧方法签名不变'),
    NativeSpec('generic_remove',(P('function','Function'),P('specializers','List'),P('qualifier','String',optional=True,default_display='"primary"')),'Function',inference_rule='generic_callable',result_callable=NativeSpec('Generic Function',(P('args',mode='varargs'),P('kwargs',mode='varkw')),'Any',summary='按真实类型分派；返回值可能为冷 Task/Generator，不隐式等待'),summary='返回移除指定方法的新泛型函数；未找到时方法表不变；不修改已有调用或旧版本'),
    NativeSpec('generic_methods',(P('function','Function'),),'List',summary='复制方法记录列表，含 specializers、implementation、qualifier；仅启用的 primary 另含 withNext:true；保留真实类型与可调用值并投影当前权限；投影后的函数对象身份可能不同；不执行方法'),
    NativeSpec('generic_info',(P('function','Function'),),'Record',result_shape='GenericFunctionInfo',summary='惰性查询 name/arity/methodCount/qualifiers/precedence/nextArguments；不分派或运行实现'),
    NativeSpec('generic_next_available',(P('next','Function'),),'Bool',summary='查询真实泛型 next 是否还有下一方法；不执行实现；严格所有权与权限检查；普通 Function 不是接续令牌'),
)

_HASH_SPECS = (
    NativeSpec('hash',(P('value'),),'Int',summary='原生键哈希或同步用户 __hash__；严格 Int，不隐式 await；同一键存放期间哈希与相等必须稳定'),
    NativeSpec('hash_map',(P('entries',optional=True,default_display='[]'),),'HashMap',summary='同步消费二元 List/Tuple 条目；返回持久有序通用键映射，最多10000输入项；不隐式 await'),
    NativeSpec('hash_get',(P('mapping','HashMap'),P('key'),P('default',optional=True,default_display='null')),'Any',summary='按实际哈希／相等协议查找；不存在返回 default；键协议可产生普通语言效果'),
    NativeSpec('hash_set',(P('mapping','HashMap'),P('key'),P('value')),'HashMap',summary='返回新映射；等价键更新值并保留原键和位置，新增键追加；旧映射不变'),
    NativeSpec('hash_remove',(P('mapping','HashMap'),P('key')),'HashMap',summary='返回删除键的新映射；不存在时保留内容，不原地修改'),
    NativeSpec('hash_has',(P('mapping','HashMap'),P('key')),'Bool',summary='按实际键哈希／相等协议检查存在性；不读取默认值'),
    NativeSpec('hash_items',(P('mapping','HashMap'),),'List',summary='按首次插入顺序返回二元 Tuple 列表；键保留身份，值保持能力投影，函数对象身份可能不同'),
    NativeSpec('hash_keys',(P('mapping','HashMap'),),'List',summary='按首次插入顺序提取实际键；不克隆实例键，不提升权限'),
    NativeSpec('hash_values',(P('mapping','HashMap'),),'List',summary='按键顺序提取实际语言值；保持当前能力投影'),
    NativeSpec('hash_info',(P('mapping','HashMap'),),'Record',result_shape='HashMapInfo',summary='纯查询 size/ordered/persistent/maxEntries/keyPolicy，不执行键哈希或相等协议'),
)

_CONTRACT_SPECS = (
    NativeSpec('contract_ref',(P('name','String'),),'Contract',summary='构造当前解释器所有的开放引用；精确名称须非空且最多128字符，不查找变量或类'),
    NativeSpec('contract_recursive',(P('definitions','Record'),P('root','String')),'Contract',summary='闭合1至128个递归契约定义；局部精确名字绑定，拒绝未绑定引用及不消费数据的循环'),
    NativeSpec('contract_list',(P('element'),),'Contract',summary='构造逐项检查 List 的纯组合契约；子契约为内建类型名、实际 Class、DataType 或 Contract'),
    NativeSpec('contract_tuple',(P('elements','List'),),'Contract',summary='构造固定长度、逐位置检查 Tuple 的契约；不转换 List 或消费迭代器'),
    NativeSpec('contract_record',(P('required','Record'),P('optional','Record | Null',optional=True,default_display='null'),P('allow_extra','Bool',optional=True,default_display='false')),'Contract',summary='构造字符串字段记录契约；必需／可省略字段互斥，额外字段默认拒绝；不执行访问协议'),
    NativeSpec('contract_union',(P('alternatives','List'),),'Contract',summary='构造1至128个子契约的联合；按声明顺序进行纯检查，共享检查预算'),
    NativeSpec('contract_optional',(P('contract'),),'Contract',summary='构造子契约与 Null 的联合；允许 null 与允许省略记录字段分别声明'),
    NativeSpec('contract_map',(P('key'),P('value')),'Contract',summary='构造原生 HashMap 的键／值契约；遍历现有条目，不调用用户哈希或相等'),
    NativeSpec('contract_accepts',(P('contract'),P('value')),'Bool',summary='按内建类型名、Class、DataType 或闭合 Contract 纯检查值；开放模板拒绝，不匹配返回 false，不吞掉所有权、控制或预算错误'),
    NativeSpec('contract_info',(P('contract'),),'Record',result_shape='ContractInfo',summary='返回带 kind 的递归诊断记录；可选字段取决于种类，不暴露实际类或可执行句柄'),
)

# Existing runtime globals: explicit metadata, never an executable signature fallback.
# Parameters retain their previous conservative tooling types; runtime validation
# and identity-sensitive inference remain authoritative.
_CORE_GLOBAL_SPECS = (
    NativeSpec('abs',(P('x'),),'Any'),
    NativeSpec('aclose',(P('handle'),),'Task',awaited_type='Null'),
    NativeSpec('acollect',(P('handle'),),'Task',awaited_type='List'),
    NativeSpec('agent_from',(P('definition'),),'Function',inference_rule='agent_factory',result_callable=NativeSpec('Agent',(P('input'),),'Any'),summary='纯定义操作，不调用提供方或授予能力'),
    NativeSpec('agent_info',(P('value'),),'Record',inference_rule='agent_definition_info',summary='纯定义操作，不调用提供方或授予能力'),
    NativeSpec('agent_source',(P('value'),P('name',optional=True,default_display='"saved_agent"'),),'String',summary='纯定义操作，不调用提供方或授予能力'),
    NativeSpec('aiter',(P('value'),),'Any',summary='AsyncIterator | AsyncGenerator；工厂返回句柄，不隐式 await'),
    NativeSpec('anext',(P('handle'),),'Task',awaited_type='Record',awaited_shape='IterationStep',summary='冷 Task，await 后为 {done: Bool, value: Any}'),
    NativeSpec('append',(P('xs'),P('x'),),'List'),
    NativeSpec('asend',(P('handle'),P('value'),),'Task',awaited_type='Record',awaited_shape='IterationStep',summary='冷 Task，await 后为 {done: Bool, value: Any}'),
    NativeSpec('athrow',(P('handle'),P('value'),),'Task',awaited_type='Record',awaited_shape='IterationStep',summary='冷 Task，await 后为 {done: Bool, value: Any}'),
    NativeSpec('bool',(P('value'),),'Bool'),
    NativeSpec('classmethod',(P('value'),),'Any'),
    NativeSpec('close',(P('handle'),),'Null'),
    NativeSpec('collect',(P('handle'),),'List'),
    NativeSpec('compose',(P('f'),P('g'),),'Function'),
    NativeSpec('contains',(P('container'),P('item'),),'Bool'),
    NativeSpec('current_program',(),'Program',summary='纯程序反射/校验，不运行候选或授予能力'),
    NativeSpec('delattr',(P('value'),P('name'),),'Null'),
    NativeSpec('delitem',(P('value'),P('key'),),'Null'),
    NativeSpec('dir',(P('value'),),'List'),
    NativeSpec('drop',(P('n'),P('xs'),),'List'),
    NativeSpec('flatten',(P('xs'),),'List'),
    NativeSpec('floordiv',(P('left'),P('right'),),'Any'),
    NativeSpec('get',(P('obj'),P('key'),P('default',optional=True,default_display='null'),),'Any'),
    NativeSpec('getattr',(P('value'),P('name'),),'Any'),
    NativeSpec('getitem',(P('value'),P('key'),),'Any'),
    NativeSpec('goal',(P('objective'),P('planner'),P('verify'),P('max_iterations',optional=True,default_display='8'),),'Goal'),
    NativeSpec('has',(P('obj'),P('key'),),'Bool'),
    NativeSpec('hasattr',(P('value'),P('name'),),'Bool'),
    NativeSpec('head',(P('xs'),),'Any'),
    NativeSpec('iter',(P('value'),),'Any',summary='Iterator | Generator；工厂返回句柄，不隐式 await'),
    NativeSpec('join',(P('sep'),P('xs'),),'String'),
    NativeSpec('json_stringify',(P('value'),),'String'),
    NativeSpec('json_copy',(P('value'),),'Any'),
    NativeSpec('keys',(P('obj'),),'List'),
    NativeSpec('length',(P('x'),),'Int'),
    NativeSpec('load_mode',(P('key'),P('revision',optional=True,default_display='null'),),'Task'),
    NativeSpec('lower',(P('s'),),'String'),
    NativeSpec('merge',(P('a'),P('b'),),'Record'),
    NativeSpec('mode_from',(P('definition'),),'Any'),
    NativeSpec('mode_info',(P('value'),),'Record'),
    NativeSpec('mode_metadata',(P('value'),),'Record'),
    NativeSpec('mode_source',(P('value'),P('name',optional=True,default_display='"saved_mode"'),),'String'),
    NativeSpec('next',(P('handle'),),'Record',result_shape='IterationStep'),
    NativeSpec('parse_json',(P('text'),),'Any'),
    NativeSpec('partial',(P('fn',mode='positional_only'),P('bound',mode='varargs'),P('keywords',mode='varkw'),),'Function'),
    NativeSpec('pow',(P('base'),P('exponent'),),'Any'),
    NativeSpec('program_adopt',(P('base'),P('candidate'),P('cases'),P('reason',optional=True,default_display='""'),),'Task',awaited_type='Record',summary='冷 Task；采用需 program.adopt 能力、纯测试和版本 CAS'),
    NativeSpec('program_edit',(P('value'),P('file'),P('operations'),),'Program',summary='纯程序反射/校验，不运行候选或授予能力'),
    NativeSpec('program_from',(P('definition'),),'Program',summary='纯程序反射/校验，不运行候选或授予能力'),
    NativeSpec('program_history',(P('after',optional=True,default_display='0'),P('limit',optional=True,default_display='50'),),'Task',awaited_type='Record',awaited_shape='ProgramHistoryInfo',summary='冷 Task；读取不可变历史，不改变当前运行、候选或采用权限'),
    NativeSpec('program_info',(P('value'),),'Record',result_shape='ProgramDefinitionInfo',summary='纯程序反射/校验，不运行候选或授予能力'),
    NativeSpec('program_ir',(P('value'),P('file',optional=True,default_display='null'),),'Record',result_shape='ProgramIRInfo',summary='纯程序反射/校验，不运行候选或授予能力'),
    NativeSpec('program_metadata',(P('value'),),'Record',result_shape='ProgramMetadataInfo',summary='纯程序反射/校验，不运行候选或授予能力'),
    NativeSpec('program_probe',(P('value'),P('cases'),),'Task',awaited_type='Record',summary='冷 Task；采用需 program.adopt 能力、纯测试和版本 CAS'),
    NativeSpec('program_revision',(P('version'),),'Task',awaited_type='Program',summary='冷 Task；读取不可变历史，不改变当前运行、候选或采用权限'),
    NativeSpec('program_source',(P('value'),P('name',optional=True,default_display='"saved_program"'),),'String',summary='纯程序反射/校验，不运行候选或授予能力'),
    NativeSpec('property',(P('fget',optional=True,default_display='null'),P('fset',optional=True,default_display='null'),P('fdel',optional=True,default_display='null'),),'Any'),
    NativeSpec('pursue',(P('value'),P('bindings'),P('input',optional=True,default_display='null'),P('key',optional=True,default_display='null'),),'Task',awaited_type='Record'),
    NativeSpec('range',(P('start'),P('stop',optional=True,default_display='null'),P('step',optional=True,default_display='1'),),'List'),
    NativeSpec('record_skill',(P('action'),P('input',optional=True,default_display='null'),P('parameters',optional=True,default_display='[]'),),'Task',awaited_type='Record'),
    NativeSpec('remove',(P('obj'),P('key'),),'Record'),
    NativeSpec('replace',(P('text'),P('old'),P('new'),),'Any'),
    NativeSpec('replay_skill',(P('skill'),P('values',optional=True,default_display='{}'),),'Task',awaited_type='List'),
    NativeSpec('reverse',(P('xs'),),'List'),
    NativeSpec('run_plan',(P('value'),P('bindings'),P('input',optional=True,default_display='null'),),'Task',awaited_type='Record'),
    NativeSpec('run_program',(P('value'),P('input',optional=True,default_display='null'),),'Task',summary='冷 Task；采用需 program.adopt 能力、纯测试和版本 CAS'),
    NativeSpec('save_mode',(P('value'),P('key'),P('revision',optional=True,default_display='null'),),'Task',awaited_type='Record'),
    NativeSpec('send',(P('handle'),P('value'),),'Record',result_shape='IterationStep'),
    NativeSpec('session_close',(P('key'),P('revision'),),'Task',awaited_type='Record',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('session_compact',(P('key'),P('revision'),P('keep_turns'),),'Task',awaited_type='Record',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('session_fork',(P('key'),P('revision'),P('new_key'),P('agent',optional=True,default_display='null'),),'Task',awaited_type='AgentSession',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('session_history',(P('key'),P('after',optional=True,default_display='0'),P('limit',optional=True,default_display='100'),),'Task',awaited_type='Record',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('session_load',(P('key'),),'Task',awaited_type='AgentSession',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('session_open',(P('key'),P('agent'),P('context_limit',optional=True,default_display='100000'),),'Task',awaited_type='AgentSession',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('session_reconcile',(P('key'),P('revision'),P('evidence'),),'Task',awaited_type='Record',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('session_status',(P('key'),),'Task',awaited_type='Record',summary='冷 Task；原生持久会话，共享权限与版本；不会恢复浏览器句柄'),
    NativeSpec('set',(P('obj'),P('key'),P('value'),),'Record'),
    NativeSpec('setattr',(P('value'),P('name'),P('item'),),'Any'),
    NativeSpec('setitem',(P('value'),P('key'),P('item'),),'Null'),
    NativeSpec('skill_source',(P('value'),P('name',optional=True,default_display='"recorded"'),),'String'),
    NativeSpec('slice',(P('value'),P('start'),P('stop',optional=True,default_display='null'),),'Any'),
    NativeSpec('split',(P('s'),P('sep'),),'List'),
    NativeSpec('staticmethod',(P('value'),),'Any'),
    NativeSpec('str',(P('x'),),'String'),
    NativeSpec('super',(P('klass'),P('instance'),),'Any'),
    NativeSpec('tail',(P('xs'),),'List'),
    NativeSpec('take',(P('n'),P('xs'),),'List'),
    NativeSpec('throw_into',(P('handle'),P('value'),),'Record',result_shape='IterationStep'),
    NativeSpec('tool',(P('name'),P('value'),),'Any'),
    NativeSpec('trim',(P('s'),),'String'),
    NativeSpec('type_of',(P('value'),),'String'),
    NativeSpec('upper',(P('s'),),'String'),
    NativeSpec('values',(P('obj'),),'List'),
    NativeSpec('vars',(P('value'),),'Record'),
    NativeSpec('workflow',(P('graph'),P('value'),),'Any'),
    NativeSpec('zip',(P('xs'),P('ys'),),'List'),
)

# Fixed language-value call contracts; these are not exported global natives.
# Runtime dispatch continues to decide which values are callable.
_CALLABLE_VALUE_SPECS = (
    NativeSpec('Bot.__call__',(P('input',optional=True,default_display='null'),),'Task',awaited_type='Record',awaited_shape='BotResult',inference_rule='receiver_call',receiver_type='Bot',compatibility_export=False,summary='冷 Task；原调度器执行目标／上下文和真实回调；默认根调用独占运行指导；嵌套或显式 scoped 使用独立收件箱；继承父决策约束，过期执行不自动重放'),
    NativeSpec('Robot.__call__',(P('command','RobotCommand'),),'Task',awaited_type='RobotReceipt',inference_rule='receiver_call',receiver_type='Robot',compatibility_export=False,summary='冷 Task；与 robot_act 同一原调用路径，消费当前观察上的一次性命令；sent 只证明传输，targetVerified=false'),
    NativeSpec('Continuation.__call__',(P('items',mode='varargs'),),'Any',inference_rule='receiver_call',receiver_type='Continuation',compatibility_export=False,summary='在当前调用者上组合运行保存的后缀，传入完整结果；每次调用使用新执行状态，共享词法变量；不重新执行捕获前缀'),
    NativeSpec('EscapeContinuation.__call__',(P('items',mode='varargs'),),'Any',inference_rule='receiver_call',receiver_type='EscapeContinuation',compatibility_export=False,summary='向原动态范围传递零／单／多个结果；仅当前逻辑 holder 内可访问的活动出口可调用；不捕获或重放已返回的计算'),
    NativeSpec('Device.__call__',(P('action','DeviceAction'),),'Task',awaited_type='DeviceReceipt',inference_rule='receiver_call',receiver_type='Device',compatibility_export=False,summary='冷 Task；await 后发送一次设备动作并返回收据'),
    NativeSpec('Computer.__call__',(P('action','ComputerAction'),),'Task',awaited_type='Observation',inference_rule='receiver_call',receiver_type='Computer',compatibility_export=False,summary='await 后执行一次动作并返回新观察'),
    NativeSpec('AgentSession.__call__',(P('input'),P('message_id',optional=True,default_display='null')),'Task',awaited_type='Record',inference_rule='receiver_call',receiver_type='AgentSession',compatibility_export=False,awaited_shape='SessionReceipt',summary='await 后发送；显式消息 ID 可幂等重试'),
    NativeSpec('Process.__call__',(P('key','String'),P('state',optional=True,default_display='null')),'Task',awaited_type='Record',inference_rule='receiver_call',receiver_type='Process',compatibility_export=False,awaited_shape='ProcessRecord'),
    NativeSpec('Team.__call__',(P('input'),),'Task',awaited_type='Any',inference_rule='receiver_call',receiver_type='Team',compatibility_export=False),
    NativeSpec('Program.__call__',(P('input'),),'Task',awaited_type='Any',inference_rule='receiver_call',receiver_type='Program',compatibility_export=False),
    NativeSpec('Plan.__call__',(P('input',optional=True,default_display='null'),P('bindings','Record',optional=True,default_display='{}')),'Task',awaited_type='Record',inference_rule='receiver_call',receiver_type='Plan',compatibility_export=False),
    NativeSpec('Goal.__call__',(P('input',optional=True,default_display='null'),P('bindings','Record',optional=True,default_display='{}')),'Task',awaited_type='Record',inference_rule='receiver_call',receiver_type='Goal',compatibility_export=False),
    NativeSpec('Skill.__call__',(P('values','Record',optional=True,default_display='{}'),),'Task',awaited_type='List',inference_rule='receiver_call',receiver_type='Skill',compatibility_export=False),
)

_MULTIPLE_VALUE_SPECS = (
    NativeSpec('return_values',(P('items',mode='varargs'),),'Any',inference_rule='multiple_values',summary='产生零个或多个返回结果；普通值位置只取首项，零结果为 null；不是 Tuple；全局 values(obj) 仍提取 Record 值'),
    NativeSpec('values_list',(P('items','List'),),'Any',inference_rule='multiple_values_list',summary='将有界 List 的各项作为返回结果；空 List 为零结果；普通值位置只取首项'),
    NativeSpec('multiple_value_list',(P('producer'),),'List',inference_rule='multiple_capture',summary='从零参 callable 或已完成 Task/Generator/AsyncGenerator 捕获全部返回结果为 List；只读句柄终值，不等待或重放；零结果 [] 与单个 null [null] 不同'),
    NativeSpec('nth_value',(P('index','Int'),P('producer')),'Any',inference_rule='multiple_nth',summary='从零参 callable 或已完成 Task/Generator/AsyncGenerator 读取指定返回结果；超出数量为 null；index 必须非负；不推进句柄'),
    NativeSpec('multiple_value_call',(P('function','Function'),P('producers',mode='varargs')),'Any',inference_rule='multiple_call',summary='从左到右读取 producers（零参 callable 或已完成 Task/Generator/AsyncGenerator），把全部结果作为 function 位置实参；保留目标全部返回结果'),
    NativeSpec('multiple_value_bind',(P('count','Int'),P('producer'),P('consumer','Function')),'Any',inference_rule='multiple_bind',summary='读取零参 callable 或已完成句柄 producer，取 count 项传给 consumer；不足补 null，多余舍弃；保留 consumer 全部返回结果'),
    NativeSpec('multiple_value_prog1',(P('first'),P('afters',mode='varargs')),'Any',inference_rule='multiple_prog1',summary='保存 first 全部返回结果，顺序读取 afters 后恢复；producer 为零参 callable 或已完成 Task/Generator/AsyncGenerator；显式 async 回调可等待，普通回调返回冷句柄不自动等待'),
)

_CONTROL_PROMPT_SPECS = (
    NativeSpec('prompt_tag',(P('name','String',optional=True,default_display='""'),),'PromptTag',result_shape='PromptTagFields',summary='创建本解释器所有的唯一提示标签；name 仅供显示，同名标签不匹配'),
    NativeSpec('call_prompt',(P('tag','PromptTag'),P('body','Function'),P('handler','Function | Null',optional=True,default_display='null')),'Any',summary='在原栈安装身份提示并进入 body；abort 后移除目标范围再调用 handler；省略 handler 保留全部中止结果'),
    NativeSpec('prompt_scope',(P('tag','PromptTag'),P('handler','Function | Null',optional=True,default_display='null')),'PromptScope',result_shape='PromptScopeFields',summary='单次原生 with 提示范围；as 绑定同一 PromptScope；流内范围可跨 yield，消费者不能进入流的控制状态'),
    NativeSpec('abort_prompt',(P('tag','PromptTag'),P('items',mode='varargs')),'Any',summary='按标签身份中止当前逻辑 holder 最近的活动提示；零／单／多结果完整传递；不存在或被向外屏障阻挡时明确拒绝'),
    NativeSpec('prompt_available',(P('tag','PromptTag'),),'Bool',summary='当前逻辑 holder 内存在可访问活动目标且不跨向外控制屏障时为 true；不创建、恢复或推进任何范围'),
    NativeSpec('call_escape',(P('body','Function'),),'Any',summary='在原动态范围向 body 传入真实 EscapeContinuation callable；出口闭合后不可调用；不提供保存后续计算的 call/cc'),
    NativeSpec('capture_continuation',(P('tag','PromptTag'),P('receiver','Function')),'Any',summary='捕获当前身份提示以内可复用的计算后缀，在原位置调用 receiver(k) 并继续；活动资源和未知原生控制边界明确拒绝'),
    NativeSpec('continuation_info',(P('continuation','Continuation'),),'Record',result_shape='ContinuationFields',summary='复制续延身份、可用状态、帧数和显式调用计数；不暴露活执行帧、词法环境或权限'),
    NativeSpec('escape_info',(P('continuation','EscapeContinuation'),),'Record',result_shape='EscapeContinuationFields',summary='仅复制 id/scopeId/state/active/available；不公开 Env、处理器、权限或控制载荷'),
    NativeSpec('control_barrier',(P('body','Function'),),'Any',summary='Chaos 向外控制转移屏障：body 内创建的目标仍可调用，跨屏障到外部目标被拒绝；不同于 Racket continuation barrier'),
    NativeSpec('dynamic_wind',(P('before','Function'),P('body','Function'),P('after','Function')),'Any',summary='预检全部回调，再在同一逻辑范围执行 before/body/after；保留 body 全部结果，before/after 结果完整验证后丢弃；硬控制沿原清理规则'),
)

_ROBOT_SPECS = (
    NativeSpec('robot_open',(P('name','String'),),'Task',awaited_type='Robot',summary='冷 Task；await 时打开可信宿主按名注册的 Robot；需要 robot.control，源码不能提供串口或驱动'),
    NativeSpec('robot_info',(P('robot','Robot'),),'Record',result_shape='RobotInfo',summary='纯复制本轮租约、声明通道及观察龄上限；不打开、观察或授予权限'),
    NativeSpec('robot_observe',(P('robot','Robot'),P('timeout_ms','Int',optional=True,default_display='1000')),'Task',awaited_type='RobotObservation',summary='冷 Task；读取一个完整遥测包，保留原 receivedAtNs／sequence 和带单位 readings；不刷新缓存或推测图像'),
    NativeSpec('robot_observation_info',(P('observation','RobotObservation'),),'Record',result_shape='RobotObservationInfo',summary='纯复制一个不可变观察包及原接收时间；JSON 不恢复活观察权限'),
    NativeSpec('robot_command',(P('observation','RobotObservation'),P('channels','Record'),P('ttl_ms','Int',optional=True,default_display='250')),'RobotCommand',summary='纯构造当前观察上的一次性命令；channels 精确匹配可信 profile 声明；TTL 以原观察时间计，不发送输入'),
    NativeSpec('robot_command_info',(P('command','RobotCommand'),),'Record',result_shape='RobotCommandInfo',summary='纯读取租约、观察、通道、TTL 和共享一次性消费状态'),
    NativeSpec('robot_act',(P('robot','Robot'),P('command','RobotCommand')),'Task',awaited_type='RobotReceipt',summary='冷 Task；await 时验证当前最新观察、绝对期限、权限并消费一次命令；不重试不确定运动'),
    NativeSpec('robot_stop',(P('robot','Robot'),),'Task',awaited_type='RobotReceipt',summary='冷 Task；关闭运动心跳并提交停止；stopSent 不证明车辆实际停止，targetVerified=false'),
    NativeSpec('robot_close',(P('robot','Robot'),),'Task',awaited_type='RobotReceipt',summary='冷 Task；成功实际退役后释放租约，失败保持不确定和资源隔离；async with 同一退出路径'),
    NativeSpec('robot_receipt_info',(P('receipt','RobotReceipt'),),'Record',result_shape='RobotReceiptInfo',summary='纯复制 sent／stopSent／closed 传输或资源回执；targetVerified=false，非固件关联 ACK 或运动成功证据'),
)

_BOT_SPECS = (
    NativeSpec('bot',(P('goal'),P('planner','Function'),P('execute','Function'),P('verify','Function'),P('context',optional=True,default_display='null'),P('max_iterations','Int | Null',optional=True,default_display='20'),P('mailbox','String',mode='keyword_only',optional=True,default_display='"auto"'),P('role','String | Null',mode='keyword_only',optional=True,default_display='null')),'Bot',summary='创建本解释器所有的共享 Bot 身份；真实规划／执行／验证回调，复制 JSON 目标和上下文；null 轮次由原预算／取消限制，不提供默认认知'),
    NativeSpec('bot_info',(P('bot','Bot'),),'Record',result_shape='BotInfo',summary='纯复制身份、状态、revision、iteration、忙状态和协调标记；无公开回调或权限'),
    NativeSpec('bot_context',(P('bot','Bot'),),'Record',result_shape='BotContext',summary='纯复制目标、上下文、输入、引导、已完成证据和历史；JSON 不恢复活 Bot'),
    NativeSpec('bot_reconcile',(P('bot','Bot'),P('context'),P('evidence')),'Bot',summary='仅空闲且 needs_reconciliation 的同一 Bot 可显式协调；替换复制上下文、追加协调证据，保留完成前缀；不调用效果或重放执行'),
)

_SPECS = _CORE_GLOBAL_SPECS + _CALLABLE_VALUE_SPECS + _CONTRACT_SPECS + _HASH_SPECS + _GENERIC_SPECS + _CONTEXT_SPECS + _STACK_SPECS + _NUMERIC_SPECS + _RECOVERY_SPECS + _MULTIPLE_VALUE_SPECS + _CONTROL_PROMPT_SPECS + _ROBOT_SPECS + _BOT_SPECS + (
    NativeSpec('ui_node',(P('tag','String'),P('args','List',optional=True,default_display='[]')),'Record',summary='共享组件目录的纯 JSON 节点；按属性顺序传参，不执行模型、脚本或回调'),
    NativeSpec('ui_artifact',(P('kind','String'),P('title','String'),P('root','Record')),'UIArtifact',summary='创建当前解释器所有的不可变页面、报告或幻灯片；严格验证共享组件树'),
    NativeSpec('ui_from_openui',(P('source','String'),P('kind','String',optional=True,default_display='"page"'),P('title','String',optional=True,default_display='""')),'UIArtifact',summary='完整 OpenUI 静态组件程序经正式解析成为原生不可变产物；预览片段不能提交'),
    NativeSpec('ui_info',(P('value','UIArtifact'),),'Record',result_shape='UIArtifactInfo',summary='只读种类、标题、内容与组件库哈希、节点数和动作名称'),
    NativeSpec('ui_definition',(P('value','UIArtifact'),),'Record',result_shape='UIArtifactDefinition',summary='完整规范定义的 JSON 快照；源码、组件树、库哈希和内容哈希一致'),
    NativeSpec('ui_source',(P('value','UIArtifact'),),'String',summary='规范 OpenUI 源码'),
    NativeSpec('ui_program',(P('value','UIArtifact'),),'String',summary='可在原语言运行的 ui_from_openui 普通 .chaos 源码'),
    NativeSpec('ui_export',(P('value','UIArtifact'),P('format','String',optional=True,default_display='"html"')),'String',summary='本地导出 html/json/openui/chaos；HTML 包含离线页面与演示控件'),
    NativeSpec('ui_prompt',(P('kind','String',optional=True,default_display='"page"'),),'String',summary='由共享组件目录生成 OpenUI 提示；不调用模型'),
    NativeSpec('ui_validate',(P('value','UIArtifact'),P('requirements','List',optional=True,default_display='[]')),'Record',result_shape='UIValidation',summary='显式要求的确定性校验及产物哈希；不把模型评价当作证明'),
    NativeSpec('ui_generate',(P('agent','Function'),P('request'),P('kind','String',mode='keyword_only',optional=True,default_display='"page"'),P('title','String',mode='keyword_only',optional=True,default_display='""'),P('previous',mode='keyword_only',optional=True,default_display='null')),'Task',awaited_type='UIArtifact',summary='冷 Task；用原生智能体生成并逐步发布 ui.delta 预览；仅完整验证结果提交不可变产物，保留所有权和取消'),
    NativeSpec('ui_dispatch',(P('value','UIArtifact'),P('event','Record'),P('handlers','Record')),'Task',awaited_type='Any',summary='冷 Task；已声明动作在当前解释器由原生函数处理；浏览器仅对同版本已保存源码显式发起新运行'),
    NativeSpec('default_getattr',(P('value'),P('name','String')),'Any',summary='跳过属性定制，沿原描述符／存储读取；保留所有权、能力及类型契约，不是任意原始字段访问'),
    NativeSpec('default_setattr',(P('value'),P('name','String'),P('item')),'Any',summary='跳过 __setattr__，沿原描述符／存储写入并返回 item；仍检查可写性、契约和能力'),
    NativeSpec('default_delattr',(P('value'),P('name','String')),'Null',summary='跳过 __delattr__，沿原描述符／存储删除；成功返回 null，保留既有保护'),
    NativeSpec('default_dir',(P('value'),),'List',summary='原有有界成员目录；不调用 __dir__、属性钩子或描述符，返回排序名称'),
    NativeSpec('class_member',(P('value'),P('mutable','Bool',mode='keyword_only',optional=True,default_display='false'),P('method','Bool',mode='keyword_only',optional=True,default_display='false'),P('contract',mode='keyword_only',optional=True,default_display='"Any"')),'ClassMember',result_shape='ClassMember',inference_rule='class_member',summary='当前解释器所有的不可变成员元信息；只读 value/mutable/method/contract，方法须原始 Function 且有首个位置接收者，mutable 与 method 不可同时为 true；不运行描述符'),
    NativeSpec('class_members',(P('klass','Class'),),'Record',inference_rule='class_members',summary='按声明顺序快照 Class 的自有成员为 ClassMember；不共享可变 Cell、不执行描述符，value 保留原所有权和能力上限'),
    NativeSpec('Type',(P('args',mode='varargs'),P('options',mode='varkw')),'Any',inference_rule='native_type',summary='原生默认元类；Type(value) 查询 Class/Instance 的实际类；Type(name,bases,namespace,**options) 直接创建类，不调用 prepare'),
    NativeSpec('Exception.__new__',(P('cls','Class'),P('args',mode='varargs'),P('options',mode='varkw')),'Instance',summary='原生 Exception 子类分配；原位置与具名参数继续交给实际同步初始化器',receiver_type='Exception',compatibility_export=False),
    NativeSpec('Exception.__init__',(P('self','Instance'),P('args',mode='varargs')),'Null',summary='初始化原生异常存储字段；支持 super 委托，不执行属性协议',receiver_type='Exception',compatibility_export=False),
    NativeSpec('Type.__prepare__',(P('mcls','Type'),P('name','String'),P('bases','List'),P('options',mode='varkw')),'Record',summary='默认空命名空间；类访问隐式绑定实际元类，源码与 new_class 高层创建调用一次',receiver_type='Type'),
    NativeSpec('Type.__new__',(P('mcls','Type'),P('name','String'),P('bases','List'),P('namespace'),P('options',mode='varkw')),'Any',summary='默认原生类分配；复制成员、C3、__set_name__ 与 __init_subclass__；无需 prepare，元类函数显式传 mcls',receiver_type='Type'),
    NativeSpec('Type.__init__',(P('cls','Class'),P('name','String'),P('bases','List'),P('namespace'),P('options',mode='varkw')),'Null',summary='默认元类同步初始化；创建返回真实 Class 时执行',receiver_type='Type'),
    NativeSpec('Type.__call__',(P('cls','Class'),P('args',mode='varargs'),P('options',mode='varkw')),'Any',summary='默认构造分派；经请求 Class 的 __new__/__init__，元类可重写类调用',receiver_type='Type'),
    NativeSpec('Type.__instancecheck__',(P('cls','Class'),P('value')),'Bool',summary='默认名义实例判断；不递归调用公开元类钩子',receiver_type='Type'),
    NativeSpec('Type.__subclasscheck__',(P('cls','Class'),P('child','Class')),'Bool',summary='默认名义继承判断；不执行用户类型判断钩子',receiver_type='Type'),
    NativeSpec('isinstance',(P('value'),P('klass','Class')),'Bool',summary='通过实际元类 __instancecheck__ 同步判断；严格返回 Bool'),
    NativeSpec('issubclass',(P('child','Class'),P('base','Class')),'Bool',summary='通过实际元类 __subclasscheck__ 同步判断；严格返回 Bool'),
    NativeSpec('class_of',(P('value'),),'Class',inference_rule='class_of',summary='Instance 的类或 Class 的元类；保持本解释器所有权，不执行用户钩子'),
    NativeSpec('new_class',(P('name','String'),P('bases','List',optional=True,default_display='[]'),P('members','Record',optional=True,default_display='{}'),P('options','Record',optional=True,default_display='{}')),'Any',inference_rule='new_class',summary='与 class 声明共享元类选择、prepare 命名空间及原生创建管线；metaclass 可为 Type 子类或显式原生可调用工厂，返回值按实际协议决定；成员元信息用 ClassMember'),
    NativeSpec('alloc_instance',(P('klass','Class'),),'Instance',inference_rule='alloc_instance',summary='仅分配当前解释器所有的已完成 Class 实例；不运行 __new__ 或 __init__，保持实例预算和能力上限'),
    NativeSpec('index',(P('value'),),'Int',summary='无损整数索引；Int 原样，Instance 经类 MRO 的同步 __index__，结果必须精确 Int；不转换 Bool/Number、不隐式 await、不调用 __int__'),
    NativeSpec('slice_key',(P('start',optional=True,default_display='null'),P('stop',optional=True,default_display='null'),P('step',optional=True,default_display='null')),'Slice',summary='所有参数表达式求值后，按 start/stop/step 顺序各转换一次整数索引协议，null 保留省略端点；返回仅含 Int/null 的不可变 Slice，step 不得为零'),
    NativeSpec('slice_indices',(P('spec','Slice'),P('length')),'List',summary='length 经同步无损整数索引协议并要求非负；返回规范化的 start/stop/step'),
    NativeSpec('gpu_map',(P('fn','Function'),P('values','List'),P('device','String',mode='keyword_only',optional=True,default_display='"auto"'),P('compiler','String',mode='keyword_only',optional=True,default_display='"tensor"')),'Task',awaited_type='Record',awaited_shape='GpuResult',summary='冷 Task；Number/Any 单参数纯函数按 Float32 返回数值；算术 IR1 保留，IR2 支持局部绑定/赋值、严格 Bool、惰性 if/布尔运算、while/break/continue/提前 return、数值字面量 List/Tuple for 和正常结束时的循环 else；IR2 最多 128 节点、深度 64、128 槽位含输入，每 lane 4096 次访问、每动态循环 1024 个 body、for 256 项；默认 tensor，metal 严格要求 MPS 隔离编译，auto 明确报告回退；返回原五字段实际设备结果'),
    NativeSpec('gpu_kernel_info',(P('task','Task'),),'Record',result_shape='GpuKernelInfo',summary='只读原 gpu_map Task 的分离编译证据；不启动、不等待、不标记已观察；实际编译和执行身份未确定时保持 Null；哈希不等同已执行，保留所有权和执行周期检查'),
    NativeSpec('gpu_matmul',(P('left','List'),P('right','List'),P('device','String',mode='keyword_only',optional=True,default_display='"auto"')),'Task',awaited_type='Record',awaited_shape='GpuResult',summary='冷 Task；有界二维矩阵 Float32 乘法；cpu 为显式 CPU，mps/cuda/gpu 要求真实可用 GPU；返回实际设备、后端和回退原因'),
    NativeSpec('gpu_devices',(),'Task',awaited_type='Record',awaited_shape='GpuDevices',summary='冷 Task；后台检测可用 GPU 和可选计算后端；读取真实设备列表与默认选择；代码分析不检测硬件'),
    NativeSpec('program_link',(P('definition','Record'),P('libraries','Record')),'Program',summary='把原四字段定义与挂载的 owned Program 合并为新闭合程序；保留源码和方言，只校验不初始化，不获得来源或权限'),
    NativeSpec('module_from',(P('program','Program'),P('bindings','Record',optional=True,default_display='{}')),'Record',summary='在原调度器初始化封闭Program模块；显式保留活值绑定，每次独立模块缓存并返回真实导出值'),
    NativeSpec('code_from',(P('source','Any'),),'Code',inference_rule='code_value',summary='以共享源码/程序IR构造不可变代码；纯宏展开，不运行默认值或函数体'),
    NativeSpec('code_source',(P('code','Code'),),'String',summary='读取代码规范源码；不执行'),
    NativeSpec('code_ir',(P('code','Code'),),'Record',summary='读取独立程序IR副本；不执行'),
    NativeSpec('code_edit',(P('code','Code'),P('operations','List')),'Code',summary='以共享结构补丁生成新代码；不修改旧代码或函数'),
    NativeSpec('code_info',(P('code','Code'),),'Record',result_shape='CodeInfo',summary='读取不可变代码身份/函数种类/参数与节点数量；不执行'),
    NativeSpec('function_from',(P('code','Code'),P('bindings','Record',optional=True,default_display='{}')),'Function',inference_rule='code_function',summary='显式捕获原语言值，在原AST调度器构建真实函数；定义期默认值按原规则执行一次，函数体保持冷状态'),
    NativeSpec('compile_function',(P('fn','Function'),P('target','String',optional=True,default_display='"auto"')),'Function',inference_rule='compiled_function',summary='返回保留参数、契约和作用域的函数副本；auto/native/interpreter 保持原子集；auto-control/native-control 编译可变循环内整数区域；auto-flow/native-flow 编译普通、async、gen 与 async gen 函数的可恢复结构控制流及直接保护范围后继，保留原错误/清理合同、冷 Task/流与等待、反馈、委托协议；受支持整数表达式可本机编译，其余按模式解释或报错；不执行函数体'),
    NativeSpec('compile_info',(P('fn','Function'),),'Record',result_shape='CompiledFunctionInfo',summary='读取函数实际编译后端、输入范围、执行计数和回退原因；不执行函数，不导出机器地址、源码或权限'),
    NativeSpec('compile_flow_info',(P('fn','Function'),),'Record',result_shape='CompiledFlowInfo',summary='读取实际可恢复控制流 ABI、分支与原生/便携续接计数；稳定定义模式；不执行、不导出状态值或机器地址'),
    NativeSpec('compile_optimization_info',(P('fn','Function'),),'Record',result_shape='CompiledOptimizationInfo',summary='读取已分配原生内核的真实整数 DAG/CSE/常量折叠/调度及完整检查点证据；不编译、不执行，不导出源码、地址或私有值'),
    NativeSpec('program_compile',(P('value','Program'),),'Record',result_shape='CompilationReport',summary='编译受控程序快照并报告依赖、结构效果和编译身份；不执行程序；共享预算，带检查点路径绕过解析缓存'),
    NativeSpec('program_references',(P('value','Program'),P('file','String'),P('position','Int'),P('include_declaration','Bool',optional=True,default_display='true')),'Record',result_shape='SymbolReferences',summary='原始 AST 的符号引用；UTF-16 位置；complete=false 必须检查 limitations；不执行程序'),
    NativeSpec('program_rename',(P('value','Program'),P('file','String'),P('position','Int'),P('new_name','String')),'Program',summary='同源语义重命名产生不可变候选，保留方言和来源；不完整或捕获风险明确拒绝，不自动采用'),
    NativeSpec('team_from',(P('definition','Record'),),'Team',inference_rule='team_from',summary='纯构造团队默认 Agent 定义；调用入口角色产生冷 Task'),
    NativeSpec('team_info',(P('value','Team'),),'Record',result_shape='TeamDefinitionInfo',summary='纯读取原始 Team 定义；拒绝活绑定，避免丢失实现'),
    NativeSpec('team_source',(P('value','Team'),P('name','String',optional=True,default_display='"saved_team"')),'String',summary='导出普通原始 Team 定义模块；不导出活实现'),
    NativeSpec('team_bind',(P('value','Team'),P('bindings','Record')),'Function',inference_rule='team_bind',summary='纯绑定本轮角色 callable；未绑定角色使用默认 Agent；转发真实参数，调用为冷 Task 并等待一层返回 Task'),
    NativeSpec('team_role',(P('value','Team | Function'),P('role','String')),'Function',inference_rule='team_role',summary='获取受控原始或活绑定团队角色；保留实际实现参数，调用产生冷 Task'),
    NativeSpec('team_bindings',(P('value','Team | Function'),),'Record',inference_rule='team_bindings',summary='取得角色到冷 callable 的新记录；活绑定不从 JSON 恢复'),
    NativeSpec('team_binding_info',(P('value','Function'),),'Record',result_shape='TeamBindingInfo',inference_rule='team_binding_info',summary='纯读取原定义、定义哈希及显式覆盖角色的真实实现种类；不导出闭包或伪造实现哈希'),
    NativeSpec('session_bind',(P('value','AgentSession'),P('tools','List')),'Function',inference_rule='bound_session',result_callable=NativeSpec('Bound Session',(P('input'),P('message_id','String | Null',optional=True,default_display='null')),'Task',awaited_type='Record',awaited_shape='SessionReceipt',summary='冷 Task；await 后发送并执行本轮绑定；JSON 不恢复闭包'),summary='纯绑定当前会话句柄与本轮语言工具；调用产生冷 Task；不改变普通会话调用，不恢复旧闭包'),
    NativeSpec('session_binding_info',(P('value','Function'),),'Record',result_shape='SessionBindingInfo',summary='纯读取会话身份与排序后的工具合同、bindingHash；不调用模型，不导出处理函数'),
    NativeSpec('session_info',(P('value','AgentSession'),),'Record',result_shape='SessionInfo',summary='纯读取句柄最后已知快照；lastToolBinding 为最近成功语言工具轮次，普通调用保留该历史标记；刷新需 await session_status(key)'),
    NativeSpec('process_evolve',(P('base','Program'),P('candidate','Program'),P('cases','List'),P('state'),P('output',optional=True,default_display='null'),P('done','Bool',optional=True,default_display='false'),P('reason','String',optional=True,default_display='""')),'ProcessEvolution',summary='仅当前 Process 轮次构造不可调用的演进值；不执行测试或采用；直接返回后，清理和测试成功才原子提交源码与检查点'),
    NativeSpec('process_evolution_info',(P('value','ProcessEvolution'),),'Record',result_shape='ProcessEvolutionInfo',summary='纯读取同一执行轮次的演进快照；描述不代表已测试或提交，不能恢复所有权'),
    NativeSpec('process_from',(P('definition','Record'),),'Process',summary='纯构造显式模块与导出绑定；调用产生启动进程的冷 Task'),
    NativeSpec('process_info',(P('value','Process'),),'Record',result_shape='ProcessDefinitionInfo',summary='纯读取 Process 的公开定义副本'),
    NativeSpec('process_source',(P('value','Process'),P('name','String',optional=True,default_display='"saved_process"')),'String',summary='导出普通 process_from 模块；不启动进程'),
    NativeSpec('process_step',(P('state'),P('output',optional=True,default_display='null'),P('done','Bool',optional=True,default_display='false')),'Record',result_shape='ProcessStepInfo',summary='纯构造原有 {state, output, done} 轮次结果；不采用源码'),
    NativeSpec('process_start',(P('key','String'),P('value','Process'),P('state',optional=True,default_display='null')),'Task',awaited_type='Record',awaited_shape='ProcessRecord',summary='冷 Task；启动持久 Process，需 process.manage'),
    NativeSpec('process_send',(P('key','String'),P('input'),P('message_id','String | Null',optional=True,default_display='null')),'Task',awaited_type='Record',awaited_shape='ProcessMessageReceipt',summary='冷 Task；向持久邮箱入队，不等待轮次完成'),
    NativeSpec('process_guide',(P('key','String'),P('input'),P('message_id','String | Null',optional=True,default_display='null'),P('target','Record | Null',mode='keyword_only',optional=True,default_display='null')),'Task',awaited_type='Record',awaited_shape='ProcessGuidanceReceipt',summary='冷 Task；持久追加 Process 指导，queued／received／applied 不代表动作完成；与普通消息共用身份，指导不会再次成为 ctx.input'),
    NativeSpec('process_message',(P('key','String'),P('message_id','String')),'Task',awaited_type='Record',awaited_shape='ProcessMessageOutcome',summary='冷 Task；按普通消息的精确 ID 读取可信关联运行；consumed 不代表成功，输出是该运行完整 ProcessStep，不调度或重放轮次'),
    NativeSpec('process_status',(P('key','String'),),'Task',awaited_type='Record',awaited_shape='ProcessRecord',summary='冷 Task；读取已提交的持久记录；lastVersion 是执行快照，lastEvolution 仅成功原子演进后存在'),
    NativeSpec('process_events',(P('key','String'),P('after','Int',optional=True,default_display='0'),P('limit','Int',optional=True,default_display='100')),'Task',awaited_type='Record',awaited_shape='ProcessEventPage',summary='冷 Task；按全局 Int eventId 读取当前项目 Process 的可信已提交事件页；after>=0，limit=1..300；不含无法证明归属的旧历史，不启动或重放轮次'),
    NativeSpec('process_control',(P('key','String'),P('revision','Int'),P('action','String')),'Task',awaited_type='Record',awaited_shape='ProcessRecord',summary='冷 Task；按当前 revision 暂停、恢复或停止 Process'),
    NativeSpec('process_configure',(P('key','String'),P('revision','Int'),P('definition','Record')),'Task',awaited_type='Record',awaited_shape='ProcessRecord',summary='冷 Task；配置未来轮次；定义变化使当前演进提案失效'),
    NativeSpec('process_reconcile',(P('key','String'),P('revision','Int'),P('state'),P('evidence','String',optional=True,default_display='""'),P('resume','Bool',optional=True,default_display='false')),'Task',awaited_type='Record',awaited_shape='ProcessRecord',summary='冷 Task；显式协调不确定效果与检查点，不重放轮次'),
    NativeSpec('agent_tool',(P('name','String'),P('handler','Function'),P('description','String',optional=True,default_display='""'),P('input_type','String',optional=True,default_display='"Any"'),P('output_type','String',optional=True,default_display='"Any"'),P('capabilities','List',optional=True,default_display='[]')),'AgentTool',summary='声明本轮语言工具合同并持有活闭包；描述符不可调用；类型合同仅支持八种 JSON 值类型'),
    NativeSpec('agent_bind',(P('value','Function'),P('tools','List')),'Function',summary='绑定 Agent 定义与本轮语言工具；不调用提供方；返回 Function，调用产生冷 Task',inference_rule='bound_agent',result_callable=NativeSpec('Bound Agent',(P('input'),),'Task',summary='冷 Task；await 后调用提供方与本轮语言工具；结果类型 Any')),
    NativeSpec('agent_tool_info',(P('value','AgentTool'),),'Record',result_shape='AgentToolInfo',summary='纯读取工具合同；不导出处理函数或恢复活闭包'),
    NativeSpec('agent_binding_info',(P('value','Function'),),'Record',result_shape='AgentBindingInfo',summary='纯读取 Agent 定义与工具合同；JSON 不能恢复绑定或权限'),
    NativeSpec('exit_stack',(P('asynchronous','Bool',optional=True,default_display='false'),P('max_entries','Int',optional=True,default_display='1024')),'ExitStack | AsyncExitStack',summary='创建本轮有界资源栈；登记不释放，退出按后进先出；动态 TaskGroup／CancelShield 不支持',inference_rule='exit_stack'),
    NativeSpec('generator_context',(P('generator','Generator | AsyncGenerator'),P('asynchronous','Bool',optional=True,default_display='false')),'ContextManager | AsyncContextManager',summary='单次生成器资源作用域；进入时运行至唯一 yield，退出时完成；不提前执行生成器',inference_rule='generator_context'),
    NativeSpec('plan',(P('steps','List'),P('name','String',optional=True,default_display='"plan"'),P('max_concurrency','Int',optional=True,default_display='1')),'Plan',summary='纯计划定义；max_concurrency 为 1–128，默认 1 按原顺序串行；并发步骤只在依赖全部完成后启动；调用返回冷 Task'),
    NativeSpec('device_open',(P('name','String'),),'Task',awaited_type='Device',summary='冷 Task；打开宿主注册的设备，独占物理资源；不接受源码提供的串口路径'),
    NativeSpec('device_info',(P('device','Device'),),'Record',result_shape='DeviceInfo',summary='纯读取本轮设备租约元数据'),
    NativeSpec('device_action',(P('device','Device'),P('spec','Record')),'DeviceAction',summary='纯构造绑定设备租约的一次性 HID 动作；不发送输入'),
    NativeSpec('device_action_info',(P('action','DeviceAction'),),'Record',result_shape='DeviceActionInfo',summary='读取设备身份、规范动作与消费状态'),
    NativeSpec('device_act',(P('device','Device'),P('action','DeviceAction')),'Task',awaited_type='DeviceReceipt',summary='冷 Task；原子消费动作后发送；不确定结果不重试，HID 跟随目标机全局焦点'),
    NativeSpec('device_receipt_info',(P('receipt','DeviceReceipt'),),'Record',result_shape='DeviceReceiptInfo',summary='读取宿主提交回执（串口桥或 Linux 队列）；targetVerified=false，不证明目标应用成功'),
    NativeSpec('device_close',(P('device','Device'),),'Task',awaited_type='DeviceReceipt',summary='冷 Task；关闭租约并释放设备；async with 使用同一清理路径'),
    NativeSpec('computer_attach',(P('name','String'),),'Task',awaited_type='Computer',summary='冷 Task；按名独占宿主注册的电脑或移动设备，需要 computer.control；不接受源码中的 endpoint 或 key'),
    NativeSpec('computer_open',(P('url','String | Null',optional=True,default_display='null'),),'Task',awaited_type='Computer',summary='冷 Task；新建本轮临时浏览器；async with 退出时关闭'),
    NativeSpec('computer_connect',(P('key','String'),),'Task',awaited_type='Computer',summary='冷 Task；独占连接项目内持久浏览器会话；每次返回本轮新租约，async with 退出时释放'),
    NativeSpec('computer_info',(P('value','Computer'),),'Record',result_shape='ComputerInfo',summary='纯读取最后成功快照；sessionKey、stableSessionId、generation、requiresReconcile、continued 仅持久连接具有；continued 表明此次连接复用了活浏览器'),
    NativeSpec('computer_observe',(P('value','Computer'),P('image','Bool',optional=True,default_display='true')),'Task',awaited_type='Observation',summary='冷 Task；获取当前观察并刷新持久连接状态；不清除恢复要求'),
    NativeSpec('observation_info',(P('value','Observation'),),'Record',result_shape='ObservationInfo',summary='纯读取不可变观察；持久连接附带会话身份与恢复状态，JSON 不恢复浏览器引用'),
    NativeSpec('computer_action',(P('observation','Observation'),P('spec','Record')),'ComputerAction',summary='纯构造绑定观察的一次性动作；不执行浏览器效果'),
    NativeSpec('action_info',(P('value','ComputerAction'),),'Record',result_shape='ComputerActionInfo',summary='纯读取动作、观察身份和消费状态'),
    NativeSpec('computer_act',(P('value','Computer'),P('action','ComputerAction')),'Task',awaited_type='Observation',summary='冷 Task；执行当前租约最新观察上的一次性动作'),
    NativeSpec('computer_close',(P('value','Computer'),),'Task',awaited_type='Record',awaited_shape='ComputerInfo',summary='冷 Task；关闭浏览器；持久会话保留身份与 storage，下次连接创建新 driver'),
    NativeSpec('computer_release',(P('value','Computer'),),'Task',awaited_type='Record',awaited_shape='ComputerInfo',summary='冷 Task；关闭本轮租约并保留持久浏览器；临时 Computer 等同 computer_close'),
    NativeSpec('computer_reconcile',(P('value','Computer'),P('observation','Observation'),P('evidence','String')),'Task',awaited_type='Record',awaited_shape='ComputerInfo',summary='冷 Task；以当前租约最新观察与 1–1000 字符非空证据确认恢复；不重放动作'),
    NativeSpec('observation_input',(P('observation','Observation'),P('input','Any',optional=True,default_display='null')),'ObservedInput',summary='纯受控图像输入；历史 JSON 不能恢复权限'),
    NativeSpec('set_of',(P('items',optional=True,default_display='[]'),),'Set',summary='按同步迭代协议逐项去重；最多 10000 输入项；原生键或同步用户哈希／相等协议，不隐式 await'),
    NativeSpec('set_add',(P('value','Set'),P('item')),'Set',summary='返回包含 item 的新集合，保留首次插入顺序；不修改原集合'),
    NativeSpec('set_discard',(P('value','Set'),P('item')),'Set',summary='返回移除 item 的新集合；不存在时保留原成员'),
    NativeSpec('set_is_disjoint',(P('left','Set'),P('right','Set')),'Bool',summary='集合是否没有共同成员；用户对象键沿共享同步哈希／相等协议'),
    NativeSpec('tuple',(P('items',optional=True,default_display='[]'),),'Tuple',summary='按同步迭代协议构造浅不可变 Tuple，最多 10000 项；不隐式 await'),
    NativeSpec('apply',(P('fn'),P('args'),P('kwargs','Record | Null',optional=True,default_display='null')),'Any',summary='args 按同步可迭代协议展开；kwargs 为 Record；返回目标调用结果，不隐式 await',inference_rule='apply_result'),
    NativeSpec('map',(P('fn'),P('xs')),'List',summary='消费同步可迭代源 xs 并生成 List；异步回调产生冷 Task，不隐式 await'),
    NativeSpec('filter',(P('fn'),P('xs')),'List',summary='消费同步可迭代源 xs；回调必须返回 Bool；生成 List'),
    NativeSpec('fold',(P('fn'),P('initial'),P('items')),'Any',summary='按同步可迭代源 items 的顺序累计；空源返回 initial'),
    NativeSpec('any',(P('f'),P('xs')),'Bool',summary='消费同步可迭代源 xs；Bool 回调首次为 true 即停止，空源为 false'),
    NativeSpec('all',(P('f'),P('xs')),'Bool',summary='消费同步可迭代源 xs；Bool 回调首次为 false 即停止，空源为 true'),
    NativeSpec('spawn',(P('task','Task'),),'Task',summary='启动并返回同一 Task，保留任务完成类型',inference_rule='same_task'),
    NativeSpec('gather',(P('tasks','List'),),'Task',awaited_type='List',summary='等待成员并完整收束；按输入顺序返回结果'),
    NativeSpec('cancel',(P('task','Task'),),'Bool',summary='请求取消尚未完成的任务'),
    NativeSpec('task_info',(P('task','Task'),),'Record',result_shape='TaskInfo',summary='有界只读快照，不启动、等待或标记已观察'),
    NativeSpec('cancel_shield',(),'CancelShield',summary='当前逻辑 Task 的单次词法取消屏蔽；with/async with 进入；不延长预算，不屏蔽运行级控制'),
    NativeSpec('cancel_shield_info',(P('shield','CancelShield'),),'Record',result_shape='CancelShieldInfo',summary='有界只读状态；不进入保护范围，不恢复活句柄'),
    NativeSpec('error',(P('value'),P('cause','Error | Null',mode='keyword_only',optional=True,default_display='<implicit>')),'Error',summary='构造原生错误；cause 可省略或显式为 Error/null；首次传播才捕获活动上下文'),
    NativeSpec('current_error',(),'Any',summary='当前捕获错误的 Error 值；无活动处理上下文返回 null'),
    NativeSpec('error_group',(P('message','String'),P('errors','List'),P('cause','Error | Null',mode='keyword_only',optional=True,default_display='<implicit>')),'Error',summary='构造保留嵌套顺序的非空 Error 组；显式 cause 独立于逐叶匹配'),
    NativeSpec('task_group',(P('errors','String',mode='keyword_only',optional=True,default_display='"first"'),),'TaskGroup',summary='async with 任务作用域；errors=group 收集普通失败，默认 first 保持首错'),
    NativeSpec('TaskGroup.spawn',(P('task','Task'),),'Task',summary='纳管并立即启动，保留任务完成类型',inference_rule='same_task',receiver_type='TaskGroup'),
    NativeSpec('task_group_info',(P('group','TaskGroup'),),'Record',result_shape='TaskGroupInfo',summary='有界只读状态，不进入或等待任务组'),
    NativeSpec('channel',(P('capacity','Int',optional=True,default_display='0'),),'Channel',summary='创建本轮有界通道；容量 0 表示收发会合'),
    NativeSpec('channel_send',(P('channel','Channel'),P('value')),'Task',awaited_type='Null',summary='冷 Task；await 后发送完成，结果为 Null'),
    NativeSpec('channel_receive',(P('channel','Channel'),),'Task',awaited_type='Record',awaited_shape='IterationStep',summary='冷 Task；await 后为 {done: Bool, value: Any}'),
    NativeSpec('guidance_receive',(P('timeout','Number | Null',optional=True,default_display='0'),),'Task',awaited_type='Record',awaited_shape='GuidanceResult',summary='冷 Task；读取当前运行的 FIFO 引导；秒超时，null 等待至取消／关闭；读取不代表应用'),
    NativeSpec('progress_emit',(P('kind','String'),P('payload','Any')),'Task',awaited_type='Record',awaited_shape='ProgressReceipt',summary='冷 Task；发布有界公开 plan／progress／result，不发布隐藏推理'),
    NativeSpec('channel_close',(P('channel','Channel'),),'Bool',summary='显式关闭共享通道；消费者退出不会关闭通道'),
    NativeSpec('channel_info',(P('channel','Channel'),),'Record',result_shape='ChannelInfo',summary='有界只读状态；不启动收发，不读取消息内容'),
    NativeSpec('sleep',(P('seconds','Number'),),'Task',awaited_type='Null',summary='冷 Task；启动后按单调时钟等待，不占宿主工作线程'),
    NativeSpec('task_wait',(P('tasks','List'),P('until','String',optional=True,default_display='"first"'),P('timeout','Number | Null',optional=True,default_display='null')),'Task',awaited_type='Record',awaited_shape='WaitResult',summary='冷 Task；等待去重成员状态，返回 done/pending/timedOut；不取消成员，也不是原子 select'),
    NativeSpec('send_case',(P('channel','Channel'),P('value')),'ChannelCase',summary='不可变可复用发送候选；创建不提交消息'),
    NativeSpec('receive_case',(P('channel','Channel'),),'ChannelCase',summary='不可变可复用接收候选；创建不消费消息'),
    NativeSpec('case_info',(P('case','ChannelCase'),),'Record',result_shape='ChannelCaseInfo',summary='只读候选身份、操作和通道；不展示发送值'),
    NativeSpec('channel_select',(P('cases','List'),P('timeout','Number | Null',optional=True,default_display='null')),'Task',awaited_type='Record',awaited_shape='SelectResult',summary='冷 Task；原子提交一个候选，保留原始索引；超时不提交候选'),
)
# Preserve the historical callable enumeration contract. New bound members
# are explicit receiver declarations, available in the complete registry and
# per-type catalog. All catalogs remain immutable and execution-free.
def _validated_catalog(specs):
    catalog={}
    for spec in specs:
        if spec.id in catalog:raise ValueError('duplicate native declaration ID')
        if spec.receiver_type is not None and (type(spec.receiver_type) is not str or not spec.receiver_type or not spec.id.startswith(spec.receiver_type+'.')):raise ValueError('invalid native receiver declaration')
        if type(spec.compatibility_export) is not bool:raise ValueError('invalid native compatibility export')
        catalog[spec.id]=spec
    return MappingProxyType(catalog)


ALL_SPECS = _validated_catalog(_SPECS)
SPECS = _validated_catalog(spec for spec in _SPECS if spec.compatibility_export)


def _member_catalog():
    receivers={}
    for spec in _SPECS:
        if spec.receiver_type is not None and spec.inference_rule != 'receiver_call':
            receiver=receivers.setdefault(spec.receiver_type,{})
            # The short member name is declared by the ID; receiver_type is
            # the ownership classification, never a runtime spelling heuristic.
            member=spec.id.rsplit('.',1)[-1]
            if member in receiver:raise ValueError('duplicate native receiver member')
            receiver[member]=spec
    return MappingProxyType({owner:MappingProxyType(members) for owner,members in receivers.items()})


MEMBER_SPECS = _member_catalog()
_EMPTY_MEMBERS = MappingProxyType({})


def spec_for(name):
    return ALL_SPECS.get(name)


def member_specs_for(receiver_type):
    return MEMBER_SPECS.get(receiver_type,_EMPTY_MEMBERS)


def all_specs():
    """Complete immutable global/legacy/receiver metadata; no code executes."""
    return ALL_SPECS



# Calling a native value does not expose a public __call__ attribute. Keep
# this projection separate from the historical member enumeration, while
# both views refer to the exact same immutable declarations in ALL_SPECS.
CALL_SPECS = MappingProxyType({spec.receiver_type: spec for spec in _SPECS
                              if spec.inference_rule == 'receiver_call'})


def call_spec_for(receiver_type):
    return CALL_SPECS.get(receiver_type)
