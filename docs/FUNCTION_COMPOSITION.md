# 可迭代值与函数组合

函数调用与高阶消费现在使用循环已有的同步迭代协议。生成器、用户数据源和智能体回调可以直接组合；数据逐项产生，执行仍由同一 AST 调度器负责，不通过角色名称或提示词猜测顺序。

## 从输入流构造智能体工作

```chaos example=iterable-jobs
fn* inputs() {
  yield 20;
  yield 21;
}
async fn work(value) { value * 2 }
let jobs = map(work, inputs());
await gather(jobs)
```

结果为 `[40,42]`。map 本身是顺序、急切的消费者：先消费输入并构造结果列表。async 函数返回冷 Task，所以 map 不会替调用者等待这些任务；gather 才启动和收束它们。work 的函数体可以调用有权限的 Agent、Team 角色或 Session，能力与预算继续来自真实调用上下文。普通同步函数中的智能体效果也会在当前任务内协作挂起；map 不隐式并发执行回调。

## 一套同步输入协议

以下操作接受 List、Tuple、Set、HashMap、String、Generator、Iterator，以及具有同步 `__iter__`／`__next__` 协议的用户对象。String 按 Unicode 码点产生字符。输入工厂调用一次，之后使用所得游标；生成器和已存在的游标从当前位置继续，不复制和重播。完成步骤的 value 不作为输入项。

| 操作 | 消费和结果 |
| --- | --- |
| `f(*source)` | 消费 source 作为本次调用的位置参数 |
| `apply(fn,args,kwargs=null)` | 消费 args 后按原规则绑定位置参数及 Record kwargs |
| `map(fn,xs)` | 每项调用一次 fn，返回结果 List |
| `filter(fn,xs)` | 谓词必须返回 Bool，返回满足条件的原项 List |
| `fold(fn,initial,items)` | 顺序调用 fn(accumulator,item)，返回最后累积值；空输入返回 initial |
| `any(f,xs)` | 遇到 true 立即返回；空输入为 false |
| `all(f,xs)` | 遇到 false 立即返回；空输入为 true |

```chaos example=iterable-values
fn* numbers() { yield 1; yield 2; yield 3; yield 4 }
[filter(fn(n) { n % 2 == 0 }, numbers()),
 fold(fn(total, n) { total + n }, 0, numbers()),
 map(upper, "a😀")]
```

结果为 `[[2,4],10,["A","😀"]]`。标准模块 std/list 的 map/filter/fold/any/all 是相同内建值的别名，具有相同语义；模块名不限制这些函数的输入必须是 List。其余列表专用函数，例如 head、tail、sort、zip、take、drop 和 sum，仍按[标准库](STDLIB.md)规定工作。本次没有新增全局 Iterable 名义类型；IDE 的 Any 表示静态未知，运行时仍检查真实协议。

HashMap 同步迭代产出实际键，提取条目使用 hash_items，详见[通用键映射](HASH_MAPS.md)。Record 不会自动迭代键或值，应明确使用 keys(record)／values(record)。AsyncGenerator、AsyncIterator 和 Channel 也不会被偷偷等待：使用 async for、acollect 或显式异步程序组织它们。filter/any/all 不会 await 异步谓词返回的 Task；它不满足 Bool 契约。

## 调用展开与效果次序

```chaos example=iterable-spread
var log = [];
fn mark(label, value) { log = append(log, label); value }
fn* arguments() {
  yield mark("first", 20);
  yield mark("second", 22);
}
fn add(a, b, *, scale = 1) { (a + b) * scale }
let result = add(*arguments(), scale=mark("scale", 2));
[result, log]
```

结果为 `[84,["first","second","scale"]]`。先计算目标函数，再按源码顺序计算每个实参；一次星号展开完整完成后才计算下一个实参。所有展开及绑定成功后才进入目标函数。展开失败、重复参数或缺参不会执行目标体，但已经发生的输入消费和工具效果不会撤销。

