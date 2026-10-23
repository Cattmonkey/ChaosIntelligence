# 智能体、函数与语言运行时

Chaos 面向图灵完备、语义自洽、可组合的智能体函数语言；用户以普通函数、模块与受控效果组合复杂功能。当前发布按[语言核心合同](LANGUAGE_CONTRACT.md#h-一次发布的有限验收)核验计算核心、Agent、生命周期、长期工作、公共入口和包，Python／Lisp 仅作能力对照。

Chaos 把智能体视为一等的、有显式外部效果的可调用值。它可以保存到绑定、作为函数参数传入、出现在列表或记录中，也可以通过 `apply`、`partial` 和 `compose` 组合。普通算术、数据处理、控制流和语法宏由解释器执行，不需要模型参与。

## 一等可调用值

```chaos example=agent-callable-values
fn invoke(f: Function, value) { f(value) }
agent reviewer {provider: "local", mode: "review", tools: []}
[invoke(fn(x) { x + 1 }, 41), type_of(reviewer), type_of(length)]
```

结果是 `[42, "Function", "Function"]`。此例声明 Agent，但没有调用它，因此在纯 `Framework.evaluate` 环境中也能运行。`Function` 契约共同接受用户函数、原生内建函数和 Agent；Agent 不是单独的内置类型契约。

| 值 | 调用含义 |
| --- | --- |
| 用户 `fn` | 绑定参数、检查契约、在捕获的词法环境中执行 AST |
| `Native` 内建函数／构造器 | 通过受控调用入口执行内建操作；效果类内建继续转入 Context |
| `Continuation` | 在当前合法 holder／epoch 上组合一份已保存同步后缀；仍受原权限、契约、预算和线性屏障检查 |
| `Agent` | 接收一个 `input` 参数，交给当前执行上下文的智能体处理器 |
| `async fn` | 建立 Task，调度并等待后得到函数完成值 |
| `fn*` | 建立 Generator，消费时逐次恢复函数执行 |
| `async fn*` | 建立 AsyncGenerator，通过异步流协议逐次恢复并等待效果 |

当前原生全局函数的参数、返回、等待结果与字段由同一 NativeSpec 合同描述；原生可调用资源使用同族调用描述，IDE 不另维护 Bot 原语的返回／等待补丁表。别名保留真实身份，用户函数遮蔽原语后不继承其提示。开源 Bot 的源码对照、内化方式及边界见[Bot 语言设计](BOT_LANGUAGE_DESIGN.md)。

Agent 支持 `reviewer(value)`、`reviewer(input=value)`，也能通过 `apply(reviewer, [value])` 调用。没有智能体处理器的纯求值环境会拒绝调用 Agent；声明本身不会调用提供方。

## 用语言核心组织复杂工作

先用函数、数据与显式契约定义纯策略，再由 AgentTool／Team／Bot 或 async Task 调用宿主效果；Context 决定权限、预算、取消和实际准入，语言控制不读取提示词来猜完成。已完成的 Agent 规划与软件 Robot 观察／命令可形成输入数据，再在资源关闭后捕获同步计算后缀，重复探索纯数据决策。跨 Task 的恢复被拒绝，返回原合法 holder 后仍可组合；不能以 continuation 复制设备租约、Goal lease、录制状态或重用一次性命令。

复杂程序的完成由原函数结果、Bot 的 status／done、Goal／Process 状态和宿主回执判定；普通 Bot 执行错误可进入 needs_reconciliation，调用方须检查证据并协调，不能只看 output 或提示词中的完成文本。

每次显式 k 调用都会执行后缀中的效果，不能把“前缀不重放”理解成所有效果恰好一次。长期目标用既有 Goal checkpoint／lease 与 Process 事务协调；continuation 是同次运行内的控制值，不是持久任务、事务回滚或自动重试。组合依赖及边界见[语言核心合同](LANGUAGE_CONTRACT.md)。

## 从源码到提供方与工具

真实调用链是：源码解析与宏展开 → Evaluator 的统一调用入口 → 运行 Context → 智能体提供方／工具 → 复制并检查的结果。`provider: "local"` 是确定性的本地规则演示，不能代表模型推理；`provider: "model"` 使用本系统 Settings 中配置的模型连接；已注册的 provider 名称进入宿主扩展。

```chaos
agent reviewer {
  provider: "model",
  prompt: "检查输入中的假设，返回简短的改进意见。",
  tools: [],
  maxSteps: 4
}
reviewer(input=input)
```

这段程序需要通过项目运行环境执行，并配置本系统的 native 模型连接。base URL、API key、模型与角色路由由 Settings／设置界面管理；完整配置与可复制学习例子见[模型连接配置](MODEL_CONFIGURATION.md)。源码不保存真实密钥。运行不依赖 Codex App。native 插件关闭或未授予能力时，不能通过语言调用绕过限制；Chaos 语言中的模型 Agent 不使用独立的 Codex 后台任务后端。

项目 `capabilities` 是授权上限，Agent 的 `tools` 只能选择其中的可调用工具。AgentTool 的真实语言回调可在 capabilities 中显式声明语言专属权限，并继续受当前项目、角色和调用链上限约束；它不把保留权限转换成模型的直接宿主工具。`device.control`、`session.manage`、`process.manage`、`program.adopt` 是语言专属权限，通过对应原生效果使用；`graph.patch` 属于平台拓扑事务。能力发现由同一描述表给出 `scope` 和 `callable`，候选 fixtures、运行时与工作流预检共用分类。纯 Agent 定义校验仍保留历史数据，不等于执行许可。模型提出结构化工具动作后，Context 仍验证工具名称、输入、能力与预算，并记录事件；提示词或宏不能自行增加授权。宿主注册的 Python provider／tool 是受信任扩展，其合作式取消边界见 [SDK](SDK.md)。

宿主工具的 native 声明现在与真实 ToolSpec 输入合同共用一条纯描述投影；普通 Agent、AgentTool 绑定 Agent、普通 Session 和绑定 Session 都使用它。模型参数统一包装为且仅为 `{input: value}`，按声明的 Any／Number／Int／String／Bool／List／Record／Null 描述；缺省合同是 Any。Int 运行时只接受整数，拒绝 Bool 与 `42.0`；Number 同样拒绝 Bool。每个模型响应的整批调用先按实际语言类型检查，后续参数有误时，前面的有效调用也不会执行。描述不会扩大权限；具体记录字段或任意用户合同并不会自动转换成提供方 Schema。Session 已持久化的本轮 user 输入仍保留，但非法批次不会写入 assistant 工具意图或调用回调。

## 宏、生成器与多个智能体任务组合

下面的完整 Python 示例注册一个确定性提供方，验证真实语言调用链，不需要外部模型服务。生产应用可以将 `app.double` 换成自身提供方或已配置的模型路由。

```python
from chaos import Framework, Registry, Final

registry = Registry()

@registry.provider("app.double")
def double(request, context):
    context.checkpoint()
    return Final(request.input * 2)

source = '''
agent worker {provider: "app.double", tools: []}
macro launch(callable, value) {
  quote { async fn() { unquote(callable)(unquote(value)) } }
}
fn* inputs() { yield 20; yield 21 }
let jobs = map(fn(x) { launch!(worker, x)() }, inputs());
await gather(jobs)
'''

with Framework(registry=registry) as app:
    project = app.create_project("agent-language", source=source, capabilities=[])
    run = app.start(project["id"], project["version"], None, mode="source")
    assert run.result(timeout=5) == [40, 42]
```

`launch` 也可以定义在独立模块中并通过 `export {launch}` 导出；入口用 `import "launch.chaos" as L` 和 `L.launch!(worker, x)` 调用。库中私有宏 helper 的含义绑定在定义模块，显式传入的 `worker` 和 `x` 保留调用者词法环境。宏导出不会增加运行期模块记录的字段，也不会自动授予 Agent 或工具能力。

这里宏只生成异步函数的 AST；Generator 产生输入；`map` 创建任务；`gather` 调度它们并按输入顺序收集完成值；Agent 调用进入注册的提供方。语言局部环境由解释器线程推进，阻塞的效果可由受控工作线程处理。纯 CPU 算术不会因此自动获得多核并行。

任务共享运行的总预算和取消边界。取消不回滚已经发生的工具副作用，也不能强制杀死不合作的 Python 回调。Task／Generator 是当前执行周期的句柄；数据库里的事件、结果或句柄描述不等于可跨进程恢复的暂停栈。详细的 `spawn`、`gather`、错误传播与关闭规则见[生成器和异步任务](EFFECTS.md)。

## 长期机器人与物理设备

机器人行为由普通函数、AgentTool 和 Team 组合；长期身份由 AgentSession key 表达，跨轮调度与有界数据记忆由 Process 管理，自我修改继续走 Program 测试及 CAS 采用。主角色是显式授权的普通角色，群聊收件人是结构化参数，不从 `@` 或固定回复文本解释控制流。Device／DeviceAction／DeviceReceipt 将已注册外接 HID 的租约、一次性动作、取消和回执接入原 Task／Effect 及资源作用域，不再建立应用专属执行机。可信宿主可选择串口桥或[树莓派 Linux HID](RASPBERRY_PI.md)，同一个函数程序和权限合同无需按后端重写。详见[机器人组合与外接设备](ROBOT_DEVICES.md)。

Computer 与 Device 的受限别名保留能力上限，派生动作和冷任务不能把窄权限恢复为宽权限；别名共享原租约及动作消费状态。视觉数据仍可交给没有浏览器控制权限的只读 Agent。硬件 ACK 不等于目标完成；Process 不保存活设备句柄，每轮重新获取，未知效果仍须显式核验。完整自动群聊、语义记忆整理和无限自我优化没有因此自动实现。

## 系统 IDE 中的语言能力

IDE 编程助手通过本系统配置的模型连接运行独立的 native 对话任务。它可调用 `language.reference` 读取当前安装版本的文档，随后用真实结构化工具编辑多文件草稿。工具声明列出可选主题，包括总览 `language`／`agent-language`、标准库 `stdlib`、能力边界 `language-roadmap`／`semantics`，以及对象、宏、调试和智能体资源的专门指南。新语法与并发指南可直接用 `channels`、`waits`、`select`、`iterable-composition`、`tuples`、`comprehensions`、`pattern-bindings` 获取；主题必须是声明中的固定 ID，不接受文件路径或任意网址。

`draft.validate` 会解析、链接并展开宏，检查纯编译阶段；`draft.evaluate` 使用有界隔离环境；省略 fixtures 时只做纯求值，提供显式 fixtures 时执行真实语言工具并核对智能体协议，不访问生产模型或真实宿主效果。修改先成为可检查提案，用户应用时经过项目版本比较与原子保存，同步源码和原始 IR；过期提案不会覆盖新版本。助手与项目运行是不同任务，助手的权限不能代替项目能力授权。

源码编辑器的高亮、补全和参数提示由语言分析器生成，不需要等待模型回答。分析可以提示已知类型和结构不匹配，但不运行函数、getter 或 Agent 来推测结果；动态值保留 Any。“文件 → 项目权限”将明确选择加入项目草稿，与源码或结构 IR 一起通过原版本检查保存；打开对话框不会授权。静态设备清单显示可信宿主注册的名字，不探测硬件。页面代码预览只读，可用同一格式化器生成独立阅读副本，复制和导入仍取原始源码；编辑器格式化修改本地草稿，实际保存仍经过项目版本检查。不同编辑区域的快捷键、保存时格式化和执行快照说明见[IDE 指南](IDE.md)。

本系统提供可组合的智能体运行核心、版本化 IDE 和扩展接口；不声称兼容所有现有通用语言、自动完成任意任务或形式化证明所有程序正确。实际语法以[语言参考](LANGUAGE.md)、[宏规范](MACROS.md)和随版本运行的测试为准。

语言情况系统的 `invoke_debugger` 将交互式恢复放在真实运行栈中。编程助手或外部工程可以先读取暂停时的纯 Condition／出口数据，再通过原 SDK 提交明确的 pauseId／restartId 及参数；模型提案不能创建出口、跨任务跳转或扩大工具能力。实际恢复复用原 RestartTransfer、资源清理和版本快照。持续 Process 可在后续轮次采用经测试的新策略，暂停栈本身仍不跨服务重启恢复。详见[情况与恢复](CONDITIONS.md)。

## 智能体组件与对象状态

[组合契约](CONTRACTS.md)用普通不可变描述值声明智能体实现函数的输入与返回边界，复用原有参数绑定、模块和能力规则。List／Record／Tuple／HashMap 可组合，并通过 contract_ref／contract_recursive 声明局部绑定的递归与互递归载荷。开放模板只作为描述值，实际注解要求闭合；检查保持纯粹，不调用模型或用户协议；提供的是运行时数据合同，不是自动供应商 Schema 或另一套应用端校验层。

状态可使用[原生 HashMap](HASH_MAPS.md)关联复合键或用户对象与真实语言值。值式更新保留旧版本，用户哈希／相等沿原函数和效果系统执行；键必须在存放期间保持稳定，能力投影不借容器扩大授权。

类把状态、纯函数和效果调用组织成可复用组件。Agent 保存为 `let` / `var` 字段时仍是原来的一元可调用值；读取它不会插入 `self`。通过命名 `fn` 声明的方法才绑定接收者。实例的 `__call__` 使组件本身可以传给 `map`、`apply`、`partial` 或其他函数。

```chaos example=agent-object-component
class Component {
  var action:Function = fn(x) { x };
  var calls:Int = 0;
  fn __init__(self, action:Function) { self.action=action; null }
  async fn __call__(self, value) {
    self.calls=self.calls+1;
    self.action(value)
  }
}
let component = Component(fn(x) { x*2 });
let result = await gather([component(20), component(21)]);
[result, component.calls]
```

结果为 `[[40,42],2]`。项目运行中可把普通函数参数换成已声明的 Agent，调用仍走统一 Context、能力检查和总预算。类继承和方法包装不会授予额外权限。类不是另一个后台进程；实例成员在解释器线程中读写，异步任务在挂起处交错，效果工作线程只接收数据。

实例的事件、结果或外部效果描述只用于观察与传输，不恢复活对象、方法闭包或暂停栈。需要持久业务状态时，显式将普通数据保存到项目记忆，再在新执行周期中构造组件。成员、继承和共享默认值细节见[对象规范](OBJECTS.md)。

需要把实例自身已赋值状态送给智能体时，可显式传入 `vars(instance)`。它返回新的 Record，排除未覆写的继承默认；包含对象或函数的字段仍遵循外部描述规则。只需部分业务数据时可显式构造 `{calls: component.calls}`。这不会把实例或其执行权限交给模型。

低层 `Evaluator` 直接接收的同步 Python 回调属于受信任宿主扩展，保留旧 Function/Native/Agent 活值兼容接口；旧函数闭包可间接引用实例。这一接口不构成隔离沙箱。新对象出现在传入数据树中时转换为描述；Framework/Registry、HTTP API 与异步效果工作线程继续使用 JSON 数据边界。

## 用语言装饰器组合智能体

Agent 声明可以带 `@decorator`。装饰器接收原 Agent 可调用值，可返回普通函数、异步函数、流函数或其它值；运行时不会按函数名猜测“缓存”“重试”等策略。策略由用户语言代码表达，原 Agent 调用继续经过 Context 的能力、预算和事件协议。

下面的流装饰器可用于普通函数；在项目中将被装饰声明换成 Agent 即可逐项调用提供方：

```chaos example=agent-decorator-stream
fn streaming(action) {
  async fn*(inputs) { for value in inputs { yield action(value) } }
}
@streaming
fn double(value) { value*2 }
await acollect(double([20,21]))
```

流按消费需求推进，取消使用原运行的合作式取消边界。自定义重试不会自动延长共享预算，也不会撤销之前的工具副作用；缓存的键、有效期和共享行为由包装函数定义。真实注册 provider 的重试/缓存/并发与取消集成测试见 `tests/test_decorator_integration.py`。

属性和装饰器均为运行期机制；宏用于生成其语法。getter 的调用进入同一个可挂起解释器，因此同步 getter/setter 内的 Agent 在已有异步任务中可以产生效果等待。异步 getter 则返回冷 Task，需显式 `await instance.field`。详细规则见[装饰器规范](DECORATORS.md)。

## 用用户描述符表达智能体属性

属性行为可由语言类定义。getter 中调用 Agent 与显式 `agent(input)` 走同一条效果链；setter、deleter、命名通知也可以在当前异步任务中等待 Agent 效果。协议名是明确的语言接口，运行时不会从提示词或字段名字猜测策略。

下面以普通函数演示一次性计算；在项目中可将 `action` 赋为已授权的 Agent。缓存行为完全由语言代码表达：

```chaos example=agent-user-cached-field
class Cached {
  var name:String = "";
  var calls:Int = 0;
  fn __set_name__(self, owner, name) { self.name=name; null }
  fn __get__(self, object, owner) {
    if object==null { self } else {
      self.calls=self.calls+1;
      let value=object.action(21);
      setattr(object,self.name,value);
      value
    }
  }
}
class Component {
  let action=fn(x){x*2};
  let answer=Cached();
}
let component=Component();
let first=component.answer;
let second=component.answer;
delattr(component,"answer");
[first,second,component.answer,Component.answer.calls]
```

结果 `[42,42,42,2]`。同一类成员的描述符实例由该类的实例共享；如需按对象持久化业务状态，应显式存储普通数据。缓存并不自动提供事务、失效策略或并发去重。真实 provider 的读取、写入、删除、命名、流消费和取消测试见 `tests/test_descriptor_integration.py`，协议参考见[用户描述符](DESCRIPTOR_PROTOCOL.md)。

## 用智能体定义计算协议

组件中的 `__add__`、`__eq__` 等方法可以调用绑定的 Agent。方法是常规语言函数；Agent 是共享项目能力、预算与取消状态的效果函数。普通协议方法在已有 Task 内调用 Agent 时可以挂起，让其它任务继续运行；无需创建另一个不受管理的解释器。

显式 async 算术方法返回冷 Task，由调用者 await；比较、长度、成员查找和 bool 要求具体 Bool/Int，不能用 Task 代替。纯模式匹配与安全变量预览不会触发 Agent。可运行的集成验证见 `tests/test_operator_integration.py`，详细接口见[运算协议](OPERATORS.md)。

## 智能体作为可推进的数据源

通过迭代器协议，智能体组件每次只响应消费者的一次推进请求。`__next__` 可在已有 Task 内调用 Agent，`__anext__` 可用 async fn 返回冷 Task；普通语言控制流定义何时完成、如何处理反馈、怎样关闭。调度器只执行已声明的协议，不从提示词或模型回复中的结束关键词推断耗尽。

同一个原始游标的多个适配别名共享忙锁与终态。不同游标可以并发调用模型；同一游标拒绝重叠推进。消费者、工具和清理阶段继续使用项目能力、预算与事件记录，不能因变成数据流而扩大权限。实际 ProviderSpec、取消和工作线程排空验证见 `tests/test_iterator_integration.py`；接口见[用户迭代器](ITERATORS.md)。

## 按键访问智能体组件

`__getitem__` 可以按业务键向 Agent 请求一个值，也可以接收 Slice 执行批量访问；语言把访问对象、键和切片边界各求值一次。`__setitem__`／`__delitem__` 可用普通 fn 在当前任务内调用已授权 Agent，然后返回 null。点属性访问与条目协议有独立语法树，不会因为字段名恰好相同而触发智能体。 类的 `__class_getitem__` 可通过 `Role[配置]` 返回真实函数或绑定 Agent；元类条目槽优先，可实现可编程组件目录。方法、作用域、权限、冷任务与 IDE 提示沿用原语言协议，详见[类参数化](INDEXING.md#类参数化与智能体组件工厂)。

[属性协议](ATTRIBUTE_PROTOCOL.md)将 __getattribute__／__getattr__／__setattr__／__delattr__／__dir__ 接入同一实例与元类运行时；动态角色代理可以返回真实 Team 函数，默认委托保留描述符、字段契约与能力限制。IDE 对被拦截成员保守给 Any，不运行钩子猜测接口。

异步 getter 返回冷 Task，调用者显式 await；生成器 getter 返回流句柄，调用者显式消费。协议本身没有隐含重试、事务回滚或额外工具权限。完整例与实际 Registry 提供方见[索引指南](INDEXING.md)。目标、计划、电脑会话和录制技能延续这一显式值／效果边界，现有实现见下节。


## 调度、资源与长期身份

同一个执行器推进词法函数、Agent 调用和冷 Task。冷 Task 通过 await、spawn 或等待 gather 结果进入调度；同步 Agent 调用沿原 Context 执行，在活动 Task 中可以挂起。资源范围负责进入与退出；取消和预算来自原运行。群体关系是数据与函数的组合，不能自动改变角色权限。持久层保存明确的数据、版本与消息，不保存活闭包、资源句柄或暂停栈。 Plan／Goal／Skill 使用同一组真实 Mode 执行帧保存依赖扫描、并行准入、持久目标检查点及录制／重放进度，普通函数与 Agent 回调不另走应用专用执行器。冷 Mode Task 仍显式消费；Tool／Team／Bot／Robot 的权限、一次准入与资源归属不变。Goal 硬取消保留待 reconciliation 的不确定动作，不能自动重放；录制必须先排空真实后代与资源才能封存。详见[Mode 执行状态](EXECUTION_FRAMES.md#plangoalskill-执行状态)。

| 需求 | 语言机制与规范 |
| --- | --- |
| 并发任务与可靠收束 | [Task／TaskGroup](TASKS.md)、[取消](CANCELLATION.md)、[动态资源栈](RESOURCE_STACKS.md) |
| 消息、期限与选择 | [Channel](CHANNELS.md)、[等待](WAITS.md)、[原子选择](SELECT.md) |
| 惰性消费与多 Agent 组合 | [函数组合](FUNCTION_COMPOSITION.md)、[std/iter](STREAMS.md)、[std/swarm](SWARM.md) |
| 分层角色与可调用实现 | [Team 函数](TEAM_FUNCTIONS.md)、[AgentTool](FUNCTION_TOOLS.md) |
| 长期身份与跨轮任务 | [AgentSession](SESSIONS.md)、[会话工具](SESSION_TOOLS.md)、[Process](PROCESSES.md) |
| 目标、计划、录制技能 | [Plan／Goal／Skill](AGENT_MODES.md)、[模式库](MODE_AUTHORING.md) |
| 浏览器、观察与物理输入 | [Computer](COMPUTERS.md)、[机器人与 Device](ROBOT_DEVICES.md)、[树莓派](RASPBERRY_PI.md) |
| 结构化恢复与数据状态 | [情况和恢复出口](CONDITIONS.md)、[异常](EXCEPTIONS.md)、[任务局部上下文](DYNAMIC_CONTEXTS.md)、[契约](CONTRACTS.md)、[HashMap](HASH_MAPS.md)、[Set](SETS.md) |

旧 workflow 的 function 节点仍有专门的效果限制，不能把它与普通语言 `fn` 等同；源码和通用程序结构 IR 执行同一函数语言。原生 Computer 的接口使用明确的观察和行动；Device 使用一次性动作和回执。物理 HID 影响目标电脑当前焦点，浏览器虚拟光标在隔离页面中工作。

## 自编程、测试与版本采用

[Code](CODE_VALUES.md)是不可变语法值，`function_from` 在原执行器构造真实函数；[Program](SELF_PROGRAMMING.md)是封闭多文件定义。修改生成新值，当前函数与已启动运行继续使用自己的定义。自编程机器人可以生成纯策略、绑定活 Agent／函数、构造模块，然后对候选 Program 做真实语言测试。

```chaos example=agent-code-build
let c=code_from("fn(x){x*factor}");
let f=function_from(c,{factor:2});
f(21)
```

[模块构造](CODE_VALUES.md)、[库链接](PROGRAM_LINKING.md)和[符号重构](SYMBOL_REFACTORING.md)使用原解析器及作用域规则。生成函数和模块的规范源码可以进入原[调试器](DEBUGGER.md)，不会写回项目草稿或覆盖固定文件。

[协议 fixtures](AGENT_TESTING.md)替换明确的外部响应，语言工具闭包仍真实执行。测试不能把语言专属权限伪装成普通工具，也不能把纯探针的通过当作物理输入、模型质量或任意优化正确性的证明。[程序历史](PROGRAM_HISTORY.md)保留旧定义；[Process 原子演进](PROCESS_EVOLUTION.md)把测试后的版本采用、轮次状态和邮箱确认放在同一提交中。采用走版本比较，不迁移活栈、不回滚已提交的外部效果。

## 编译、数值与异构执行

普通函数可以成为 AgentTool 或 Team 的实现，再通过原编译入口组合。[编译前端](COMPILER.md)进行模块增量分析；[整数优化](SCALAR_OPTIMIZER.md)、[控制流计算区域](NATIVE_CONTROL_REGIONS.md)和[原生续接控制流](NATIVE_FLOW.md)保持原参数、作用域、预算与效果顺序。通用调用和受保护范围仍通过宿主事件恢复，编译不把工具权限移入机器码。

[GPU 数值任务](GPU_COMPUTE.md)、[Metal](GPU_METAL.md)和[局部 GPU 控制流](GPU_CONTROL_FLOW.md)处理规定的数值域；闭包、模型、外部工具和任意语言对象继续由宿主执行。精确状态可选择[Fraction／Complex](NUMBERS.md)或[Decimal](DECIMALS.md)。这不是通用 AOT、跨目标 ABI 或自研 GC。

## 当前规范与扩展边界

语法以[语言参考](LANGUAGE.md)为准，类型与对象协议分别见[属性](ATTRIBUTE_PROTOCOL.md)、[类创建](CLASS_CREATION.md)、[类型协议](TYPE_PROTOCOLS.md)和[泛型函数](GENERIC_FUNCTIONS.md)。界面、SDK、CLI 和 HTTP 是原语言的操作入口；它们不从提示词或字段名称猜测执行规则。

当前能力与 Python／Lisp 的语义及生态差异见[能力对照](PYTHON_LISP_CAPABILITIES.md)，版本兼容及实现历史集中见[路线图](LANGUAGE_ROADMAP.md)。发布以当前组合合同和声明范围的证据为准，外语差异不自动成为待完成功能清单。解释器自举、通用 continuation、活栈持久化、完整静态推导、全部外语生态和无限自优化正确性证明仍未完成。

## 多结果的函数交接

[多返回值](MULTIPLE_VALUES.md)允许普通函数同时完成答案与附加证据。Team 角色的活 Task 保留完整结果，显式 async producer 可 await 并捕获它们；AgentTool 对外输出与输出契约使用首项，发送完整结果时先捕获为 List。所有接口仍通过原调度器执行真实函数，不从文本、名称或记录字段猜测次项用途。

本轮已完成的 Task 或同步／异步 Generator 可直接交给多结果接收器读取终值。角色执行、工具效果与生成器消费都不会重放，普通回调返回冷句柄仍保留一个值。

## 原生控制提示与动态出口

[身份提示与控制范围](CONTROL_PROMPTS.md)使真实函数、AgentTool／Team 和 Code／Program 可组合提示中止、动态 escape、向外屏障及 wind 清理。控制目标按原生身份与活动逻辑状态选择，调用仍受原权限、预算、记录和生命周期限制。语言控制不会解释模型提示文本，也不创建独立应用端执行器；同步解释段可用[组合式 Continuation](CONTINUATIONS.md)，通用异步／资源控制段仍不支持。

## 原生 Robot 与普通策略函数

[Robot](NATIVE_ROBOTS.md)由可信宿主注册，普通函数、AgentTool／Team、Code／Program 与 async fn* 可组合新观察、profile 通道和一次性命令。robot.control 是默认不授予的保留语言权限；模型不能把它当作直接工具或从动作文本取得权威。OpenBot 的回执证明传输或资源关闭，targetVerified=false；没有实现 Android 视觉／导航。


## 默认执行帧与智能体函数

原生 Bot 的完整规划／执行／验证、指导采用、决策证据、提交与拥有流清理同样迁入真实类型化帧，复用原 Invocation、TaskGroup、效果准入及 Process 事务。待决提案、决策身份、结果／错误和关闭阶段由字段驱动；单个等待保留原等待协议，流关闭进入统一的迭代／流执行帧。参数、权限、宿主事件和跨轮持久化合同不变，没有提供活栈恢复、默认模型认知或可复制 continuation。详见[Bot 执行状态](EXECUTION_FRAMES.md)。

[默认求值游标](EXECUTION_FRAMES.md)驱动普通操作数、函数调用、返回、循环及 try／catch／catch*／try…else／finally、with／async with（含结构模式）的真实待决状态；保护范围的尾调用、错误分区、资源体与清理阶段保存在显式帧中。词法 PromptScope／CancelShield／TaskGroup 与组体完成沿真实帧和原调度器执行，获取／退出仍保留原租约、能力、取消与排空协议。原生 prompt／escape／barrier／wind 请求和共享回调／处理器也沿真实帧执行，保存待决函数、参数、完整结果、控制目标、权限恢复与 wind 错误上下文；词法提示与函数式提示使用同一处理器合同。Code／Program、宏生成的调用、AgentTool 与 Team 函数使用同一入口。每个逻辑 Runner／流保留自己的调度身份，Task 和外部效果不重放。资源获取、退出、生成器管理器、异步清理等待与排空、动态 Context.run、退出栈及录制终结也保存真实待决状态；同步租约记账与原生设备调用保留原边界。await／yield／yield from 及 throw／重抛的语法状态也由共享挂起帧推进；其它待决协议与提供方循环继续使用明确边界；受支持同步解释段可通过 capture_continuation 保存和组合；这些异步／资源协议仍是屏障，保存控制段不增加任何模型或设备权限。

## 运行中的 Bot 引导与公开进度

Bot 使用同一语言中的真实 Function、Agent、AgentTool、Team、Goal 和 Plan 值组合能力。模型连接沿本系统 Settings 的 baseURL／key／路由，普通函数仍直接执行语言代码；不是把全部原语自动序列化为模型工具。只有授予当前程序与调用链的能力可执行，设备、机器人和自我修改仍沿各自原生合同。

`guidance_receive(timeout=0)` 返回冷 Task。await 得到 `{received,message,closed}`；message 含 runId、messageId、revision、input、status。timeout 使用秒，0 为立即查询，null 持续等待。真正的 null 输入保存在 message.input，和没有消息不同。`progress_emit(kind,payload)` 也是冷 Task；kind 为 plan、progress 或 result，通过同一运行事件流发布公开摘要或结果。IDE 签名／字段提示来自原生 NativeSpec，不识别特定关键词或固定输出格式。

[可复用 guided_bot 函数](../examples/guided-bot.chaos)接收真正的异步规划、执行、验证函数。控制器在规划前与规划完成后读取 FIFO 引导；新消息到达时舍弃未执行的旧提案并重新规划，保留已完成的证据。可把 Agent／绑定 AgentTool／Team 适配到这些函数中，再通过普通调用组合全部已授予功能。max_rounds 与运行预算限制一次运行；跨运行长期状态仍使用 Process／Session。

用户可在运行中从工作台“追加引导”、RunHandle.guide、Framework.guide、Client.guide 或 POST `/api/runs/{id}/guidance` 提交 JSON／自然语言输入。相同 messageId 和相同内容只接收一次，不同内容冲突；queued 证明接收，received 证明程序取出，均不冒充“已应用”。当前运行最多 64 条排队、256 个消息身份；单输入 40000 字符、4096 节点；进度单条 8000 字符、2048 节点，一次运行最多 1000 条。

这是协作式决策点：不会自动中断已发出的模型请求或撤销已发生的设备操作；读取之后与下一操作之间仍可能到达新消息。任意自定义程序应显式选择读取位置，必要时增加自己的版本校验。活跃队列在内存中，服务重启后不自动重放；已有运行按 interrupted／reconciliation 处理，持久 Process 邮箱另有跨轮合同。事件保存脱敏的身份与公开摘要，不保存隐藏模型推理。追加源代码只是数据，只有程序显式调用 code_from／function_from 后才按语言语法编译，消息接口本身不执行文本。


## 原生 Bot 的长期目标与动态上下文

[原生 Bot](NATIVE_BOTS.md)是新的真实可调用值：bot 保存复制的 goal/context 和实际 planner／execute／verify 回调，调用返回冷 Task；std/bot 的四个 Bot 构造／查询导出与全局原语身份相同，另提供三个 Process 消息／事件／指导别名。BotContext／BotResult 的字段提示来自同一 NativeSpec，不按名称或返回对象猜测语义。模型仍由显式回调通过已配置 Agent／AgentTool 调用，不提供默认认知或新增权限。

原生 Bot 的决策身份沿原 Task／Team／Program／绑定 AgentTool 传播，已接受指导与实际效果准入共用宿主锁。只有执行主体未进入且规划阶段没有外部动作前缀时，旧提案才自动重新规划。规划中的工具／Program 等已开始动作也属于前缀；任何 tool:* 即使声明 effect="pure" 仍保守处理。有前缀或已进入执行主体后过期，则保留完成／不确定工作并停止 needs_reconciliation，程序检查证据后显式 bot_reconcile，不自动重放。现有合作式 guided_bot 与 guidance_receive 合同保持可用；跨运行持久目标仍使用 Process／Session，永久运行和隐藏推理直播没有因此实现。


终态提交也在宿主同一锁下校验指导 revision，并封闭当次 Bot scope；直到 scope 关闭，新的指导明确拒绝。已经接受的相同 messageId 与相同内容仍返回原幂等回执，不再入队。非终态上下文提交不封闭 scope，之后接受的新指导仍可令旧决策过期。这个短暂封闭只保护当次终态提交，不把整个运行、持久目标或已发生的效果变成可撤销事务。


## Bot 调用的原生作用域

Bot 现在可在原调度器中嵌套调用。默认根继续独占原运行指导队列；嵌套 Bot 自动隔离收件箱，独立角色可构造 `bot(..., mailbox="scoped")` 后并发组合。父子决策的完整凭据链在真实效果准入时验证，子权限不能超过父权限。作用域与指导目标是宿主真实身份，不从角色名称、提示词或进度文本推断。

SDK、HTTP、终端和工作台通过同一个可选 target 定向入口与真实活动作用域列表工作。Process 保留根检查点与子效果账目，显式声明的角色使用跨重启稳定的 processRole 地址；执行栈和决策票据不跨重启恢复。参数、全局资源上限、收到与采用的区别及完整示例见[原生 Bot](NATIVE_BOTS.md#原生-bot-的函数组合与定向指导)。


Process 的可选 roles 声明和 Bot 的 keyword-only role 将长期角色邮箱纳入语言本身：稳定角色身份、每次调用的新 scope、独立修订与消息确认共享原调度、权限及根检查点。无界面程序通过 process_status/process_guide 使用同一邮箱；网页和终端读取真实持久注册表。重启保留队列和确认账，不恢复执行帧或重放不确定效果。见[长期角色邮箱](NATIVE_BOTS.md#长期-process-的角色邮箱)。


版本化项目记忆通过 [std/memory](STDLIB.md#stdmemory版本化共享记忆) 的真实语言函数复用原工具效果、能力交集、预算、Bot准入与事件。`read`取得不可变JSON快照和版本；`compare_exchange`只在期望版本一致时原子提交。普通旧put也递增版本。冲突数据明确返回，不隐式重放更新回调；键事务与Process检查点／外部世界效果保持各自提交和协调边界。


不可变词法整数特化属于共享编译／求值核心：AgentTool、Bot回调及普通函数可复用已知let／别名算术结果，但仍实际读取名称、检查预算并按原序执行工具和模型效果。它不把智能体结果当作常量，不跨权限或效果边界重排工作；静态候选与真实执行命中分开报告，见[编译指南](COMPILER.md)。

长期 Bot 可以用 `await process_message(key,message_id)` 读取某条普通 Process 消息的原始运行结果，并将明确的 linkStatus／run.status／验证输出交给下一步函数；不需要把最新 Process 输出当作该消息答案。它是原生冷 Task，`std/process` 和 `std/bot` 保留同一函数身份；只读并不调度、重放或授予能力。邮箱 consumed 与运行 completed 分开；旧证据不足时保持 unavailable，任务是否达成仍由程序 verifier 判断。见[持久 Process](PROCESSES.md#按消息-id-读取结果)。

情况与恢复的完整控制阶段也使用[真实执行帧](EXECUTION_FRAMES.md#情况与恢复的执行状态)：signal／error、出口查询／调用、case 展开、共享回调和交互式 debugger 保留原动态身份、屏蔽、能力与完整多结果。普通函数、AgentTool、Team、Bot 和 Program 使用同一实现；词法 scope 沿原租约进入和退出。其余未迁移协议、跨异步／资源 continuation、独立 GC 与通用 AOT 仍是独立的未实现方向；同步组合式 continuation 已有明确支持范围。

迭代器、消费者、yield from、流恢复／关闭和全部推导形式也由真实执行帧驱动。智能体函数可作为同一语言协议的工厂、步骤和投影，复用实际权限、Task、效果与预算；待决来源、参数、结果及逐项词法绑定都属于正在执行的状态。图形与无界面入口共用这些状态，不依据提示词中的结束关键词判断数据流耗尽。原调度器仍协调任务与整个周期的注册表清理，完整 continuation、独立 GC、自举和通用 AOT 仍未完成。

对象运算的六段协作算法，以及属性钩子／默认委托／描述符、条目与整数索引、属性／索引增强赋值的15段对象访问算法，均保存为同一逻辑执行者上的真实类型化状态。待决目标、键、旧值与右值、回调、权限和错误决定后续执行；普通函数、AgentTool、Team、Bot、Program 与既有编译回退共用实现。原有求值次序、效果、冷句柄、检查点和取消合同不变；提供方循环等其它未迁移协议仍保留明确边界，保存／复用 continuation、独立 GC、通用 AOT、自举和完整外语生态仍未完成。详见[运算执行状态](OPERATORS.md#h-运算协议的执行状态)及[对象访问执行状态](EXECUTION_FRAMES.md#对象属性描述符与索引的执行状态)。

HashMap 的递归哈希、实例哈希、碰撞查询、持久更新、构造与相等，以及 Set 的构造、提升、成员判断、集合代数、更新和字面量求值，使用16种真实[容器执行帧](EXECUTION_FRAMES.md#持久哈希容器的执行状态)。帧保存实际草稿、游标、循环位置、共享预算、待决协议与权限恢复状态；原有调用次序、检查点、首个等价键的身份、插入时哈希、借用与取消合同保持不变。纯值计算仍是同步叶操作。这没有增加保存／复用 continuation、独立 GC 或通用 AOT 的能力。

类声明、运行期 new_class／Type 构造、基类适配、准备命名空间的读写、元类调用与类型判断，以及函数／类／Agent 装饰器，现在由同一执行游标上的21种真实类型化帧推进。待决字段保存实际命名空间、共享 Cell、成员快照、参数、类壳和构建栈租约，直接决定下一步执行；源码、IR、Code／Program、宏和智能体回调共用这些状态。原有从左到右求值、协议调用顺序、权限上限、检查点、冷 Task、错误和取消合同保持不变。函数形成与泛型方法组合也已使用[共享执行帧](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)；这没有提供保存或复用 continuation、独立 GC、通用 AOT、自举或完整 Python／Lisp 生态。 详见[类与元类执行状态](EXECUTION_FRAMES.md#类元类准备命名空间与装饰器的执行状态)。


函数形成与泛型方法组合现在由3种真实执行帧统一推进：四种函数字面量、def／rec／agent 声明的默认值和发布阶段，function_from 的环境与函数求值，以及泛型 before／primary／after 的待决调用和范围。默认值仍在定义时从左到右求值，rec 保留原共享快照、两轮绑定及失败时已发生的行为；泛型纯选择、primary next 与尾调用辅助保持原实现，冷 Task／Generator 不隐式等待。这不是自定义 CLOS 方法组合、跨资源活栈持久化、独立 GC、通用 AOT 或完整 Python／Lisp 生态的完成声明。 详见[函数形成与方法组合](EXECUTION_FRAMES.md#函数形成与泛型方法组合的执行状态)。

结构化智能体结果可直接进入 `match`、结构声明或结构赋值；四种形式的右值、守卫与主体现在由同一真实模式执行帧保存待决状态。AgentTool／Team／Bot 使用普通语言函数与同一纯模式算法，无需转换成另一套应用工作流。纯结构匹配本身不会请求模型或调用对象协议，结构正确也不证明模型答案正确；详细顺序与剩余边界见[模式执行状态](EXECUTION_FRAMES.md#h-模式分支与结构绑定的执行状态)。

封闭模块初始化、普通同步模块加载、import／export 与 callable 导出入口也沿[真实模块状态帧](EXECUTION_FRAMES.md#模块初始化导入与导出入口的执行状态)执行。每实例缓存与失败清理保持原语义，普通加载不合并原逐语句 evaluate／resolve Runner；全图解析、卫生宏、源注册准备及原调度周期仍保留自己的边界。

挂起表达式与错误转移的五种原有语法由同一个[真实挂起帧](EXECUTION_FRAMES.md#h-挂起表达式与错误转移的执行状态)推进。Agent／Team／Bot 的函数调用仍使用原 Task、能力、资源与效果准入；普通 Worker 不执行 AST，也不因恢复或关闭而重复模型请求或机器人命令。提供方循环和整个调度周期没有因此完成状态迁移。

多返回值 receiver／producer 的实际挂起、顺序、参数和完整完成也保存为两个共享执行帧，原 AgentTool 首项输出与显式捕获、Team 完整 Task 结果、Bot 执行和 Robot 准入合同不变；多值迁移没有改变原语；当前目录增加两个 continuation 全局入口，总数335，完整 NativeSpec 为381项，不增加模型或设备权限。具体行为见[多返回值](MULTIPLE_VALUES.md)，现有实现进一步将函数、原生请求及 Agent 等待统一为显式调用协调状态，保留 Bot／Robot 命令一次准入与权限合同，详见[执行帧](EXECUTION_FRAMES.md)。受支持同步解释段的[可复用控制合同](CONTINUATIONS.md)已经接入原调用入口；Mode／Goal lease、录制和资源仍不可复制。
