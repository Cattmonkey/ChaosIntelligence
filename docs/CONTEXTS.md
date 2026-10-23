# 资源作用域与智能体组件

`with` / `async with` 是语言表达式：进入一个资源上下文，在其词法作用域中执行程序，随后按规定退出。类协议、Agent 调用、生成器和 Computer 使用同一个解释器、任务调度器、能力检查和预算。

## 语法与作用域

```chaos example=context-basic
var events=[];
class Scope {
  var label:String="";
  fn __init__(self,label) { self.label=label; null }
  fn __enter__(self) { events=append(events,"enter:"+self.label); self.label }
  fn __exit__(self,error) { events=append(events,"exit:"+self.label); null }
}
let answer=with Scope("outer") as outer, Scope(outer+"/inner") as inner {
  length(inner)+31
};
[answer,events]
```

结果为 `[42,["enter:outer","enter:outer/inner","exit:outer/inner","exit:outer"]]`。管理器表达式从左到右各求值一次；后项可以读取前项的绑定，退出顺序相反。`as` 名字只在该作用域中有效；不用其结果时写 `as _`。每项绑定都引入新的词法作用域，重复名字会在内层遮蔽前项。

完整形式为 `with manager as target { body }` 和 `async with manager as target { body }`。target 可以是普通名称，或列表／记录／构造器结构模式；结构叶子在进入成功并完整匹配后成为只读局部变量，参见[资源解构](PATTERN_BINDINGS.md#资源进入结果中的结构)。多个管理器用逗号分隔，语义相当于嵌套作用域。`as` 必须显式书写，这让历史程序中的 `with`、`async` 普通标识符保持可用。`async with` 允许在顶层、`async fn` 或 `async fn*` 内使用；同步函数不能借默认参数、类体或宏展开绕过这一限制。

正常完成时，表达式返回 body 的最后一个值。退出方法的返回值不会替换这个值。body 的尾调用及 `return` 的值在退出之前完成求值。

## 用户类协议

| 作用域 | 进入 | 退出 |
| --- | --- | --- |
| `with` | `__enter__(self)` 返回绑定值 | `__exit__(self,error)` 返回 Bool 或 null |
| `async with` | `__aenter__(self)` 返回 Task，等待后绑定完成值 | `__aexit__(self,error)` 返回 Task，等待后得到 Bool 或 null |

协议通过类的 MRO 与描述符规则查找；同名实例字段不能伪造特殊方法。进入前解析两个方法、验证其可调用性并保存退出方法的绑定。进入失败时，不调用该管理器的退出方法；已经进入的外层管理器仍按普通异常规则退出。进入后修改类上的方法不改变本次已经保存的退出绑定。进入结果的结构模式失配属于进入成功后的普通错误，仍经过退出协议；可以被普通错误抑制规则处理。

同步进入方法可以返回任意语言值，包括作为资源的 Task 或流；`with` 不隐式等待它。异步协议明确要求当前解释器、当前执行周期的 Task。普通同步方法在已有 Task 内调用 Agent 时也可以通过既有效果调度挂起。

`error` 为 null，或与 `catch e` 相同结构的隔离错误记录：`{code,message,value,location}`。退出钩子只在收到普通可捕获错误时，才能通过 `true` 将错误抑制为 null。`false` 与 null 保持错误传播。

```chaos example=context-suppression
class Recover {
  fn __enter__(self) { 7 }
  fn __exit__(self,error) { error!=null && error.code=="user_throw" }
}
let result=with Recover() as value { throw value };
[result,42]
```

结果为 `[null,42]`。退出钩子抛出新的普通错误时，新错误替代原普通错误或完成值，交给外层上下文或 catch 处理。

## 控制流、挂起与取消

正常完成、普通异常、`return`、`break`、`continue` 和正常生成器关闭都会经过已进入上下文的退出方法。非异常控制转移向退出方法传 null；即使方法返回 true，也不能抑制 return、break、continue 或生成器关闭。

作用域在 `yield` 挂起期间保持进入状态；`close` / `aclose` 正常关闭挂起的生成器时执行退出。异步退出继续使用原任务调度器等待，不创建另一个解释器，不获得新能力或独立预算。

与现有 `finally` 一致，`ResourceLimit` 与宿主取消 `ExecutionControl` 属于硬控制：直接传播并跳过用户退出钩子。退出钩子自身遇到硬控制也立即停止，外层不能将它抑制。Python 宿主中断不是可传给用户退出方法的普通错误。不能把这解释为任意取消后仍保证运行语言清理代码；宿主自身的资源关闭与效果线程排空继续执行。进程被强制杀死时也没有语言清理保证。参见[挂起与任务](EFFECTS.md)和[SDK 生命周期](SDK.md)。

## 用语言标准库管理流和自定义资源

`std/context` 由普通 Chaos 类与函数实现，生成器适配器调用解释器内置的资源生命周期桥接：

| 函数 | 用法 |
| --- | --- |
| `contextmanager(factory)` | 将 `fn*` 工厂变为可重复调用的资源工厂，每次调用返回新的单次同步管理器 |
| `asynccontextmanager(factory)` | 将 `async fn*` 工厂变为异步资源工厂，每次调用返回新的单次异步管理器 |
| `closing(value, closer=close)` | `with` 进入时返回 value，退出时调用 closer(value) 并返回 null |
| `aclosing(value, closer=aclose)` | `async with` 进入时返回 value，退出时等待 closer(value) 的 Task 并返回 null |

同步 `closing` 拒绝关闭函数返回的 Task、Generator 或 AsyncGenerator，防止把尚未执行的冷计算当成清理成功；普通已计算返回值可以忽略。`aclosing` 等待关闭函数返回的一层 Task，并同样拒绝其完成值仍是冷计算的情况；它不自动反复 await 或消费流。

默认 closer 覆盖现有 Generator/Iterator 或异步版本。任意自定义资源需要显式传入关闭函数；不会根据资源名、提示词或对象字段猜测关闭策略。Task 本身没有自动 cancel/join 语义。

```chaos example=context-closing
import "std/context" as Context;
var closed=false;
fn* numbers() { try { yield 1; yield 2 } finally { closed=true } }
let first=with Context.closing(numbers()) as stream { next(stream).value };
[first,closed]
```

结果 `[1,true]`。即使只消费一个元素，普通作用域退出也会关闭已启动的生成器。

```chaos example=context-aclosing
import "std/context" as Context;
var closed=false;
async fn* numbers() { try { yield 42; yield 43 } finally { closed=true } }
let first=async with Context.aclosing(numbers()) as stream {
  (await anext(stream)).value
};
[first,closed]
```

结果 `[42,true]`。自定义异步关闭函数必须返回 Task，例如 `Context.aclosing(browser, computer_close)`。

## Computer 与长期智能体

Computer 原生支持异步作用域，绑定结果为该 Computer 本身：

```chaos
async with await computer_open(input.url) as browser {
  let before=await computer_observe(browser);
  observation_info(before)
}
```

computer_open 的临时资源退出复用 `computer_close`；computer_connect 的持久资源退出调用 `computer_release`，保留同一服务中的页面。两者共用冷 Task、身份／执行周期验证和宿主路径；同一 Computer 同时只能被一个原生作用域持有。同步 `with` 不会隐式执行异步电脑操作。权限、观察身份、行动一次性检查与资源忙锁保持原有规则。硬取消跳过语言退出时，运行结束的宿主清理负责关闭 driver 并归还租约；持久会话保留可恢复的 cookies/localStorage，硬取消不承诺保留 DOM。

AgentSession 是跨轮持久业务状态；离开词法作用域不会自动 `session_close`。用户可以用普通类在进入或退出方法中调用 Agent／Session，或把函数式角色传入管理器，实现自己的验收、提交或记录策略。所有调用仍使用原有 Context 和能力交集；作用域不提供额外权限，也不会撤销已经发生的外部效果。

[观察 dot](../examples/observed-dot/dot.chaos)使用 `async with` 管理每轮浏览器；Program 自编程、AgentSession 的持续上下文和 Process 的跨轮状态各自保持原语义。

## 无界面、结构编辑与宏

AST / IR 增加 `with` 和 `async_with`，字段为 `name`、`manager`、`body`，body 是块。多项头部解析为嵌套节点；结构打印可能展开为嵌套形式。源码保真格式化保留原始标记与注释。已有 schema、补丁、投影、SDK、HTTP 与 CLI 使用同一校验器。

宏模板为 `as` 绑定生成卫生名字，管理器在外层环境中解析，body 使用新绑定；宏展开后再次检查 async 和控制流上下文。调试断点与局部值来自真实 AST/词法环境，不另造可视化执行语义。IDE 基于真实声明提供协议和资源变量的保守提示；无法静态确定进入值时显示 Any。

本接口参考 [Python with 语义](https://docs.python.org/3/reference/compound_stmts.html#the-with-statement)与 [contextlib 的资源管理](https://docs.python.org/3/library/contextlib.html)，采用 Chaos 自身的错误记录、严格 Bool/null、词法块和硬控制规则；它不导入 Python 上下文对象。0.41 增加下述生成器资源工厂；0.42 已增加资源栈；0.49 提供显式[词法取消屏蔽](CANCELLATION.md)；尚无跨重启资源租约或通用事务回滚。


## 原生任务组的资源边界

`async with task_group() as workers { ... }` 使用原生 TaskGroup 协议，不调用用户类的进入／退出钩子。组体与成员共用现有解释器；退出前等待成员，失败时取消并排空。普通成员失败会展开组体的 finally 和资源退出；真正硬控制继续跳过任意用户清理代码。原生调度收尾始终执行。TaskGroup 不能跨自身向外 yield，组内消费生成器不受此限制。详见[任务组规范](TASKS.md)。

任务组打断异步资源协议时，已存在的返回 Task 只被借用，不会因此取消；本次协议调用新建的私有 Task 则先停止并实际排空。新建的协议 gather 若混有借用成员，在启动前拒绝，避免通过 collector 取消外部任务。同组收尾取消经 finally 中的 await 转发后仍保留其内部来源，不能掩盖最初的普通失败；显式 cancel 或混合外部取消来源仍不可捕获。


## 用函数定义资源：contextmanager

资源工厂仍是普通语言函数：参数绑定、解构、默认参数、关键字参数、词法闭包、宏与效果调用沿用原有规则。装饰器不是按函数名识别的关键字；导入后可以取别名或作为值传递。

```chaos example=context-generator-factory
import "std/context" as Context;
var events=[];
@Context.contextmanager
fn* resource(label:String="worker") {
  events=append(events,"enter:"+label);
  try { yield {label:label,answer:42} }
  finally { events=append(events,"exit:"+label) }
}
let answer=with resource(label="planner") as item { item.answer };
[answer,events]
```

结果 `[42,["enter:planner","exit:planner"]]`。调用 `resource(...)` 只绑定参数并创建冷管理器；`with` 才执行到第一个 `yield`，将其值绑定给 `as` 名字。退出时从这个位置继续。每个管理器只能进入一次；重复调用工厂会创建独立管理器。原生桥接 `generator_context(generator, asynchronous=false)` 只接收当前执行周期尚未启动且未被其他管理器接管的对应生成器；接管后不能从生成器别名绕开资源作用域继续消费。

生成器必须恰好 yield 一次。进入时没有 yield、退出时再次 yield、同步／异步种类不匹配、重复进入及跨解释器／执行周期使用均为错误。生成器的最终返回值不替换作用域结果；`fn*` 的返回类型契约仍约束最终返回值，不是 yield 的值。

```chaos example=context-generator-suppression
import "std/context" as Context;
var errorCode=null;
@Context.contextmanager
fn* recover() {
  try { yield 42 }
  catch error { errorCode=error.code }
}
let result=with recover() as value { 1/0 };
[result,errorCode]
```

结果 `[null,"ZeroDivisionError"]`。普通 body 错误以原有内部异常注入暂停的 yield；生成器处理后结束可抑制错误，未处理时保留原始错误及位置，清理代码抛出的新错误则替代它。语言内的 `catch` 仍得到隔离记录；`throw error` 仍是抛出记录值的新用户错误，不是透明重抛。`finally` 可执行清理并保留原错误。

```chaos example=context-async-generator-factory
import "std/context" as Context;
var events=[];
async fn mark(value) { events=append(events,value); null }
@Context.asynccontextmanager
async fn* resource(value:Int=42) {
  await mark("enter");
  try { yield value }
  finally { await mark("exit") }
}
let result=async with resource() as value { value };
[result,events]
```

结果 `[42,["enter","exit"]]`。异步进入和清理仍在同一调度器内等待。工厂可以在 yield 前调用 Agent 获取资源，在 finally 中调用有权限的工具；也可以在内部用 `async with await computer_open(...)` 管理真实 Computer，再 yield 给调用方。它不提升调用者的能力、预算或模型权限。

普通 return、break、continue、生成器关闭经过清理，不能被工厂抑制；任务组的普通失败也不能被包装器吞掉。硬控制遵守前文规则，跳过用户清理。TaskGroup 仍不能跨自身向外 yield，不能用该装饰器把组作用域拆成两个独立阶段。本版本的管理器值用于 with／async with，不实现 Python ContextDecorator 的“管理器实例再次装饰任意函数”接口。

编辑器、LSP 与 SDK 基于同一 AST 保留资源工厂参数提示；能证明的 yield 值为 as 变量提供成员信息，不确定时使用 Any。格式化保留装饰器、注释、字符串与程序语义。底层程序仍可无界面运行，资源生命周期不依赖 IDE。


0.42 已提供[动态资源栈](RESOURCE_STACKS.md)，可按数据组织同步／异步资源，登记退出函数和转移清理责任；保留原硬控制及录制边界。

## 取消交付与资源清理

收到硬控制后跳过用户退出钩子的规则保持有效。0.49 允许在取消交付之前主动进入 `CancelShield`，将需要完成的资源作用域放在其内部，从而在局部取消尚待交付时正常退出资源。它不能撤销已经交付的控制，也不能延迟运行级停止或预算耗尽，详见[词法取消屏蔽](CANCELLATION.md)。


## 协议的执行状态

资源获取、进入／退出、生成器上下文推进，以及异步协议操作的等待与取消排空使用[真实执行帧](EXECUTION_FRAMES.md#资源协议与动态上下文的执行状态)。帧保留管理器、已保存的退出绑定、待决操作及错误；暂停后继续使用这些值，不重放已经完成的进入或工具调用。直接资源协议与普通函数、AgentTool、Team、Bot 调用使用同一调度器和权限合同。同步租约管理与原生设备动作保留原边界；这不提供恢复已关闭资源或复制活动资源租约的能力。


## 资源结构实现：资源范围的结构 CFG

资源结构实现让 auto-flow／native-flow 在普通、异步、生成器及异步生成器函数中，递归编译直接选中的 with、async_with、with_pattern 与 async_with_pattern。原语法适用范围不扩大：普通函数不因此接受原本非法的 async with。后端返回的真实后继选择 manager 与 body 子树；原 ContextFrame／ProtectedValueFrame 继续负责进入、绑定、完整结果、尾调用与退出。获取失败不退出，成功获取的资源只退出一次，多资源逆序退出，模式失败先沿原资源合同清理；只有原可抑制 LanguageError 允许 true 退出值抑制。return／break／continue、NonlocalTransfer 与 GeneratorClose 不被 true 吞掉；ResourceLimit／ExecutionControl／原生 GeneratorExit 保持原硬控制策略。

PromptScope 与 CancelShield 保持原词法释放、处理器范围及延迟取消优先级。TaskGroup 仍由原 GroupRun 创建独立组体 Task／ExecutionCursor，组体使用自己的实际 FlowState 与捕获根；父状态不重新绑定到子游标。清理与取消只处理原合同拥有的操作，不把借用任务或流变成新所有物。活动定义／状态损坏只按原规则展开已经取得的范围，不恢复无效 PC，不重放工具或 Robot 动作。复用原算法帧，compile_info 仍12字段、compile_flow_info 仍11字段。

这是当前编译覆盖，语言资源功能此前已经存在。源码实现、真实测试、安装与分发分别验收；具体版本仍独立验收，不承诺性能加速、物理设备达成、全机器码、可复用 continuation、独立 GC、通用 AOT／跨架构或完整 Python／Lisp 生态。宿主模型／工具／Robot 准入与整个调度周期仍保留明确边界。
