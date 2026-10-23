# 生成器、异步任务与智能体效果

这些是解释器的语言能力。函数、智能体和工具仍经过相同的调用、能力与预算检查；生成器和任务保存执行状态，不把提示词中的文本当作语法。

## 生成器

`fn*` 声明生成器函数，调用后得到 `Generator`。函数主体直到消费时才运行，每次 `yield` 暂停并保存局部绑定及执行位置；后续恢复接着执行。`yield from` 委托另一个生成器或受支持的序列。

```chaos example=effects-generator
fn* count(n) {
  var i = 0;
  while i < n {
    yield i;
    i = i + 1;
  }
}
collect(count(4))
```

`next(stream)` 返回 `{done, value}`，`collect(stream)` 在有界预算内收集值。普通 `for` 可以逐项消费生成器。显式 `close(stream)` 关闭暂停的生成器；关闭过程的清理语义与限制见本页运行时边界。生成器不是已物化的列表，不应交给仅接受 List 的函数；需要列表时显式 collect。

## 异步函数与任务

`async fn` 声明异步函数，调用产生 `Task`；其返回注解约束完成值。`await` 等待任务结果并传播错误，允许模块顶层使用。`spawn` 调度任务，`gather` 按输入顺序收集多个任务的完成值。

```chaos example=effects-async
async fn twice(x: Int) -> Int {
  x * 2
}
await gather([twice(20), twice(21)])
```

异步不代表纯 CPU 算术自动使用多个核心。语言 AST 和词法环境由同一线程推进，暂停点把控制权交回调度器；可能阻塞的智能体和工具由受控效果工作线程处理。它们拿到复制后的数据及宿主回调，不能在后台线程直接修改语言局部环境。已有运行会继续使用启动时的源码、配置权限及预算。

`tool(name, input)` 和直接声明的 Agent 调用在当前 Task 内经调度器挂起，恢复后返回结果值。调用时使用 `tool("name", input)` 或 `agent(input)`；`await` 接收真正的 Task，例如 `async fn`、`guidance_receive`、`progress_emit`、绑定 AgentTool／Team 和异步资源操作返回的冷任务。规划器可以写成 `async fn planner(context) { agent(context) }`，调用控制器时等待 `planner(context)` 的 Task。函数提示中的返回类型帮助区分结果值与 Task。

## 与语法宏组合

```chaos example=effects-macro-task
macro identity(x) {
  quote { unquote(x) }
}
async fn answer() {
  identity!(42)
}
await answer()
```

宏发生在程序运行之前；Task 发生在运行期间。宏可以生成异步语法，但不能在展开期间调用智能体或工具。更多卫生和阶段规则见[语法宏](MACROS.md)。

## 运行时边界

Task、Generator、AsyncGenerator、Iterator 和 AsyncIterator 是当前执行上下文的运行时句柄，不是可通过 JSON、项目记忆或图边传输的活动执行对象。外部接口接收完成结果或有界描述，不提供暂停栈持久化与跨进程恢复。

执行仍有步数、时间、深度和集合大小限制；有限运行时不承诺无限等待。宿主扩展需要合作检查取消，无法通过 Python 线程强制杀死任意第三方回调。

调用 async 函数会先检查和绑定实参，主体等到 `spawn` 或 `await` 才开始。默认参数依旧在定义函数时求值。`gather` 先完整预检再启动成员，按输入顺序返回；任何成员失败都会及时请求取消剩余成员，并在实际宿主效果排空后传播错误。硬控制优先于普通失败，取消来源不会被混淆。已启动而未被等待、未被 gather 接管的失败任务会使本次运行失败，不会静默丢掉异常。

`cancel(task)` 请求单任务取消，不取消其它任务。已交付的任务取消、全局取消与资源限制属于不可捕获的执行控制信号，跳过语言 `catch/finally`；宿主资源释放由运行时负责。普通语言错误仍进入 `catch/finally`。运行退出时取消未完成任务、关闭生成器并等待效果线程退出，因此后台效果不会悄悄越过已完成运行的生命周期。非合作宿主回调可能延迟这个退出过程。

普通 `close(handle)` 和正常运行结束时会执行已启动生成器的 `finally`；尚未启动的生成器不执行主体。关闭期间再次 `yield` 会报错并关闭句柄。`for` 的 `break` 保留流供继续消费；不再需要时可显式 close。`fn*` 返回注解约束最终完成值，不约束每次 yield；最后一次 `next` 的 `done` 为 true，`value` 为完成值，`collect` 只收集产出的元素。

活动句柄不能跨独立运行或 REPL 提交周期恢复；每次提交结束都会回收本周期挂起活动。普通函数定义可在 Session 保留，宏声明只作用于当前模块提交。任务生命周期以 `program.task.*` 进入运行事件；生成器和表达式细节由有界 trace 提供。

