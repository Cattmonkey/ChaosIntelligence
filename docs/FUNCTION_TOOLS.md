# 语言函数作为智能体工具

一个智能体可以把同一程序中的函数作为自己的工具。工具函数能够捕获局部状态，调用其它智能体、使用异步控制流与资源作用域；这些行为都通过原解释器执行。无需先在 Python Registry 中注册这个函数，也不从模型回复的关键词猜测动作。

## 工具合同、活绑定和调用

```chaos example=function-tool-values
let twice=agent_tool("twice",fn(x:Int){x*2},
  description="将整数乘以二",input_type="Int",output_type="Int");
agent worker {provider:"local",tools:[]}
let equipped=agent_bind(worker,[twice]);
[type_of(twice),type_of(equipped),type_of(equipped(21))]
```

结果 `["AgentTool","Function","Task"]`。AgentTool 是工具合同与语言 callable 的绑定；BoundAgent 是满足 Function 契约的智能体可调用值。`agent_bind` 不改变原 Agent。绑定函数的调用创建冷 Task，`await equipped(input)` 才开始提供方执行；构造、检查和创建冷任务本身不调用模型。

| 函数 | 行为 |
| --- | --- |
| `agent_tool(name,handler,description="",input_type="Any",output_type="Any",capabilities=[])` | 绑定一个一元 callable 与公开工具合同 |
| `agent_bind(value,tools)` | 为一个 Agent 配置语言工具，返回 BoundAgent |
| `agent_tool_info(value)` | 返回工具合同的隔离副本 |
| `agent_binding_info(value)` | 返回原 Agent 的公开定义及工具合同；不返回函数实现 |

```chaos example=function-tool-contract
let twice=agent_tool("twice",fn(x){x*2},
  input_type="Int",output_type="Int");
let info=agent_tool_info(twice);
[info.name,info.inputType,info.outputType,info.capabilities]
```

输入和输出合同使用 `Any`、`Number`、`Int`、`String`、`Bool`、`List`、`Record`、`Null`。这是明确的语言类型检查，不是完整 JSON Schema。`Any` 仍受 JSON 边界限制，不表示允许任意活对象。输入合同在 handler 执行前检查，输出合同在得到最终结果后检查；模型声称遵守合同不代替运行时校验。

handler 接收一个输入值，需要多个参数时可显式使用 Record 或普通语言适配函数。名字、描述及合同均由程序提供，不从函数名推断策略。提供方向模型声明 `{input: ...}` 参数对象，再将其中的 input 交给 handler。

工具名称沿用宿主 ToolSpec 的命名约束；一次绑定接受 0 至 128 个工具，拒绝重复名称和与原 Agent 已请求宿主工具重名。空工具列表仍可使用原 Agent 的宿主工具或直接取得提供方结果。调用支持注册 Provider 和 native 模型协议。演示用 `local` provider 没有结构化工具协议，因此本页第一个纯示例只构造冷任务；不能用它执行绑定后的工具调用。

## 在原闭包中执行

下面完整示例使用确定性 Provider 发出真正的结构化 ToolCall，以便无需外部模型就能验证语言调用链：

```python
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall

registry=Registry()
def choose(request, context):
    context.checkpoint()
    if request.step == 1:
        return ToolCall("twice", request.input)
    return Final(request.history[-1]["output"])
registry.register_provider(ProviderSpec("choose", choose))

source='''
var seen=[];
fn twice(x:Int)->Int {seen=append(seen,x);x*2}
agent worker {provider:"choose",tools:[]}
let equipped=agent_bind(worker,[agent_tool("twice",twice,
  input_type="Int",output_type="Int")]);
[await equipped(input),seen]
'''
with Framework(registry=registry,settings=Settings(":memory:")) as app:
    app.create_project("function-tools",source=source,capabilities=[])
    assert app.start("function-tools",1,21,mode="source").result() == [42,[21]]
```

`seen` 属于原词法环境。提供方的工作线程仅处理协议和 JSON；收到工具请求后，线程让出执行位置，由原 AST 调度器调用 `twice`。不会创建另一个解释器来模拟这个闭包，也不会让全部效果线程阻塞等待语言函数。

生产模型可把 provider 换为 `"model"`，使用本系统配置的 native backend、base URL、密钥和路由。普通模型最终文本仍为 String；需要 Record 时由语言程序显式解析和校验。该功能不依赖 Codex App，也不向独立 Codex 后端偷偷传递宿主函数。

## 异步、嵌套与失败