```chaos example=iterable-apply
fn* arguments() { yield 20; yield 22 }
fn add(a, b, *, scale = 1) { (a + b) * scale }
apply(fn=add, args=arguments(), kwargs={scale: 2})
```

结果为 `84`。kwargs 仍只接受 Record 或 null，不新增映射协议。调用的参数名及位置／具名顺序规则保持原样，apply、partial 和 compose 仍共享相同尾调用与返回契约；这不是调用自动柯里化。

## 短路与借用游标

```chaos example=iterable-short-circuit
var visited = [];
fn* numbers() { yield 1; yield 2; yield 3; yield 4 }
let cursor = numbers();
let found = any(fn(n) {
  visited = append(visited, n);
  n == 2
}, cursor);
[found, next(cursor), visited]
```

结果为 `[true,{"done":false,"value":3},[1,2]]`。any 没有为了判断下一项而多取 3。普通回调错误或短路退出后，借用游标继续保留已消费的位置，不隐式关闭它。需要确定的关闭范围时使用 std/context.closing；本轮结束的正常清理规则保持不变。

一次消费期间游标处于独占 busy 状态。回调不能再次推进或关闭同一游标，另一个任务也不能借别名绕过此限制。退出时释放本次锁；未被当前操作拥有的游标不会被自动重置。异常发生于输入协议自身时，游标继续遵守其原有失败状态规则。跨解释器、执行周期或录制范围的句柄仍会被拒绝。

## 用户协议也是函数

```chaos example=iterable-protocol
class Counter {
  var current = 0;
  fn __iter__(self) { self }
  fn __next__(self) {
    self.current = self.current + 1;
    {done: self.current > 3, value: self.current}
  }
}
let source = Counter();
[fold(fn(total, n) { total + n }, 0, source), source.current]
```

结果为 `[6,4]`。终止步骤执行了一次，但完成值 4 没有参与累积。协议方法可以调用普通函数和授权智能体；所有推进与回调保留真实执行事件、调用栈、取消检查和权限边界。

## 资源与一致性

每次高阶消费最多处理10000个输入项，独立于输出数；过滤全部元素不能绕过限制。完整消费在10000项后仍需读取一次步骤以判断 EOF；若该步骤又产出一项，则报告资源限制，该溢出项已被读取但不会传给回调或目标函数。短路操作在决定结果后不做这种探测。步数、时间、值大小和效果预算同时生效，通常会更早约束昂贵或无限输入。

位置展开与 apply 沿用单次调用位置加具名参数总计10000项的上限；多次展开共同计数。硬取消和资源限制仍是不可捕获控制信号，遵守运行期排空与清理规则，不承诺强行停止不合作的宿主回调，也不回滚已提交工具效果。

现有 spread/call AST 已可表达这些语义，没有增加 IR 节点或更改公开 API 形状。源码、格式化、结构编辑、宏、SDK、CLI 和 HTTP 运行同一程序。IDE 和在线／离线代码提示来自真实内建签名及作用域分析，不消费输入或执行回调来推测类型。原生列表／记录推导及同步／异步生成器表达式已在 0.35 接入，集合推导在 0.57 接入，见[推导式](COMPREHENSIONS.md)。按需组合使用[std/iter 惰性流](STREAMS.md)，它返回借用输入的单次生成器，与本页急切高阶消费者保持区别；专门的智能体 token 流协议和完整 Python/Lisp 生态仍未提供。

## 位置专用参数

`/` 将此前的参数标记为“只按位置传入”。这让普通函数、智能体工具函数和库函数的内部参数名能够独立调整；调用方式由参数位置、契约和默认值决定。`/` 是签名边界，不占实参位置。

```chaos example=posonly-basic
fn consume(value: Int, /, **options) {
  [value, options]
}
consume(42, value="metadata")
```