为保留旧的同名函数调用，紧邻的 `await(task)` 是普通函数调用，`await task` 和 `await (task)` 才是等待语法。本版不支持任意第三方 asyncio 库或跨进程恢复活动执行栈。异步生成器和双向通信见下文，模块宏见[宏规范](MACROS.md)。


## 双向生成器

`yield` 是表达式：产出的值交给消费者，下次 `send(handle, value)` 的 value 成为该表达式的结果。`next(handle)` 等价于发送 null。`throw_into(handle, value)` 在当前暂停点抛出语言异常，生成器内可以用 catch 处理；它不接受宿主 Python 异常类，也不表示取消。

```chaos example=streams-send-throw
fn* dialog() {
  let first = yield "ready";
  let replacement = try { yield first } catch error { error.value };
  return replacement
}
let stream = dialog();
[next(stream), send(stream, 40), throw_into(stream, 42)]
```

结果是 ready、40 两次产出，随后以 42 完成。未启动的生成器通过 `send` / `asend` 接收的首个值只能是 `null`；发送其它值会拒绝该操作且不启动主体。已结束流的 next/send 返回完成记录，throw_into 仍向调用者抛出注入的值。所有操作沿用 `{done, value}` 结果形状，避免用某个特殊业务值表示结束。

`yield from` 把输入、异常和关闭转交给被委托的生成器，并取得其完成值。委托 List/String 时只能继续消费普通序列，不具备处理发送值或注入异常的协程主体。关闭不能重新产出元素；清理错误仍会向调用者传播。

## 异步生成器与背压

`async fn*` 创建 AsyncGenerator，主体可以同时使用 await 和 yield。调用本身只创建句柄；`anext`、`asend`、`athrow`、`aclose`、`acollect` 创建冷 Task，只有调度或等待这些 Task 时才开始对应操作。消费者每请求一项，生产者才继续到下一个 yield，因此不会自行拉取全部输入或无限堆积结果。

```chaos example=streams-async-for
async fn score(x) { x + 1 }
async fn* scores(inputs) {
  for value in inputs { yield await score(value) }
}
var total = 0;
async for answer in scores([40, 42]) { total = total + answer }
total
```

结果为 84。`async for` 接收 AsyncGenerator、AsyncIterator 或定义了异步迭代工厂的对象，可出现在模块顶层、async fn 或 async fn* 中；普通 fn 不能隐式挂起。break 保留流供继续使用，离开后可用 `await aclose(stream)` 显式关闭。

```chaos example=streams-asend
async fn* dialog() {
  let feedback = yield "ready";
  yield feedback + 1;
  return 0
}
let stream = dialog();
[await anext(stream), await asend(stream, 41), await anext(stream)]
```

AsyncGenerator 的返回注解检查最终完成值，允许 return 携带值，这是 Chaos 的明确扩展，不声称逐项兼容 Python 异步生成器协议。anext/asend/athrow 的任务完成值为 `{done, value}`，aclose 为 null，acollect 为产出值组成的 List。

同一个流在一次恢复操作尚未完成时拒绝重叠消费或关闭。可以对不同流并发 `gather`，而不是让多个消费者同时推进同一个词法栈。控制信号、共享预算、所在执行周期与 Task 相同；取消不回滚已经完成的智能体或工具效果。

异步生成器中的 `yield from` 支持同步或异步流，并委托双向通信与关闭；同步生成器不能委托 AsyncGenerator。这是本语言的组合能力，Python 本身不提供 async yield from。

## 智能体作为流内的函数

智能体仍是普通的 Function 契约值。异步生成器可以接受任意智能体或普通函数，把输入逐项传入；其工具动作仍走 Context 的能力检查、预算和事件记录，宏不能扩大权限。

```chaos example=streams-callable-pipeline
async fn* pipeline(worker: Function, inputs: List) {
  for value in inputs { yield worker(value) }
}
await acollect(pipeline(fn(value) { value * 2 }, [20, 21]))
```

结果为 `[40, 42]`。把 worker 换成已授权 Agent 时，阻塞的提供方和工具调用进入效果工作线程；AST、局部绑定和流恢复仍由解释器线程推进。调试器在真实 await/yield 位置暂停，显示当前任务和局部值，编辑器与无界面运行使用同一个程序版本。

## 对象方法与挂起

`async fn`、`fn*`、`async fn*` 方法与独立函数创建相同种类的 Task/Generator/AsyncGenerator。绑定方法保存接收者，取出后可交给高阶函数；恢复时仍使用原来的实例状态及当前操作 Task。`__call__` 方法同样支持这些类型，调用实例可以得到冷 Task 或流句柄。

