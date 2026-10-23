# 推导式与生成器表达式

0.35 将推导式实现为语言 AST 节点。源码、Program 结构、CLI、SDK、HTTP 和 IDE 使用同一解析器及运行时；没有把代码改写成提示词。智能体、Team、Session 与普通函数可以在投影表达式中按原有调用和效果协议组合。

## 列表与嵌套绑定

```chaos example=comprehension-list
[x * 10 + y for x in [1, 2, 3] if x > 1 for y in [x, x + 1]]
```

结果为 `[22, 23, 33, 34]`。子句按书写顺序执行，每个 `for name in source` 先计算来源，再为每一项建立独立的只读绑定；后续来源和条件可以引用此前绑定，投影可以引用全部绑定。绑定不泄漏到推导外，同名内层绑定按词法作用域遮蔽外层。

```chaos example=comprehension-closures
let x = 100;
let functions = [fn() { x } for x in [1, 2]];
[functions[0](), functions[1](), x]
```

`if` 条件必须是 `Bool`，不进行 Python 风格的隐式真值转换。0.36 起绑定目标还可使用[结构模式](PATTERN_BINDINGS.md)，与同步／异步 `for` 一致；0.57 起支持[集合推导](SETS.md)。

## 动态记录键

```chaos example=comprehension-record
let result = {key: key + key for key in ["b", "a", "b"]};
[result, keys(result)]
```

记录推导的 `key` 是表达式，必须产生 `String`；运行时先求键并检查类型，再求值。重复键以后值覆盖，保留第一次插入的位置。普通记录 `{name: value}` 的静态字段语法保持不变。

## 惰性生成器

```chaos example=comprehension-lazy
var calls = 0;
fn source() { calls = calls + 1; [20, 21] }
let values = (x * 2 for x in source());
let before = calls;
[before, next(values).value, next(values).value, next(values).done, calls]
```

创建生成器时只计算并保存最外层来源表达式。首次 `next` 才取得其迭代器并取项；内层来源、条件和投影均在消费时计算。生成器捕获创建时的词法环境，已存在的可变绑定仍共享 Cell。停止消费不会自动关闭借用的来源；`close` 关闭推导包装器，`send` 的输入被忽略，`throw_into` 的异常不转发给来源。需要自动释放来源时使用显式[资源作用域](CONTEXTS.md)。

同步来源支持 List、Tuple、Set、String、Generator、Iterator 和用户 `__iter__` 协议。每一步沿用现有迭代器互斥规则；暂停的推导不会长期锁住上游游标，其他持有者仍能显式消费它。

## 异步投影和来源

```chaos example=comprehension-async-list
async fn double(x) { x * 2 }
[await double(x) for x in [20, 21]]
```

列表／记录推导在当前上下文执行。只有顶层或允许等待的异步函数上下文可以使用 `await`、`async for`；异步函数调用本身仍返回冷 Task，不自动等待。

异步生成器使用显式 `async (...)`，其类型不会因为宏展开后出现 `await` 而悄悄改变。

```chaos example=comprehension-async-generator
async fn double(x) { x * 2 }
let values = async (await double(x) for x in [20, 21]);
[(await anext(values)).value, (await anext(values)).value, (await anext(values)).done]
```

```chaos example=comprehension-async-source
async fn* source() { yield 20; yield 21 }
[x * 2 async for x in source()]
```

`async for` 使用原生异步迭代协议，也可消费 Channel。异步生成器的内部投影和后续子句允许等待，但最外层来源表达式在创建处立即求值，其等待权限来自外层上下文。普通名为 `async` 的函数调用仍是普通调用；只有完整的生成器推导结构使用该语法。

推导组件建立自己的控制边界，不允许直接 `return`、`yield`、`break` 或 `continue` 穿透到外层函数／循环；组件内显式定义的函数和循环仍遵循自身规则。取消和错误沿用既有调度器，异步取项任务会在包装器中断时请求取消。

## 与智能体函数组合

下例可以作为已有 `worker` 智能体函数的批次编排：

```chaos
async fn work(item) { worker(item) }
let jobs = [work(item) for item in inputs];
let results = await gather(jobs);
```

每次调用仍经过语言的能力、录制范围、预算和效果审计。推导式本身不额外授予工具权限；TaskGroup 可用于需要词法归属与失败排空的批次。模型效果仍需配置真实提供方，上述片段不属于离线示例测试。

## 宏、结构编辑与代码提示

```chaos example=comprehension-macro
macro doubled(values) { quote { [x * 2 for x in unquote(values)] } }
let x = 100;
[doubled!([20, 21]), x]
```

宏按“来源→新绑定→后续子句→投影”的顺序生成独立绑定身份；`unquote` 保留调用方名称。编译期纯求值支持 List／Tuple／Set／String 的同步列表、记录和集合推导，仍受宏预算约束；生成器、异步调用和运行时效果只能放在 `quote` 中。

Program 使用 `list_comp`、`set_comp`、`record_comp`、`gen_comp`、`async_gen_comp` 及其 `comp_for`、`comp_async_for`、`comp_if` 子句。现有 `chaos.program/1` 和 languageVersion 1／2 保持不变，子句不能成为独立可执行表达式。结构编辑与源码格式化后均重新解析验证语义；编辑器按真实绑定位置和使用范围给投影中的名称提供高亮、补全及悬浮说明。

## 真实待决遍历状态

以下片段用显式计数展示惰性来源和逐项消费的时序：

```chaos
var reads=0;
fn* source(){for value in [20,21]{reads=reads+1;yield value}}
let stream=(value*2 for value in source());
let before=reads;let first=next(stream).value;
let remaining=collect(stream);
[before,first,remaining,reads]
```

结果为 `[0,40,[42],2]`。

推导式创建与逐子句遍历使用统一执行游标上的真实帧；共享结果、集合构造器、条目计数与每个子句的来源、游标、词法环境、键／投影和待决异步步骤都属于实际执行状态。修改待决字段会影响后续执行，不另建只用于动画的解释器。源码、结构 IR、Code／Program、宏和智能体函数共用此路径。

创建时最外来源的时序、每项独立绑定与共享可变 Cell、严格 Bool、先键后值、yield 后计数、借用游标不自动关闭及原检查点均保留。原调度器继续处理实际任务、取消和执行周期清理；帧不表示活栈可复制、持久化或跨重启恢复。见[执行状态](EXECUTION_FRAMES.md#迭代消费委托与推导的执行状态)。

## 有界运行与兼容边界

每个推导含 1–64 个子句，首项必须是遍历子句。每次来源调用最多消费 10000 项，随后允许一次结束探测；每个立即结果最多接纳 10000 项，重复记录键和集合成员也计数。全局步骤、时间、值、任务和效果预算继续生效。内部 List／Tuple／Set／String 临时游标不积累公开句柄额度。推导式不会为有限机器取消资源限制，也不代表实现了 Python／Lisp 的全部语法或生态。

参见[语言参考](LANGUAGE.md)、[函数组合](FUNCTION_COMPOSITION.md)、[迭代协议](ITERATORS.md)、[任务](TASKS.md)及[IDE](IDE.md)。

## 0.57 集合推导

`{head for target in source if guard}` 立即创建原生 Set，也可使用已有 async for／await 作用域；头表达式每次执行后逐项校验去重。变量作用域、结构模式、过滤、取消和资源协议与既有立即推导一致。Set 的纯键／用户键协议、预算与顺序见[集合规范](SETS.md)。
