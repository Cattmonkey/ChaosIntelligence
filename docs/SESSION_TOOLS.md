# 持久智能体的语言函数工具

0.46 将持久 AgentSession 与真实语言函数组合。`session_bind` 返回当前解释器拥有的 BoundSession，可像函数一样调用；模型的结构化工具请求回到原 AST 调度器，执行捕获局部环境的普通函数或异步函数。

## 显式组合

```chaos
agent worker {provider:"model",tools:[]}
let session=await session_open("worker",worker);
let offset=2;
let add=agent_tool("add",fn(value:Int)->Int {value+offset},
  description="加上当前程序的偏移值",input_type="Int",output_type="Int");
let equipped=session_bind(session,[add]);
let pending=equipped({value:40},message_id="request-001");
let receipt=await pending;
[receipt.output,session_binding_info(equipped),session_info(session).turn]
```

项目须授予 `session.manage`，且 native 模型后端使用本系统的 Settings。`session_bind` 与 `session_binding_info` 是纯操作；调用 equipped 创建冷 Task，await 才领取会话并请求模型。回执沿用 `{key,messageId,status,output,revision,replayed}`，output 为 String。模型最终文本不会被关键词猜测成动作。

```chaos example=session-tool-composition-function
fn equip(session,offset:Int)->Function {
  session_bind(session,[agent_tool("add",fn(value:Int)->Int {value+offset},
    input_type="Int",output_type="Int")])
}
type_of(equip)
```

结果 `"Function"`，此示例只定义组合函数，不打开会话或请求模型。

| 函数 | 返回与约束 |
| --- | --- |
| `session_bind(value,tools)` | 给当前解释器的 AgentSession 绑定 AgentTool 列表，返回满足 Function 契约的 BoundSession |
| `session_binding_info(value)` | 返回 `{session:{key,revision,definitionHash},tools,bindingHash}` 的隔离副本 |
| `equipped(input,message_id=null)` | 冷 Task；await 后得到持久会话回执 |

每个工具的公开合同为 `{name,description,inputType,outputType,capabilities}`。使用与 BoundAgent 相同的严格 JSON、输入/输出类型检查和一层 Task 等待。工具最多128个，合同总量受40000字符边界限制。空列表也是一次明确的语言绑定。`session_open` 仍接受普通 Agent 定义；需要绑定时在加载或打开后的句柄上显式调用 session_bind。

## 源码更新与消息身份

会话的 Agent 定义不因绑定工具而改变。绑定摘要包含 language 类型、原 Agent definitionHash 和按名称排序的完整工具合同。同一 message_id 必须具有相同输入及绑定摘要；普通原始会话调用、空语言绑定和非空语言绑定是不同调用身份，不能互相冒充重放。有效重放先于旧 revision 检查，直接返回已提交回执，不再请求模型或执行 handler。

handler 的源码、捕获值不属于合同摘要。相同合同配合相同消息 ID 表示重取旧结果，即使源码已更新；要执行新的计算必须使用新消息 ID。每次领取记录实际 `runId` 与 `sourceVersion`，可以据此找到当时程序快照。

主机重启后，程序先 session_load 或 session_open，再用当前源码的函数显式绑定。保存工具名称及合同不能恢复闭包、权限或暂停栈。普通 AgentSession 调用仍按原接口使用宿主工具；不会隐式安装上一次语言 handler。

## 持久证据与资源

绑定轮次的 pending 和历史消息带有 `toolBinding`，内容为 `{kind:"language",definitionHash,tools,bindingHash,runId,sourceVersion}`。会话的 `lastToolBinding` 指向最近一次成功提交的绑定轮次；之后的普通调用保留该历史标记，它不表示原始调用自动获得工具。旧会话没有这些可选字段，接口保持兼容。

完整助手工具批次先记入日志，然后逐个执行实际函数并写入结果。分叉复制保留轮次的绑定证据，压缩只改变活动上下文；元数据不会变成可调用函数。取消、错误或未交付的开始结果必须关闭驱动并保留不确定记录，不能静默重试可能已执行的工具。

语言 handler 继续使用原调用者的运行资源，捕获的 Computer 由原调用者管理；native 会话宿主工具使用该次会话调用的私有浏览器资源，并在模型协议的多次暂停/恢复间保留。这是两个显式资源范围，不能假设 `tool("browser.control",...)` 的语言 handler 与会话宿主工具操作同一个浏览器。两者都遵守各自清理及取消语义。

普通宿主工具能力必须同时满足 Agent 声明、会话创建时上限、当前项目与调用者范围。语言专属权限只可通过当前程序的真实 AgentTool 回调显式委派：它们按 BoundSession 创建时的调用者权限、当前角色／项目权限及回调 capabilities 求交；不会加入会话的宿主工具列表，也不从 launchCapabilities 或历史 toolBinding 恢复。每个新消息在领取前再次检查当前项目授权，权限撤销后不能执行新的回调。

会话管理等保留权限不能成为普通模型工具。绑定不会让一个空权限 handler 获得 Session 管理权限；历史合同只保留声明及证据，服务重启后仍须用当前源码重建活绑定。相同消息 ID 的已完成回执保持原来的只读重取语义，不执行回调。Session 调用不能录制为可重播 Skill。

## 可持续修改工具的 dot

完整示例：[启动](../examples/tool-session-dot/main.chaos)、[持续函数](../examples/tool-session-dot/dot.chaos)、[行为模块](../examples/tool-session-dot/behavior.chaos)、[测试入口](../examples/tool-session-dot/tests.chaos)。

编程角色调用捕获当前 Program 的 program_snapshot 函数；审查角色调用捕获候选程序的异步 check_candidate，真实执行 program_probe。两个角色各自保留原生历史，每一轮从当前源码重新绑定。ProcessEvolution 在清理和最终测试后原子采用候选源码与进程检查点，下一轮加载新版本。

会话回执与 ProcessEvolution 仍是独立事务。若角色已完成而后续进程失败，会话历史保留；恢复时使用原消息 ID 取回结果，再据真实证据协调。有限测试证明具体执行链，不证明任意模型改写都正确，也不恢复提供方隐藏状态。

## 页面与无界面使用

IDE/LSP 共用内置函数签名、返回类型和成员信息，提供颜色、参数提示、补全及保持语义的格式化。会话面板显示真实合同、运行与源码版本；源码/IR、CLI 和 SDK 运行同一程序。HTTP/SDK/CLI 可以读取 toolBinding，但不能通过 JSON 提交一个闭包。网页的普通发送表单仍执行普通 AgentSession 调用；语言工具调用应运行包含 session_bind 的项目源码。

相关指南：[会话](SESSIONS.md)、[函数工具](FUNCTION_TOOLS.md)、[Process 演进](PROCESS_EVOLUTION.md)、[语言设计](AGENT_LANGUAGE.md)。

0.47 可将预先创建的 BoundSession 直接作为 Team 角色实现。新调用按角色与捕获权限求交，message_id 和完整回执直接转发；已创建的宽权限 Task 仍不能被窄上下文借用。见[函数角色团队](TEAM_FUNCTIONS.md)。
