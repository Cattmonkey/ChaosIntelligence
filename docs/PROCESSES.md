# Process：持续运行的语言函数

0.23 将持久后台运行放进语言运行时：Process 定义明确指定一个导出函数；Engine 在消息到来或定时唤醒时，以最新已保存程序创建一轮执行。关闭浏览器不影响运行。Framework、Client、CLI 与 IDE 使用相同 Store、运行记录和调度器，不把提示词任务重新包装为语言进程。

## 一等定义与冷调用

```chaos example=process-definition
let p=process_from({kind:"process",name:"counter",module:"counter.chaos",export:"step",intervalSeconds:null,maxTurns:100});
[type_of(p),process_info(p).module,type_of(p("counter",{count:0}))]
```

结果 `["Process","counter.chaos","Task"]`。Process 满足 Function 契约，支持普通高阶函数、partial、装饰器和 Plan 组合。调用只是创建冷 Task；实际启动须显式 await，并需要项目宿主及 `process.manage` 权限。定义只有上述六个字段，不包含项目身份、工具授权、提供方密钥或活闭包。

```chaos example=process-definition-copy
let p=process_from({kind:"process",name:"original",module:"dot.chaos",export:"step",intervalSeconds:30,maxTurns:null});
let q=process_from(merge(process_info(p),{name:"derived",intervalSeconds:60}));
[process_info(p).intervalSeconds,process_info(q).intervalSeconds]
```

`process_info` 返回隔离副本；`process_source` 导出普通 `process_from` 模块。JSON 描述不会自动复活成可执行 Process。`module` 和 `export` 明确绑定模块与导出名称，运行时不搜索名为 step 的函数，也不根据角色名称或提示词关键词决定行为。

`intervalSeconds:null` 表示首轮后只响应邮箱；数值范围 1–86400 秒。首轮始终自动安排。定时到期只合并为一轮，不补跑停机期间的全部 tick；FIFO 消息优先。`maxTurns` 为正整数，最多1000000；显式 null 表示持续到函数返回 done、用户停止或执行遇到问题。持续运行仍受每轮计算、工具、时间和并发预算约束。

## 状态转换函数

一个普通 `counter.chaos` 模块可以这样写：

```chaos
fn step(ctx) {
  let delta=if ctx.input==null {1} else {get(ctx.input,"delta",1)};
  let n=ctx.state.count+delta;
  process_step({count:n},{answer:n},n>=3)
}
export {step};
```

函数收到 `{state,input,turn,process:{key,kind,messageId},programVersion}`；kind 是显式的 start、message 或 timer，自动唤醒的 input 为 null。函数可以使用 Agent、Team、Plan、Goal、工具、宏和 async/await。普通函数或 async 函数均可导出；运行时等待其返回的一层 Task，未消费的流和其它活对象不能成为持久状态。

```chaos example=process-step-result
let result=process_step({count:2},{answer:42},false);
[result.state.count,result.output.answer,result.done]
```

普通返回值必须严格为 `{state,output,done}`，done 必须是 Bool。0.45 也支持直接返回受控的 `ProcessEvolution`，在清理和测试成功后将新程序与本轮检查点一起提交，详见[原子自我演进](PROCESS_EVOLUTION.md)。`process_step` 是这一协议的纯构造函数，也可直接构造同样记录；它不判断自然语言目标是否完成。需要目标验收时，在 step 中使用 Goal 或自己编写验证函数。

只初始化选定模块及其依赖，然后在同一解释器周期内调用导出值、等待效果并完成清理；不会执行无关启动入口。整个 ProgramBundle 仍保留项目原入口，所以 `current_program()` 的源码与受控来源继续准确。模块顶层代码也属于本轮执行；建议把模块顶层用于声明，把动作写在导出函数里。

## 启动、消息和控制

在已授权的项目程序中：

```chaos
let p=process_from({kind:"process",name:"counter",module:"counter.chaos",export:"step",intervalSeconds:null,maxTurns:100});
let started=await process_start("counter",p,{count:0});
await process_send("counter",{delta:1},message_id="request-001");
await process_status("counter");
```

