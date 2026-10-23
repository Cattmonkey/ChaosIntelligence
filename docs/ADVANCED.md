# 进阶编程：组合、状态、模块与预算

本页在[语言参考](LANGUAGE.md)基础上介绍 language core v1 的程序设计方式。示例都是确定性的本地计算；可执行代码块与预期结果登记在 `examples.json`，用于随解释器一起回归验证。函数签名查[标准库](STDLIB.md)，宿主工具及提供方扩展查[SDK](SDK.md)。

## 用纯函数组织数据转换

把输入数据、转换步骤和外部效果分开，最容易复用和验证计算。`map`、`filter`、`fold` 都按列表顺序运行；`filter` 谓词必须返回 Bool，`fold` 回调的参数顺序是累积值、当前元素。

```chaos example=advanced-pipeline
let orders = [
  {name: "book", price: 12, count: 2},
  {name: "pen", price: 3, count: 0},
  {name: "lamp", price: 20, count: 1}
];
orders
  |> partial(filter, fn(order) { order.count > 0 })
  |> partial(map, fn(order) { order.price * order.count })
  |> sum
```

结果是 `44`。`partial(f, value...)` 绑定函数最左侧位置参数；由于 `map(fn, xs)` 的列表在最后，特别适合管道。`append(xs, x)` 的列表在最前，应写 `xs |> fn(items) { append(items, item) }`。`partial` 也可绑定具名参数，见下文。

```chaos example=advanced-compose
fn increment(x) { x + 1 }
fn double(x) { x * 2 }
let transform = compose(increment, double);
[transform(3), partial(fn(a, b, c) { a + b + c }, 10, 20)(3)]
```

结果是 `[7, 33]`。`compose(f, g)` 先把调用时全部位置和具名参数交给 `g`，再把它的单个结果交给 `f`；`g` 可以有多个参数，`f` 仍收到一个位置值。构造组合时恰好接收两个可调用值，更多步骤可显式嵌套组合。

```chaos example=advanced-compose-arguments
fn doubled(x: Int) -> Int { x * 2 }
fn add(a: Int, b: Int = 1) -> Int { a + b }
fn countDown(n: Int, total: Int = 0) -> Int {
  if n == 0 { total } else { countDown(n - 1, total + 1) }
}
let transform = compose(doubled, add);
[transform(20, b=1), compose(fn(x) { x + 1 }, countDown)(150)]
```

