# 模式异常处理与动态重抛

异常处理属于函数语言的原生控制流。普通函数、Agent、Team 角色与 Session 工具处理器使用同一个错误记录、模式匹配、作用域和挂起运行时。程序可以按错误码和业务数据选择恢复分支，不依赖错误消息中的关键词。

## 有序模式与条件守卫

```chaos example=exception-ordered
fn recover(value){
  try {throw value}
  catch {code:"user_throw",value:{kind:"retry",attempt:n},.._} if n<3 {n+40}
  catch {value:{kind:"retry",attempt:n},.._} {n}
  catch other {throw}
}
[recover({kind:"retry",attempt:2}),recover({kind:"retry",attempt:3})]
```

结果为 `[42,3]`。每个 catch 匹配完整错误记录 `{code,message,value,location}`，业务数据位于 value 字段。记录模式没有剩余项时要求字段集合完全相同，通常使用 `.._` 忽略其余字段。列表、元组、标量和标签构造器使用现有[模式规则](PATTERN_BINDINGS.md)；例如业务标签放在 `catch {value:Rejected(reason),.._}` 中。

候选从上到下选择。模式匹配不调用 getter、用户运算符、工具或模型；只有匹配后才运行可选的 `if` 守卫。守卫必须返回 Bool，false 继续下一项。守卫和处理块可以使用当前位置允许的普通调用、await 和控制流。没有候选命中时，原错误继续向外传播，保留错误码、值与首次定位，不变成新的 user_throw。

历史 `catch error { ... }` 保留原语法和 AST；`catch _ { _ }` 仍把 `_` 作为错误记录名称绑定。结构模式内部的 `_` 则继续表示忽略。每个候选从原错误取得新的容器副本；被拒绝候选中的绑定和容器变更不影响下一候选。共享活对象及已经发生的外部效果仍遵循既有身份、权限与预算规则，不自动回滚。

## 分支作用域和错误边界

```chaos example=exception-scopes
let amount=42;
try{throw 21}
catch {value:amount,.._} if false {0}
catch {value:number,.._} {[number,amount]}
```

结果为 `[21,42]`。每个候选建立独立局部作用域，其绑定供自己的守卫和处理块使用；构造器名称从外层词法环境解析。其他候选、else 和 finally 看不到这些局部绑定。

```chaos example=exception-boundary
try{
  try{throw 20}
  catch {value:number,.._} if do{throw 42} {number}
  catch other {99}
}catch outer{outer.value}
```

结果为 `42`。模式中的构造器查找出错、守卫抛错和选中处理块抛错，都直接交给外层处理器；后面的同级 catch 不负责处理这些新错误。成功 else 与清理 finally 继续使用[正常完成语义](CONTROL_COMPLETION.md)：仅保护块正常完成触发 else，任何选中的 handler 都不会再触发 else。

## 保留原错误的裸 throw

```chaos example=exception-rethrow
fn again(){throw}
try{
  try{throw 42}catch original{again()}
}catch final{[final.code,final.value]}
```

结果为 `["user_throw",42]`。`throw expression` 对普通值构造新的 user_throw；从 0.81 起，表达式为原生 Error 时传播该错误；单独的 `throw` 重新抛出当前正在处理的原错误。它是动态执行状态，同步辅助函数可以访问调用者的活动错误，闭包不会把错误永久捕获为词法变量。没有活动错误时，裸 throw 抛出 `rethrow_error`。嵌套处理器退出后恢复外层错误；尾调用和 compose/partial 等函数组合保留相应调用上下文，不迫使旧处理器尾递归增长普通调用栈。

裸 throw 的边界由表达式终止符确定；为避免与下一条表达式连在一起，连续语句应写 `throw;`。换行本身不是自动语句终止符。重抛不会根据某个形似错误的记录恢复宿主异常，修改 catch 的记录也不会改写原始错误身份。

```chaos example=exception-finally
try{
  try{throw 42}finally{throw}
}catch {value:answer,.._}{answer}
```