`__init__` 必须是同步普通函数并返回 null；需要等待外部效果的构造流程用显式 `async fn` 工厂表达。正常关闭、异常、取消与预算规则不因方法绑定而改变。类不能使句柄跨运行周期使用。

## 装饰器与属性的挂起语义

装饰器调用与普通函数调用一致：异步装饰器返回 Task，不会为了“得到函数”自动等待；如果希望异步生成装饰器，可先显式 await 工厂并绑定它，再使用 @。装饰器表达式的 await 仍遵守其声明所在的语法上下文。

由 `property` 装饰的 `async fn value(self) { ... }` 在读取时产生冷 Task，使用 `await object.value` 等待。同步 getter/setter 可以在已有 Task 中调用 Agent，其效果仍由原调度器处理。setter 必须同步完成为 null，显式 async/生成器 setter 会被拒绝，隐藏的 Task/Generator 结果也不能当作成功赋值。普通属性读写不会隐式 await。

## 用户描述符中的效果

`__get__` 可为普通函数、async 函数或生成器；读取异步 getter 仅产生冷 Task，读取流 getter 仅产生流句柄。`__set__`、`__delete__`、`__set_name__` 和 property deleter 必须同步完成为 null；普通 fn 内部调用 Agent 仍可以在当前 Task 中挂起，并共享取消与预算。`delattr` 不另建线程或偷偷 await。

命名通知在类公开前执行。取消或失败停止后续通知，但不回滚已经发生的外部效果、已修改的描述符状态或已泄露的 owner 引用。取消后的合作式效果线程排空规则与其它 Agent 调用一致。

## 可挂起的运算协议

普通 `__add__` 等协议内部可调用 Agent，并在当前 Task 中让出执行权；权限、预算、取消及合作式工作线程排空与普通 Agent 调用一致。async 算术协议只产生冷 Task，必须显式 await；生成器算术协议返回流句柄，必须显式消费。比较、length、contains、bool 不隐式等待 Task，其返回契约直接拒绝错误类型。

## 用户迭代器与生成器互操作

用户数据源可以让 `fn* __iter__` 返回 Generator，或让 `async fn* __aiter__` 返回 AsyncGenerator；工厂按返回值校验，Task 不会被隐式等待。自有 `__next__` 返回 done/value 记录，`__anext__` 返回 Task，由异步消费协议等待并验证结果。

for / async for 提前退出继续保留游标；yield from 可以委托用户迭代器，并取得最终完成值。可选 send/throw/close 协议承接双向交互；缺失能力不会被推测。用户适配器的关闭与生成器 finally 使用各自明确的生命周期规则，见[用户迭代器](ITERATORS.md)。

索引 getter 与普通函数拥有同一挂起模型：同步方法中的 Agent 效果可等待工作线程；async fn 方法返回 Task。写删协议要求同步 Null，拒绝把未等待的 Task 当作写入完成；已发生的外部效果不回滚。见[对象索引](INDEXING.md)。

## 资源上下文与挂起

with/async with 的作用域保持到 body 完成；yield 暂停不触发退出，正常 close/aclose 会沿暂停栈退出。std/context.closing/aclosing 用普通类组合流关闭。作用域不会额外授予预算，也不会把不可捕获取消变成普通错误。完整语义见[资源作用域](CONTEXTS.md)。

词法 TaskGroup、失败打断组体、任务只读反射、成员预检、硬错误仲裁和调试可视化见[并发任务](TASKS.md)。

局部取消可由显式[CancelShield](CANCELLATION.md)延迟到最外层词法范围退出后交付；这不延迟全局取消或预算耗尽。

## 异步正常完成分支

async for 支持耗尽 else，else 内可以继续 await/yield；break、当前执行生成器被关闭、取消、推进错误和死锁均不触发正常完成；来源正常报告 done 仍表示耗尽，包括 Channel 关闭后排空。try/catch else 只在保护体正常结束时运行；创建冷 Task 不隐式 await。成功分支的效果与错误由当前执行上下文处理，外层 finally 仍覆盖它，见[完成分支](CONTROL_COMPLETION.md)。

## 活动错误与挂起

catch/finally 中的动态重抛上下文跨 await/yield 保存。独立冷 Task 和生成器不捕获创建者／调用者的错误；同步 helper 和 TaskGroup 词法主体继承当前范围。并发角色的错误不会互相覆盖，取消及硬错误保持既有优先级。见[异常并发语义](EXCEPTIONS.md)。

## 运行中的 Bot 引导与公开进度

Bot 使用同一语言中的真实 Function、Agent、AgentTool、Team、Goal 和 Plan 值组合能力。模型连接沿本系统 Settings 的 baseURL／key／路由，普通函数仍直接执行语言代码；不是把全部原语自动序列化为模型工具。只有授予当前程序与调用链的能力可执行，设备、机器人和自我修改仍沿各自原生合同。