handler 可以是普通函数、异步函数或另一个智能体。若结果是 Task，执行路径等待一层；随后必须得到符合合同的真实 JSON。再次返回冷 Task、Generator、Computer、函数或其他活句柄不算工具完成，不能先转成描述记录来绕过限制。

嵌套智能体仍使用同一调用链、总预算和能力范围。工具内部可以显式组合 Plan、TaskGroup、Channel 和资源作用域；普通语言代码负责业务重试与停止条件。提供方的步骤上限、模型回退和协议校验继续有效；原生工具调用批次必须先验证，再执行任何一项。

可报告的普通工具错误与取消、资源耗尽不是同一类结果。硬控制必须终止并收束执行，不能转换成模型可忽略的成功结果。已发生的外部效果不会因失败回滚；绑定也不是任意工具事务或取消屏蔽。

## 能力与持久化边界

工具名称是模型的调用入口，不是宿主权限。`capabilities` 默认空列表。普通宿主工具能力仍须由原 Agent 的 tools 以及上层执行范围同时允许。语言专属权限可在 AgentTool.capabilities 中显式委派给真实回调；创建、绑定和实际调用都受当前项目、角色与调用链的权限上限约束。模型只看到声明的具名函数，不会因此获得保留权限对应的直接工具。

例如，已授予 process.manage 的程序可绑定 `agent_tool("guide_worker",async fn(request){await process_guide(request.key,request.input)},capabilities=["process.manage"])`，让模型通过该函数给现有持久进程提交引导；调用方还须满足目标 Process 原启动权限上限，必要时显式声明该目标所要求的其他权限。Session 管理、程序采用及 Device／Robot 操作使用同一委派规则；回调仍须取得有效原生资源、遵守版本检查和动作回执合同。普通 `tool("process.manage",...)` 继续被拒绝。

工具内部的 `tool(...)`、嵌套 Agent 与其他效果仍受相应范围、预算和取消约束。绑定一个捕获闭包不会授予它创建者曾经拥有的更多权限；未显式声明的能力仍不可用。语言权限不能由模型文本、工具名称或合同 JSON 自动授予。

绑定属于当前解释器，不能从普通 JSON 恢复为函数。`agent_tool_info` 和 `agent_binding_info` 只用于检查合同；没有隐藏源码、捕获环境、密钥或可恢复暂停栈。`agent_info`、`agent_source`、Session 配置与 Team 定义不能悄悄丢掉工具绑定。需要持续 dot 时，把函数写在普通模块里，每轮重新构造绑定；Program 仍保存并版本化真实源码。

录制技能记录语言工具实际产生的宿主工具回执。纯计算不变成一个可跨进程重放的闭包；播放技能沿用已有宿主效果回放协议。资源获取和清理应使用语言资源作用域，确保回执属于正确录制范围。

## IDE、无界面与自编程

源码、结构 IR、CLI、Framework、Client 和网页运行同一个程序。IDE/LSP 使用真实内置签名与返回值信息提供颜色、参数提示和补全；格式化调整排版，不执行 handler 或模型。

Program 可以修改包含工具函数和绑定的模块、运行候选，再采用新版本。当前运行保持自己的函数与闭包快照，下一次运行加载新源码。这使 dot 的工具策略成为语言程序的一部分；不意味着模型能修改宿主解释器、迁移活栈或自动证明任意优化正确。

相关指南：[智能体语言设计](AGENT_LANGUAGE.md)、[定义值](AGENT_VALUES.md)、[自编程](SELF_PROGRAMMING.md)、[资源作用域](CONTEXTS.md)、[动态资源栈](RESOURCE_STACKS.md)、[录制技能](AGENT_MODES.md)。

从 0.44 起，可在 Program cases 中提供显式智能体 fixtures，要求模型调用语言工具并核对其真实输出，再执行版本采用。它补齐完整调用链的验收；没有使用模型回答猜测通过。详见[智能体协议测试](AGENT_TESTING.md)。

从 0.46 起，`session_bind` 将同一 AgentTool 与已打开的 AgentSession 组合；仍不能把 BoundAgent JSON 当成会话定义。工具合同持久化、闭包由当前程序重建，参见[持久函数工具](SESSION_TOOLS.md)。

## 位置专用 handler

handler 可以声明 `/`，例如 `fn(request: Record, /) { request.value }`。AgentTool 按位置传入单个 input，异步 handler 的一层 Task 等待规则保持不变。partial 的同名关键字仍按函数的 `**` 规则绑定。该能力不把多参数签名转换为提供方 schema，也不新增持久合同字段；要接收多个业务字段，继续使用一个明确的 Record 输入和参数模式。