结果为 `42`。finally 因普通语言错误进入时，该待决错误在清理块内成为活动错误；正常进入的 finally 继承已有外层活动上下文。资源限制、已交付取消及其他硬控制继续遵循原有优先级，不被 catch 捕获，也不会为执行清理延长预算。

## Task、生成器和词法任务组

```chaos example=exception-async
fn again(){throw}
async fn work(value){
  try{
    try{throw value}catch error{await sleep(0);again()}
  }catch {value:answer,.._}{answer}
}
await gather([work(20),work(22)])
```

结果为 `[20,22]`。每个任务有独立活动错误，await 后恢复自己的上下文。生成器也保留自己的 handler/finally 状态跨 yield 恢复，调用 next/send/close 的另一个函数不会把自己的活动错误借给它。

创建冷 Task 或生成器不会捕获创建者的活动错误；它们首次运行时也不会继承启动者的错误。`async with task_group() as group` 的词法主体属于当前动态控制范围，其内部调度载体继承活动错误；`group.spawn(...)` 接纳的独立成员不继承。

自动激活原错误的边界是 catch 选择／守卫／处理块，以及因普通错误进入的 finally。`with` 的 `__exit__`、ExitStack 等资源出口仍按已有协议接收错误记录，不会仅因收到该记录而新增活动错误；同步出口调用若位于外层活动 handler 内，仍继承那个外层错误。这是 Chaos 的明确规则，不等同于 Python 全部动态异常上下文行为。生成器资源管理器中显式捕获的注入错误仍按普通 catch 规则处理。

## 宏和程序自修改

```chaos example=exception-macro
macro recover(action,fallback){quote{
  try{unquote(action)()}
  catch {value:value,.._} if value>40 {value}
  catch other{unquote(fallback)}
}}
let value=42;
[recover!(fn(){throw 41},value),recover!(fn(){throw 1},value)]
```

结果为 `[41,42]`。各处理器的模式绑定卫生重命名，守卫和处理块引用相同身份；外层值通过 unquote 显式传入。quote 可以生成裸 throw 辅助函数，活动错误在运行时解析。纯编译期求值器仍只支持[宏规范](MACROS.md)中的子集。

```chaos example=exception-program
let old=program_from({kind:"program",entry:"main.chaos",
  files:{"main.chaos":"try{throw 1}catch {value:answer,.._}{41}"},
  languageVersions:{"main.chaos":2}});
let result=program_ir(old).root.statements[0].cases[0].body.statements[0];
let changed=program_edit(old,"main.chaos",[
  {op:"set",id:result.id,field:"value",value:42}
]);
[result.value,program_ir(changed).root.statements[0].cases[0].body.statements[0].value]
```

结果为 `[41,42]`。修改产生新的不可变 Program；实际采用仍需要宿主、能力检查、probe/adopt 和项目版本检查。角色函数因此能够以结构编辑修改自己的错误恢复策略，历史运行保持旧快照。

## IR、编辑器与边界

`chaos.program/1` 新增 `try_cases {body,cases}` 和零字段 `rethrow`。每个 case 是 `{pattern,guard,body}`；guard 可以为 null，保护块和每个 handler 必须为 block。单个无守卫名称处理器使用历史 try 节点；不接受另一种会在格式化后改变节点类别的等价编码。catch 根模式不能是 p_any，避免把它打印成历史名称 `_`；结构内部允许 p_any。

IDE、CLI、SDK、HTTP、LSP 和结构编辑共享同一 AST。图形界面可增删、重排处理器及修改守卫；顺序会改变实际选择。高亮、成员提示、定义导航与重命名使用各分支自己的词法身份。候选模式可能不匹配是合法控制流，不会被误报为解构赋值失败；分析不运行守卫、Agent 或工具。

