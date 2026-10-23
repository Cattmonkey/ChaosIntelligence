# AgentSession：语言里的持久智能体会话

0.24 为持续智能体增加真实对话上下文。AgentSession 是当前解释器拥有的可调用值，满足 Function 契约；它通过项目宿主关联持久会话。下一次调用会把已提交的用户、助手、工具调用和工具结果重新编码为原生模型消息，不把历史拼接成一段提示词。当前实现使用已配置的 native 模型后端；普通 Agent 的 local、Registry 与 Codex 后台功能保持原接口，但不是持久会话后端。

## 打开并调用

```chaos example=session-cold-open
let assistant=agent_from({kind:"agent",name:"assistant",config:{provider:"model",tools:[]}});
type_of(session_open("assistant",assistant))
```

结果为 `"Task"`。构造 Agent 或创建冷 Task 都不会请求模型、访问配置或写入数据库；await 才执行宿主效果。项目须明确授予 `session.manage`，Agent 请求的工具也须已获授权。

```chaos
let definition=agent_from({kind:"agent",name:"assistant",config:{
  provider:"model",route:"assistant",role:"planner",tools:[],
  prompt:"Use the actual observations in this conversation to answer the next request."
}});
let assistant=await session_open("assistant",definition);
let first=await assistant({question:"检查这些假设",facts:input},message_id="request-001");
let second=await assistant({question:"根据上轮结论继续分析"},message_id="request-002");
[first.output,second.output,session_info(assistant).revision]
```

调用返回 `{key,messageId,status:"completed",output,revision,replayed}`，output 是模型返回的字符串。返回文本保持文本，不通过固定关键词、Markdown 或 JSON 外观猜测工具动作。需要程序定义等结构化结果时，由用户语言代码显式 `parse_json` 并调用相应验证器。

`session_open(key,agent,context_limit=100000)` 创建新会话，或加载绑定及 contextLimit 完全相同的会话。相同 key 不会悄悄改用新的 Agent 提示词或工具定义；不同绑定返回冲突。`session_load(key)` 重新取得当前会话的受控句柄。`session_info(value)` 只复制句柄最后已知的元数据，不隐式读数据库；需要刷新时显式 load/status。

```chaos example=session-cold-composition
let worker=agent_from({kind:"agent",name:"worker",config:{provider:"model",tools:[]}});
let pending=map(fn(key){session_open(key,worker)},["planning","execution"]);
map(type_of,pending)
```

结果 `["Task","Task"]`。AgentSession 可以作为参数、组件字段或 Plan 的可调用绑定；普通函数、宏、Task 和 Process 继续决定何时调用、等待和验收。

## 身份、版本与消息幂等

会话只属于当前项目。句柄带有所属解释器与最后已知 revision，不能通过 JSON 伪造或跨解释器直接使用；持久程序应保存 key，在新一轮显式 load。每个句柄的重叠调用会被拒绝；不同句柄对同一会话也通过数据库租约和版本比较保持单飞。顺序调用成功后句柄更新到最新 revision。

message_id 省略时由宿主生成。持续进程建议自己提供稳定 ID：同一 ID、同一输入与绑定返回原提交结果，不再次请求模型或执行工具；即使调用者持有旧 revision，已提交回执仍可幂等返回。相同 ID 携带不同输入冲突。仍在执行、不确定或已明确放弃的 ID 不会自动重新执行。

模型、base URL、key、角色路由和降级候选仍在本系统 Settings 中，按每次调用解析，支持既有配置热生效。会话固定公开 Agent 定义，模型版本可以按配置更新；切换模型不恢复提供方隐藏推理状态。backend 切为 Codex 时明确拒绝会话调用，不绕过原项目工具边界。

## 原生工具消息与权限

历史使用真实工具名、原生 callId、严格 JSON arguments/result 保存公开协议消息。传输时生成稳定的模型工具别名；撤销一项工具后，旧历史中的别名不会指向另一项工具。每个新工具请求仍需当前工具声明与能力检查。

创建时只捕获声明工具和 session.manage 的能力上限；每次调用再与当前项目、调用者及 Team 范围求交。新增项目权限不会自动扩大既有会话。较窄调用上下文不能读取或控制当前有效权限更宽的会话。撤销的工具不再向模型提供，旧记录作为历史保留。session.manage 是专用语言/API 权限，不能请求为普通 Agent 工具。

有界图像结果以原生多模态消息进入历史，不恢复浏览器对象或旧定位句柄。超出消息/上下文预算会报错，不静默丢图。电脑资源仍随当前运行生命周期关闭；后续调用必须重新观察，不能把历史截图当作当前屏幕。

## 提交、失败与协调

领取会话、建立 pending、绑定真实 run/sourceVersion 在事务中完成。完整助手工具批次先写入日志，然后才执行工具；每项实际结果随后写入。最终完整上下文与回执原子提交。未完成的调用保留旧已提交上下文及 pending 日志，进入 needs_reconciliation，主机重启也不会重播工具。

会话提交与外层 Process 状态提交是两个持久效果边界。例如助手已经完成并提交，但 step 随后抛错：会话结果仍然存在，Process 需要协调。使用原 message_id 可以取回该结果；程序应根据已发生的源码采用、会话回执和工具记录选择新的 Process 检查点，不盲目重跑整轮。

```chaos
let current=await session_status("assistant");
let history=await session_history("assistant",after=0,limit=50);
// 核实 pending 日志及外部效果后，明确放弃未完成的会话轮次。
await session_reconcile("assistant",current.revision,"已核实外部效果；后续使用新的消息 ID 继续");
```

协调保留 pending 日志与已放弃的 ID，恢复最后已提交上下文，不编造模型回答或工具结果。需要补充观察或重试时，提交一个新 ID 的用户输入。所有 session 效果均不能录制为可重播 Skill，拒绝发生在派发前并使录制失效；禁止 Agent 的函数节点也不能借会话执行 Agent。