`p(key,state)` 与 `process_start(key,p,state)` 相同。启动返回持久记录；`process_send` 返回入队回执，不等待该消息执行。相同 messageId 与相同数据重复提交幂等；相同 ID 携带不同数据返回冲突。消息以入队顺序处理。

语言控制接口：

- `process_control(key,revision,"pause")`：停止后续调度，当前轮正常结束后暂停。
- `process_control(key,revision,"resume")`：继续已暂停或空闲时停止的进程。
- `process_control(key,revision,"stop")`：停止后续调度，并取消当前轮。
- `process_configure(key,revision,definition)`：更改后续轮使用的定义；当前函数、状态和执行快照保持原状。
- `process_reconcile(key,revision,state,evidence="",resume=false)`：明确处理未确定完成的轮次，选择保留的状态并说明处理依据。

控制、配置和协调均要求最新 Process revision；过期返回409。新的定义不扩大启动时捕获的授权。completed 不能普通 resume；needs_reconciliation 必须先协调。读取状态后状态可能继续变化，调用者需要处理冲突并重新读取，不能无条件重试覆盖。

## 持久化、恢复与效果边界

Process 状态与邮箱使用项目数据库中的两张表。领取消息、记录 pending、创建真实 run 快照在同一事务完成；成功后的状态、邮箱确认、运行终态与完成事件也在同一事务提交。每个实际轮次都有普通 runId、源码版本及哈希，等待状态不占用运行槽。

工具外部效果和立即调用 `program_adopt` 已提交的源码不属于最终状态事务。若希望源码采用与检查点一起成功或失败，应直接返回 `process_evolve(...)`；该路径也不包含外部世界效果。执行失败、取消、清理失败或进程中断可能意味着效果已经发生，所以保存旧检查点及 pending 身份，进入 needs_reconciliation；系统不会自动重放这一轮。协调会确认该待处理消息，不自动重新入队；需要重试时，显式提交新消息。evidence 是至多1000字符的说明，不是“已通过”字样触发器。

主机重启后，空闲等待进程恢复消息和定时调度；不恢复 Python/Chaos 调用栈、闭包或 Task。Computer 具名会话只恢复已保存 cookies/localStorage，不恢复页面 DOM或旧租约；见[电脑会话](COMPUTERS.md)。新版本需要迁移状态格式时，迁移逻辑由新的导出函数根据显式 state 编写。调用 Framework.close 会停止派发、取消并排空活动轮次；持续运行需要主机保持开启。

每个项目最多32个 Process，运行时最多256个持久记录。每个 Process 最多256条待处理消息（queued/claimed），达到上限明确拒绝。已消费消息清除 input 正文，保留 messageId、输入哈希和状态作为轻量幂等回执；历史回执不占待处理配额、不自动过期，与事件记录一样占用持久存储。旧 ID 的同数据请求继续返回已消费，不重复执行；不同数据仍冲突。state、input、output 各自最多40000 JSON字符，并有深度/节点限制；超限失败，不截断。Process 管理效果不能录制成 Skill，会在派发前拒绝并使录制无法封存。

## 多角色 dot 持续给自己编程

发行包包含 [启动程序](../examples/persistent-dot/main.chaos)、[多角色 step](../examples/persistent-dot/dot.chaos)、[行为模块](../examples/persistent-dot/behavior.chaos)、[测试模块](../examples/persistent-dot/tests.chaos)。启动程序只创建 Process；之后每轮由 Engine 自动调用 step。

程序里的 Team 分别提出候选和审查意见；随后执行实际纯测试，再调用 `program_adopt`。当前 step 的函数仍来自原源码，下一轮重新加载新源码，取得新的受控 origin，可以再次采用新版本。示例最后用第三轮观察第二次采用后的行为。

回归使用受控 Registry Provider，验证两次采用和三轮状态衔接；这不是外部模型的优化成功率评测。审查意见不能替代实际测试，测试也不能证明任意输入正确。显式 state 可携带受限对话数据，但不等于恢复模型提供方的原生 assistant/tool 消息会话。

## 无界面 SDK 与 CLI

持续服务可由 `chaos serve --port 8765` 启动；关闭网页后仍继续运行。通过 Client 提交：