0.81 已实现下述 Error、嵌套错误组和 catch*；0.82 增加原生 `cause/context` 因果链。0.83 增加下述原生异常类与名义捕获；跨进程活动异常恢复或任意 Python/Lisp 包兼容仍未实现。动态重抛概念可对照 [Python raise 参考](https://docs.python.org/3/reference/simple_stmts.html#the-raise-statement)，本语言的结构模式、显式 Task、生成器隔离和资源出口以本页合同为准。

## 按抛出的类实例选择分支

`catch {value: Result::{status: status}, .._}` 在完整错误记录的 value 内匹配原生类实例。它使用同一纯字段模式，不执行 getter；名义类目标错误直接向外传播。现有动态重抛和 finally 优先级不变。0.83 的根异常类模式另有下述名义捕获语义；嵌套在 value 中的模式仍按此处旧规则执行，见[类字段模式](CLASS_PATTERNS.md)。


## Error 与分组捕获（0.81）

`error(value)` 构造一个原生 Error，`error_group(message, errors)` 将非空 List[Error] 组合为嵌套错误树。Error 是新增内建类型名；原有同名 class/data 声明需要重命名。error、current_error、error_group 是可由局部声明遮蔽的普通内建绑定。Error 属于创建它的 Evaluator；只能通过真实语言值传递，JSON 诊断不能恢复活错误。只读成员为 code、message、value、location、exceptions，以及下述 cause、context、suppress_context、instance；叶子的 exceptions 是空 Tuple，组的 exceptions 是直接子 Error 的 Tuple。普通错误保持 user_throw 和业务 value；组的 code 为 exception_group，value 为 `{exceptions: [子错误记录...]}`，普通 catch 仍得到原来的四字段记录。

```chaos example=exception-group-partition
let batch=error_group("workers",[error(20),error(22)]);
var outputs=[];
try{throw batch}
catch* {value:n,.._} if n==20 as selected {
  outputs=append(outputs,selected.exceptions[0].value)
}
catch* _ as remaining {
  outputs=append(outputs,remaining.exceptions[0].value)
};
outputs
```

结果为 `[20,22]`。catch* 按原顺序匹配每个尚未处理的**叶子错误**：普通模式使用四字段记录，0.83 的根异常类模式使用名义实例视图；严格 Bool 守卫也逐叶执行；匹配子树作为一个 Error 交给该处理器一次。组的嵌套、名称、顺序和重复叶子的出现位置均保留。模式绑定只供守卫使用；`as` 名称只在处理块内可见，表示整个子组。catch* 根 `_` 是通配模式；普通 catch 根 `_` 仍是历史绑定名。两类 catch 不能混写在同一个 try 内。

`current_error()` 返回当前活动 Error，没有活动处理器时为 null。守卫中它指向当前叶子，处理块中指向选中的子组；普通单错误进入 catch* 也绑定一个单元素组。原生 Error 相等按底层错误身份判断；裸 throw 与 `throw selected` 保留原始出现位置，重建尚未处理和重抛的原树。受限智能体中提取或重抛含可调用值的错误仍保留权限上限；必要的权限投影不会扁平化错误树。

```chaos example=exception-group-rethrow
try{
  try{throw error_group("batch",[error(20),error(22)])}
  catch* {value:20,.._} as selected{throw}
}catch* _ as all{map(fn(e){e.value},all.exceptions)}
```

结果为 `[20,22]`。处理器新抛出的错误在最后合并，不交给后面的同级分支；未匹配及裸重抛的原始部分保留树形。某个模式或守卫中途失败时，本分支尚未提交的整个剩余树都保留，选择停止，新错误与之前处理器的新错误一起向外传播。守卫裸重抛不会重复添加当前叶子。已经发生的效果不自动回滚。

保护块成功时返回它的值；全部错误处理完时返回最后执行的处理器值；else 仅在保护块正常完成时运行。守卫和处理器不能用 return 或跨出自身的 break／continue 跳过剩余错误；自身循环和嵌套函数的控制流仍合法。函数默认值在外层求值，也接受这个检查。合法位置仍可 await／yield，活动错误由原调度器保存；私有 restart 转移、生成器关闭和取消继续遵循各自的原生展开合同，不能被当作普通 Error 收集。显式 restart 转移会按其词法出口离开本次捕获，放弃尚未执行的同级分支。

错误树原有出现位置预算为最多10000个节点、32层；因果图还受下述共享预算限制。所有载荷仍受语言值的节点、字符串、步数及时间预算限制。ResourceLimit、运行级控制、任务取消和宿主硬中断都不能包装成普通叶子。诊断序列化是有界静态描述，不包含宿主 traceback 或可恢复的执行状态。

IR 新增 `try_group {body,cases}`，cases 为非空 `group_case {pattern,guard,name,body}` 节点列表。group_case 只能直接出现在该位置；源码、IR、宏展开与运行时共用控制验证。IDE 提示、精确重命名、格式化与可视分支增删／排序共用此结构，不执行 Agent、守卫或工具。分组思想参考 [PEP 654](https://peps.python.org/pep-0654/)，Chaos 使用自己的结构模式和调度器，不接受 Python 的 except* 源码。


## 原生错误因果链（0.82）

智能体或角色函数将工具失败翻译为业务失败时，可以保留原生错误身份及诊断关系。`error(value, *, cause=<implicit>)` 与 `error_group(message, errors, *, cause=<implicit>)` 使用现有具名参数语法；`<implicit>` 仅是签名提示，表示省略此参数，不是可写入源码的值。

| `cause` 参数 | `cause` 成员 | `suppress_context` | 链式展示的选择规则 |
| --- | --- | --- | --- |
| 省略 | null | false | 展示已有 context |
| 原生 Error | 指定 Error | true | 优先展示显式 cause |
| null | null | true | 不自动展示 context；仍可直接查询 |

`cause` 和 `context` 都是只读 Error 或 null，`suppress_context` 是只读 Bool。显式原因也必须属于当前 Evaluator，受调用权限交集限制；普通 Record 或 `$error` JSON 不能伪造活错误。旧调用的位置参数不变，`cause` 只接受具名传入。局部声明仍可以遮蔽 error／error_group；分析器依据绑定与元数据提示，不按函数名文本猜测语义。

```chaos example=error-chain-explicit
let original=error({stage:"fetch"});
try { throw original }
catch ignored {
  try { throw error({stage:"plan"},cause=current_error()) }
  catch wrapped {
    let failure=current_error();
    [failure.value.stage,failure.cause.value.stage,
     failure.context==original,failure.suppress_context,keys(wrapped)]
  }
}
```

结果为 `["plan","fetch",true,true,["code","message","value","location"]]`。普通 catch 的四字段记录保持不变；catch* 的普通叶子模式也使用原四字段记录；根异常类模式见下一节。因果关系通过原生 Error 查询，不成为额外失败叶子，不改变 TaskGroup 的失败数量或 catch* 分区。

### 构造、传播与动态隔离

构造 error／error_group 不捕获活动错误。一个普通错误**第一次实际传播**时，记录所在逻辑执行范围的活动因果上下文；之后重抛保留已有关系和底层错误身份，不因经过其他处理器改写来源。构造时和抛出时可能位于不同的处理器中：

```chaos example=error-chain-first-propagation
let pending=try { throw "created here" }
catch ignored { error("run later") };
try { throw "propagated here" }
catch ignored {
  try { throw pending }
  catch wrapped {
    [pending.context.value,pending.cause,pending.suppress_context]
  }
}
```

结果为 `["propagated here",null,false]`。普通 throw、语言运算失败及处理器内新失败遵循同一运行时规则。清理期间的新错误可关联正在展开的普通错误；这个因果状态与当前捕获错误分开，资源出口不会因此额外获得裸 throw 或 current_error 的活动处理权限。

普通 Task／Generator 使用自己的动态错误状态，不继承创建者正在处理的错误；await／yield 后恢复各自状态。透明异步资源清理沿原逻辑调用保留待清理错误的因果关系。取消、预算耗尽、运行级控制和宿主硬中断保持原控制通道，不包装成 Error，也不作为因果链节点。

```chaos example=error-chain-suppress-context
try { throw "internal detail" }
catch ignored {
  try { throw error("public failure",cause=null) }
  catch wrapped {
    let failure=current_error();
    [failure.cause,failure.context.value,failure.suppress_context]
  }
}
```

结果为 `[null,"internal detail",true]`。抑制控制默认展示，不删除证据，也不是秘密脱敏机制。若应用只允许输出公开信息，应自己选择输出字段；读取 Error.context 仍能访问原错误。

### 共享图、工具链与诊断边界

错误组子节点和 cause／context 组成同一有界图：最多10000个不同错误节点、20000条边；错误组本身仍限32层，并继续保留按出现位置计算的原预算。因果链接不额外受32层限制。分区、重抛及权限投影保留因果关联；同一错误可以由多个组或原因链接引用。共享引用不等于重复失败，cause／context 不参与 catch* 的逐叶匹配。

有界 `$error` 诊断给本次输出中的错误节点分配局部数字 id，重复引用使用 `{"$errorRef":id}`。它保留 cause、context、suppress_context，包括被抑制展示的 context；id 不是跨运行标识。诊断最多完整展开16层和512个不同节点，更深部分只给出带 id／code／message 的 truncated 描述；活 Error 的链成员仍可读取。诊断不保存宿主 traceback、执行帧、权限或可反序列化状态。本版本提供上述结构化诊断；应用进行链式展示时，应先选择非空 cause，否则仅在 suppress_context 为 false 时显示 context，并遵守图预算与重复引用。

源码、Program IR、宏、CLI、SDK、HTTP 与便携包使用同一成员和原调用 AST；无需新增专门关键字。IDE／LSP／文档代码高亮共享原生元数据，cause／context 的可空类型在推断中保持 Any，提示说明 Error | Null，不执行智能体或追踪运行中因果图。

因果关系区分参考 [PEP 3134](https://peps.python.org/pep-3134/) 与 [Python 异常上下文](https://docs.python.org/3/library/exceptions.html#exception-context)；Chaos 采用自己的只读值、首次传播、任务隔离和预算合同，不接收 Python raise-from 语法，也不表示完整 Python／Lisp 异常类系统已完成。


## 原生异常类与名义捕获（0.83）

`Exception` 是当前 Evaluator 所有的真实语言 Class，内建异常类与用户子类使用同一 C3 继承、方法、元类和实例管线。它们不是匹配模型文本的名称，也不是宿主 Python 异常对象。以下内建类被封闭，不能直接改写其成员；用户可以继承、建立别名，或用局部声明遮蔽同名绑定。

| 类 | 直接父类 | 默认 code |
| --- | --- | --- |
| `Exception` | 无 | `exception` |
| `RuntimeError` | `Exception` | `runtime_error` |
| `UserError` | `Exception` | `user_throw` |
| `TypeError` | `Exception` | `type_mismatch` |
| `ArgumentError` | `TypeError` | `argument_error` |
| `ValueError` | `Exception` | `value_error` |
| `LookupError` | `Exception` | `lookup_error` |
| `IndexError` | `LookupError` | `index_error` |
| `KeyError` | `LookupError` | `key_error` |
| `ArithmeticError` | `Exception` | `arithmetic_error` |
| `ZeroDivisionError` | `ArithmeticError` | `zero_division` |
| `OverflowError` | `ArithmeticError` | `overflow_error` |
| `AttributeError` | `Exception` | `attribute_error` |
| `NameError` | `Exception` | `undefined_name` |
| `MatchError` | `Exception` | `match_error` |
| `OwnershipError` | `Exception` | `ownership_error` |
| `CapabilityError` | `Exception` | `capability_error` |
| `ToolError` | `Exception` | `tool_error` |
| `AgentError` | `Exception` | `agent_error` |
| `ProviderError` | `AgentError` | `provider_error` |
| `AgentToolError` | `AgentError` | `agent_tool_error` |
| `ImportError` | `Exception` | `import_error` |

默认构造 `Exception(*args)` 及其内建子类接受任意数量的位置参数。只读默认字段是 `code: String`、`message: String`、`args: Tuple`、`value: Any`：code 来自类别；message 在唯一实参为 String 时取该字符串，否则取实际类名；args 保留全部实参；value 在一个实参时为该实参，其他情况为 args Tuple。原生默认 __init__(self,*args) 不接受具名参数；用户子类定义 __init__ 后，原位置及具名参数交给该初始化器。可以用 super(子类,self).__init__(...) 按原类协议重新设置默认载荷字段；初始化器必须同步完成并返回 null。__new__／__init__ 的普通类协议仍适用，未知自定义分配器的结果不由 IDE 猜测。

```chaos example=exception-class-role
class PlanFailure(ValueError) { let stage="plan" }
let failure=PlanFailure("bad input");
try { throw failure }
catch PlanFailure::{stage:stage,message:message,.._} {
  [stage,message,current_error().instance==failure]
}
```

结果为 `["plan","bad input",true]`。根 `catch Exception::{...}` 或子类模式匹配当前错误的名义异常实例；匹配只读取真实存储字段，不执行 getter、用户 __instancecheck__ 或类名文本判断。普通 catch 绑定、记录模式及嵌套 `{value: SomeClass::{...}, .._}` 仍使用原来的四字段记录。

用户子类可以按普通类规则增加或覆盖成员，甚至改写分配器。因此未知子类的模式字段在 IDE 中保持 Any；精确已知构造结果才给出可证明的字段类型。显式抛出异常实例时，code／message 必须是有效 String 存储字段，不能依靠属性 getter 生成。

### Error 是传播信息，Exception 是名义实例

`Error.instance` 对显式抛出的 Exception 实例保留原身份；原 Error 仍承担传播位置、错误组及 cause／context 关系。抛出同一个实例会产生新的错误发生记录；需要原发生记录的重抛，继续使用裸 throw 或 `throw current_error()`。

AgentError／ProviderError／AgentToolError 按实际智能体及提供方错误 code 归类，使同一名义捕获适用于角色函数和原效果系统。对于除零、参数错误、工具失败等运行时普通失败，每次读取 instance 都在当前权限范围内分配新的普通异常实例视图，消耗原实例预算；不缓存更高权限的视图。错误类别来自共享稳定 code 映射，不通过 message 字符串猜测。未映射的普通错误使用 RuntimeError；错误组使用 Exception 视图，没有虚构 ExceptionGroup 类。

```chaos example=exception-class-runtime
try { 1/0 }
catch ArithmeticError::{.._} {
  let failure=current_error();
  [isinstance(failure.instance,ZeroDivisionError),
   failure.instance==failure.instance]
}
```

结果为 `[true,false]`：稳定的是名义类别与诊断数据，不是新分配视图的身份。权限、预算和所有权仍沿原 Error／实例路径检查；取消、资源上限及硬控制没有 Exception 视图。

### 错误组按名义类别分区

根异常类模式也适用于 catch* 的每个普通叶子；`as` 仍绑定整个匹配子组 Error。类模式守卫中的字段只属于当前叶子，处理块使用 subgroup.exceptions 中的实际 Error。

```chaos example=exception-class-group
var outputs=[];
try { throw error_group("roles",[error(ValueError("bad")),error(42)]) }
catch* ValueError::{.._} as invalid {
  outputs=append(outputs,invalid.exceptions[0].instance.message)
}
catch* UserError::{.._} as remaining {
  outputs=append(outputs,remaining.exceptions[0].value)
};
outputs
```

结果为 `["bad",42]`。选择失败、剩余树、嵌套层级、动态重抛和因果图仍遵守前述规则，不增加第二种分组调度器。源码、Program IR、宏、图编辑、IDE／LSP、CLI、SDK、HTTP 和便携包共用原 Class 与 p_class 模式节点，无新增关键词。该实现补齐语言内的基本异常类层次；完整 Python 异常库、Common Lisp 条件类体系和跨进程活异常恢复仍未完成。

显式 Exception 实例通过 throw_into／athrow 注入时使用同一名义抛出路径，并保留自定义迭代器 __throw__／__athrow__ 与 yield from 委托。原有 Error 值注入仍按普通载荷处理；只有语言 throw Error 或裸 throw 保留错误发生记录的身份。
