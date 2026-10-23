# 原生 Bot（可调用值）

Bot 是当前解释器所有的原生可调用值，保存长期目标、动态上下文和真实 planner／execute／verify 回调。`bot(...)` 不选择默认模型、解释自然语言关键词或自动授予工具；显式回调可组合普通函数、Agent、绑定 AgentTool、Team、Code 与 Program。调用返回原调度器的冷 Task，只有 await 后才进入控制器。完整运行源码见[两步验证示例](../examples/native-bot.chaos)。

| 接口 | 合同 |
| --- | --- |
| `bot(goal,planner,execute,verify,context=null,max_iterations=20,*,mailbox="auto",role=null)` | 返回 Bot；goal/context 是有界复制 JSON，三个回调保留活身份；max_iterations 为 1..10000 的 Int 或 null；mailbox 只能是 auto／scoped，role 为显式 Process 角色键或 null；null 轮次仍受原运行预算及取消限制 |
| `bot_info(bot)` | 复制 id、status、busy、revision、iteration、maxIterations、needsReconciliation |
| `bot_context(bot)` | 复制 goal、context、input、guidance、evidence、history、revision、iteration |
| `bot_reconcile(bot,context,evidence)` | 只协调空闲且 needs_reconciliation 的同一 Bot；替换复制上下文、追加协调证据／历史并保留完成前缀；返回原 Bot，不调用外部效果 |
| `bot(input=null)` | 返回冷 Task；await 得到 `{done,status,state,output}`，state 与 BotContext 同合同 |

Bot 没有公开成员方法；查询通过上述纯函数。`std/bot` 的四个 Bot 构造／查询导出是同一 Native 的别名；模块还导出 process_guide／process_events／process_message，接入同一 NativeSpec、类型注解、IDE／LSP、Code／Program 与普通 call／await IR。JSON 只携带诊断快照，不能恢复回调、epoch 或权限。

```chaos example=native-bot-aliases
import "std/bot" as B;
[B.bot==bot,B.bot_info==bot_info,B.bot_context==bot_context,B.bot_reconcile==bot_reconcile]
```

```chaos example=native-bot-cold
var calls=0;
fn plan(state){calls=calls+1;state.input}
fn execute(proposal,state){proposal}
fn verify(state){{done:true,context:state.context,output:state.evidence}}
let worker=bot("goal",plan,execute,verify);
let cold=worker();
[type_of(worker),type_of(cold),calls,bot_info(worker).busy,bot_info(worker).maxIterations]
```

planner 接收一个状态快照，返回 proposal JSON；execute 接收 `(proposal,state)`，返回 result JSON；verify 接收 state，必须返回精确 `{done:Bool,context:Any,output:Any}`。可用 async fn 包装模型或其他冷 Task；普通 tool 和直接 Agent 调用自身挂起并返回效果结果。三个回调沿原调用协议执行，权限按实际调用链相交，不能从 goal、引导文本或快照恢复权威。

状态在多轮中保留 goal 和完成 evidence，verify 的 context 是后续动态上下文。input 是本次调用的数据，guidance 保留实际消息身份与 revision；终态结果的 status 为 achieved、exhausted 或 needs_reconciliation。规划阶段尚无外部动作前缀的普通失败会传播；执行／验证阶段的普通错误则返回 needs_reconciliation，并保留原完成证据与决策历史，不能自动重试。尚未提交的 output 可能仍为 null；调用方须检查 status、done、证据及实际效果回执，不能只读取 output 判断成功。硬预算、取消及非局部控制继续沿原传播与清理合同处理。

```chaos example=native-bot-context
let worker=bot({objective:"retain evidence"},fn(s){s.input},fn(p,s){p},fn(s){{done:true,context:s.context,output:s.evidence}},context={stage:"draft"});
let state=bot_context(worker);
[state.goal,state.context,state.input,state.guidance,state.evidence,state.history,state.revision,state.iteration]
```

## 语言执行状态

Bot 的调用、规划、执行、验证、指导吸收、证据保存、提交和拥有流的清理由同一求值游标上的类型化帧驱动。阶段、提案、实际决策 token、待决结果／错误和退役状态是执行所用的真实字段；回调继续复用普通 Invocation 和原 TaskGroup，不建立另一套机器人执行器。Frame 不是 JSON 序列化接口，也不改变既有 Bot 参数、事件、权限或 Process 事务。完整示例及清理边界见[默认执行帧](EXECUTION_FRAMES.md)。