```python
from chaos import Client
client=Client("http://127.0.0.1:8765")
project=client.create_project("counter",source="null",files={
    "counter.chaos":"fn step(ctx){process_step({count:ctx.state.count+1},ctx.programVersion,false)};export{step};"
},capabilities=["process.manage"])
definition={"kind":"process","name":"counter","module":"counter.chaos","export":"step",
            "intervalSeconds":None,"maxTurns":100}
process=client.create_process(project["id"],project["version"],"counter",definition,state={"count":0})
receipt=client.send_process(project["id"],"counter",{"delta":1},message_id="request-001")
```

嵌入式 Framework 提供完全相同的方法，进程随 Framework 生命周期运行。不要在 `with Framework(...)` 退出后仍期待后台线程继续；长期服务应持有 Framework 或连接已启动的服务。现有 `chaos run` 是一次运行命令，不替代长期宿主。

```shell
chaos process list counter --server http://127.0.0.1:8765
chaos process get counter counter --server http://127.0.0.1:8765
chaos process send counter counter --input-json '{"delta":1}' --message-id request-002 --server http://127.0.0.1:8765
```

`create` 需要 `--version`、`--definition-file`，可给 `--state-json`。`pause/resume/stop/configure/reconcile` 需要 `--revision`；configure 使用定义文件，reconcile 使用状态 JSON、evidence 和可选 `--resume-after-reconcile`。CLI 只是同一 API 的客户端，不负责计时或在本地复制运行状态。

## GUI、权限与公开接口

IDE 的持续进程面板编辑同一定义、显示检查点、当前/最近实际运行与下次唤醒，提供消息、暂停、恢复、停止和协调。创建使用已保存项目；本地未保存源码和面板草稿保持独立。界面显示真实 waiting/paused/running 等状态，不把空闲动画当作模型正在思考。

`process.manage` 是显式、默认关闭的项目权限，不能当作普通工具或 Agent 工具请求。语言操作仅限当前项目；启动能力上限来自调用方并受 Team 范围限制，每轮再与当前项目授权求交。新增权限不自动扩权，撤销影响下一轮；较窄执行上下文不能借发消息、恢复或配置来控制当前有效权限更宽的进程；已撤销权限不妨碍保留 process.manage 的调用方管理进程。禁止 Agent 的函数节点上下文也不能通过 Process 调度绕过这一限制，状态读取除外。

Framework／Client 方法为 `validate_process`、`process_schema`、`create_process`、`processes`、`get_process`、`send_process`、`control_process`、`configure_process`、`reconcile_process`。管理操作走同一 Engine，运行与 Process 事件沿用项目 SSE；原 run/program/mode 接口不变。

HTTP 提供 `/api/v1/processes/schema`、`/api/v1/processes/validate`、`/api/v1/projects/{id}/processes` 及单项 `/messages`、`/control`、`/configure`、`/reconcile` 子路径，完整字段见 [HTTP 合同](HTTP_API.md)。

## 0.24 持续模型上下文

Process 的显式 state 可保存会话 key，在每轮通过[AgentSession](SESSIONS.md)恢复原生公开消息上下文。会话提交独立于 Process 检查点，失败协调需检查稳定 messageId 回执；成功会话不因后续 step 错误回滚。


## 0.25：每轮重新观察

持续机器人可以在 step 内打开 Computer、获得 Observation、调用持续角色并行动。轮次之间保存 JSON 状态/会话 key/观察记录 ID，不能保存活 Computer。已采用源码在下一轮生效，新租约与新观察重新建立。使用 computer_open 每轮新建浏览器；使用 computer_connect(key) 可继续同一服务保留的具名页面。见[Computer](COMPUTERS.md)。

## 从历史实现恢复持续函数

Process可用 `await program_revision(version)` 获取旧 Program，测试后通过 `program_adopt(current_program(),historical,cases)` 创建新版本。当前轮函数不替换；下一轮按已采用源码继续，JSON状态与邮箱不回滚。发生采用后检查点失败时仍需根据回执协调。随包[恢复 dot](../examples/recovering-dot/dot.chaos)演示跨宿主重启的三轮验证，详见[程序历史](PROGRAM_HISTORY.md)。