结果为 `[42,{value:"metadata"}]`。同名关键字没有绑定 value，而是进入 options。没有 `**options` 时它是未知关键字；即使提供 `value=42`，仍不能替代必需的位置参数。

```chaos example=posonly-default
fn consume(value: Int = 10, /, **options) { [value, options] }
[consume(value=99), consume(42)]
```

结果为 `[[10,{value:99}],[42,{}]]`。位置专用参数省略时使用默认值，关键字数据仍单独保留。默认表达式在定义时按顺序计算一次；`/` 不重置默认值排序规则，例如 `fn f(a=1, /, b)` 仍是语法错误。

```chaos example=posonly-pattern
fn consume(value as [left, right]: List, /,
           scale: Int = 1, *, offset: Int = 0, **options) {
  [(left + right) * scale + offset, options]
}
consume([20, 21], scale=1, offset=1, value="metadata")
```

结果为 `[42,{value:"metadata"}]`。参数槽的整值契约、只读模式叶子和构造器作用域沿用[参数解构](PATTERN_BINDINGS.md#函数参数中的模式)。绑定、契约和模式检查全部成功后，普通函数才进入主体，直接调用的异步／生成器函数才创建冷句柄。已经求值的实参效果不会撤销。

```chaos example=posonly-error
var calls = 0;
fn work(value, /) { calls += 1; value }
[try { work(value=42) } catch error { error.code }, calls]
```

结果为 `["argument_error",0]`。`apply`、`partial` 和 `compose` 最终都使用同一绑定器；`partial(work, value=42)` 不会把这个关键字转换为位置值，是否有效仍由最终调用决定。

`/` 最多一次，前面至少一个普通参数，并且必须位于 `*`／`**` 之前。可以写 `fn f(value, /)` 或 `fn f(value, /,)`。它不改变调用端“位置及 `*` 在前、具名及 `**` 在后”的规则，也不改变除法运算或行注释。

```chaos example=posonly-method
class Component {
  fn answer(self, value: Int, /, **options) { [value, options] }
}
Component().answer(42, self="metadata", value="label")
```

结果为 `[42,{self:"metadata",value:"label"}]`。绑定接收者仍作为首个位置实参传入。普通、静态和类方法，以及属性、运算和资源方法使用同一规则；声明为位置专用并不改变调用协议所需的实参数量。普通 Agent 本身仍沿用既有一元 input 接口；位置专用签名用于语言函数及其组合，不修改 Agent 的传输合同。

IDE 根据真实参数模式显示 `/`，不为位置专用参数提供 `name=` 补全。隐藏方法接收者后，仅在剩余参数确有位置专用项时保留 `/`。结构编辑中的参数模式、默认值和排序会按解析器相同的约束验证；无效修改不会被静默重排。源码、IR、API、SDK、LSP、宏和页面使用同一参数定义。

语法借鉴 [PEP 570](https://peps.python.org/pep-0570/)；本语言保留自身的类型契约、模式匹配、能力边界和资源预算。

## 元组函数产品

`tuple(items=[])` 使用共享同步迭代器构造原生 Tuple。Tuple 可直接参与位置展开、apply、map/filter/fold/any/all 和普通迭代；`(action,value)` 可将真实函数及输入作为一个值交给角色或 Channel。List 专用函数保持既有合同，需显式 collect 时不会自动转换。构造、切片、相等、模式及 JSON 边界见[元组规范](TUPLES.md)。

## 消费者的执行状态

map／filter／fold／any／all、参数展开、apply 及 Tuple／Set 构造共用真实消费帧。待决回调、参数、累积值和输入位置由字段驱动，保留严格 Bool、完整消费的一次溢出探测、短路不多取项与借用不关闭合同。异步函数仍返回需要显式等待的冷 Task；迁移不会自动展开结果或授予新权限。见[执行帧](EXECUTION_FRAMES.md#迭代消费委托与推导的执行状态)。