原 Task／流等待协议仍作为明确的叶操作存在。活动 Bot／Task 栈不能保存或复制到 continuation；支持的同步解释后缀已可[捕获和重复组合](CONTINUATIONS.md)。跨重启活 Bot 栈恢复、默认自动模型规划和自动语义记忆压缩不在当前范围；长期目标继续使用本页的显式回调和 Process 合同。

以下两轮示例须通过拥有指导宿主的 Framework／SDK／CLI 或工作台项目运行；纯 Evaluator 不伪造准入：

```chaos
fn plan(s){s.context.next}
fn execute(p,s){p*2}
fn verify(s){
 let next=s.context.next+1;
 {done:next>2,context:{next:next},output:s.evidence}
}
let worker=bot({objective:"two checks"},plan,execute,verify,context={next:1});
let pending=worker();let before=bot_info(worker).busy;
let actual=await pending;
[before,actual.status,actual.output,actual.state.context]
```

结果为 `[false,"achieved",[2,4],{"next":3}]`：调用返回的冷 Task 尚未开始，两轮通过同一 Bot 共享上下文和 evidence。

## 引导与效果准入

默认 `mailbox="auto"` 的根 Bot 继续独占当前 run 的 FIFO 指导队列；普通 guidance_receive 及第二个默认根 Bot 明确拒绝争抢。嵌套调用自动建立独立作用域收件箱；独立并发角色可在构造时显式使用 `mailbox="scoped"`，并通过实际 scopeId 定向指导。不广播、不重定向或悄悄偷取其他 Bot 的消息。现有 RunHandle.guide／Framework.guide／Client.guide、HTTP 和工作台输入继续发送到同一运行。queued 表示宿主接受，received 表示取出；`bot.context.applied` 是控制器实际吸收消息的宿主事件，不能把公开 progress 数据冒充这个状态。

Bot 的封闭决策身份随原 Task、子调用、Team、Program 和绑定 AgentTool 传播。宿主在真实效果边界用同一锁比较已接受指导 revision 与决策 revision，再记录准入。只有执行主体尚未进入且规划阶段没有外部动作前缀时，过期提案才被舍弃并重新规划。规划器也可能通过工具、Program 或其他回调开始外部动作；任何 tool:* 调用都保守计入这个前缀，即使可信描述写 effect="pure" 也不因此自动重放。已有规划动作或已进入执行主体后遇到过期决策，保留已完成／不确定前缀并停止为 needs_reconciliation。已开始的模型请求、工具或物理操作不会被撤销，completed 与 uncertain 都必须按实际回执检查。可信资源退役、取消和预算检查继续有效。

`bot.scope.opened`／`bot.scope.closed`、`bot.action.admitted`／`bot.action.completed` 与上下文应用事件带真实 scopeId／decisionId／actionId／revision；公开事件不包含隐藏模型推理。现有事件控制台按这些宿主身份显示进展；普通返回值始终使用通用结果视图。原 0.107 的 guided_bot 函数与任意自定义接收循环保留合作式读取合同，不自动获得原生 Bot 的准入身份。

已有外部动作前缀或已进入执行主体的工作不自动重放。协调必须由拥有原 Bot 的程序在检查已完成／不确定结果后显式调用 bot_reconcile；它不证明外部操作成功，也不回放中断主体。活身份只在原解释器／epoch 内使用，别名共享 busy 与协调状态。跨运行持久目标仍须用 Process／Session 的既有存储与协调合同；max_iterations=null 不代表永久运行或服务重启后的连续栈。

```chaos example=native-bot-generated
macro build(make){quote{unquote(make)("goal",fn(s){s.input},fn(p,s){p},fn(s){{done:true,context:s.context,output:s.evidence}},context={stage:"draft"})}}
let worker=build!(bot);
[type_of(worker),bot_context(worker).context.stage]
```

上面的构造／查询／冷性示例不调用模型或外部设备。await Bot 必须位于拥有可信指导宿主的活动运行；直接无宿主求值器不模拟宿主准入。完整源程序可在工作台既有源码／结构／图与 API／SDK 的 start 入口运行。


终态提交也在宿主同一锁下校验指导 revision，并封闭当次 Bot scope；直到 scope 关闭，新的指导明确拒绝。已经接受的相同 messageId 与相同内容仍返回原幂等回执，不再入队。非终态上下文提交不封闭 scope，之后接受的新指导仍可令旧决策过期。这个短暂封闭只保护当次终态提交，不把整个运行、持久目标或已发生的效果变成可撤销事务。


history 是已复制的决策快照，不是完整持久事件日志。硬取消可能在结果复制前中断；已准入动作的实际状态应核对宿主事件与外部回执，再由原程序显式协调。这个查询快照不声称包含每一个已发生效果的完整、持久结果。