## 有界上下文、分叉和关闭

每个公开消息最多40000 JSON字符；context_limit 范围1000–200000，默认100000。预算超限明确失败，不自动截断、不按文本内容推断“重要”消息。可编程策略使用 `session_compact(key,revision,keep_turns)` 保留最近 N 个完整已提交轮次作为活动上下文，N 可为0；完整存档继续可分页查询，不从中间切断工具调用/结果。

`session_fork(key,revision,new_key,agent=null)` 复制已提交的活动上下文到新会话，可显式更换公开 Agent 定义但不能扩大原权限上限；减少新 Agent 的 tools 只会减少后续工具调用；分叉仍保留继承历史的访问权限上限，不能借分叉把较宽权限产生的历史交给较窄调用者。原会话不变。返回新的受控句柄。源码自修改需要改变持续角色时可选择分叉，不能靠同名提示词静默替换绑定。

`session_close(key,revision)` 关闭空闲会话，保留历史；忙时须先取消真实 run 并协调。closed 会话不能执行新一轮调用，已提交的同 ID/同输入回执仍可取回；也可从已提交上下文分叉。fork、compact、close、reconcile 都使用 revision CAS，过期请求不得覆盖新状态。每项目最多32个、每宿主最多256个保留会话；历史和幂等回执占用持久存储。

```chaos example=session-cold-inspection
[type_of(session_status("assistant")),type_of(session_history("assistant",after=0,limit=20))]
```

## 持续给自己编程的 dot

发行包包含 [启动程序](../examples/session-dot/main.chaos)、[持续角色函数](../examples/session-dot/dot.chaos)、[行为模块](../examples/session-dot/behavior.chaos)和[验收模块](../examples/session-dot/tests.chaos)。两个角色各有稳定会话 key 与消息 ID。Process 每轮取得最新程序快照，加载原会话；编程角色提出候选，审查角色评价，普通函数执行实际测试并采用程序，下一轮继续新源码。

这是语言程序，不依赖网页计时器或 Codex App。示例的可复现测试使用受控 native 传输，核实第二轮真的包含第一轮原生消息，三轮内完成两次源码采用；不构成外部模型优化成功率评估。有限测试也不证明任意输入正确。

## GUI、SDK、CLI

IDE 的“原生会话”面板直接显示同一持久会话、真实消息和工具日志、当前状态与实际 run。界面关闭后已提交的后台调用继续；外部 SDK/源码更新也从同一存储读取。表单与消息草稿不因轮询自动覆盖，过期操作显示冲突。

```python
from chaos import Client
client=Client("http://127.0.0.1:8765")
project=client.create_project("conversation",source="null",capabilities=["session.manage"])
agent={"kind":"agent","name":"assistant","config":{"provider":"model","tools":[],"prompt":"Answer using this conversation."}}
session=client.open_agent_session(project["id"],project["version"],"assistant",agent)
run=client.start_session_turn(project["id"],project["version"],"assistant",session["revision"],{"question":"开始分析"},message_id="request-001")
receipt=run.result(timeout=60)
history=client.agent_session_history(project["id"],"assistant")
```

嵌入式 Framework 提供相同方法。`start_session_turn` 返回普通 RunHandle，使用现有查询、等待、取消和事件 API；HTTP turns 返回202运行记录，请求线程不等待模型。语言在已有运行中调用同一宿主，不另建一套后台执行器。

```shell
chaos session list conversation --server http://127.0.0.1:8765
chaos session history conversation assistant --after 0 --limit 50 --server http://127.0.0.1:8765
chaos session send conversation assistant --version 1 --revision 1 --input-json '{"question":"继续"}' --message-id request-002 --server http://127.0.0.1:8765
```

send 需要实际当前 revision，上例1仅示意。open 使用 --agent-file 和 --version；fork 使用 --revision/--new-key 与可选 --agent-file；compact 使用 --revision/--keep-turns；close/reconcile 使用 --revision，后者另有 --evidence。send 返回真实运行记录，可用既有运行 API 查询，不在 CLI 复制会话。

完整接口见[HTTP](HTTP_API.md)与[SDK](SDK.md)。这里持久化的是公开模型协议与事务记录，不是模型权重、隐藏推理、执行栈或完整桌面会话。

HTTP 在入队前发现的过期 revision、关闭状态或 messageId 冲突返回 409；请求入队后的并发竞争仍由执行线程重新检查，失败记录在返回的 Run 中。请同时检查 Run 终态。


## 0.25：显式观察输入

Session 可接收 observation_input(observation,input)，原生 user 消息含真实 text/image_url parts；消息验证在 claim 前完成，幂等摘要纳入图片。后续轮次恢复公开视觉历史，不恢复电脑资源。详见[Computer](COMPUTERS.md)。

## 0.46：绑定当前程序的函数

`session_bind(session,tools)` 为已加载句柄创建独立的可调用绑定，复用原 AST 执行闭包与异步 AgentTool。新一轮显式绑定，普通 Session 调用方式不变；幂等 ID 同时区分原始调用与语言工具合同。可选 toolBinding/lastToolBinding 保存真实执行证据，不能恢复闭包。详见[持久函数工具](SESSION_TOOLS.md)。


## 与每轮原生 Bot 的持久边界

Process 可以每轮 load／bind 原 Session，并创建新的 Bot 回调。`process_guide` 的持久指导与 Session 模型消息不是同一个队列，也不会隐式添加 system／assistant 角色。Session 成功提交后，Bot 或 Process 仍可能失败；先用稳定会话 messageId 核实已提交回执和实际工具日志，再显式协调 Process，不自动重跑该会话或外部动作。