## 一轮内部的消息流

Process 的持久邮箱与语言 Channel 分工不同：邮箱跨轮次保存 JSON 消息及领取／确认事务，Channel 在当前轮次解释器内连接函数与智能体任务，支持背压和异步消费。轮次结束时通道句柄失效，不可将描述存入状态后恢复为活通道。示例与协议见[原生消息通信](CHANNELS.md)。

## 一轮内部的等待

[原生计时与多源等待](WAITS.md) 用于当前轮内协调 Agent／Team／Channel 任务，不占效果工作线程；它们依然受本轮时间预算约束。跨轮或重启的定时唤醒继续使用 Process 的持久调度，不能用 sleep 保存暂停栈。

ChannelCase 和 channel_select 的模板、注册与计时只存在于当前执行周期。持久 Process 下一轮须重新构造候选；保存的描述不能还原上一轮收发。跨轮仍使用持久邮箱、定时唤醒和效果协调，见[原子选择](SELECT.md)。

## 0.109：持久指导与每轮原生 Bot

`process_guide(key,input,message_id=null)` 返回冷 Task，显式 await 才向当前项目 Process 的持久指导邮箱提交。`process_send` 仍是下一轮普通 input；指导有独立用途，不会再次成为 `ctx.input`。两者共用项目／key／messageId 身份：相同用途与原始有界 JSON 幂等，不同数据或用途冲突。消息 ID 接受 `[A-Za-z0-9_.-]{1,128}`，未提供时宿主生成可重用的带前缀 ID；Process key 仍使用原标识符限制。

```chaos example=process-guide-cold
[type_of(process_guide("worker",{text:"请先检查结果"},message_id="1-guide")),type_of(process_send("worker",null,message_id="1-input"))]
```

指导回执精确为 `{key,runId,messageId,revision,status}`；runId 可为 null，并保留原始投递身份。queued 表示有界、已脱敏的 payload 已持久入队；received 表示原运行已取出；applied 表示运行已采用。原生 Bot 的 receive／adopt 分两阶段；普通 guidance_receive 在 Process 内保留原合作式接收语义，在实际接收时完成采用，均不证明外部动作完成。恢复运行向原生接收协议映射当前 runId，不改写历史回执。只有采用的指导随真实 Process 检查点成功提交或排空后的显式 CAS 协调确认；queued／未采用输入保留，重启不会复制成普通消息。原始规范 JSON 哈希保留在正文清除之后，重复请求不会重放。

每轮仍由原模块／export 在新解释器周期中重新构造回调和 Bot，持久数据不会恢复闭包、Task、资源或执行栈。[完整示例](../examples/durable-bot/bot.chaos)创建新的 Bot，使用已保存的 `state.goal`／`state.context`，并返回 `process_step(actual.state,actual.output,actual.done)`；[启动模块](../examples/durable-bot/main.chaos)沿用普通 Process。Bot 的每次调用 iteration 是当前 Bot 的迭代，Process turn 是持久轮次，不能把两者当作同一个计数器。

```chaos example=process-bot-step-shape
let actual={state:{goal:"retained",context:{count:1}},output:{count:1},done:false};
let checkpoint=process_step(actual.state,actual.output,actual.done);
[checkpoint.state.goal,checkpoint.output.count,checkpoint.done]
```

这个纯数据示例只说明返回合同。真实轮次的宿主保存原生 Bot 提交的检查点，并检查最终 ProcessStep 与其脱敏副本一致；伪造或忽略失败 Bot 的 JSON 不能清除受保护的 blocked 状态，纯 `bot_reconcile` 也不能协调持久轮次。失败、取消、清理或持久写入不确定时保留 pending／动作证据，由原 Process revision CAS 协调，不自动重放。模块初始化及非 Bot 动作仍服从原 Process pending／恢复合同，不声称所有代码都自动获得 Bot 准入身份。

HTTP `POST /api/v1/projects/{projectId}/processes/{key}/guidance` 使用 `{input,messageId?}`，返回202。Framework／Client `guide_process(project_id,key,value,message_id=...)` 和 CLI `chaos process guide PROJECT KEY --input-json '{"text":"新的指导"}' --message-id 1-guide` 使用同一服务。既有活动 Process run 的 `RunHandle.guide` 也进入这一持久身份；其它普通 run 的合作式指导合同不变。