## 0.109：通过 Process 保留状态与指导

跨轮仍显式创建新的 Bot；没有 bot_attach 或透明回调恢复。Process 导出函数从已提交 JSON 取出目标／上下文，构造真实 planner、execute、verify，并把本轮真实结果交给 `process_step(actual.state,actual.output,actual.done)`。旧 evidence／history 是 Process 保存的快照，程序必须明确选择如何用于新的上下文；JSON 不恢复旧 Bot 身份或权限。见[持久示例](../examples/durable-bot/bot.chaos)。

```chaos example=process-guide-alias
import "std/bot" as B;
[B.process_guide==process_guide,type_of(B.process_guide("worker",{text:"检查"}))]
```

```chaos example=durable-bot-cold
fn plan(state){state.input};fn execute(proposal,state){proposal};fn verify(state){{done:true,context:state.context,output:state.evidence}};
let fresh=bot("retained goal",plan,execute,verify,context={count:0},max_iterations=1);
[type_of(fresh),type_of(fresh(null)),bot_context(fresh).context.count]
```

Process 指导先持久入队，再由本轮运行显式接收。原生 Bot 分别接收／采用；普通 guidance_receive 在实际合作式接收时完成采用；准入使用当前持久 guidance revision 和原 Process 租约。已有规划动作或执行主体过期仍要求协调，不自动重放。宿主受保护检查点与 blocked 标记不能由普通 JSON 或纯 bot_reconcile 改写；必须核实实际动作后使用原 Process 协调。Session 提交和外层 Process 检查点仍是不同效果边界，稳定会话 messageId 可读取原回执，不能因此重跑已经发生的动作。

持久快照不恢复闭包、Task、资源或执行栈。

## 0.110：跨轮公开记录

`process_events(key,after=0,limit=100)` 是只读的冷 Task。等待后返回当前项目 Process 的可信事件页；`items` 中的 `id` 是原始全局整数，`lastEventId` 可作为下次 `after`。`processOrigin` 由宿主关系表生成：`lifecycle` 表示真实 Process 事务，`run` 表示该 Process 实际准入运行的公开事件。回调填写同名字段或事件名称不会获得生命周期权限。

```chaos example=process-events-cold
let pending=process_events("worker",after=0,limit=100);
[type_of(pending),task_info(pending).state]
```

```chaos example=process-events-alias
import "std/bot" as B;
[B.process_events==process_events,type_of(B.process_events("worker"))]
```

页额外包含 `historyStartEventId` 和 `legacyHistoryIncluded=false`。0.110 之前未记录可信归属的旧事件不通过名称或输入猜测补入；旧记录本身仍保留。公开计划、进展、动作准入和结果是执行证据，记录不提供模型的隐藏推理。SDK、HTTP、Web 和终端读取同一事件源。等待、暂停、停止和待核对状态仍可观察；单次 run 完成不结束长期 Process 观察，只有已完成 Process 且 pending 清空后排空当时已提交尾部。


## 原生 Bot 的函数组合与定向指导

`bot(goal, planner, execute, verify, context=null, max_iterations=20, *, mailbox="auto", role=null)` 保留原参数与默认行为；新增的 mailbox 只能按关键字传入。`auto` 在根位置使用原运行收件箱，在真实父 Bot 决策内调用时使用私有收件箱。`scoped` 总是使用私有收件箱，因此 `await gather([a(), b()])` 可执行两个显式 scoped 角色。同一个 Bot 的别名仍共享 busy 状态，不能重入。

```chaos
fn plan(s) { s.input }
fn execute(p, s) { p }
fn verify(s) { {done: true, context: s.context, output: s.evidence[-1]} }
let child = bot("计算", plan, execute, verify);
async fn delegate(p, s) { (await child(p)).output }
let parent = bot("委派", plan, delegate, verify);
(await parent(42)).output
```

此例使用真实子 Bot 完成 42，不调用模型。用户函数、TaskGroup、AgentTool 以及经受控 Program 产生的子解释器保留真实父决策的祖先凭据；子解释器只继承宿主封装的凭据数据，不转移父解释器的函数、活决策对象或权限。每次外部动作在原运行锁内逐层校验活动身份、revision、票据及权限交集。父指导使尚未准入的后代动作失效；子指导只改变该子收件箱。已经发生或不确定的动作不会因重规划而自动重放；不确定结果不能被父回调捕获后洗成成功。

`Framework/Client/RunHandle.guidance_scopes` 读取宿主当前活动作用域，包含真实父子身份、收件箱类型和是否可定向。它不靠有限的事件历史重建列表。`guide(..., target={"kind":"botScope","scopeId":真实ID})` 只接受当前运行中的活动私有作用域；显式指向原运行收件箱的根 ID 会拒绝，请沿用省略 target 的旧入口。终止、关闭、未知或其他运行的 ID 不会回退到根。HTTP 不接受显式 target:null。