`guidance_receive(timeout=0)` 返回冷 Task。await 得到 `{received,message,closed}`；message 含 runId、messageId、revision、input、status。timeout 使用秒，0 为立即查询，null 持续等待。真正的 null 输入保存在 message.input，和没有消息不同。`progress_emit(kind,payload)` 也是冷 Task；kind 为 plan、progress 或 result，通过同一运行事件流发布公开摘要或结果。IDE 签名／字段提示来自原生 NativeSpec，不识别特定关键词或固定输出格式。

[可复用 guided_bot 函数](../examples/guided-bot.chaos)接收真正的异步规划、执行、验证函数。控制器在规划前与规划完成后读取 FIFO 引导；新消息到达时舍弃未执行的旧提案并重新规划，保留已完成的证据。可把 Agent／绑定 AgentTool／Team 适配到这些函数中，再通过普通调用组合全部已授予功能。max_rounds 与运行预算限制一次运行；跨运行长期状态仍使用 Process／Session。

用户可在运行中从工作台“追加引导”、RunHandle.guide、Framework.guide、Client.guide 或 POST `/api/runs/{id}/guidance` 提交 JSON／自然语言输入。相同 messageId 和相同内容只接收一次，不同内容冲突；queued 证明接收，received 证明程序取出，均不冒充“已应用”。当前运行最多 64 条排队、256 个消息身份；单输入 40000 字符、4096 节点；进度单条 8000 字符、2048 节点，一次运行最多 1000 条。

这是协作式决策点：不会自动中断已发出的模型请求或撤销已发生的设备操作；读取之后与下一操作之间仍可能到达新消息。任意自定义程序应显式选择读取位置，必要时增加自己的版本校验。活跃队列在内存中，服务重启后不自动重放；已有运行按 interrupted／reconciliation 处理，持久 Process 邮箱另有跨轮合同。事件保存脱敏的身份与公开摘要，不保存隐藏模型推理。追加源代码只是数据，只有程序显式调用 code_from／function_from 后才按语言语法编译，消息接口本身不执行文本。


## 原生 Bot 的长期目标与动态上下文

[原生 Bot](NATIVE_BOTS.md)是新的真实可调用值：bot 保存复制的 goal/context 和实际 planner／execute／verify 回调，调用返回冷 Task；std/bot 四个导出与全局原语身份相同。BotContext／BotResult 的字段提示来自同一 NativeSpec，不按名称或返回对象猜测语义。模型仍由显式回调通过已配置 Agent／AgentTool 调用，不提供默认认知或新增权限。

原生 Bot 的决策身份沿原 Task／Team／Program／绑定 AgentTool 传播，已接受指导与实际效果准入共用宿主锁。只有执行主体未进入且规划阶段没有外部动作前缀时，旧提案才自动重新规划。规划中的工具／Program 等已开始动作也属于前缀；任何 tool:* 即使声明 effect="pure" 仍保守处理。有前缀或已进入执行主体后过期，则保留完成／不确定工作并停止 needs_reconciliation，程序检查证据后显式 bot_reconcile，不自动重放。现有合作式 guided_bot 与 guidance_receive 合同保持可用；跨运行持久目标仍使用 Process／Session，永久运行和隐藏推理直播没有因此实现。


终态提交也在宿主同一锁下校验指导 revision，并封闭当次 Bot scope；直到 scope 关闭，新的指导明确拒绝。已经接受的相同 messageId 与相同内容仍返回原幂等回执，不再入队。非终态上下文提交不封闭 scope，之后接受的新指导仍可令旧决策过期。这个短暂封闭只保护当次终态提交，不把整个运行、持久目标或已发生的效果变成可撤销事务。

流恢复、关闭、yield from 与迭代／推导协议由同一逻辑执行者上的真实帧保存待决状态；发送值、注入错误、当前 Runner 和权限恢复参与原拥有权与能力校验。原调度器仍负责任务协调、硬中止和执行周期注册表清理，每次实际流恢复／关闭进入该实现；没有重放已完成效果、复制宿主生成器或恢复已关闭资源。详见[统一执行帧](EXECUTION_FRAMES.md#迭代消费委托与推导的执行状态)。


## 资源协议与动态上下文

资源获取、生成器上下文推进、退出栈清理、协议 Task 的等待与取消排空，以及 Context.run 的安装和恢复由同一逻辑执行者上的[真实资源执行帧](EXECUTION_FRAMES.md#资源协议与动态上下文的执行状态)驱动。帧保留当前操作、拥有关系、完整结果和错误，供实际继续执行与捕获图预算检查；不会把借用 Task 改为私有，也不会因暂停重放已完成的工具效果。同步租约记账、原生设备效果、硬中止和调度器全周期协调继续保留原边界。
