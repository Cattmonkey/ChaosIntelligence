# Team：真实函数与持续智能体的角色组合

0.47 的 `team_bind` 将 Team 的角色关系、能力上限与当前语言中的实际 callable 组合。角色可以由普通函数、异步函数、Agent、绑定工具的 Agent、AgentSession 或 BoundSession 实现。调用仍走同一个 AST 解释器与调度器。

## 保留函数本来的参数和结果

```chaos example=team-function-keywords
let policy=team_from({kind:"team",name:"calculation",entry:"worker",roles:[
  {id:"worker",parent:null,capabilities:[],agent:{kind:"agent",name:"worker",config:{provider:"local",tools:[]}}}
]});
fn add(value:Int,*,offset:Int=2)->Int {value+offset}
let crew=team_bind(policy,{worker:add});
let members=team_bindings(crew);
let pending=members.worker(40,offset=2);
[type_of(crew),type_of(pending),await pending]
```

结果 `["Function","Task",42]`。构造、绑定与创建 Task 不运行角色；await 或调度才执行。参数按原函数规则转发，保留位置参数、命名参数、默认值和类型契约，不把全部参数包装成固定 input Record。调用入口 Team 等价于调用其 entry 角色。

若实际 callable 返回 Task，角色等待一层并保留其结果；不循环展开任意 Task。普通函数的语言对象结果保持原语义，不强制转换为 JSON。Agent 与 Session 仍遵守自身的数据边界，Session 返回完整回执，不自动取 `.output`。

| 函数 | 行为 |
| --- | --- |
| `team_bind(value,bindings)` | 从普通 Team 和 role id→callable Record 构造本解释器、本执行轮次拥有的 BoundTeam |
| `team_role(value,role)` | 为普通 Team 或 BoundTeam 取得角色 callable |
| `team_bindings(value)` | 返回普通 Team 或 BoundTeam 的全部角色函数 Record |
| `team_binding_info(value)` | 返回 `{team,definitionHash,bindings}`，其中 bindings 只描述显式绑定角色的 `{kind}` |

bindings 可以是部分角色映射；未绑定角色仍执行定义中的默认 Agent。绑定不会改写原 Team、已有角色句柄或之前创建的任务。`team_info`、`team_source` 只导出普通 Team，拒绝把 BoundTeam 的函数悄悄丢弃。新接口的 definitionHash 仍标识原定义，不是闭包源码或捕获值的摘要。

## 持久会话是实际角色实现

```chaos
let session=await session_load("researcher");
let worker=session_bind(session,[agent_tool("calculate",fn(x:Int)->Int{x*2},
  input_type="Int",output_type="Int")]);
let crew=team_bind(policy,{researcher:worker});
let receipt=await team_role(crew,"researcher")({question:input},message_id="request-001");
receipt.output
```

这里 policy 须有 researcher 角色，其上限包含 `session.manage` 及会话所需工具能力。命名参数 message_id 原样转交会话；重复 ID 的身份检查、回执、真实消息与绑定合同沿用 Session 协议。历史 JSON 不会恢复 handler；主机重启后由当前源码显式重建 Team 及函数绑定。

## 角色范围跨越异步与清理

执行能力取角色上限、绑定创建时范围、函数创建时范围及实际调用者范围的交集。父子关系限定上限；从子角色调用父角色不会恢复父级更宽权限。新建调用可以在更窄范围运行，但不能借用一个已创建、权限更宽的 Task 或资源来绕过边界。

受限角色内新建的闭包带出作用域后仍保留创建时能力。Task、生成器、迭代器和资源栈的后续执行与清理保留其创建/获取时的角色来源；普通 callable 被另一个角色调用时记录实际调用角色，同时保留能力交集。嵌套返回后恢复外层来源，并发角色不共享一个可变全局角色状态。

实际角色事件及工具事件携带 Team 来源，可定位角色路径、实际实现种类与本次调用。公开定义哈希不代表函数实现相同；真正代码仍由运行的源码快照与版本确定。输入 Record 中伪造 `_team` 不会安装执行范围。

取消与失败复用原 TaskGroup、资源作用域和宿主清理协议，不回滚已发生的外部效果；Session 的不确定轮次需显式协调。录制 Skill 只记录允许录制的真实效果，不能把 Session 管理变成可重播工具。

## 用语言实现会修改自己的角色团队

随包示例：[启动](../examples/function-team-dot/main.chaos)、[角色程序](../examples/function-team-dot/dot.chaos)、[行为模块](../examples/function-team-dot/behavior.chaos)、[测试入口](../examples/function-team-dot/tests.chaos)。

programmer 与 reviewer 由两个持久 BoundSession 直接实现；check 是接收真实 Program 与 List 的异步函数。审查工具调用 check 角色执行候选测试，ProcessEvolution 再进行最终测试并原子采用源码与检查点。每轮重建当前实现，保留原生会话和稳定消息 ID。该组合没有额外后台工作流解释器。

Session 回执与 ProcessEvolution 仍是两个提交边界。有限测试验证具体调用链，不证明任意候选程序正确，不迁移活调用栈，也不表示完整 Python/Lisp 生态兼容。

## 可视编写与源码使用同一语义

Team 表单编写角色关系、能力和默认 Agent，输出普通源码模块；活实现由源码中的 team_bind 明确绑定，表单不能通过 JSON 恢复闭包。IDE/LSP 根据真实 AST 数据流提供已知角色的补全和实际 callable 参数提示，包括 Session 的 message_id。动态入口或无法确定的绑定保持保守类型。

高亮、格式化、源码/IR、CLI、SDK 与网页运行使用同一语言程序。格式化不执行角色或请求模型。相关规范：[Team](TEAMS.md)、[持久函数工具](SESSION_TOOLS.md)、[函数工具](FUNCTION_TOOLS.md)、[语言设计](AGENT_LANGUAGE.md)。