现有持续进程输入旁的“追加持久指导”显示真实回执；pending.botWork 展示 phase、blocked、scope、actions、checkpoint、checkpointRevision、scopes 和 guidanceRevision 的公开副本，没有租约或准入 tickets。“查看实际运行”继续使用现有事件／结果控制台。公开计划与结果只来自显式 progress 或宿主事件，不输出隐藏推理。

## 连续观察与随时引导

语言使用 `await process_events("counter",after=cursor,limit=100)` 读取有界公开记录。全局整数事件游标与 Process revision、guidance revision、列表的 key 游标相互独立。该只读效果不会启动轮次或唤醒调度器；录制 scope 继续禁止 Process 效果。

SDK `watch_process(project_id,key,after=0,timeout=60)` 跨运行轮次等待，直到 Process 已完成且已提交尾部排空，或本地超时／分离。暂停、停止、等待消息及待核对仍保留观察连接；停止不等于不可恢复的完成。超时和关闭观察不会调用 stop、cancel 或 reconcile。

```sh
chaos process watch project worker --server http://127.0.0.1:8891 --after 0
chaos process guide project worker --server http://127.0.0.1:8891 --text '请核对新的目标约束' --message-id guide-001
chaos process attach project worker --server http://127.0.0.1:8891
```

`watch` 逐条刷新 JSON 行，支持重定向和管道；`attach` 在 POSIX 终端同时显示公开事件并编辑指导文本。`/detach`、Ctrl-D 和 Ctrl-C 仅分离本地观察；`/retry` 重用上次未确认指导的同一消息身份。暂停、恢复、停止和协调继续使用带 revision 的独立命令。Web 的持续进程面板读取相同的记录并接受普通指导文本，JSON 消息入口保留。

可信归属在原 Store 的 claim／Process 写事务中记录。升级仅增加两张归属表及索引，不改变已有表定义或用户行。缺少可信归属的旧事件仍可通过原项目／运行历史读取；Process 记录页明确说明这一边界，不伪造完整历史。

## 按消息 ID 读取结果

`await process_message(key,message_id)` 是原 Process 效果系统中的冷读取 Task；`message_id` 必须是明确的 1–128 字符普通消息身份，不接受 null，也不会生成新身份。`std/process` 导出全部 process_* 原生别名，`std/bot` 同时导出 process_message。导入和创建 Task 都不启动或重放工作。

结果精确包含 `{key,messageId,mailboxStatus,linkStatus,run,reconciliation}`。mailboxStatus 为 queued／claimed／consumed；consumed 只说明该消息被确认，不能推出成功。linkStatus 为 unclaimed（尚无领取）、linked（有宿主可信关联）或 unavailable（旧历史缺少充分证据，尚未建立可信关联）。普通指导与角色指导不是这个查询的命名空间；不存在的普通消息返回404；已经保存关联证书后，关联运行、领取事件或私有来源缺失或冲突时返回409，不能静默降为 unavailable。

run 为 null 或该消息原始运行的 `{id,version,status,output,error,created,updated}`。output 保留该次运行完整 ProcessStep 的 `{state,output,done}`，不取当前 Process 的上轮输出。后续消息、幂等重送及后续协调不会把旧运行改成新结果。reconciliation 为 null 或可信宿主协调事件 `{eventId,runId,evidence,at}`；旧事件没有保存说明时 evidence 为 null，不能从最新 Process 状态猜测。失败运行经协调后仍保持其原失败状态。

Python 的 `Framework.get_process_message(project_id,key,message_id)` 与 `Client.get_process_message(...)`、HTTP `GET /api/v1/projects/{projectId}/processes/{key}/messages/{messageId}` 和 CLI `chaos process message PROJECT KEY --message-id ID` 返回同一记录。HTTP 查询不接受额外 query，读取不触发调度或重试；GUI“持续进程”中的“按消息 ID 查看结果”也使用此读取接口，并保留项目、进程及消息身份的过期响应保护。