省略 target 的回执保持原四个字段；定向回执增加 target，received 消息也保留该目标。messageId 幂等在每个收件箱内生效，相同 ID 可以用于不同角色；同一收件箱的相同 ID 与不同输入冲突。整个运行最多保留 128 个作用域、同时活动 32 个、嵌套深度 16、2000 个决策、4096 个实际动作、256 个指导身份和 64 条尚未采用（排队或已取出）的消息，按整个运行合计。关闭作用域保留未采用的收件箱证据直到运行退出；不把它转给另一个 Bot，queued 仍不代表 applied。

工作台的指导目标选择器、`chaos run-guide --bot-scope SCOPE_ID`、HTTP 原指导路径的可选 target，以及 SDK 使用同一接口。GET `/api/v1/runs/{runId}/guidance` 返回真实活动列表。公开进度中的作用域身份由宿主标记，用户 progress 数据中的同名字段不构成权威身份；不导出隐藏模型推理。

Process 仍只有一个受保护的根检查点。嵌套子 Bot 的动作与生命周期记入同一回合账目，子提交不能覆盖根检查点，子动作不确定会阻止根完成。原 guide_process 和省略 target 的运行指导继续持久发送给根。声明角色后可以使用下述持久角色邮箱；Process 仍拒绝临时 botScope 目标。重启不恢复子作用域、Task、决策票据或调用栈，也不自动重放动作。


## 长期 Process 的角色邮箱

Process 定义可选 `roles: ["researcher", "reviewer"]`；省略时保留原六字段定义及其哈希。角色名是程序显式选择的稳定身份，不能根据目标文本、显示名或源码位置推断。嵌套的 `bot(..., role="reviewer")` 在真实 Process 回合租约下绑定已声明角色；普通运行和 Process 根 Bot 不可冒充这个绑定，同一角色不能同时被两个活动调用占用。

`Framework/Client.process_guidance(project_id, key)` 和 GET `/api/v1/projects/{projectId}/processes/{key}/guidance` 返回持久注册表，包括 roleKey、roleId、revision、pendingCount、实际 activeScopeId 和 target。暂停、等待或暂未调用的角色仍可接收指导。把返回的完整 `target={kind:"processRole",roleId:...}` 交给 `guide_process(..., target=target)`、当前 Process Run 的 `guide(...,target=target)` 或原生 `process_guide(...,target=target)`；语言程序也可从 `process_status(key).botRoles` 读取目标。角色地址不授予权限，每次动作仍验证当前调用、全部祖先和 Process 的权限上限。

每次调用产生新的随机 scopeId；roleId 跨回合、服务重启及保留相同角色名的源码升级保持稳定。角色变更只能在没有活动回合时配置；删除仍有未提交消息的角色会拒绝。删除后再添加同名角色产生新的 roleId，旧地址不会转发给新角色。注册表最多 16 个活动角色、累计 128 个角色身份；角色消息累计保留 4096 个幂等身份，根与所有角色合计最多 64 条未提交指导。

角色邮箱独立排序、修订和幂等；不同角色可使用相同 messageId，同一角色相同 ID 与不同输入会冲突。定向回执含 key/runId/messageId/revision/status/target，重试保留首次入队的 runId（可能为空或属于以前回合）。原生 Bot 收到的是标记当前交付运行的消息视图；它不会改写数据库里的原回执。received 表示已取出，applied 表示已采用；最终只有根 ProcessStep 成功提交或用户明确协调后才消费已采用消息。子 Bot 提交不能覆盖根检查点。

新指导只唤醒原调度器一次；本轮没有调用对应角色时，未采用消息保留且不会导致空转。计时、普通消息、新指导或显式恢复可以再次提供这些消息。完成整个 Process 前若仍有未采用角色消息会拒绝完成，不能默默丢弃。重启中的回合、失败子作用域和不确定外部动作仍进入 needs_reconciliation；根指导使正在等待的子角色失效后，现有保守的失败锁存也可能要求明确协调。角色自己在尚未准入外部动作时可局部重规划，不把指导转给兄弟或根。

网页的角色选择器支持离线邮箱并分别保留草稿、重试身份和回执。终端 `process guide`、`process attach --role-key reviewer` 从真实注册表解析目标；一次 attach 的重试固定原 roleId，不因角色删除重建而改投新身份。公开事件携带宿主角色身份和原持久回执；不展示模型隐藏推理。