结果是 `[42, 151]`。组合、部分应用与 `apply` 使用同一套参数绑定和尾调用机制；目标函数的契约仍检查，错误位置保留原调用来源。包装器不会把内部尾调用描述当成普通结果交给后续函数。跨 `try/finally` 清理范围仍有额外调用深度限制，见[递归与有限执行预算](#递归与有限执行预算)。

高阶函数也能接收智能体，但那会产生效果并消耗同一运行预算；不要把这种计算当成可随意重放的纯函数。

## 把配置与调用参数分开

用默认值表达稳定的省略行为，用仅关键字参数表达阅读时需要名称的选项。用 `partial` 创建带配置的可调用值，之后调用时同名具名参数覆盖先前绑定值；位置参数按先绑定、后调用的顺序连接。覆盖规则不消除位置与名称同时填充同一槽位的错误。

```chaos example=advanced-apply-partial
fn total(a: Int, b: Int = 0, *, factor: Int = 1) { (a + b) * factor }
let configured = partial(total, 10, b=2, factor=3);
[apply(total, [10, 2], {factor: 3}), configured(), configured(b=4, factor=2)]
```

结果是 `[36, 36, 28]`。`apply` 适合从列表和记录中组装调用：第二个实参必须为 List，第三个实参省略或传入 `null` 时使用空 Record，其他值必须为 Record。它不会把字符串当作可展开序列。

`partial(total, 10, a=20)` 可以构造部分应用，但调用它时仍会因为 `a` 同时由位置与名称提供而失败；绑定校验不会让目标函数体先执行。`partial` 的目标函数必须作为第一个位置实参传入，这使 `partial(fnTarget, fn=value)` 可以将名为 `fn` 的选项交给目标函数。

默认表达式在定义时求值一次，适合常量配置；若需要每次调用读取最新状态，写成函数体中的显式计算。`*rest` 与 `**options` 仅负责收集，不会替业务校验选项名称和值。

## 使用记录表达不可变更新

原生容器不提供成员或条目原位修改；List／String 的 `+=` 返回新连接值并赋回变量。保留旧值并产生新值使输入、输出及中间状态容易比对。更新嵌套记录时逐层 `set`；`merge` 只合并一层，重复字段以右侧为准。

```chaos example=advanced-records
let original = {profile: {name: "Ada", score: 2}, enabled: true};
let updated = set(original, "profile", set(original.profile, "score", 3));
[original.profile.score, updated.profile.score,
 merge({x: 1, nested: {a: 1}}, {nested: {b: 2}})]
```

结果是 `[2, 3, {"x": 1, "nested": {"b": 2}}]`。这不是持久化数据库事务；它只是语言内值的更新方式。JSON 输入与工具边界会复制容器，避免宿主回调意外修改原语言值。

## 显式状态与闭包

需要计数、缓存或累积状态时使用 `var`，通过闭包限定访问范围。函数捕获已有 `var` 的共享单元，而已有 `let` 绑定保持创建时的值。

```chaos example=advanced-counter
fn makeCounter(start: Int) {
  var value: Int = start;
  {
    next: fn() { value = value + 1; value },
    read: fn() { value }
  }
}
let a = makeCounter(10);
let b = makeCounter(0);
[a.next(), a.next(), b.next(), a.read()]
```

结果是 `[11, 12, 1, 12]`。每次工厂调用创建独立状态；同一个返回记录里的闭包共享该次调用的单元。`let a` 固定的是记录绑定，其内部闭包仍可更新捕获的 `var`。不要把 `let` 等同于“包含的所有函数都无副作用”。

```chaos example=advanced-loop-capture
var callbacks = [];
for item in [10, 20, 30] {
  callbacks = append(callbacks, fn() { item });
}
map(fn(f) { f() }, callbacks)
```

结果是 `[10, 20, 30]`。每轮 `for` 都建立新绑定，闭包不会全部读到最后一轮的值。会话和完整运行的生命周期也不同：REPL 的 `Session` 保留状态；独立 `Framework.evaluate` 调用从头初始化程序。

## 用 Option 和 Result 表达预期分支

“数据不存在”与“操作失败”可以是正常返回值，用显式数据类型代替约定俗成的特殊字符串。标准模块中的 `Option` 和 `Result` 用模块身份区分，即使不同模块的构造器恰好同名也不会混淆。

```chaos example=advanced-option-result
import "std/option" as O;
import "std/result" as R;
fn first(xs) {
  match xs { [] => O.None(), [x, .._] => O.Some(x) }
}
fn checkedDouble(value) {
  if value < 0 { R.Err("negative") } else { R.Ok(value * 2) }
}
let picked = O.map(fn(x) { x + 1 }, first([20, 30]));
let answer = match picked {
  O.Some(x) => checkedDouble(x),
  O.None() => R.Err("empty")
};
match answer { R.Ok(x) => {ok: true, value: x}, R.Err(e) => {ok: false, error: e} }
```

结果是 `{"ok": true, "value": 42}`。`O.map` 只转换 `Some`，保留 `None`；`R.map` 只转换 `Ok`，保留 `Err`。这里没有自动传播运算符，可用 `match` 明确衔接。`try/catch` 用于异常；资源限制和取消始终向外传播，不能包装成业务成功值。

协议可序列化，但不是不可伪造对象。与不可信外部输入交互时，类型契约只检查 `$type/$tag/$values` 的结构、类型身份、标签和字段数量；业务字段本身还应由程序验证。

## 让循环退出经过清理

当一轮循环用 `continue` 跳过或用 `break` 结束时，把本轮必须完成的语言状态更新放进 `finally`。清理使用外层共享状态，不依赖保护块内部的局部变量。

```chaos example=advanced-finally-loop
var cleaned = [];
for item in [1, 2, 3] {
  try {
    if item == 1 { continue } else { null };
    if item == 2 { break } else { null };
  } finally {
    cleaned = append(cleaned, item);
  }
}
cleaned
```

结果是 `[1, 2]`：第一轮继续前清理，第二轮退出前清理，第三轮不会开始。清理块正常完成的值不改变保护块的结果；清理中的 `return/throw` 会替换普通待决结果，应避免把它误写成日志或记录操作。

`finally` 是有预算的语言控制流机制，不能保证超时、取消或资源耗尽后的清理。已交付的上述硬控制会跳过清理；显式[取消保护](CANCELLATION.md)可以在局部取消交付前完成内部清理，但不延迟运行级停止和预算耗尽；清理本身触发硬控制时也立即向外传播。文件句柄、进程、网络连接等宿主资源仍应由宿主工具在自身生命周期中释放，语言示例不建立跨工具事务或自动回滚。

## 模块封装与初始化

模块公开稳定的函数接口，隐藏可变状态。下面入口同时通过两个写法导入同一规范路径，读取和更新的是同一实例：

```chaos example=advanced-module-state
import "./counter.chaos" as A;
import "counter.chaos" as B;
[A.next(), B.next(), A.read()]
```

伴随文件 `counter.chaos` 的完整内容：

```chaos
var count: Int = 0;
fn next() -> Int { count = count + 1; count }
fn read() -> Int { count }
export {next, read};
```

结果是 `[1, 2, 2]`。可通过 SDK 提供同一个文件包：

```python
from chaos import Framework
from chaos.modules import ProgramBundle

source = 'import "counter.chaos" as C; [C.next(), C.read()]'
files = {"counter.chaos": "var n = 0; fn next() { n = n + 1; n }; fn read() { n }; export {next, read}"}
bundle = ProgramBundle(source, files=files)
assert Framework.evaluate(bundle) == [1, 1]
assert Framework.evaluate(bundle) == [1, 1]  # 每次完整求值重新初始化模块
```

包在执行前验证所有提供文件，拒绝循环依赖。`rec` 解决同一词法作用域的互递归，不能用它绕过模块导入环。对于互相依赖的模块，把公共 data 或函数提取到第三个基础模块，或显式传入所需函数。

顶层效果也只在模块初始化时执行，但这种“每次运行最多一次”不是跨运行的幂等保证。重试完整运行会再次初始化模块；持久化写入应由宿主工具提供清晰的幂等协议。

## 递归与有限执行预算

累积参数可以把普通递归转换为尾递归。尾调用不会为每步积累用户函数调用深度，但仍消耗表达式步数和时间。

```chaos example=advanced-tail-recursion
fn totalTo(n: Int, acc: Int) -> Int {
  if n == 0 { acc } else { totalTo(n - 1, acc + n) }
}
totalTo(100, 0)
```

结果是 `5050`。对列表求和通常直接使用 `sum` 或 `fold` 更清晰。`n + f(n - 1)` 不是尾调用，因为返回前仍要执行加法。`try` 处理器和 `finally` 清理需要在其动态范围内解析尾调用，跨这些范围的递归可能消耗普通调用深度；不要仅凭外观推断它可以无限堆叠。

| 限制位置 | 当前实现上限或默认值 |
| --- | --- |
| 单个源码文件 | 100000 个 Unicode 字符；报错文案中的 “100 KB” 是简写 |
| 封闭包 | 64 个文件（含入口），累计 1000000 个 UTF-8 字节 |
| 模块依赖深度 | 32 |
| `Framework.evaluate` / `Evaluator` / Session 单次提交 | 默认 30000 步、20 秒，可通过构造参数设置 |
| 集成项目运行（包括 CLI `run`） | 共享 100000 步；时间取 `runtime.timeout`，默认 120 秒；每个 Evaluator 仍最多 30000 步 |
| 单次调用 | 全部位置参数与具名参数合计最多 10000 项，包含展开和部分应用合并后的参数 |
| 原生函数 JIT | 每 Evaluator 最多 32 个内核；每内核最多 8 个简单参数、128 个 AST 节点、表达式深度 64；原检查点计费不变 |
| 非尾调用深度 | 120，同时仍受宿主语法求值嵌套限制 |
| 整数 | 14000 bit；`pow`、`**` 和左移会在分配前拒绝过大结果 |
| 列表 / 单条记录 | 10000 项 / 10000 字段 |
| 单次值展开检查 | 20000 节点、最大深度 100、字符串合计 250000 字符 |
| 程序 IR | 10000 节点、深度 128、序列化长度 2000000 字符 |
| CLI `run --trace` | 最多 2000 条表达式记录，再输出截断提示 |

步数是解释器计数，不等于源代码行数。高阶回调、相等比较、模块及工作流都可能消耗预算。`timeout` 是运行检查点上的时间预算，不能强行抢占一个未返回的同步宿主回调；扩展工具应调用 `context.checkpoint()` 协作响应取消，见[SDK](SDK.md)。

纯计算调整预算的例子：

```python
from chaos import Framework
from chaos.language import ResourceLimit

assert Framework.evaluate("sum(range(100))", step_limit=10000, timeout=2) == 4950
try:
    Framework.evaluate("while true {}", step_limit=100, timeout=1)
except ResourceLimit as error:
    assert error.code == "resource_limit"
else:
    raise AssertionError("资源限制应终止循环")
```

`try { while true {} } catch e { ... }` 无法解除限制。提高预算只适用于已理解成本的工作负载；容器和整数等结构限制不会随 `step_limit` 自动提高。

## 抽象计算能力与真实运行

讨论图灵完备性时，指的是去掉资源上限后的抽象递归与无界数据语义：整数、条件分支、状态或递归足以描述两计数器机的增量、减量与零检测操作。这是对抽象模型的论证方向，不是“当前部署能无限运行”的承诺。

实际解释器始终有上述有限预算。测试只证明被测程序在相应输入下的行为，不能证明任意程序终止、模型推理正确、工具效果可逆或分布式执行可靠。当前提供 [AST 宏](MACROS.md)、[生成器与 async/await](EFFECTS.md) 和 [源码调试](DEBUGGER.md)；全局惰性求值与任意 continuation 尚未提供。

## 调试与可维护性

先用 `chaos check` 检查包结构，再用纯 `Framework.evaluate` 验证确定性转换，最后在集成运行里验证工具与能力。`check` 不会查出所有运行时未定义名称或动态契约失败。错误记录含源文件和位置，导入模块里的错误应定位到该模块。

`chaos run --trace` 给出有界的表达式观察记录，不自动重放工具、提供方或工作流效果。CLI fmt、IDE、SDK 与 HTTP 的源码格式化共用保留注释的格式化器；从结构 IR 重新生成代码时，IR 本身没有保存注释，因此仍应保留原源码。把关键纯例子纳入 `examples.json` 的原因是让签名与语义漂移可以在文档检查时被发现，而不是等到读者照抄失败。


## 有界的流式智能体

异步流把工作量交给消费者控制；`anext`/`asend` 是冷 Task，不应无界创建。一次流操作未完成前不发起同流第二次操作；多个独立流可用 gather 并发。流不会增加原运行的总预算或权限，也不是持久化工作队列。

同步/异步 yield from 委托局部执行状态，跨进程传递请使用数据、任务 ID 或项目源码，不能发送活动栈。关闭和取消遵循不同规则，详见[流生命周期](EFFECTS.md)；不要假设硬取消会执行任意用户 finally。

## 用组件组织有状态智能体

用类将 Agent 字段与 `__call__` 方法封装为可组合函数；用 `async fn*` 方法表达可暂停、接收反馈的工作流。类的 `var` 默认值在声明时求值一次，实例首次赋值后拥有自己的绑定；若默认值本身是可变实例，多个对象会共享它。独立对象状态应在 `__init__` 中逐实例构造。

多继承用 C3 顺序处理共同基类，协作方法显式调用 `super(CurrentClass, self)`。不要把它当成“第一个父类”：它沿接收者真实继承顺序前进。继承不会自动扩大智能体能力。例子和边界见[对象与组件](OBJECTS.md)。


## 0.72.0：原生任务局部上下文

[任务局部上下文与动态绑定](DYNAMIC_CONTEXTS.md)说明 ContextVar、ContextToken、ContextScope 和 Context 的实际语言语义：普通任务/生成器创建时浅复制绑定，词法与透明协议调用共享上下文，选定 async 回调原 AST 执行，退出与取消恢复绑定。上下文可保存合法原生语言值，不能扩大工具权限。源码/IR、IDE、CLI、SDK 与 HTTP 共用实现；复制是有界 O(n)，宿主 GC 未被替换。本增量不表示完整 Python/Lisp 生态或自托管已经完成。


## 0.73 原生整数优化

[原生整数 DAG 优化](SCALAR_OPTIMIZER.md)为现有 LLVM 标量区域提供实际常量折叠、CSE 和数据依赖调度。原逻辑检查点、源位置、Task/Context 和效果边界保持不变；普通编译函数可直接成为 AgentTool/Team 的函数实现。compile_optimization_info 是共享 NativeSpecs 的只读查询，旧 compile_function/compile_info 不变。它不表示整程序指令重排、链接删除、原生循环/AOT、自研 GC 或完整 Python/Lisp 能力已经完成。


## 0.74 可变控制流中的原生区域

[可变控制流中的原生计算区域](NATIVE_CONTROL_REGIONS.md)将显式 auto-control/native-control 配置接入原 compile_function 两参数接口。原 Function/AST/Cell、同步迭代器、循环耗尽 else、break/continue/return 与真实 CHECKPOINT 保持不变；选中表达式逐次读取当前值并进入受守卫的 LLVM 内核。增强赋值只编译 RHS；严格 Bool、短路与相等使用原协议。旧 auto/native/interpreter 的范围、默认行为、compile_info 12 字段和优化查询保持兼容。此实现不是整个循环 CFG 的机器分支、可恢复 PC ABI、AOT、自研 GC、完整 Python/Lisp 或自举。
